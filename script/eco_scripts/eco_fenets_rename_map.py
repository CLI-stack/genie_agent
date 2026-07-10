#!/usr/bin/env python3
"""
eco_fenets_rename_map.py — Build per-stage rename map JSON from FM
find_equivalent_nets raw rpts + the rtl_diff JSON.

The rename map is the AUTHORITATIVE per-stage net resolution that
eco_netlist_studier consults FIRST in its 0b-STAGE-NETS phase. Replaces
the studier's neighbor-DFF inference for any signal in the map.

Usage:
    python3 eco_fenets_rename_map.py \\
        --rtl-diff data/<TAG>_eco_rtl_diff.json \\
        --raw-dir  data/                         (or --raw-files <paths>) \\
        --tag      <TAG> \\
        --tile     <TILE> \\
        --output   data/<TAG>_eco_fenets_rename_map.json

The eco_step2_fenets.rpt human-review file is unchanged — this script
ONLY produces the structured JSON map.

Output schema:
    {
      "_metadata": {"tag": ..., "tile": ..., "queries_total": N, "raw_rpts_parsed": [...]},
      "<scope>/<signal>": {
        "Synthesize": "<net_name>",
        "PrePlace":   "<net_name>",
        "Route":      "<net_name>",
        "source":     "changes[<idx>].<source_field>",
        "warning":    "<optional, set when no equivalents found>",
        "mode_I_signature": <bool, set when query was Mode I candidate AND no driver found>
      },
      ...
    }
"""
import argparse, glob, json, os, re, subprocess, sys
from pathlib import Path
try:
    from eco_fenets_derive_queries import _pf_cone_leaves
except Exception:
    _pf_cone_leaves = None
try:
    from eco_cone_rebuild import cone_leaves as _cnf_cone_leaves
except Exception:
    _cnf_cone_leaves = None
try:
    from eco_cone_rebuild import selector_folded_conditions as _cnf_sel_conds
except Exception:
    _cnf_sel_conds = None

# ── Raw FM rpt parser ────────────────────────────────────────────────────────

# Block markers in a raw fenets rpt
TARGET_RE = re.compile(r'^TARGET:\s*(FmEqv\S+)')
NET_RE    = re.compile(r'^Net:\s*r:/[^/]+/[^/]+/(\S+)')
FM036_RE  = re.compile(r'\(FM-036\)')
NOEQ_RE   = re.compile(r'^---\s*No Equivalent Nets:')
EQ_RE     = re.compile(r'^---\s*Equivalent Nets:')
IMPL_NET_RE = re.compile(r'^\s*Impl\s+Net\s+([+\-])\s+i:/[^/]+/[^/]+/(\S+)')

# FmEqv target → stage name map
TARGET_TO_STAGE = {
    'FmEqvPreEcoSynthesizeVsPreEcoSynRtl':       'Synthesize',
    'FmEqvPreEcoPrePlaceVsPreEcoSynthesize':     'PrePlace',
    'FmEqvPreEcoRouteVsPreEcoPrePlace':          'Route',
    'FmEqvEcoSynthesizeVsSynRtl':                'Synthesize',
    'FmEqvEcoPrePlaceVsEcoSynthesize':           'PrePlace',
    'FmEqvEcoRouteVsEcoPrePlace':                'Route',
}

def parse_raw_rpt(path):
    """Parse one raw fenets rpt. Returns dict {(stage, net_signal): result}
    where result is {'status': 'FOUND'|'FM036'|'NO_EQUIV', 'positive': [<impl_net>], 'inverted': [<impl_net>]}."""
    out = {}
    cur_stage = None
    cur_net = None     # short signal name (last path component)
    cur_status = None
    cur_pos = []
    cur_neg = []

    def flush():
        if cur_stage and cur_net:
            out[(cur_stage, cur_net)] = {
                'status': cur_status,
                'positive': list(cur_pos),
                'inverted': list(cur_neg),
            }

    with open(path, errors='ignore') as f:
        for line in f:
            m = TARGET_RE.match(line)
            if m:
                flush()
                cur_stage = TARGET_TO_STAGE.get(m.group(1))
                cur_net = None
                cur_status = None
                cur_pos = []; cur_neg = []
                continue
            n = NET_RE.match(line)
            if n:
                flush()
                full = n.group(1)
                # Reduce to leaf signal name to match how queries are keyed
                # (build_rename_map looks up by short signal name, not full path).
                cur_net = full.rsplit('/', 1)[-1]
                # Normalize bus suffix: 'BeqCtrlPeSrc_0_' → 'BeqCtrlPeSrc'
                if cur_net.endswith('_0_'):
                    cur_net = cur_net[:-3]
                cur_net = cur_net.rstrip('_')
                cur_status = 'PENDING'
                cur_pos = []; cur_neg = []
                continue
            if cur_net is None:
                continue
            if FM036_RE.search(line):
                cur_status = 'FM036'
                continue
            if NOEQ_RE.match(line):
                cur_status = 'NO_EQUIV'
                continue
            if EQ_RE.match(line):
                cur_status = 'FOUND'
                continue
            i = IMPL_NET_RE.match(line)
            if i and cur_status == 'FOUND':
                pol, net_full = i.group(1), i.group(2)
                # net_full may be cell/pin (e.g. "phfnr_buf/I") — keep cell.pin as-is
                short = net_full
                if pol == '+': cur_pos.append(short)
                else:          cur_neg.append(short)
    flush()
    return out


# ── Build query plan from rtl_diff ───────────────────────────────────────────

def derive_queries(rtl_diff, ref_dir=None):
    """Walk changes[] and produce list of {net_path, signal, source} for every
    net we want a per-stage rename for. See eco_fenets_runner.md STEP A
    for the 7 categories. With ref_dir, also derives priority_force condition-cone
    leaves (matching eco_fenets_derive_queries) so the map covers them."""
    queries = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        ct = c.get('change_type', '')
        scope = c.get('scope') or c.get('instance_scope') or ''

        # priority_force condition-cone leaves (same derivation as the query script)
        if ct == 'priority_force' and ref_dir and _pf_cone_leaves:
            for leaf in _pf_cone_leaves(c, ref_dir):
                queries.append({'net_path': f'{scope}/{leaf}'.strip('/'),
                                'signal': leaf,
                                'source': f'changes[{idx}].priority_force_cone_leaf'})

        # comb_net_force cone leaves (mirror eco_fenets_derive_queries Cat 4c) so the
        # map emits per-stage keys for the rebuilt cone leaves that were queried.
        if ct == 'comb_net_force' and ref_dir and _cnf_cone_leaves:
            sig = c.get('signal') or c.get('new_token') or c.get('target')
            if sig:
                for leaf in _cnf_cone_leaves(ref_dir, c.get('module_name') or '', sig):
                    queries.append({'net_path': f'{scope}/{leaf}'.strip('/'),
                                    'signal': leaf,
                                    'source': f'changes[{idx}].comb_net_force_cone_leaf'})

        # Cat 4d: comb_net_force SELECTOR branch-conditions (mirror eco_fenets_derive_queries
        # Cat 4d). These folded conditions (e.g. dsp_cmd_valid, dsp_cnt_end) resolve ONLY in
        # Synthesize (RTL name lives in SynRtl); PP/Route echo/FM-036 here and are fixed by the
        # per-stage chaining pass (eco_fenets_chain.py). Emitting the key so the Synthesize seed
        # exists for that chain.
        if ct == 'comb_net_force' and ref_dir and _cnf_sel_conds:
            for cond in _cnf_sel_conds(c, ref_dir):
                queries.append({'net_path': f'{scope}/{cond}'.strip('/'),
                                'signal': cond,
                                'source': f'changes[{idx}].comb_net_force_selector_cond'})

        # Cat 1: wire_swap / and_term tokens
        if ct in ('wire_swap', 'and_term'):
            for fld in ('old_token', 'new_token'):
                t = c.get(fld)
                if t:
                    queries.append({'net_path': f'{scope}/{t}'.strip('/'),
                                    'signal': t, 'source': f'changes[{idx}].{fld}'})
        # Cat 2-4: new_logic DFF + chain leaves
        if ct in ('new_logic', 'new_logic_dff'):
            for fld in ('dff_clock', 'reset_signal'):
                v = c.get(fld)
                if v:
                    queries.append({'net_path': f'{scope}/{v}'.strip('/'),
                                    'signal': v, 'source': f'changes[{idx}].{fld}'})
            for g in (c.get('d_input_gate_chain') or []):
                for inp in (g.get('inputs') or []):
                    base = inp.split('[')[0]
                    if base.startswith(('n_eco_', "1'b", "0'b")):
                        continue
                    queries.append({'net_path': f'{scope}/{base}'.strip('/'),
                                    'signal': base,
                                    'source': f'changes[{idx}].chain[{g.get("seq","?")}]'})
        # Cat 5: port_promotion
        if ct == 'port_promotion':
            s = c.get('signal_name') or c.get('new_token')
            if s:
                queries.append({'net_path': f'{scope}/{s}'.strip('/'),
                                'signal': s, 'source': f'changes[{idx}].port_promotion'})
        # Cat 6: Mode I candidates (UNCONNECTED bus rename targets)
        unc = c.get('original_unconnected_net', '') or ''
        if unc.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_')):
            sm = c.get('submodule_instance') or c.get('instance_name', '')
            port = c.get('port_name', '')
            bbi = c.get('bus_bit_index')
            if sm and port and bbi is not None:
                signal = f'{port}[{bbi}]'
                queries.append({'net_path': f'{scope}/{sm}/{signal}'.strip('/'),
                                'signal': signal,
                                'source': f'changes[{idx}].mode_I_candidate',
                                'mode_I_candidate': True})

        # Cat 8: Mode-S anchor wires (SI/SE/Q for new DFF scan stitching).
        # Without these in the rename map, Step 3 studier has no per-stage
        # data for the bridge source wires and must grep the netlist directly
        # (fragile — works when names are stable, fails when CTS rebalances).
        anchor = c.get('mode_s_anchor') or {}
        if anchor:
            fm_scope    = anchor.get('fm_scope', '')
            for role, fld in (('SI', 'anchor_si_wire'),
                              ('SE', 'anchor_se_wire'),
                              ('Q',  'anchor_q_wire')):
                wire = anchor.get(fld)
                if wire:
                    queries.append({
                        'net_path':    f'{fm_scope}/{wire}'.strip('/'),
                        'signal':      wire,
                        'source':      f'changes[{idx}].mode_s_anchor.{fld}',
                        'cat_8_anchor': True,
                        'anchor_role': role,
                    })

    # Deduplicate by net_path (preserve first source)
    seen = set(); unique = []
    for q in queries:
        if q['net_path'] in seen:
            continue
        seen.add(q['net_path'])
        unique.append(q)
    return unique


# ── Build rename map ─────────────────────────────────────────────────────────

# Wire-on-pin lookup cache (per-stage): {(stage, cell_pin): wire_name}
_WIRE_ON_PIN_CACHE = {}


def _wire_on_pin(cell_pin, ref_dir, stage):
    """Given `<cell_instance>/<pin>`, return the actual wire connected to
    that pin in the stage's PreEco netlist. Caches the lookup. Returns
    empty string on failure (caller should fall back to the cell_pin form).

    This is the polarity-correct identifier for the signal at that location,
    distinct from any bare RTL name that may have INVs inserted between the
    pin and the RTL-named wire (P&R drive-strength optimization). Patch #3
    of the Rule 32 polarity fix — see CRITICAL_RULES.md Rule 32 and
    eco_validate_step3.py Check 38.
    """
    if not cell_pin or '/' not in cell_pin:
        return ''
    inst, pin = cell_pin.rsplit('/', 1)
    if not ref_dir:
        return ''
    key = (stage, inst, pin)
    if key in _WIRE_ON_PIN_CACHE:
        return _WIRE_ON_PIN_CACHE[key]
    gz = os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
    if not os.path.isfile(gz):
        _WIRE_ON_PIN_CACHE[key] = ''
        return ''
    try:
        # Find the instance block then extract `.pin(wire)` value
        cmd = (f"zcat {gz} | grep -A20 '\\b{re.escape(inst)}\\s*(' "
               f"| head -25")
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=60)
        block = r.stdout or ''
        m = re.search(rf'\.\s*{re.escape(pin)}\s*\(\s*([A-Za-z_][\w\[\]]*)\s*\)',
                      block)
        result = m.group(1) if m else ''
    except Exception:
        result = ''
    _WIRE_ON_PIN_CACHE[key] = result
    return result


def build_rename_map(rtl_diff, fm_results, tag, tile, raw_rpts, ref_dir=''):
    """fm_results is dict {(stage, signal): {status, positive, inverted}} merged
    across all parsed raw rpts. ref_dir is used by _wire_on_pin for Patch #3
    polarity-correct actual_wire emission."""
    queries = derive_queries(rtl_diff, ref_dir)
    rmap = {
        '_metadata': {
            'tag': tag,
            'tile': tile,
            'queries_total': len(queries),
            'raw_rpts_parsed': [str(p) for p in raw_rpts],
        }
    }
    for q in queries:
        sig = q['signal']
        entry = {'source': q['source']}
        had_warning = False
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            r = fm_results.get((stage, sig))
            if r is None or r['status'] in ('FM036', None):
                # Fallback to original signal name (gate-level Synth uses RTL names directly)
                entry[stage] = sig
            elif r['status'] == 'NO_EQUIV':
                entry[stage] = sig
                had_warning = True
            elif r['status'] == 'FOUND' and r['positive']:
                # Prefer a positive net whose path passes through the SAME scope
                # as the original RTL signal (gives studier a scope-correct alias);
                # fall back to first positive when no scope match exists.
                scope_hint = (q.get('net_path') or '').rsplit('/', 1)[0]
                pick = None
                for p in r['positive']:
                    if scope_hint and scope_hint in p:
                        pick = p; break
                if pick is None:
                    pick = r['positive'][0]

                # CLOCK signals: the FM "equivalent nets" for a clock contain
                # every register's clock pin (CP / clocked_on / etc.) — picking
                # the first one returns a misleading `<some_reg>/CP` cell-pin
                # path instead of the clock WIRE name. Worse, the picked
                # register differs per stage → Check 27 false-positives a
                # clock-stage mismatch on what is actually the same UCLK root.
                # For clock queries, return the bare clock signal name (the
                # studier/applier resolves the per-stage wire by greping the
                # actual host module body — see eco_emit_dff_entry.py).
                is_clock_query = ('dff_clock' in q.get('source', '') or
                                  re.search(r'(?i)\bclk\b|^[A-Z]+CLK[A-Z0-9_]*$', sig or ''))
                if is_clock_query:
                    entry[stage] = sig
                else:
                    # Keep the last 2 path components so studier sees
                    # `<cell>/<pin>` (e.g. `ArbCtrlPeRdy_reg/Q`) rather than
                    # just `Q` — useful for disambiguating identical pin names
                    # across cells.
                    parts = pick.rsplit('/', 2)
                    entry[stage] = '/'.join(parts[-2:]) if len(parts) >= 2 else pick
                    # Also emit the polarity-correct ACTUAL WIRE on that pin
                    # (Patch #3 for Rule 32 fix). Studier should use
                    # `actual_wire_<stage>` instead of bare RTL name whenever
                    # they disagree. P&R may add odd inverters between a
                    # registered driver and the port-named wire (drive-
                    # strength optimization at module boundaries) — bare
                    # RTL name then carries INVERSE logical value vs. the
                    # FM-resolved pin. See run 20260515084942 round 6 for
                    # the ArbCtrlPeRdy 3-INV polarity flip in Route.
                    if ref_dir:
                        cell_pin = entry[stage]
                        actual_wire = _wire_on_pin(cell_pin, ref_dir, stage)
                        if actual_wire:
                            entry[f'actual_wire_{stage}'] = actual_wire
            else:
                entry[stage] = sig
                had_warning = True
        if had_warning:
            entry['warning'] = 'no equivalent nets found in some stage — studier should fall back to neighbor-DFF inference for those stages'
        # Mode I signature: candidate query AND all stages fell through to original name
        if q.get('mode_I_candidate'):
            if all(entry.get(s) == sig for s in ('Synthesize', 'PrePlace', 'Route')):
                entry['mode_I_signature'] = True
        rmap[q['net_path']] = entry
    return rmap


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--rtl-diff', required=True)
    p.add_argument('--raw-dir',  default=None,
                   help='Directory holding *_find_equivalent_nets_raw*.rpt files. '
                        'If provided, all matching files are parsed.')
    p.add_argument('--raw-files', nargs='*', default=[],
                   help='Explicit list of raw rpt paths to parse '
                        '(overrides --raw-dir if both given)')
    p.add_argument('--tag',  required=True)
    p.add_argument('--tile', required=True)
    p.add_argument('--ref-dir', default='',
                   help='REF_DIR for per-stage PreEco netlist lookup '
                        '(Patch #3 — actual_wire_<stage> polarity-correct '
                        'wire-on-pin resolution to defeat Rule 32 polarity '
                        'flips at module boundaries with odd inverter chains)')
    p.add_argument('--output', required=True)
    args = p.parse_args()

    # Discover raw rpts — ONLY for this run's fenets tags (avoid pollution from other runs).
    # Each raw rpt filename starts with the fenets_tag (a timestamp), NOT the ECO tag.
    # The eco_fenets_runner passes the exact files via --raw-files; --raw-dir is a fallback
    # that reads the fenets_tag list from data/<TAG>_eco_fenets_queries.json to filter.
    raw_paths = list(args.raw_files)
    if not raw_paths and args.raw_dir:
        # Try to read fenets tags from queries JSON to filter only this run's rpts
        queries_json = os.path.join(args.raw_dir, f'{args.tag}_eco_fenets_queries.json')
        fenets_tags = set()
        if os.path.exists(queries_json):
            try:
                qdata = json.load(open(queries_json))
                # queries JSON may list fenets_tags used
                for entry in (qdata if isinstance(qdata, list) else []):
                    ft = entry.get('fenets_tag') or entry.get('tag')
                    if ft:
                        fenets_tags.add(str(ft))
            except Exception:
                pass
        if fenets_tags:
            # Filter raw rpts to only those whose filename starts with a known fenets_tag
            all_rpts = sorted(glob.glob(os.path.join(args.raw_dir,
                                                      '*find_equivalent_nets_raw*.rpt')))
            raw_paths = [p for p in all_rpts
                         if any(os.path.basename(p).startswith(ft) for ft in fenets_tags)]
        if not raw_paths:
            # Final fallback: glob all — warn about potential pollution
            raw_paths = sorted(glob.glob(os.path.join(args.raw_dir,
                                                       '*find_equivalent_nets_raw*.rpt')))
            if raw_paths:
                print(f'WARN: eco_fenets_rename_map: using all {len(raw_paths)} raw rpts in '
                      f'{args.raw_dir} — pass --raw-files for this run\'s rpts only to avoid '
                      f'pollution from other runs.', file=sys.stderr)
    if not raw_paths:
        print('FAIL: no raw fenets rpts to parse '
              '(use --raw-dir or --raw-files)', file=sys.stderr)
        return 1

    # Parse all raw rpts and merge results
    fm_results = {}
    for path in raw_paths:
        if not os.path.exists(path):
            print(f'WARN: raw rpt not found: {path}', file=sys.stderr)
            continue
        partial = parse_raw_rpt(path)
        # Merge — prefer FOUND over FM036 / NO_EQUIV when same key seen multiple times
        for k, v in partial.items():
            cur = fm_results.get(k)
            if cur is None:
                fm_results[k] = v
            elif cur['status'] != 'FOUND' and v['status'] == 'FOUND':
                fm_results[k] = v

    # Build map
    try:
        rtl_diff = json.loads(Path(args.rtl_diff).read_text())
    except Exception as e:
        print(f'FAIL: cannot read rtl_diff: {e}', file=sys.stderr)
        return 1

    rmap = build_rename_map(rtl_diff, fm_results, args.tag, args.tile, raw_paths,
                             ref_dir=args.ref_dir)
    Path(args.output).write_text(json.dumps(rmap, indent=2))

    n = len(rmap) - 1  # minus _metadata
    n_warned = sum(1 for k, v in rmap.items() if k != '_metadata' and v.get('warning'))
    n_mode_i = sum(1 for k, v in rmap.items() if k != '_metadata' and v.get('mode_I_signature'))
    print(f'ECO_RPT_GENERATED: rename map → {args.output}')
    print(f'  queries:        {n}')
    print(f'  with warning:   {n_warned}')
    print(f'  Mode I flagged: {n_mode_i}')
    print(f'  raw rpts:       {len(raw_paths)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
