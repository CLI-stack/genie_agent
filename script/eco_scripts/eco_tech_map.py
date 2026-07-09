#!/usr/bin/env python3
"""eco_tech_map.py — depth-reducing compound-cell mapping for emitted ECO logic.

The ECO logic emitter (eco_rtl_synth) builds combinational cones from PRIMITIVE cells only
(AND2/OR2/INV), which yields deeper logic than real synthesis. Real synthesis packs two boolean
levels into one COMPOUND cell (AOI21 = ~((A1&A2)|B), OAI21, AOI22, OA21, AO21, AOI211, INR2 ...).
This post-pass folds primitive clusters into the library's compound cells, cutting logic depth
(Levels-of-Logic) toward human/synthesis QoR — WITHOUT changing function.

FAIL-CLOSED by construction:
  * a compound family is used only if an instance of it exists in the module's netlist (never
    invent a cell name the library may not have);
  * every individual fold is verified by exhaustive/vector simulation (compound function ==
    the primitive cluster it replaces);
  * the whole mapped network is re-verified against the original at every primary output.
On ANY mismatch or unresolved cell the pass returns the ORIGINAL primitive gates unchanged, so it
can only ever make already-correct logic shallower — never emit wrong logic.

Entry point:  tech_map_gates(gates, ref_dir, module, jira='eco', protected_nets=None)
                  -> list[gate_dict]
  `protected_nets`: nets referenced OUTSIDE `gates` (rewire old/new nets, exposed signals).
  They are never folded away. Callers (any emitter) MUST pass every net their rewires /
  study entries reference so tech mapping can be adopted safely across emitters.

Library/JIRA-agnostic: cell names are discovered from the module's own netlist and validated
against the truth-table library; nothing is tied to a foundry, tile, or a specific JIRA.
"""
import re
import itertools
import eco_cell_truth_tables as ett
import eco_netlist_sim as ns

# families we target -> their output pin (pins/functions from ett.ABSTRACT_GATE_FUNCTIONS).
_FAMILIES = {
    # compound (two boolean levels -> one cell)
    'AOI21': 'ZN', 'AOI22': 'ZN', 'OAI21': 'ZN', 'OAI22': 'ZN',
    'AO21': 'Z', 'AO22': 'Z', 'OA21': 'Z', 'OA22': 'Z',
    'AOI211': 'ZN', 'INR2': 'ZN',
    # wide same-op (collapses a balanced OR2/AND2 reduction tree -> log_k depth)
    'OR3': 'Z', 'OR4': 'Z', 'AND3': 'Z', 'AND4': 'Z',
    'NOR2': 'ZN', 'NOR3': 'ZN', 'NOR4': 'ZN', 'NAND2': 'ZN', 'NAND3': 'ZN', 'NAND4': 'ZN',
}
# Keys above are ABSTRACT_GATE_FUNCTIONS-canonical (so pin maps + sim fallback work). This maps
# each to the library cell-name short-forms to grep for (AN=AND, NR=NOR, ND=NAND, etc.).
_FAM_PATS = {
    'AND3': ['AN3', 'AND3'], 'AND4': ['AN4', 'AND4'],
    'NOR2': ['NR2', 'NOR2'], 'NOR3': ['NR3', 'NOR3'], 'NOR4': ['NR4', 'NOR4'],
    'NAND2': ['ND2', 'NAND2'], 'NAND3': ['ND3', 'NAND3'], 'NAND4': ['ND4', 'NAND4'],
}
_OUTP = ('ZN', 'Z', 'ZN1', 'QN', 'Q', 'CO', 'CON', 'S', 'SN')


# ── cell-name resolution (per library, netlist-scanned; fail-closed) ─────────────
def _resolve_compound_cells(ref_dir, module):
    """{family: exact_cell_name} for the compound families that ACTUALLY appear in the module's
    netlist. A family with no instance is omitted (never invent a name for a possibly-absent cell)."""
    out = {}
    if not ref_dir:
        return out
    try:
        from eco_emit_priority_force import _module_netlist_body
        body = _module_netlist_body(ref_dir, module) or ''
    except Exception:
        body = ''
    if not body:
        return out
    for fam in _FAMILIES:
        prefixes = _FAM_PATS.get(fam, [fam])       # canonical fam -> library short-form(s)
        cands = []
        for pre in prefixes:
            # Library-agnostic: match a standard-cell token '<prefix>D<drive><suffix>' (no
            # foundry name hardcoded) and KEEP it only if (a) its family is exactly `pre` and
            # (b) the truth-table library actually defines it — so we never fold to a cell whose
            # function we cannot verify.
            for c in set(re.findall(r'\b(' + pre + r'D\d\w*)\b', body)):
                if ett.family_of(c) == pre and ett.truth_table_of(c, ref_dir=ref_dir):
                    cands.append(c)
        if cands:
            out[fam] = sorted(cands, key=lambda c: ('SPG' in c, c.endswith('LL'), len(c)))[0]
    return out


# ── gate-graph helpers ───────────────────────────────────────────────────────────
def _out_pin(g):
    pc = g.get('port_connections') or {}
    for p in ('Z', 'ZN', 'ZN1', 'Z1'):
        if p in pc:
            return p
    return 'Z'


def _in_nets(g):
    pc = g.get('port_connections') or {}
    op = _out_pin(g)
    return [n for p, n in pc.items() if p != op]


def _index(gates):
    """by_out[net]=gate (net driven by an ECO gate); cons[net]=[gates consuming net as input]."""
    by_out, cons = {}, {}
    for g in gates:
        o = g.get('output_net')
        if o:
            by_out[o] = g
    for g in gates:
        for n in _in_nets(g):
            cons.setdefault(n, []).append(g)
    return by_out, cons


def _absorb(net, fn, by_out, cons, protected):
    """Return the gate driving `net` iff it is an ECO `fn` gate consumed ONLY here (fanout 1)
    AND `net` is not `protected` (referenced outside `gates`, e.g. by a rewire) — folding it
    away would delete a net the caller still references."""
    if net in protected:
        return None
    g = by_out.get(net)
    if g and g.get('gate_function') == fn and len(cons.get(net, [])) == 1:
        return g
    return None


def _flatten_same(root, by_out, cons, protected, limit=4):
    """Flat operand list of a same-op OR2/AND2 tree rooted at `root`, absorbing fanout-1 same-op
    children, capped at `limit` operands. Returns (operands, absorbed_gates including root)."""
    fn = root.get('gate_function')
    operands, absorbed = [], [root]
    queue = list(_in_nets(root))
    while queue:
        n = queue.pop(0)
        g = _absorb(n, fn, by_out, cons, protected)
        if g and (len(operands) + len(queue) + 2) <= limit:   # absorbing net n -> its 2 inputs
            absorbed.append(g)
            queue.extend(_in_nets(g))
        else:
            operands.append(n)
    return operands, absorbed


def _mk(cells, family, pinmap, out_net, module):
    pinmap = dict(pinmap)
    pinmap[_FAMILIES[family]] = out_net
    return {'change_type': 'new_logic_gate', 'cell_type': cells[family], 'gate_function': family,
            'output_net': out_net, 'module_name': module, 'port_connections': pinmap,
            'confirmed': True, 'source': 'eco_tech_map', 'reason': f'tech-map fold -> {family}'}


# ── equivalence checking (fail-closed guard) ───────────────────────────────────────
def _insts_drivers(gates, ref_dir):
    """Build eco_netlist_sim (insts, drivers) from a gate-dict list (mirrors
    eco_functional_precheck._study_insts_drivers). Uses the library truth table, falling back to
    the abstract gate function so both primitive and compound cells simulate identically."""
    insts, drivers = {}, {}
    for g in gates:
        cell = g.get('cell_type')
        inst = g.get('instance_name') or g.get('output_net')
        pins = {p: str(n) for p, n in (g.get('port_connections') or {}).items()}
        tt = ns._normalize_tt(ett.truth_table_of(cell, ref_dir=ref_dir)) if cell else None
        if not tt:
            fn = g.get('gate_function')
            tt = ns._normalize_tt(ett.ABSTRACT_GATE_FUNCTIONS.get(fn)) if fn else None
        if not tt:
            continue
        tt_pin = next(iter(tt))
        if tt_pin not in pins:                       # re-key tt onto the instance's real out pin
            iop = next((p for p in _OUTP if p in pins), None)
            if iop:
                expr = tt[tt_pin]
                if {iop, tt_pin} in ({'Z', 'ZN'}, {'Q', 'QN'}):
                    expr = '(~(%s))' % expr
                tt = {iop: expr}
        insts[inst] = {'cell': cell, 'pins': pins, 'tt': tt}
        for op in tt:
            n = pins.get(op)
            if n:
                drivers[n] = (inst, op, False)
    return insts, drivers


def _equiv_at(gates_a, gates_b, out_net, ref_dir, n=4000):
    """True iff `out_net` computes the same boolean function in gate lists A and B."""
    ia, da = _insts_drivers(gates_a, ref_dir)
    ib, db = _insts_drivers(gates_b, ref_dir)
    oa, la = ns.cone_of(out_net, ia, da)
    ob, lb = ns.cone_of(out_net, ib, db)
    leaves = sorted(l for l in (set(la) | set(lb)) if not ns._is_const(l))
    if len(leaves) <= 16:
        combos = itertools.product((0, 1), repeat=len(leaves))
    else:
        import random
        rng = random.Random(1)
        combos = (tuple(rng.randint(0, 1) for _ in leaves) for _ in range(n))
    for vec in combos:
        env = dict(zip(leaves, vec))
        if ns.simulate(oa, ia, env).get(out_net, 0) != ns.simulate(ob, ib, env).get(out_net, 0):
            return False
    return True


def _primary_outputs(gates):
    by_out, cons = _index(gates)
    return [o for o in by_out if len(cons.get(o, [])) == 0]


# ── fold matching ──────────────────────────────────────────────────────────────────
def _match_fold(g, by_out, cons, cells, protected):
    """If root gate `g` (AND2/OR2) can absorb neighbour(s) into an available compound cell,
    return (new_gate, subset, out_net); else None. subset = gates to remove (root + absorbed)."""
    fn = g.get('gate_function')
    if fn not in ('AND2', 'OR2'):
        return None
    o = g['output_net']
    ins = _in_nets(g)
    if len(ins) != 2:
        return None
    x, y = ins
    # inverting variant: root's SOLE consumer is an INV -> fold it in, drive the INV's output.
    # Only allowed when `o` is NOT protected: the inverting fold DELETES `o` (root's net), so a
    # protected/exposed `o` must keep its own driver (use a non-inverting fold instead).
    c_o = cons.get(o, [])
    inv_g = (c_o[0] if (o not in protected and len(c_o) == 1
                        and c_o[0].get('gate_function') == 'INV') else None)
    inv_out = inv_g['output_net'] if inv_g else None

    def have(fam):
        return fam in cells

    if fn == 'AND2':
        # INR2: a & ~b  (absorb an INV feeding an input). Non-inverting overall (out = o).
        for a_net, b_net in ((x, y), (y, x)):
            ig = _absorb(b_net, 'INV', by_out, cons, protected)
            if ig and have('INR2'):
                bsrc = _in_nets(ig)[0]
                gate = _mk(cells, 'INR2', {'A1': a_net, 'B1': bsrc}, o, g['module_name'])
                return gate, [g, ig], o
        # OA22 / OAI22: (p|q)&(r|s)
        or0, or1 = _absorb(x, 'OR2', by_out, cons, protected), _absorb(y, 'OR2', by_out, cons, protected)
        if or0 and or1:
            fam = 'OAI22' if inv_g else 'OA22'
            if have(fam):
                p, q = _in_nets(or0); r, s = _in_nets(or1)
                out = inv_out if inv_g else o
                gate = _mk(cells, fam, {'A1': p, 'A2': q, 'B1': r, 'B2': s}, out, g['module_name'])
                sub = [g, or0, or1] + ([inv_g] if inv_g else [])
                return gate, sub, out
        # OA21 / OAI21: (p|q)&c
        for or_net, c_net in ((x, y), (y, x)):
            org = _absorb(or_net, 'OR2', by_out, cons, protected)
            if org:
                fam = 'OAI21' if inv_g else 'OA21'
                if have(fam):
                    p, q = _in_nets(org)
                    out = inv_out if inv_g else o
                    gate = _mk(cells, fam, {'A1': p, 'A2': q, 'B': c_net}, out, g['module_name'])
                    sub = [g, org] + ([inv_g] if inv_g else [])
                    return gate, sub, out

    if fn == 'OR2':
        # AO22 / AOI22: (p&q)|(r&s)
        an0, an1 = _absorb(x, 'AND2', by_out, cons, protected), _absorb(y, 'AND2', by_out, cons, protected)
        if an0 and an1:
            fam = 'AOI22' if inv_g else 'AO22'
            if have(fam):
                p, q = _in_nets(an0); r, s = _in_nets(an1)
                out = inv_out if inv_g else o
                gate = _mk(cells, fam, {'A1': p, 'A2': q, 'B1': r, 'B2': s}, out, g['module_name'])
                sub = [g, an0, an1] + ([inv_g] if inv_g else [])
                return gate, sub, out
        # AOI211 (inverting only): ~((p&q)|r|s)
        if inv_g and have('AOI211'):
            for and_net, or_net in ((x, y), (y, x)):
                ang = _absorb(and_net, 'AND2', by_out, cons, protected)
                org = _absorb(or_net, 'OR2', by_out, cons, protected)
                if ang and org:
                    p, q = _in_nets(ang); r, s = _in_nets(org)
                    gate = _mk(cells, 'AOI211', {'A1': p, 'A2': q, 'B': r, 'C': s},
                               inv_out, g['module_name'])
                    return gate, [g, ang, org, inv_g], inv_out
        # AO21 / AOI21: (p&q)|c
        for and_net, c_net in ((x, y), (y, x)):
            ang = _absorb(and_net, 'AND2', by_out, cons, protected)
            if ang:
                fam = 'AOI21' if inv_g else 'AO21'
                if have(fam):
                    p, q = _in_nets(ang)
                    out = inv_out if inv_g else o
                    gate = _mk(cells, fam, {'A1': p, 'A2': q, 'B': c_net}, out, g['module_name'])
                    sub = [g, ang] + ([inv_g] if inv_g else [])
                    return gate, sub, out

    # ---- wide same-op fold (collapse an OR2/AND2 reduction tree; tried AFTER compounds) ----
    operands, absorbed = _flatten_same(g, by_out, cons, protected, limit=4)
    n = len(operands)
    if fn == 'OR2':
        wide = {3: 'OR3', 4: 'OR4'}; invf = {2: 'NOR2', 3: 'NOR3', 4: 'NOR4'}
    else:
        wide = {3: 'AND3', 4: 'AND4'}; invf = {2: 'NAND2', 3: 'NAND3', 4: 'NAND4'}
    if inv_g and invf.get(n) in cells:                 # OR2/AND2 tree feeding INV -> NOR/NAND
        fam = invf[n]
        pins = {f'A{i+1}': operands[i] for i in range(n)}
        return _mk(cells, fam, pins, inv_out, g['module_name']), absorbed + [inv_g], inv_out
    if n >= 3 and wide.get(n) in cells:                # 3/4-input OR/AND
        fam = wide[n]
        pins = {f'A{i+1}': operands[i] for i in range(n)}
        return _mk(cells, fam, pins, o, g['module_name']), absorbed, o
    return None


# ── entry point ────────────────────────────────────────────────────────────────────
def tech_map_gates(gates, ref_dir, module, jira='eco', protected_nets=None):
    """Fold primitive AND2/OR2/INV clusters in `gates` into library compound cells to reduce
    logic depth. `protected_nets` = nets referenced outside `gates` (rewire/exposed) that must
    never be folded away. Fail-closed: returns the ORIGINAL `gates` unchanged if no compound
    cells are available or if the mapped network is not provably equivalent."""
    if not gates:
        return gates
    cells = _resolve_compound_cells(ref_dir, module)
    if not cells:
        return gates
    protected = set(protected_nets or ())
    orig = [dict(g) for g in gates]
    work = [dict(g) for g in gates]

    changed, guard = True, 0
    while changed and guard < 100000:
        changed = False
        guard += 1
        by_out, cons = _index(work)
        for g in list(work):
            m = _match_fold(g, by_out, cons, cells, protected)   # protected nets never absorbed
            if not m:
                continue
            new_gate, subset, out_net = m
            # per-fold soundness check (cheap: small cluster) — skip an unsound fold
            if not _equiv_at(subset, [new_gate], out_net, ref_dir):
                continue
            rem = {id(x) for x in subset}
            work = [x for x in work if id(x) not in rem]
            work.append(new_gate)
            changed = True
            break   # re-index after each applied fold

    # whole-network hard guard: every exposed net (true primary outputs + any protected net that
    # is a gate output) must compute the same function after mapping.
    byo_orig, _ = _index(orig)
    check = set(_primary_outputs(orig)) | {p for p in protected if p in byo_orig}
    for o in check:
        if not _equiv_at(orig, work, o, ref_dir):
            return gates   # FAIL-CLOSED — revert to primitives
    return work
