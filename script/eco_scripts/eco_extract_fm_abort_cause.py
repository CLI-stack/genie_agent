#!/usr/bin/env python3
"""
eco_extract_fm_abort_cause.py — Deterministic FM ABORT classification.

Step 6 (eco_fm_runner) writes eco_fm_verify.json with `status: "ABORT"` when
FM died before comparison. Today the orchestrator agent is supposed to read
the raw FM logs, classify the abort, write round_handoff.json with a
remediation hint, and spawn ROUND_ORCHESTRATOR. In practice — after a 3+ hour
FM wait — the orchestrator agent often runs close to its context limit and
exits without spawning the next round.

This script removes the agent dependency: invoke it unconditionally after
Step 6 and it produces the deterministic verdict, classified abort type,
extracted log excerpt, and a remediation hint, written to round_handoff.json.

Abort taxonomy (matches eco_fm_pattern_library.md §B-ABORT-1..4):
  ABORT_NETLIST — Verilog parse failure (FM-599, FM-001, "Duplicate wire/...")
  ABORT_LINK    — Linking/elaboration failure (FM-036 unknown net, FE-LINK-7,
                  port mismatch)
  ABORT_SVF     — SVF guidance error (CMD-005, CMD-010)
  ABORT_OTHER   — Anything else (license, segfault, OOM)

Usage:
    python3 eco_extract_fm_abort_cause.py \\
        --fm-verify   data/<TAG>_eco_fm_verify.json \\
        --logs-dir    <REF_DIR>/logs \\
        --tag         <TAG> \\
        --round       1 \\
        --output      data/<TAG>_eco_fm_abort_classification.json \\
        [--update-round-handoff data/<TAG>_round_handoff.json]
"""
import argparse, gzip, json, os, re, sys
from pathlib import Path

import yaml  # PyYAML — REQUIRED. eco_fm_abort_patterns.yaml is the single source
             # of truth for ABORT pattern definitions. No hardcoded fallback.


# Default YAML location — alongside the eco_agents MD library.
DEFAULT_PATTERN_YAML = (
    Path(__file__).resolve().parent.parent.parent
    / 'config' / 'eco_agents' / 'eco_fm_abort_patterns.yaml'
)


def load_patterns_from_yaml(yaml_path):
    """Load ABORT patterns from YAML. Returns list of tuples:
        [(compiled_regex, abort_class, pattern_kind, suggested_action), ...]

    Raises RuntimeError on missing file, parse error, empty patterns, or any
    regex compile failure. There is NO hardcoded fallback — the YAML is the
    single source of truth, by design (see FUTURE_GAPS B9: parallel knowledge
    stores caused stale recipes; YAML must be authoritative).
    """
    yp = Path(yaml_path)
    if not yp.is_file():
        raise RuntimeError(
            f"ABORT pattern YAML not found at {yp}. This file is the SINGLE "
            f"SOURCE OF TRUTH for ABORT pattern definitions; the script has "
            f"no hardcoded fallback. Restore the file or pass --pattern-yaml "
            f"<other_path>.")
    try:
        data = yaml.safe_load(yp.read_text())
    except Exception as e:
        raise RuntimeError(f"cannot parse pattern YAML {yp}: {e}") from e
    patterns_dict = (data or {}).get('patterns', {})
    if not isinstance(patterns_dict, dict) or not patterns_dict:
        raise RuntimeError(
            f"pattern YAML {yp} has no 'patterns:' dict or it is empty. "
            f"Cannot classify any ABORT — refusing to silently no-op.")
    out = []
    errors = []
    for kind, body in patterns_dict.items():
        if not isinstance(body, dict):
            errors.append(f"  {kind!r}: not a dict")
            continue
        rx = body.get('regex', '')
        if not rx:
            errors.append(f"  {kind!r}: missing 'regex' field")
            continue
        flags = 0
        if body.get('multiline'):   flags |= re.MULTILINE
        if body.get('ignore_case'): flags |= re.IGNORECASE
        try:
            crx = re.compile(rx, flags)
        except re.error as e:
            errors.append(f"  {kind!r}: regex compile failed — {e}")
            continue
        abort_class = body.get('abort_class', '')
        if not abort_class:
            errors.append(f"  {kind!r}: missing 'abort_class' field")
            continue
        action = body.get('suggested_action', '').strip()
        out.append((crx, abort_class, kind, action))
    if errors:
        raise RuntimeError(
            "Pattern YAML has invalid entries — refusing to load partially:\n"
            + '\n'.join(errors))
    return out


def _load_one_yaml_safe(yaml_path, required=False):
    """Internal helper used by load_patterns_with_auto. Returns list of pattern
    tuples (possibly empty) without raising on a missing/empty optional file.
    The `required=True` path delegates to load_patterns_from_yaml (raises)."""
    if required:
        return load_patterns_from_yaml(yaml_path)
    if not Path(yaml_path).is_file():
        return []
    try:
        return load_patterns_from_yaml(yaml_path)
    except RuntimeError as e:
        # Auto-YAML failures must not break main classification — log and skip.
        # (Main YAML failure is fatal and goes through the required=True branch.)
        print(f'WARN: auto pattern YAML {yaml_path} failed to load — '
              f'falling back to main YAML only. Error: {e}', file=sys.stderr)
        return []


def load_patterns_with_auto(main_yaml_path):
    """Load main YAML (required) and merge sibling _auto.yaml (optional, agent-
    grown). Auto patterns are appended LAST so they have lower priority than
    curated main patterns when ranking matches.

    `<dir>/eco_fm_abort_patterns.yaml`        — main, engineer-curated
    `<dir>/eco_fm_abort_patterns_auto.yaml`   — appended by APPLY_ORCHESTRATOR
                                                after a reasoning-mode fix that
                                                ended in FM PASS. Each entry is
                                                a candidate the engineer may
                                                review and promote to main.
    """
    main = _load_one_yaml_safe(main_yaml_path, required=True)
    auto_path = Path(main_yaml_path).with_name(
        Path(main_yaml_path).stem + '_auto.yaml')
    auto = _load_one_yaml_safe(auto_path, required=False)
    if auto:
        print(f'eco_extract_fm_abort_cause.py: loaded {len(auto)} '
              f'auto-pattern(s) from {auto_path}', file=sys.stderr)
    return main + auto


# Active pattern table — main YAML (required) + auto YAML (optional, agent-grown).
# No hardcoded fallback. Auto patterns appended last → lower priority than curated.
ABORT_PATTERNS = load_patterns_with_auto(DEFAULT_PATTERN_YAML)


def _read_log(path):
    """Read a possibly-gzipped log file as text. Streams line-by-line and keeps
    only lines containing anchor tokens (FM-, FE-, SVR-, Error:, Warning,
    AMD-, Cannot, abort) plus 5 lines of context above each match. Bounds
    memory regardless of log size, but does NOT silently truncate the way the
    old 10 MB cap did.
    """
    ANCHOR = re.compile(r'(FM-\d|FE-\w|SVR-\d|^\s*Error:|^\s*Warning|AMD-|'
                        r'Cannot|abort|License|Out of memory|Segmentation)',
                        re.IGNORECASE)
    try:
        opener = (lambda p: gzip.open(p, 'rt', errors='replace')) \
                 if str(path).endswith('.gz') \
                 else (lambda p: open(p, 'r', errors='replace'))
        kept = []           # list of (line_no, line_text)
        ring = []           # last 5 lines (context buffer)
        last_kept_idx = -1
        with opener(path) as f:
            for i, line in enumerate(f, start=1):
                ring.append((i, line))
                if len(ring) > 5:
                    ring.pop(0)
                if ANCHOR.search(line):
                    # Flush context (deduped against already-kept)
                    for ln, lt in ring:
                        if ln > last_kept_idx:
                            kept.append((ln, lt))
                            last_kept_idx = ln
        # Re-assemble as a single text blob with original line numbers
        # preserved by sentinel comments — classify() doesn't need line nums
        # but evidence reporting uses them via _line_for_match()
        return ''.join(lt for _, lt in kept)
    except Exception as e:
        print(f'WARN: cannot read log {path}: {e}', file=sys.stderr)
        return ''


def _find_log_path(logs_dir, target):
    """Search order for a per-target FM log. Returns first existing path or None.
    Order matches eco_fm_runner.md §STEP E search list."""
    candidates = [
        Path(logs_dir) / f'{target}.log.gz',
        Path(logs_dir) / f'{target}.log',
        Path(logs_dir) / f'{target}.log.bz2',
        # Also check rpts/<target>/formality.log.{gz,} — some flows write here
        Path(logs_dir).parent / 'rpts' / target / 'formality.log.gz',
        Path(logs_dir).parent / 'rpts' / target / 'formality.log',
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def classify(log_text, target):
    """Return list of {pattern_kind, abort_type, severity, suggested_action,
    match_groups, log_excerpt} for every pattern that fires in the log."""
    hits = []
    for pat, abort_type, kind, action_template in ABORT_PATTERNS:
        for m in pat.finditer(log_text):
            # Extract a small context window around the match (3 lines before, 3 after)
            line_start = log_text.rfind('\n', 0, m.start()) + 1
            line_end   = log_text.find('\n', m.end())
            if line_end == -1:
                line_end = len(log_text)
            # Pull surrounding 3 lines on each side
            before_starts = [m.start()]
            cur = line_start
            for _ in range(3):
                prev_nl = log_text.rfind('\n', 0, cur - 1)
                if prev_nl == -1:
                    break
                before_starts.append(prev_nl + 1)
                cur = prev_nl + 1
            ex_start = before_starts[-1]
            after_ends = [line_end]
            cur = line_end
            for _ in range(3):
                next_nl = log_text.find('\n', cur + 1)
                if next_nl == -1:
                    break
                after_ends.append(next_nl)
                cur = next_nl
            ex_end = after_ends[-1]
            excerpt = log_text[ex_start:ex_end]
            match_str = m.group(1) if m.lastindex else ''
            hits.append({
                'target':           target,
                'abort_type':       abort_type,
                'pattern_kind':     kind,
                'match':            match_str,
                'suggested_action': action_template.format(match=match_str or '<see excerpt>'),
                'log_excerpt':      excerpt.strip()[:600],
            })
    # Dedupe by (target, abort_type, pattern_kind, match) — keep first
    seen = set(); uniq = []
    for h in hits:
        key = (h['target'], h['abort_type'], h['pattern_kind'], h['match'])
        if key in seen:
            continue
        seen.add(key); uniq.append(h)
    return uniq


def _is_abort(value):
    """Return True if a per-target status value indicates ABORT.
    Accepts THREE schemas:
      1. Legacy string:   "ABORT"
      2. Legacy dict:     {"status": "ABORT", ...}
      3. New v1 schema:   {"verdict": "ABORT_NETLIST" | "ABORT_LINK" | ...}
    Used to detect ABORT regardless of which schema upstream wrote."""
    if isinstance(value, dict):
        if value.get('status') == 'ABORT':
            return True
        verdict = value.get('verdict', '')
        if isinstance(verdict, str) and verdict.startswith('ABORT'):
            return True
        return False
    return str(value).strip().upper() == 'ABORT'


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--fm-verify',  required=True, help='eco_fm_verify.json from Step 6')
    p.add_argument('--logs-dir',   required=True, help='REF_DIR/logs containing FmEqv*.log.gz')
    p.add_argument('--tag',        required=True)
    p.add_argument('--round',      type=int, default=1)
    p.add_argument('--output',     required=True, help='Output JSON with classification')
    p.add_argument('--update-round-handoff', default='',
                   help='Optional path to round_handoff.json — script merges '
                        'remediation_hint + abort_classification into it.')
    p.add_argument('--pattern-yaml', default=str(DEFAULT_PATTERN_YAML),
                   help='Override path to eco_fm_abort_patterns.yaml (rare).')
    args = p.parse_args()

    # Allow CLI override of pattern source. If overridden, reload.
    global ABORT_PATTERNS
    if args.pattern_yaml and Path(args.pattern_yaml) != DEFAULT_PATTERN_YAML:
        ABORT_PATTERNS = load_patterns_from_yaml(args.pattern_yaml)

    fm_verify = json.loads(Path(args.fm_verify).read_text())

    # ── ABORT detection — check ALL status fields, not just one ─────────────
    # Bug history: prior version exited early when top-level
    # `status` / `overall_status` was anything but exactly "ABORT". Run
    # 20260512070625's eco_fm_runner wrote `overall_status="FM_FAILED"` even
    # though every per-target was ABORT, so the classifier said "no ABORT
    # detected" and the flow burned 3 rounds chasing the wrong cause.
    #
    # New rule: ABORT is detected if ANY of these is true:
    #   1. Top-level `status` == "ABORT"
    #   2. Top-level `overall_status` == "ABORT"
    #   3. Any per-target dict has `status` == "ABORT"
    #   4. Any per-target string value == "ABORT"
    # Otherwise treat as no-ABORT — but ALWAYS still grep the per-target
    # logs so we have evidence for FAIL diagnosis too (cheap; ~1s per target).
    # New v1 schema (eco_fm_status_collector.py output) has top-level `verdict`
    # AND a nested `per_target` dict; legacy schema has top-level `status` /
    # `overall_status` and per-target dicts at the top level.
    top_verdict_v1 = fm_verify.get('verdict', '')
    top_status_raw = (fm_verify.get('status')
                      or fm_verify.get('overall_status')
                      or top_verdict_v1
                      or '')

    if 'per_target' in fm_verify and isinstance(fm_verify['per_target'], dict):
        targets_status = fm_verify['per_target']
    else:
        targets_status = {k: v for k, v in fm_verify.items()
                          if k.startswith('FmEqv')}

    abort_targets = [t for t, v in targets_status.items() if _is_abort(v)]
    detected_abort = (top_status_raw == 'ABORT'
                      or top_verdict_v1.startswith('ABORT')
                      or bool(abort_targets))

    classifications = []
    for target, value in targets_status.items():
        # Always read the log if the target ABORTed. (Earlier behavior gated
        # on top-level — this layer now decides per-target.)
        if not _is_abort(value):
            continue
        log_path = _find_log_path(args.logs_dir, target)
        if log_path is None:
            classifications.append({
                'target':     target,
                'abort_type': 'ABORT_OTHER',
                'note':       f'no per-target log found in any expected location '
                              f'(<logs_dir>/{target}.log[.gz|.bz2], '
                              f'<ref>/rpts/{target}/formality.log[.gz])',
            })
            continue
        log_text = _read_log(log_path)
        hits = classify(log_text, target)
        if hits:
            classifications.extend(hits)
        else:
            classifications.append({
                'target':     target,
                'abort_type': 'ABORT_OTHER',
                'pattern_kind': 'unknown',
                'note':       'log read but no known YAML pattern matched — '
                              'add a new pattern entry to eco_fm_abort_patterns.yaml',
                'log_path':   log_path,
            })

    # ── Branch: no ABORT anywhere → write a clean no-op result ──────────────
    if not detected_abort:
        out = {
            'tag':                args.tag,
            'round':              args.round,
            'status':             top_status_raw or 'UNKNOWN',
            'detected_abort':     False,
            'note':               'No ABORT detected in top-level status or any '
                                  'per-target status — classification skipped.',
            'targets_seen':       sorted(targets_status.keys()),
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f'eco_extract_fm_abort_cause.py: top_status={top_status_raw!r} '
              f'no per-target ABORT — wrote {args.output}')
        return 0

    # Aggregate verdict — pick the most-frequent abort_type as primary
    from collections import Counter
    type_counts = Counter(c.get('abort_type', 'ABORT_OTHER') for c in classifications)
    primary_abort_type = type_counts.most_common(1)[0][0] if type_counts else 'ABORT_OTHER'

    # Build remediation hints (deduped by suggested_action)
    seen_actions = set()
    remediation_hints = []
    for c in classifications:
        action = c.get('suggested_action', '')
        if action and action not in seen_actions:
            seen_actions.add(action)
            remediation_hints.append(action)

    out = {
        'tag':                args.tag,
        'round':              args.round,
        'status':             'ABORT',
        'detected_abort':     True,
        'top_status_raw':     top_status_raw,
        'primary_abort_type': primary_abort_type,
        'targets_aborted':    sorted(abort_targets),
        'classifications':    classifications,
        'remediation_hints':  remediation_hints,
        # ABORT NEVER triggers re-study (Step 1/2/3) — per ROUND_ORCHESTRATOR.md
        # line 50 ('ABORT verdict MUST NOT trigger re-study or eco_netlist_port_rewire
        # re-run. Only netlist patches that fix the elaboration error.'). The
        # restart point is always Step 5 (re-validate after patch) → Step 6
        # (resubmit FM), within the same round counter.
        'restart_from_step':  5,
        'rerun_same_round':   True,
        'loop_verdict':       'RERUN_SAME_ROUND',
    }
    Path(args.output).write_text(json.dumps(out, indent=2))

    # Optionally merge into round_handoff.json so the next-round orchestrator
    # has the hint without re-running this script
    if args.update_round_handoff and Path(args.update_round_handoff).is_file():
        try:
            handoff = json.loads(Path(args.update_round_handoff).read_text())
        except Exception:
            handoff = {}
        handoff['fm_abort_classification'] = out
        handoff['loop_verdict']            = 'RERUN_SAME_ROUND'
        Path(args.update_round_handoff).write_text(json.dumps(handoff, indent=2))

    print(f'eco_extract_fm_abort_cause.py: ABORT classified as '
          f'{primary_abort_type} ({len(classifications)} hits across '
          f'{len(set(c["target"] for c in classifications))} targets)')
    for h in remediation_hints:
        print(f'  → {h[:120]}')
    print(f'  output: {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
