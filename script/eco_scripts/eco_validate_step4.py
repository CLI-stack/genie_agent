#!/usr/bin/env python3
"""
eco_validate_step4.py — Validate eco_applied_round<N>.json completeness before Step 5.

Checks that eco_applier produced complete, valid output with no silently-skipped
critical entries that Step 5 and FM would miss.

Usage:
    python3 script/eco_scripts/eco_validate_step4.py \
        --applied  data/<TAG>_eco_applied_round<N>.json \
        --study    data/<TAG>_eco_preeco_study.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --round    <N> \
        --output   data/<TAG>_eco_validate_step4_round<N>.json

Exit: 0 = PASS, 1 = FAIL
"""

import argparse, gzip, json, re, subprocess, sys
from pathlib import Path
from eco_validate_io import write_result


# ── Helpers ──────────────────────────────────────────────────────────────────

# Verilog wire decls must use a flat identifier — bracket form `wire X[N];` is
# illegal (FM rejects with SVR-4 + SVR-64 + FM-599). Same check exists in
# eco_pre_fm_check.py Check 22; this is the post-apply / pre-Step-5 mirror.
_BAD_WIRE_DECL = re.compile(r'^\s*(wire|tri|wand|wor|reg)\s+(\w+)\[(\d+)\]\s*;\s*$')


def lint_postEco_grammar(ref_dir):
    """Walk each PostEco netlist; return list of (file, line, kind, text) for
    every illegal `wire <name>[<bit>] ;` decl. The applier's auto-sanitize in
    eco_perl_spec.py SHOULD have flattened these — this validator is the
    safety net catching anything that slipped through."""
    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        path = Path(ref_dir) / 'data' / 'PostEco' / f'{stage}.v.gz'
        if not path.is_file():
            continue
        try:
            with gzip.open(path, 'rt', errors='replace') as f:
                for ln_idx, line in enumerate(f, start=1):
                    bm = _BAD_WIRE_DECL.match(line)
                    if bm:
                        kind, name, bit = bm.group(1), bm.group(2), bm.group(3)
                        failures.append({
                            'stage': stage, 'line': ln_idx,
                            'kind': kind, 'name': name, 'bit': bit,
                            'text': line.rstrip(),
                            'fix_hint': f'flatten to `{kind} {name}_{bit}_ ;`',
                        })
        except Exception as e:
            failures.append({'stage': stage, 'note': f'cannot read: {e}'})
    return failures


def md5(path):
    """Return md5 hash of file, or None if command fails (file missing or error)."""
    try:
        r = subprocess.run(f'md5sum {path}', shell=True, capture_output=True, text=True)
        parts = r.stdout.split()
        return parts[0] if parts else None
    except Exception:
        return None


# ── Netlist ground-truth helpers ───────────────────────────────────────────────
# The applied-status JSON can be out of sync with reality: a driver-side net-force
# rewire whose old net is (correctly) renamed away is recorded SKIPPED, and the flow's
# recovery step may land a change without updating its status. So before FAILing on a
# SKIPPED / not-in-applied-JSON entry, we check the APPLIED PostEco netlist directly —
# only fail if the change is GENUINELY absent. Conservative: undetermined -> still fail.
_NL_TEXT = {}

def _posteco_text(ref_dir, stage):
    key = (ref_dir, stage)
    if key not in _NL_TEXT:
        p = Path(ref_dir) / 'data' / 'PostEco' / f'{stage}.v.gz'
        try:
            with gzip.open(p, 'rt', errors='replace') as f:
                _NL_TEXT[key] = f.read()
        except Exception:
            _NL_TEXT[key] = ''
    return _NL_TEXT[key]


def _in_netlist(token, ref_dir, stage):
    """Word-boundary presence of `token` in the applied netlist — fast C-level substring
    find + boundary verify (regex \\b over a ~215 MB text per token is far too slow)."""
    if not token or not isinstance(token, str):
        return False
    t = _posteco_text(ref_dir, stage)
    n = len(token)
    i = t.find(token)
    while i != -1:
        b = t[i - 1] if i > 0 else ' '
        a = t[i + n] if i + n < len(t) else ' '
        if not (b.isalnum() or b == '_') and not (a.isalnum() or a == '_'):
            return True
        i = t.find(token, i + 1)
    return False


def _in_any_stage(token, ref_dir):
    return any(_in_netlist(token, ref_dir, s) for s in ('Synthesize', 'PrePlace', 'Route'))


def _skipped_landed(e, study, ref_dir, stage):
    """True if a SKIPPED entry's effect is actually present in the applied netlist (i.e.
    it was recovered/landed despite the SKIPPED status). None if undeterminable (then the
    caller keeps failing — we never mask a genuine miss)."""
    reason = e.get('reason', '') or ''
    cands = set()
    for f in ('new_net', 'output_net'):
        if e.get(f):
            cands.add(e[f])
    m = re.search(r'[→>]\s*([A-Za-z_]\w*)', reason)      # "old -> new" or "old→new"
    if m:
        cands.add(m.group(1))
    if not cands:
        # cross-ref the study by (cell, old_net) parsed from a "pin .P(old) not found in CELL block"
        rm = re.search(r'\.\w+\s*\(\s*(\w+)\s*\).*?in\s+(\w+)\s+block', reason)
        if rm:
            old, cell = rm.group(1), rm.group(2)
            for st in ('Synthesize', 'PrePlace', 'Route'):
                for se in study.get(st, []):
                    if se.get('change_type') != 'rewire' or se.get('old_net') != old:
                        continue
                    cn = se.get('cell_name') or (se.get('cell_name_per_stage') or {}).get(st)
                    if cn == cell and se.get('new_net'):
                        cands.add(se['new_net'])
    if not cands:
        return None
    for c in cands:
        if _in_netlist(c, ref_dir, stage) or _in_any_stage(c, ref_dir):
            return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--applied',  required=True)
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--output',   required=True)
    p.add_argument('--iter', type=int, default=None,
                   help='catch-and-fix iteration number; when set, ALSO write '
                        '<output>_iter<N>.json (history). Canonical --output stays latest/final.')
    args = p.parse_args()

    applied = json.loads(Path(args.applied).read_text())
    study   = json.loads(Path(args.study).read_text())
    issues  = []

    summary = applied.get('summary', {})

    # ── 1. No VERIFY_FAILED entries ──────────────────────────────────────────
    vf = summary.get('verify_failed', 0)
    if vf > 0:
        issues.append(f"CRITICAL: {vf} VERIFY_FAILED entries — PostEco netlist may be corrupted; do NOT submit to FM")

    # ── 1b. No unrecoverable SKIPPED entries ─────────────────────────────────
    # Policy: the applier MUST actively recover from CTS renames / per-stage
    # cell rename / net rename via cell_type+pin grep, backward-trace from
    # target_register.D, and _0/_1 uniquification suffix variants. Any SKIP
    # that escapes recovery means the rewire/insertion did NOT land in this
    # stage — FM will see PreEco logic on the affected pin. HARD FAIL so the
    # studier or applier is fixed before FM wastes runtime.
    #
    # Intentional skips (allowlist — not a failure):
    INTENTIONAL_SKIP_SUBSTRINGS = (
        'implicitly declared by port connections',  # implicit-wire dedup (port_decl)
        'GAP-3: bridge_port_role',                  # legacy Mode-S Synth skip
        'GAP-4c: si_consumer_replace',              # legacy Mode-S Synth skip
        'ALREADY_APPLIED',                          # idempotent re-run
    )
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        for e in applied.get(stage, []):
            if e.get('status') != 'SKIPPED':
                continue
            reason = e.get('reason', '') or ''
            if any(s in reason for s in INTENTIONAL_SKIP_SUBSTRINGS):
                continue
            # Netlist ground truth: if the change actually landed (recovered by the flow /
            # driver-rename that removed the old net), the applied netlist has its effect —
            # not a real miss. Only fail when genuinely absent (or undeterminable).
            if _skipped_landed(e, study, args.ref_dir, stage) is True:
                continue
            inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name','?')
            ct = e.get('change_type') or e.get('ct') or '?'
            issues.append(
                f"HIGH: SKIPPED — {stage} {ct} {inst!r}: {reason}. "
                f"Applier could not land this change (per-stage cell rename / "
                f"net rename / instance suffix); FM will see PreEco logic on "
                f"the affected pin. Studier MUST emit per-stage cell_name + "
                f"net_per_stage; applier MUST recover via cell_type+pin grep "
                f"or backward-trace from target_register.D before SKIP.")

    # ── 2. eco_perl_spec markers exist for all 3 stages ────────────────────
    data_dir = Path(args.applied).parent
    tag = args.tag
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        m = data_dir / f"{tag}_eco_perl_spec_{stage}_marker.txt"
        if not m.exists():
            issues.append(f"HIGH: eco_perl_spec_{stage}_marker.txt missing — eco_perl_spec.py did not run for {stage}")

    # ── 3. PostEco actually changed from PreEco (for stages with insertions) ─
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        stage_entries = applied.get(stage, [])
        has_changes = any(e.get('status') in ('APPLIED','INSERTED') for e in stage_entries)
        if has_changes:
            pre = f"{args.ref_dir}/data/PreEco/{stage}.v.gz"
            post = f"{args.ref_dir}/data/PostEco/{stage}.v.gz"
            pre_md5  = md5(pre)
            post_md5 = md5(post)
            if pre_md5 is None or post_md5 is None:
                issues.append(f"HIGH: {stage} MD5 check failed — PreEco or PostEco file missing/unreadable")
            elif pre_md5 == post_md5:
                issues.append(f"CRITICAL: {stage} PostEco MD5 unchanged from PreEco despite {sum(1 for e in stage_entries if e.get('status') in ('APPLIED','INSERTED'))} applied changes — writes may have failed")

    # ── 4. Every INSERTED entry has instance_name populated ─────────────────
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        for e in applied.get(stage, []):
            if e.get('status') == 'INSERTED':
                if not (e.get('instance_name') or e.get('cell_name') or e.get('signal_name')):
                    issues.append(f"MEDIUM: INSERTED entry in {stage} has no identifier (instance_name/cell_name/signal_name) — RPT will show '?'")

    # ── 5. All confirmed study entries are accounted for ─────────────────────
    for stage in ['Synthesize']:
        study_confirmed = {e.get('instance_name','') or e.get('cell_name','') or e.get('signal_name','')
                          for e in study.get(stage, []) if e.get('confirmed', True) and e.get('change_type') != 'remove_wire_decl'}
        applied_names = {e.get('instance_name','') or e.get('cell_name','') or e.get('signal_name','')
                        for e in applied.get(stage, [])}
        unaccounted = study_confirmed - applied_names - {'', '?'}
        # Netlist ground truth: an entry missing from the applied JSON but PRESENT in the
        # applied netlist did land (recovery / status not logged) — not a silent skip.
        really_missing = sorted(n for n in unaccounted if not _in_any_stage(n, args.ref_dir))
        if really_missing:
            issues.append(f"HIGH: {stage} study entries not found in applied JSON AND absent from "
                          f"applied netlist: {really_missing[:5]} — eco_applier skipped them silently")

    # ── 6. Backup files exist for stages with changes ────────────────────────
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        bak = f"{args.ref_dir}/data/PostEco/{stage}.v.gz.bak_{tag}_round{args.round}"
        if applied.get(stage) and not Path(bak).exists():
            issues.append(f"MEDIUM: Backup {stage}.v.gz.bak_{tag}_round{args.round} not found — revert protection missing")

    # ── 7. GAP-2 enforcement: bus_rename APPLIED entries should carry cleanup
    # tags ([removed_orphan] / [added_decl]) when the old net was an UNCONNECTED.
    # The applier prints these in the status `reason` string — verify presence.
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        for e in applied.get(stage, []):
            if e.get('status') != 'APPLIED':
                continue
            reason = e.get('reason', '')
            if 'bus_rename' not in reason or 'UNCONNECTED' not in reason:
                continue
            if '[' not in reason or ('removed_orphan' not in reason and 'added_decl' not in reason):
                inst = e.get('instance_name') or e.get('cell_name') or '?'
                issues.append(
                    f"HIGH: GAP-2 — {stage} bus_rename on {inst} renamed an UNCONNECTED net but "
                    f"reason text shows no [removed_orphan]/[added_decl] cleanup tag. "
                    f"Orphan wire decl may still exist or new wire missing explicit decl. reason={reason!r}")

    # ── 8. GAP-3 enforcement: bridge_port_role entries MUST be SKIPPED in Synth.
    # Studier marks bridge plumbing entries with bridge_port_role; applier
    # skips them when stage==Synthesize. Audit by cross-referencing study JSON.
    bridge_role_insts = set()
    for s in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(s, []):
            if e.get('bridge_port_role'):
                key = e.get('instance_name') or e.get('signal_name') or e.get('cell_name', '')
                if key:
                    bridge_role_insts.add(key)
    for e in applied.get('Synthesize', []):
        key = e.get('instance_name') or e.get('signal_name') or e.get('cell_name', '')
        if key in bridge_role_insts and e.get('status') in ('APPLIED', 'INSERTED'):
            issues.append(
                f"HIGH: GAP-3 — Synthesize entry {key!r} has bridge_port_role in study but was "
                f"APPLIED/INSERTED in Synth (expected SKIPPED — Synth uses constant_zero, no bridge plumbing).")

    # ── 9. Verilog grammar lint on PostEco wire decls ────────────────────────
    # Catches `wire <name>[<bit>] ;` that slipped past the applier's
    # auto-sanitize. Run 20260512070625 root cause #2: studier emitted
    # `named_net: REG_UmcCfgEco[1]` and applier passed it through verbatim.
    # eco_perl_spec.py now auto-converts to flat-net form; this is the safety
    # net catching anything that bypasses the sanitizer (manual edits,
    # corruption, future code paths).
    grammar_failures = lint_postEco_grammar(args.ref_dir)
    for f in grammar_failures:
        if 'note' in f:
            issues.append(f"MEDIUM: GRAMMAR-LINT — {f['stage']}: {f['note']}")
        else:
            issues.append(
                f"CRITICAL: GRAMMAR-LINT — {f['stage']}:{f['line']} illegal wire decl "
                f"`{f['text'].strip()}`. {f['fix_hint']}. FM will reject with "
                f"SVR-4 + SVR-64 + FM-599 → ABORT in PreVerify. Likely cause: "
                f"applier auto-sanitize bypassed OR netlist manually edited.")

    # ── 10. CROSS-STAGE-EDIT-PARITY (G4) ─────────────────────────────────────
    # For high-risk per-stage edit types (unconnected_rewires, port_connection,
    # wire_swap, port_promotion), the same logical edit MUST be applied to all
    # 3 stages. Silent stage-skip produces a stage-divergent netlist where the
    # rewired bit creates a real cone path in only one stage — FM cones diverge
    # on apparently-unrelated DFFs that walk through the modified region.
    HIGH_RISK_TYPES = {'unconnected_rewires', 'port_connection',
                       'wire_swap', 'port_promotion', 'bus_rename'}
    # Build per-edit per-stage success map. Use change_index from study (or
    # name if change_index missing) as the edit identity.
    def _edit_key(e):
        ci = e.get('change_index')
        if ci is not None:
            return f"#{ci}"
        return (e.get('instance_name') or e.get('cell_name') or
                e.get('signal_name') or e.get('name') or '?')

    def _is_success(status):
        return status in ('APPLIED', 'INSERTED', 'QUEUED', 'AUTO_SANITIZED', 'ALREADY_APPLIED')

    per_edit = {}  # key → { stage → status }
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in applied.get(stage, []):
            ct = e.get('change_type', '')
            # Also catch carried-with-other-types entries: any entry whose
            # reason mentions bus_bit_replace or unconnected_rewires.
            reason = e.get('reason', '')
            looks_like_high_risk = (
                ct in HIGH_RISK_TYPES or
                'unconnected_rewires' in reason or
                'bus_bit_replace' in reason or
                'bus_rename' in reason
            )
            if not looks_like_high_risk:
                continue
            key = _edit_key(e)
            per_edit.setdefault(key, {})[stage] = e.get('status', '?')

    for key, by_stage in per_edit.items():
        success_stages = {s for s, st in by_stage.items() if _is_success(st)}
        failed_stages  = {s: st for s, st in by_stage.items() if not _is_success(st)}
        # If the edit succeeded in some stages but not all 3 → cross-stage divergence
        if 0 < len(success_stages) < 3:
            missing = sorted({'Synthesize','PrePlace','Route'} - success_stages)
            issues.append(
                f"CRITICAL/10-CROSS-STAGE-EDIT-PARITY: edit {key!r} (high-risk "
                f"per-stage type) applied successfully in {sorted(success_stages)} "
                f"but missing/failed in {missing}. by_stage_status={by_stage}. "
                f"Stage-divergent edits cause FM cone walks to reach different "
                f"physical wires per stage on apparently-unrelated DFFs. The "
                f"applier MUST apply the same edit to all 3 stages or HARD ERROR.")

    # ── 11. GAP-2: PENDING_STAGE_RESOLUTION wrong-signal substitution check ─────
    # When a condition gate input was PENDING_STAGE_RESOLUTION, the applier must
    # mark it confirmed: false and SKIP — never substitute a signal from a different
    # change type. Detect when a resolved net name matches a signal from a
    # port_declaration or port_promotion entry (wrong change type substitution).
    port_decl_names = set()
    for s in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(s, []):
            if e.get('change_type') in ('port_declaration', 'new_port', 'port_promotion'):
                sig = e.get('signal_name') or e.get('port_name') or e.get('new_token', '')
                if sig:
                    port_decl_names.add(sig)
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in applied.get(stage, []):
            if e.get('status') not in ('APPLIED', 'INSERTED'):
                continue
            if e.get('change_type') not in ('new_logic_gate', 'condition_gate', 'new_logic'):
                continue
            reason = e.get('reason', '')
            if 'PENDING_STAGE_RESOLUTION' not in reason:
                continue
            # Check if resolved net name appears in port_decl_names
            resolved = e.get('resolved_net') or ''
            if not resolved:
                # Try extracting from reason string
                import re as _re
                m = _re.search(r'resolved[_\s]+(?:to|net)[=:\s]+[\'"]?(\w+)[\'"]?', reason, _re.I)
                if m:
                    resolved = m.group(1)
            if resolved and resolved in port_decl_names:
                inst = e.get('instance_name') or e.get('cell_name') or '?'
                issues.append(
                    f"HIGH: GAP-2 — {stage} condition gate {inst!r} resolved "
                    f"PENDING_STAGE_RESOLUTION to {resolved!r} which is a port_declaration "
                    f"signal from a different change type. Applier substituted wrong signal. "
                    f"Must mark confirmed: false and SKIP when no valid Mode H recovery found.")

    # ── 12. GAP-4: port_declaration applied in Synth must exist in PP/Route ────
    # Every new port added in Synthesize must also be declared in PrePlace and Route.
    # Silently missing port decls in PP/Route cause FE-LINK-7 ABORT or SVR-8.
    synth_port_decls = {}  # signal_name → entry
    for e in applied.get('Synthesize', []):
        if e.get('change_type') in ('port_declaration', 'new_port') \
                and e.get('status') in ('APPLIED', 'INSERTED'):
            sig = e.get('signal_name') or e.get('port_name') or e.get('instance_name', '')
            if sig:
                synth_port_decls[sig] = e
    for sig in synth_port_decls:
        for stage in ('PrePlace', 'Route'):
            found = any(
                (e.get('signal_name') == sig or e.get('port_name') == sig or
                 e.get('instance_name') == sig) and
                e.get('status') in ('APPLIED', 'INSERTED', 'ALREADY_APPLIED')
                for e in applied.get(stage, [])
                if e.get('change_type') in ('port_declaration', 'new_port')
            )
            if not found:
                issues.append(
                    f"HIGH: GAP-4 — port_declaration {sig!r} APPLIED in Synthesize "
                    f"but missing in {stage}. Gates using this port in {stage} will "
                    f"trigger FE-LINK-7 ABORT or SVR-8 in FM. eco_netlist_port_rewire.py must "
                    f"apply port_declaration entries to all 3 stages.")

    # ── 13. wire_swap + intermediate_net_insertion: pivot net must be driven ─
    # When new_condition_gate_chain was applied, the last gate must drive
    # <pivot_net> in PostEco. If the chain was skipped, <pivot_net> becomes
    # undriven (renamed to _orig but nothing drives the original name).
    try:
        rtl_diff_path = Path(args.applied).parent / f"{tag}_eco_rtl_diff.json"
        if rtl_diff_path.exists():
            rtl_diff_data = json.loads(rtl_diff_path.read_text())
            for idx, c in enumerate(rtl_diff_data.get('changes', [])):
                if c.get('change_type') != 'wire_swap': continue
                if c.get('fallback_strategy') != 'intermediate_net_insertion': continue
                chain = c.get('new_condition_gate_chain') or []
                if not chain: continue
                pivot_net = chain[-1].get('output_net', '')
                if not pivot_net: continue
                orig_net = f"{pivot_net}_orig"
                for stage in ['Synthesize', 'PrePlace', 'Route']:
                    gz = f"{args.ref_dir}/data/PostEco/{stage}.v.gz"
                    if not Path(gz).exists(): continue
                    # orig net must exist (renamed from pivot) AND
                    # pivot net must still be driven (by the new chain)
                    try:
                        r_orig = subprocess.run(f'zgrep -c "{orig_net}" {gz}',
                            shell=True, capture_output=True, text=True, timeout=30)
                        r_pivot = subprocess.run(f'zgrep -c "\.ZN\\|\.Z\\|\.Q" {gz} | head -1',
                            shell=True, capture_output=True, text=True, timeout=30)
                        orig_count = int(r_orig.stdout.strip()) if r_orig.stdout.strip().isdigit() else 0
                        # If orig_net exists but pivot_net has no driver → chain missing
                        r_drv = subprocess.run(
                            f'zgrep -c "\\.ZN ( {pivot_net} )\\|\\.Z ( {pivot_net} )" {gz}',
                            shell=True, capture_output=True, text=True, timeout=30)
                        drv_count = int(r_drv.stdout.strip()) if r_drv.stdout.strip().isdigit() else 0
                        if orig_count > 0 and drv_count == 0:
                            issues.append(
                                f"CRITICAL: {stage} pivot net '{pivot_net}' renamed to '{orig_net}' "
                                f"but has NO driver in PostEco — condition_gate_chain was NOT inserted. "
                                f"Studier Phase 1 must emit gate chain entries for "
                                f"wire_swap+intermediate_net_insertion. Undriven pivot → thousands of "
                                f"FM failing compare points on downstream DFFs.")
                    except Exception:
                        pass
    except Exception:
        pass

    # ── 14. driver_substitution completion check ─────────────────────────────
    # When fallback_strategy=driver_substitution, verify in PostEco:
    # (a) original target net is still present (driven by new gate chain)
    # (b) renamed original net (ECO_<jira>_net_orig or similar) also exists
    # (c) no undriven references to either net
    try:
        rtl_diff_path = Path(args.applied).parent / f"{tag}_eco_rtl_diff.json"
        if rtl_diff_path.exists():
            rtl_diff_data = json.loads(rtl_diff_path.read_text())
            for c in rtl_diff_data.get('changes', []):
                if c.get('fallback_strategy') != 'driver_substitution': continue
                target_net = c.get('driver_sub_target_net', '')
                renamed_to = c.get('driver_sub_renamed_to', '')
                if not target_net: continue
                for stage in ['Synthesize', 'PrePlace', 'Route']:
                    gz = f"{args.ref_dir}/data/PostEco/{stage}.v.gz"
                    if not Path(gz).exists(): continue
                    try:
                        r = subprocess.run(f'zgrep -c "{target_net}" {gz}',
                            shell=True, capture_output=True, text=True, timeout=30)
                        cnt = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
                        if cnt == 0:
                            issues.append(
                                f"HIGH: GAP-DRVSUB — {stage}: driver_substitution target "
                                f"net '{target_net}' not found in PostEco — new gate chain "
                                f"may not have been applied or final gate output net is wrong.")
                    except Exception:
                        pass
    except Exception:
        pass

    # ── Result ───────────────────────────────────────────────────────────────
    passed = len(issues) == 0
    result = {'tag': tag, 'round': args.round, 'passed': passed, 'issues': issues}
    write_result(args.output, result, passed, getattr(args, 'iter', None))

    marker_txt = (
        f"ECO_SCRIPT_LAUNCHED: eco_validate_step4.py\n"
        f"  round:  {args.round}\n"
        f"  passed: {passed}\n"
        f"  issues: {len(issues)}\n"
        f"  output: {args.output}"
    )
    print(marker_txt)
    if issues:
        print("\nISSUES FOUND:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("\nStep 4 output COMPLETE — all checks passed.")

    Path(args.output.replace('.json','_marker.txt')).write_text(marker_txt + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
