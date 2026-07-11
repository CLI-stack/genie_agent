#!/usr/bin/env python3
"""eco_fenets_chain.py — per-stage CHAINING for comb_net_force SELECTOR conditions.

The initial step-2 find_equivalent_nets run resolves the selector branch-conditions
(e.g. dsp_cmd_valid, dsp_cnt_end) ONLY in Synthesize: their RTL name exists in the
Synthesize target's reference (SynRtl), but the PrePlace/Route boundary targets reference
the PREVIOUS stage's NETLIST, where the RTL name was folded away by synthesis -> FM-036.

This resolves PP/Route by CHAINING: query the *previous stage's* resolved net against the
current stage's boundary target, with a SURVIVAL SHORTCUT (if the previous-stage net name
still exists in the current stage's netlist, reuse it — no query needed).

    Synth : dsp_cnt_end  --(SynRtl target)-->  N1920538
    PP    : N1920538      --(PP target, or survives)-->  FxPrePlace_ZINV_489_2238
    Route : <PP net>      --(Route target, or survives)-->  FxPlace_ZINV_338_1726

Phase is preserved by always chaining off a same-phase ('+') equivalent, so no inverter is
needed. The condition set is computed by eco_cone_rebuild.selector_folded_conditions() — the
SAME helper the deriver and the step-2 validator (C10) use, so all three cannot drift.

Two modes (the runner invokes find_equivalent_nets.csh between them):
  --mode emit-nets  --stage <PrePlace|Route>   -> prints nets to query (one per line) for that
                                                  stage's target; survivors are copied directly
                                                  into the rename map and written to --output.
  --mode merge      --stage <PrePlace|Route> --raw-rpt <FM output>  -> parse the chained FM
                                                  output, pick the best same-phase equivalent,
                                                  merge per-stage into the rename map (--output).
"""
import argparse, json, re, sys
from pathlib import Path

try:
    from eco_cone_rebuild import selector_folded_conditions, reg_guard_cone_leaves
    from eco_emit_priority_force import _module_netlist_body
except Exception:
    selector_folded_conditions = reg_guard_cone_leaves = _module_netlist_body = None

PREV = {'PrePlace': 'Synthesize', 'Route': 'PrePlace'}


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}


def _conditions(rtl_diff, ref_dir):
    """[(scope, signal, module_name), ...] folded-out guard/selector conditions across all
    comb_net_force AND reg_guard_delta (Intent-A and_term on a register) changes. Uses the
    shared helpers so it matches the deriver (Cat 4d/4e) and the step-2 validator exactly."""
    out = []
    for c in rtl_diff.get('changes', []):
        ct = c.get('change_type')
        scope = c.get('scope') or c.get('instance_scope') or ''
        mod = c.get('module_name') or ''
        sigs = []
        if ct == 'comb_net_force' and selector_folded_conditions:
            sigs = selector_folded_conditions(c, ref_dir)
        elif ct == 'and_term' and c.get('target_register') and reg_guard_cone_leaves:
            # chain the reg_guard BUILD cone leaves (queryable grounding nets, e.g. dsp_cmd_msc) —
            # NOT the dissolved folded guard (recdsp_c0cs, which FM can't anchor; it gets rebuilt).
            sigs = reg_guard_cone_leaves(c, ref_dir)
        for sig in sigs:
            key = (scope, sig, mod)
            if key not in out:
                out.append(key)
    return out


def _survives(net, ref_dir, module, stage):
    """True if net name still exists in `module`'s <stage> netlist body (survival shortcut)."""
    if not (_module_netlist_body and net):
        return False
    body = _module_netlist_body(ref_dir, module, stage) or ''
    return bool(re.search(r'\b' + re.escape(net) + r'\b', body))


def _parse_raw(path):
    """Parse a find_equivalent_nets output -> {ref_net_leaf: [(sign, impl_net_scoped_leaf)]}.
    ref_net_leaf / impl_net_scoped_leaf are the module-scope-relative net (after .../recdsp/)."""
    res, cur = {}, None
    txt = Path(path).read_text(errors='replace') if Path(path).is_file() else ''
    for ln in txt.splitlines():
        m = re.search(r'Net:\s+r:/\S+?/ddrss_\w+?_t/(.+?)\s*$', ln)
        if m:
            cur = m.group(1).rsplit('/', 1)[-1]     # leaf net name
            res.setdefault(cur, [])
            continue
        m2 = re.search(r'Impl\s+Net\s+([+-])\s+i:/\S+?/ddrss_\w+?_t/(.+?)\s*$', ln)
        if m2 and cur is not None:
            # keep the recdsp-scope-relative net (last 1-2 components: NET or CELL/PIN)
            full = m2.group(2)
            parts = full.split('/')
            impl = '/'.join(parts[-2:]) if parts[-1] in ('A1','A2','A3','B','B1','B2','C','I','ZN','Z','D','A') else parts[-1]
            res[cur].append((m2.group(1), impl))
    return res


def _pick(equivs):
    """Choose the best same-phase (+) equivalent: prefer a plain NET (no pin '/') over a pin."""
    plus = [e for s, e in equivs if s == '+']
    if not plus:
        return None
    nets = [e for e in plus if '/' not in e]
    return (nets or plus)[0]


def _stage_key_net(rmap, key, stage):
    e = rmap.get(key)
    return e.get(stage, '') if isinstance(e, dict) else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', required=True, choices=['emit-nets', 'merge'])
    ap.add_argument('--stage', required=True, choices=['PrePlace', 'Route'])
    ap.add_argument('--rename-map', required=True)
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--raw-rpt', default='', help='FM output (merge mode)')
    ap.add_argument('--output', default='', help='updated rename_map (defaults to --rename-map)')
    a = ap.parse_args()

    rmap = _load(a.rename_map)
    rtl = _load(a.rtl_diff)
    prev = PREV[a.stage]
    conds = _conditions(rtl, a.ref_dir)
    outp = a.output or a.rename_map

    if a.mode == 'emit-nets':
        to_query = []
        for scope, sig, mod in conds:
            key = f'{scope}/{sig}' if scope else sig
            prev_net = _stage_key_net(rmap, key, prev)
            if not prev_net or prev_net == sig or 'FM-036' in str(prev_net):
                print(f'# WARN: {key} has no resolved {prev} net — run initial fenets first',
                      file=sys.stderr)
                continue
            # SURVIVAL SHORTCUT: reuse the prev net name if it persists in this stage
            if _survives(prev_net, a.ref_dir, mod, a.stage):
                rmap.setdefault(key, {})[a.stage] = prev_net
            else:
                # query prev net against this stage's target; print scope-relative path
                to_query.append(f'{scope}/{prev_net}' if scope else prev_net)
        Path(outp).write_text(json.dumps(rmap, indent=0))
        # emit the query list on stdout for the runner to pass to find_equivalent_nets.csh
        for q in to_query:
            print(q)
        return 0

    # merge mode
    parsed = _parse_raw(a.raw_rpt) if a.raw_rpt else {}
    missing = []
    for scope, sig, mod in conds:
        key = f'{scope}/{sig}' if scope else sig
        if isinstance(rmap.get(key), dict) and rmap[key].get(a.stage):
            continue   # already set (survivor)
        prev_net = _stage_key_net(rmap, key, prev)
        equivs = parsed.get(prev_net.rsplit('/', 1)[-1], [])
        pick = _pick(equivs)
        if pick:
            rmap.setdefault(key, {})[a.stage] = pick
        else:
            missing.append(key)
    Path(outp).write_text(json.dumps(rmap, indent=0))
    if missing:
        print(f'CHAIN {a.stage}: UNRESOLVED (no + equivalent): {missing}', file=sys.stderr)
        return 1
    print(f'CHAIN {a.stage}: resolved {len([c for c in conds])} condition(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
