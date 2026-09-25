#!/usr/bin/env python3
"""
eco_check_cross_module_polarity.py — simple-mode standalone structural check.

Runs ONLY the cross-module primary-input polarity check (the same "Check 68" logic
eco_validate_step3.py runs as one of ~40 checks in complete mode) against a study
JSON, with NO Formality/fenets dependency and none of complete mode's other checks.
Simple mode must never run the full eco_validate_step3.py validator (most of its
checks assume fenets rename-map data simple mode doesn't produce) — this script is
the lightweight, self-contained substitute for the one check that actually matters
without FM data.

Usage:
  python3 eco_check_cross_module_polarity.py \\
      --study <TAG>_eco_preeco_study.json --ref-dir <REF_DIR> --tag <TAG> \\
      --output <AI_ECO_FLOW_DIR>/<TAG>_eco_cross_module_polarity.json
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
    p.add_argument('--iter', type=int, default=None)
    args = p.parse_args()

    study = json.loads(Path(args.study).read_text())
    issues = check_cross_module_polarity(study, args.ref_dir)

    critical = [i for i in issues if i.startswith('CRITICAL/')]
    passed = len(critical) == 0
    result = {
        'tag': args.tag,
        'passed': passed,
        'issues': issues,
        'issue_count': len(issues),
        'critical_count': len(critical),
    }
    write_result(args.output, result, passed, args.iter)

    print(f"ECO_SCRIPT_LAUNCHED: eco_check_cross_module_polarity.py")
    print(f"  passed: {passed}")
    print(f"  issues: {len(issues)} ({len(critical)} CRITICAL)")
    print(f"  output: {args.output}")
    if issues:
        print("\nISSUES FOUND:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("\nNo cross-module polarity issues found.")
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
