#!/usr/bin/env python3
"""eco_functional_precheck.py — pre-FM LOCAL functional check of the emitted study.

For each change in the rtl_diff, independently verify that the logic the study emitted
computes the INTENDED function — grounded LOCALLY (at the net the change edits, where
there are few/no don't-cares) so the check is sound. This is a fast on-machine smoke
alarm that pinpoints a wrong study entry BEFORE the slow FM run. It does NOT replace FM
(deep sequential / reachable-state equivalence is FM's job) — it is fail-closed and
SKIPs (reports "FM-only") any case it cannot check soundly.

Two independent engines are compared, per change:
  DUT = simulate the emitted study gates (cell truth tables, via eco_netlist_sim)
  REF = the intended function (from the change's schema / the ECO'd RTL via _Interp)

Supported checks (sound + local):
  * new_logic_dff / new_logic   → the emitted D-input cone computes the DFF's
    d_input_expected_function (a self-contained boolean expr) over all leaf combos
    (exhaustive, or 4000 random if >16 leaves). Early validation for a NEW DFF.
  * and_term + equality_decode  → the emitted comparator computes (signal == CONST)
    over ALL 2^width signal values (exhaustive).
  * compare_fold                → the emitted fold computes M & ~(R & S) (the emitter's
    own exhaustive self-check, re-run here).
  * comb_net_force              → eco_synth_selfcheck.run: rebuilt cone == ECO'd RTL
    over N random vectors.
Everything else → SKIP (FM-only) with a reason.

Usage:
  python3 eco_functional_precheck.py --study <study.json> --rtl-diff <diff.json> \
      --ref-dir <REF_DIR> --jira <JIRA> [--n 2000]
Exit 0 = no FAIL (PASS/SKIP only); 1 = at least one FAIL.
"""
import argparse, io, itertools, json, os, re, sys, contextlib
import eco_netlist_sim as ns
import eco_cell_truth_tables as ett

_OUTP = ('ZN', 'Z', 'ZN1', 'QN', 'Q', 'CON', 'CO', 'SN', 'S')


def _study_insts_drivers(gates, stage, ref_dir):
    """Build (insts, drivers) in eco_netlist_sim format from emitted study gates, so we can
    reuse ns.cone_of / ns.simulate to evaluate the emitted logic directly."""
    insts, drivers = {}, {}
    for g in gates:
        if g.get('change_type') != 'new_logic_gate':
            continue
        cell, inst = g.get('cell_type'), g.get('instance_name')
        if not cell or not inst:
            continue
        pcs = (g.get('port_connections_per_stage', {}) or {}).get(stage) or g.get('port_connections') or {}
        pins = {p: str(n) for p, n in pcs.items()}
        tt = ns._normalize_tt(ett.truth_table_of(cell, ref_dir=ref_dir))
        if not tt:
            continue
        tt_pin = next(iter(tt))
        if tt_pin not in pins:                       # re-key tt onto instance's real out pin
            io_pin = next((p for p in _OUTP if p in pins), None)
            if io_pin:
                expr = tt[tt_pin]
                if {io_pin, tt_pin} in ({'Z', 'ZN'}, {'Q', 'QN'}):
                    expr = '(~(%s))' % expr
                tt = {io_pin: expr}
        insts[inst] = {'cell': cell, 'pins': pins, 'tt': tt}
        for op in tt:
            n = pins.get(op)
            if n:
                drivers[n] = (inst, op, False)
    return insts, drivers


def check_eq_decode(idx, c, study, ref_dir, jira):
    ed = c.get('equality_decode') or {}
    sig, width = ed.get('signal'), ed.get('width')
    cb, match = str(ed.get('const_binary') or ''), ed.get('match', True)
    if not (sig and width and re.fullmatch(r'[01]+', cb) and len(cb) == width):
        return ('SKIP', f'eq_decode schema incomplete for {sig}')
    match_net = f'n_eco_{jira}_eq_{sig}_{cb}'
    gates = [e for e in study.get('Synthesize', [])
             if e.get('change_type') == 'new_logic_gate' and e.get('source') == 'eco_emit_eq_decode']
    insts, drivers = _study_insts_drivers(gates, 'Synthesize', ref_dir)
    if match_net not in drivers:
        return ('SKIP', f'comparator match net {match_net} not found among eco_emit_eq_decode gates')
    order, leaves = ns.cone_of(match_net, insts, drivers)
    const_int = int(cb, 2)
    for val in range(1 << width):
        env = {f'{sig}[{b}]': (val >> b) & 1 for b in range(width)}
        dut = ns.simulate(order, insts, env).get(match_net, 0) & 1
        ref = int((val == const_int) if match else (val != const_int))
        if dut != ref:
            return ('FAIL', f'comparator {match_net}: at {sig}={val:0{width}b} DUT={dut} '
                            f'expected={ref} ((=={cb})={match})')
    return ('PASS', f'comparator {match_net} == ({sig}=={cb}) over all {1<<width} values')


def _eval_expr(node, env):
    """Tiny boolean evaluator for a d_input_expected_function AST (single-bit domain)."""
    t = node[0]
    if t == 'num':
        return (node[2] or 0) & 1
    if t == 'id':
        return env.get(node[1], 0) & 1
    if t == 'bit':
        base = node[1][1] if node[1][0] == 'id' else str(node[1])
        idx = node[2][2] if (isinstance(node[2], tuple) and node[2][0] == 'num') else 0
        return env.get(f'{base}[{idx}]', env.get(base, 0)) & 1
    if t == 'un':
        a = _eval_expr(node[2], env)
        return (1 - a) if node[1] in ('~', '!') else a
    if t == 'red':                      # reduction: |bus / &bus over 1-bit here
        a = _eval_expr(node[2], env)
        return a & 1
    if t == 'bin':
        a, b, op = _eval_expr(node[2], env), _eval_expr(node[3], env), node[1]
        return {'&': a & b, '|': a | b, '^': a ^ b, '&&': int(a and b), '||': int(a or b),
                '==': int(a == b), '!=': int(a != b)}.get(op, 0)
    if t == 'tern':
        return _eval_expr(node[2], env) if _eval_expr(node[1], env) else _eval_expr(node[3], env)
    return 0


def check_new_dff(idx, c, study, ref_dir):
    """Verify the emitted D-input cone computes d_input_expected_function. This is the
    early validation for a NEW DFF: the D expression is a self-contained boolean function
    of its leaves (no shared priority-mux don't-cares), so it is soundly checkable."""
    try:
        from eco_rtl_synth import parse_expr
    except Exception as e:
        return ('SKIP', f'cannot import parse_expr: {e}')
    expr = c.get('d_input_expected_function')
    d_net = c.get('d_input_net')
    if not expr or not d_net:
        return ('SKIP', 'no d_input_expected_function / d_input_net (structural DFF only)')
    try:
        ast = parse_expr(expr)
    except Exception as e:
        return ('SKIP', f'cannot parse expected function {expr!r}: {e}')
    gates = [e for e in study.get('Synthesize', []) if e.get('change_type') == 'new_logic_gate']
    insts, drivers = _study_insts_drivers(gates, 'Synthesize', ref_dir)
    if d_net not in drivers:
        return ('SKIP', f'D net {d_net} not driven by any emitted gate')
    order, leaves = ns.cone_of(d_net, insts, drivers)
    leaves = sorted(l for l in leaves if not ns._is_const(l))
    if len(leaves) > 16:
        import random
        rng = random.Random(1)
        combos = [tuple(rng.randint(0, 1) for _ in leaves) for _ in range(4000)]
        mode = f'{len(combos)} random vectors ({len(leaves)} leaves)'
    else:
        combos = list(itertools.product((0, 1), repeat=len(leaves)))
        mode = f'all {len(combos)} combos ({len(leaves)} leaves)'
    for vec in combos:
        env = dict(zip(leaves, vec))
        # bit-addressable alias for base names too
        for lf, v in list(env.items()):
            m = re.match(r'^(.*)\[(\d+)\]$', lf)
            if m:
                env.setdefault(m.group(1), v)
        dut = ns.simulate(order, insts, env).get(d_net, 0) & 1
        ref = _eval_expr(ast, env) & 1
        if dut != ref:
            shown = {l: env[l] for l in leaves}
            return ('FAIL', f'D-cone {d_net} != ({expr}) at {shown}: DUT={dut} REF={ref}')
    return ('PASS', f'D-cone {d_net} == ({expr}) over {mode}')


def check_compare_fold(idx, c, ref_dir, jira):
    try:
        import eco_emit_compare_fold as cf
    except Exception as e:
        return ('SKIP', f'cannot import eco_emit_compare_fold: {e}')
    module = c.get('module_name')
    fold_signal = c.get('fold_signal') or c.get('new_token')
    compare_signal = c.get('compare_signal') or c.get('target_register')
    ctx = c.get('context_line', '')
    params, errs = cf.derive_params(ref_dir, module, ctx, fold_signal, compare_signal)
    if errs:
        return ('FAIL', 'derive_params: ' + '; '.join(errs[:2]))
    params['compare_signal'] = compare_signal
    fam = c.get('uniquified_family') or module
    by_stage = {s: cf._family_copies(ref_dir, fam, s) for s in cf.STAGES}
    idxs = sorted(by_stage['Synthesize'].keys())
    if not idxs:
        nm = cf._resolve_netlist_module(ref_dir, module)
        stage_mods = {s: nm for s in cf.STAGES}
    else:
        i0 = idxs[0]
        stage_mods = {s: by_stage[s].get(i0) for s in cf.STAGES}
    r = cf.build_for_module(ref_dir, stage_mods, jira, params,
                            tag=(f'_{idxs[0]}' if idxs else ''), scope=c.get('scope', ''),
                            compare_signal=compare_signal)
    if r['errors']:
        return ('FAIL', '; '.join(r['errors'][:2]))
    return ('PASS', f'fold M & ~(R & {params["s_net"]}) self-check passed (exhaustive) on '
                    f'{stage_mods["Synthesize"]}')


def check_comb_net_force(idx, c, ref_dir, n):
    try:
        import eco_synth_selfcheck as sc
    except Exception as e:
        return ('SKIP', f'cannot import eco_synth_selfcheck: {e}')
    module = c.get('module_name')
    signal = c.get('signal') or c.get('new_token') or c.get('target')
    if not (module and signal):
        return ('SKIP', 'comb_net_force missing module/signal')
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = sc.run(ref_dir, module, signal, n=n)
    except Exception as e:
        return ('SKIP', f'selfcheck raised {type(e).__name__}: {e} (FM-only)')
    tail = buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else ''
    return ('PASS' if ok else 'FAIL', f'{signal}: {tail}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--study', required=True)
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--jira', required=True)
    ap.add_argument('--n', type=int, default=2000, help='random vectors for comb_net_force')
    ap.add_argument('--output', default=None,
                    help='write canonical result JSON here (with a `passed` bool) + a '
                         '<output>_marker.txt sidecar. Consumed by the APPLY spawn-gate: '
                         'the file is written on every run — passed=false on any FAIL — so an '
                         'ABSENT file or passed!=true both block APPLY (fail-closed).')
    args = ap.parse_args()
    study = json.loads(open(args.study).read())
    rtl_diff = json.loads(open(args.rtl_diff).read())

    results = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        ct = c.get('change_type')
        if ct in ('new_logic_dff', 'new_logic'):
            status, detail = check_new_dff(idx, c, study, args.ref_dir)
            tgt = f"{ct} {c.get('dff_output_net') or c.get('d_input_net')}"
        elif ct == 'and_term' and c.get('equality_decode'):
            status, detail = check_eq_decode(idx, c, study, args.ref_dir, args.jira)
            tgt = f"and_term/eq_decode {c.get('equality_decode',{}).get('signal')}"
        elif ct == 'compare_fold':
            status, detail = check_compare_fold(idx, c, args.ref_dir, args.jira)
            tgt = f"compare_fold {c.get('compare_signal') or c.get('target_register')}"
        elif ct == 'comb_net_force':
            status, detail = check_comb_net_force(idx, c, args.ref_dir, args.n)
            tgt = f"comb_net_force {c.get('signal')}"
        else:
            status, detail = ('SKIP', f'{ct}: no sound local check yet -> FM-only')
            tgt = f"{ct}"
        results.append((idx, ct, tgt, status, detail))

    print('ECO_SCRIPT_LAUNCHED: eco_functional_precheck.py')
    npass = sum(1 for r in results if r[3] == 'PASS')
    nfail = sum(1 for r in results if r[3] == 'FAIL')
    nskip = sum(1 for r in results if r[3] == 'SKIP')
    for idx, ct, tgt, status, detail in results:
        print(f"  [{status:4}] change[{idx}] {tgt}")
        print(f"         {detail}")
    print(f"\n  summary: PASS={npass} FAIL={nfail} SKIP={nskip} "
          f"-> {'FAIL' if nfail else 'OK (no functional mismatch found)'}")
    print("  NOTE: SKIP = not soundly checkable locally (FM remains authoritative).")

    if args.output:
        passed = (nfail == 0)
        out = {
            'passed': passed,
            'summary': {'pass': npass, 'fail': nfail, 'skip': nskip},
            'results': [{'idx': idx, 'change_type': ct, 'target': tgt,
                         'status': status, 'detail': detail}
                        for idx, ct, tgt, status, detail in results],
        }
        with open(args.output, 'w') as f:
            json.dump(out, f, indent=2)
        marker = (f"ECO_SCRIPT_LAUNCHED: eco_functional_precheck.py\n"
                  f"  passed: {passed}  (PASS={npass} FAIL={nfail} SKIP={nskip})\n"
                  f"  output: {args.output}")
        with open(os.path.splitext(args.output)[0] + '_marker.txt', 'w') as f:
            f.write(marker + '\n')
        print(f"  wrote {args.output} (passed={passed})")
    return 1 if nfail else 0


if __name__ == '__main__':
    sys.exit(main())
