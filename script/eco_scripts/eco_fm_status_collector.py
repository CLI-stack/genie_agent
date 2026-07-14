#!/usr/bin/env python3
"""
eco_fm_status_collector.py — Single source of truth for ECO FM verdict.

Replaces the agent's free-form Layer 1 work in eco_fm_runner.md STEP E.
Reads FM-generated artifacts deterministically and writes the canonical
eco_fm_verify.json schema. No agent in this layer.

Inputs per ECO target (FmEqvEcoSynthesizeVsSynRtl, FmEqvEcoPrePlaceVs...,
FmEqvEcoRouteVs...):
  <REF_DIR>/rpts/<target>/<target>__runtime.rpt.gz
  <REF_DIR>/rpts/<target>/<target>.dat
  <REF_DIR>/logs/<target>.log[.gz|.bz2]
    (or <REF_DIR>/rpts/<target>/formality.log[.gz])

Output: <BASE_DIR>/data/<TAG>_eco_fm_verify.json

Canonical schema (v1):
{
  "schema_version": "v1",
  "tag": "...",
  "round": 1,
  "verdict": "PASS|FAIL|ABORT_NETLIST|ABORT_LINK|ABORT_SVF|ABORT_OTHER|NOT_RUN",
  "per_target": {
    "<target>": {
      "verdict":          "PASS|FAIL|ABORT_*|NOT_RUN",
      "runtime_seconds":  <int>,
      "phase_status": {
        "Constraints":  "<numeric|error|n/a>",
        "PreVerify":    "...",
        "Match":        "...",
        "Verify":       "...",
        "RefElab":      "...",
        ...
      },
      "abort_pattern":    "<pattern_kind>" or null,
      "abort_class":      "<ABORT_NETLIST|...>" or null,
      "abort_evidence":   [{"file": ..., "line": ..., "text": ...}],
      "failing_points":   [<paths>] or null,
      "log_path":         "<resolved>" or null
    }
  },
  "abort_targets":     [...],
  "fail_targets":      [...],
  "ok_targets":        [...]
}

Determines verdict from a SINGLE decision table (no per-call ambiguity):

  failing_points.rpt.gz is the AUTHORITATIVE source. The FM log and
  TileBuilderShow status may show "FAILED" even when compare points = 0
  (AMD Tcl wrapper emits "Formality run Failed" for constraint warnings etc.).
  Rule: if failing_points.rpt.gz parses to 0 entries → PASS always.

  __runtime.rpt.gz row     | failing_points | → per-target verdict
  ──────────────────────────┼────────────────┼─────────────────────
  all phases numeric        | empty/none     | PASS
  all phases numeric        | non-empty      | FAIL
  any phase == 'error'      | empty/none     | ABORT_<class from log>
  missing/no row            | (don't care)   | NOT_RUN

Top-level verdict:
  any per_target ABORT_*   → ABORT_<most_severe_class>
  any per_target FAIL      → FAIL
  any per_target NOT_RUN   → mixed (caller decides)
  all per_target PASS      → PASS
"""
import argparse, gzip, json, os, re, sys
from pathlib import Path

# Reuse the abort cause classifier (loads YAML at import time)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eco_extract_fm_abort_cause as _abort_cause
from eco_fm_targets import detect_targets


SCHEMA_VERSION = "v1"

DEFAULT_ECO_TARGETS = [
    "FmEqvEcoSynthesizeVsSynRtl",
    "FmEqvEcoPrePlaceVsEcoSynthesize",
    "FmEqvEcoRouteVsEcoPrePlace",
]


def _runtime_seconds(s):
    """Parse '9m1s', '6m19s', '0s', '3h12m', etc. into integer seconds. Returns
    None for non-time strings ('error', 'n/a', '')."""
    if not s or s in ('error', 'n/a', '-'):
        return None
    total = 0
    m = re.match(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$', s.strip())
    if not m or not any(m.groups()):
        return None
    h, mi, se = m.groups()
    if h:  total += int(h) * 3600
    if mi: total += int(mi) * 60
    if se: total += int(se)
    return total


def parse_runtime_rpt(ref_dir, target):
    """Read <ref_dir>/rpts/<target>/<target>__runtime.rpt.gz and return:
        {column_name: cell_value, 'runtime_seconds': int|None}
    or None if file missing / unparseable."""
    rpt = Path(ref_dir) / 'rpts' / target / f'{target}__runtime.rpt.gz'
    if not rpt.is_file():
        return None
    try:
        with gzip.open(rpt, 'rt', errors='replace') as f:
            text = f.read()
    except Exception:
        return None
    lines = [ln.rstrip('\n') for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        return None
    # Format:
    #   Data for FmEqv...
    #   <header line — column names whitespace-separated>
    #   ====...===
    #   <data line — cells whitespace-separated>
    #   ====...===
    header = None
    data = None
    for i, ln in enumerate(lines):
        if ln.startswith('==='):
            # Next non-divider line is the data row
            for j in range(i+1, len(lines)):
                if not lines[j].startswith('==='):
                    data = lines[j]; break
            # Header is the previous non-divider line
            for j in range(i-1, -1, -1):
                if not lines[j].startswith('==='):
                    header = lines[j]; break
            break
    if not header or not data:
        return None
    cols = header.split()
    # Data cells — first cell is TileName (one token), rest map by position
    data_cells = data.split()
    # Drop tile name (first cell)
    if len(data_cells) > 1 and len(cols) > 1:
        out = dict(zip(cols[1:], data_cells[1:]))
    else:
        out = dict(zip(cols, data_cells))
    # Compute runtime_seconds from the Overall column
    overall = out.get('Overall', '')
    out['runtime_seconds'] = _runtime_seconds(overall)
    return out


def parse_dat_run_status(ref_dir, target):
    """Read <ref_dir>/rpts/<target>/<target>.dat and return runStatus value."""
    dat = Path(ref_dir) / 'rpts' / target / f'{target}.dat'
    if not dat.is_file():
        return None
    for ln in dat.read_text(errors='replace').splitlines():
        if ln.startswith('runStatus:'):
            return ln.split(':', 1)[1].strip()
    return None


def parse_failing_points(ref_dir, target):
    """Read <target>__failing_points.rpt.gz; return list of failing point paths
    or empty list if FM hadn't reached the verify phase / file missing.

    Formality reports failing points as consecutive line PAIRS:
      Ref  DFF   r:/FMWORK_REF_.../DFF_name
      Impl DFF   i:/FMWORK_IMPL_.../DFF_name
    Each path is on a SEPARATE line — never on the same line. The old check
    ('i:/' in ln and 'r:/' in ln) would NEVER match, causing all failing
    reports to appear as 0 failing points → incorrect PASS verdict.
    """
    rpt = Path(ref_dir) / 'rpts' / target / f'{target}__failing_points.rpt.gz'
    if not rpt.is_file():
        return []
    points = []
    HEADER_RE = re.compile(r'^\s*(Reference|Implementation|Version|Date|Report)\s*:')
    NONE_SENTINEL = re.compile(r'No failing compare points\.?$')
    # Count summary line: "N Failing compare points"
    COUNT_RE = re.compile(r'^\s*(\d+)\s+Failing compare points')
    try:
        with gzip.open(rpt, 'rt', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return []

    # Fast path: check for explicit "No failing" sentinel or count=0
    for ln in lines:
        if NONE_SENTINEL.search(ln):
            return []
        m = COUNT_RE.match(ln)
        if m and int(m.group(1)) == 0:
            return []

    # Parse paired lines: "  [*]Ref  <type>  r:/..." followed by "  Impl <type>  i:/..."
    # Formality may prefix the Ref line with '*' when the failure has an undriven signal.
    prev_ref = None
    for ln in lines:
        if HEADER_RE.match(ln):
            continue
        ln_s = ln.strip()
        # Match "Ref ..." or "*Ref ..." (asterisk prefix for undriven-related failures)
        is_ref  = (ln_s.startswith('Ref ') or ln_s.startswith('*Ref ')) and 'r:/' in ln_s
        is_impl = ln_s.startswith('Impl ') and 'i:/' in ln_s
        if is_ref:
            prev_ref = ln_s
        elif is_impl and prev_ref:
            # Pair complete — record the ref path as the failing point identifier
            points.append(prev_ref)
            prev_ref = None
        else:
            prev_ref = None  # reset on non-matching line

    # Fallback: also catch any single-line format with both i:/ and r:/
    if not points:
        for ln in lines:
            if 'i:/' in ln and 'r:/' in ln and not HEADER_RE.match(ln):
                points.append(ln.strip())

    return points


def classify_target(ref_dir, logs_dir, target):
    """Compute the per-target verdict by combining runtime row + failing
    points + (if needed) abort log classification."""
    runtime = parse_runtime_rpt(ref_dir, target)
    if runtime is None:
        return {
            'verdict':         'NOT_RUN',
            'runtime_seconds': None,
            'phase_status':    {},
            'abort_pattern':   None,
            'abort_class':     None,
            'abort_evidence':  [],
            'failing_points':  None,
            'log_path':        None,
        }
    phase_status = {k: v for k, v in runtime.items()
                    if k not in ('runtime_seconds',)}
    runtime_secs = runtime.get('runtime_seconds')

    # Detect ABORT: any of the comparison phases is 'error'
    abort_phase_keys = ('PreVerify', 'Match', 'Verify', 'RefElab')
    is_abort = any(phase_status.get(k) == 'error' for k in abort_phase_keys)

    failing = parse_failing_points(ref_dir, target)
    log_path = _abort_cause._find_log_path(logs_dir, target)

    # FAILING_POINTS TAKES PRIORITY OVER ABORT/FAILED LOG STATUS:
    # Formality's AMD wrapper may emit "Formality run Failed" in the log and
    # set some internal phase to 'error' even when all compare points pass
    # (e.g. unread_analysis warnings, internal constraint issues). The only
    # authoritative source of logical equivalence is the failing_points.rpt.gz.
    # Rule: if failing_points.rpt.gz is explicitly parsed AND empty → PASS,
    # regardless of what the log or TileBuilderShow reports as status.
    if failing is not None and len(failing) == 0 and is_abort:
        # Phase showed error but failing points = 0 → FM completed verification
        # and found no mismatches. Treat as PASS (AMD wrapper false-FAILED).
        is_abort = False

    if is_abort:
        # Read the FM log + classify via YAML pattern table
        abort_pattern = None
        abort_class = None
        abort_evidence = []
        if log_path:
            log_text = _abort_cause._read_log(log_path)
            hits = _abort_cause.classify(log_text, target)
            # Pick the first / most-specific ABORT_NETLIST > ABORT_LINK >
            # ABORT_SVF > ABORT_OTHER, then the first hit otherwise.
            class_priority = {'ABORT_NETLIST': 0, 'ABORT_LINK': 1,
                              'ABORT_SVF': 2, 'ABORT_OTHER': 3}
            hits.sort(key=lambda h: class_priority.get(h.get('abort_type'), 9))
            if hits:
                top = hits[0]
                abort_class   = top.get('abort_type', 'ABORT_OTHER')
                abort_pattern = top.get('pattern_kind')
                abort_evidence = [
                    {
                        'file': log_path,
                        'pattern_kind': h.get('pattern_kind'),
                        'abort_type':   h.get('abort_type'),
                        'match':        h.get('match'),
                        'log_excerpt':  h.get('log_excerpt'),
                    }
                    for h in hits[:5]  # cap at 5 per target
                ]
            else:
                abort_class = 'ABORT_OTHER'
                abort_pattern = 'unknown'
                abort_evidence = [{'file': log_path, 'note':
                    'log read but no YAML pattern matched — extend '
                    'eco_fm_abort_patterns.yaml with a new entry'}]
        else:
            abort_class = 'ABORT_OTHER'
            abort_pattern = 'log_not_found'
            abort_evidence = [{'note': f'no per-target log found near '
                                       f'{logs_dir} for {target}'}]
        return {
            'verdict':         abort_class,
            'runtime_seconds': runtime_secs,
            'phase_status':    phase_status,
            'abort_pattern':   abort_pattern,
            'abort_class':     abort_class,
            'abort_evidence':  abort_evidence,
            'failing_points':  None,
            'log_path':        log_path,
        }

    # Not ABORT — has FM finished comparison? failing_points decides PASS/FAIL.
    if failing:
        return {
            'verdict':         'FAIL',
            'runtime_seconds': runtime_secs,
            'phase_status':    phase_status,
            'abort_pattern':   None,
            'abort_class':     None,
            'abort_evidence':  [],
            'failing_points':  failing,
            'log_path':        log_path,
        }
    # All phases numeric AND no failing points → PASS
    return {
        'verdict':         'PASS',
        'runtime_seconds': runtime_secs,
        'phase_status':    phase_status,
        'abort_pattern':   None,
        'abort_class':     None,
        'abort_evidence':  [],
        'failing_points':  [],
        'log_path':        log_path,
    }


def aggregate_top_verdict(per_target):
    """Top-level verdict from per-target verdicts."""
    verdicts = [v.get('verdict', 'NOT_RUN') for v in per_target.values()]
    # Severity priority: ABORT > FAIL > NOT_RUN > PASS
    abort_classes = [v for v in verdicts if v.startswith('ABORT_')]
    if abort_classes:
        # Pick the most severe ABORT class observed
        priority = ['ABORT_NETLIST', 'ABORT_LINK', 'ABORT_SVF', 'ABORT_OTHER']
        for cls in priority:
            if cls in abort_classes:
                return cls
        return abort_classes[0]
    if 'FAIL' in verdicts:
        return 'FAIL'
    if 'NOT_RUN' in verdicts and 'PASS' not in verdicts:
        return 'NOT_RUN'
    if all(v == 'PASS' for v in verdicts):
        return 'PASS'
    # Mix of PASS + NOT_RUN — partial completion
    return 'PARTIAL'


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--ref-dir',   required=True, help='REF_DIR (TileBuilder root)')
    p.add_argument('--tag',       required=True)
    p.add_argument('--round',     type=int, default=1)
    p.add_argument('--output',    required=True, help='Output eco_fm_verify.json')
    p.add_argument('--targets',   default=None,
                   help='Comma-separated list of FM targets to inspect '
                        '(default: auto-detect the tile\'s Eco triple, '
                        'infix-tolerant for UPF-named projects)')
    p.add_argument('--prev-verify', default=None,
                   help='Path to previous round eco_fm_verify.json — carry forward '
                        'PASS results for targets not in --targets (avoids missing '
                        'results in HTML when skipping already-passing targets)')
    args = p.parse_args()

    ref_dir = Path(args.ref_dir).resolve()
    if not ref_dir.is_dir():
        print(f'ERROR: --ref-dir {ref_dir} is not a directory', file=sys.stderr)
        return 2
    logs_dir = ref_dir / 'logs'

    if args.targets:
        targets = [t.strip() for t in args.targets.split(',') if t.strip()]
    else:
        # Auto-detect the tile's Eco triple (plain or UPF).
        targets = detect_targets(str(ref_dir), 'Eco')
    per_target = {}
    for t in targets:
        per_target[t] = classify_target(str(ref_dir), str(logs_dir), t)

    # Carry forward PASS results from previous round for skipped targets
    if args.prev_verify and Path(args.prev_verify).is_file():
        try:
            prev = json.loads(Path(args.prev_verify).read_text())
            prev_per = prev.get('per_target', {})
            # Iterate the ACTUAL prior-round target keys (project-aware: works
            # for plain and UPF names) rather than a hardcoded triple.
            for t, pv in prev_per.items():
                if t not in per_target and isinstance(pv, dict) and pv.get('verdict') == 'PASS':
                    per_target[t] = dict(pv)
                    per_target[t]['carried_forward_from_round'] = prev.get('round')
                    print(f'  CARRY_FORWARD: {t} PASS from round {prev.get("round")}')
        except Exception as ex:
            print(f'WARNING: could not read --prev-verify: {ex}', file=sys.stderr)

    top_verdict = aggregate_top_verdict(per_target)

    abort_targets = [t for t, v in per_target.items()
                     if v['verdict'].startswith('ABORT_')]
    fail_targets  = [t for t, v in per_target.items() if v['verdict'] == 'FAIL']
    ok_targets    = [t for t, v in per_target.items() if v['verdict'] == 'PASS']

    out = {
        'schema_version': SCHEMA_VERSION,
        'tag':            args.tag,
        'round':          args.round,
        'verdict':        top_verdict,
        'per_target':     per_target,
        'abort_targets':  abort_targets,
        'fail_targets':   fail_targets,
        'ok_targets':     ok_targets,
    }
    Path(args.output).write_text(json.dumps(out, indent=2))

    print(f'eco_fm_status_collector.py: verdict={top_verdict}')
    for t, v in per_target.items():
        line = f'  {t}: {v["verdict"]}'
        if v.get('runtime_seconds') is not None:
            line += f' (runtime={v["runtime_seconds"]}s)'
        if v.get('abort_pattern'):
            line += f' pattern={v["abort_pattern"]}'
        if v.get('failing_points'):
            line += f' failing={len(v["failing_points"])}'
        print(line)
    print(f'  output: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
