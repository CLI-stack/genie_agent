#!/usr/bin/env python3
"""
eco_validate_io.py — shared output writer for the ECO step validators
(eco_validate_step1/2/3/4, eco_pre_fm_check).

Two rules, uniform across every validator:
  1. ALWAYS write a per-iteration debug file `<base>_iter<N>.json` on every run
     (N = explicit iter if given, else auto = highest existing +1). This is the
     full attempt history — never overwritten.
  2. The canonical `<canonical_path>` holds ONLY a PASSING result: it is written
     when passed is True and REMOVED when passed is False. So the canonical file
     exists iff the most recent validation passed — downstream gates can trust
     "file present + passed:true" and never read a stale/failing snapshot.
"""
import glob as _glob
import json as _json
import os as _os
import re as _re


def _next_iter(base):
    nums = [int(m) for f in _glob.glob(f'{base}_iter*.json')
            for m in _re.findall(r'_iter(\d+)\.json$', f)]
    return (max(nums) + 1) if nums else 1


def write_result(canonical_path, obj, passed, iter_n=None):
    """Write the per-iteration debug file always; write the canonical only on pass
    (remove it on fail). Returns the iteration file path."""
    base = canonical_path[:-5] if canonical_path.endswith('.json') else canonical_path
    n = iter_n if iter_n is not None else _next_iter(base)
    iter_path = f'{base}_iter{n}.json'
    with open(iter_path, 'w') as f:
        _json.dump(obj, f, indent=2)
    if passed:
        with open(canonical_path, 'w') as f:
            _json.dump(obj, f, indent=2)
    elif _os.path.exists(canonical_path):
        _os.remove(canonical_path)
    return iter_path
