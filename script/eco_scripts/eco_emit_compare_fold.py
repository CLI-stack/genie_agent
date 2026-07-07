#!/usr/bin/env python3
"""eco_emit_compare_fold.py — deterministic emitter for a COMPARE-OPERAND OR-fold: an
RTL change of the form `term | R` inserted into one operand of an equality compare that
feeds a registered signal (optionally replicated across a uniquified generate array).

This is NOT a plain OR-widen at the flop D-net (`D | R`). The fold lives INSIDE an
equality comparison operand, so in the netlist it becomes a localized force on the
SHARED mismatch net of the compared term-pair — a distinct pattern the generic
`and_term` OR-widen handler models incorrectly.

  RTL:  {.. , op | (|Rbus)} == {.. , cmp_bit | (|Rbus)}   (in SOME branches only)

The synthesized netlist represents the compared term-pair by a single MISMATCH net
  M = opA ^ opB
shared across ALL branches of the register's next-state. The fold must apply ONLY in
the branches that fold in RTL, so a naive global `M & ~R` corrupts the sibling
(unfolded) branches on reachable states -> FM fail. Correct fold:

  M_new = M & ~( R & S )

where S is the LOCALIZATION LITERAL: a single bit of a STAGE-STABLE branch-select field
(a module-input bus, so it survives P&R) that is 1 for every folded-branch opcode and 0
for every unfolded (sibling) opcode. Derived from the branch guards + decode wires.

Everything is SELF-DERIVED from the ECO'd RTL (`context_line` + full always-block) and
the PreEco netlist — nothing is hardcoded to any JIRA. Emission (surgical net-force,
preserves the net name so all fanout follows):
  1. rename M's driver output  M -> n_eco_<jira>_cf_m   (raw opA^opB)
  2. OR-reduce Rbus            -> n_eco_<jira>_cf_r
  3. AND2/INR2(r, S)           -> n_eco_<jira>_cf_rs     (= R & S, S polarity honored)
  4. INR2(m, rs)  (ZN=A1&~B1)  -> M                      (= M & ~(R & S))

Exhaustive local self-check (opA,opB,Rbits,S) hard-gates against silent-wrong. Handles
XOR/XNOR mismatch polarity, uniquified families (per-copy own mismatch net), and per-stage
leaf renaming (via the fenets rename map). Fail-closed (exit 2, study untouched) whenever a
param can't be derived or a case is unsupported (multi-bit operand, non-clean mismatch,
no separating literal).

Usage (study integration):
  python3 eco_emit_compare_fold.py --rtl-diff <d> --study <s> --jira <J> --ref-dir <R> \
      [--rename-map <m>] --output <s>
Standalone single-module debug:
  python3 eco_emit_compare_fold.py --ref-dir <R> --netlist-module <M> --jira <J> \
      --context-line "<rtl>" --fold-signal <sig> --compare-signal <reg>
"""
import argparse, itertools, json, os, re, sys
import eco_netlist_sim as ns
from eco_cone_rebuild import parse_always
from eco_extract_pf_condition import resolve_rtl
from eco_rtl_config import RtlConfig
try:
    from eco_rtl_synth import build_width_map
except Exception:
    build_width_map = None
try:
    from eco_emit_priority_force import _resolve_cells as _pf_resolve_cells
except Exception:
    _pf_resolve_cells = None

STAGES = ('Synthesize', 'PrePlace', 'Route')
_DEF = {'OR2': 'OR2D1BWP136P5M156H3P48CPDLVT',
        'AND2': 'AND2D1BWP136P5M156H3P48CPDLVT',
        'INR2': 'INR2D1BWP136P5M156H3P48CPDLVT'}  # ZN = A1 & ~B1
_CLK = ('CP', 'CPN', 'CK', 'CKB', 'CLK')


# ────────────────────────────── netlist analysis ──────────────────────────────
def find_mismatch_net(insts, drivers, opA, opB):
    """Find the net whose local function == opA ^ opB over leaves {opA, opB}.
    Returns list of (net, driver_inst, driver_pin, polarity) polarity in {XOR, XNOR}."""
    want = {opA, opB}
    hits = []
    for net, (inst, pin, seq) in drivers.items():
        if seq:
            continue
        order, leaves = ns.cone_of(net, insts, drivers)
        real = {l for l in leaves if not ns._is_const(l)}
        if not real or not real <= want:
            continue
        vals = [ns.simulate(order, insts, {opA: a, opB: b}).get(net, 0) & 1
                for a, b in itertools.product((0, 1), repeat=2)]
        xor = [a ^ b for a, b in itertools.product((0, 1), repeat=2)]
        if vals == xor:
            hits.append((net, inst, pin, 'XOR'))
        elif vals == [1 - x for x in xor]:
            hits.append((net, inst, pin, 'XNOR'))
    return hits


def fanout_sinks(body):
    """net -> list of (inst, pins, is_seq, out_nets) for every instance reading it."""
    inst_re = re.compile(r'([A-Za-z][\w]*)\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*;', re.S)
    pin_re = re.compile(r'\.\s*([A-Za-z][\w]*)\s*\(\s*([^)]*?)\s*\)')
    _OUT = ('ZN', 'Z', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN', 'Q1', 'Q2', 'Q3')
    adj = {}
    for m in inst_re.finditer(body):
        cell, inst, pinstr = m.group(1), m.group(2), m.group(3)
        if cell in ('module', 'wire', 'input', 'output', 'inout', 'reg', 'assign', 'endmodule'):
            continue
        pins = {p: n.strip() for p, n in pin_re.findall(pinstr)}
        isseq = any(cp in pins for cp in _CLK)
        outs = [pins[p] for p in pins if p in _OUT]
        for p, n in pins.items():
            adj.setdefault(n, []).append((inst, pins, isseq, outs))
    return adj


def reaches_only(body, net, want_flop_substr):
    """Forward-reachability: does `net` reach ONLY sequential sinks whose instance name
    contains want_flop_substr? Returns (ok, other_flop_insts)."""
    adj = fanout_sinks(body)
    seen, frontier, other = set(), [net], set()
    while frontier:
        n = frontier.pop()
        for inst, pins, isseq, outs in adj.get(n, []):
            if isseq:
                if want_flop_substr not in inst:
                    other.add(inst)
                continue
            for o in outs:
                if o and o not in seen:
                    seen.add(o); frontier.append(o)
    return (not other), other


def separating_literal(field_signal, field_range, folded_ops, unfolded_ops):
    """A single field bit that is 1 for all folded_ops and 0 for all unfolded_ops (or the
    inverse). Opcodes are MSB..LSB binary strings over the field. Returns
    (net_name, polarity) — polarity True = S = bit, False = S = ~bit — or None."""
    lo, hi = min(field_range), max(field_range)
    width = hi - lo + 1
    if not folded_ops or not unfolded_ops:
        return None
    if any(len(s) != width for s in list(folded_ops) + list(unfolded_ops)):
        return None
    for i in range(width):
        fbits = {s[width - 1 - i] for s in folded_ops}
        ubits = {s[width - 1 - i] for s in unfolded_ops}
        if fbits == {'1'} and ubits == {'0'}:
            return (f'{field_signal}[{lo + i}]', True)
        if fbits == {'0'} and ubits == {'1'}:
            return (f'{field_signal}[{lo + i}]', False)
    return None


# ────────────────────────── RTL self-derivation ──────────────────────────
def _split_top(s, sep=','):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _bare(operand):
    """Leading signal token (optionally bit/part-select), stripping any fold `| ...`."""
    m = re.match(r'^\s*([\w]+(?:\[[^\]]+\])?)', operand)
    return m.group(1) if m else operand.strip()


def _mopbin(cfg, macro):
    raw = cfg.defs.get(macro.lstrip('`'))
    if raw and "'b" in raw:
        return raw.strip().strip('"').split('b')[1]
    return None


def _decode_map(rtl):
    """signal -> set of (field_token, opcode_macro) for combinational decodes of the form
    `field[FLD] == MOP` (transitively resolved through || / | of other decode wires)."""
    defs = {}
    for m in re.finditer(r'\b(?:wire|assign)\s+(?:\[[^\]]*\]\s*)?(\w+)\s*=\s*([^;]+);', rtl):
        defs[m.group(1)] = m.group(2)

    def direct(expr):
        return {(f.replace(' ', ''), op.lstrip('`'))
                for f, op in re.findall(r'(\w+\s*\[\s*`?\w+\s*\])\s*==\s*(`?\w+)', expr)}

    dm = {k: direct(v) for k, v in defs.items()}
    for _ in range(6):                                   # fixpoint over transitive refs
        changed = False
        for k, expr in defs.items():
            for ref in re.findall(r'\b(\w+)\b', expr):
                if ref in dm and ref != k and dm[ref] - dm[k]:
                    dm[k] |= dm[ref]; changed = True
        if not changed:
            break
    return {k: v for k, v in dm.items() if v}


def _own_atoms(cond):
    """Signals in a branch's OWN guard (the sense=True terms) -> {signal: positive}. The
    negated priority-prefix (sense=False) is a priority exclusion, NOT a requirement."""
    atoms = {}
    for expr, sense in cond:
        if not sense:
            continue
        for tok in re.split(r'&+', expr):
            t = tok.strip()
            if not t:
                continue
            neg = t.startswith('~'); t = t.lstrip('~').strip()
            if re.match(r'^\w+$', t):
                atoms[t] = not neg
    return atoms


def _resolve_bit(operand, cfg):
    """Resolve a compared operand to a concrete net: `sig[`MACRO]` -> `sig[bit]` (single-
    bit fields only), `sig[N]` / `sig` pass through. Returns (net, err)."""
    operand = operand.strip()
    m = re.match(r'^(\w+)\s*\[\s*`?(\w+)\s*\]$', operand)
    if not m:
        return operand, None
    base, idx = m.group(1), m.group(2)
    if idx.isdigit():
        return f'{base}[{idx}]', None
    ps = cfg.part_select(idx.lstrip('`'))
    if ps is None:
        return None, f'cannot resolve field macro {idx!r} on {base}'
    lo, hi = min(ps), max(ps)
    if lo != hi:
        return None, f'{base}[{idx}] is a multi-bit field {ps} - multi-bit fold unsupported'
    return f'{base}[{lo}]', None


def derive_params(ref_dir, module, context_line, fold_signal, compare_signal):
    """Self-derive the compare-fold parameters from the ECO'd RTL. Returns (params, errors).
    params: operand_a, operand_b(net), fold_bits[], field_signal, field_range,
            folded_ops(set), unfolded_ops(set), s_net, s_pol."""
    errs = []
    try:
        rtl = open(resolve_rtl(ref_dir=ref_dir, module=module, subdir='SynRtl'),
                   errors='replace').read()
    except Exception as e:
        return None, [f'cannot open ECO RTL for {module}: {e}']
    cfg = RtlConfig(ref_dir)
    tree = parse_always(rtl, compare_signal)
    if not tree or not tree.get('assigns'):
        return None, [f'no always-block for {compare_signal} in {module} ECO RTL']
    dm = _decode_map(rtl)
    foldpat = re.compile(r'\|\s*\(\s*\|\s*' + re.escape(fold_signal) + r'\s*\)'
                         r'|\|\s*' + re.escape(fold_signal) + r'\b')

    operands = None
    folded, all_branches = [], []
    for cond, rhs in tree['assigns']:
        cmp = re.search(r'\{([^}]*)\}\s*==\s*\{([^}]*)\}', rhs)
        if not cmp:
            continue
        L = _split_top(cmp.group(1)); Rr = _split_top(cmp.group(2))
        pos = next((i for i, o in enumerate(L) if foldpat.search(o)), None)
        all_branches.append((cond, L, Rr, pos))
        if pos is not None and operands is None and pos < len(Rr):
            operands = (_bare(L[pos]), _split_top(Rr[pos], '|')[0].strip())
    if operands is None:
        return None, [f'no compare operand carries `| (|{fold_signal})` in {compare_signal}']
    opA = operands[0]
    opB, e = _resolve_bit(operands[1], cfg)
    if e:
        errs.append(e)
    # folded = branches with the fold; unfolded = sibling branches using opA WITHOUT fold
    for cond, L, Rr, pos in all_branches:
        if pos is not None:
            folded.append(cond)
    unfolded = [cond for cond, L, Rr, pos in all_branches
                if pos is None and any(_bare(o) == opA for o in L)]
    if not folded:
        return None, ['no folded branch found']

    # discriminator decode: signal positive in every folded OWN guard, negated in every unfolded
    fa = [_own_atoms(c) for c in folded]
    ua = [_own_atoms(c) for c in unfolded]
    disc = None
    for sig in dm:
        if all(a.get(sig) is True for a in fa) and (not ua or all(a.get(sig) is False for a in ua)):
            disc = sig; break
    if disc is None:
        errs.append('no decode signal cleanly separates folded vs unfolded branch guards')
        return None, errs

    # field + folded opcodes from the discriminator decode
    field_tok = sorted(dm[disc])[0][0]
    fm = re.search(r'(\w+)\s*\[\s*`?(\w+)\s*\]', field_tok)
    if not fm:
        return None, [f'cannot parse field from decode {disc}: {field_tok}']
    field_signal, fld = fm.group(1), fm.group(2)
    frange = cfg.part_select(fld) if not fld.isdigit() else (int(fld), int(fld))
    if frange is None:
        return None, [f'cannot resolve field range for {fld}']
    folded_ops = set()
    for _, op in dm[disc]:
        b = _mopbin(cfg, op)
        if b:
            folded_ops.add(b)
    # unfolded opcodes: positive decodes in unfolded own guards, minus the folded set
    unf_ops = set()
    for a in ua:
        for sig, val in a.items():
            if val and sig in dm and sig != disc:
                for _, op in dm[sig]:
                    b = _mopbin(cfg, op)
                    if b:
                        unf_ops.add(b)
    unf_ops -= folded_ops
    S = separating_literal(field_signal, frange, folded_ops, unf_ops)
    if S is None:
        errs.append(f'no single separating literal over {field_signal}[{frange}] for '
                    f'folded={sorted(folded_ops)} unfolded={sorted(unf_ops)}')
        return None, errs

    # fold bus bits (reduction-OR): width from RTL declaration / width-map
    fold_bits = _fold_bus_bits(rtl, fold_signal)
    if not fold_bits:
        errs.append(f'cannot determine width of fold signal {fold_signal}')
        return None, errs

    if errs:
        return None, errs
    return {
        'operand_a': opA, 'operand_b': opB, 'fold_bits': fold_bits,
        'field_signal': field_signal, 'field_range': list(frange),
        'folded_ops': sorted(folded_ops), 'unfolded_ops': sorted(unf_ops),
        's_net': S[0], 's_pol': S[1], 'discriminator': disc,
    }, []


def _fold_bus_bits(rtl, fold_signal):
    """Return the list of bit-nets the reduction-OR reads: `sig[w-1]..sig[0]`, or [sig]
    for a scalar. Width from an `input/wire/reg [hi:lo] sig` declaration or width-map."""
    m = re.search(r'\b(?:input|output|wire|reg)\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*' +
                  re.escape(fold_signal) + r'\b', rtl)
    if m:
        hi, lo = int(m.group(1)), int(m.group(2))
        lo, hi = min(hi, lo), max(hi, lo)
        return [f'{fold_signal}[{b}]' for b in range(lo, hi + 1)]
    if build_width_map is not None:
        try:
            w = build_width_map(rtl, {}).get(fold_signal)
            if w and w > 1:
                return [f'{fold_signal}[{b}]' for b in range(w)]
        except Exception:
            pass
    # scalar fold
    return [fold_signal]


def derive_nets(ref_dir, module, context_line, fold_signal, compare_signal):
    """Leaf nets step-2 must resolve per-stage: the two compare operands, the fold bus
    bits, and the separating-literal field bit. (The mismatch net + its driver are found
    per-stage by the emitter itself from the netlist, so they are NOT listed here.)"""
    p, errs = derive_params(ref_dir, module, context_line, fold_signal, compare_signal)
    if errs or not p:
        return [], errs
    nets = [p['operand_a'], p['operand_b'], p['s_net']] + list(p['fold_bits'])
    seen, out = set(), []
    for n in nets:
        if n not in seen:
            seen.add(n); out.append(n)
    return out, []


# ────────────────────────────── gate emission ──────────────────────────────
def _selfcheck(gates, m_raw, M, Rbits, s_net, s_pol):
    """Exhaustively verify the emitted chain computes M = (opA^opB) & ~(R & S)."""
    diff = 0
    nbits = len(Rbits)
    for combo in itertools.product((0, 1), repeat=1 + nbits + 1):
        env = {m_raw: combo[0], s_net: combo[-1]}
        for i, rb in enumerate(Rbits):
            env[rb] = combo[1 + i]
        val = dict(env)
        for g in gates:
            pc = g['port_connections']; fn = g['gate_function']
            gv = lambda n: val.get(n, 0)
            if fn == 'OR2':
                val[pc['Z']] = gv(pc['A1']) | gv(pc['A2'])
            elif fn == 'AND2':
                val[pc['Z']] = gv(pc['A1']) & gv(pc['A2'])
            elif fn == 'INR2':
                val[pc['ZN']] = gv(pc['A1']) & (1 - gv(pc['B1']))
            else:
                return (False, -1)
        got = val.get(M, 0) & 1
        R = 0
        for rb in Rbits:
            R |= env[rb]
        S = env[s_net] if s_pol else (1 - env[s_net])
        want = combo[0] & (1 - (R & S))
        if got != want:
            diff += 1
    return (diff == 0, diff)


def _gate(inst, cell, fn, pc, out_net, pcs=None):
    return {'change_type': 'new_logic_gate', 'instance_name': inst, 'cell_type': cell,
            'gate_function': fn, 'output_net': out_net, 'port_connections': dict(pc),
            'port_connections_per_stage': pcs or {s: dict(pc) for s in STAGES},
            'confirmed': True, 'source': 'eco_emit_compare_fold'}


def _build_gates(jira, tag, m_raw, M, Rbits, s_net, s_pol, cells):
    """Build the fold gate chain for ONE resolved net-set (single stage view).
    Returns (gates, r_net)."""
    r_net = f'n_eco_{jira}_cf_r{tag}'
    rs_net = f'n_eco_{jira}_cf_rs{tag}'
    gates = []
    cur, level = list(Rbits), 0
    while len(cur) > 1:
        nxt = []
        for i in range(0, len(cur), 2):
            grp = cur[i:i + 2]
            if len(grp) == 1:
                nxt.append(grp[0]); continue
            o = r_net if len(cur) <= 2 else f'{r_net}_{level}_{i}'
            gates.append(_gate(f'eco_{jira}_cf_or{tag}_{level}_{i}', cells['OR2'], 'OR2',
                               {'A1': grp[0], 'A2': grp[1], 'Z': o}, o))
            nxt.append(o)
        cur, level = nxt, level + 1
    if len(Rbits) == 1:
        r_net = Rbits[0]
    if s_pol:
        gates.append(_gate(f'eco_{jira}_cf_and{tag}', cells['AND2'], 'AND2',
                           {'A1': r_net, 'A2': s_net, 'Z': rs_net}, rs_net))
    else:
        gates.append(_gate(f'eco_{jira}_cf_inrs{tag}', cells['INR2'], 'INR2',
                           {'A1': r_net, 'B1': s_net, 'ZN': rs_net}, rs_net))
    gates.append(_gate(f'eco_{jira}_cf_fold{tag}', cells['INR2'], 'INR2',
                       {'A1': m_raw, 'B1': rs_net, 'ZN': M}, M))
    return gates, r_net


def _cells_for(ref_dir, module):
    if _pf_resolve_cells:
        try:
            c = _pf_resolve_cells(ref_dir, module) or {}
            return {'OR2': c.get('OR2', _DEF['OR2']), 'AND2': c.get('AND2', _DEF['AND2']),
                    'INR2': c.get('INR2', _DEF['INR2'])}
        except Exception:
            pass
    return dict(_DEF)


def _stage_net(rename_map, scope, net, stage):
    """Per-stage resolved name via the fenets rename map; base name if absent."""
    if not rename_map:
        return net
    for key in ((f'{scope}/{net}' if scope else None), net):
        if key and isinstance(rename_map.get(key), dict) and rename_map[key].get(stage):
            return rename_map[key][stage]
    return net


# ─────────────────────────── study integration ───────────────────────────
def _family_copies(ref_dir, base, stage='Synthesize'):
    """index -> netlist module name for a uniquified child family in `stage`. P&R
    re-uniquifies with a trailing `_0` at Route (umcrecrcqentry_39 -> _39_0), so this must
    be resolved PER STAGE."""
    gz = f'{ref_dir}/data/PreEco/{stage}.v.gz'
    op = __import__('gzip').open
    rx = re.compile(r'^\s*module\s+(\S*' + re.escape(base) + r'_(\d+)(?:_0)?)\s*[(;]')
    out = {}
    try:
        with op(gz, 'rt', errors='replace') as f:
            for ln in f:
                m = rx.match(ln)
                if m:
                    out[int(m.group(2))] = m.group(1)
    except Exception:
        pass
    return out


def build_for_module(ref_dir, stage_mods, jira, params, tag='', rename_map=None,
                     scope='', compare_signal=''):
    """Emit the fold across all 3 stages. `stage_mods` maps stage -> netlist module name
    (P&R re-uniquifies the name at Route). Returns {'gates','rename','errors'}. Per-stage
    net names come from each stage's own netlist (mismatch net) + the rename map (leaves)."""
    cells = _cells_for(ref_dir, stage_mods['Synthesize'])
    m_raw = f'n_eco_{jira}_cf_m{tag}'
    per_stage_gates = {}          # stage -> gates
    per_stage_M = {}              # stage -> mismatch net
    per_stage_drv = {}            # stage -> (inst, pin)
    errs = []
    for st in STAGES:
        netlist_module = stage_mods.get(st)
        if not netlist_module:
            errs.append(f'no {st} module for copy{tag}'); continue
        gz = f'{ref_dir}/data/PreEco/{st}.v.gz'
        insts, drivers = ns.parse_module(gz, netlist_module, ref_dir=ref_dir)
        if not insts:
            errs.append(f'{netlist_module}: absent in {st}'); continue
        body = ns._module_body(gz, netlist_module)
        opA = _stage_net(rename_map, scope, params['operand_a'], st)
        opB = _stage_net(rename_map, scope, params['operand_b'], st)
        hits = [h for h in find_mismatch_net(insts, drivers, opA, opB) if h[3] == 'XOR']
        xnor = [h for h in find_mismatch_net(insts, drivers, opA, opB) if h[3] == 'XNOR']
        chosen = hits or xnor
        if not chosen:
            errs.append(f'{netlist_module}/{st}: no net computes {opA}^{opB}'); continue
        M, drv_inst, drv_pin, pol = chosen[0]
        ok, other = reaches_only(body, M, compare_signal or params.get('compare_signal', ''))
        if not ok:
            errs.append(f'{netlist_module}/{st}: {M} also feeds {sorted(other)} '
                        f'(net-split unsupported)'); continue
        s_net = _stage_net(rename_map, scope, params['s_net'], st)
        Rbits = [_stage_net(rename_map, scope, b, st) for b in params['fold_bits']]
        # XNOR polarity: M already inverted -> the raw m_raw would be ~(opA^opB); the fold
        # M_new = M & ~(R&S) still holds on the SAME net M, so no change needed (we drive
        # the same net; the renamed raw keeps the driver's native polarity).
        gates, _ = _build_gates(jira, tag, m_raw, M, Rbits, s_net, params['s_pol'], cells)
        ok2, nd = _selfcheck(gates, m_raw, M, Rbits, s_net, params['s_pol'])
        if not ok2:
            errs.append(f'{netlist_module}/{st}: self-check FAILED ({nd})'); continue
        per_stage_gates[st] = gates
        per_stage_M[st] = M
        per_stage_drv[st] = (drv_inst, drv_pin)
    if errs:
        return {'errors': errs, 'gates': [], 'rename': {}}
    # merge the 3 per-stage gate views into unified entries with port_connections_per_stage
    base_gates = per_stage_gates['Synthesize']
    merged = []
    for gi, g in enumerate(base_gates):
        pcs = {st: per_stage_gates[st][gi]['port_connections'] for st in STAGES}
        mg = dict(g); mg['port_connections_per_stage'] = pcs
        merged.append(mg)
    rename = {'change_type': 'rewire', 'module_name': stage_mods['Synthesize'],
              'module_name_per_stage': dict(stage_mods),
              'cell_name': per_stage_drv['Synthesize'][0],
              'cell_name_per_stage': {st: per_stage_drv[st][0] for st in STAGES},
              'pin': per_stage_drv['Synthesize'][1],
              'pin_per_stage': {st: per_stage_drv[st][1] for st in STAGES},
              'old_net': per_stage_M['Synthesize'],
              'old_net_per_stage': {st: per_stage_M[st] for st in STAGES},
              'new_net': m_raw, 'source': 'eco_emit_compare_fold'}
    return {'gates': merged, 'rename': rename, 'errors': []}


def emit_into_study(rtl_diff, study, jira, ref_dir, rename_map=None):
    """Process every compare_fold change; dedup by (module-family, operand pair, fold sig);
    emit per uniquified copy. Returns (n_emitted, errors)."""
    changes = [c for c in rtl_diff.get('changes', []) if c.get('change_type') == 'compare_fold']
    if not changes:
        return 0, []
    errs, n = [], 0
    seen = set()
    for c in changes:
        module = c.get('module_name')
        fold_signal = c.get('fold_signal') or c.get('new_token')
        compare_signal = c.get('compare_signal') or c.get('target_register')
        ctx = c.get('context_line', '')
        key = (module, fold_signal, compare_signal)
        if key in seen:
            continue
        seen.add(key)
        params, derr = derive_params(ref_dir, module, ctx, fold_signal, compare_signal)
        if derr:
            errs += [f'[{module}] {e}' for e in derr]; continue
        params['compare_signal'] = compare_signal
        fam = c.get('uniquified_family')
        scope = c.get('scope', '')
        # per-stage module maps (P&R renames): {index: {stage: modname}}
        by_stage = {st: _family_copies(ref_dir, fam or module, st) for st in STAGES}
        idxs = sorted(by_stage['Synthesize'].keys())
        if not idxs:
            nm = _resolve_netlist_module(ref_dir, module)
            idxs, by_stage = [0], {st: {0: nm} for st in STAGES}
        for i in idxs:
            stage_mods = {st: by_stage[st].get(i) for st in STAGES}
            r = build_for_module(ref_dir, stage_mods, jira, params,
                                 tag=(f'_{i}' if fam else ''), rename_map=rename_map,
                                 scope=scope, compare_signal=compare_signal)
            if r['errors']:
                errs += r['errors']; continue
            for g in r['gates']:
                g = dict(g)
                g['module_name'] = stage_mods['Synthesize']
                g['module_name_per_stage'] = dict(stage_mods)
                study.setdefault('Synthesize', []).append(g)
                study.setdefault('PrePlace', []).append(dict(g))
                study.setdefault('Route', []).append(dict(g))
            for st in STAGES:
                study.setdefault(st, []).append(dict(r['rename']))
            n += 1
    return n, errs


def _resolve_netlist_module(ref_dir, module):
    """Best-effort: find the netlist module name matching a short RTL module name."""
    gz = f'{ref_dir}/data/PreEco/Synthesize.v.gz'
    op = __import__('gzip').open
    try:
        with op(gz, 'rt', errors='replace') as f:
            for ln in f:
                m = re.match(r'^\s*module\s+(\S*' + re.escape(module) + r')\s*[(;]', ln)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl-diff')
    ap.add_argument('--study')
    ap.add_argument('--jira', default='chk')
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--rename-map')
    ap.add_argument('--output')
    # standalone debug mode
    ap.add_argument('--netlist-module')
    ap.add_argument('--context-line')
    ap.add_argument('--fold-signal')
    ap.add_argument('--compare-signal')
    args = ap.parse_args()

    if args.rtl_diff and args.study:
        rtl_diff = json.loads(open(args.rtl_diff).read())
        study = json.loads(open(args.study).read())
        rename_map = None
        if args.rename_map and os.path.isfile(args.rename_map):
            try:
                rename_map = json.loads(open(args.rename_map).read())
            except Exception:
                rename_map = None
        n, errs = emit_into_study(rtl_diff, study, args.jira, args.ref_dir, rename_map)
        marker_path = (args.output or args.study).replace('.json', '_compare_fold_marker.txt')
        if errs:
            marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_compare_fold.py\n"
                      f"  ABORTED — {len(errs)} issue(s):\n" +
                      "".join(f"    - {e}\n" for e in errs[:20]) +
                      "  Study UNTOUCHED.\n")
            print(marker); open(marker_path, 'w').write(marker); return 2
        open(args.output or args.study, 'w').write(json.dumps(study, indent=2))
        marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_compare_fold.py\n"
                  f"  compare_fold folds emitted (per copy): {n}\n")
        print(marker); open(marker_path, 'w').write(marker); return 0

    # standalone debug
    if not (args.netlist_module and args.context_line and args.fold_signal and args.compare_signal):
        ap.error('need --rtl-diff+--study OR --netlist-module+--context-line+--fold-signal+--compare-signal')
    short = re.sub(r'^ddrss_\w+?_t_', '', re.sub(r'_\d+$', '', args.netlist_module))
    params, errs = derive_params(args.ref_dir, short, args.context_line,
                                 args.fold_signal, args.compare_signal)
    if errs:
        print('DERIVE ABORT:', *errs, sep='\n  '); return 2
    params['compare_signal'] = args.compare_signal
    print('derived params:', json.dumps(params, indent=2))
    # resolve per-stage module names for the given copy
    im = re.search(r'_(\d+)(?:_0)?$', args.netlist_module)
    if im:
        idx = int(im.group(1))
        stage_mods = {st: _family_copies(args.ref_dir, short, st).get(idx) for st in STAGES}
    else:
        stage_mods = {st: args.netlist_module for st in STAGES}
    r = build_for_module(args.ref_dir, stage_mods, args.jira, params,
                         compare_signal=args.compare_signal)
    if r['errors']:
        print('BUILD ABORT:', *r['errors'], sep='\n  '); return 2
    print(f"emitted {len(r['gates'])} gates + rename; mismatch net (Synth) = {r['rename']['old_net']}")
    for g in r['gates']:
        print("  ", g['gate_function'], g['instance_name'], '->', g['output_net'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
