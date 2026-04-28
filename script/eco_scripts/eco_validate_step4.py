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

import argparse, json, subprocess, sys
from pathlib import Path


def md5(path):
    try:
        r = subprocess.run(f'md5sum {path}', shell=True, capture_output=True, text=True)
        return r.stdout.split()[0]
    except:
        return ''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--applied',  required=True)
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--output',   required=True)
    args = p.parse_args()

    applied = json.loads(Path(args.applied).read_text())
    study   = json.loads(Path(args.study).read_text())
    issues  = []

    summary = applied.get('summary', {})

    # ── 1. No VERIFY_FAILED entries ──────────────────────────────────────────
    vf = summary.get('verify_failed', 0)
    if vf > 0:
        issues.append(f"CRITICAL: {vf} VERIFY_FAILED entries — PostEco netlist may be corrupted; do NOT submit to FM")

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
            if md5(pre) == md5(post):
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
        if unaccounted:
            issues.append(f"HIGH: {stage} study entries not found in applied JSON: {list(unaccounted)[:5]} — eco_applier skipped them silently")

    # ── 6. Backup files exist for stages with changes ────────────────────────
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        bak = f"{args.ref_dir}/data/PostEco/{stage}.v.gz.bak_{tag}_round{args.round}"
        if applied.get(stage) and not Path(bak).exists():
            issues.append(f"MEDIUM: Backup {stage}.v.gz.bak_{tag}_round{args.round} not found — revert protection missing")

    # ── Result ───────────────────────────────────────────────────────────────
    passed = len(issues) == 0
    result = {'tag': tag, 'round': args.round, 'passed': passed, 'issues': issues}
    Path(args.output).write_text(json.dumps(result, indent=2))

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
