#!/usr/bin/env python3
"""
eco_emit_eq_decode.py — deterministically BUILD the comparator gate chain for an
`and_term`/`wire_swap` whose new term is an equality/enum match `(sig == CONST)`
(Intent-A pattern), so it is correct by construction instead of left as a PENDING
placeholder that Step 2 echo-falls-back and Step 3 substitutes with a wrong net.

Per matching change (schema `equality_decode` emitted by Step 1):
  1. Per bit b of `signal[width-1:0]`: term_b = signal[b] if const bit==1, else
     INV(signal[b]) -> fresh inverted net. (const_binary is MSB..LSB.)
  2. AND-reduce all term nets (AND4/AND3/AND2 tree) -> match net
     n_eco_<jira>_eq_<sig>_<const>.  `match:false` (a `!=`) adds a final INV.
  3. Rewrite the studier's COMBINE gate (identified by the change's
     and_term_gate_chain_design output_net/instance_name): its non-old_token input
     slot is repointed onto the fresh match net (replacing PENDING / wrong net).

Required schema on each such change (rtl_diff_analyzer.md §2c):
  equality_decode: {signal, width, const_binary (e.g. "00011"), match (bool),
                    new_token}.  old_token identifies the combine gate's kept input.

Fail-closed grounding (--ref-dir): every signal[b] bit net must exist in the
change's module in the PreEco Synthesize netlist; otherwise ABORT (exit 2, study
untouched) rather than build a comparator on nets that do not exist.

Usage:
    python3 script/eco_scripts/eco_emit_eq_decode.py \
        --rtl-diff data/<TAG>_eco_rtl_diff.json --study data/<TAG>_eco_preeco_study.json \
        --jira <JIRA> --ref-dir <REF_DIR> --output data/<TAG>_eco_preeco_study.json
Idempotent (skips a change whose match net already exists).
"""
import argparse, gzip, json, os, re, sys

STAGES = ('Synthesize', 'PrePlace', 'Route')
_INV_CELL = 'INVD1BWP136P5M156H3P48CPDLVT'
_AND2_CELL = 'AND2D1BWP136P5M156H3P48CPDLVT'
_AND3_CELL = 'AND3D1BWP136P5M156H3P48CPDLVT'
_AND4_CELL = 'AND4D1BWP136P5M156H3P48CPDLVT'
_OUT_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN')


def _pcstage(pc):
    return {s: dict(pc) for s in STAGES}


def _mod_key(n):
    """Canonical module key: strip the tile prefix (ddrss_*_t_) and any uniquify
    suffix (_<i>), so a change's short name (umcrecdsp) matches the netlist's
    prefixed name (ddrss_umcdat_t_umcrecdsp) and its uniquified copies."""
    return re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', str(n or '')))


def _module_body(gz, module):
    """Return the text of the module(s) matching `module` — tolerant of the tile
    prefix and uniquify suffix (the AI names modules inconsistently short vs full)."""
    if not os.path.isfile(gz):
        return ''
    want = _mod_key(module)
    out, cur = [], False
    pat = re.compile(r'^module\s+(\S+)')
    with gzip.open(gz, 'rt', errors='replace') as f:
        for ln in f:
            m = pat.match(ln)
            if m:
                cur = _mod_key(m.group(1)) == want
            if cur:
                out.append(ln)
                if ln.lstrip().startswith('endmodule'):
                    cur = False
    return ''.join(out)


def _bit_present(body, sig, b):
    """Is signal[b] referenced as a net in the module body (flat or bracket form)?"""
    if not body:
        return False
    pats = (re.escape(f'{sig}[{b}]'), re.escape(f'{sig}_{b}_'))
    return any(re.search(p, body) for p in pats)


def _reduce_and(terms, mk_net, jira, tagbase, mod, gates, seq, cells=None):
    """AND-reduce a list of nets to one, using AND4/AND3/AND2. Appends gates.
    `cells` is the per-module library-resolved cell map (AND2/AND3/AND4); when
    omitted, falls back to the hardcoded defaults (which may not exist in every
    library — pass resolved cells to avoid CELL_TYPE_STAGE_VALID / FE-LINK-2)."""
    cells = cells or {}
    a2 = cells.get('AND2', _AND2_CELL)
    a3 = cells.get('AND3', _AND3_CELL)
    a4 = cells.get('AND4', _AND4_CELL)
    level = 0
    cur = list(terms)
    if len(cur) == 1:
        return cur[0]
    while len(cur) > 1:
        nxt = []
        i = 0
        while i < len(cur):
            grp = cur[i:i + 4] if len(cur) - i != 5 else cur[i:i + 3]  # avoid leaving 1
            if len(grp) == 1:
                nxt.append(grp[0]); i += 1; continue
            cell, fn, pins_in = {
                2: (a2, 'AND2', ('A1', 'A2')),
                3: (a3, 'AND3', ('A1', 'A2', 'A3')),
                4: (a4, 'AND4', ('A1', 'A2', 'A3', 'A4')),
            }[len(grp)]
            out = mk_net(f'{tagbase}_a{level}_{i}')
            pc = {p: g for p, g in zip(pins_in, grp)}
            pc['Z'] = out
            gates.append(_gate(f'eco_{jira}_eq_{tagbase}_a{level}_{i}', cell, fn, out, mod, pc))
            nxt.append(out); i += len(grp)
        cur = nxt; level += 1
    return cur[0]


def _gate(inst, cell, fn, out, mod, pc):
    return {
        'change_type': 'new_logic_gate', 'instance_name': inst, 'cell_type': cell,
        'gate_function': fn, 'output_net': out, 'module_name': mod,
        'port_connections': pc, 'port_connections_per_stage': _pcstage(pc),
        'confirmed': True, 'source': 'eco_emit_eq_decode',
    }


def _find_combine(study, output_net, inst_name):
    """Locate the studier's combine gate across all stages -> list of entries."""
    hits = []
    for st in STAGES:
        for e in study.get(st, []):
            if e.get('change_type') != 'new_logic_gate':
                continue
            if (output_net and e.get('output_net') == output_net) or \
               (inst_name and e.get('instance_name') == inst_name):
                hits.append(e)
    return hits


def _resolver(ref_dir, module, rename_map, scope):
    """Per-stage net resolver for comparator leaves (sig[b]) — mirrors priority_force:
    fenets rename map (authoritative) then the flat-name heuristic (sig[b]->sig_b_).
    Internal n_eco_ nets are absent from both and pass through unchanged. Returns a
    function (net, stage)->name, or None when ref_dir is not available."""
    if not ref_dir:
        return None
    try:
        from eco_emit_priority_force import _stage_net_tokens, _stage_net, _map_stage_net
    except Exception:
        return None
    toks = {st: _stage_net_tokens(ref_dir, module, st) for st in STAGES}

    def _resolve(net, st):
        return (_map_stage_net(net, st, scope, rename_map) or _stage_net(net, toks[st]))
    return _resolve


def emit(rtl_diff, study, jira, ground_body_of, ref_dir=None, rename_map=None):
    seq = [0]
    def nn(tag):
        seq[0] += 1
        return f'n_eco_{jira}_eq_{tag}_{seq[0]}'
    added, errs = 0, []
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') not in ('and_term', 'wire_swap'):
            continue
        # Register guard-change (Intent-A) is owned by eco_cone_rebuild.emit_reg_guard_delta:
        # it rebuilds the whole D-cone region (incl. its own equality comparator) and re-drives
        # .D correct-by-construction. There is NO studier-built combine gate for these, so skip
        # here — otherwise the combine lookup below would (wrongly) error.
        if c.get('target_register') and c.get('branch_assigns') is not None:
            continue
        ed = c.get('equality_decode')
        if not isinstance(ed, dict):
            continue
        sig = ed.get('signal'); width = ed.get('width')
        cb = str(ed.get('const_binary') or '')
        match = ed.get('match', True)
        new_token = ed.get('new_token')
        old_token = c.get('old_token')
        if not (sig and width and re.fullmatch(r'[01]+', cb) and len(cb) == width):
            errs.append(f"change new_token={new_token!r}: bad equality_decode schema "
                        f"(signal/width/const_binary inconsistent).")
            continue
        # locate combine gate to know the module + to rewire its new-term input
        design = (c.get('and_term_gate_chain_design') or [{}])[0]
        combine = _find_combine(study, design.get('output_net'), design.get('instance_name'))
        if not combine:
            errs.append(f"change new_token={new_token!r}: combine gate "
                        f"{design.get('output_net') or design.get('instance_name')!r} not in study "
                        f"(studier did not build the and_term OR/AND gate).")
            continue
        mod = combine[0].get('module_name') or c.get('module_name') or ''
        # Resolve exact library cell names from the module's PreEco netlist (AN2D1 vs
        # AND2D1, etc.) — hardcoded AND2D1/AND3D1 do not exist in every library and
        # would fail step3 CELL_TYPE_STAGE_VALID / FM FE-LINK-2.
        try:
            from eco_emit_priority_force import _resolve_cells
            cells = _resolve_cells(ref_dir, mod)
        except Exception:
            cells = {}
        inv_cell = cells.get('INV', _INV_CELL)
        match_net = f'n_eco_{jira}_eq_{sig}_{cb}'
        # Build the comparator ONCE (idempotent) — but the combine-gate rewrite below
        # still runs for EVERY change that shares this match net (e.g. the same opcode
        # match OR'd into two different branches).
        already = any(e.get('output_net') == match_net for e in study.get('Synthesize', []))
        if not already:
            # fail-closed grounding: each signal bit must exist in the module body
            body = ground_body_of(mod) if ground_body_of else None
            if body is not None:
                missing = [b for b in range(width) if not _bit_present(body, sig, b)]
                if missing:
                    errs.append(f"change new_token={new_token!r}: signal bit(s) {sig}{missing} "
                                f"not found in module {mod!r} PreEco Synthesize netlist.")
                    continue
            # build per-bit terms (const_binary is MSB..LSB; bit b uses cb[width-1-b])
            gates, terms = [], []
            for b in range(width):
                want1 = cb[width - 1 - b] == '1'
                bitnet = f'{sig}[{b}]'
                if want1:
                    terms.append(bitnet)
                else:
                    inv_out = nn(f'{sig}_n{b}')
                    gates.append(_gate(f'eco_{jira}_eq_{sig}_inv{b}_{seq[0]}', inv_cell, 'INV',
                                       inv_out, mod, {'I': bitnet, 'ZN': inv_out}))
                    terms.append(inv_out)
            red = _reduce_and(terms, nn, jira, f'{sig}_{cb}', mod, gates, seq, cells)
            # optional final polarity: != -> invert the equality
            final = red
            if not match:
                final = nn(f'{sig}_{cb}_ne')
                gates.append(_gate(f'eco_{jira}_eq_{sig}_ne_{seq[0]}', inv_cell, 'INV',
                                   final, mod, {'I': red, 'ZN': final}))
            # rename the reduce root to the canonical match_net (stable/idempotent)
            for g in gates:
                if g['output_net'] == final:
                    g['output_net'] = match_net
                    for p in _OUT_PINS:
                        if p in g['port_connections']:
                            g['port_connections'][p] = match_net
                            g['port_connections_per_stage'] = _pcstage(g['port_connections'])
            # PER-STAGE resolution of comparator leaves (sig[b]) — P&R renames the
            # combinational signal's bits (sig[b]->sig_b_ or MB-banked), so echoing the
            # Synthesize name to PP/Route would be NET-ABSENT. Resolve via rename map +
            # flat heuristic; internal n_eco_ nets pass through unchanged.
            scope = c.get('scope') or c.get('instance_scope') or ''
            resolve = _resolver(ref_dir, mod, rename_map, scope)
            if resolve:
                for g in gates:
                    pcs = g.get('port_connections_per_stage') or _pcstage(g['port_connections'])
                    for st in STAGES:
                        if isinstance(pcs.get(st), dict):
                            pcs[st] = {p: resolve(v, st) for p, v in pcs[st].items()}
                    g['port_connections_per_stage'] = pcs
            for st in STAGES:
                study.setdefault(st, []).extend(dict(g) for g in gates)
            added += len(gates) * len(STAGES)
        # rewrite the combine gate's new-term input slot -> match_net (all stages)
        for e in combine:
            pc = e.get('port_connections') or {}
            slot = None
            for p, v in pc.items():
                if p in _OUT_PINS:
                    continue
                if v == old_token:
                    continue
                # prefer the slot referencing new_token / a PENDING of it
                if v == new_token or (isinstance(v, str) and v.startswith('PENDING_FM_RESOLUTION')
                                      and v.split(':', 1)[-1] == new_token):
                    slot = p; break
                slot = slot or p
            if slot:
                pc[slot] = match_net
                e['port_connections'] = pc
                e['port_connections_per_stage'] = _pcstage(pc)
                e['eq_decode_input_fixed'] = True
    return added, errs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--study', required=True)
    ap.add_argument('--jira', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--ref-dir', help='REF_DIR: fail-closed grounding of signal bits '
                    'against PreEco Synthesize netlist; also enables per-stage resolution '
                    'of the comparator leaves (sig[b]->sig_b_ / MB-banked).')
    ap.add_argument('--rename-map', default=None,
                    help='fenets rename map JSON — AUTHORITATIVE per-stage names for the '
                         'comparator signal bits (before the flat-name heuristic).')
    args = ap.parse_args()
    rtl_diff = json.loads(open(args.rtl_diff).read())
    study = json.loads(open(args.study).read())
    rename_map = None
    if args.rename_map and os.path.isfile(args.rename_map):
        try:
            rename_map = json.loads(open(args.rename_map).read())
        except Exception:
            rename_map = None

    ground = None
    if args.ref_dir:
        gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
        _cache = {}
        def ground(mod):
            if mod not in _cache:
                _cache[mod] = _module_body(gz, mod)
            return _cache[mod]

    n, errs = emit(rtl_diff, study, args.jira, ground, ref_dir=args.ref_dir, rename_map=rename_map)
    if errs:
        marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_eq_decode.py\n"
                  f"  ABORTED — {len(errs)} equality_decode change(s) not buildable:\n"
                  + "".join(f"    - {e}\n" for e in errs)
                  + "  Study UNTOUCHED. Fix Step 1 equality_decode schema / signal bits and re-run.\n")
        print(marker)
        open(args.output.replace('.json', '_eq_decode_marker.txt'), 'w').write(marker)
        return 2

    open(args.output, 'w').write(json.dumps(study, indent=2))
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_emit_eq_decode.py\n"
              f"  equality-decode gates spliced (all stages): {n}\n"
              f"  netlist-grounded: {'yes' if args.ref_dir else 'NO (no --ref-dir)'}\n")
    print(marker)
    open(args.output.replace('.json', '_eq_decode_marker.txt'), 'w').write(marker)
    return 0


if __name__ == '__main__':
    sys.exit(main())
