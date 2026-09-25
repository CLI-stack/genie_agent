#!/usr/bin/env python3
"""
eco_check_cross_module_polarity.py — simple-mode-only, INFORMATIONAL structural check.

Runs the cross-module primary-input polarity check ("Check 68") against a study
JSON, with NO Formality/fenets dependency. Simple mode has no rename map at all, so
this is the only available signal for this bug class there; running it is
MANDATORY in simple mode's flow, but its findings are advisory, NEVER a hard gate.

This does NOT exist in complete mode's eco_validate_step3.py, and deliberately so:
complete mode's fenets rename map already resolves this class of issue correctly
(the studier prioritizes its FM-verified `<stage>_polarity`/`actual_wire_<stage>`
fields). A purely structural check has no way to see that a leaf's cross-module
inversion is legitimately compensated by an independently-inverted OTHER operand
(a fully-synthesized, name-mangled local net whose true polarity only Formality can
prove) — confirmed to false-positive-flag an already-correct, Formality-verified-
passing design. Treat every finding here as "worth a human look," not "proven bug."

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
