#!/usr/bin/env python3
"""
eco_pre_fm_check.py — Deterministic Step 5 Pre-FM Quality Checker

Replaces agent judgment in eco_pre_fm_checker.md with a script that
reads the applied JSON + study JSON and validates all required conditions.
No agent decisions — every check is deterministic: PASS or FAIL.

Usage:
    python3 eco_pre_fm_check.py \
        --tag <TAG> \
        --round <N> \
        --base-dir <BASE_DIR> \
        --ref-dir <REF_DIR> \
        --jira <JIRA>

Exit 0 = all checks PASS (safe to submit FM)
Exit 1 = any check FAIL (do NOT submit FM)

Writes:
    <BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<N>.json
    <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<N>.rpt
    <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<N>_marker.txt
"""

import argparse, json, os, re, subprocess, sys
from pathlib import Path


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--tag',      required=True)
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--base-dir', required=True, dest='base_dir')
    p.add_argument('--ref-dir',  required=True, dest='ref_dir')
    p.add_argument('--jira',     required=True)
    return p.parse_args()


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    try:
        return json.load(open(path))
    except Exception as e:
        return None


def zgrep_count(pattern, gz_path):
    try:
        r = subprocess.run(
            f'zcat {gz_path} | grep -c {re.escape(pattern)}',
            shell=True, capture_output=True, text=True, timeout=120
        )
        return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
    except Exception:
        return 0


DEFERRED_REASONS = ('deferred', 'pending', 'round 2', 'application', 'defer')

def is_deferred(reason):
    r = (reason or '').lower()
    return any(k in r for k in DEFERRED_REASONS)


# ── Check implementations ─────────────────────────────────────────────────────

def check_no_deferred(applied):
    """
    FAIL if any port_declaration or port_connection entry is SKIPPED
    with a deferral reason. These cause FM ABORT.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            ct = e.get('change_type', '')
            st = e.get('status', '')
            reason = e.get('reason', '')
            if ct in ('port_declaration', 'port_promotion', 'port_connection') \
               and st == 'SKIPPED' and is_deferred(reason):
                failures.append(f'{stage}: {ct} {e.get("name","?")} — {reason[:80]}')
    return failures


def check_stage_consistency(applied):
    """
    FAIL if an ECO gate is INSERTED in some stages but SKIPPED in others.
    Each new_logic_gate/dff must appear in all 3 stages.
    """
    gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    per_stage = {}
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        inserted = {e.get('name','') for e in entries
                    if e.get('change_type','') in gate_types
                    and e.get('status','') == 'INSERTED'}
        skipped  = {e.get('name','') for e in entries
                    if e.get('change_type','') in gate_types
                    and e.get('status','') == 'SKIPPED'}
        per_stage[stage] = {'inserted': inserted, 'skipped': skipped}

    stages = [s for s in per_stage if per_stage[s]['inserted'] or per_stage[s]['skipped']]
    if len(stages) < 2:
        return []

    all_gates = set()
    for s in stages:
        all_gates |= per_stage[s]['inserted'] | per_stage[s]['skipped']

    failures = []
    for gate in sorted(all_gates):
        stage_results = {}
        for s in stages:
            if gate in per_stage[s]['inserted']:
                stage_results[s] = 'INSERTED'
            elif gate in per_stage[s]['skipped']:
                stage_results[s] = 'SKIPPED'
            else:
                stage_results[s] = 'MISSING'
        if len(set(stage_results.values())) > 1:
            failures.append(f'{gate}: {stage_results}')
    return failures


def check_port_declarations_applied(applied):
    """
    FAIL if any port_declaration/port_connection is SKIPPED (for any reason).
    These are all mandatory — no deferral, no skipping.
    Exception: 'wire' type entries (implicitly created) and ALREADY_APPLIED.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            ct = e.get('change_type', '')
            st = e.get('status', '')
            name = e.get('name', '?')
            reason = e.get('reason', '')
            if ct in ('port_declaration', 'port_promotion', 'port_connection') \
               and st == 'SKIPPED' \
               and 'wire' not in reason.lower() \
               and 'implicit' not in reason.lower():
                failures.append(f'{stage}: {ct} {name} SKIPPED — {reason[:80]}')
    return failures


def check_no_unhandled(applied):
    """
    FAIL if any entry has status UNHANDLED — indicates eco_perl_spec didn't
    recognize the change_type, so it was silently dropped.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if e.get('status','') == 'UNHANDLED':
                failures.append(
                    f'{stage}: {e.get("change_type","?")} {e.get("name","?")} UNHANDLED')
    return failures


def check_check8(check8_json_path):
    """
    Read pre-computed eco_check8 result. FAIL if any stage is not PASS.
    """
    d = load_json(check8_json_path)
    if d is None:
        return ['eco_check8 result not found — cannot validate Verilog syntax']
    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        result = d.get(stage, 'MISSING')
        if result != 'PASS':
            failures.append(f'eco_check8 {stage}: {result}')
    return failures


def check_eco_cell_counts(applied):
    """
    WARN (not FAIL) if ECO cell counts differ significantly across stages.
    Route may legitimately have fewer (module renamed in P&R).
    Returns (warnings, failures) — failures are hard FAIL conditions.
    """
    gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    counts = {}
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        counts[stage] = sum(
            1 for e in entries
            if e.get('change_type','') in gate_types
            and e.get('status','') in ('INSERTED', 'ALREADY_APPLIED')
        )

    if not counts:
        return [], []

    max_count = max(counts.values())
    warnings = []
    failures = []
    for stage, count in counts.items():
        if count == 0 and max_count > 0:
            failures.append(f'Stage {stage}: 0 ECO cells applied but other stages have {max_count}')
        elif count < max_count * 0.5:
            warnings.append(f'Stage {stage}: {count} cells vs max {max_count} — possible partial application')
    return warnings, failures


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    base  = args.base_dir
    tag   = args.tag
    rnd   = args.round
    jira  = args.jira

    applied_path  = f'{base}/data/{tag}_eco_applied_round{rnd}.json'
    check8_path   = f'{base}/data/{tag}_eco_check8_round{rnd}.json'
    out_json_path = f'{base}/data/{tag}_eco_pre_fm_check_round{rnd}.json'
    out_rpt_path  = f'{base}/data/{tag}_eco_step5_pre_fm_check_round{rnd}.rpt'
    marker_path   = f'{base}/data/{tag}_eco_step5_pre_fm_check_round{rnd}_marker.txt'

    applied = load_json(applied_path) or {}

    results   = {}
    all_fails = []
    warnings  = []

    # Check 1 — No deferred port declarations
    fails = check_no_deferred(applied)
    results['no_deferred_ports'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[DEFERRED] {f}' for f in fails])

    # Check 2 — Port declarations all applied
    fails = check_port_declarations_applied(applied)
    results['port_declarations_applied'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[PORT_SKIP] {f}' for f in fails])

    # Check 3 — Stage consistency (gates inserted in all stages)
    fails = check_stage_consistency(applied)
    results['stage_consistency'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[STAGE_MISMATCH] {f}' for f in fails])

    # Check 4 — No UNHANDLED entries
    fails = check_no_unhandled(applied)
    results['no_unhandled'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[UNHANDLED] {f}' for f in fails])

    # Check 5 — eco_check8 Verilog validator
    fails = check_check8(check8_path)
    results['check8_verilog'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[SVR4_SVR9] {f}' for f in fails])

    # Check 6 — ECO cell counts (warnings only for partial, hard fail for zero)
    w, fails = check_eco_cell_counts(applied)
    results['eco_cell_counts'] = 'PASS' if not fails else 'FAIL'
    warnings.extend(w)
    all_fails.extend([f'[ZERO_CELLS] {f}' for f in fails])

    passed = len(all_fails) == 0

    # ── Write JSON ────────────────────────────────────────────────────────────
    out = {
        'tag':           tag,
        'round':         rnd,
        'jira':          jira,
        'passed':        passed,
        'failures':      all_fails,
        'warnings':      warnings,
        'check_summary': results,
    }
    with open(out_json_path, 'w') as f:
        json.dump(out, f, indent=2)

    # ── Write RPT ─────────────────────────────────────────────────────────────
    status_str = 'PASS' if passed else 'FAIL'
    lines = [
        '=' * 72,
        f'STEP 5 — PRE-FM QUALITY CHECK (Round {rnd})',
        f'Tag: {tag}  |  JIRA: {jira}',
        '=' * 72,
        f'RESULT: {status_str}',
        '',
    ]
    for check, result in results.items():
        lines.append(f'  {check:<35}: {result}')
    if all_fails:
        lines += ['', 'FAILURES:']
        lines += [f'  {f}' for f in all_fails]
    if warnings:
        lines += ['', 'WARNINGS (non-blocking):']
        lines += [f'  {w}' for w in warnings]
    lines += ['', '=' * 72]
    rpt_text = '\n'.join(lines) + '\n'
    with open(out_rpt_path, 'w') as f:
        f.write(rpt_text)

    # ── Write marker ──────────────────────────────────────────────────────────
    marker = f'ECO_SCRIPT_LAUNCHED: eco_pre_fm_check.py\n  result: {status_str}\n  output: {out_json_path}\n'
    with open(marker_path, 'w') as f:
        f.write(marker)

    print(rpt_text)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
