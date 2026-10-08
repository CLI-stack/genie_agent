#!/usr/bin/env python3
"""
eco_verify_boolean_fold.py — deterministic, mechanical verification that a candidate
gate chain computes EXACTLY the Boolean function it is supposed to.

WHY THIS EXISTS: and_term / combinational-fold corrections were being "verified" by
an agent manually decoding compound Liberty cell functions and hand-enumerating
truth-table combinations in prose. That is the SAME skill used to build the
(sometimes wrong) gate chain in the first place, so a misread cell function could
survive its own "verification" — the build step and the grading step were not
independent. A real incident: a fold used the wrong Boolean operator (AND-with-
complement where OR was required) on a shared net whose polarity had been
hand-decoded from a compound cell; the mistake was applied identically everywhere
that net fanned out and was never caught before real equivalence checking.

This script replaces that hand-check with real code: it builds the candidate gate
chain's function from the SAME real cell-truth-table data (eco_cell_truth_tables.py)
via mechanical substitution — never by an agent reading Liberty text — evaluates it
over every combination of its free (external) inputs, and compares the result
against an explicit target expression. No Liberty-reading-by-eye, no "I checked all
N combinations" prose: the verdict is a real, reproducible truth-table diff.

Scope and limits (by design, not oversight):
  - This does NOT parse Verilog/RTL. --target-expr is a plain Python boolean
    expression over the SAME free-variable names the candidate chain uses.
    Transcribing an RTL clause into that form is a much lower-risk mechanical step
    than hand-deriving a truth table from a compound standard cell, but it is still
    the caller's responsibility — this script only guarantees the CANDIDATE side is
    built from real, uninterpreted cell data.
  - It does not trace into the pre-existing netlist beyond the listed gates. Any
    net that isn't the output of one of the listed gates is treated as an opaque
    free variable. That is intentional: if the caller's candidate chain reads an
    existing net whose own meaning is in question, that uncertainty belongs in how
    --target-expr is derived (ideally from real external data — find_equivalent_nets,
    a confirmed rename-map entry — not from re-decoding that net's own driving logic
    by eye, which is the exact risk class this script exists to route around).

Usage:
    python3 eco_verify_boolean_fold.py \\
        --study    <AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json \\
        --stage    Synthesize \\
        --gates    eco_102_c6_0_nds,eco_102_c6_0_inner,eco_102_c6_0_wb1,eco_102_c6_0_termA,eco_102_c6_0_nw,eco_102_c6_0_nb1,eco_102_c6_0_term1part,eco_102_c6_0_term1,eco_102_c6_0_result \\
        --output-net n_eco_102_c6_0_newvalue \\
        --target-expr "OldTerm | SeqScrubWrDepScrub" \\
        --ref-dir  <REF_DIR> \\
        --result   /tmp/verdict.json

Each --gates instance must be a new_logic_gate (or new_logic_dff, Q-only) entry in
the study JSON for the given stage, with cell_type + port_connections (or
port_connections_per_stage[stage]) + output_net populated.

Exit code: 0 = match (candidate == target for every combination), 1 = mismatch
(see --result for the exact failing rows), 2 = error (bad gate list, missing cell
function, undefined variable, malformed --target-expr, combinational loop). Callers
MUST treat any non-zero exit as "not verified" — never assume 2 means "probably
fine, just a script hiccup."
"""

import argparse
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eco_cell_truth_tables as ett


def load_stage_entries(study_path, stage):
    with open(study_path) as f:
        study = json.load(f)
    return study.get(stage, [])


def find_entry(entries, instance_name):
    for e in entries:
        if e.get('instance_name') == instance_name:
            return e
    return None


def gate_input_pins(entry, stage):
    """Return {pin: net} for this entry's INPUT pins only (output pin excluded)."""
    pcs = dict(entry.get('port_connections_per_stage', {}).get(stage)
               or entry.get('port_connections', {}) or {})
    out_net = entry.get('output_net')
    return {pin: net for pin, net in pcs.items() if net != out_net and not pin.startswith('_')}


def normalize_cell_expr(expr):
    """Cell truth-table expressions use C-style &/|/~ (e.g. 'A1 & A2', '~A1'), and
    mux/ternary cells use 'COND ? THEN : ELSE'. Convert both to Python boolean
    syntax for eval(). Spacing is defensive against operators with no surrounding
    whitespace in the source expression."""
    if '?' in expr and ':' in expr:
        cond, rest = expr.split('?', 1)
        then_, else_ = rest.split(':', 1)
        expr = f"({then_.strip()}) if ({cond.strip()}) else ({else_.strip()})"
    out = []
    i = 0
    while i < len(expr):
        c = expr[i]
        if c == '&':
            out.append(' and ')
        elif c == '|':
            out.append(' or ')
        elif c == '~':
            out.append(' not ')
        else:
            out.append(c)
        i += 1
    return ''.join(out).strip()


def build_candidate_functions(gates, entries_by_name, stage, free_vars):
    """Returns {net_name: callable(env)->0/1} for every output net reachable from
    the listed gates, built by mechanical substitution over real cell functions.
    Any net not produced by one of `gates` is a free variable pulled from `env`,
    and its name is recorded into `free_vars`."""
    funcs = {}
    net_to_entry = {}
    for name in gates:
        e = entries_by_name[name]
        out_net = e.get('output_net')
        if not out_net:
            raise ValueError(f"gate {name!r} has no output_net in the study entry")
        net_to_entry[out_net] = e

    def resolve(net, visiting):
        if net in funcs:
            return funcs[net]
        if net in visiting:
            raise ValueError(f"combinational loop detected at net {net!r}")
        producer = net_to_entry.get(net)
        if producer is None:
            free_vars.add(net)
            def f(env, _net=net):
                return 1 if env[_net] else 0
            funcs[net] = f
            return f

        visiting = visiting | {net}
        cell_type = producer.get('cell_type', '')
        tt = ett.truth_table_of(cell_type, ref_dir=producer.get('_ref_dir'))
        if not tt:
            raise ValueError(
                f"no truth table found for cell_type {cell_type!r} "
                f"(instance {producer.get('instance_name')!r}) — cannot verify "
                f"mechanically; do NOT fall back to hand-decoding this cell")
        # eco_cell_truth_tables.py uses two incompatible schemas depending on
        # cell complexity: most cells return {output_pin: 'C-style expr'}, but
        # mux/ternary-style cells (e.g. MUX2) return a structured
        # {'pins':{...}, 'function': 'S ? I1 : I0', 'output_pin': 'Z', ...} form
        # instead. Detect and normalize both rather than assuming the simple one
        # everywhere (that mismatch is itself the kind of silent-wrong-answer bug
        # this script exists to prevent).
        if 'function' in tt and 'output_pin' in tt:
            out_pin = tt['output_pin']
            expr_str = tt['function']
            if tt.get('output_is_inverting'):
                expr_str = f"~({expr_str})"
        else:
            out_pin, expr_str = next(iter(tt.items()))
        py_expr = normalize_cell_expr(expr_str)
        try:
            code = compile(py_expr, f'<{producer.get("instance_name")}:{cell_type}>', 'eval')
        except SyntaxError as ex:
            raise ValueError(
                f"cell_type {cell_type!r} truth table {expr_str!r} failed to "
                f"compile after normalization ({py_expr!r}): {ex}")

        pin_to_net = gate_input_pins(producer, stage)
        pin_funcs = {pin: resolve(n, visiting) for pin, n in pin_to_net.items()}

        def f(env, _code=code, _pin_funcs=pin_funcs, _inst=producer.get('instance_name')):
            pin_env = {}
            for pin, fn in _pin_funcs.items():
                pin_env[pin] = bool(fn(env))
            try:
                return 1 if eval(_code, {'__builtins__': {}}, pin_env) else 0
            except NameError as ex:
                raise ValueError(
                    f"cell function for {_inst!r} referenced pin {ex} not present "
                    f"in its port_connections")

        funcs[net] = f
        return f

    for name in gates:
        out_net = entries_by_name[name].get('output_net')
        resolve(out_net, set())
    return funcs


def _abort(result_path, msg):
    """Write both stderr and a persistent marker file for an aborted run, so the
    flow's audit trail (grep ECO_SCRIPT_LAUNCHED / ls *_marker.txt) shows this
    script ran even when it errored out — same convention as the other
    deterministic emitter scripts (e.g. eco_emit_uniquify.py)."""
    print(f"ERROR: {msg}", file=sys.stderr)
    marker = f"ECO_SCRIPT_LAUNCHED: eco_verify_boolean_fold.py\n  ABORTED — {msg}\n"
    try:
        open(result_path.replace('.json', '_marker.txt'), 'w').write(marker)
    except OSError:
        pass
    sys.exit(2)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--study', required=True)
    p.add_argument('--stage', default='Synthesize', choices=['Synthesize', 'PrePlace', 'Route'])
    p.add_argument('--gates', required=True, help='Comma-separated instance_name list (any order; must all be new_logic_gate/new_logic_dff entries in the study)')
    p.add_argument('--output-net', required=True, help='The net whose function is being checked (must be produced by one of --gates)')
    p.add_argument('--target-expr', required=True, help='Python boolean expression over the candidate chain\'s free/external variable names')
    p.add_argument('--ref-dir', required=True, help='Used for the tile-specific Liberty cache, if present')
    p.add_argument('--result', required=True)
    args = p.parse_args()

    entries = load_stage_entries(args.study, args.stage)
    gates = [g.strip() for g in args.gates.split(',') if g.strip()]
    if not gates:
        _abort(args.result, "--gates is empty")

    entries_by_name = {}
    for name in gates:
        e = find_entry(entries, name)
        if e is None:
            _abort(args.result, f"gate instance {name!r} not found in study (stage={args.stage})")
        e = dict(e)
        e['_ref_dir'] = args.ref_dir
        entries_by_name[name] = e

    free_vars = set()
    try:
        funcs = build_candidate_functions(gates, entries_by_name, args.stage, free_vars)
    except ValueError as ex:
        _abort(args.result, str(ex))

    if args.output_net not in funcs:
        _abort(args.result, f"output_net {args.output_net!r} is not produced by any listed gate "
               f"(available: {sorted(funcs.keys())})")
    candidate_fn = funcs[args.output_net]

    free_vars = sorted(free_vars)
    try:
        target_code = compile(args.target_expr, '<target-expr>', 'eval')
    except SyntaxError as ex:
        _abort(args.result, f"--target-expr failed to parse: {ex}")

    mismatches = []
    error = None
    for bits in itertools.product([0, 1], repeat=len(free_vars)):
        env = dict(zip(free_vars, bits))
        try:
            cand = candidate_fn(env)
        except ValueError as ex:
            error = f"candidate evaluation failed: {ex}"
            break
        bool_env = {k: bool(v) for k, v in env.items()}
        try:
            tgt = 1 if eval(target_code, {'__builtins__': {}}, bool_env) else 0
        except NameError as ex:
            error = f"--target-expr referenced a variable not in the candidate's free set: {ex}"
            break
        if cand != tgt:
            mismatches.append({**env, 'candidate': cand, 'target': tgt})

    if error:
        _abort(args.result, error)

    verdict = {
        'match': len(mismatches) == 0,
        'gates': gates,
        'output_net': args.output_net,
        'target_expr': args.target_expr,
        'free_vars': free_vars,
        'total_combinations': 2 ** len(free_vars),
        'mismatch_count': len(mismatches),
        'mismatches': mismatches[:16],
    }
    with open(args.result, 'w') as f:
        json.dump(verdict, f, indent=2)

    marker = (f"ECO_SCRIPT_LAUNCHED: eco_verify_boolean_fold.py\n"
              f"  gates: {gates}\n"
              f"  output_net: {args.output_net}\n"
              f"  free_vars ({len(free_vars)}): {free_vars}\n"
              f"  total_combinations: {verdict['total_combinations']}\n"
              f"  mismatch_count: {verdict['mismatch_count']}\n"
              f"  MATCH: {verdict['match']}\n"
              f"  result: {args.result}\n")
    print(marker)
    open(args.result.replace('.json', '_marker.txt'), 'w').write(marker)

    sys.exit(0 if verdict['match'] else 1)


if __name__ == '__main__':
    main()
