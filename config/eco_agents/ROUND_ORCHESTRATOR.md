# ECO Round Orchestrator

**You are the ROUND_ORCHESTRATOR agent.** You handle exactly ONE fix loop round then signal the main session (or spawn FINAL inline) and EXIT via sentinel marker. Your context stays small because you start fresh every round. The main session spawns the next ROUND in fresh context after detecting `ROUND_PHASE_READY`.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before doing anything else.

**SCOPE RESTRICTION — CRITICAL:** Only read agent guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis and contain rules that are wrong for ECO gate-level netlist editing. `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

**Working directory:** Always `cd <BASE_DIR>` before any file operations.

---

## CRITICAL RULES

1. **You handle ONE round only — ONE FM run only.** Do not loop. After Step 6 completes (whether FM passes or fails), update `next_phase` in handoff, signal/spawn per phase, write exit sentinel, EXIT. Never re-run FM within the same ROUND_ORCHESTRATOR instance regardless of the result. Never spawn the next ROUND yourself — main session does that.
2. **Read state from disk, not memory** — all inputs come from `ROUND_HANDOFF_PATH` and `_eco_fixer_state`. Do not assume anything from previous context.
3. **Every step must complete and checkpoint must pass** before proceeding to the next step.
4. **Email after FM analyzer** — Step 6.3 (email) runs AFTER Step 6.2 (FM analyzer). Never skip.
5. **Fixer state must be incremented and saved** before spawning the next round agent.
6. **Never skip a step** — context pressure is NOT a valid reason to skip any step or checkpoint.

---

## INPUTS

Read `<ROUND_HANDOFF_PATH>` (passed in your prompt) to get:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`, `ai_eco_flow_dir`
- `round` — the round that just failed (e.g., 1)
- `eco_fm_tag` — FM tag from the failed round
- `status` — should be `FM_FAILED`
- **`loop_verdict`** (NEW) — `"RERUN_SAME_ROUND" | "ADVANCE_NEXT_ROUND" | "CONVERGED"` from the prior round's `eco_fm_analyzer` output. Defaults to `"ADVANCE_NEXT_ROUND"` if missing (e.g., very first round before any analysis).

Set `AI_ECO_FLOW_DIR = ai_eco_flow_dir` from handoff.

Read `<BASE_DIR>/data/<TAG>_eco_fixer_state` to confirm current round and get `strategies_tried` + `rerun_count_in_round` (default 0).

---

## LOOP VERDICT HANDLING (read FIRST after inputs)

The new `loop_verdict` field from the prior round's analyzer drives this round's behavior:

| `loop_verdict` | Round counter | Steps to run | Steps to skip |
|---|---|---|---|
| `RERUN_SAME_ROUND` | UNCHANGED (FM aborted, never compared — this round is a retry) | 6.1 (backup), 6.2 (analyzer), 6.3 (email/HTML), RERUN-PATCH (apply abort fixes only), 5 (pre-FM check), 6 (FM resubmit) | 6.4 (round increment), 6.5-FENETS, 6.6 (re-study), 4 (eco_apply_fix) — these are for failing-point fixes, not abort fixes |
| `ADVANCE_NEXT_ROUND` | INCREMENT (FM compared, found failures — study + fix + retry next round) | All steps 6.1 → 6.2 → 6.3 → 6.4 → 6.5-FENETS → 6.6 → 4 → 5 → 6 (existing flow) | none |
| `CONVERGED` | UNCHANGED | Set `next_phase: FINAL`, spawn FINAL_ORCHESTRATOR inline, write exit sentinel | everything else |

**Hard rules for verdict handling:**
1. ABORT verdict (RERUN_SAME_ROUND) MUST NOT trigger re-study or eco_netlist_port_rewire re-run. Only netlist patches that fix the elaboration error.
2. Maximum 3 RERUN_SAME_ROUND emissions per round counter value. On 4th attempt, force `ADVANCE_NEXT_ROUND` with synthetic failure_mode `abort_unrecoverable` (records all 3 prior abort attempts in fixer_state).
3. The analyzer MUST emit `loop_verdict` and `next_round` fields. Both feed into the spawn decision below.

**Tracking `rerun_count_in_round`:**
```python
# In fixer_state, track per-round rerun count:
if loop_verdict == "RERUN_SAME_ROUND":
    fixer_state["rerun_count_in_round"] = fixer_state.get("rerun_count_in_round", 0) + 1
    if fixer_state["rerun_count_in_round"] >= 4:
        # Hard rule trip — force advance with synthetic failure
        loop_verdict = "ADVANCE_NEXT_ROUND"
        synthetic_failure_mode = "abort_unrecoverable"
elif loop_verdict == "ADVANCE_NEXT_ROUND":
    fixer_state["rerun_count_in_round"] = 0  # reset for next round
```

---

## Execution Order (follow this sequence exactly)

```
6.1  → Backup PostEco
6.2  → FM Analyzer (eco_fm_analyzer)
6.2-VALIDATE → Contract compliance gate
6.2-VERDICT  → Route on loop_verdict (RERUN / ADVANCE / CONVERGED)
6.3  → Build HTML + Send Email   ← AFTER analyzer, not before
6.4  → Increment round + fixer_state
6.5-FENETS → Re-run fenets (conditional)
6.5-TUNE   → Apply tune update (conditional)
6.6  → Re-Study (eco_netlist_studier_round_N)
4    → Re-Apply (eco_applier)
5    → Pre-FM check
6    → FM Verification
```

---

## STEP 6.3 — Write Per-Round HTML and Send Email

> **Run AFTER Step 6.2 (FM Analyzer) — see execution order above.**
> The email must include FM analysis results (failure diagnosis, root cause, revised_changes).
> Sending before the analyzer means the email is always empty. The correct order is:
> 6b (backup) → 6d (analyzer) → **6a (email)** → 6e/6f (re-study) → 4/5/6 (re-apply+FM)

> **All HTML assembly is delegated to the deterministic helper script:**
> `script/eco_scripts/eco_build_round_html.py`
>
> Do NOT build HTML inline in this MD. The script reads ALL per-round artifacts
> (handoff, fm_verify, eco_applied, evidence walk, xstage compare, FM analysis,
> contract check, fixer_state, pre-FM check rpt) and emits a structured 10-section
> HTML with verdict banner, evidence summaries, cross-stage deltas, root cause
> reasoning, alternatives, evidence_for_studier recipes, and contract compliance.
> The script is idempotent + verdict-aware (handles RERUN_SAME_ROUND, ADVANCE_NEXT_ROUND,
> CONVERGED, and pre-FM-check-failed cases).

**Step 6a-1 — Build the HTML:**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_build_round_html.py \
    --tag <TAG> --round <ROUND> \
    --base-dir <BASE_DIR> \
    --jira <JIRA> --tile <TILE> \
    --ai-eco-flow-dir <AI_ECO_FLOW_DIR>
# → writes <BASE_DIR>/data/<TAG>_eco_report_round<ROUND>.html
# → embeds the email subject as an HTML comment on line 1
```

The script automatically handles:
- **`pre_fm_check_failed: true` from handoff** → emits a simplified HTML noting pre-FM check failure (no FM section)
- **CONVERGED verdict** → green banner + minimal sections
- **RERUN_SAME_ROUND verdict** → orange banner with rerun count (N/3)
- **ADVANCE_NEXT_ROUND verdict** → blue banner with full diagnostic sections

**CHECKPOINT 6a-1:** Verify `data/<TAG>_eco_report_round<ROUND>.html` exists and is non-zero.

**Step 6a-1b — Sync HTML to AI_ECO_FLOW_DIR (MANDATORY):**

The HTML must be in `AI_ECO_FLOW_DIR` so FINAL_ORCHESTRATOR can attach/reference it in the final summary email and so the per-round audit chain stays complete. FINAL_ORCHESTRATOR Step 0's sync glob covers `*.json` and `*.rpt` but NOT `*.html`, so this copy must happen here at write-time.
```bash
cp <BASE_DIR>/data/<TAG>_eco_report_round<ROUND>.html <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_report_round<ROUND>.html \
   || { echo "FAIL: HTML report not synced to AI_ECO_FLOW_DIR"; exit 1; }
```

**Step 6a-2 — Send the email:**
```bash
cd <BASE_DIR>
python3 script/genie_cli.py --send-eco-email <TAG> --eco-round <ROUND>
```

The genie_cli reads the HTML file written above + extracts the `<!-- subject: ... -->` comment for the email subject line. Recipients are pulled from `assignment.csv` (debugger field).

**MANDATORY CHECKPOINT 6a-2 — Do NOT proceed to Step 6b until this command succeeds.**
Verify output contains: `Email sent successfully`
If it fails, retry once. If still fails, log the error — but never skip the attempt.

> **Sections produced by the helper (current order):**
> 1. FM Results table (per-target Status + Failing Points)
> 2. Failing Points Detail (full DFF hierarchy paths)
> 3. Evidence Walk Summary (signals grouped by level: critical/high/info + tune directives applied)
> 4. Cross-Stage Netlist Deltas (per failing DFF: pin_changes, wire_present_per_stage, cell_blackboxed)
> 5. ECO Changes Applied This Round (Applied/Inserted/Skipped/Already/VerifyFailed counts)
> 6. Pre-FM Check (first 30 lines of step5 rpt)
> 7. Failure Diagnosis (failure_mode + diagnosis + root_cause_reasoning + alternatives_considered)
> 8. Revised Changes + Evidence For Studier (per change: rationale + fallback + top recipe + scope/avoid chips + first divergent point)
> 9. Analyzer Evidence Contract (compliance pass/fail + violations table)
> 10. Companion Artifacts (full path + existence checkmark for all step6 JSONs + RPTs)
>
> Plus a **verdict banner** at the top (color-coded: green/blue/orange/red/gray).

---

## STEP 6.1 — Backup Current PostEco (Surgical Patch Mode)

> **Architecture change — do NOT revert to PreEco.** Previous rounds applied changes that were correct. Reverting to PreEco and re-applying everything from scratch causes duplicate insertions when ALREADY_APPLIED detection misfires. Instead: backup the current PostEco (which has all previous rounds' changes), then eco_applier will surgically undo only the failing entries and re-apply corrections.

Backup current PostEco as the rollback point for this round:
```bash
for stage in Synthesize PrePlace Route:
    # Tag the backup with NEXT_ROUND so each round has its own rollback point
    cp <REF_DIR>/data/PostEco/<Stage>.v.gz \
       <REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<NEXT_ROUND>
```

**Do NOT restore from any previous backup.** The current `PostEco/<Stage>.v.gz` already contains all correctly-applied changes from previous rounds — eco_applier will leave those untouched in Surgical Mode and only undo+reapply entries marked `force_reapply: true`.

**Safety net:** `bak_<TAG>_round1` (written by eco_applier in Round 1) is always the original PreEco state. It is never overwritten and can be used to fully restore if needed.

**CHECKPOINT:** For each stage, verify the backup file `bak_<TAG>_round<NEXT_ROUND>` was created and is non-zero. Do NOT proceed to Step 6c if any backup failed.

---

## STEP 6.2 — Analyze FM Failure

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_fm_analyzer.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `ROUND=<ROUND>`, `AI_ECO_FLOW_DIR`
- `eco_fm_tag` — from ROUND_HANDOFF or fixer_state
- Path to FM spec: `<BASE_DIR>/data/<eco_fm_tag>_spec`
- Path to applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`
- Path to RTL diff: `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`
- Previous strategies from `eco_fixer_state.strategies_tried`
- Output: `<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`

**CHECKPOINT — Schema validation (Fix #6):** Verify `data/<TAG>_eco_fm_analysis_round<ROUND>.json` exists and contains ALL required fields:
```bash
python3 -c "
import json, sys
a = json.load(open('data/<TAG>_eco_fm_analysis_round<ROUND>.json'))
required = [
    'loop_verdict',          # RERUN_SAME_ROUND | ADVANCE_NEXT_ROUND | CONVERGED
    'next_round',            # integer: next round number
    'failure_mode',          # A-H or ABORT_* or UNKNOWN
    'revised_changes',       # list (may be empty for CONVERGED)
    'diagnosis',             # one-sentence root cause
    'root_cause_reasoning',  # narrative tying hypothesis to evidence
    'alternatives_considered', # list of ruled-out hypotheses
    'evidence_summary',      # dict with evidence_walk_json + xstage_compare_json paths
    'failing_points_count',  # dict per target
]
missing = [f for f in required if f not in a]
if missing:
    print(f'FAIL: eco_fm_analyzer JSON missing required fields: {missing}')
    sys.exit(1)
# Validate evidence_for_studier present on every non-exempt revised_change
exempt_actions = {'cascade_verified_skip', 'manual_only'}
for i, rc in enumerate(a.get('revised_changes', [])):
    if rc.get('action') not in exempt_actions and 'evidence_for_studier' not in rc:
        print(f'FAIL: revised_changes[{i}] action={rc.get(\"action\")} missing evidence_for_studier block')
        sys.exit(1)
print('eco_fm_analyzer output schema OK')
"
```
If any field is missing → re-spawn eco_fm_analyzer with the missing fields listed explicitly. Do NOT proceed with incomplete analysis.

---

## STEP 6.2-VALIDATE — Helper-output + Contract Compliance Gate (MANDATORY)

The eco_fm_analyzer sub-agent is responsible for invoking the helper scripts (`eco_fm_evidence_walk.py` and, for FAIL verdicts, `eco_fm_xstage_compare.py`) per its Phase 1+2 contract. ROUND_ORCHESTRATOR must verify those outputs exist AND that the analyzer's `revised_changes` honor the evidence-for-studier contract before any further routing or studier hand-off.

### Step 6d-VALIDATE-1 — Verify helper-script outputs exist

```bash
# 1. Phase 1 (evidence walk) is mandatory for ALL verdicts
ls <BASE_DIR>/data/<TAG>_eco_fm_evidence_round<ROUND>.json \
   || { echo "FAIL: evidence walk JSON missing — eco_fm_analyzer skipped Phase 1"; exit 1; }

# 2. Phase 2 (xstage compare) only for ADVANCE_NEXT_ROUND verdicts.
#    The script auto-stubs for RERUN/CONVERGED, so the file should still exist.
ls <BASE_DIR>/data/<TAG>_eco_fm_xstage_round<ROUND>.json \
   || { echo "FAIL: xstage compare JSON missing — eco_fm_analyzer skipped Phase 2"; exit 1; }
```

**If either file is missing:** the analyzer sub-agent did NOT follow Phase 1+2 of `eco_fm_analyzer.md`. Re-spawn the analyzer once with explicit instruction to run both helper scripts; fail the round if it skips them again.

### Step 6d-VALIDATE-2 — Run analyzer evidence contract validator

```bash
python3 script/eco_scripts/eco_validate_analyzer_evidence_contract.py \
    --analysis-json <BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json \
    --output        <BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.contract_check.json \
    --ai-eco-flow-dir <AI_ECO_FLOW_DIR> \
    --tag <TAG> --round <ROUND> \
    --strict
RC=$?
```

The validator enforces the `evidence_for_studier` schemas defined in `config/eco_agents/eco_re_studier_evidence_contract.md` §2 — universal block + per-action required fields + evidence_path_refs resolvability.

**Exit codes:**
- `0` → all `revised_changes` comply; proceed to Step 6d-VERDICT
- `1` → contract violations; re-spawn eco_fm_analyzer ONCE with the violation list as input. If second pass also fails, write a TUNE_ESCALATION ticket and force ADVANCE_NEXT_ROUND with synthetic failure_mode `analyzer_contract_violation`
- `2` → analysis JSON malformed or missing; re-run Step 6d completely

**Sync the contract check JSON + RPT to AI_ECO_FLOW_DIR (the validator handles the RPT when `--ai-eco-flow-dir` is passed).** If JSON wasn't synced by the validator (legacy mode), copy it manually:
```bash
cp <BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.contract_check.json <AI_ECO_FLOW_DIR>/
```

### Step 6d-VALIDATE-3 — Re-spawn-on-violation policy

If the contract validator returns RC=1:

1. Read the violations list from `data/<TAG>_eco_fm_analysis_round<ROUND>.contract_check.json`
2. Re-spawn `eco_fm_analyzer` sub-agent ONCE with extra prompt fields:
   - `RETRY_REASON: contract_violation`
   - `PRIOR_VIOLATIONS: <violations array as JSON>`
3. After retry, re-run Step 6d-VALIDATE-2
4. If retry STILL fails (RC≠0): set `synthetic_failure_mode: analyzer_contract_violation` in the analysis JSON, force `loop_verdict: ADVANCE_NEXT_ROUND`, and proceed — do NOT loop indefinitely on contract retries

This bounds the retry budget to 1 per round so the loop can never get stuck on analyzer-side bugs.

**CHECKPOINT 6d-VALIDATE:** Both `eco_fm_evidence_round<ROUND>.json` and `eco_fm_xstage_round<ROUND>.json` exist; contract check JSON shows `compliant: true` (or contract retry exhausted with synthetic failure). Only then proceed to Step 6a (email) and then the early-exit / verdict-routing logic below.

---

> **NOW run Step 6a (email/HTML) — FM Analyzer is complete, all data is available.**
> eco_build_round_html.py will include FM results, failing points, evidence walk,
> root cause reasoning, and revised_changes in the email sent to debuggers.

---

**CRITICAL — When to exit the loop early based on eco_fm_analyzer output:**

- `failure_mode: UNKNOWN` → NOT a reason to stop — eco_fm_analyzer MUST have run Step 3b deep investigation before returning UNKNOWN. If `revised_changes` is non-empty, apply them and continue. If empty, treat same as MAX_ROUNDS.
- `failure_mode: ABORT_LINK` → NOT a reason to stop — `revised_changes` contains `force_port_decl` entries; apply them in Step 6e (`force_reapply: true` in study JSON), continue to next round
- `failure_mode: ABORT_CELL_TYPE` → NOT a reason to stop — `revised_changes` contains `fix_cell_type` entries; eco_netlist_studier_round_N re-searches PreEco for correct cell type and updates study JSON, continue to next round
- `failure_mode: T` (compound-cell truth-table mismatch) → NOT a reason to stop — `revised_changes` contains `swap_compound_cell` entries; eco_netlist_studier_round_N overrides `cell_type` (and re-permutes `port_connections` per `port_remap` if present) for all 3 stages in study JSON, continue to next round. If Check T could not find a same-family match, eco_fm_analyzer escalates to Mode F with action `try_structural_decomposition` (rebuild chain with simpler 2/3-input primitives) — never `manual_only`.
- `failure_mode: I` (child output port internally undriven) → NOT a reason to stop — `revised_changes` contains a second `port_connection` entry with `module_name=<child>`, `bus_bit_index`, `net_name=<port>[<bit>]`. eco_netlist_studier_round_N appends it to study JSON; existing `_apply_bus_rename` in eco_netlist_port_rewire wires the child's internal slot to its own output pin. Continue to next round.
- `failure_mode: H` (hierarchical port bus input) → NOT a reason to stop — `revised_changes` contains `fix_named_wire` entries; eco_netlist_studier_round_N sets `needs_named_wire: true` in study JSON, eco_apply_fix_round_N declares named wire and rewires port bus, continue to next round
- `needs_rerun_fenets: true` → NOT a reason to stop — Step 6f-FENETS re-queries the missing signals; eco_netlist_studier_round_N resolves PENDING_FM_RESOLUTION inputs from the rerun results; continue to next round
- `failure_mode: ABORT_NETLIST` → NOT a reason to stop — eco_applier corrupted the netlist; revert is already done in 6b; revised_changes will re-apply the affected entries correctly
- `failure_mode: E` (pre-existing) → revised_changes contains `manual_only` entries. These failures existed before this ECO — the AI flow cannot fix them. Report in FINAL_ORCHESTRATOR summary for engineer review. Engineer decides whether SVF `set_dont_verify` is appropriate. Do NOT apply SVF in the AI flow.
- `failure_mode: G` (structural stage mismatch) → for any ECO gate whose input pin is absent or placeholder in the failing P&R stage, run `eco_resolve_synth_internal.py` first:
  ```bash
  python3 script/eco_scripts/eco_resolve_synth_internal.py \
      --ref-dir <REF_DIR> --synth-net <synth_net_for_pin> \
      --stage <PrePlace|Route> --output /tmp/resolve.json
  # If resolved_net != UNRESOLVABLE: update study JSON pin, set force_reapply:true
  ```
  If UNRESOLVABLE → apply manual F1-F3 forward consumer search (eco_netlist_verifier.md Check 12 F1→F2→F3): find Synth consumers of the net, locate in P&R, read same input pin.
  If still unresolved → `fix_named_wire` (Mode H path). If no fixable net found → `manual_only`. Do NOT apply SVF.
- `failure_mode: F` (manual_only — `d_input_decompose_failed`) → check `revised_changes`:
  - If ALL entries have `action: manual_only` **AND NEXT_ROUND ≥ max_rounds** → exit with `status: MAX_ROUNDS`, spawn FINAL_ORCHESTRATOR (this is a MAX_ROUNDS exit, NOT a manual_only early exit)
  - If ALL entries have `action: manual_only` **AND NEXT_ROUND < max_rounds** → **DO NOT exit early**. eco_fm_analyzer has queued progressive strategies (invert_cmux_constants, try_strategy_A_andterm, try_alternative_pivot). Continue to Steps 6e/6f/4/5 — the studier will attempt the next strategy. `manual_only` means "no fix found YET", not "no fix possible ever".
  - If mixed (some manual_only, some fixable) → always continue; apply fixable changes, leave manual_only points for later rounds or final report
- If `revised_changes` is empty → exit early — treat same as MAX_ROUNDS; spawn FINAL_ORCHESTRATOR with `status: MAX_ROUNDS`

**CORE RULE: `manual_only` is ONLY a final outcome at max rounds. Within the fix loop, it means "try a different strategy next round". NEVER exit early purely because revised_changes are all manual_only unless NEXT_ROUND ≥ max_rounds.**

**RULE: Early-exit decisions happen HERE immediately after Step 6d — but ONLY when ALL strategies exhausted (all manual_only AND at max rounds), OR revised_changes is empty.**

---

## STEP 6.2-VERDICT — Route Based on `loop_verdict`

Read `data/<TAG>_eco_fm_analysis_round<ROUND>.json` and extract:
```python
loop_verdict   = analysis["loop_verdict"]    # mandatory
next_round     = analysis["next_round"]      # mandatory
failure_mode   = analysis["failure_mode"]
revised_changes = analysis["revised_changes"]
```

Branch:

### Branch A — `loop_verdict == "CONVERGED"`

This shouldn't happen at Step 6.2 (we only reach 6.2 when FM failed), but if the analyzer disagrees with our FM-failed assumption, trust the analyzer:
- Skip Steps 6.4, 6.5-FENETS, 6.6, 4, 5
- **Fix #10:** Update `eco_fixer_state` with `converged_at_round: CURRENT_ROUND` and save to disk BEFORE spawning FINAL
- Update round_handoff.json with `status: "FM_PASSED"`, `loop_verdict: "CONVERGED"`, `next_phase: "FINAL"`
- Spawn FINAL_ORCHESTRATOR inline, write `<TAG>_round<CURRENT_ROUND>_phase_exited.marker`, EXIT

### Branch B — `loop_verdict == "RERUN_SAME_ROUND"` (FM aborted)

The analyzer detected an FM ABORT — the netlist failed elaboration, FM never compared. The fix is structural (port missing, wire syntax error, SVF error). Apply ONLY the netlist patches and resubmit FM in this round.

**Pre-check: enforce max-rerun rule (Fix #5 — save fixer_state BEFORE handoff)**
```python
fixer_state = json.load(open(f"data/{TAG}_eco_fixer_state"))
fixer_state["rerun_count_in_round"] = fixer_state.get("rerun_count_in_round", 0) + 1
if fixer_state["rerun_count_in_round"] >= 4:
    # Hard rule #2 trip — abort retry exhausted, force advance
    loop_verdict = "ADVANCE_NEXT_ROUND"
    failure_mode = "abort_unrecoverable"
    print("HARD RULE TRIP: 3 RERUN_SAME_ROUND already attempted; forcing ADVANCE.")
    # Continue to Branch C below
# MANDATORY: save updated rerun_count to disk BEFORE writing round_handoff.json
# so the next ROUND reads the correct counter (not the stale pre-increment value).
json.dump(fixer_state, open(f"data/{TAG}_eco_fixer_state", "w"), indent=2)
```

If `loop_verdict` is still `RERUN_SAME_ROUND` after the rerun-count check:

**Step 6d-RERUN-VERIFY:** All `revised_changes` entries MUST have `action` ∈ {`force_port_decl`, `fix_cell_type`, `fix_netlist_syntax`, `remove_svf_entry`}. If any entry has a non-abort action, this is an analyzer bug — log and treat as ADVANCE_NEXT_ROUND fallback.

**Step 6d-RERUN-PATCH:** Apply the netlist patches inline (no eco_netlist_port_rewire re-run needed for these focused patches):
```python
for change in revised_changes:
    if change["action"] == "force_port_decl":
        # Re-invoke eco_netlist_port_rewire in surgical mode for THIS port_declaration entry only
        run_eco_netlist_port_rewire_surgical(stage=change["stage"], entry={
            "change_type": "port_declaration",
            "signal_name": change["signal_name"],
            "module_name": change["module_name"],
            "declaration_type": change["declaration_type"],
            "force_reapply": True,
        })
    elif change["action"] == "fix_cell_type":
        # Update study JSON cell_type field, re-apply that one change
        update_study_cell_type(change["gate_instance"], change["correct_cell_type"])
        run_eco_netlist_port_rewire_surgical(stage=change["stage"], entry=updated_entry)
    elif change["action"] == "fix_netlist_syntax":
        # Direct text edit of PostEco file
        apply_text_edit(change["file"], change["error_line"], change["fix_description"])
    elif change["action"] == "remove_svf_entry":
        # Edit eco_svf_entries.tcl
        remove_svf_op(change["op_id"])
```

**Step 6d-RERUN-CHECKPOINT:** Verify all patches applied successfully (each `revised_change` has a corresponding entry in the new `eco_applied_round<ROUND>.json` with `status: APPLIED`).

**Step 6d-RERUN-FM:** Skip directly to **Step 5 (pre-FM check)** then **Step 6 (FM submit)** with the SAME `ROUND` value (no increment). Update round_handoff.json with `loop_verdict: "RERUN_SAME_ROUND"` and `rerun_count_in_round: <new value>`.

**Important:** Do NOT run Step 6e (round increment), Step 6f-FENETS, Step 6f (re-study), or Step 4 (eco_apply_fix) for RERUN_SAME_ROUND. These are for failing-point fixes, and a RERUN doesn't have failing points to fix.

### Branch C — `loop_verdict == "ADVANCE_NEXT_ROUND"` (FM failed)

The original failing-point flow. Continue to **Step 6e** as before. Reset `rerun_count_in_round` to 0:
```python
fixer_state["rerun_count_in_round"] = 0
json.dump(fixer_state, open(f"data/{TAG}_eco_fixer_state", "w"), indent=2)
```

The remainder of this MD (Steps 6e, 6f-FENETS, 6f, 4, 5, 6) executes only for Branch C.

---

## STEP 6.4 — Increment Round and Update fixer_state

Read `data/<TAG>_eco_fm_analysis_round<ROUND>.json`.

**ROUND is the round that just failed** (from ROUND_HANDOFF_PATH). `NEXT_ROUND = ROUND + 1`.

Update `eco_fixer_state`:
1. Append strategy description to `strategies_tried`:
   ```python
   strategy_entry = {
       "round": ROUND,
       "failure_mode": fm_analysis["failure_mode"],
       "diagnosis": fm_analysis.get("diagnosis", ""),
       "actions": [
           f"{c['stage']}:{c.get('cell_name', c.get('signal_name','?'))}/{c.get('pin','?')}:{c['action']}"
           for c in fm_analysis["revised_changes"]
       ]
   }
   eco_fixer_state["strategies_tried"].append(strategy_entry)
   ```
2. Set `NEXT_ROUND = ROUND + 1`
3. Set `eco_fixer_state["round"] = NEXT_ROUND`
4. Save updated `eco_fixer_state`

**CHECKPOINT:** Verify `eco_fixer_state` saved, `eco_fixer_state["round"] == NEXT_ROUND`. This is the round number used for ALL subsequent steps (6f, 4, 4b, 5).

---

## STEP 6.5-FENETS — Re-run find_equivalent_nets for missing signals (conditional)

**Run this step ONLY if `eco_fm_analysis_round<ROUND>.json` has `"needs_rerun_fenets": true` and a non-empty `rerun_fenets_signals` list.** Skip to Step 6f directly if not needed.

This step submits the condition input signals that were never queried in the original Step 2 run to FM find_equivalent_nets. The results allow eco_netlist_studier_round_N to resolve `PENDING_FM_RESOLUTION` inputs in the gate chain.

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_fenets_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `RERUN_MODE=true`
- `ROUND=<ROUND>` (the round that just failed)
- `RERUN_SIGNALS=<list from eco_fm_analysis rerun_fenets_signals>`
- Task: submit missing signals to FM, poll until complete, resolve condition inputs, write rerun rpt + JSON

Wait for sub-agent to complete.

**CHECKPOINT:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_fenets_rerun_round<ROUND>.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets_rerun_round<ROUND>.rpt
```
Verify JSON contains `condition_input_resolutions` array. Do NOT proceed to Step 6f without this.

**Step 6f-FENETS-RESOLVE — Refresh SPEC_SOURCES (MANDATORY after re-run):**
```bash
python3 script/eco_scripts/eco_resolve_spec_sources.py \
    --tag <TAG> --round <ROUND> --base-dir <BASE_DIR>
# → writes data/<TAG>_eco_spec_sources_round<ROUND>.json
```
Pass that JSON path (not the original SPEC_SOURCES dict) to eco_netlist_re_studier in Step 6f. Without this, the re-studier may resolve gate-level nets from stale specs and produce wrong port_connections.

---

## STEP 6.5-TUNE — PROHIBITED

**Tune file updates are PROHIBITED. The AI flow must NEVER modify any file under `tune/`.**

If `eco_fm_analysis_round<ROUND>.json` contains `"action": "tune_file_update"` entries, **skip them entirely** and proceed to the next round as normal. Do NOT read or write any `tune/FmTargets/*.tcl` file. MANUAL_ONLY is only triggered when max rounds is reached without convergence — not because a tune action was skipped.

---

## STEP 6.6 — Re-Study (eco_netlist_studier_round_N)

**MANDATORY pre-Step 6f: Run GAP-15 check script:**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_and_term_port_check.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --ref-dir  <REF_DIR> \
    --output   data/<TAG>_eco_and_term_port_check.json
```
Pass `GAP15_CHECK_PATH=data/<TAG>_eco_and_term_port_check.json` to the studier sub-agent prompt.

**Step 6f has two sequential passes — re_studier fixes failing entries, verifier enriches ALL entries:**

**Pass 6f-A — Spawn eco_netlist_re_studier** with `config/eco_agents/eco_netlist_re_studier.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `RE_STUDY_MODE=true`
- `ROUND=<ROUND>` (the round that just failed)
- `FM_ANALYSIS_PATH=<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`
- `FENETS_RERUN_PATH=<BASE_DIR>/data/<TAG>_eco_fenets_rerun_round<ROUND>.json` if Step 6.5-FENETS ran, otherwise `null`
- `SPEC_SOURCES` **(Fix #3):** If Step 6.5-FENETS ran AND `data/<TAG>_eco_spec_sources_round<ROUND>.json` exists → use that file (contains updated per-stage spec paths from the rerun). Otherwise fall back to extracting from `<BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt` footer. Never use the original Step 2 sources when a rerun has newer data.
- Task: fix failing entries only in `eco_preeco_study.json`; write `eco_step3_netlist_study_round<NEXT_ROUND>.rpt`

Wait for eco_netlist_re_studier to complete and verify `eco_step3_netlist_study_round<NEXT_ROUND>.rpt` exists.

**Pass 6f-B — Spawn eco_netlist_verifier** with `config/eco_agents/eco_netlist_verifier.md` prepended. Pass:
- `TAG`, `REF_DIR`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `GAP15_CHECK_PATH=data/<TAG>_eco_and_term_port_check.json`
- `SPEC_SOURCES` (same mapping — verifier uses it for per-stage net resolution in Check 2)
- Task: re-enrich ALL entries in `eco_preeco_study.json` with per-stage nets, gap checks, port boundary, consumer cascade, CTS checks

Wait for eco_netlist_verifier to complete.

**CHECKPOINT 6f (MANDATORY — verify ALL four outputs before continuing):**
```bash
ls <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt
ls <BASE_DIR>/data/<TAG>_eco_step3_netlist_verify.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_verify.rpt
```
If re_studier RPT missing → re_studier failed. Re-spawn Pass 6f-A.

**Fix #2 — Study JSON additive check after verifier:**
```python
import json
study = json.load(open(f"data/{TAG}_eco_preeco_study.json"))
for stage in ("Synthesize", "PrePlace", "Route"):
    entries = study.get(stage, [])
    # All entries must have confirmed=true or confirmed=false — never deleted
    # Verify force_reapply entries from revised_changes are still present
    fm_analysis = json.load(open(f"data/{TAG}_eco_fm_analysis_round{ROUND}.json"))
    for rc in fm_analysis.get("revised_changes", []):
        cell = rc.get("cell_name") or rc.get("instance_name")
        stage_rc = rc.get("stage", "all")
        if stage_rc not in (stage, "all"):
            continue
        found = any(
            e.get("cell_name") == cell or e.get("instance_name") == cell
            for e in entries
        )
        assert found, f"FAIL: revised_change entry for {cell} ({stage}) missing from study JSON after re-study — re-studier deleted it"
```
If any assertion fails → re_studier removed a required entry. Re-spawn Pass 6f-A with explicit `PRESERVE_ENTRIES` list from revised_changes.
If verifier RPT missing → verifier failed. Re-spawn Pass 6f-B.
Verify `eco_preeco_study.json` modified time is after Step 6d completed. Do NOT proceed to eco_expand_chains without all four.

**MANDATORY: Run eco_expand_chains.py after verifier to inject any missing D-input gate chains:**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_expand_chains.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --study    data/<TAG>_eco_preeco_study.json \
    --ref-dir  <REF_DIR> --jira <JIRA> \
    --output   data/<TAG>_eco_preeco_study.json
```
eco_expand_chains runs AFTER verifier (not just after re_studier) because verifier may have added new entries that reference d_input chains not yet injected.

**MANDATORY: Re-validate study JSON post-expand_chains** — same contract enforcement as ORCHESTRATOR Step 3. Catches malformed chain output (Check 16 `[CHAIN_INJECTION_SCHEMA]`) AND Mode J chain-leaf polarity flips (Check 38 `[HIGH/38-CHAIN-LEAF-POLARITY-MISMATCH]`) before Step 4 of the next round:
```bash
python3 script/eco_scripts/eco_validate_step3.py \
    --study data/<TAG>_eco_preeco_study.json \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --ref-dir <REF_DIR> --tag <TAG> \
    --output data/<TAG>_eco_validate_step3_round<NEXT_ROUND>.json
```

**Exit 1 → branch on issue type:**

```python
result = json.load(open(f"data/{TAG}_eco_validate_step3_round{NEXT_ROUND}.json"))
mode_j_issues = [i for i in result['issues'] if 'HIGH/38-CHAIN-LEAF-POLARITY-MISMATCH' in i]
other_issues  = [i for i in result['issues'] if i not in mode_j_issues]

if mode_j_issues:
    # Mode J — re_studier patched a gate's input wire to a bare RTL name that
    # has odd INV-parity in one stage. update_gate_function (Mode A) will NOT
    # converge — needs a wire change, not a function change.
    # Re-spawn re_studier with explicit Mode J hint:
    re_studier_extra_input = {
      "mode_J_hints": [
        {
          "gate_instance": <parse from issue text>,
          "pin": <parse>,
          "stages_parity": <parse>,        # e.g. {Synth:0, PrePlace:0, Route:1}
          "remediation": "rewire_gate_input to polarity-correct wire — see pattern_library §B-FAIL-J. Use MB DFF Q-pin direct (aps_rename_*) or actual_wire_<stage> from rename_map. NEVER mid-buffer-chain nets (FxPlace_ZINV_*).",
        }
        for issue in mode_j_issues
      ]
    }
    # Re-spawn Pass 6f-A with this extra input. Do NOT proceed to applier.

elif other_issues:
    # Other validator failures (Check 16 schema, port_declaration, etc.)
    # Re-spawn re_studier or eco_expand_chains until passing.
    pass
```

Mode J cannot be fixed by re-applying the same gate function — applier will re-emit the same wrong wire. The re_studier must change `port_connections_per_stage[<failing_stage>][<pin>]` to a polarity-correct wire BEFORE Step 4 re-runs.

**MANDATORY: Re-load study JSON before exit check** — the file was just updated by verifier + eco_expand_chains. Do NOT use any in-memory study JSON from earlier in this instance. Always load fresh from disk:

**EXIT RULE — MAX_ROUNDS ONLY (no MANUAL_LIMIT early exit):**

```python
# MANDATORY: load fresh from disk
study = load(f"data/{TAG}_eco_preeco_study.json")

# NEVER exit early due to manual_only — the flow must always try its best.
# Exit ONLY when MAX_ROUNDS is reached.
if NEXT_ROUND > max_rounds:
    update_handoff(status="MAX_ROUNDS", next_phase="FINAL", next_phase_reason="MAX_ROUNDS reached")
    spawn a sub-agent (general-purpose) with FINAL_ORCHESTRATOR.md prepended, TOTAL_ROUNDS=<NEXT_ROUND>  # do NOT write HTML/RPT/email yourself
    write <TAG>_round<CURRENT_ROUND>_phase_exited.marker
    EXIT

# Always continue to eco_applier — even if revised_changes are all manual_only.
# eco_applier handles already_applied entries gracefully.
# eco_fm_analyzer will try progressive strategies each round until max_rounds.
```

**CRITICAL — MANUAL_ONLY is abolished:** Do NOT exit early because eco_fm_analyzer classified something as manual_only. The analyzer must always prescribe a progressive strategy (try_structural_insertion, try_alternative_pivot, conservative_constant, move_gate_to_submodule, etc.) rather than giving up. Use all 6 rounds.

---

## STEP 4 — Apply ECO Fix (eco_apply_fix_round_N)

> **ROLLBACK INVARIANT** — eco_applier writes directly to `<REF_DIR>/data/PostEco/<Stage>.v.gz` BEFORE Step 5 runs. If Step 5 self-healing fails or eco_applier introduces syntax errors, PostEco is left mid-applied. **Step 6b backup of THIS round** (taken at line 134-146 of THIS instance) IS the rollback point — the next ROUND_ORCHESTRATOR's Step 6b will overwrite this round's backup with the now-broken state, and the FIRST-round backup (`bak_<TAG>_round1`) remains the deepest restore point. Surgical mode in eco_netlist_port_rewire handles partial-applied state correctly when re-applying with `force_reapply: true`.

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND=<NEXT_ROUND>`, `AI_ECO_FLOW_DIR`
- PreEco study JSON: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` — **fully enriched** by eco_netlist_verifier (Pass 6f-B), contains `port_connections_per_stage` for all stages and all auto-added entries
- Output: `<BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json`

This agent is `eco_apply_fix_round_N` — it applies the fix strategy identified by eco_fm_analyzer and refined by eco_netlist_studier_round_N. It reads `force_reapply: true` flags and applies port declarations unconditionally when set.

**CHECKPOINT:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json
```

**Generate Step 4 RPT from JSON — do this yourself, do NOT rely on eco_applier:**

```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_rpt_generator.py step4 \
    --applied data/<TAG>_eco_applied_round<NEXT_ROUND>.json \
    --tag <TAG> --jira <JIRA> --round <NEXT_ROUND> \
    --output  data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt

# Copy to AI_ECO_FLOW_DIR
cp <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt
```

Do NOT proceed to Step 5 until the RPT is confirmed in both data/ and AI_ECO_FLOW_DIR.

**MANDATORY pre-Step 5 gate — verify eco_applier JSON exists:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json
```
If this file does NOT exist — eco_applier failed to write its output JSON. Do NOT proceed to Step 5 or FM. Re-spawn eco_applier with the same inputs. **NEVER submit FM without this JSON existing** — the pre-FM checker reads it and without it Step 5 cannot run.

---

## STEP 5 — Pre-FM Quality Checker (MANDATORY)

**BEFORE spawning eco_pre_fm_checker: run eco_verilog_validator.sh directly from ROUND_ORCHESTRATOR.**

eco_verilog_validator.sh is the syntax gate that prevents FM ABORT_NETLIST. It MUST be run by the orchestrator — not delegated to the sub-agent which has repeatedly skipped it. Run it NOW:

```bash
cd <BASE_DIR>
bash script/eco_scripts/eco_verilog_validator.sh \
    <BASE_DIR> <REF_DIR> <TAG> <NEXT_ROUND> \
    data/<TAG>_eco_applied_round<NEXT_ROUND>.json
CHECK8_EXIT=$?
```

- If CHECK8_EXIT = 0 (all PASS) → proceed to spawn eco_pre_fm_checker
- If CHECK8_EXIT = 1 (any FAIL) → apply inline SVR-9/FM-599 fixes directly (remove duplicate wire decls, fix bare parens), then re-run eco_verilog_validator.sh. Only proceed when PASS.

Pass `CHECK8_RESULT_PATH=data/<TAG>_eco_verilog_validator_round<NEXT_ROUND>.json` to eco_pre_fm_checker — it reads this pre-computed result (does NOT re-run eco_verilog_validator.sh).

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_pre_fm_checker.md` prepended. Pass:
- `TAG`, `REF_DIR`, `BASE_DIR`, `ROUND=<NEXT_ROUND>`, `AI_ECO_FLOW_DIR`
- Path to applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json`
- `CHECK8_RESULT_PATH=<BASE_DIR>/data/<TAG>_eco_verilog_validator_round<NEXT_ROUND>.json`

Wait for sub-agent to complete.

**Read result — gate FM submission:**

**MANDATORY JSON INTEGRITY GATE** — run BEFORE schema validation. Round-N agents have been observed editing the script-written `check_summary` to insert `PASS_OVERRIDE` strings to bypass real failures. The integrity validator hard-fails on any such tamper or on `passed=True`-with-non-empty-failures contradictions. If it fails, **abort this round** and re-spawn `eco_pre_fm_checker` with a fresh, non-edited file (deletion of the tampered JSON first):
```bash
python3 script/eco_scripts/eco_validate_pre_fm_integrity.py \
    --check-json data/<TAG>_eco_pre_fm_check_round<NEXT_ROUND>.json
# exit 1 → integrity FAIL → tampered or contradictory; do NOT submit FM
```

**MANDATORY JSON SCHEMA VALIDATION** — same contract as ORCHESTRATOR:
```python
check = load(f"data/{TAG}_eco_pre_fm_check_round{NEXT_ROUND}.json")

required = ["tag", "round", "passed", "attempts", "issues_found", "issues_fixed",
            "issues_unresolved", "warnings", "check_summary"]
missing = [f for f in required if f not in check]
if missing:
    raise RuntimeError(f"eco_pre_fm_checker JSON missing required fields: {missing}. "
                       f"Re-spawn eco_pre_fm_checker.")

if "check8_verilog_validator" not in check.get("check_summary", {}):
    raise RuntimeError("eco_pre_fm_checker JSON missing check8_verilog_validator. "
                       "Re-spawn eco_pre_fm_checker.")

if check["passed"]:
    # All checks passed (fixes applied inline if needed) → proceed to Step 6
    pass
else:
    # Inline fixes exhausted — attempt self-healing within this same round before escalating:
    #
    # Step 5 Self-Healing Loop (one attempt):
    #   1. Re-spawn eco_netlist_verifier (re-enrich study JSON — checks 7/8/9 auto-add missing entries)
    #   2. Re-spawn eco_applier (re-apply force_reapply entries with re-enriched study)
    #   3. Re-run eco_verilog_validator.sh
    #   4. Re-spawn eco_pre_fm_checker (fresh full attempt)
    #   5. If passed=true → proceed to Step 6
    #   6. If still passed=false → THEN escalate to next ROUND_ORCHESTRATOR

    # Step 5a: Re-enrich
    spawn eco_netlist_verifier (TAG, REF_DIR, BASE_DIR, AI_ECO_FLOW_DIR, SPEC_SOURCES, GAP15_CHECK_PATH)

    # Step 5b: Re-apply
    spawn eco_applier (ROUND=NEXT_ROUND, study JSON re-enriched)

    # Step 5c: Re-run check8
    bash script/eco_scripts/eco_verilog_validator.sh <BASE_DIR> <REF_DIR> <TAG> <NEXT_ROUND> \
        data/<TAG>_eco_applied_round<NEXT_ROUND>.json
    CHECK8_RESULT_PATH = data/<TAG>_eco_verilog_validator_round<NEXT_ROUND>.json

    # Step 5d: Re-run pre_fm_checker
    spawn eco_pre_fm_checker (CHECK8_RESULT_PATH=<above>)
    check2 = load(f"data/{TAG}_eco_pre_fm_check_round{NEXT_ROUND}.json")

    if check2["passed"]:
        pass  # self-healing succeeded → proceed to Step 6
    else:
        # True escalation — cannot fix within this round; signal main session for next ROUND
        update_round_handoff(
            status="FM_FAILED",
            pre_fm_check_failed=True,
            next_phase="ROUND" if NEXT_ROUND + 1 <= 5 else "FINAL",
            next_phase_reason="pre_fm_check failed after self-healing"
        )
        update_eco_fixer_state(strategies_tried=[{
            "round": NEXT_ROUND, "failure_mode": "PRE_FM_CHECK_UNRESOLVED",
            "unresolved_issues": check2["issues_unresolved"]
        }])
        emit ROUND_PHASE_READY signal block to SPEC_FILE  # if next_phase=ROUND
        write round<NEXT_ROUND>_phase_exited.marker
        EXIT  # Step 6 skipped — FM never submitted this round; main session spawns next ROUND
```

---

## STEP 6 — PostEco Formality Verification

**MANDATORY pre-FM gate — verify Step 5 JSON exists and passed:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<NEXT_ROUND>.json
```
If this file does NOT exist → Step 5 was never run → ABORT. Re-spawn eco_pre_fm_checker. **FM must NEVER be submitted without a passing Step 5 JSON.** No exceptions.

> **HARD RULE: Each ROUND_ORCHESTRATOR instance runs PostEco FM EXACTLY ONCE for its round.**
> If FM fails after this one run: do NOT re-run FM. Do NOT spawn another eco_fm_runner.
> Instead: update round_handoff.json with `next_phase`, signal/spawn per phase, write exit sentinel, EXIT.
> - `next_phase: ROUND` → emit `ROUND_PHASE_READY` signal block; main session spawns next ROUND in fresh context.
> - `next_phase: FINAL` → spawn FINAL_ORCHESTRATOR inline.
> See "After Step 6 — Hand off to next phase" below.

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fm_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`, `ROUND=<NEXT_ROUND>`
- `ECO_TARGETS=<all 3 targets>` — **ALWAYS run all 3 FM targets every round, regardless of prior PASS/FAIL status.** eco_applier modifies ALL 3 PostEco stages in every round (even for passing targets). Skipping FM on a "passing" stage that was modified risks silent regression — a previously-passing stage could now FAIL after the applier touched it, and we would never detect it until several rounds later. The cost of re-running a passing target is ~30 min FM time; the cost of missing a regression is wasted rounds and incorrect final result.
- Path to existing `data/<TAG>_eco_fm_verify.json` (for merge with previous round results)
- Task: write FM config, submit FM, block until complete, parse+merge results, write verify JSON + RPT

Wait for the sub-agent to complete. **Do NOT spawn another eco_fm_runner if results are not what you expected — read them as-is and hand off.**

> **CRITICAL: When eco_fm_runner returns — ABORT is NOT the same as FAIL. eco_fm_runner does NOT patch on ABORT (STEP F was deleted in the consolidation that put all recovery in `abort_recovery_agent`). On ABORT, APPLY_ORCHESTRATOR's Step 6 inline-loop runs the recovery agent (whitelisted patterns dispatched via YAML `recovery.action`) up to 10×. If ABORT reaches ROUND_ORCHESTRATOR, that loop was exhausted (10 attempts) OR the pattern wasn't whitelisted — eco_fm_analyzer now diagnoses and re_studier fixes the root cause in this round.**

> **ABORT classification is already done.** `post_eco_formality.csh` invoked `eco_fm_status_collector.py` after FM completed; the classifier (`eco_extract_fm_abort_cause.py`) was called as a library and its results are embedded in `data/<TAG>_eco_fm_verify.json`:
> - Top-level `verdict` field — `PASS / FAIL / ABORT_NETLIST / ABORT_LINK / ABORT_SVF / ABORT_OTHER / NOT_RUN / PARTIAL`
> - Per-target `per_target[<t>].abort_pattern` — pattern_kind from `eco_fm_abort_patterns.yaml` (e.g. `invalid_wire_decl_bracket`)
> - Per-target `per_target[<t>].abort_evidence` — log excerpts with file + pattern_kind for each hit
>
> **Read `eco_fm_verify.json` directly. Do NOT re-invoke the classifier CLI** — the data is already there, and re-running it would just re-parse the same logs. To extend pattern coverage for an unseen abort, add a new entry to `eco_fm_abort_patterns.yaml` (single source of truth) — it'll take effect on the next FM submission automatically.

**CHECKPOINT:** Verify ALL of the following:
```bash
ls <BASE_DIR>/data/<TAG>_eco_fm_verify.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step6_fm_verify_round<NEXT_ROUND>.rpt
```
Read `data/<TAG>_eco_fm_tag_round<NEXT_ROUND>.tmp` to get `eco_fm_tag` — save to `eco_fixer_state.fm_results_per_round`.

**CHECKPOINT:** Verify `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step6_fm_verify_round<NEXT_ROUND>.rpt` both exist. Verify `eco_fm_tag` is recorded.

---

## After Step 6 — Hand off to next phase

> **HARD RULE: Read eco_fm_verify.json ONCE, decide `next_phase`, signal/spawn per phase, write exit sentinel, EXIT. Do not loop within this orchestrator.**
> - `status: "PASS"` on ALL targets → `next_phase: FINAL` (spawn FINAL_ORCHESTRATOR inline)
> - `status: "FAIL"` on ANY target AND next round ≤ 10 → `next_phase: ROUND` (emit `ROUND_PHASE_READY` signal, main session spawns next ROUND in fresh context)
> - `status: "ABORT"` on ANY target AND rerun_count < 3 AND next round ≤ 10 → `next_phase: ROUND` (analyzer's RERUN_SAME_ROUND verdict reuses the SAME round number)
> - max rounds (10) hit → `next_phase: FINAL` with `status: MAX_ROUNDS`
> - NEVER re-submit FM here. NEVER apply patches here. NEVER re-run eco_applier here.
> - NEVER spawn ROUND_ORCHESTRATOR yourself — main session does that after seeing the signal.

**Round-number rules:**
- `loop_verdict: RERUN_SAME_ROUND` → next round uses the SAME round number (retry, ROUND value unchanged)
- `loop_verdict: ADVANCE_NEXT_ROUND` → next round uses `NEXT_ROUND = ROUND + 1`
- `loop_verdict: CONVERGED` → no next ROUND; FINAL fires instead

### Mandatory Step A — Update round_handoff.json with `next_phase`

**Fix #1 — Read NEW FM tag BEFORE writing handoff (not the stale tag from INPUTS):**
```bash
# Read the NEW eco_fm_tag from this round's FM submission
NEW_ECO_FM_TAG=$(cat <BASE_DIR>/data/<TAG>_eco_fm_tag_round<NEXT_ROUND>.tmp | grep -o 'eco_fm_tag=.*' | cut -d= -f2)
# This MUST be used in the handoff, not the old eco_fm_tag from INPUTS
```

Update `<BASE_DIR>/data/<TAG>_round_handoff.json`:
```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "ai_eco_flow_dir": "<AI_ECO_FLOW_DIR>",
  "round": "<NEXT_ROUND or SAME_ROUND per verdict>",
  "fenets_tag": "<fenets_tag from INPUTS, OR new rerun tag if Step 6.5-FENETS ran — Fix #8>",
  "eco_fm_tag": "<NEW eco_fm_tag from eco_fm_tag_round<NEXT_ROUND>.tmp — Fix #1>",
  "status": "<FM_PASSED|FM_FAILED|MAX_ROUNDS>",
  "loop_verdict": "<RERUN_SAME_ROUND|ADVANCE_NEXT_ROUND|CONVERGED>",
  "rerun_count_in_round": <N>,
  "next_phase": "<ROUND|FINAL|STOP>",
  "next_phase_reason": "<short note: e.g. 'FM PASS — converged', 'FAIL on FmEqvEcoRouteVsEcoPrePlace — needs round N+1 re-study', 'MAX_ROUNDS (10) reached'>"
}
```

**`next_phase` decision matrix (Fix #4 — explicit NEXT_ROUND boundary):**

Compute NEXT_ROUND first:
- `ADVANCE_NEXT_ROUND` → `NEXT_ROUND = CURRENT_ROUND + 1`
- `RERUN_SAME_ROUND` → `NEXT_ROUND = CURRENT_ROUND` (same round, retry)

| Condition | `next_phase` |
|---|---|
| FM PASS on all targets (CONVERGED) | `FINAL` |
| FM FAIL or ABORT, AND `NEXT_ROUND ≤ 10` | `ROUND` |
| FM FAIL or ABORT, AND `NEXT_ROUND > 10` (CURRENT_ROUND == 10 for ADVANCE) | `FINAL` (with `status: MAX_ROUNDS`) |
| Max rounds (5) hit on RERUN_SAME_ROUND (rerun_count ≥ 4) | `FINAL` (with `status: MAX_ROUNDS`) |
| Pre-FM check failed AND `NEXT_ROUND ≤ 10` | `STOP` (applier issue — not ROUND) |
| Unrecoverable error (no FM verdict, etc.) | `STOP` |

**CRITICAL: `ai_eco_flow_dir` MUST be in every round_handoff.json** — every subsequent ROUND_ORCHESTRATOR and FINAL_ORCHESTRATOR reads it. The value never changes across rounds — always `<REF_DIR>/AI_ECO_FLOW_<TAG>`.

The next ROUND_ORCHESTRATOR also reads `loop_verdict` and `rerun_count_in_round` to enter Branch B (RERUN) or Branch C (ADVANCE) of Step 6d-VERDICT.

### Mandatory Step B — Signal OR spawn per `next_phase`

#### `next_phase: FINAL` → spawn FINAL_ORCHESTRATOR as a sub-agent (do NOT do FINAL work yourself)

**CRITICAL: Do NOT write eco_summary.rpt, eco_report.html, or send email yourself.** All of that belongs to FINAL_ORCHESTRATOR. Your only job here is to spawn it.

**Do NOT emit `ROUND_PHASE_READY` when `next_phase=FINAL`.** The two branches are mutually exclusive — emitting the round signal AND doing FINAL work is a protocol violation.

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: `<current ROUND>`

Wait for the sub-agent to complete before writing the exit sentinel.

#### `next_phase: ROUND` → emit `ROUND_PHASE_READY` signal block + EXIT (no spawn)

The main session detects `ROUND_PHASE_READY` (per CLAUDE.md ECO Round Mode) and spawns the next ROUND_ORCHESTRATOR in fresh context.

Update `eco_fixer_state.fm_results_per_round` with this round's result, then append to `<SPEC_FILE>`:
```
ROUND_PHASE_READY
TAG=<TAG>
REF_DIR=<REF_DIR>
TILE=<TILE>
JIRA=<JIRA>
BASE_DIR=<BASE_DIR>
AI_ECO_FLOW_DIR=<AI_ECO_FLOW_DIR>
LOG_FILE=<LOG_FILE>
SPEC_FILE=<SPEC_FILE>
ROUND=<next round number per loop_verdict>
HANDOFF_PATH=<BASE_DIR>/data/<TAG>_round_handoff.json
```

#### `next_phase: STOP` → no signal, no spawn

Write a one-line note to SPEC_FILE describing the stop reason. Main session reads `next_phase` from handoff and reports stop reason to user.

### Mandatory Step C — Write EXIT sentinel + HARD STOP

```bash
date -Iseconds | xargs -I{} echo "exited {}" > <BASE_DIR>/data/<TAG>_round<CURRENT_ROUND>_phase_exited.marker
ls -la <BASE_DIR>/data/<TAG>_round<CURRENT_ROUND>_phase_exited.marker
```

Where `<CURRENT_ROUND>` is the round number this orchestrator just executed (NOT the next round). The main session polls for this exact marker name.

**Fix #7 — Sentinel naming convention:** Always use `round<N>_phase_exited.marker` regardless of verdict (CONVERGED, ADVANCE, RERUN). Do NOT use `apply_phase_exited.marker` — that is ONLY written by APPLY_ORCHESTRATOR. The main session detects ROUND exit via `round<N>_phase_exited.marker` and APPLY exit via `apply_phase_exited.marker`. These are distinct and must not be mixed.

This is the LAST file you write. After this:

**Your task ends here. Make no further tool calls. Return your status to the caller.**

You MUST stop after writing the sentinel. Do not:
- Run any bash commands after the sentinel write
- Write any more files
- Spawn the next ROUND_ORCHESTRATOR yourself (main session does it)
- "Help" the next ROUND or FINAL agent by doing their work early

---

## Output Files (this agent produces per round)

| File | Written by | Content |
|------|-----------|---------|
| `data/<TAG>_eco_report_round<ROUND>.html` | ROUND_ORCHESTRATOR (Step 6a) | Per-round HTML summary before revert |
| `data/<TAG>_eco_fm_evidence_round<ROUND>.json` | eco_fm_evidence_walk.py (Step 6d Phase 1) | Per-DFF dossier from 12+ FM reports + log |
| `<AI_ECO_FLOW_DIR>/<TAG>_eco_step6_evidence_walk_round<ROUND>.rpt` | eco_fm_evidence_walk.py | Human-readable summary of evidence walk |
| `data/<TAG>_eco_fm_xstage_round<ROUND>.json` | eco_fm_xstage_compare.py (Step 6d Phase 2) | 3-way Synth/PrePlace/Route netlist deltas (FAIL verdicts only) |
| `<AI_ECO_FLOW_DIR>/<TAG>_eco_step6_xstage_compare_round<ROUND>.rpt` | eco_fm_xstage_compare.py | Human-readable summary of cross-stage compare |
| `data/<TAG>_eco_fm_analysis_round<ROUND>.json` | eco_fm_analyzer (Step 6d) | FM failure diagnosis + revised_changes WITH evidence_for_studier blocks |
| `<AI_ECO_FLOW_DIR>/<TAG>_eco_step6_fm_analysis_round<ROUND>.rpt` | eco_fm_analyzer | Human-readable analysis summary |
| `data/<TAG>_eco_fm_analysis_round<ROUND>.contract_check.json` | eco_validate_analyzer_evidence_contract.py | Validator output: contract violations (if any) |
| `<AI_ECO_FLOW_DIR>/<TAG>_eco_step6_evidence_contract_check_round<ROUND>.rpt` | eco_validate_analyzer_evidence_contract.py | Human-readable contract check summary |
| `data/<TAG>_eco_fenets_rerun_round<ROUND>.json` | eco_fenets_runner RERUN_MODE (Step 6f-FENETS) | condition_input_resolutions from re-queried signals |
| `data/<TAG>_eco_step2_fenets_rerun_round<ROUND>.rpt` | eco_fenets_runner RERUN_MODE (Step 6f-FENETS) | Per-signal FM results from rerun |
| `data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` | eco_netlist_studier_round_N (Step 6f) | What was re-studied, what was updated in study JSON |
| `data/<TAG>_eco_preeco_study.json` | eco_netlist_studier_round_N (Step 6f) | Updated study — force_reapply flags, corrected nets |
| `data/<TAG>_eco_fixer_state` | ROUND_ORCHESTRATOR (Step 6e) | Incremented round + strategies_tried |
| `data/<TAG>_eco_applied_round<NEXT_ROUND>.json` | eco_apply_fix_round_N (Step 4) | ECO changes applied in fix round |
| `data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt` | ROUND_ORCHESTRATOR (Step 4) | Detailed application report |
| `data/<TAG>_eco_fm_verify.json` | eco_fm_runner (Step 6) | Merged FM results cumulative across rounds |
| `data/<TAG>_eco_step6_fm_verify_round<NEXT_ROUND>.rpt` | eco_fm_runner (Step 6) | Step 6 FM result RPT |
| `data/<TAG>_round_handoff.json` | ROUND_ORCHESTRATOR (After Step 5) | Updated handoff for next agent |
