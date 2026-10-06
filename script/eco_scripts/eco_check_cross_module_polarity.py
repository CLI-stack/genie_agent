#!/usr/bin/env python3
"""
eco_check_cross_module_polarity.py — simple-mode-only structural + rename-map check.

Runs the cross-module primary-input polarity check ("Check 68") against a study
JSON. If Step 2 (fenets) ran and `--rename-map` is given, that is the GOLDEN
REFERENCE and is checked FIRST for every candidate net — structural tracing is
only a fallback for nets the rename map doesn't cover (or when no rename map
exists at all, simple mode's default). Running it is MANDATORY in simple mode's
flow; its findings are advisory, NEVER a hard gate — but the discipline required
to investigate them differs: a rename-map-sourced verdict is real Formality
equivalence data (reliable); a structural-only verdict can mis-anchor to the
wrong one of several equivalent registers in a design and should be treated with
more caution (confirmed on a real design).

This does NOT exist in complete mode's eco_validate_step3.py, and deliberately
so: complete mode's fenets rename map + studier already resolve this class of
issue via the same golden-reference-first priority this script now also applies.

Usage:
  python3 eco_check_cross_module_polarity.py \\
      --study <TAG>_eco_preeco_study.json --ref-dir <REF_DIR> --tag <TAG> \\
      --output <AI_ECO_FLOW_DIR>/<TAG>_eco_cross_module_polarity.json \\
      [--rename-map <AI_ECO_FLOW_DIR>/<TAG>_eco_fenets_rename_map.json]
"""
import argparse
import json
import sys
from pathlib import Path

from eco_structural_polarity import check_cross_module_polarity
from eco_validate_io import write_result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study', required=True)
    p.add_argument('--ref-dir', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--rename-map', default=None,
                    help='fenets rename map JSON, if Step 2 ran — checked FIRST, '
                         'before structural tracing, for every candidate net')
    p.add_argument('--iter', type=int, default=None)
    args = p.parse_args()

    study = json.loads(Path(args.study).read_text())
    rename_map = {}
    if args.rename_map and Path(args.rename_map).is_file():
        rename_map = json.loads(Path(args.rename_map).read_text())
    issues = check_cross_module_polarity(study, args.ref_dir, rename_map=rename_map)

    review = [i for i in issues if i.startswith('REVIEW/')]
    advisory = [i for i in issues if i.startswith('ADVISORY/')]
    result = {
        'tag': args.tag,
        'passed': True,   # advisory-only — this script never gates the flow
        'issues': issues,
        'issue_count': len(issues),
        'review_count': len(review),
        'advisory_count': len(advisory),
    }
    write_result(args.output, result, True, args.iter)

    print(f"ECO_SCRIPT_LAUNCHED: eco_check_cross_module_polarity.py (advisory only, never blocking)")
    print(f"  issues: {len(issues)} ({len(review)} REVIEW, {len(advisory)} ADVISORY)")
    print(f"  output: {args.output}")
    if issues:
        print("\nFINDINGS (review, not proof of a bug):")
        for i in issues:
            print(f"  - {i}")
    else:
        print("\nNo cross-module polarity issues found.")
    return 0   # advisory only — always exit 0, never blocks the caller


if __name__ == '__main__':
    sys.exit(main())
