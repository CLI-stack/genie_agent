#!/usr/bin/env python3
"""
eco_emit_priority_force.py — deterministically BUILD the gate logic for every
`priority_force` change (Intent-B: "under a new condition, force sig=CONST"),
so it is correct by construction instead of left to LLM free-form or a PENDING
placeholder. Splices force-mux gates + DFF-pin rewires into the study.

Per priority_force change:
  1. Condition cone: the change's `condition_gate_chain` gates are emitted verbatim;
     the last gate's output is the condition net `cond` (used directly by the muxes).
  2. Per forced signal, per bit b (const bit value decides the gate):
        const bit == 1  ->  OR2 (cond, old_bit)         -> force to 1 when cond
        const bit == 0  ->  INR2(A1=old_bit, B1=cond)    -> old & ~cond = force to 0
     output is a fresh n_eco_<jira>_pf_<sig>_<b>, then a DFF-pin rewire repoints
     that bit's flop .D from old_bit to the fresh net.

Required schema on each priority_force change (Step 1 / rtl_diff_analyzer.md #1):
  module_name, condition_gate_chain:[{instance_name,gate_function,cell_type,
      port_connections}], forced_signals:[{signal, const, bits:[{bit, old_net,
      dff_cell, dff_pin}]}].  (bits[].dff_cell/dff_pin identify the flop to rewire;
  old_net is that bit's pre-ECO D-input net.)

Fail-closed grounding: pass --ref-dir to verify every bit's (module, dff_cell).dff_pin
actually carries old_net in the PreEco Synthesize netlist. Any mismatch ABORTS the build
(exit 2, study untouched) instead of rewiring the wrong pin on a hallucinated net.

Usage:
    python3 script/eco_scripts/eco_emit_priority_force.py \
        --rtl-diff data/<TAG>_eco_rtl_diff.json --study data/<TAG>_eco_preeco_study.json \
        --jira <JIRA> --ref-dir <REF_DIR> --output data/<TAG>_eco_preeco_study.json
"""
import argparse, gzip, json, os, re, sys
try:
    from eco_rtl_config import RtlConfig
except Exception:
    RtlConfig = None
try:
    from eco_extract_pf_condition import extract_condition, resolve_rtl, branch_assignments
except Exception:
    extract_condition = resolve_rtl = branch_assignments = None

STAGES = ('Synthesize', 'PrePlace', 'Route')
_INV_CELL = 'INVD1BWP136P5M156H3P48CPDLVT'
_OR2_CELL = 'OR2D1BWP136P5M156H3P48CPDLVT'
_INR2_CELL = 'INR2D1BWP136P5M156H3P48CPDLVT'
_AND2_CELL = 'AND2D1BWP136P5M156H3P48CPDLVT'


# ── Boolean condition parser: RTL condition_expr -> AST ─────────────────────────
# Grammar (Verilog boolean subset used by priority_force conditions):
#   or   := and ( '|' and )*
#   and  := unary ( '&' unary )*
#   unary:= '~' unary | ('~&'|'~|') '(' or ')' | primary
#   prim := '(' or ')' | SIG          (SIG = ident with optional [..] select)
# AST nodes: ('sig',s) ('not',a) ('and',[..]) ('or',[..]) ('red','nand'|'nor',a)
class _PErr(Exception):
    pass


def _tok(expr):
    # RED (multi-char reductions) must precede NOT/AND/OR/XOR so ~& etc. win.
    spec = [('WS', r'\s+'), ('RED', r'~&|~\||~\^|\^~'), ('NOT', r'~|!'),
            ('AND', r'&&|&'), ('OR', r'\|\||\|'), ('XOR', r'\^'),
            ('LP', r'\('), ('RP', r'\)'),
            ('SIG', r'[A-Za-z_]\w*(?:\s*\[[^\]]*\])?')]
    rx = re.compile('|'.join(f'(?P<{n}>{p})' for n, p in spec))
    out, i = [], 0
    while i < len(expr):
        m = rx.match(expr, i)
        if not m:
            raise _PErr(f"unparsable token at {expr[i:i+20]!r}")
        i = m.end()
        if m.lastgroup == 'WS':
            continue
        out.append((m.lastgroup, re.sub(r'\s+', '', m.group())))
    return out


def parse_condition(expr):
    """Parse a Verilog boolean condition into an AST. Raises _PErr on anything
    outside the supported operator set (so the caller can fail-closed)."""
    toks = _tok(expr)
    pos = [0]
    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)
    def eat(kind=None):
        k, v = peek()
        if kind and k != kind:
            raise _PErr(f"expected {kind}, got {k} ({v})")
        pos[0] += 1
        return v
    def p_or():
        node = p_and()
        kids = [node]
        while peek()[0] == 'OR':
            eat('OR'); kids.append(p_and())
        return ('or', kids) if len(kids) > 1 else node
    def p_and():
        node = p_unary()
        kids = [node]
        while peek()[0] == 'AND':
            eat('AND'); kids.append(p_unary())
        return ('and', kids) if len(kids) > 1 else node
    _REDOP = {'~&': 'nand', '~|': 'nor', '~^': 'xnor', '^~': 'xnor',
              '&': 'and', '|': 'or', '^': 'xor'}
    def p_unary():
        k, v = peek()
        if k == 'NOT':
            eat('NOT'); return ('not', p_unary())
        # unary reduction: ~& ~| ~^ ^~ (multi-char), or a bitwise &/|/^ appearing
        # in operand position (a leading binary operator would be a syntax error,
        # so here it must be a reduction). Operand is a paren-expr or a bare bus.
        if k == 'RED' or (k in ('AND', 'OR', 'XOR') and v in ('&', '|', '^')):
            eat(k); return ('red', _REDOP[v], p_prim())
        return p_prim()
    def p_prim():
        k, v = peek()
        if k == 'LP':
            eat('LP'); inner = p_or(); eat('RP'); return inner
        if k == 'SIG':
            eat('SIG'); return ('sig', v)
        raise _PErr(f"unexpected token {k} ({v})")
    ast = p_or()
    if pos[0] != len(toks):
        raise _PErr(f"trailing tokens from {toks[pos[0]:]}")
    return ast


# ── AST -> gate synthesis ───────────────────────────────────────────────────────
# Builds real gates for a condition AST. Scalar nodes -> a 1-bit net; reduction
# nodes expand per-bit over the resolved bus width (RtlConfig part-selects) and
# reduce (AND-tree+INV for ~& / OR-tree+INV for ~|). Returns (cond_net, gates).
_PARTSEL = re.compile(r'^(\w+)\[(`?\w+)\]$')
_RANGE   = re.compile(r'^(\w+)\[\s*(\d+)\s*:\s*(\d+)\s*\]$')   # sig[7:0]
_BITSEL  = re.compile(r'^(\w+)\[\s*\d+\s*\]$')                 # sig[3]


def _reduction_width(node, cfg):
    """Bus width of a reduction operand: from a part-select leaf's field width
    (macro `sig[`FIELD]`) or an explicit range (`sig[7:0]`)."""
    found = [None]
    def walk(n):
        if n[0] == 'sig':
            m = _PARTSEL.match(n[1])
            if m and m.group(2).startswith('`') and cfg:
                ps = cfg.part_select(m.group(2))
                if ps:
                    found[0] = ps[1] - ps[0] + 1
                return
            r = _RANGE.match(n[1])
            if r:
                found[0] = abs(int(r.group(2)) - int(r.group(3))) + 1
        elif n[0] in ('and', 'or'):
            for k in n[1]:
                walk(k)
        elif n[0] == 'not':
            walk(n[1])
    walk(node)
    return found[0]


def _bit_ref(sig, i, cfg):
    """Per-bit net of a leaf for bit i inside a reduction: explicit range
    sig[hi:lo] -> sig[lo+i]; part-select sig[`FIELD] -> sig[base+i]; bare bus
    -> sig[i]; fixed sig[N] -> broadcast as-is."""
    r = _RANGE.match(sig)
    if r:
        base, hi, lo = r.group(1), int(r.group(2)), int(r.group(3))
        return f'{base}[{min(hi, lo) + i}]'
    m = _PARTSEL.match(sig)
    if not m:
        return f'{sig}[{i}]'
    base, idx = m.group(1), m.group(2)
    if idx.startswith('`'):
        ps = cfg.part_select(idx) if cfg else None
        return f'{base}[{ps[0] + i}]' if ps else None
    return sig


def _local_defs(rtl_text):
    """Map a module's local COMBINATIONAL definitions -> RHS expr, so condition
    leaves that synthesis flattened away (they are no netlist net) can be rebuilt
    from their real inputs. Covers three forms, keyed by the exact LHS token:
      - `wire <name> = <expr>;`
      - `assign <name>[i]? = <expr>;`
      - `always @* <name>[i]? = <expr>;` / `always_comb <name>[i]? = <expr>;`
        (single blocking assign; begin/end blocks are skipped — safe: an unfound
        leaf is caught by the fail-closed grounding check, never emitted dangling).
    Per-bit combinational regs (e.g. `always @* WckIsInSync[0] = |WckSyncCtr0[7:0]
    | WckIsAlwaysInSync0;`) are keyed as `WckIsInSync[0]`."""
    out = {}
    if not rtl_text:
        return out
    txt = re.sub(r'//[^\n]*', '', rtl_text)
    txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.DOTALL)
    clean = lambda e: re.sub(r'\s+', ' ', e).strip()
    nospc = lambda s: re.sub(r'\s+', '', s)
    for m in re.finditer(r'\bwire\b[^;=]*\b(\w+)\s*=\s*([^;]+);', txt):
        out.setdefault(m.group(1), clean(m.group(2)))
    for m in re.finditer(r'\bassign\b\s+(\w+(?:\s*\[\s*\d+\s*\])?)\s*=\s*([^;]+);', txt):
        out.setdefault(nospc(m.group(1)), clean(m.group(2)))
    for m in re.finditer(
            r'\balways\b\s*(?:@\s*\*|@\s*\(\s*\*\s*\)|_comb)\s*'
            r'(\w+(?:\s*\[\s*\d+\s*\])?)\s*=\s*([^;]+);', txt):
        out.setdefault(nospc(m.group(1)), clean(m.group(2)))
    return out


class _CondSynth:
    def __init__(self, jira, module, cfg, mk, rtl_text=None, in_netlist=None):
        self.jira, self.module, self.cfg, self.mk = jira, module, cfg, mk
        self.wires = _local_defs(rtl_text)     # local combinational decompositions
        self.in_netlist = in_netlist or (lambda s: True)  # leaf presence predicate
        self._decomposing = set()              # recursion guard
        self.gates = []
    def _resolve_sig(self, s):
        """Resolve a single-bit macro select: base[`FIELD] -> base[N] when FIELD is
        a scalar field. Leaves bare names / numeric selects unchanged."""
        m = _PARTSEL.match(s)
        if m and m.group(2).startswith('`') and self.cfg:
            ps = self.cfg.part_select(m.group(2))
            if ps and ps[0] == ps[1]:
                return f'{m.group(1)}[{ps[0]}]'
        return s
    def _g(self, cell, fn, pc):
        out = pc[[p for p in ('Z', 'ZN') if p in pc][0]]
        self.gates.append({
            'change_type': 'new_logic_gate',
            'instance_name': f'eco_{self.jira}_pfc_{len(self.gates)}',
            'cell_type': cell, 'gate_function': fn, 'output_net': out,
            'module_name': self.module, 'port_connections': pc,
            'port_connections_per_stage': _pcstage(pc),
            'confirmed': True, 'source': 'eco_emit_priority_force'})
        return out
    def _and(self, nets):
        cur = list(nets)
        while len(cur) > 1:
            a, b = cur.pop(0), cur.pop(0)
            cur.append(self._g(_AND2_CELL, 'AND2', {'A1': a, 'A2': b, 'Z': self.mk('and')}))
        return cur[0]
    def _or(self, nets):
        cur = list(nets)
        while len(cur) > 1:
            a, b = cur.pop(0), cur.pop(0)
            cur.append(self._g(_OR2_CELL, 'OR2', {'A1': a, 'A2': b, 'Z': self.mk('or')}))
        return cur[0]
    def _inv(self, a):
        return self._g(_INV_CELL, 'INV', {'I': a, 'ZN': self.mk('inv')})
    def _decomp_key(self, s):
        """Return the local_defs key to decompose `s` by, or None. Prefers an exact
        token match (per-bit reg like WckIsInSync[0]); else the bare base name (a
        whole-signal wire/assign). Only decomposes when the base is NOT a netlist
        net (a flattened intermediate), never a real port/reg."""
        base = (_PARTSEL.match(s) or _RANGE.match(s) or _BITSEL.match(s))
        base = base.group(1) if base else s
        if self.in_netlist(base):
            return None
        if s in self.wires and s not in self._decomposing:
            return s
        if base in self.wires and base not in self._decomposing:
            return base
        return None
    def scalar(self, node):
        k = node[0]
        if k == 'sig':
            s = self._resolve_sig(node[1])
            key = self._decomp_key(s)
            if key is not None:
                self._decomposing.add(key)
                try:
                    sub = self.scalar(parse_condition(self.wires[key]))
                finally:
                    self._decomposing.discard(key)
                return sub
            return s
        if k == 'not':
            return self._inv(self.scalar(node[1]))
        if k == 'and':
            return self._and([self.scalar(x) for x in node[1]])
        if k == 'or':
            return self._or([self.scalar(x) for x in node[1]])
        if k == 'red':
            _, op, inner = node
            if op == 'xor' or op == 'xnor':
                raise _PErr(f"xor/xnor reduction unsupported (no XOR cell): {inner}")
            W = _reduction_width(inner, self.cfg)
            if not W:
                raise _PErr(f"cannot resolve reduction bus width for {inner}")
            bits = [self.bit(inner, i) for i in range(W)]
            if None in bits:
                raise _PErr(f"unresolvable bit ref in reduction {inner}")
            root = self._and(bits) if op in ('and', 'nand') else self._or(bits)
            return self._inv(root) if op in ('nand', 'nor') else root
        raise _PErr(f"bad node {node}")
    def bit(self, node, i):
        k = node[0]
        if k == 'sig':
            ref = _bit_ref(node[1], i, self.cfg)
            if ref is None:
                return None
            key = self._decomp_key(ref)              # per-bit reg (WckIsInSync[0])
            if key is not None:
                self._decomposing.add(key)
                try:
                    sub = self.scalar(parse_condition(self.wires[key]))
                finally:
                    self._decomposing.discard(key)
                return sub
            return ref
        if k == 'not':
            b = self.bit(node[1], i); return self._inv(b) if b else None
        if k == 'and':
            bs = [self.bit(x, i) for x in node[1]]
            return self._and(bs) if None not in bs else None
        if k == 'or':
            bs = [self.bit(x, i) for x in node[1]]
            return self._or(bs) if None not in bs else None
        raise _PErr(f"reduction inner has nested reduction/unsupported node {node}")


def synthesize_condition(cond_expr, jira, module, cfg, mk, rtl_text=None, in_netlist=None):
    """Parse + synthesize a condition_expr into gates. Returns (cond_net, gates).
    rtl_text + in_netlist enable local-wire decomposition of flattened intermediate
    signals. Raises _PErr on anything unbuildable (caller fails closed)."""
    ast = parse_condition(cond_expr)
    s = _CondSynth(jira, module, cfg, mk, rtl_text=rtl_text, in_netlist=in_netlist)
    cond = s.scalar(ast)
    # FAIL-CLOSED grounding: every leaf feeding the cone (a gate input that no
    # other cone gate produces) must be a real netlist net or a constant. If any
    # leaf is ungrounded the condition was built on a signal the module does not
    # have — raise so the caller aborts instead of emitting a dangling reference.
    outs = {g['output_net'] for g in s.gates}
    leaves = {v for g in s.gates for p, v in g['port_connections'].items()
              if p not in ('Z', 'ZN') and isinstance(v, str) and v not in outs}
    bad = sorted(l for l in leaves
                 if not re.match(r"^\d*'[bhdo]", l) and not l.isdigit()
                 and not s.in_netlist((_BITSEL.match(l) or _PARTSEL.match(l)
                                       or _RANGE.match(l)).group(1)
                                      if (_BITSEL.match(l) or _PARTSEL.match(l)
                                          or _RANGE.match(l)) else l))
    if bad:
        raise _PErr(f"condition leaves not grounded in netlist: {bad[:12]}")
    return cond, s.gates

_MOD = re.compile(r'^\s*module\s+(\S+)')
_INST = re.compile(r'^\s*[\w:]+\s+(\w+)\s*\(')
_PIN = re.compile(r'\.(\w+)\s*\(\s*([^)]*?)\s*\)')


def _scan_pins(gz):
    """One linear pass over a netlist -> inst_pins[(module, inst)] = {pin: net}."""
    inst_pins = {}
    if not os.path.isfile(gz):
        return inst_pins
    mod, cur, depth = None, None, 0
    with gzip.open(gz, 'rt', errors='replace') as f:
        for ln in f:
            mm = _MOD.match(ln)
            if mm:
                mod, cur, depth = mm.group(1), None, 0
                continue
            im = _INST.match(ln)
            if im and depth == 0:
                cur = im.group(1)
            depth += ln.count('(') - ln.count(')')
            if depth <= 0:
                depth = 0
            if cur and mod:
                for pm in _PIN.finditer(ln):
                    inst_pins.setdefault((mod, cur), {})[pm.group(1)] = pm.group(2).strip()
    return inst_pins


def _find_pins(inst_pins, mod, inst):
    """Route re-uniquifies the study module with a trailing _0."""
    for m in (mod, mod + '_0'):
        if (m, inst) in inst_pins:
            return inst_pins[(m, inst)]
    return None


def _module_netlist_body(ref_dir, module):
    """PreEco Synthesize netlist body text of <module> (and _0 variant)."""
    gz = os.path.join(ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    body, grab = [], False
    if os.path.isfile(gz):
        try:
            with gzip.open(gz, 'rt', errors='replace') as f:
                for ln in f:
                    mm = re.match(r'^module\s+(\S+)', ln)
                    if mm:
                        grab = mm.group(1) in (module, module + '_0')
                    if grab:
                        body.append(ln)
                        if ln.lstrip().startswith('endmodule'):
                            grab = False
        except Exception:
            body = []
    return ''.join(body)


def _resolve_macro(ref_dir, macro):
    """Resolve `define <macro> N'b...` from the PreEco SynRtl headers. Returns the
    literal string (e.g. "5'b01011") or None if not found."""
    import subprocess
    root = os.path.join(ref_dir, 'data', 'PreEco', 'SynRtl')
    try:
        out = subprocess.run(
            ['grep', '-rhoE', r"define[ \t]+" + re.escape(macro) + r"[ \t]+[0-9]+'[bBhHdD][0-9a-fA-FxXzZ_]+",
             root], capture_output=True, text=True, timeout=90).stdout
    except Exception:
        return None
    for ln in out.splitlines():
        m = re.search(r"([0-9]+'[bBhHdD][0-9a-fA-FxXzZ_]+)", ln)
        if m:
            return m.group(1)
    return None


def _norm_const(lit):
    """(width, int_value) from a Verilog literal, or None if unparsable/has x/z."""
    m = re.match(r"^\s*(\d+)'([bBhHdD])([0-9a-fA-FxXzZ_]+)\s*$", str(lit or ''))
    if not m:
        return None
    digits = m.group(3).replace('_', '')
    if re.search(r'[xXzZ]', digits):
        return None
    base = {'b': 2, 'h': 16, 'd': 10}[m.group(2).lower()]
    try:
        return (int(m.group(1)), int(digits, base))
    except Exception:
        return None


def ground_bits(rtl_diff, ref_dir):
    """Fail-closed: verify EVERY priority_force bit's (module, dff_cell).dff_pin actually
    carries old_net in the PreEco Synthesize netlist, AND (when const_macro is given)
    that the forced const matches the macro's RTL define value. Returns a list of error
    strings; empty means it is safe to build."""
    gz = os.path.join(ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    inst_pins = _scan_pins(gz)
    errs = []
    if not inst_pins:
        return [f"cannot ground priority_force: PreEco netlist not readable at {gz}"]
    for ci, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'priority_force':
            continue
        mod = c.get('module_name') or ''
        for f in c.get('forced_signals') or []:
            sig = f.get('signal')
            # OPCODE-VALUE grounding: if the force targets a named macro (const_macro),
            # the forced const MUST equal that macro's RTL `define` value. Catches a
            # corrupted/mis-resolved opcode (e.g. UMC_MOP_CAS silently became 5'b00001
            # instead of 5'b01011) that every schema check accepts as a valid constant.
            cm = f.get('const_macro')
            if cm:
                resolved = _resolve_macro(ref_dir, cm)
                nc, nr = _norm_const(f.get('const')), (_norm_const(resolved) if resolved else None)
                if resolved is None:
                    # can't resolve (macro defined outside SynRtl) => cannot verify, but
                    # do NOT block a legit build; only a definitive mismatch aborts.
                    pass
                elif nc is None or nr is None or nc != nr:
                    errs.append(f"changes[{ci}] {sig}: forced const {f.get('const')!r} does NOT match "
                                f"macro {cm} = {resolved!r} from the RTL define — WRONG opcode value "
                                f"(the ECO would force the wrong command). Fix const to match {cm}.")
            for bspec in f.get('bits') or []:
                b = bspec.get('bit')
                old = bspec.get('old_net')
                cell = bspec.get('dff_cell')
                pin = bspec.get('dff_pin', 'D')
                pins = _find_pins(inst_pins, mod, cell) if cell else None
                if pins is None:
                    errs.append(f"changes[{ci}] {sig}[{b}]: dff_cell {cell!r} not found in "
                                f"module {mod!r} of PreEco Synthesize netlist.")
                    continue
                actual = pins.get(pin)
                if actual != old:
                    errs.append(f"changes[{ci}] {sig}[{b}]: {cell}.{pin} carries {actual!r} in "
                                f"PreEco netlist but bits[].old_net says {old!r} — net mismatch, "
                                f"force would rewire the WRONG pin.")
    return errs


def _const_bits(const):
    """'5'b01011' -> ['0','1','0','1','1'] (MSB..LSB); '1'b1' -> ['1']. None if unparsable."""
    m = re.match(r"^(\d+)'b([01]+)$", str(const).strip())
    if m:
        w, bits = int(m.group(1)), m.group(2)
        return list(bits.zfill(w))
    m = re.match(r"^1'b([01])$", str(const).strip())
    return [m.group(1)] if m else None


def _pcstage(pc):
    return {s: dict(pc) for s in STAGES}


def emit(rtl_diff, study, jira, ref_dir=None):
    seq = [0]
    def nn(tag):
        seq[0] += 1
        return f"n_eco_{jira}_pf_{tag}_{seq[0]}"
    cfg = RtlConfig(ref_dir) if (ref_dir and RtlConfig) else None
    _body_cache = {}
    def _in_netlist_pred(module):
        if not ref_dir:
            return (lambda s: True)
        if module not in _body_cache:
            _body_cache[module] = _module_netlist_body(ref_dir, module)
        txt = _body_cache[module]
        return (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', txt)))
    added, errs = 0, []
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'priority_force':
            continue
        mod = c.get('module_name') or ''
        new_gates, new_rewires = [], []
        # 1. condition cone — BUILD from condition_expr (anchored/extracted), correct
        #    by construction. The RTL is ground truth: when a const_macro anchor +
        #    ref_dir are available, extract the condition from the RTL and PREFER it
        #    over any AI-stored condition_expr (which can capture the wrong branch).
        #    Fall back to the stored condition_expr only when extraction isn't possible.
        cond_expr = None
        anchor = next((f for f in (c.get('forced_signals') or []) if f.get('const_macro')), None)
        if ref_dir and extract_condition and resolve_rtl and anchor:
            base = re.sub(r'^ddrss_\w+?_t_', '', mod)
            rtl = resolve_rtl(ref_dir=ref_dir, module=base)
            ms = extract_condition(rtl, anchor['signal'], anchor['const_macro']) if rtl else []
            if len(ms) == 1 and ms[0].get('condition_expr'):
                cond_expr = ms[0]['condition_expr']
            elif len(ms) > 1:
                errs.append(f"priority_force {mod}: condition anchor {anchor['signal']}="
                            f"{anchor['const_macro']} matched {len(ms)} assignments — ambiguous.")
                continue
            # FAIL-CLOSED completeness: forced_signals must drive EVERY signal the
            # anchored RTL branch assigns. A dropped driven signal is a silent-wrong
            # ECO (the force would only partially apply).
            if branch_assignments and rtl:
                expected = set(branch_assignments(rtl, anchor['signal'], anchor['const_macro']))
                have = {f.get('signal') for f in (c.get('forced_signals') or [])}
                miss = sorted(expected - have)
                if miss:
                    errs.append(f"priority_force {mod}: forced_signals is INCOMPLETE — the RTL "
                                f"branch also assigns {miss}, but they are not in forced_signals. "
                                f"Every signal the branch drives must be forced.")
                    continue
        if not cond_expr:
            cond_expr = c.get('condition_expr')
        cond = None
        if cond_expr and cfg is not None:
            rtl = resolve_rtl(ref_dir=ref_dir, module=re.sub(r'^ddrss_\w+?_t_', '', mod)) if resolve_rtl else None
            rtl_text = open(rtl, errors='replace').read() if rtl and os.path.isfile(rtl) else None
            try:
                cond, cone = synthesize_condition(cond_expr, jira, mod, cfg, nn,
                                                  rtl_text=rtl_text, in_netlist=_in_netlist_pred(mod))
                new_gates.extend(cone)
            except _PErr as e:
                errs.append(f"priority_force {mod}: cannot synthesize condition — {e}. cond_expr={cond_expr!r}")
                continue
        else:
            # legacy fallback: emit a well-formed condition_gate_chain verbatim
            for g in (c.get('condition_gate_chain') or []):
                pc = g.get('port_connections') or {}
                if not g.get('cell_type') or not pc:
                    errs.append(f"priority_force {mod}: condition_gate_chain gate "
                                f"{g.get('instance_name')!r} is an empty skeleton and no condition_expr "
                                f"is available to build from.")
                    cond = None; break
                new_gates.append({
                    'change_type': 'new_logic_gate', 'instance_name': g['instance_name'],
                    'cell_type': g['cell_type'], 'gate_function': g.get('gate_function', ''),
                    'output_net': g.get('output_net') or _out_of(pc),
                    'module_name': mod, 'port_connections': pc,
                    'port_connections_per_stage': g.get('port_connections_per_stage') or _pcstage(pc),
                    'confirmed': True, 'source': 'eco_emit_priority_force'})
                cond = g.get('output_net') or _out_of(pc)
        if not cond:
            if cond_expr is None and not (c.get('condition_gate_chain')):
                errs.append(f"priority_force {mod}: no condition_expr and no condition_gate_chain.")
            continue
        # NOTE: no INV(cond) is emitted — the force-muxes use `cond` directly
        # (OR2(cond,old) for a const-1 bit; INR2(A1=old,B1=cond)=old&~cond for a
        # const-0 bit), so an inverted copy would be dead logic (dangling-cone).
        # 2. per forced signal, per bit force-mux + DFF-pin rewire
        for f in c.get('forced_signals') or []:
            cbits = _const_bits(f.get('const'))
            bits = f.get('bits') or []
            if cbits is None or not bits:
                continue
            for bspec in bits:
                b = bspec.get('bit')
                old = bspec.get('old_net')
                dff_cell = bspec.get('dff_cell'); dff_pin = bspec.get('dff_pin', 'D')
                if b is None or old is None or not dff_cell:
                    continue
                # const bit for this position (bits given MSB..LSB; index by width-1-b)
                cval = cbits[len(cbits) - 1 - b] if b < len(cbits) else '0'
                fresh = nn(f"{f.get('signal','sig')}_{b}")
                if cval == '1':
                    pc = {'A1': cond, 'A2': old, 'Z': fresh}
                    cell, fn = _OR2_CELL, 'OR2'
                else:
                    pc = {'A1': old, 'B1': cond, 'ZN': fresh}
                    cell, fn = _INR2_CELL, 'INR2'
                new_gates.append({
                    'change_type': 'new_logic_gate',
                    'instance_name': f"eco_{jira}_pf_mux_{f.get('signal','sig')}_{b}",
                    'cell_type': cell, 'gate_function': fn, 'output_net': fresh,
                    'module_name': mod, 'port_connections': pc,
                    'port_connections_per_stage': bspec.get('port_connections_per_stage') or _pcstage(pc),
                    'confirmed': True, 'source': 'eco_emit_priority_force',
                    'notes': f"priority_force: force {f.get('signal')}[{b}]={cval} when condition, else hold.",
                })
                rw = {
                    'change_type': 'rewire', 'instance_name': dff_cell, 'cell_name': dff_cell,
                    'module_name': mod, 'pin': dff_pin, 'old_net': old, 'new_net': fresh,
                    'confirmed': True, 'force_reapply': True, 'source': 'eco_emit_priority_force',
                    'notes': f"priority_force DFF-pin rewire for {f.get('signal')}[{b}].",
                }
                if bspec.get('cell_name_per_stage'):
                    rw['cell_name_per_stage'] = bspec['cell_name_per_stage']
                if bspec.get('pin_per_stage'):
                    rw['pin_per_stage'] = bspec['pin_per_stage']
                if bspec.get('old_net_per_stage'):
                    rw['old_net_per_stage'] = bspec['old_net_per_stage']
                new_rewires.append(rw)
        for st in STAGES:
            study.setdefault(st, []).extend([dict(g) for g in new_gates] + [dict(r) for r in new_rewires])
            added += len(new_gates) + len(new_rewires)
    return added, errs


def _out_of(pc):
    for p in ('Z', 'ZN', 'CO', 'S'):
        if p in pc:
            return pc[p]
    return ''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--study', required=True)
    ap.add_argument('--jira', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--ref-dir', help='REF_DIR: when given, every priority_force bit is '
                    'grounded against PreEco Synthesize netlist and the build ABORTS '
                    '(nonzero exit, study untouched) on any dff_cell/old_net mismatch.')
    args = ap.parse_args()
    rtl_diff = json.loads(open(args.rtl_diff).read())
    study = json.loads(open(args.study).read())

    # Fail-closed grounding: refuse to build on hallucinated/wrong nets.
    if args.ref_dir:
        errs = ground_bits(rtl_diff, args.ref_dir)
        if errs:
            marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_priority_force.py\n"
                      f"  ABORTED — {len(errs)} priority_force bit(s) not netlist-grounded:\n"
                      + "".join(f"    - {e}\n" for e in errs)
                      + "  Study UNTOUCHED. Fix bits[].dff_cell/old_net in the RTL diff and re-run.\n")
            print(marker)
            open(args.output.replace('.json', '_priority_force_marker.txt'), 'w').write(marker)
            return 2

    n, build_errs = emit(rtl_diff, study, args.jira, ref_dir=args.ref_dir)
    if build_errs:
        marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_priority_force.py\n"
                  f"  ABORTED — {len(build_errs)} priority_force condition build error(s):\n"
                  + "".join(f"    - {e}\n" for e in build_errs)
                  + "  Study UNTOUCHED. Fix condition_expr / thread in missing leaves and re-run.\n")
        print(marker)
        open(args.output.replace('.json', '_priority_force_marker.txt'), 'w').write(marker)
        return 2

    open(args.output, 'w').write(json.dumps(study, indent=2))
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_emit_priority_force.py\n"
              f"  priority_force entries spliced (gates+rewires, all stages): {n}\n"
              f"  condition: built from condition_expr (synthesized)\n"
              f"  netlist-grounded: {'yes' if args.ref_dir else 'NO (no --ref-dir)'}\n")
    print(marker)
    open(args.output.replace('.json', '_priority_force_marker.txt'), 'w').write(marker)
    return 0


if __name__ == '__main__':
    sys.exit(main())
