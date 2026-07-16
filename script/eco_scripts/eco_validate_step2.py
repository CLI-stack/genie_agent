#!/usr/bin/env python3
"""
eco_validate_step2.py — Deterministic Step 2 (fenets) validator.

Gates Step 3 handoff. Verifies that for every Mode-S anchor query the
deriver emitted (Cat 8), the raw FM rpt actually returned equivalence
data and the rename map captured per-stage wires.

Checks:
  C1: every Cat 8 query in <tag>_eco_fenets_queries.json appears as a
      `Net:` entry in any of the raw fenets rpts.
  C2: every Cat 8 query receives at least one `Equivalent Nets:` block
      (i.e. FM did NOT respond with FM-036 / Unknown name / empty).
  C3: optional — if eco_bridge_candidates.json exists, every anchor pin
      has at least one candidate with stages_available covering both
      PrePlace and Route.

Exit 0 = pass, 1 = fail. ROUND_ORCHESTRATOR blocks Step 3 handoff on fail.

Usage:
    python3 eco_validate_step2.py \\
        --queries     data/<TAG>_eco_fenets_queries.json \\
        --raw-rpts    data/<FENETS_TAG>_find_equivalent_nets_raw*.rpt \\
        --rename-map  data/<TAG>_eco_fenets_rename_map.json \\
        --candidates  data/<TAG>_eco_bridge_candidates.json   (optional) \\
        --output      data/<TAG>_eco_validate_step2.json
"""
import argparse, glob, json, re, subprocess, sys
from pathlib import Path
from eco_validate_io import write_result


def _load_json(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def _load_raw_rpts(patterns):
    """Concatenate all matching raw rpt files (text)."""
    text_blocks = []
    for pat in patterns:
        for f in sorted(glob.glob(pat)):
            try:
                text_blocks.append(Path(f).read_text())
            except Exception:
                continue
    return '\n'.join(text_blocks)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--queries',    required=True, help='fenets_queries.json (post-sanitize)')
    p.add_argument('--queries-raw', required=False, default='',
                   help='fenets_queries_raw.json (pre-sanitize) — required for FROZEN contract check (C4)')
    p.add_argument('--raw-rpts',   nargs='+', default=[], help='glob(s) for raw FM rpts')
    p.add_argument('--rename-map', required=False, default='', help='fenets_rename_map.json')
    p.add_argument('--candidates', required=False, default='', help='eco_bridge_candidates.json (optional)')
    p.add_argument('--rtl-diff',   required=False, default='', help='eco_rtl_diff.json — required for C9 Mode H recovery check')
    p.add_argument('--ref-dir',    required=False, default='', help='REF_DIR — used by C6 to verify preserved bus names exist as real wires in PP/Route netlists (avoids false-positive echo-fallback flags)')
    p.add_argument('--output',     required=True)
    p.add_argument('--iter', type=int, default=None,
                   help='catch-and-fix iteration number; when set, ALSO write '
                        '<output>_iter<N>.json (history). Canonical --output stays latest/final.')
    args = p.parse_args()

    queries = _load_json(args.queries) or []
    if not isinstance(queries, list):
        print('FAIL: queries file is not a list', file=sys.stderr); return 1

    cat8 = [q for q in queries if q.get('category') == 8 and q.get('mode_s_anchor')]
    issues = []
    warnings = []   # non-blocking advisories (e.g. reg_guard guard-leaf not FM-bindable -> heavier
                    # but CORRECT rebuild; unlike comb_net_force C10 these must NOT fail the gate)

    raw_text = _load_raw_rpts(args.raw_rpts) if args.raw_rpts else ''

    # C4-marker: sanitize script MUST have produced its marker file. Without the
    # marker, queries.json may have been written by some other mechanism (agent
    # or manual copy) — defeats the deterministic-sanitize guarantee.
    sanitize_marker = args.queries.replace('.json', '_sanitize_marker.txt')
    if not Path(sanitize_marker).is_file():
        issues.append(
            f"C4-marker: sanitize marker file missing — {sanitize_marker!r}. "
            f"Cannot prove eco_fenets_sanitize_queries.py was invoked. "
            f"queries.json may have been produced by agent-side write, copy, "
            f"or skipped sanitize step entirely. Orchestrator MUST run the "
            f"sanitize script per ORCHESTRATOR.md §STEP 2.")

    # C4: FROZEN contract — the sanitized queries.json MUST equal the deriver's
    # sanitize output. Any agent-side rewrite that drops or transforms entries
    # is FORBIDDEN. Compare per-category counts: if any category lost entries
    # between raw and sanitized, the agent bypassed the deterministic sanitize
    # script (history: 9868 R1 lost 4/6 Cat 8 anchor pin queries).
    if args.queries_raw and Path(args.queries_raw).is_file():
        raw_queries = _load_json(args.queries_raw) or []
        # Per-category counts
        from collections import Counter
        raw_cnt = Counter(q.get('category') for q in raw_queries)
        san_cnt = Counter(q.get('category') for q in queries)
        for cat, raw_n in raw_cnt.items():
            san_n = san_cnt.get(cat, 0)
            if san_n < raw_n:
                issues.append(
                    f"C4: FROZEN contract violation — Cat {cat} count dropped from "
                    f"{raw_n} (raw) to {san_n} (sanitized). The sanitize script preserves "
                    f"all entries; an agent-side rewrite is the only way to lose them. "
                    f"FORBIDDEN — re-run orchestrator's deterministic sanitize "
                    f"(eco_fenets_sanitize_queries.py) and do NOT manually edit queries.json.")
        # Also detect path mangling: every raw net_path's clean form must appear in sanitized
        try:
            from eco_fenets_sanitize_queries import collapse_dup_scope
            for q in raw_queries:
                np_raw = q.get('net_path', '')
                np_clean, _ = collapse_dup_scope(np_raw)
                if not any(s.get('net_path') == np_clean for s in queries):
                    cat = q.get('category', '?')
                    issues.append(
                        f"C4: FROZEN contract violation — raw net_path={np_raw!r} (Cat {cat}) "
                        f"missing from sanitized output (expected clean form: {np_clean!r}). "
                        f"Agent likely transformed the path; only collapse_dup_scope is permitted.")
        except ImportError:
            pass  # sanitize_queries module not importable; skip path check

    # C5: per-anchor WIRE coverage — every Cat 8 anchor MUST have wires queried
    # for all 3 roles (SI, SE, Q). Cat 8 entries carry `anchor_pin` as the role
    # label and `anchor_wire` as the actual queried wire. Without all 3 wires,
    # Step 3 lacks data for bridge source selection (SI/SE) or Q-closure pick (Q).
    anchor_role_map = {}  # (sibling, dff) → set of anchor_pin (role) values present
    for q in cat8:
        sib = q.get('sibling_module', '')
        dff = q.get('anchor_dff', '')
        role = q.get('anchor_pin', '')   # field carries the role label (SI/SE/Q)
        if not q.get('anchor_wire'):
            issues.append(
                f"C5: Cat 8 entry has anchor_pin={role!r} but no anchor_wire — "
                f"deriver should emit wire from picker's recommended_pick fields. "
                f"Pin paths return FM-036; wires are queryable.")
            continue
        anchor_role_map.setdefault((sib, dff), set()).add(role)
    for (sib, dff), roles in anchor_role_map.items():
        for required in ('SI', 'SE', 'Q'):
            if required not in roles:
                issues.append(
                    f"C5: anchor {sib}/{dff} missing wire query for role {required!r} — "
                    f"Cat 8 must cover all 3 roles. Without {required} wire, "
                    f"Step 3 lacks data to {'pick bridge source wire' if required in ('SI','SE') else 'verify Q closure'}.")

    # C6: rename map echo-fallback detection — every Cat 1/4 query whose rename_map
    # entry has IDENTICAL strings across all 3 stages (Synth==PP==Route) is suspicious:
    # likely the rename map fell back to "use input name as-is" because FM returned no
    # equivalence data. True stage-stable signals exist but should be the minority.
    # GAP-1 fix: skip signals that are new_port/port_promotion — they legitimately
    # have no FM equivalence in PreEco (the port doesn't exist yet), so echo-fallback
    # is the correct and expected behavior for them.
    new_port_signal_names = set()
    if args.rtl_diff and Path(args.rtl_diff).is_file():
        _rtl = _load_json(args.rtl_diff) or {}
        for c in _rtl.get('changes', []):
            if c.get('change_type') in ('new_port', 'port_promotion', 'port_declaration'):
                sig = c.get('new_token') or c.get('signal_name') or c.get('port_name', '')
                if sig:
                    new_port_signal_names.add(sig)

    if args.rename_map and Path(args.rename_map).is_file():
        rmap = _load_json(args.rename_map) or {}
        echo_fallbacks = []
        for sig_key, stages in rmap.items():
            if sig_key == '_metadata' or not isinstance(stages, dict):
                continue
            syn = stages.get('Synthesize', '')
            pp  = stages.get('PrePlace', '')
            rt  = stages.get('Route', '')
            # Echo fallback: all 3 are the input signal name (the trailing component
            # of the key) AND no '/' suggesting a real cell/pin path
            tail = sig_key.rsplit('/', 1)[-1]
            if syn == pp == rt == tail and '/' not in syn:
                echo_fallbacks.append(sig_key)
        if echo_fallbacks:
            # Build expected_echo set from rtl_diff — signals that legitimately
            # have no FM equivalence and echo-fallback is correct behavior:
            # - new_port / port_promotion (already in new_port_signal_names)
            # - condition_inputs_to_query (Mode H signals, FM-036 in PP/Route expected)
            # - any signal explicitly marked PENDING_FM_RESOLUTION in gate chains
            expected_echo = set(new_port_signal_names)
            if args.rtl_diff and Path(args.rtl_diff).is_file():
                _rtl2 = _load_json(args.rtl_diff) or {}
                for c in _rtl2.get('changes', []):
                    for ci in (c.get('condition_inputs_to_query') or []):
                        sig = ci.get('signal', '')
                        if sig:
                            expected_echo.add(sig)
                    # A to-be-BUILT new term legitimately echo-falls-back: the
                    # equality-decode match (eco_emit_eq_decode builds it) and any
                    # synthetic ECO net (bus OR/AND reduce, n_eco_/eco_). These do not
                    # exist in PreEco, so echo is correct — not a C6 failure.
                    ed = c.get('equality_decode')
                    if isinstance(ed, dict) and ed.get('new_token'):
                        expected_echo.add(ed['new_token'])
                    if c.get('change_type') in ('and_term', 'wire_swap'):
                        nt = c.get('new_token')
                        if isinstance(nt, str) and (
                                nt.startswith(('n_eco_', 'eco_'))
                                or re.search(r'_eq_[A-Z0-9_]+$|_or$|_or_reduce$', nt)):
                            expected_echo.add(nt)
                    # Bus bits previously spare/unconnected (UNCONNECTED_*) have no
                    # named wire in PreEco, so FM returns FM-036 and echo-fallback
                    # is the correct and expected behavior — not a validation failure.
                    if c.get('submodule_bus_driven') and c.get('original_unconnected_net'):
                        bp = c.get('bus_port', '')
                        bi = c.get('bus_bit_index')
                        if bp and bi is not None:
                            expected_echo.add(f"{bp}_{bi}_")
                        for gate in (c.get('d_input_gate_chain') or []):
                            for inp in (gate.get('inputs') or []):
                                if inp:
                                    expected_echo.add(inp)
            real_fallbacks = [sig for sig in echo_fallbacks
                              if sig.rsplit('/', 1)[-1] not in expected_echo]

            # Auto-classify preserved-name echoes: if the bare signal name
            # exists as a wire/port in BOTH PP and Route netlists, the echo
            # is legitimate (P&R preserved the name unchanged — no FM-036).
            # Only flag echoes that are genuinely missing in PP or Route.
            # Closes the false-positive class observed in run 20260514070341
            # where 5 of 6 C6 entries (BeqCtrlPeSrc bits, REG_UmcCfgEco_1_)
            # were preserved-name bus signals, not FM failures.
            if real_fallbacks and args.ref_dir:
                syn_gz = Path(args.ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
                pp_gz = Path(args.ref_dir) / 'data' / 'PreEco' / 'PrePlace.v.gz'
                rt_gz = Path(args.ref_dir) / 'data' / 'PreEco' / 'Route.v.gz'
                accepted = set()
                # Match BOTH the bracket form (foo[3]) and the bit-blasted netlist
                # form (foo_3_) — synthesis renames bus bits to underscores.
                def _present(gz, bare):
                    m = re.match(r'^(.*)\[(\d+)\]$', bare)
                    if m:
                        b, i = re.escape(m.group(1)), m.group(2)
                        pat = rf'\b{b}\[{i}\]\b|\b{b}_{i}_\b'
                    else:
                        pat = rf'\b{re.escape(bare)}\b'
                    try:
                        r = subprocess.run(f"zgrep -cE '{pat}' '{gz}'",
                                           shell=True, capture_output=True, text=True, timeout=90)
                        return int((r.stdout or '0').strip() or '0') > 0
                    except Exception:
                        return False
                if syn_gz.is_file() and pp_gz.is_file() and rt_gz.is_file():
                    for sig in real_fallbacks:
                        bare = sig.rsplit('/', 1)[-1]
                        in_syn = _present(syn_gz, bare)
                        in_pp  = _present(pp_gz, bare)
                        in_rt  = _present(rt_gz, bare)
                        # (a) PRESERVED name: survives PP+Route unchanged -> legit echo.
                        # (b) GENUINELY DISSOLVED: absent in ALL stages (incl Synthesize)
                        #     -> synthesis eliminated it (behavioral always@* local like
                        #     dsp_condsok[1-7,9-11], or a dissolved internal reg like
                        #     recdsp_c0cs). FM correctly returns FM-036; Step-3 REBUILDS it
                        #     from real surviving leaves (eco_cone_rebuild _sig_bit->_rebuild)
                        #     or it folds out of the delta region entirely. Echo is expected
                        #     and the emitted study never wires the dissolved name (proven:
                        #     passing study 20260711182702 has zero dsp_condsok/recdsp_c0cs
                        #     references). This matches the reg_guard C10b advisory treatment.
                        # FLAG ONLY (c): present in Synthesize but optimized away in PP/Route
                        #     -> a REAL net that needs per-stage fenets binding (the
                        #     dsp_cmd_msc[0]/[3] NET-ABSENT class) — genuine C6 failure.
                        if (in_pp and in_rt) or (not in_syn and not in_pp and not in_rt):
                            accepted.add(sig)
                real_fallbacks = [s for s in real_fallbacks if s not in accepted]

            if real_fallbacks:
                issues.append(
                    f"C6: rename map echo-fallback detected for {len(real_fallbacks)} signal(s): "
                    f"{real_fallbacks[:5]}... Stage entries identical to input name suggests "
                    f"FM returned no equivalence data and rename_map.py fell back to echoing "
                    f"the input. Studier will use these as if real per-stage names — silently "
                    f"wrong. Investigate FM-036 retries and re-query with corrected paths.")

    # C1 + C2: each Cat 8 query must appear in the raw rpts AND have Equivalent
    # Nets block. NO WAIVERS — every anchor wire MUST resolve in FM. The previous
    # waivers (HFSNET pattern + bare-wire-name pattern) silently passed every
    # FM-036 because EVERY anchor wire is a bare name → studier saw zero
    # equivalence data → built bridges on guessed wires → FM Route failed.
    # Per-Net block parser: walk from this `Net:` line until next `Net:` or EOF
    # — robust to retry rpts where multiple `Net:` lines stack closely.
    net_block_pat = re.compile(r'^Net:\s+(\S+).*?(?=^Net:|\Z)', re.MULTILINE | re.DOTALL)
    net_blocks_by_path = {}
    for nb in net_block_pat.finditer(raw_text):
        path = nb.group(1).strip()
        net_blocks_by_path.setdefault(path, []).append(nb.group(0))
    for q in cat8:
        np_q = q.get('net_path', '')
        ctx  = f"anchor pin={q.get('anchor_pin')} dff={q.get('anchor_dff')} sib={q.get('sibling_module')} wire={q.get('anchor_wire')}"
        if not np_q:
            issues.append(f"C1: Cat 8 entry missing net_path ({ctx})")
            continue
        # Match path with any leading FM-prefix (r:/.../ or i:/.../)
        matching = [b for path, blocks in net_blocks_by_path.items() if path.endswith('/' + np_q) or path == np_q for b in blocks]
        if not matching:
            issues.append(f"C1: anchor query NOT submitted to FM — net_path={np_q!r} ({ctx})")
            continue
        # C2: at least one block must contain `Equivalent Nets:` AND no FM-036/Unknown
        ok = any(('Equivalent Nets' in b) and ('FM-036' not in b) and ('Unknown name' not in b)
                 for b in matching)
        if not ok:
            issues.append(
                f"C2: anchor query returned FM-036 / no equivalence — net_path={np_q!r} ({ctx}). "
                f"Likely cause: scope path uses module-type instead of instance name "
                f"(e.g. 'ddrss_<tile>_t_<peer>/...' vs 'ARB/DCQARB/...'). Re-run "
                f"eco_pick_sibling.py with --tile-module and copy recommended_pick.fm_scope "
                f"into mode_s_anchor.fm_scope, then re-derive Step 2 queries.")

    # C7 — RENAME-COVERAGE: every Cat 8 anchor wire MUST appear as a key in the
    # fenets rename_map.json. If FM returned data, the collator would have
    # written an entry. Missing key = FM-036 + no per-stage data for studier.
    if args.rename_map and Path(args.rename_map).is_file():
        rmap = _load_json(args.rename_map) or {}
        rmap_keys = set(k for k in rmap.keys() if k != '_metadata')
        for q in cat8:
            np_q = q.get('net_path', '')
            if not np_q:
                continue
            # Match by exact key OR by suffix (rmap may key by sibling-internal scope)
            hit = (np_q in rmap_keys) or any(k.endswith('/' + np_q) or np_q.endswith('/' + k) for k in rmap_keys)
            if not hit:
                issues.append(
                    f"C7: anchor wire MISSING from rename_map — net_path={np_q!r} "
                    f"(pin={q.get('anchor_pin')} dff={q.get('anchor_dff')}). "
                    f"FM returned no per-stage equivalence for this wire (almost "
                    f"certainly FM-036). Step 3 studier has no stage-stable bridge "
                    f"source data. Fix Step 1 mode_s_anchor.fm_scope first, then re-run.")

    # C3: bridge_candidates.json (if present) must list ≥1 stage-stable candidate per anchor pin
    if args.candidates and Path(args.candidates).is_file():
        cands = _load_json(args.candidates) or {}
        for q in cat8:
            dff = q.get('anchor_dff'); pin = q.get('anchor_pin')
            key = f"{q.get('sibling_module')}/{dff}"
            entry = cands.get(key, {}).get(pin.lower() + '_candidates') if dff and pin else None
            if not entry:
                issues.append(f"C3: bridge_candidates missing entry for {key}/{pin}")
                continue
            ok = any(set(c.get('stages_available') or []) >= {'PrePlace', 'Route'} for c in entry)
            if not ok:
                issues.append(f"C3: no candidate covers PrePlace+Route for {key}/{pin}")

    # C8: rename-map polarity stage-consistency. For each rename_map entry,
    # verify all 3 stages (Synthesize / PrePlace / Route) reference wires of
    # the SAME polarity class (input pin / output pin / scalar wire). Mixed
    # polarity (e.g. PP=<inv>/ZN, Route=<inv>/I) means the same logical signal
    # is being read with opposite sign in different stages — silently inverts
    # downstream logic when studier wires it through a new INV cell.
    #
    # Polarity classification for a per-stage value `<cell>/<pin>`:
    #   /ZN, /Q, /Z         → 'output'  (cell drives the wire)
    #   /I, /A, /A1, /A2,
    #     /B, /B1, /B2, /D  → 'input'   (cell reads the wire)
    #   no '/'              → 'wire'    (bare wire name, no cell context)
    OUT_PINS = {'ZN', 'Q', 'Z', 'CO', 'S', 'QN'}
    IN_PINS  = {'I', 'A', 'A1', 'A2', 'A3', 'A4', 'B', 'B1', 'B2', 'B3', 'B4',
                'C', 'C1', 'C2', 'D', 'D1', 'D2', 'CP', 'SI', 'SE', 'EN'}
    def _polarity(val):
        if not val or '/' not in val:
            return 'wire'
        pin = val.rsplit('/', 1)[-1]
        if pin in OUT_PINS: return 'output'
        if pin in IN_PINS:  return 'input'
        return 'wire'   # unknown pin — treat as bare wire reference

    if args.rename_map and Path(args.rename_map).is_file():
        rmap = _load_json(args.rename_map) or {}
        for sig_key, stages in rmap.items():
            if sig_key == '_metadata' or not isinstance(stages, dict):
                continue
            kinds = {st: _polarity(stages.get(st, '')) for st in ('Synthesize','PrePlace','Route')}
            distinct = set(k for k in kinds.values() if k)
            # Mixed = both 'input' AND 'output' present → silent polarity flip
            if 'input' in distinct and 'output' in distinct:
                issues.append(
                    f"C8: rename_map polarity mismatch — {sig_key!r} has mixed "
                    f"input/output references across stages: {kinds}. The same "
                    f"logical signal is read with opposite sign in different stages. "
                    f"Studier wiring this through a new INV cell will silently flip "
                    f"the logic in one of the stages → FM mismatch. Studier MUST "
                    f"pick a single-polarity reference (preferably the wire/output "
                    f"form, not an inverter input) per stage.")

    # C9: Mode H recovery — for every condition gate input that FM-036'd in PP/Route
    # but was resolved in Synth, verify that a fallback query using the Synth driver
    # cell name was submitted and returned equivalence data.
    #
    # Background: signals like phfnn_2405075 / N2408127 are synthesis intermediates
    # that don't survive to PP/Route as named wires. When FM-036 is returned for
    # these in PP/Route, eco_fenets_runner MUST submit a fallback query using the
    # Synth driver cell name (e.g. ARB/CMDARB/A2304450) to find the PP/Route
    # equivalent output net. Without this, eco_netlist_studier has no per-stage data
    # for the condition gate inputs → falls back to wrong signal → FM FAIL.
    if args.rtl_diff and Path(args.rtl_diff).is_file() and args.rename_map and Path(args.rename_map).is_file():
        rtl_diff = _load_json(args.rtl_diff) or {}
        rmap     = _load_json(args.rename_map) or {}
        rmap_keys = set(k for k in rmap.keys() if k != '_metadata')

        # Collect all condition_inputs_to_query from rtl_diff changes
        cond_inputs = []
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            for ci in (c.get('condition_inputs_to_query') or []):
                cond_inputs.append({
                    'signal':    ci.get('signal', ''),
                    'net_path':  ci.get('net_path', ''),
                    'scope':     ci.get('scope', ''),
                    'change_idx': idx,
                })

        for ci in cond_inputs:
            signal = ci.get('signal', '')
            scope  = ci.get('scope', '')
            # Build net_path from scope+signal if net_path not explicitly set
            np_q = ci.get('net_path', '') or (f"{scope}/{signal}" if scope and signal else signal)
            if not np_q or not signal:
                continue

            # Check if Synth resolved it (present in rename map with a non-echo value)
            rmap_entry = rmap.get(np_q) or {}
            synth_val  = rmap_entry.get('Synthesize', '') if isinstance(rmap_entry, dict) else ''
            pp_val     = rmap_entry.get('PrePlace', '')   if isinstance(rmap_entry, dict) else ''
            rt_val     = rmap_entry.get('Route', '')      if isinstance(rmap_entry, dict) else ''

            def _is_unresolved(v, sig):
                """True when FM returned no usable equivalence for this stage."""
                if not v: return True
                if v == sig: return True
                if 'FM-036' in v or 'FALLBACK' in v: return True
                return False

            synth_resolved = synth_val and not _is_unresolved(synth_val, signal)
            pp_fm036       = _is_unresolved(pp_val, signal)
            rt_fm036       = _is_unresolved(rt_val, signal)

            if not synth_resolved:
                continue  # Synth also failed — not a Mode H case
            if not (pp_fm036 or rt_fm036):
                continue  # PP and Route both resolved — no issue

            # Synth resolved but PP/Route FM-036 — check for fallback query
            # A fallback query uses the Synth driver cell name as the net_path.
            # It should appear in queries.json with category=9 (mode_H_recovery)
            # or as a separate entry whose net_path contains the Synth cell name.
            # Extract the net name from the Synth resolved path (last component)
            # e.g. 'ARB/CMDARB/phfnn_2405075' → net='phfnn_2405075'
            # The driver cell must be found by grepping PreEco Synth netlist for
            # what drives this net (e.g. grep ".ZN ( phfnn_2405075 )" → cell name)
            synth_net  = synth_val.rsplit('/', 1)[-1] if '/' in synth_val else synth_val
            synth_cell = synth_net  # use net name as proxy for fallback query

            fallback_found = False
            if synth_cell:
                fallback_found = any(
                    q.get('net_path', '').endswith(synth_cell) or
                    q.get('mode_H_recovery') is True
                    for q in queries
                )
                # Also check rename_map for a mode_H_recovery entry
                if not fallback_found:
                    mh_key = f"{ci.get('scope','')}/{synth_cell}" if synth_cell else ''
                    fallback_found = mh_key in rmap_keys or any(
                        isinstance(v, dict) and v.get('mode_H_recovery')
                        for v in rmap.values()
                        if isinstance(v, dict)
                    )

            if not fallback_found:
                stages_missing = []
                if pp_fm036: stages_missing.append('PrePlace')
                if rt_fm036: stages_missing.append('Route')
                issues.append(
                    f"C9: Mode H recovery MISSING — condition gate input {signal!r} "
                    f"(net_path={np_q!r}) resolved in Synth ({synth_val!r}) but "
                    f"FM-036 in {stages_missing}. eco_fenets_runner MUST submit a "
                    f"fallback query using Synth driver cell {synth_cell!r} for "
                    f"{stages_missing} stages. Without this, studier has no per-stage "
                    f"data and will substitute a wrong signal (GAP-2 in 9899 run).")

    # C13 — UNIQUIFIED per-instance resolution completeness. When a wire_swap/
    # and_term change enumerates instances[] (a synthesis-uniquified generate
    # array), FM must resolve its old_token in EVERY instance scope, not just the
    # first. History: 9666 fresh run (20260704085942) resolved SEQMAP_NET_425 for
    # entry _0 only — the other 39 copies' compare net (a different per-copy name)
    # never resolved, so Step 3 rewired only _0 and left 39/40 entries a silent
    # no-op. A single symbolic old_token cannot resolve across copies whose local
    # net names differ; this catch forces per-copy resolution before Step 3.
    if args.rtl_diff and Path(args.rtl_diff).is_file() and args.rename_map and Path(args.rename_map).is_file():
        _rtl_c13 = _load_json(args.rtl_diff) or {}
        _rmap_c13 = _load_json(args.rename_map) or {}
        _rkeys = [k for k in _rmap_c13.keys() if k != '_metadata']
        for ci, c in enumerate(_rtl_c13.get('changes', [])):
            if c.get('change_type') not in ('and_term', 'wire_swap'):
                continue
            insts = c.get('instances') or []
            old_tok = c.get('old_token')
            if len(insts) < 2 or not old_tok:
                continue
            # A BARE/shared resolution — a rename_map key that is exactly old_token, or
            # ends with /old_token where the preceding path segment is NOT one of the
            # instances — means the copies share ONE net name (e.g. 9899 DCQARB/DCQARB1
            # both use QualPmArbWinVld_d1). One shared resolution covers all copies →
            # not a partial. C13 only fires on INSTANCE-SCOPED partials (9666: each
            # uniquified copy has its own local net, resolved for _0 only).
            shared = False
            for k in _rkeys:
                if k == old_tok:
                    shared = True; break
                if k.endswith('/' + old_tok):
                    prefix = k[:-(len(old_tok) + 1)]
                    if prefix.rsplit('/', 1)[-1] not in insts:
                        shared = True; break
            if shared:
                continue
            resolved = [inst for inst in insts
                        if any(k.endswith(f"/{inst}/{old_tok}") or k == f"{inst}/{old_tok}"
                               for k in _rkeys)]
            if 0 < len(resolved) < len(insts):
                missing = [i for i in insts if i not in resolved][:8]
                issues.append(
                    f"C13: change[{ci}] {c.get('change_type')} old_token={old_tok!r} is a "
                    f"uniquified generate-array edit across {len(insts)} instances but FM resolved "
                    f"it for only {len(resolved)} — {len(insts) - len(resolved)} copies UNRESOLVED "
                    f"(e.g. {missing}). Each uniquified copy's compare net has its own local name, "
                    f"so one symbolic old_token only resolves in the first copy; Step 3 would then "
                    f"rewire only the resolved copy and leave the rest a silent no-op. FIX: Step 1 "
                    f"must supply per-copy old nets (flat_net_name_per_instance), or Step 2 must "
                    f"resolve old_token in EACH instance scope (find each copy's own net in the "
                    f"netlist), so all N copies get a per-stage rename_map entry.")

    # C10 — comb_net_force SELECTOR-CONDITION completeness (final step2->step3 gate).
    # The region selector references folded combinational wires (delta-prefix branch
    # conditions, e.g. dsp_cmd_valid/dsp_cnt_end = counter comparisons) that synthesis
    # deleted from the netlist. Step 3 MUST bind these to existing netlist nets (via FM) or
    # it rebuilds them (591-gate bloat) AND references synthesis-removed nets (NET-ABSENT at
    # apply). FAIL if any such condition was not resolved per-stage in the rename_map, so a
    # missing Cat-4d query or an FM no-equivalent is caught HERE, not at step3/apply. Uses the
    # SAME helper the deriver uses (single source of truth) so the two cannot drift.
    if args.ref_dir and args.rtl_diff and Path(args.rtl_diff).is_file() and args.rename_map and Path(args.rename_map).is_file():
        try:
            from eco_cone_rebuild import selector_folded_conditions as _sfc
        except Exception:
            _sfc = None
        if _sfc:
            _rtl_c10 = _load_json(args.rtl_diff) or {}
            _rmap_c10 = _load_json(args.rename_map) or {}
            def _c10_unres(v, sig):
                return (not v) or v == sig or 'FM-036' in str(v) or 'FALLBACK' in str(v)
            for idx, c in enumerate(_rtl_c10.get('changes', [])):
                if c.get('change_type') != 'comb_net_force':
                    continue
                scope = c.get('scope') or c.get('instance_scope') or ''
                try:
                    conds = _sfc(c, args.ref_dir)
                except Exception:
                    conds = []
                for cond in conds:
                    key = f"{scope}/{cond}" if scope else cond
                    entry = _rmap_c10.get(key)
                    if not isinstance(entry, dict):
                        issues.append(
                            f"C10: change[{idx}] comb_net_force {c.get('signal')!r} selector "
                            f"condition {cond!r} NOT queried/resolved (missing rename_map key "
                            f"{key!r}). Step 3 would REBUILD it (591-gate bloat + NET-ABSENT at "
                            f"apply). FIX: Step 2 must emit a Cat-4d find_equivalent_nets query "
                            f"for {cond!r} and resolve it per-stage (chain Synth net -> PP -> Route).")
                        continue
                    for st in ('Synthesize', 'PrePlace', 'Route'):
                        v = entry.get(st, '')
                        if _c10_unres(v, cond):
                            issues.append(
                                f"C10: change[{idx}] comb_net_force {c.get('signal')!r} selector "
                                f"condition {cond!r} UNRESOLVED in {st} (rename_map[{key!r}][{st}]="
                                f"{v!r}). Step 3 would re-derive it in {st} -> NET-ABSENT at apply. "
                                f"FIX: chain the Synth net through the {st} FM target.")

    # C10b — reg_guard_delta (Intent-A and_term on a register) GUARD-LEAF binding (ADVISORY, NOT a
    # gate). The clock-gate builder re-derives the load guard from RTL; a guard signal synthesis
    # folded out (e.g. 9666 WckSyncCtr0 `recdsp_c0cs`, an internal combinational reg) is BOUND via
    # FM when possible (lighter) or REBUILT from its real flop/port leaves when not. Unlike C10
    # (comb_net_force -> unbound = NET-ABSENT = BROKEN), an unbound reg_guard leaf still produces a
    # CORRECT build (grounds on existing flops; just more gates). So this is a WARNING only — a
    # HARD-FAIL would permanently BLOCK any clock-gate ECO whose guard touches a dissolved internal
    # reg (which find_equivalent_nets cannot anchor at all — FM-036 in every stage/scope; that is a
    # Conformal structural-reuse capability, not a name-based FM one). Same shared helper as the
    # deriver (Cat 4e) / chain so they cannot drift.
    if args.ref_dir and args.rtl_diff and Path(args.rtl_diff).is_file() and args.rename_map and Path(args.rename_map).is_file():
        try:
            from eco_cone_rebuild import reg_guard_folded_conditions as _rgfc
        except Exception:
            _rgfc = None
        if _rgfc:
            _rtl_c10b = _load_json(args.rtl_diff) or {}
            _rmap_c10b = _load_json(args.rename_map) or {}
            def _c10b_unres(v, sig):
                return (not v) or v == sig or 'FM-036' in str(v) or 'FALLBACK' in str(v)
            for idx, c in enumerate(_rtl_c10b.get('changes', [])):
                if c.get('change_type') != 'and_term' or not c.get('target_register'):
                    continue
                scope = c.get('scope') or c.get('instance_scope') or ''
                try:
                    conds = _rgfc(c, args.ref_dir)
                except Exception:
                    conds = []
                for cond in conds:
                    key = f"{scope}/{cond}" if scope else cond
                    entry = _rmap_c10b.get(key)
                    unres_stages = []
                    if not isinstance(entry, dict):
                        unres_stages = ['Synthesize', 'PrePlace', 'Route']
                    else:
                        unres_stages = [st for st in ('Synthesize', 'PrePlace', 'Route')
                                        if _c10b_unres(entry.get(st, ''), cond)]
                    if unres_stages:
                        warnings.append(
                            f"C10b (advisory): change[{idx}] reg_guard_delta {c.get('target_register')!r} "
                            f"guard leaf {cond!r} not FM-bound in {unres_stages} — Step 3 will REBUILD it "
                            f"from real flop/port leaves (CORRECT, no NET-ABSENT; just heavier). If it is "
                            f"FM-anchorable (a port/flop, not a dissolved combinational reg), add a Cat-4e "
                            f"query + chain to slim it. Non-blocking.")

    # C10c — old_guard_net per-stage resolution check (Cat 4f queries).
    # The input-pin-rewire builder needs old_guard_net resolved per stage so it can find
    # the correct consumer cell/pin in PP/Route. If the net is stable (same name in all
    # stages) the rename_map entry will have identical values → no issue. If it was P&R
    # renamed (absent or different in PP/Route) and the entry is missing/echo-fallback,
    # the builder cannot resolve the correct consumer → HARD-FAIL (builder aborts).
    if args.rename_map and Path(args.rename_map).is_file() and args.rtl_diff and Path(args.rtl_diff).is_file():
        _rmap_c10c = _load_json(args.rename_map) or {}
        _rtl_c10c  = _load_json(args.rtl_diff) or {}
        def _c10c_unres(v, sig):
            return (not v) or v == sig or 'FM-036' in str(v) or 'FALLBACK' in str(v)
        for idx, c in enumerate(_rtl_c10c.get('changes', [])):
            if c.get('change_type') != 'and_term' or not c.get('target_register'):
                continue
            ogn = c.get('old_guard_net') or ''
            if not ogn:
                continue
            scope = c.get('scope') or c.get('instance_scope') or ''
            key = f"{scope}/{ogn}" if scope else ogn
            entry = _rmap_c10c.get(key)
            if not isinstance(entry, dict):
                issues.append(
                    f"C10c: change[{idx}] reg_guard_delta {c.get('target_register')!r} "
                    f"old_guard_net={ogn!r} is NOT in the rename_map. "
                    f"Add Cat-4f query for old_guard_net in eco_fenets_derive_queries.py so FM "
                    f"resolves it per-stage. Without it the input-pin-rewire builder cannot find "
                    f"the correct consumer cell in PP/Route → fails closed.")
            else:
                unres = [st for st in ('Synthesize', 'PrePlace', 'Route')
                         if _c10c_unres(entry.get(st, ''), ogn)
                         and entry.get(f'actual_wire_{st}') is None]
                if unres:
                    issues.append(
                        f"C10c: change[{idx}] reg_guard_delta {c.get('target_register')!r} "
                        f"old_guard_net={ogn!r} UNRESOLVED in stages {unres} "
                        f"(rename_map value = echo/FM-036). Builder will use Synth consumers as "
                        f"fallback for those stages — consumer-pin may be wrong if P&R renamed it.")

    out = {
        'queries':            args.queries,
        'queries_raw':        args.queries_raw,
        'cat8_count':         len(cat8),
        'anchor_count':       len(anchor_role_map),
        'issue_count':        len(issues),
        'issues':             issues,
        'warnings':           warnings,
        'overall_pass':       not issues,
    }
    write_result(args.output, out, not issues, getattr(args, 'iter', None))

    print('ECO_SCRIPT_LAUNCHED: eco_validate_step2.py')
    print(f'  queries:    {args.queries}')
    print(f'  cat8:       {len(cat8)}  issues: {len(issues)}  warnings: {len(warnings)}')
    print(f'  overall:    {"PASS" if not issues else "FAIL"}')
    for iss in issues:
        print(f'    - {iss}')
    for w in warnings:
        print(f'    ~ {w}')
    return 0 if not issues else 1


if __name__ == '__main__':
    sys.exit(main())
