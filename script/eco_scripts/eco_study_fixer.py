#!/usr/bin/env python3
"""
eco_study_fixer.py — Auto-apply deterministic fixes to eco_preeco_study.json
based on eco_validate_step3.py issues.

Called by STUDY_ORCHESTRATOR after the validator fails. Walks the ENTIRE issue
list in one pass and applies every deterministic fix it can (batch), then the
orchestrator re-validates ONCE. Non-deterministic / topology issues (missing
gates, driver-rename, undriven nets) are left for the re-studier.

Deterministic classes handled:
  - chain-injection schema "missing ['gate_function']"  -> derive from cell_type family
  - ANDTERM-WRONG-POLARITY                                -> flip NOR2<->INR2 per FM polarity
  - NET-ABSENT-IN-STAGE                                   -> flat->bracket recovery, then resolve
  - PENDING-UNRESOLVED                                    -> eco_resolve_synth_internal.py
  - CONDITION-POLARITY                                    -> use resolved condition net
  - REWIRE-CELL-ABSENT                                    -> use stage-correct cell_name_per_stage

Usage:
    python3 script/eco_scripts/eco_study_fixer.py \\
        --study   data/<TAG>_eco_preeco_study.json \\
        --issues  data/<TAG>_eco_validate_step3.json \\
        --rtl-diff data/<TAG>_eco_rtl_diff.json \\
        --ref-dir <REF_DIR> \\
        --raw-rpts data/*_find_equivalent_nets_raw*.rpt \\
        --step2-rpt data/<TAG>_eco_step2_fenets.rpt \\
        --output  data/<TAG>_eco_preeco_study.json

Returns exit code 0 if all issues fixed, 1 if issues remain (need manual fix).
Prints summary of fixes applied and remaining issues.
"""
import argparse, glob, gzip, json, os, re, subprocess, sys

STAGES = ('Synthesize', 'PrePlace', 'Route')

# family_of() maps a full cell_type to its abstract gate family (OR2, NR2, AO22, …).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from eco_cell_truth_tables import family_of as _family_of
except Exception:
    _family_of = None


# ── helpers ───────────────────────────────────────────────────────────────────

_PREECO_CACHE = {}


def _preeco_text(ref_dir, stage):
    """Cached PreEco <stage>.v.gz text (read once per stage)."""
    key = (ref_dir, stage)
    if key not in _PREECO_CACHE:
        p = os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
        try:
            with gzip.open(p, 'rt', errors='replace') as f:
                _PREECO_CACHE[key] = f.read()
        except Exception:
            _PREECO_CACHE[key] = ''
    return _PREECO_CACHE[key]


def _flat_to_bracket(net):
    """'sig_3_' -> 'sig[3]' ; returns None if not flat-indexed form."""
    m = re.match(r'^(.*)_(\d+)_$', net or '')
    return f'{m.group(1)}[{m.group(2)}]' if m else None


def _gate_function_from_cell(cell_type):
    """Derive the abstract gate_function from a full cell_type (via family_of)."""
    if not cell_type:
        return None
    if _family_of:
        fam = _family_of(cell_type)
        if fam:
            return fam
    m = re.match(r'^([A-Z]+\d*)', cell_type)   # fallback: leading alpha+digit prefix
    return m.group(1) if m else None

def run_resolve(ref_dir, synth_net, stage):
    """Run eco_resolve_synth_internal.py. Returns resolved_net or 'UNRESOLVABLE'."""
    script = os.path.join(os.path.dirname(__file__), 'eco_resolve_synth_internal.py')
    out = '/tmp/eco_study_fixer_resolve.json'
    try:
        subprocess.run(
            f'python3 {script} --ref-dir {ref_dir} --synth-net {synth_net} '
            f'--stage {stage} --output {out}',
            shell=True, timeout=120, capture_output=True)
        r = json.load(open(out))
        return r.get('resolved_net', 'UNRESOLVABLE')
    except Exception:
        return 'UNRESOLVABLE'


def read_raw_polarity(raw_rpts, cell_inst):
    """Read FM (+/-) polarity for a cell instance from raw rpt files."""
    raw_text = ''
    for rp in sorted(raw_rpts):
        try:
            raw_text += open(rp).read()
        except Exception:
            pass
    if not raw_text:
        return None
    m = re.search(
        r'Impl\s+Net\s+([+\-])\s+i:[^\n]+/' + re.escape(cell_inst) + r'/',
        raw_text)
    return m.group(1) if m else None


def read_condition_resolutions(step2_rpt):
    """Parse CONDITION_INPUT_RESOLUTIONS from step2 rpt."""
    res = {}
    if not step2_rpt or not os.path.exists(step2_rpt):
        return res
    for line in open(step2_rpt):
        m = re.match(r'\s+(\w+):\s+resolved=(\S+)', line)
        if m:
            res[m.group(1)] = m.group(2)
    return res


# ── per-issue fixers ──────────────────────────────────────────────────────────

def fix_andterm_wrong_polarity(study, issue_text, raw_rpts):
    """ANDTERM-WRONG-POLARITY: flip gate function and remap pins based on FM polarity."""
    inst_m = re.search(r"'(eco_\w+)'\s+uses\s+(NOR2|INR2)\s+but.*\(([+\-])\)", issue_text)
    if not inst_m:
        return False
    inst, wrong, fm_pol = inst_m.group(1), inst_m.group(2), inst_m.group(3)
    correct = 'INR2' if fm_pol == '+' else 'NOR2'
    if wrong == correct:
        return False
    cell_map = {'INR2': 'INR2D1BWP136P5M156H3P48CPDLVT',
                'NOR2': 'NR2D1SPG1AMDBWP136P5M156H3P48CPDLVT'}
    # Pin remapping: NOR2 uses A2; INR2 uses B1 for the second input
    pin_remap = {}
    if wrong == 'NOR2' and correct == 'INR2':
        pin_remap = {'A2': 'B1'}  # NOR2.A2 → INR2.B1
    elif wrong == 'INR2' and correct == 'NOR2':
        pin_remap = {'B1': 'A2'}  # INR2.B1 → NOR2.A2
    fixed = 0
    for stage in STAGES:
        for e in study.get(stage, []):
            if e.get('instance_name') == inst and e.get('gate_function') == wrong:
                e['gate_function'] = correct
                e['cell_type'] = cell_map.get(correct, correct)
                # Remap pins in port_connections and port_connections_per_stage
                for pc in [e.get('port_connections') or {}] + \
                          list((e.get('port_connections_per_stage') or {}).values()):
                    for old_pin, new_pin in pin_remap.items():
                        if old_pin in pc:
                            pc[new_pin] = pc.pop(old_pin)
                fixed += 1
    return fixed > 0


def fix_missing_gate_function(study, issue_text):
    """chain-injection schema '... missing ['gate_function']': derive it from cell_type.
    Only fills the field (never overrides an existing one); consistent with cell_type so
    the Check-12 truth-table check stays satisfied by construction."""
    m = re.search(r"new_logic_gate\s+(\S+)\s+in\s+(?:Synthesize|PrePlace|Route)\s+missing\s+\[?'?gate_function",
                  issue_text)
    if not m:
        return False, 'parse_failed'
    inst = m.group(1)
    fixed, fam = 0, None
    for st in STAGES:
        for e in study.get(st, []):
            if e.get('instance_name') != inst or e.get('change_type') != 'new_logic_gate':
                continue
            if e.get('gate_function'):
                continue
            ct = e.get('cell_type') or e.get('dff_cell_type')
            fam = _gate_function_from_cell(ct)
            if fam:
                e['gate_function'] = fam
                fixed += 1
    if fixed:
        return True, f'gate_function={fam}'
    return False, 'no_cell_type'


def fix_net_absent(study, issue_text, ref_dir):
    """NET-ABSENT-IN-STAGE: first try flat->bracket form (verified against PreEco),
    then fall back to eco_resolve_synth_internal.py to find the correct P&R net."""
    m = re.search(r'(Synthesize|PrePlace|Route)\s+(eco_\w+)\.(\w+)\s+=\s+\'(\S+)\'', issue_text)
    if not m:
        return False, 'parse_failed'
    stage, inst, pin, wrong_net = m.group(1), m.group(2), m.group(3), m.group(4)

    # ── flat -> bracket form recovery (e.g. recdsp_c0mop_4_ -> recdsp_c0mop[4]) ──
    br = _flat_to_bracket(wrong_net)
    if br and br in _preeco_text(ref_dir, stage):
        n = 0
        for st in STAGES:
            for e in study.get(st, []):
                if e.get('instance_name') != inst:
                    continue
                pc = e.get('port_connections') or {}
                if pc.get(pin) == wrong_net:
                    pc[pin] = br; n += 1
                sp = (e.get('port_connections_per_stage') or {}).get(stage) or {}
                if sp.get(pin) == wrong_net:
                    sp[pin] = br; n += 1
        if n:
            return True, f'flat->bracket {wrong_net}->{br}'

    if stage == 'Synthesize':
        return False, 'synth_absent_manual'
    # Determine Synth net for this pin
    synth_net = None
    for e in study.get('Synthesize', []):
        if e.get('instance_name') == inst:
            pps = e.get('port_connections_per_stage', {})
            synth_net = (pps.get('Synthesize') or {}).get(pin) or \
                        (e.get('port_connections') or {}).get(pin)
            break
    if not synth_net or any(synth_net.startswith(p) for p in
                             ('UNRESOLVABLE', 'PENDING', 'MODE_H', 'NEEDS', "1'b")):
        return False, 'no_synth_net'
    resolved = run_resolve(ref_dir, synth_net, stage)
    if resolved == 'UNRESOLVABLE':
        return False, f'unresolvable:{synth_net}'
    # Apply fix across all study stage entries
    fixed = 0
    for st in STAGES:
        for e in study.get(st, []):
            if e.get('instance_name') != inst:
                continue
            pps = e.setdefault('port_connections_per_stage', {})
            pps.setdefault(stage, {})[pin] = resolved
            fixed += 1
    return fixed > 0, f'{synth_net}→{resolved}'


def fix_pending_unresolved(study, issue_text, ref_dir):
    """PENDING-UNRESOLVED: run eco_resolve_synth_internal.py."""
    m = re.search(r'(Synthesize|PrePlace|Route)\s+(eco_\w+).*\[(\w+)\]\.(\w+)\s+=\s+\w+:(\w+)',
                  issue_text)
    if not m:
        return False, 'parse_failed'
    study_stage, inst, pps_stage, pin, sig = (m.group(1), m.group(2), m.group(3),
                                               m.group(4), m.group(5))
    if pps_stage == 'Synthesize':
        return False, 'synth_pending_manual'
    # Find synth resolved net from condition_resolutions or study
    synth_net = None
    for e in study.get('Synthesize', []):
        if e.get('instance_name') == inst:
            pps = e.get('port_connections_per_stage', {})
            synth_net = (pps.get('Synthesize') or {}).get(pin)
            break
    if not synth_net or any(synth_net.startswith(p) for p in
                             ('UNRESOLVABLE', 'PENDING', 'MODE_H', 'NEEDS')):
        return False, 'no_synth_net'
    resolved = run_resolve(ref_dir, synth_net, pps_stage)
    if resolved == 'UNRESOLVABLE':
        return False, f'unresolvable:{synth_net}'
    fixed = 0
    for st in STAGES:
        for e in study.get(st, []):
            if e.get('instance_name') != inst:
                continue
            pps = e.setdefault('port_connections_per_stage', {})
            pps.setdefault(pps_stage, {})[pin] = resolved
            fixed += 1
    return fixed > 0, f'{synth_net}→{resolved}'


def fix_condition_polarity(study, issue_text, cond_res):
    """CONDITION-POLARITY: replace wrong Synth net with resolved net."""
    m = re.search(r"(eco_\w+)\.(\w+)\s+=\s+'(\S+)'.*resolved.*to\s+'(\S+)'", issue_text)
    if not m:
        return False
    inst, pin, wrong, correct = m.group(1), m.group(2), m.group(3), m.group(4)
    fixed = 0
    for stage in STAGES:
        for e in study.get(stage, []):
            if e.get('instance_name') != inst:
                continue
            pps = e.get('port_connections_per_stage', {})
            synth_p = pps.get('Synthesize', {})
            if synth_p.get(pin) == wrong:
                synth_p[pin] = correct
                fixed += 1
            pcs = e.get('port_connections', {})
            if pcs.get(pin) == wrong:
                pcs[pin] = correct
                fixed += 1
    return fixed > 0


def fix_rewire_cell_absent(study, issue_text, ref_dir):
    """REWIRE-CELL-ABSENT: the rewire names a cell that has 0 occurrences in that stage
    (usually a PrePlace/Route cell name used in Synthesize). If the entry carries a
    per-stage cell map, use the stage-correct name; verify it exists in that stage's PreEco."""
    m = re.search(r'(Synthesize|PrePlace|Route)\s+rewire\s+\'(\S+)→(\S+)\'\s+uses\s+cell\s+\'(\S+)\'',
                  issue_text)
    if not m:
        return False, 'parse_failed'
    stage, old_net, new_net, wrong_cell = m.group(1), m.group(2), m.group(3), m.group(4)
    fixed = 0
    for e in study.get(stage, []):
        if e.get('change_type') != 'rewire':
            continue
        if e.get('new_net') != new_net and (e.get('new_net_per_stage') or {}).get(stage) != new_net:
            continue
        cps = e.get('cell_name_per_stage') or {}
        cand = cps.get(stage)
        if cand and cand != wrong_cell and cand in _preeco_text(ref_dir, stage):
            e['cell_name'] = cand
            fixed += 1
    if fixed:
        return True, f'use per-stage cell for {stage}'
    return False, 'needs_rename_map_lookup'


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--study',    required=True)
    ap.add_argument('--issues',   required=True, help='eco_validate_step3.json')
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--ref-dir',  required=True)
    ap.add_argument('--raw-rpts', nargs='*', default=[])
    ap.add_argument('--step2-rpt', default='')
    ap.add_argument('--output',   required=True)
    args = ap.parse_args()

    study = json.load(open(args.study))
    validate = json.load(open(args.issues))
    issues = validate.get('issues', [])
    raw_rpts = args.raw_rpts or glob.glob(os.path.join(
        os.path.dirname(args.study), '*_find_equivalent_nets_raw*.rpt'))
    cond_res = read_condition_resolutions(args.step2_rpt)

    if not issues:
        print('No issues to fix.')
        json.dump(study, open(args.output, 'w'), indent=2)
        sys.exit(0)

    fixed_list = []
    remaining = []

    for issue in issues:
        applied = False
        detail = ''

        if "missing ['gate_function']" in issue or 'missing [\'gate_function\']' in issue \
           or ("gate_function" in issue and 'chain-injection schema' in issue and 'missing' in issue):
            applied, detail = fix_missing_gate_function(study, issue)

        elif 'ANDTERM-WRONG-POLARITY' in issue:
            applied = fix_andterm_wrong_polarity(study, issue, raw_rpts)
            detail = 'flip NOR2↔INR2'

        elif 'NET-ABSENT-IN-STAGE' in issue:
            applied, detail = fix_net_absent(study, issue, args.ref_dir)

        elif 'PENDING-UNRESOLVED' in issue:
            applied, detail = fix_pending_unresolved(study, issue, args.ref_dir)

        elif 'CONDITION-POLARITY' in issue:
            applied = fix_condition_polarity(study, issue, cond_res)
            detail = 'replace with resolved net'

        elif 'REWIRE-CELL-ABSENT' in issue:
            applied, detail = fix_rewire_cell_absent(study, issue, args.ref_dir)

        if applied:
            fixed_list.append(f'FIXED [{detail}]: {issue[:80]}...')
        else:
            remaining.append(f'MANUAL [{detail}]: {issue[:80]}...')

    json.dump(study, open(args.output, 'w'), indent=2)

    print(f'\n=== eco_study_fixer results ===')
    print(f'Fixed:     {len(fixed_list)}')
    print(f'Remaining: {len(remaining)}')
    for f in fixed_list:
        print(f'  ✅ {f}')
    for r in remaining:
        print(f'  ❌ {r}')

    sys.exit(0 if not remaining else 1)


if __name__ == '__main__':
    main()
