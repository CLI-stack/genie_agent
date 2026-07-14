#!/usr/bin/env python3
"""Shared FM-target helpers — project-portable (plain + UPF naming).

The ECO flow constantly needs to answer two questions about Formality target
names:

  1. "Which pipeline stage does this target check?"  -> target_to_stage()
  2. "What are the 3 target names for this phase in this tile?" -> detect_targets()

Historically both were answered with hardcoded literal names (e.g.
'FmEqvPreEcoSynthesizeVsPreEcoSynRtl'). That breaks on projects that insert an
infix — notably UPF power-aware designs (soundwave), whose targets look like
'FmEqvPwrAllUpfSuppliesOnPreEcoSynthesizeVsPreEcoSynRtl'. These helpers key on
the STABLE stage-revealing suffix instead of the full literal, so they work for
konark (plain) and soundwave (UPF) identically, and for any future infix.

Two phases exist and are DIFFERENT target sets:
  * PreEco  — the baseline netlist<->RTL equivalence, queried by Step 2
              (find_equivalent_nets) to resolve per-stage net names.
  * Eco     — the post-ECO verification, run by Step 6 (post_eco_formality).

Suffix rules (mutually exclusive, infix-agnostic):
  ...SynthesizeVs<any>SynRtl     -> Synthesize
  ...PrePlaceVs<any>Synthesize   -> PrePlace
  ...RouteVs<any>PrePlace        -> Route

Phase discriminator: a PreEco target name contains the substring 'PreEco';
an Eco target name contains 'Eco' but NOT 'PreEco'.
"""

# NOTE: intentionally no `from __future__ import annotations` — this module is
# invoked by find_equivalent_nets.csh under the TileBuilder cpd.cshrc env, whose
# python3 is 3.6.8 (the annotations future feature requires 3.7+). No PEP 604/585
# annotation syntax is used here, so the import is unnecessary. Keep it removed.
import os
import re

# Canonical fallbacks (plain, non-UPF) — used only when rpts/ cannot be scanned.
_FALLBACK = {
    "PreEco": [
        "FmEqvPreEcoSynthesizeVsPreEcoSynRtl",
        "FmEqvPreEcoPrePlaceVsPreEcoSynthesize",
        "FmEqvPreEcoRouteVsPreEcoPrePlace",
    ],
    "Eco": [
        "FmEqvEcoSynthesizeVsSynRtl",
        "FmEqvEcoPrePlaceVsEcoSynthesize",
        "FmEqvEcoRouteVsEcoPrePlace",
    ],
}

# Stage suffix patterns (anchored at end of the target name).
_STAGE_SUFFIX = [
    (re.compile(r"SynthesizeVs\w*SynRtl$"),   "Synthesize"),
    (re.compile(r"PrePlaceVs\w*Synthesize$"), "PrePlace"),
    (re.compile(r"RouteVs\w*PrePlace$"),      "Route"),
]

_STAGE_ORDER = ("Synthesize", "PrePlace", "Route")


def target_to_stage(name):
    """Return 'Synthesize' | 'PrePlace' | 'Route' | None for any FmEqv* target
    name, ignoring project infixes (e.g. 'PwrAllUpfSuppliesOn') and phase
    (PreEco vs Eco). Suffix rules are mutually exclusive."""
    if not name:
        return None
    for rx, stage in _STAGE_SUFFIX:
        if rx.search(name):
            return stage
    return None


def _is_phase(name, phase):
    """True if `name` belongs to `phase` ('PreEco' or 'Eco')."""
    has_pre = "PreEco" in name
    if phase == "PreEco":
        return has_pre
    # 'Eco' phase = an Eco target that is NOT a PreEco target
    return ("Eco" in name) and not has_pre


def _scan_targets(dir_path, phase, strip_suffix=""):
    """Scan one directory for FmEqv* entries of `phase`, returning a
    {stage: target_name} map. `strip_suffix` (e.g. '.cmd') is removed from
    entry names before classification."""
    found = {}
    if not os.path.isdir(dir_path):
        return found
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return found
    for entry in entries:
        name = entry[:-len(strip_suffix)] if strip_suffix and entry.endswith(strip_suffix) else entry
        if not name.startswith("FmEqv"):
            continue
        if not _is_phase(name, phase):
            continue
        stage = target_to_stage(name)
        if not stage:
            continue
        if stage not in found or len(name) < len(found[stage]):
            found[stage] = name
    return found


def detect_targets(ref_dir, phase):
    """Return the [Synthesize, PrePlace, Route] FM target NAMES for `phase`
    ('PreEco' | 'Eco'), infix-tolerant (picks up UPF-named targets).

    Source priority:
      1. <ref_dir>/cmds/*.cmd  — authoritative, present for ALL targets from
         GenerateAllCommands even before a target has ever run. This matters
         for the Eco phase on the FIRST verify (no Eco rpts/ dirs exist yet).
      2. <ref_dir>/rpts/       — fallback if cmds/ is unavailable.
      3. Canonical plain names — final fallback (keeps konark identical and
         never returns an empty/short triple).
    """
    if phase not in ("PreEco", "Eco"):
        raise ValueError(f"phase must be 'PreEco' or 'Eco', got {phase!r}")

    found = _scan_targets(os.path.join(str(ref_dir), "cmds"), phase, strip_suffix=".cmd")
    # Fill any stages cmds/ missed from rpts/.
    if len(found) < len(_STAGE_ORDER):
        for stage, name in _scan_targets(os.path.join(str(ref_dir), "rpts"), phase).items():
            found.setdefault(stage, name)

    fb = dict(zip(_STAGE_ORDER, _FALLBACK[phase]))
    return [found.get(stage, fb[stage]) for stage in _STAGE_ORDER]


def smart_eco_targets(ref_dir, applied_json, prev_verify_json):
    """Round-2+ SMART_TARGETS selection: pick the minimal Eco target set to
    re-run based on which stages the applier changed + which targets already
    passed. Returns a list of target names (falls back to the full triple if
    nothing selected). Mirrors the logic previously inlined in
    post_eco_formality.csh (extracted so the csh avoids a fragile multi-line
    backtick that some tcsh builds reject)."""
    import json
    T = detect_targets(ref_dir, "Eco")
    changed = {"Synthesize": 0, "PrePlace": 0, "Route": 0}
    try:
        d = json.load(open(applied_json))
        for s in changed:
            changed[s] = sum(1 for e in d.get(s, [])
                             if e.get("status") in ("APPLIED", "INSERTED"))
    except Exception:
        pass
    prev = {}
    try:
        pt = json.load(open(prev_verify_json)).get("per_target", {})
        prev = {t: (v.get("verdict") == "PASS")
                for t, v in pt.items() if isinstance(v, dict)}
    except Exception:
        pass
    targets = []
    if changed["Synthesize"] > 0 or not prev.get(T[0], False):
        targets.append(T[0])
    if changed["PrePlace"] > 0 or changed["Synthesize"] > 0 or not prev.get(T[1], False):
        targets.append(T[1])
    if changed["Route"] > 0 or changed["PrePlace"] > 0:
        targets.append(T[2])
    return targets if targets else T


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 5 and sys.argv[1] == "--smart-eco":
        # usage: eco_fm_targets.py --smart-eco <ref_dir> <applied_json> <prev_verify_json>
        print(" ".join(smart_eco_targets(sys.argv[2], sys.argv[3], sys.argv[4])))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--detect":
        # usage: eco_fm_targets.py --detect <ref_dir> <PreEco|Eco>
        print(",".join(detect_targets(sys.argv[2], sys.argv[3])))
    elif len(sys.argv) >= 3 and sys.argv[1] == "--stage":
        # usage: eco_fm_targets.py --stage <target_name>
        print(target_to_stage(sys.argv[2]) or "")
    else:
        print("usage: eco_fm_targets.py --detect <ref_dir> <PreEco|Eco>", file=sys.stderr)
        print("       eco_fm_targets.py --stage <target_name>", file=sys.stderr)
        sys.exit(2)
