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

Two phases exist and are DIFFERENT target sets on projects that name them that
way:
  * PreEco  — the baseline netlist<->RTL equivalence, queried by Step 2
              (find_equivalent_nets) to resolve per-stage net names.
  * Eco     — the post-ECO verification, run by Step 6 (post_eco_formality).

Suffix rules (mutually exclusive, infix-agnostic):
  ...SynthesizeVs<any>SynRtl     -> Synthesize
  ...PrePlaceVs<any>Synthesize   -> PrePlace
  ...RouteVs<any>PrePlace        -> Route

Phase discriminator: a PreEco target name contains the substring 'PreEco';
an Eco target name contains 'Eco' but NOT 'PreEco'.

A THIRD convention exists too: some projects don't distinguish PreEco/Eco by
name at all — the tile has ONE target per stage (e.g. 'FmEqvSynthesizeVsSynRtl'),
re-run/re-pointed at whatever netlist content is current, used for BOTH the
Step 2 baseline resolution AND the Step 6 post-ECO check (confirmed real-world:
some tiles have no PreEco/Eco-infixed targets at all — only this
bare form). This is phase 'Bare': a target matching a valid stage suffix but
containing NEITHER 'PreEco' NOR 'Eco'. `detect_targets()` treats a real 'Bare'
target found on disk as a valid answer for EITHER phase request when no
phase-infixed target exists for that stage — a real target beats a fictional
canonical guess.
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
    "Bare": [
        "FmEqvSynthesizeVsSynRtl",
        "FmEqvPrePlaceVsSynthesize",
        "FmEqvRouteVsPrePlace",
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
    """True if `name` belongs to `phase` ('PreEco' | 'Eco' | 'Bare')."""
    has_pre = "PreEco" in name
    has_eco = "Eco" in name and not has_pre
    if phase == "PreEco":
        return has_pre
    if phase == "Eco":
        return has_eco
    # 'Bare' = neither infix present at all (e.g. 'FmEqvSynthesizeVsSynRtl') —
    # some projects use one un-phased target for both PreEco and Eco purposes.
    return not has_pre and not has_eco


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


def detect_targets(ref_dir, phase, stages=None):
    """Return the [Synthesize, PrePlace, Route] FM target NAMES for `phase`
    ('PreEco' | 'Eco' | 'Bare'), infix-tolerant (picks up UPF-named targets).
    If `stages` is None, auto-detects active stages from <ref_dir>/data/PreEco.

    Source priority:
      1. <ref_dir>/cmds/*.cmd  — authoritative, present for ALL targets from
         GenerateAllCommands even before a target has ever run. This matters
         for the Eco phase on the FIRST verify (no Eco rpts/ dirs exist yet).
      2. <ref_dir>/rpts/       — fallback if cmds/ is unavailable.
      3. A real 'Bare' target on disk (no PreEco/Eco infix at all) for any
         stage the requested phase didn't find — some projects use ONE
         un-phased target for both PreEco and Eco purposes (confirmed
         real-world: some tiles have no phase-infixed targets at all).
         A real target beats a fictional canonical guess, regardless of
         which phase was actually requested.
      4. Canonical plain names for the REQUESTED phase — final fallback
         (keeps konark identical and never returns an empty/short triple).
    """
    if phase not in ("PreEco", "Eco", "Bare"):
        raise ValueError(f"phase must be 'PreEco', 'Eco', or 'Bare', got {phase!r}")

    if stages is None:
        stages = ["Synthesize"]
        preeco = os.path.join(str(ref_dir), "data", "PreEco")
        if os.path.isdir(preeco):
            if os.path.isfile(os.path.join(preeco, "PrePlace.v.gz")) or os.path.isfile(os.path.join(preeco, "PrePlace.v")):
                stages.append("PrePlace")
            if os.path.isfile(os.path.join(preeco, "Route.v.gz")) or os.path.isfile(os.path.join(preeco, "Route.v")):
                stages.append("Route")
        else:
            stages = list(_STAGE_ORDER)

    def _scan_both(phase_):
        f = _scan_targets(os.path.join(str(ref_dir), "cmds"), phase_, strip_suffix=".cmd")
        if len(f) < len(_STAGE_ORDER):
            for stage, name in _scan_targets(os.path.join(str(ref_dir), "rpts"), phase_).items():
                f.setdefault(stage, name)
        return f

    found = _scan_both(phase)
    # A real 'Bare' target on disk fills in any stage the requested phase
    # didn't find, before resorting to a fictional canonical guess — unless
    # the caller already asked for 'Bare' (nothing more to try).
    if phase != "Bare" and len(found) < len(_STAGE_ORDER):
        for stage, name in _scan_both("Bare").items():
            found.setdefault(stage, name)

    fb = dict(zip(_STAGE_ORDER, _FALLBACK[phase]))
    return [found.get(stage, fb[stage]) for stage in stages if stage in found or stage in fb]


def verify_session(fm_session_dir, target_name):
    """Verify that `target_name`'s FM session has actually been run and can be
    reopened (e.g. via `TileBuilderIntFM <target_name>`), regardless of whether that
    run passed or failed, and regardless of the target's name (no 'PreEco' substring
    required, and no inspection of what netlist it currently points at). Validity is
    simply: does a `<target_name>_{passed,failed}.fss` session file exist under
    `<fm_session_dir>/rpts/<target_name>/<target_name>_runData/`? If either exists,
    the session is invocable and usable for find_equivalent_nets.

    This replaces an earlier, more elaborate content/md5-based check that opened the
    target's .cmd and compared its netlist against a recorded PreEco baseline — that
    additional rigor turned out to be unnecessary: what Step 2 actually needs is a
    reopenable FM session, and the .fss file's mere existence already proves that.

    Returns a dict:
      result: "MATCH" (session exists, invocable) | "NO_STAGE" | "NO_FSS"
      stage: resolved pipeline stage ('Synthesize'|'PrePlace'|'Route'), or None
      fss_path: the found .fss path, or None
      status: "passed" | "failed" | None
    """
    stage = target_to_stage(target_name)
    if not stage:
        return {"result": "NO_STAGE", "stage": None, "fss_path": None, "status": None}

    run_dir = os.path.join(str(fm_session_dir), "rpts", target_name, target_name + "_runData")
    for status in ("passed", "failed"):
        fss = os.path.join(run_dir, target_name + "_" + status + ".fss")
        if os.path.isfile(fss):
            return {"result": "MATCH", "stage": stage, "fss_path": fss, "status": status}
    return {"result": "NO_FSS", "stage": stage, "fss_path": None, "status": None}


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
    t_syn = next((t for t in T if target_to_stage(t) == "Synthesize"), None)
    t_pp = next((t for t in T if target_to_stage(t) == "PrePlace"), None)
    t_rt = next((t for t in T if target_to_stage(t) == "Route"), None)

    if t_syn and (changed["Synthesize"] > 0 or not prev.get(t_syn, False)):
        targets.append(t_syn)
    if t_pp and (changed["PrePlace"] > 0 or changed["Synthesize"] > 0 or not prev.get(t_pp, False)):
        targets.append(t_pp)
    if t_rt and (changed["Route"] > 0 or changed["PrePlace"] > 0):
        targets.append(t_rt)
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
    elif len(sys.argv) >= 4 and sys.argv[1] == "--verify-session":
        # usage: eco_fm_targets.py --verify-session <fm_session_dir> <target_name>
        r = verify_session(sys.argv[2], sys.argv[3])
        print("RESULT=%s" % r["result"])
        print("STAGE=%s" % (r["stage"] or ""))
        print("FSS_PATH=%s" % (r["fss_path"] or ""))
        print("STATUS=%s" % (r["status"] or ""))
    else:
        print("usage: eco_fm_targets.py --detect <ref_dir> <PreEco|Eco>", file=sys.stderr)
        print("       eco_fm_targets.py --stage <target_name>", file=sys.stderr)
        print("       eco_fm_targets.py --verify-session <fm_session_dir> <target_name>", file=sys.stderr)
        sys.exit(2)
