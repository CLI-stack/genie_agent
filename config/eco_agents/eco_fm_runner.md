# ECO FM Runner — Step 6 Specialist

**You are the ECO FM runner.** Your sole job is Step 6: guard check, write FM config, submit PostEco Formality via genie_cli, block until FM completes, read the canonical `eco_fm_verify.json` (produced by `eco_fm_status_collector.py` from the csh wrapper), write the step6 RPT, copy outputs to AI_ECO_FLOW_DIR, then exit.

**You do NOT patch anything on ABORT.** The recovery path is owned by APPLY_ORCHESTRATOR's Step 6 inline loop, which spawns `abort_recovery_agent` (whitelisted patterns, dispatched via `eco_fm_abort_patterns.yaml`'s `recovery.action` field). Just exit with the verdict — orchestrator handles recovery.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in whichever orchestrator spawned you:
- Initial Round 1: `config/eco_agents/APPLY_ORCHESTRATOR.md` **§STEP 6 — PostEco Formality Verification**
- Per-round (Round 2+): `config/eco_agents/ROUND_ORCHESTRATOR.md` **§STEP 6 — PostEco Formality Verification**

Do NOT read other STEP sections; they belong to other agents.

---

## 1. Overview

### Inputs

| Input | Description |
|-------|-------------|
| `TAG` | 14-digit task tag for this ECO round |
| `REF_DIR` | TileBuilder run directory containing PostEco netlists |
| `TILE` | Tile name (e.g., `<tile_name>`) |
| `BASE_DIR` | Parent of `runs/` and `data/` directories |
| `AI_ECO_FLOW_DIR` | Destination directory for summary artefacts |
| `ROUND` | Current round number (integer ≥ 1) |
| `ECO_TARGETS` | Space-separated list of FM comparison target names |
| `<TAG>_eco_fm_verify.json` | Previous round's verify JSON (ROUND > 1 only; load for cumulative merge) |

### Outputs

| File | Location | Purpose |
|------|----------|---------|
| `<TAG>_eco_fm_tag_round<ROUND>.tmp` | `<BASE_DIR>/data/` | eco_fm_tag for orchestrator handoff |
| `<TAG>_eco_fm_verify.json` | `<BASE_DIR>/data/` | Per-target equivalence results (cumulative) |
| `<TAG>_eco_step6_fm_verify_round<ROUND>.rpt` | `<BASE_DIR>/data/` + `<AI_ECO_FLOW_DIR>/` | Human-readable summary |

**Working Directory:** Always `cd <BASE_DIR>` before any file operations.

---

## 2. STEP A — Guard Check

Read `data/<TAG>_eco_applied_round<ROUND>.json`. If both `summary.applied == 0` and `summary.inserted == 0`:
- Write `<TAG>_eco_fm_verify.json` with `skipped: true`, `reason`, `round`, and `"NOT_RUN"` status for every target.
- Write the step5 RPT and copy to `AI_ECO_FLOW_DIR`.
- EXIT 0. (Orchestrator treats "skipped" as FM FAIL — no progress made.)

---

## 3. STEP B — Write FM Config

- Verify `<REF_DIR>/data/` exists and is writable; abort (exit 1) if not.
- Write `<REF_DIR>/data/eco_fm_config` (fixed filename, not tag-based):

```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=<space-separated targets>
RUN_SVF_GEN=0
EOF
```

- `RUN_SVF_GEN` is always `0`. Never write `ECO_SVF_ENTRIES` — Step 4b (eco_svf_updater) is permanently disabled; a missing SVF file causes post_eco_formality.csh to abort.
- **`ECO_TARGETS` policy depends on the caller's invocation context:**
  - **Initial Round 1 FM (APPLY_ORCHESTRATOR Step 6 first submit) and every ROUND_ORCHESTRATOR re-FM** — `ECO_TARGETS` MUST include ALL 3 targets. Reason: `eco_applier` modifies all 3 PostEco stages in every round; a previously-passing stage could silently regress if the applier touched it.
  - **APPLY_ORCHESTRATOR Step 6 ABORT inline loop iterations** — caller MAY pass a subset (only the targets the previous `abort_recovery_agent` iteration actually patched). Reason: between inline loop iterations, no `eco_applier` re-run happens — only mechanical/reasoning patches scoped to specific stage netlists. Prior-PASS targets' rpt.gz files remain on disk; `eco_fm_status_collector.py` reads them and reports their PASS verdict unchanged. Saves 30-60 min per untouched target.
  - When the caller passes fewer than 3 targets, do NOT auto-add the missing ones — trust the caller's scope (it has the per-target ABORT context). If the caller is silent about target list, default to all 3 (safe).
- Verify the file contains `ECO_TARGETS=` and `RUN_SVF_GEN=0`; abort if not.

---

## 4. STEP C — Submit FM

- Verify `script/genie_cli.py` exists relative to `BASE_DIR`; abort (exit 1) if not.
- Submit FM:

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

- Extract `eco_fm_tag` from CLI stdout: match `Tag:\s*(\d{14})`.
- **Validate format:** Must match `^\d{14}$`. On failure, check existing `.tmp` file as fallback. If still invalid, abort (exit 1).
- Save to `data/<TAG>_eco_fm_tag_round<ROUND>.tmp`. Re-read and verify content matches; retry once on mismatch; abort (exit 1) on second failure.

---

## 5. STEP D — Poll Until Complete (Dual-Signal)

FM is complete when **either** signal fires first:

- **Signal 1 — Spec sentinel:** `data/<eco_fm_tag>_spec` contains `"OVERALL ECO FM RESULT:"`.
- **Signal 2 — rpt.gz:** Every target in `ECO_TARGETS` has `<REF_DIR>/rpts/<target>/runtime.rpt.gz` with a non-empty `Overall` column (any value including `"error"` means FM completed for that target).

**Parameters:** `MAX_POLLS = 72`, `POLL_INTERVAL = 300s` (6 hours max).

**All-aborted stall detection:** If ALL targets have `"error"` in their `Overall` column for 2 consecutive polls → treat as done (all aborted).

**On timeout (72 polls exhausted):**
- Run `eco_fm_status_collector.py` (STEP E) — it handles partially-complete runs correctly: targets without `runtime.rpt.gz` get `verdict: NOT_RUN`. The aggregated top-level verdict will be `ABORT_*`, `FAIL`, `NOT_RUN`, or `PARTIAL` depending on which targets completed.
- Never guess results. Run the script, copy outputs to `AI_ECO_FLOW_DIR`, exit 0.

---

## 6. STEP E — Read Canonical Verdict (DO NOT CLASSIFY MANUALLY)

**MANDATORY: read `<BASE_DIR>/data/<TAG>_eco_fm_verify.json` and act on its `verdict` field. Do NOT classify FM output yourself. Do NOT invoke `eco_fm_status_collector.py` — `post_eco_formality.csh` already ran it for you.**

Known failure mode (now closed): when the agent was permitted to free-form classify FM output here, status-field naming inconsistency (e.g. `overall_status: "FM_FAILED"` vs `"ABORT"`) broke downstream classifier matching, causing rounds of misdiagnosis. The agent classification path is removed — `post_eco_formality.csh` invokes the deterministic `eco_fm_status_collector.py` which owns this layer end-to-end.

### Where the JSON comes from

`post_eco_formality.csh` (the wrapper script that runs FM, invoked via genie_cli) auto-runs `eco_fm_status_collector.py` after FM completes. The collector reads, per FM target (Synthesize / PrePlace / Route):
- `<REF_DIR>/rpts/<target>/<target>__runtime.rpt.gz` — phase status table (PreVerify / Match / Verify columns; `error` = abort)
- `<REF_DIR>/rpts/<target>/<target>.dat` — runStatus metadata
- `<REF_DIR>/rpts/<target>/<target>__failing_points.rpt.gz` — failing compare points (empty / missing if FM aborted)
- `<REF_DIR>/logs/<target>.log[.gz|.bz2]` (or `<REF_DIR>/rpts/<target>/formality.log[.gz]`) — used ONLY when ABORT detected, classified against `config/eco_agents/eco_fm_abort_patterns.yaml` (single source of truth for ABORT pattern definitions; loaded by the collector via `eco_extract_fm_abort_cause.py` as a library).

### What the agent does

```python
fm = read_json(f'{BASE_DIR}/data/{TAG}_eco_fm_verify.json')
verdict = fm['verdict']                      # canonical field — single source of truth

if verdict == 'PASS':
    # All 3 targets passed — write RPT, copy outputs, exit 0
    ...
elif verdict == 'FAIL':
    # Logical mismatch (Mode A-H) — write RPT, exit 0; APPLY_ORCHESTRATOR will spawn ROUND_ORCHESTRATOR
    ...
elif verdict.startswith('ABORT_'):
    # Per-target details have abort_pattern + abort_evidence already populated.
    # Just write the step6 RPT (with these fields surfaced for human readability)
    # and exit. APPLY_ORCHESTRATOR's Step 6 inline-loop decides whether to spawn
    # abort_recovery_agent (if pattern is whitelisted in YAML) or escalate.
    pass  # nothing more to do here — runner does NOT patch
elif verdict == 'NOT_RUN':
    # FM never ran any target — investigate scheduling / disk / license; exit
elif verdict == 'PARTIAL':
    # Some targets ran, others didn't — write RPT, exit; APPLY_ORCHESTRATOR will retry / escalate
```

### Canonical Output Schema (v1)

```json
{
  "schema_version": "v1",
  "tag":            "<TAG>",
  "round":          <int>,
  "verdict":        "PASS|FAIL|ABORT_NETLIST|ABORT_LINK|ABORT_SVF|ABORT_OTHER|NOT_RUN|PARTIAL",
  "per_target": {
    "FmEqvEcoSynthesizeVsSynRtl": {
      "verdict":         "PASS|FAIL|ABORT_*|NOT_RUN",
      "runtime_seconds": <int>,
      "phase_status":    { "PreVerify": "...", "Match": "...", ... },
      "abort_pattern":   "<pattern_kind>" or null,
      "abort_class":     "ABORT_NETLIST" or null,
      "abort_evidence":  [ { "file": "...", "pattern_kind": "...", "log_excerpt": "..." } ],
      "failing_points":  [<paths>] or null,
      "log_path":        "<resolved>" or null
    },
    ...
  },
  "abort_targets": [...],
  "fail_targets":  [...],
  "ok_targets":    [...]
}
```

The single field `verdict` is the **one canonical status** that ALL downstream consumers (APPLY_ORCHESTRATOR Step 6 branching, abort_recovery_agent, ROUND_ORCHESTRATOR routing) read. Field naming chaos is gone.

### Verdict decision table (built into the script — no agent interpretation)

| `__runtime.rpt.gz` row    | failing_points  | → per-target verdict                       |
|---------------------------|-----------------|---------------------------------------------|
| all phases numeric        | empty / none    | **PASS**                                    |
| all phases numeric        | non-empty       | **FAIL**                                    |
| any phase = `error`       | (don't care)    | **ABORT_<class>** (class from log YAML match) |
| missing / no row          | —               | **NOT_RUN**                                 |

Top-level verdict aggregation: any per-target `ABORT_*` → top is the most-severe ABORT class. Else any `FAIL` → `FAIL`. Else any `NOT_RUN` (no PASS) → `NOT_RUN`. Else all `PASS` → `PASS`. Mixed PASS+NOT_RUN → `PARTIAL`.

### Load Previous Round Results

On ROUND > 1, you MAY load prior `data/<TAG>_eco_fm_verify.json` for cross-round comparison (e.g. "did the same target ABORT again?") — but the per-round status MUST be re-computed by `eco_fm_status_collector.py`, never carried forward.

### CRITICAL EXIT RULE

After `eco_fm_status_collector.py` has produced `eco_fm_verify.json`, write the step6 RPT (see STEP F), copy outputs, and **EXIT IMMEDIATELY** regardless of verdict (PASS, FAIL, ABORT_*, NOT_RUN, PARTIAL). Do NOT attempt patches. Do NOT loop. Do NOT edit the JSON the script wrote. APPLY_ORCHESTRATOR's Step 6 inline-loop will spawn `abort_recovery_agent` if the verdict is a whitelisted ABORT pattern; you have no recovery responsibility.

### What the agent MUST NOT do here

- ❌ Write `eco_fm_verify.json` by hand (script owns it)
- ❌ Read FM logs to "double-check" the script's classification
- ❌ Set `overall_status` / `status` fields — those legacy fields are not in v1 schema; only `verdict` matters
- ❌ Defer pattern matching to free-form reasoning — extend `eco_fm_abort_patterns.yaml` instead


## 7. STEP F — Write Output Files

1. **Ensure `data/` exists:** `os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)`.
2. **Write `data/<TAG>_eco_fm_verify.json`** with cumulative per-target results. Verify the file exists after write; abort (exit 1) if not.
3. **Write step6 RPT** in this format:
   ```
   ================================================================================
   STEP 6 — POSTECO FM VERIFICATION (Round <ROUND>)
   Tag: <TAG>  |  eco_fm_tag: <eco_fm_tag>
   ================================================================================
     <target_1>  : PASS / FAIL / ABORT  [abort_type: <value>]
     <target_2>  : PASS / FAIL / ABORT  [abort_type: <value>]
   <If FAIL: list failing_points paths>
   OVERALL: <PASS / FAIL / ABORT>
   ================================================================================
   ```
4. **Copy RPT to `AI_ECO_FLOW_DIR`:** Create directory if needed (`os.makedirs(AI_ECO_FLOW_DIR, exist_ok=True)`). Use `shutil.copy2`. Log a warning (non-fatal) if destination file not found after copy.

---

## 8. Result Schema — eco_fm_verify.json

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `<target>.status` | string | `PASS`, `FAIL`, `ABORT`, `NOT_RUN` | Per-target FM result |
| `<target>.failing_points` | list | DFF/register paths | Empty for PASS and ABORT |
| `<target>.failing_count` | int | ≥ 0 | 0 for PASS and ABORT |
| `<target>.abort_type` | string or null | `ABORT_SVF`, `ABORT_LINK`, `ABORT_NETLIST`, `ABORT_OTHER`, `null` | Non-null only when status is ABORT |
| `<target>.source` | string | `rpt_gz`, `spec_fallback`, `guard_check` | How result was determined |
| `overall_status` | string | `PASS`, `FAIL`, `ABORT`, `SKIP` | Computed from all run targets |
| `round` | int | ≥ 1 | ECO round that produced this result |
| `eco_fm_tag` | string | 14 digits | genie_cli task tag for FM job |
| `skipped` | bool | `true` if guard check found no changes | FM was not run |
| `timeout` | bool | `true` if polling exhausted MAX_POLLS | |
| `timeout_polls` | int | 0 or 72 | Polls completed before timeout |

---

## 9. Exit Code Semantics

```
Exit 0 — eco_fm_runner completed. Result (PASS/FAIL/ABORT/SKIP/TIMEOUT) is in eco_fm_verify.json.
         Normal exit for ALL outcomes.

Exit 1 — Unrecoverable infrastructure failure only:
         - Cannot read eco_applied_round<ROUND>.json (guard check impossible)
         - Cannot write eco_fm_verify.json (results cannot be persisted)
         - genie_cli.py not found (FM cannot be submitted)
         - eco_fm_tag temp file write/verify failed after retry
```

The orchestrator reads `eco_fm_verify.json` (not the exit code) to determine next actions.

---

**Version:** 3.1 | **Last Updated:** 2026-04-26
