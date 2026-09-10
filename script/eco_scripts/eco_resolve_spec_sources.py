#!/usr/bin/env python3
"""eco_resolve_spec_sources.py — resolve current SPEC_SOURCES per stage.

Walks <BASE_DIR>/data/ for all per-tag spec files (initial + retries +
re-runs), groups them by ECO stage, and emits the latest-spec-per-stage
mapping as JSON. Used by ROUND_ORCHESTRATOR Step 6f-FENETS to refresh
SPEC_SOURCES before passing to eco_netlist_re_studier — prevents stale
spec files from being read after a fenets re-run.

Output: <BASE_DIR>/data/<TAG>_eco_spec_sources_round<N>.json
{
  "round": N,
  "spec_sources": {
    "Synthesize": "data/<latest_spec_file>",
    "PrePlace":   "data/<latest_spec_file>",
    "Route":      "data/<latest_spec_file>"
  },
  "discovered": [<all spec file paths considered, newest first>]
}

CLI:
    python3 eco_resolve_spec_sources.py --tag <TAG> --round <N> --base-dir <BASE_DIR>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Stage detection from spec filename or content header.
STAGE_RE = re.compile(r"\b(Synthesize|PrePlace|Route)\b")


def discover_specs(base_dir: Path, tag: str) -> list[Path]:
    """All spec files for this tag, newest first by mtime."""
    candidates = []
    # Search flat in base_dir first
    candidates += list(base_dir.glob(f"{tag}_*spec"))
    candidates += list(base_dir.glob(f"{tag}_*spec.gz"))
    # Also check base_dir/data for legacy runs
    data = base_dir / "data"
    if data.is_dir():
        candidates += list(data.glob(f"{tag}_*spec"))
        candidates += list(data.glob(f"{tag}_*spec.gz"))
    # Common spec naming variants used across the flow:
    #   <fenets_tag>_spec, <fenets_tag>_spec_rerun_round<N>, <retry_tag>_spec_retry<N>
    return sorted(candidates, key=lambda p: -p.stat().st_mtime)


def stage_of(spec: Path) -> str | None:
    """Detect stage from filename or first non-empty content line."""
    name_match = STAGE_RE.search(spec.name)
    if name_match:
        return name_match.group(1)
    try:
        # Read first ~1KB to find a stage header
        with spec.open() as f:
            head = f.read(1024)
        m = STAGE_RE.search(head)
        return m.group(1) if m else None
    except OSError:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tag", required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--base-dir", required=True)
    p.add_argument("--ref-dir", default=None)
    p.add_argument("--output", default=None)
    args = p.parse_args()

    base_dir = Path(args.base_dir)
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = (
            base_dir / f"{args.tag}_eco_spec_sources_round{args.round}.json"
            if (base_dir / f"{args.tag}_round_handoff.json").exists() or not (base_dir / "data").is_dir()
            else base_dir / "data" / f"{args.tag}_eco_spec_sources_round{args.round}.json"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine active stages from ref_dir if available
    active_stages = ["Synthesize"]
    if args.ref_dir and Path(args.ref_dir).is_dir():
        preeco_dir = Path(args.ref_dir) / "data" / "PreEco"
        if (preeco_dir / "PrePlace.v.gz").is_file() or (preeco_dir / "PrePlace.v").is_file():
            active_stages.append("PrePlace")
        if (preeco_dir / "Route.v.gz").is_file() or (preeco_dir / "Route.v").is_file():
            active_stages.append("Route")
    else:
        active_stages = ["Synthesize", "PrePlace", "Route"]

    discovered = discover_specs(base_dir, args.tag)
    spec_sources: dict[str, str] = {}
    for spec in discovered:
        st = stage_of(spec)
        if st and st not in spec_sources:
            spec_sources[st] = str(spec)

    out = {
        "round": args.round,
        "tag": args.tag,
        "spec_sources": spec_sources,
        "discovered": [str(p) for p in discovered],
    }
    out_path.write_text(json.dumps(out, indent=2))
    print(f"ECO_RPT_GENERATED: spec sources → {out_path}")
    for stg in active_stages:
        print(f"  {stg:11s}: {spec_sources.get(stg, '<NONE>')}")
    if not all(s in spec_sources for s in active_stages):
        print(f"WARN: not all active stages ({active_stages}) have a spec file resolved")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
