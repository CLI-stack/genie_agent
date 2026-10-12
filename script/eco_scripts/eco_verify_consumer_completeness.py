#!/usr/bin/env python3
"""
eco_verify_consumer_completeness.py — deterministic, mechanical verification that a
Method B/C rewire of a shared net is COMPLETE and landed on the right wires.

WHY THIS EXISTS: eco_verify_boolean_fold.py proves a candidate gate chain computes the
exact Boolean function it is supposed to — but it has no idea whether every real
consumer of the net being replaced actually got rewired to the right place. That is a
SEPARATE failure mode, and it has already happened twice for real in this flow:
  1. A shared gate was edited in place without checking its full fan-out; it also fed
     two unrelated sibling registers, which silently kept reading garbage once the
     edit landed (the original "StgBufDepEn" incident Method C exists because of).
  2. Two replacement nets were each independently proven 100% correct by
     eco_verify_boolean_fold.py, and the design still failed real Formality, because
     the REWIRE step connected the wrong one of the two correct nets to the wrong
     16 consumer instances (a true/complement polarity swap). The gate logic was
     never the bug; the wiring assignment was.

This script closes that gap with real code: it loads the REAL baseline netlist (before
this ECO's rewires), enumerates every actual consumer of --old-net in --module, and
confirms each one is explicitly accounted for — either rewired (per the study's own
rewire entries for this net) or deliberately left alone (per --keep) — with nothing
missed and nothing stale. It then loads the already-applied result and confirms the
ACTUAL wiring matches that classification exactly.

Scope and limits (by design, not oversight):
  - This does NOT replace eco_verify_boolean_fold.py. Method B/C entries need both:
    one proves the new logic is correct, this one proves it landed on the right wires.
  - This does NOT judge whether a consumer SHOULD be rewired or kept — that is still
    the agent's Method A/B/C fan-out call. It only guarantees every real consumer was
    explicitly classified one way or the other, and that the netlist reflects it.
  - One net, one module scope, per invocation. Multi-bit/bank completeness (checking
    every bit-group) is a separate, already-documented responsibility and is
    unaffected by this tool.
  - The rewire map is auto-collected from the study JSON's own `rewire` entries for
    --old-net — never hand-retyped by the caller. A caller-typed "here's what I
    rewired" list would just recreate the exact gap this script exists to close (a
    forgotten consumer in the caller's memory). --keep stays caller-supplied because
    "deliberately left alone" is a judgment call the tool cannot infer on its own.

Usage:
    python3 eco_verify_consumer_completeness.py \\
        --study    <AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json \\
        --stage    Synthesize \\
        --old-net  n_eco_102_c6_1_true_newvalue \\
        --module   umccmd_umcstgbufslice_1 \\
        --keep     some_inst:A1,other_inst:B2 \\
        --ref-dir  <REF_DIR> \\
        --result   /tmp/verdict.json

Exit code: 0 = complete (every real consumer accounted for, every classified consumer
wired to exactly the expected net), 1 = incomplete/mismatch (see --result for exact
missing/stale/wrong_net rows), 2 = error (bad args, module/net/file not found).
Callers MUST treat any non-zero exit as "not verified" — never assume 2 means
"probably fine, just a script hiccup."
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eco_lol_impact as L          # parse_modules, parse_instances, build_graph, _norm
import eco_cone_trace as CT         # load(), _read()


def load_stage_entries(study_path, stage):
    with open(study_path) as f:
        study = json.load(f)
    return study.get(stage, [])


def resolve_rewire_fields(entry, stage):
    """Mirror eco_netlist_port_rewire.py's apply_rewire() field resolution exactly,
    so this tool sees the SAME rewire the applier actually acted on."""
    per_stage_cell = (entry.get('per_stage_cell_name') or entry.get('cell_name_per_stage')
                       or entry.get('mux_cell_instance_per_stage') or {})
    cell_name = per_stage_cell.get(stage, '') or entry.get('cell_name', '')
    per_stage_pin = entry.get('per_stage_pin', {}) or entry.get('pin_per_stage', {})
    pin_name = per_stage_pin.get(stage, '') or entry.get('pin', '')
    old_net = (entry.get('per_stage_old_net', {}) or {}).get(stage, '') or entry.get('old_net', '')
    new_net = (entry.get('per_stage_new_net', {}) or {}).get(stage, '') or entry.get('new_net', '')
    mod_name = ((entry.get('module_name_per_stage') or {}).get(stage)
                or entry.get('module_name', ''))
    return cell_name, pin_name, old_net, new_net, mod_name


def _abort(result_path, msg):
    """Same convention as eco_verify_boolean_fold.py's _abort(): persist a marker file
    even on error, so the flow's audit trail shows this script ran."""
    print(f"ERROR: {msg}", file=sys.stderr)
    marker = f"ECO_SCRIPT_LAUNCHED: eco_verify_consumer_completeness.py\n  ABORTED — {msg}\n"
    try:
        open(result_path.replace('.json', '_marker.txt'), 'w').write(marker)
    except OSError:
        pass
    sys.exit(2)


def parse_keep(keep_arg):
    out = set()
    for tok in (keep_arg or '').split(','):
        tok = tok.strip()
        if not tok:
            continue
        if ':' not in tok:
            raise ValueError(f"--keep entry {tok!r} must be inst:pin")
        inst, pin = tok.split(':', 1)
        out.add((inst.strip(), pin.strip()))
    return out


def inst_pin_nets(netlist_path, module):
    """{(inst, pin): [nets]} for direct lookup of what's actually connected right now.
    Same module-scoping/tolerance as eco_cone_trace.load() (reused, not reimplemented)."""
    text = CT._read(netlist_path)
    if module:
        mods = L.parse_modules(text)
        body = mods.get(module)
        if body is None:
            cand = [m for m in mods if m == module or m.endswith('_' + module) or module in m]
            if not cand:
                return None
            body = mods[sorted(cand, key=len)[0]]
        text = body
    insts = L.parse_instances(text)
    out = {}
    for it in insts:
        for pin, nets in it['pins'].items():
            out[(it['inst'], pin)] = nets
    return out


def _find_stage_netlist(ref_dir, kind, stage):
    """kind: 'PreEco' or 'PostEco'. Tolerate an uncompressed sibling if the .gz isn't there."""
    gz = os.path.join(ref_dir, 'data', kind, f'{stage}.v.gz')
    if os.path.isfile(gz):
        return gz
    plain = gz[:-3]
    if os.path.isfile(plain):
        return plain
    return gz  # return the expected path anyway so the caller's error message is useful


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--study', required=True)
    p.add_argument('--stage', default='Synthesize', choices=['Synthesize', 'PrePlace', 'Route'])
    p.add_argument('--old-net', required=True, help='The shared net name as it existed in the PreEco baseline')
    p.add_argument('--module', required=True, help='Module scope to search (tolerant of tile-prefixed/uniquified names)')
    p.add_argument('--keep', default='', help='Comma-separated inst:pin list deliberately left on --old-net (Method C)')
    p.add_argument('--ref-dir', required=True)
    p.add_argument('--result', required=True)
    args = p.parse_args()

    old_net = L._norm(args.old_net)

    try:
        keep_set = parse_keep(args.keep)
    except ValueError as ex:
        _abort(args.result, str(ex))

    preeco = _find_stage_netlist(args.ref_dir, 'PreEco', args.stage)
    posteco = _find_stage_netlist(args.ref_dir, 'PostEco', args.stage)
    if not os.path.isfile(preeco):
        _abort(args.result, f"PreEco baseline not found: {preeco}")
    if not os.path.isfile(posteco):
        _abort(args.result, f"PostEco result not found: {posteco}")

    try:
        _, consumers, _, _, _ = CT.load(preeco, module=args.module)
    except SystemExit as ex:
        _abort(args.result, f"failed to load PreEco baseline (module {args.module!r} not found?): {ex}")

    if old_net not in consumers:
        _abort(args.result, f"old_net {old_net!r} has no consumers at all in module {args.module!r} "
                             f"of {preeco} — check the net name/module scope before trusting any result")

    real_consumers = sorted(set((inst, pin) for (inst, _is_reg, pin) in consumers[old_net]))

    entries = load_stage_entries(args.study, args.stage)
    rewire_map = {}
    for e in entries:
        if e.get('change_type') != 'rewire':
            continue
        cell_name, pin_name, e_old_net, e_new_net, e_mod = resolve_rewire_fields(e, args.stage)
        if not cell_name or not pin_name or not e_new_net:
            continue
        if L._norm(e_old_net) != old_net:
            continue
        if args.module and e_mod and e_mod != args.module:
            continue
        rewire_map[(cell_name, pin_name)] = L._norm(e_new_net)

    # Classify every REAL consumer: rewired (per study), kept (per --keep), or missing.
    classification = {}   # (inst,pin) -> ('rewired'|'kept', expected_net)
    missing = []
    for (inst, pin) in real_consumers:
        if (inst, pin) in rewire_map:
            classification[(inst, pin)] = ('rewired', rewire_map[(inst, pin)])
        elif (inst, pin) in keep_set:
            classification[(inst, pin)] = ('kept', old_net)
        else:
            missing.append({'inst': inst, 'pin': pin})

    # Any rewire the study claims that was never actually a real consumer? (typo'd
    # instance, already-renamed cell, wrong module scope, etc.)
    real_set = set(real_consumers)
    stale = [{'inst': inst, 'pin': pin, 'claimed_new_net': net}
             for (inst, pin), net in rewire_map.items() if (inst, pin) not in real_set]

    # Cross-check the ACTUAL applied netlist against the classification.
    post_pins = inst_pin_nets(posteco, args.module)
    if post_pins is None:
        _abort(args.result, f"module {args.module!r} not found in PostEco netlist {posteco}")

    rewired_ok, kept_ok, wrong_net = [], [], []
    for (inst, pin), (kind, expected) in classification.items():
        actual_nets = post_pins.get((inst, pin))
        record = {'inst': inst, 'pin': pin, 'expected_net': expected, 'actual_nets': actual_nets}
        if actual_nets is None:
            wrong_net.append({**record, 'reason': 'instance/pin not found in PostEco'})
        elif expected not in [L._norm(n) for n in actual_nets]:
            wrong_net.append({**record, 'reason': 'wired to a different net than expected'})
        elif kind == 'rewired':
            rewired_ok.append(record)
        else:
            kept_ok.append(record)

    complete = not (missing or stale or wrong_net)

    verdict = {
        'old_net': old_net,
        'module': args.module,
        'stage': args.stage,
        'consumer_count': len(real_consumers),
        'rewired_ok': rewired_ok,
        'kept_ok': kept_ok,
        'missing': missing,
        'stale': stale,
        'wrong_net': wrong_net,
        'complete': complete,
    }
    with open(args.result, 'w') as f:
        json.dump(verdict, f, indent=2)

    marker = (f"ECO_SCRIPT_LAUNCHED: eco_verify_consumer_completeness.py\n"
              f"  old_net: {old_net}\n"
              f"  module: {args.module}\n"
              f"  consumer_count: {len(real_consumers)}\n"
              f"  rewired_ok: {len(rewired_ok)}  kept_ok: {len(kept_ok)}\n"
              f"  missing: {len(missing)}  stale: {len(stale)}  wrong_net: {len(wrong_net)}\n"
              f"  COMPLETE: {complete}\n"
              f"  result: {args.result}\n")
    print(marker)
    open(args.result.replace('.json', '_marker.txt'), 'w').write(marker)

    sys.exit(0 if complete else 1)


if __name__ == '__main__':
    main()
