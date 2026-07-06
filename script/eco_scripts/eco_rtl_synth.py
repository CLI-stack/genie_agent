#!/usr/bin/env python3
"""
eco_rtl_synth.py — a bounded, recursive Verilog RTL synthesizer for ECO cone rebuild.

Parses the RTL *control-logic* expression subset into an AST and (in the lowering,
S2) recursively synthesizes gates, grounding at real netlist nets. This is what lets
a combinational-cone ECO (e.g. a priority-chain guard change) be rebuilt fully — the
selector's context signals (themselves computed by procedural blocks) are rebuilt
recursively instead of failing to ground.

S1 (this file, first phase): expression parser + width inference.

Supported expression constructs:
  ternary  c ? a : b
  logical  || &&           (1-bit)
  equality == !=           (1-bit)
  relational < > <= >=     (1-bit)   [lowered later only if needed; parsed here]
  bitwise  | ^ ~^ &        (bus)
  reductions & | ^ ~& ~| ~^ ^~  (unary, 1-bit)
  unary    ~ ! -
  arithmetic + -           (parsed; lowering may fail-closed)
  concat   {a, b, ...}
  replication {N{x}}
  select   x[i] · x[msb:lsb] · x[base +: w] · x[base -: w]
  constants  N'bxxx / N'hxx / decimal · macros `NAME
  signals    bare identifiers

Raises _SErr fail-closed on anything outside this subset.

AST nodes (tuples):
  ('num', width, value|None, raw)      value None if x/z present
  ('id',  name)
  ('bit', node, idx_expr)
  ('part', node, msb_expr, lsb_expr)
  ('idxpart', node, base_expr, width_int, up_bool)
  ('un', op, a)          op in ~ ! -
  ('red', op, a)         op in & | ^ ~& ~| ~^
  ('bin', op, a, b)
  ('tern', c, a, b)
  ('concat', [nodes])
  ('rep', n_int, node)
"""
import re


class _SErr(Exception):
    pass


# ── tokenizer ────────────────────────────────────────────────────────────────
_TOKSPEC = [
    ('WS',   r'\s+'),
    ('NUM',  r"\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+|\d+"),
    ('ID',   r"`?[A-Za-z_]\w*"),
    ('IPU',  r'\+:'),
    ('IPD',  r'-:'),
    ('REL',  r'<=|>=|<|>'),
    ('EQ',   r'==|!='),
    ('LAND', r'&&'),
    ('LOR',  r'\|\|'),
    ('RNAND', r'~&'), ('RNOR', r'~\|'), ('RXNOR', r'~\^|\^~'),
    ('AND',  r'&'), ('OR', r'\|'), ('XOR', r'\^'),
    ('NOT',  r'~'), ('LNOT', r'!'),
    ('PLUS', r'\+'), ('MINUS', r'-'),
    ('Q',    r'\?'), ('COLON', r':'),
    ('LB',   r'\['), ('RB', r'\]'),
    ('LC',   r'\{'), ('RC', r'\}'),
    ('LP',   r'\('), ('RP', r'\)'),
    ('COMMA', r','),
]
_TOKRE = re.compile('|'.join(f'(?P<{n}>{p})' for n, p in _TOKSPEC))


def tokenize(expr):
    out, i = [], 0
    while i < len(expr):
        m = _TOKRE.match(expr, i)
        if not m:
            raise _SErr(f"unlexable at {expr[i:i+24]!r}")
        i = m.end()
        if m.lastgroup == 'WS':
            continue
        out.append((m.lastgroup, m.group()))
    return out


# binary operator precedence (higher binds tighter)
_BINPREC = {
    'LOR': 1, 'LAND': 2, 'OR': 3, 'XOR': 4, 'RXNOR': 4, 'AND': 5,
    'EQ': 6, 'REL': 7, 'PLUS': 8, 'MINUS': 8,
}
_UNARY = {'NOT', 'LNOT', 'MINUS', 'AND', 'OR', 'XOR', 'RNAND', 'RNOR', 'RXNOR'}
_REDMAP = {'AND': '&', 'OR': '|', 'XOR': '^', 'RNAND': '~&', 'RNOR': '~|', 'RXNOR': '~^'}


_PARSE_CACHE = {}


def parse_expr(expr):
    """Parse a Verilog expression string -> AST (memoized; ASTs are immutable tuples).
    Raises _SErr fail-closed."""
    cached = _PARSE_CACHE.get(expr)
    if cached is not None:
        return cached
    ast = _parse_expr(expr)
    _PARSE_CACHE[expr] = ast
    return ast


def _parse_expr(expr):
    toks = tokenize(expr)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def eat(kind=None):
        k, v = peek()
        if kind and k != kind:
            raise _SErr(f"expected {kind}, got {k} {v!r}")
        pos[0] += 1
        return v

    def p_ternary():
        c = p_binary(1)
        if peek()[0] == 'Q':
            eat('Q'); a = p_ternary(); eat('COLON'); b = p_ternary()
            return ('tern', c, a, b)
        return c

    def p_binary(minp):
        left = p_unary()
        while True:
            k, v = peek()
            p = _BINPREC.get(k)
            if p is None or p < minp:
                return left
            eat(k)
            right = p_binary(p + 1)
            left = ('bin', v, left, right)

    def p_unary():
        k, v = peek()
        if k in _UNARY:
            eat(k)
            operand = p_unary()
            if k in ('NOT', 'LNOT', 'MINUS'):
                return ('un', v, operand)
            return ('red', _REDMAP[k], operand)   # reduction
        return p_postfix()

    def p_postfix():
        node = p_primary()
        while peek()[0] == 'LB':
            node = p_select(node)
        return node

    def p_select(node):
        eat('LB')
        a = p_ternary()
        k, _ = peek()
        if k == 'RB':
            eat('RB'); return ('bit', node, a)
        if k == 'COLON':
            eat('COLON'); b = p_ternary(); eat('RB'); return ('part', node, a, b)
        if k in ('IPU', 'IPD'):
            up = (k == 'IPU'); eat(k); w = p_ternary(); eat('RB')
            return ('idxpart', node, a, w, up)   # width resolved at lowering (cfg/macros)
        raise _SErr(f"bad select syntax near {k}")

    def p_primary():
        k, v = peek()
        if k == 'NUM':
            eat('NUM'); return _num_node(v)
        if k == 'ID':
            eat('ID'); return ('id', v)
        if k == 'LP':
            eat('LP'); e = p_ternary(); eat('RP'); return e
        if k == 'LC':
            return p_concat()
        raise _SErr(f"unexpected token {k} {v!r}")

    def p_concat():
        eat('LC')
        first = p_ternary()
        k, _ = peek()
        if k == 'LC':          # replication {N{x}}
            n = _const_int(first)
            if n is None:
                raise _SErr("replication count not constant")
            eat('LC'); inner = p_ternary(); eat('RC'); eat('RC')
            return ('rep', n, inner)
        parts = [first]
        while peek()[0] == 'COMMA':
            eat('COMMA'); parts.append(p_ternary())
        eat('RC')
        return ('concat', parts)

    ast = p_ternary()
    if pos[0] != len(toks):
        raise _SErr(f"trailing tokens {toks[pos[0]:]}")
    return ast


def _num_node(raw):
    m = re.match(r"^(\d*)'[sS]?([bBoOdDhH])([0-9a-fA-FxXzZ_]+)$", raw)
    if m:
        width = int(m.group(1)) if m.group(1) else None
        base = m.group(2).lower(); digits = m.group(3).replace('_', '')
        if re.search(r'[xXzZ]', digits):
            return ('num', width, None, raw)
        val = int(digits, {'b': 2, 'o': 8, 'd': 10, 'h': 16}[base])
        if width is None:
            width = max(1, val.bit_length())
        return ('num', width, val, raw)
    if re.match(r'^\d+$', raw):
        val = int(raw)
        return ('num', max(1, val.bit_length()), val, raw)
    raise _SErr(f"bad number {raw!r}")


def _const_int(node):
    """Return the integer value of a constant AST node, or None if not constant."""
    if node[0] == 'num':
        return node[2]
    return None


# ── width inference ──────────────────────────────────────────────────────────
def build_width_map(rtl_text, macros=None):
    """Map signal name -> bit width from module declarations. macros: dict name->int
    to resolve `MACRO bounds in [msb:lsb]."""
    wm = {}
    txt = re.sub(r'//[^\n]*', '', rtl_text)
    txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.DOTALL)
    decl = re.compile(r'\b(?:input|output|inout|wire|reg|logic)\b\s*(?:signed\s*)?'
                      r'(?:\[\s*([^\]:]+?)\s*:\s*([^\]]+?)\s*\]\s*)?'
                      r'([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*[;,)]')
    for m in decl.finditer(txt):
        msb, lsb = m.group(1), m.group(2)
        names = [x.strip() for x in m.group(3).split(',')]
        if msb is None:
            w = 1
        else:
            hi = _eval_bound(msb, macros); lo = _eval_bound(lsb, macros)
            w = (abs(hi - lo) + 1) if (hi is not None and lo is not None) else None
        for nm in names:
            if nm and (nm not in wm or wm[nm] is None):
                wm[nm] = w
    return wm


_BOUND_CACHE = {}


def _eval_bound(expr, macros):
    key = (expr, id(macros))
    hit = _BOUND_CACHE.get(key)
    if hit is not None or key in _BOUND_CACHE:
        return hit
    r = __eval_bound(expr, macros)
    _BOUND_CACHE[key] = r
    return r


def __eval_bound(expr, macros):
    e = expr.strip()
    if macros:
        for _ in range(6):
            m = re.search(r'`?(\w+)', e)
            done = True
            for mm in re.finditer(r'`?([A-Za-z_]\w*)', e):
                nm = mm.group(1)
                if nm in macros:
                    e = e[:mm.start()] + str(macros[nm]) + e[mm.end():]
                    done = False
                    break
            if done:
                break
    if re.fullmatch(r'[0-9()+\-*/ ]+', e):
        try:
            return int(eval(e, {'__builtins__': {}}))
        except Exception:
            return None
    m = re.fullmatch(r'\d+', e)
    return int(e) if m else None


_WIDTH_CACHE = {}


def width_of(node, wm, macros=None):
    """Infer the bit width of an AST node (memoized; env-independent). int or None."""
    key = (node, id(wm))
    hit = _WIDTH_CACHE.get(key)
    if hit is not None or key in _WIDTH_CACHE:
        return hit
    r = _width_of(node, wm, macros)
    _WIDTH_CACHE[key] = r
    return r


def _width_of(node, wm, macros=None):
    t = node[0]
    if t == 'num':
        return node[1]
    if t == 'id':
        return wm.get(node[1].lstrip('`'), None)
    if t == 'bit':
        return 1
    if t == 'part':
        hi = _const_int(node[2]); lo = _const_int(node[3])
        if hi is None:
            hi = _eval_bound(_flatten(node[2]), macros)
        if lo is None:
            lo = _eval_bound(_flatten(node[3]), macros)
        return abs(hi - lo) + 1 if (hi is not None and lo is not None) else None
    if t == 'idxpart':
        w = _const_int(node[3])
        return w if w is not None else _eval_bound(_flatten(node[3]), macros)
    if t == 'un':
        return 1 if node[1] == '!' else width_of(node[2], wm, macros)
    if t == 'red':
        return 1
    if t == 'bin':
        op = node[1]
        if op in ('&&', '||', '==', '!=', '<', '>', '<=', '>='):
            return 1
        a = width_of(node[2], wm, macros); b = width_of(node[3], wm, macros)
        return max([x for x in (a, b) if x is not None], default=None)
    if t == 'tern':
        a = width_of(node[2], wm, macros); b = width_of(node[3], wm, macros)
        return max([x for x in (a, b) if x is not None], default=None)
    if t == 'concat':
        ws = [width_of(p, wm, macros) for p in node[1]]
        return sum(ws) if all(w is not None for w in ws) else None
    if t == 'rep':
        w = width_of(node[2], wm, macros)
        return node[1] * w if w is not None else None
    return None


# ── S2: recursive multi-bit lowering to gates ────────────────────────────────
# Grounds at real netlist nets (registers/ports/surviving comb nets). A signal that
# is local-combinational (has an `always @*` and is absent from the netlist) is
# rebuilt recursively from its RTL. Registers (always @posedge) ground at their Q, so
# combinational feedback loops terminate.

_DEF_CELLS = {'INV': 'INVD1BWP136P5M156H3P48CPDLVT', 'AND2': 'AN2D1BWP136P5M156H3P48CPDLVT',
              'OR2': 'OR2D1BWP136P5M156H3P48CPDLVT', 'INR2': 'INR2D1BWP136P5M156H3P48CPDLVT'}


class _Synth:
    def __init__(self, cfg, wm, rtl_text, in_netlist, mk, module, cells=None):
        self.cfg = cfg
        self.wm = wm
        self.rtl = rtl_text
        self.in_netlist = in_netlist
        self.mk = mk
        self.module = module
        self.cells = cells or _DEF_CELLS
        self.macros = {k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None} if cfg else {}
        self.gates = []
        self._sig_cache = {}      # signal name -> {bit: net} (rebuilt comb signals)
        self._building = set()    # cycle guard

    # gate emit
    def _g(self, cell, fn, pc):
        out = pc.get('Z') or pc.get('ZN')
        self.gates.append({'change_type': 'new_logic_gate', 'cell_type': cell,
                           'gate_function': fn, 'output_net': out, 'module_name': self.module,
                           'port_connections': pc, 'confirmed': True, 'source': 'eco_rtl_synth',
                           'reason': f'cone-rebuild {fn}'})
        return out

    def _inv(self, a):
        if isinstance(a, str) and a in ("1'b0",):
            return "1'b1"
        if isinstance(a, str) and a in ("1'b1",):
            return "1'b0"
        return self._g(self.cells['INV'], 'INV', {'I': a, 'ZN': self.mk('inv')})

    def _and(self, a, b):
        if a == "1'b0" or b == "1'b0":
            return "1'b0"
        if a == "1'b1":
            return b
        if b == "1'b1":
            return a
        return self._g(self.cells['AND2'], 'AND2', {'A1': a, 'A2': b, 'Z': self.mk('and')})

    def _or(self, a, b):
        if a == "1'b1" or b == "1'b1":
            return "1'b1"
        if a == "1'b0":
            return b
        if b == "1'b0":
            return a
        return self._g(self.cells['OR2'], 'OR2', {'A1': a, 'A2': b, 'Z': self.mk('or')})

    def _mux(self, sel, a, b):
        """sel ? a : b."""
        if sel == "1'b1":
            return a
        if sel == "1'b0":
            return b
        return self._or(self._and(sel, a), self._and(self._inv(sel), b))

    def _reduce(self, nets, op):
        cur = list(nets)
        if not cur:
            return "1'b0"
        while len(cur) > 1:
            a, b = cur.pop(0), cur.pop(0)
            cur.append(self._and(a, b) if op == 'and' else
                       self._or(a, b) if op == 'or' else self._xor(a, b))
        return cur[0]

    def _xor(self, a, b):
        # a ^ b = (a & ~b) | (~a & b) — built from AND/OR/INV (no XOR cell needed)
        return self._or(self._and(a, self._inv(b)), self._and(self._inv(a), b))

    # width helper (macro-field bit-select `x[`FIELD]` is really a part-select)
    def _w(self, node):
        if node[0] == 'bit':
            ps = self._macro_field(node[2])
            if ps:
                return ps[1] - ps[0] + 1
        return width_of(node, self.wm, self.macros)

    def _macro_field(self, idx_node):
        """If idx_node is a `FIELD macro that resolves to a part-select range, return
        (lsb, msb); else None."""
        if idx_node[0] == 'id' and self.cfg:
            return self.cfg.part_select(idx_node[1].lstrip('`'))
        return None

    # constant width from cfg for idxpart width nodes
    def _cint(self, node):
        v = _const_int(node)
        if v is not None:
            return v
        if node[0] == 'id':
            return self.cfg.value(node[1].lstrip('`')) if self.cfg else None
        return _eval_bound(_flatten(node), self.macros)

    # ── SCALAR (1-bit boolean value of a node) ──
    def scalar(self, node):
        w = self._w(node)
        if node[0] in ('bin',) and node[1] in ('&&', '||', '==', '!=', '<', '>', '<=', '>='):
            return self.bit(node, 0)
        if node[0] == 'red' or node[0] == 'un' and node[1] == '!':
            return self.bit(node, 0)
        if w == 1 or w is None:
            return self.bit(node, 0)
        # multi-bit in boolean context => reduction OR (nonzero)
        return self._reduce([self.bit(node, i) for i in range(w)], 'or')

    # ── BIT i of a node ──
    def bit(self, node, i):
        t = node[0]
        if t == 'num':
            if node[2] is None:
                raise _SErr(f"x/z constant unsupported: {node[3]}")
            return "1'b1" if (node[2] >> i) & 1 else "1'b0"
        if t == 'id':
            nm = node[1]
            bare = nm.lstrip('`')
            # a `MACRO is a constant (enum/opcode/param), not a signal — synthesize its
            # RTL definition (sized literal `2'b11`, nested macro, or arithmetic).
            is_macro = nm.startswith('`') or (self.cfg and bare in self.cfg.defs
                                              and bare not in self.wm and not self.in_netlist(bare))
            if is_macro and self.cfg:
                raw = self.cfg.defs.get(bare)
                if raw is not None and raw.strip() and ':' not in raw:  # skip range macros
                    try:
                        return self.bit(parse_expr(raw), i)
                    except _SErr:
                        pass
                v = self.cfg.value(bare)
                if v is not None:
                    return "1'b1" if (v >> i) & 1 else "1'b0"
            return self._sig_bit(nm, i)
        if t == 'bit':
            ps = self._macro_field(node[2])          # x[`FIELD] with a range macro
            if ps:
                return self._sig_bit(_base_name(node[1]), ps[0] + i)
            idx = self._cint(node[2])
            if idx is None:
                raise _SErr(f"non-constant bit index on {_base_name(node[1])}[{node[2]}]")
            return self._sig_bit(_base_name(node[1]), idx)
        if t == 'part':
            hi = self._cint(node[2]); lo = self._cint(node[3])
            if hi is None or lo is None:
                raise _SErr("non-constant part-select")
            return self._sig_bit(_base_name(node[1]), min(hi, lo) + i)
        if t == 'idxpart':
            base = self._cint(node[2])
            if base is None:
                raise _SErr("non-constant indexed part-select base")
            return self._sig_bit(_base_name(node[1]), base + i if node[4] else base - i)
        if t == 'un':
            if node[1] == '~':
                return self._inv(self.bit(node[2], i))
            if node[1] == '!':
                return self._inv(self.scalar(node[2]))
            raise _SErr(f"unary {node[1]} unsupported")
        if t == 'red':
            inner = node[2]
            w = self._w(inner)
            if not w:
                raise _SErr(f"reduction width unknown for {inner}")
            bits = [self.bit(inner, k) for k in range(w)]
            rop = 'and' if node[1] in ('&', '~&') else 'xor' if node[1] in ('^', '~^') else 'or'
            base = self._reduce(bits, rop)
            return self._inv(base) if node[1] in ('~&', '~|', '~^') else base
        if t == 'bin':
            return self._bin_bit(node, i)
        if t == 'tern':
            return self._mux(self.scalar(node[1]), self.bit(node[2], i), self.bit(node[3], i))
        if t == 'concat':
            return self._concat_bit(node[1], i)
        if t == 'rep':
            return self._concat_bit([node[2]] * node[1], i)
        raise _SErr(f"cannot lower node {t}")

    def _bin_bit(self, node, i):
        op, a, b = node[1], node[2], node[3]
        if op == '&':
            return self._and(self.bit(a, i), self.bit(b, i))
        if op == '|':
            return self._or(self.bit(a, i), self.bit(b, i))
        if op == '^':
            return self._xor(self.bit(a, i), self.bit(b, i))
        if op in ('&&',):
            return self._and(self.scalar(a), self.scalar(b))
        if op in ('||',):
            return self._or(self.scalar(a), self.scalar(b))
        if op in ('==', '!='):
            return self._eq(a, b, op == '!=')
        if op in ('<', '>', '<=', '>='):
            return self._cmp(a, b, op)
        raise _SErr(f"binary op {op} unsupported")

    def _cmp(self, a, b, op):
        """Unsigned magnitude comparator -> 1-bit net. Built from AND/OR/INV only."""
        wa = self._w(a); wb = self._w(b)
        w = max([x for x in (wa, wb) if x], default=1)
        # a>b = OR_k ( a_k & ~b_k & AND_{j>k} (a_j==b_j) ); a<b symmetric.
        gt_terms, lt_terms, eq_terms = [], [], []
        eq_bits = [self._xnor(self.bit(a, k), self.bit(b, k)) for k in range(w)]
        for k in range(w):
            higher_eq = self._reduce(eq_bits[k + 1:], 'and') if k + 1 < w else "1'b1"
            ak, bk = self.bit(a, k), self.bit(b, k)
            gt_terms.append(self._and(self._and(ak, self._inv(bk)), higher_eq))
            lt_terms.append(self._and(self._and(self._inv(ak), bk), higher_eq))
        gt = self._reduce(gt_terms, 'or')
        lt = self._reduce(lt_terms, 'or')
        eq = self._reduce(eq_bits, 'and')
        if op == '>':
            return gt
        if op == '<':
            return lt
        if op == '>=':
            return self._or(gt, eq)
        return self._or(lt, eq)   # '<='

    def _eq(self, a, b, neg):
        wa = self._w(a); wb = self._w(b)
        w = max([x for x in (wa, wb) if x], default=1)
        terms = []
        for k in range(w):
            terms.append(self._xnor(self.bit(a, k), self.bit(b, k)))
        eq = self._reduce(terms, 'and')
        return self._inv(eq) if neg else eq

    def _xnor(self, a, b):
        # a==b (1 bit) = (a&b)|(~a&~b)
        return self._or(self._and(a, b), self._and(self._inv(a), self._inv(b)))

    def _concat_bit(self, parts, i):
        # parts are MSB-first (Verilog {a,b}: a is MSB). bit i counts from LSB.
        widths = [self._w(p) for p in parts]
        if any(w is None for w in widths):
            raise _SErr("concat with unknown-width part")
        # walk from LSB (last part) upward
        off = 0
        for p, w in zip(reversed(parts), reversed(widths)):
            if i < off + w:
                return self.bit(p, i - off)
            off += w
        raise _SErr(f"concat bit {i} out of range")

    # ── signal bit: ground at netlist net, else recursively rebuild ──
    def _sig_bit(self, name, i):
        name = name.lstrip('`')
        w = self.wm.get(name)
        netname = name if (w in (1, None)) else f'{name}[{i}]'
        # ground if present in the netlist (register Q, port, surviving comb net)
        if self.in_netlist(name):
            return netname
        # a REGISTER (assigned via nonblocking <=) is a real flop — its Q is a netlist
        # net (renamed by P&R); ground it as a leaf and let per-stage resolution map it.
        # Recursion stops here, so combinational feedback through registers terminates.
        if self._is_reg(name):
            return netname
        # PER-BIT combinational driver: `sig[k] = expr` (each bit its own always block,
        # e.g. WckIsInSync[k] = |WckSyncCtrk | ...). Must lower bit i from ITS OWN driver,
        # not replicate bit 0. Only when there is no whole-signal driver.
        pb = self._perbit_bit(name, i)
        if pb is not None:
            return pb
        # local COMBINATIONAL signal (whole-signal blocking =) -> rebuild recursively
        if name in self._building:
            raise _SErr(f"combinational cycle on {name}")
        rebuilt = self._rebuild(name)
        if rebuilt is None:
            raise _SErr(f"signal {name!r} not in netlist, not a register, not rebuildable")
        return rebuilt.get(i, "1'b0")

    def _perbit_bit(self, name, i):
        """Lower bit i of a signal driven bit/part-wise (`name[SEL] = RHS`). Returns the
        net, or None if `name` is not per-bit driven (has a whole-signal driver or no
        matching bit driver). Memoized per (name, i)."""
        from eco_cone_rebuild import has_whole_driver, perbit_drivers
        if not hasattr(self, '_pb_cache'):
            self._pb_cache = {}
            self._pb_whole = {}
        if name not in self._pb_whole:
            self._pb_whole[name] = has_whole_driver(self.rtl, name)
        if self._pb_whole[name]:
            return None
        key = (name, i)
        if key in self._pb_cache:
            return self._pb_cache[key]
        for sel, rhs in perbit_drivers(self.rtl, name):
            lo, hi = self._sel_range(sel)
            if lo is None or not (lo <= i <= hi):
                continue
            if name in self._building:
                raise _SErr(f"combinational cycle on {name}")
            self._building.add(name)
            try:
                r = self.bit(parse_expr(rhs), i - lo)
            finally:
                self._building.discard(name)
            self._pb_cache[key] = r
            return r
        return None

    def _sel_range(self, sel):
        """A bit/part-select LHS string -> (lo, hi) inclusive, or (None, None). Handles a
        plain index, `MSB:LSB`, and a `FIELD macro (range or scalar)."""
        s = sel.strip()
        if ':' in s:
            a, b = s.split(':', 1)
            hi = _eval_bound(a, self.macros); lo = _eval_bound(b, self.macros)
            if hi is None or lo is None:
                return (None, None)
            return (min(hi, lo), max(hi, lo))
        ps = self.cfg.part_select(s.lstrip('`')) if self.cfg else None
        if ps:
            return ps
        v = _eval_bound(s, self.macros)
        return (v, v) if v is not None else (None, None)

    def _is_reg(self, name):
        if not hasattr(self, '_reg_cache'):
            self._reg_cache = {}
        if name not in self._reg_cache:
            if not hasattr(self, '_stripped'):
                self._stripped = re.sub(r'/\*.*?\*/', '', re.sub(r'//[^\n]*', '', self.rtl), flags=re.DOTALL)
            self._reg_cache[name] = bool(
                re.search(r'\b' + re.escape(name) + r'\s*(?:\[[^\]]*\])?\s*<=', self._stripped))
        return self._reg_cache[name]

    def _cont_assign_rhs(self, name):
        """RHS of a continuous assignment (`assign name = ...;` or `wire ... name = ...;`).
        Returns the RHS string or None. Reached only after the always-block tree is empty,
        so a `name = expr;` here is a continuous (combinational) driver, not a procedural
        blocking assign (which would live inside an always block parse_always would find)."""
        if not hasattr(self, '_ca_cache'):
            self._ca_cache = {}
        if name in self._ca_cache:
            return self._ca_cache[name]
        if not hasattr(self, '_stripped'):
            self._stripped = re.sub(r'/\*.*?\*/', '', re.sub(r'//[^\n]*', '', self.rtl), flags=re.DOTALL)
        # `name =` but not `<=`, `==`, `>=`, `<=`, `!=` (the \s* won't swallow a leading
        # operator char, and (?!=) rejects `==`).
        m = re.search(r'\b' + re.escape(name) + r'\s*=(?!=)\s*(.*?);', self._stripped, re.DOTALL)
        rhs = m.group(1).strip() if m else None
        self._ca_cache[name] = rhs
        return rhs

    def _rebuild(self, name):
        if name in self._sig_cache:
            return self._sig_cache[name]
        try:
            tree = _parse_always_for(self.rtl, name)
        except Exception:
            tree = None
        has_always = bool(tree and (tree.get('default') is not None or tree.get('assigns')))
        self._building.add(name)
        try:
            w = self.wm.get(name) or 1
            if has_always:
                dflt_ast = parse_expr(tree['default']) if tree.get('default') is not None else None
                branch_asts = [(cond, parse_expr(val)) for cond, val in tree['assigns']]
                bits = {}
                for i in range(w):
                    cur = self.bit(dflt_ast, i) if dflt_ast is not None else "1'b0"
                    for cond, vast in branch_asts:
                        sel = self._path_scalar(cond)
                        cur = self._mux(sel, self.bit(vast, i), cur)
                    bits[i] = cur
            else:
                # continuous assignment (`assign`/`wire name = expr;`)
                rhs = self._cont_assign_rhs(name)
                if rhs is None:
                    return None
                vast = parse_expr(rhs)
                bits = {i: self.bit(vast, i) for i in range(w)}
            self._sig_cache[name] = bits
            return bits
        finally:
            self._building.discard(name)

    def _path_scalar(self, path_cond):
        """AND of (expr,sense) terms -> 1-bit net."""
        net = "1'b1"
        for expr, sense in path_cond:
            s = self.scalar(parse_expr(expr))
            if not sense:
                s = self._inv(s)
            net = self._and(net, s)
        return net


def _base_name(node):
    if node[0] == 'id':
        return node[1]
    if node[0] in ('bit', 'part', 'idxpart'):
        return _base_name(node[1])
    raise _SErr(f"cannot take base of {node[0]}")


def _flatten(node):
    """Best-effort flatten of a small constant expression node to a string for eval."""
    if node[0] == 'num':
        return str(node[2]) if node[2] is not None else '0'
    if node[0] == 'id':
        return node[1].lstrip('`')
    if node[0] == 'bin':
        return f'({_flatten(node[2])}{node[1]}{_flatten(node[3])})'
    if node[0] == 'un' and node[1] == '-':
        return f'(-{_flatten(node[2])})'
    return '0'


# parse_always is provided by eco_cone_rebuild; import lazily to avoid a cycle.
def _parse_always_for(rtl_text, signal):
    from eco_cone_rebuild import parse_always
    return parse_always(rtl_text, signal)


if __name__ == '__main__':
    import sys, json
    e = sys.argv[1]
    ast = parse_expr(e)
    print(json.dumps(ast, indent=1, default=str))
