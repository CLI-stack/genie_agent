#!/usr/bin/env python3
"""eco_emit_compare_fold.py — deterministic emitter for a COMPARE-OPERAND OR-fold: an
RTL change of the form `term | R` inserted into one operand of an equality compare that
feeds a registered signal (optionally replicated across a uniquified generate array).

This is NOT a plain OR-widen at the flop D-net (`D | R`). The fold lives INSIDE an
equality comparison operand, so in the netlist it becomes a localized force on the
SHARED mismatch net of the compared term-pair — a distinct pattern the generic
`and_term` OR-widen handler models incorrectly.

  RTL:  {.. , op | (|Rbus)} == {.. , msc_bit | (|Rbus)}   (in SOME branches only)

The synthesized netlist represents the compared term-pair by a single MISMATCH net
  M = opA ^ opB
shared across ALL branches of the register's next-state. The fold must apply ONLY in
the branches that fold in RTL, so a naive global `M & ~R` corrupts the sibling
(unfolded) branches on reachable states -> FM fail. Correct fold:

  M_new = M & ~( R & S )

where S is the LOCALIZATION LITERAL: a single bit of the branch-select field that is 1
for every folded-branch opcode and 0 for every unfolded (sibling) opcode. This is the
minimal condition separating the folded branch from its siblings and is derivable from
the opcode sets (fail-closed if no single separating bit exists).

Emission (surgical net-force, preserves the net name so all fanout follows):
  1. rename M's driver output  M -> n_eco_<jira>_c_m  (the raw opA^opB)
  2. OR-reduce Rbus            -> n_eco_<jira>_c_r
  3. AND2(r, S)               -> n_eco_<jira>_c_rs
  4. INR2(m, rs)  (ZN=A1&~B1) -> M           (= M & ~(R & S))

Self-verifies EXHAUSTIVELY over the local inputs (opA,opB,Rbus bits,S) that the emitted
function equals the intended oracle; fail-closed on mismatch.

This mirrors the human ECO structurally (their OAOI211 collapses steps 2-4) and is
functionally identical.
"""
import argparse, itertools, json, re, sys
import eco_netlist_sim as ns

STAGES = ('Synthesize', 'PrePlace', 'Route')
# resolved per-module below; safe TSMC defaults
_DEF = {'OR2': 'OR2D1BWP136P5M156H3P48CPDLVT',
        'AND2': 'AND2D1BWP136P5M156H3P48CPDLVT',
        'INR2': 'INR2D1BWP136P5M156H3P48CPDLVT'}  # ZN = A1 & ~B1
_CLK = ('CP', 'CPN', 'CK', 'CKB', 'CLK')


def find_mismatch_net(insts, drivers, opA, opB):
    """Find the net whose local function == opA ^ opB over leaves {opA, opB}.
    Returns (net, driver_inst, driver_pin, polarity) where polarity 'XOR' or 'XNOR'."""
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


def fanout_sinks(body, net):
    """All (inst, is_seq) that read `net` as an input pin."""
    inst_re = re.compile(r'([A-Za-z][\w]*)\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*;', re.S)
    pin_re = re.compile(r'\.\s*([A-Za-z][\w]*)\s*\(\s*([^)]*?)\s*\)')
    _OUT = ('ZN', 'Z', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN', 'Q1', 'Q2', 'Q3')
    adj = {}          # net -> list of (inst, pins, isseq, outs)
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
    contains want_flop_substr? Returns (ok, other_flops)."""
    adj = fanout_sinks(body, net)
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
    """Find a single field bit that is constant 1 across folded_ops and constant 0
    across unfolded_ops (or the inverse). Opcode strings are MSB..LSB over the field.
    Returns (net_name, polarity) where net = field_signal[abs_bit]; polarity True means
    S = bit, False means S = ~bit. Fail-closed -> None."""
    lo, hi = min(field_range), max(field_range)
    width = hi - lo + 1
    folded = [s for s in folded_ops]
    unfolded = [s for s in unfolded_ops]
    if any(len(s) != width for s in folded + unfolded):
        return None
    for i in range(width):                       # i is LSB..? index from the right
        fbits = {s[width - 1 - i] for s in folded}
        ubits = {s[width - 1 - i] for s in unfolded}
        if fbits == {'1'} and ubits == {'0'}:
            return (f'{field_signal}[{lo + i}]', True)
        if fbits == {'0'} and ubits == {'1'}:
            return (f'{field_signal}[{lo + i}]', False)
    return None


def _selfcheck(gates, m_raw, M, Rbits, r_net, s_net, s_pol):
    """EXHAUSTIVELY verify the emitted gate chain computes M = (opA^opB) & ~(R & S)
    over all local inputs (m_raw, each R bit, s_net). Simulates the emitted gates via
    eco_netlist_sim; hard-gate against silent-wrong. Returns (ok, ndiff)."""
    diff = 0
    nbits = len(Rbits)
    for combo in itertools.product((0, 1), repeat=1 + nbits + 1):
        env = {m_raw: combo[0], s_net: combo[-1]}
        for i, rb in enumerate(Rbits):
            env[rb] = combo[1 + i]
        val = dict(env)
        for g in gates:                       # gates are in emission (topo) order
            pc = g['port_connections']; fn = g['gate_function']
            def gv(n):
                return val.get(n, 0)
            if fn == 'OR2':
                o = gv(pc['A1']) | gv(pc['A2']); val[pc['Z']] = o
            elif fn == 'AND2':
                o = gv(pc['A1']) & gv(pc['A2']); val[pc['Z']] = o
            elif fn == 'INR2':
                o = gv(pc['A1']) & (1 - gv(pc['B1'])); val[pc['ZN']] = o
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


def _gate(inst, cell, fn, pc, out_net):
    pcs = {s: dict(pc) for s in STAGES}
    return {'change_type': 'new_logic_gate', 'instance_name': inst, 'cell_type': cell,
            'gate_function': fn, 'output_net': out_net, 'port_connections': pc,
            'port_connections_per_stage': pcs, 'confirmed': True,
            'source': 'eco_emit_compare_fold'}


def build_fold(ref_dir, netlist_module, jira, params, cells=None):
    """Return {'gates':[...], 'rename':{...}, 'net':M, 'errors':[...], 'oracle_ok':bool}."""
    cells = cells or _DEF
    opA = params['operand_a']; opB = params['operand_b']
    Rbits = params['fold_bits']                       # list of net names, reduction-OR'd
    errs = []
    gz = f"{ref_dir}/data/PreEco/Synthesize.v.gz"
    insts, drivers = ns.parse_module(gz, netlist_module, ref_dir=ref_dir)
    if not insts:
        return {'errors': [f'module {netlist_module} not found'], 'gates': [], 'rename': {}}
    body = ns._module_body(gz, netlist_module)
    hits = find_mismatch_net(insts, drivers, opA, opB)
    xor_hits = [h for h in hits if h[3] == 'XOR']
    if not xor_hits:
        return {'errors': [f'no net computes {opA}^{opB} in {netlist_module}'],
                'gates': [], 'rename': {}}
    M, drv_inst, drv_pin, _ = xor_hits[0]
    ok, other = reaches_only(body, M, params['compare_signal'])
    if not ok:
        errs.append(f'{M} also feeds non-{params["compare_signal"]} flops {sorted(other)} '
                    f'- net-split required (not implemented)')
    S = separating_literal(params['field_signal'], params['field_range'],
                           params['folded_ops'], params['unfolded_ops'])
    if S is None:
        errs.append('no single separating literal for folded/unfolded opcode sets')
    if errs:
        return {'errors': errs, 'gates': [], 'rename': {}, 'net': M}
    s_net, s_pol = S
    # fresh nets
    m_raw = f'n_eco_{jira}_c_m'; r_net = f'n_eco_{jira}_c_r'
    rs_net = f'n_eco_{jira}_c_rs'; s_use = s_net
    gates = []
    # rename driver output M -> m_raw
    rename = {'instance_name': drv_inst, 'pin': drv_pin, 'old_net': M, 'new_net': m_raw,
              'change_type': 'rewire', 'source': 'eco_emit_compare_fold'}
    # OR-reduce Rbits -> r_net (tree of OR2)
    cur = list(Rbits)
    level = 0
    while len(cur) > 1:
        nxt = []
        for i in range(0, len(cur), 2):
            grp = cur[i:i + 2]
            if len(grp) == 1:
                nxt.append(grp[0]); continue
            o = r_net if (len(cur) <= 2) else f'{r_net}_{level}_{i}'
            gates.append(_gate(f'eco_{jira}_c_or_{level}_{i}', cells['OR2'], 'OR2',
                               {'A1': grp[0], 'A2': grp[1], 'Z': o}, o))
            nxt.append(o)
        cur = nxt; level += 1
    if len(Rbits) == 1:
        r_net = Rbits[0]                              # single-bit fold: R is the bit itself
    # optional inversion of S if polarity False (need S true-sense). Use INR2 later handles ~.
    # AND2(r_net, S) -> rs_net   (if s_pol False, we need r & ~S => use INR2(r,S)->rs)
    if s_pol:
        gates.append(_gate(f'eco_{jira}_c_and', cells['AND2'], 'AND2',
                           {'A1': r_net, 'A2': s_use, 'Z': rs_net}, rs_net))
    else:
        gates.append(_gate(f'eco_{jira}_c_inr_s', cells['INR2'], 'INR2',
                           {'A1': r_net, 'B1': s_use, 'ZN': rs_net}, rs_net))
    # INR2(m_raw, rs_net) -> M    (ZN = A1 & ~B1 = M_raw & ~(R&S))
    gates.append(_gate(f'eco_{jira}_c_fold', cells['INR2'], 'INR2',
                       {'A1': m_raw, 'B1': rs_net, 'ZN': M}, M))
    # HARD GATE: exhaustively verify emitted chain == oracle (opA^opB) & ~(R & S).
    ok, ndiff = _selfcheck(gates, m_raw, M, Rbits, r_net, s_net, s_pol)
    if not ok:
        return {'errors': [f'self-check FAILED ({ndiff} local mismatches) - emitted fold '
                           f'does not equal (opA^opB) & ~(R & S); refusing to emit'],
                'gates': [], 'rename': {}, 'net': M}
    return {'gates': gates, 'rename': rename, 'net': M, 's_net': s_net, 's_pol': s_pol,
            'r_net': r_net, 'm_raw': m_raw, 'errors': [], 'selfcheck_ok': True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--netlist-module', required=True)
    ap.add_argument('--jira', default='chk')
    ap.add_argument('--params', required=True, help='JSON file with compare-fold params')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    params = json.loads(open(args.params).read())
    r = build_fold(args.ref_dir, args.netlist_module, args.jira, params)
    if r['errors']:
        print('ABORT:', *r['errors'], sep='\n  '); return 2
    print(f"mismatch net: {r['net']}   separating literal: {r['s_net']} (pol={r['s_pol']})")
    print(f"emitted {len(r['gates'])} gates + 1 rename")
    for g in r['gates']:
        print("  ", g['gate_function'], g['instance_name'], '->', g['output_net'], g['port_connections'])
    print("  RENAME", r['rename'])
    if args.out:
        json.dump(r, open(args.out, 'w'), indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
