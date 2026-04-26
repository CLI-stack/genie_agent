# ECO Round Orchestrator

**You are the ROUND_ORCHESTRATOR agent.** You handle exactly ONE fix loop round then spawn the next agent and EXIT. Your context stays small because you start fresh every round.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else. Every rule in that file addresses a known failure mode. Acknowledge each rule before proceeding.

**SCOPE RESTRICTION — CRITICAL:** Only read agent guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis and contain rules that are wrong for ECO gate-level netlist editing. `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

**Working directory:** Always `cd <BASE_DIR>` before any file operations.

---

## CRITICAL RULES

1. **You handle ONE round only — ONE FM run only.** Do not loop. After Step 5 completes (whether FM passes or fails), spawn the next agent and EXIT. Never re-run FM within the same ROUND_ORCHESTRATOR instance regardless of the result.
2. **Read state from disk, not memory** — all inputs come from `ROUND_HANDOFF_PATH` and `_eco_fixer_state`. Do not assume anything from previous context.
3. **Every step must complete and checkpoint must pass** before proceeding to the next step.
4. **Email before revert** — Step 6a (email) always runs before Step 6b (revert). Never skip.
5. **Fixer state must be incremented and saved** before spawning the next round agent.
6. **Never skip a step** — context pressure is NOT a valid reason to skip any step or checkpoint.

---

## INPUTS

Read `<ROUND_HANDOFF_PATH>` (passed in your prompt) to get:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`, `ai_eco_flow_dir`
- `round` — the round that just failed (e.g., 1)
- `eco_fm_tag` — FM tag from the failed round
- `status` — should be `FM_FAILED`

Set `AI_ECO_FLOW_DIR = ai_eco_flow_dir` from handoff.

Read `<BASE_DIR>/data/<TAG>_eco_fixer_state` to confirm current round and get `strategies_tried`.

---

## STEP 6a — Write Per-Round HTML and Send Email

**Check if FM was submitted this round:**
```python
handoff = load(f"data/{TAG}_round_handoff.json")
pre_fm_check_failed = handoff.get("pre_fm_check_failed", False)
```

If `pre_fm_check_failed: true` — FM was never submitted (blocked by Step 4c). Write a **simplified HTML** noting pre-FM check failure and skip the FM failing points section. Do NOT try to read `eco_fm_tag_spec` (it doesn't exist). Send email with subject indicating "Pre-FM Check Failed".

If `pre_fm_check_failed: false` (normal FM failure) — write full HTML:
- Round N summary: which targets failed, failing point count per target
- ECO changes attempted: read from `data/<TAG>_eco_applied_round<ROUND>.json`
- FM failing points detail: hierarchy paths of failing DFFs — read from `data/<eco_fm_tag>_spec`
- Pre-FM check results: read from `data/<TAG>_eco_step4c_pre_fm_check_round<ROUND>.rpt` (if exists)
- What will be tried next round (from eco_fm_analyzer — written in Step 6d)

Then send:
```bash
cd <BASE_DIR>
python3 script/genie_cli.py --send-eco-email <TAG> --eco-round <ROUND>
```

**MANDATORY CHECKPOINT — Do NOT proceed to Step 6b until this command succeeds.**
Verify output contains: `Email sent successfully`
If it fails, retry once. If still fails, log the error — but never skip the attempt.

---

## STEP 6b — Backup Current PostEco (Surgical Patch Mode)

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

## STEP 6c — (Removed — SVF is engineers-only)

## STEP 6d — Analyze FM Failure

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_fm_analyzer.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `ROUND=<ROUND>`, `AI_ECO_FLOW_DIR`
- `eco_fm_tag` — from ROUND_HANDOFF or fixer_state
- Path to FM spec: `<BASE_DIR>/data/<eco_fm_tag>_spec`
- Path to applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`
- Path to RTL diff: `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`
- Previous strategies from `eco_fixer_state.strategies_tried`
- Output: `<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`

**CHECKPOINT:** Verify `data/<TAG>_eco_fm_analysis_round<ROUND>.json` exists and contains `revised_changes[]` before proceeding.

**CRITICAL — When to exit the loop early based on eco_fm_analyzer output:**

- `failure_mode: UNKNOWN` → NOT a reason to stop — eco_fm_analyzer MUST have run Step 3b deep investigation before returning UNKNOWN. If `revised_changes` is non-empty, apply them and continue. If empty, treat same as MAX_ROUNDS.
- `failure_mode: ABORT_LINK` → NOT a reason to stop — `revised_changes` contains `force_port_decl` entries; apply them in Step 6e (`force_reapply: true` in study JSON), continue to next round
- `failure_mode: ABORT_CELL_TYPE` → NOT a reason to stop — `revised_changes` contains `fix_cell_type` entries; eco_netlist_studier_round_N re-searches PreEco for correct cell type and updates study JSON, continue to next round
- `failure_mode: H` (hierarchical port bus input) → NOT a reason to stop — `revised_changes` contains `fix_named_wire` entries; eco_netlist_studier_round_N sets `needs_named_wire: true` in study JSON, eco_apply_fix_round_N declares named wire and rewires port bus, continue to next round
- `needs_rerun_fenets: true` → NOT a reason to stop — Step 6f-FENETS re-queries the missing signals; eco_netlist_studier_round_N resolves PENDING_FM_RESOLUTION inputs from the rerun results; continue to next round
- `failure_mode: ABORT_NETLIST` → NOT a reason to stop — eco_applier corrupted the netlist; revert is already done in 6b; revised_changes will re-apply the affected entries correctly
- `failure_mode: E` (pre-existing) → revised_changes contains `manual_only` entries. These failures existed before this ECO — the AI flow cannot fix them. Report in FINAL_ORCHESTRATOR summary for engineer review. Engineer decides whether SVF `set_dont_verify` is appropriate. Do NOT apply SVF in the AI flow.
- `failure_mode: G` (structural stage mismatch) → first attempt `fix_named_wire` (Mode H path) for any ECO gate with a P&R-renamed net. If Priority 3 structural trace confirms no fixable net → revised_changes contains `manual_only` entries. Report for engineer review. Do NOT apply SVF.
- `failure_mode: F` (manual_only — `d_input_decompose_failed`) → check `revised_changes`:
  - If ALL entries have `action: manual_only` **AND NEXT_ROUND ≥ max_rounds** → exit early with `status: MANUAL_LIMIT`, spawn FINAL_ORCHESTRATOR
  - If ALL entries have `action: manual_only` **AND NEXT_ROUND < max_rounds** → **DO NOT exit early**. eco_fm_analyzer has queued progressive strategies (invert_cmux_constants, try_strategy_A_andterm, try_alternative_pivot). Continue to Steps 6e/6f/4/5 — the studier will attempt the next strategy. `manual_only` means "no fix found YET", not "no fix possible ever".
  - If mixed (some manual_only, some fixable) → always continue; apply fixable changes, leave manual_only points for later rounds or final report
- If `revised_changes` is empty → exit early — treat same as MAX_ROUNDS; spawn FINAL_ORCHESTRATOR with `status: MAX_ROUNDS`

**CORE RULE: `manual_only` is ONLY a final outcome at max rounds. Within the fix loop, it means "try a different strategy next round". NEVER exit early purely because revised_changes are all manual_only unless NEXT_ROUND ≥ max_rounds.**

**RULE: Early-exit decisions happen HERE immediately after Step 6d — but ONLY when ALL strategies exhausted (all manual_only AND at max rounds), OR revised_changes is empty.**

---

## STEP 6e — Increment Round and Update fixer_state

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

## STEP 6f-FENETS — Re-run find_equivalent_nets for missing signals (conditional)

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

---

## STEP 6f — Re-Study (eco_netlist_studier_round_N)

**ALWAYS spawn eco_netlist_studier in RE_STUDY_MODE regardless of failure mode.** The studier reads the eco_fm_analyzer output, inspects the actual PostEco netlist, and updates `eco_preeco_study.json` with corrected/forced entries. This replaces the previous approach of manually patching the study JSON in ROUND_ORCHESTRATOR.

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_netlist_studier.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `RE_STUDY_MODE=true`
- `ROUND=<ROUND>` (the round that just failed — studier reads `<TAG>_eco_fm_analysis_round<ROUND>.json`)
- `FM_ANALYSIS_PATH=<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`
- `FENETS_RERUN_PATH=<BASE_DIR>/data/<TAG>_eco_fenets_rerun_round<ROUND>.json` if Step 6f-FENETS ran, otherwise `null`
- Task: update `eco_preeco_study.json` for failing entries only; write `eco_step3_netlist_study_round<NEXT_ROUND>.rpt`

Wait for sub-agent to complete.

**CHECKPOINT:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt
```
Verify `eco_preeco_study.json` modified time is after Step 6d completed. Do NOT proceed to Step 4 without both.

**MANUAL_ONLY RE-CHECK (after Step 6f):** After eco_netlist_studier_round_N completes, re-scan `eco_preeco_study.json` for entries with `force_reapply: true`:

```python
study = load(f"data/{TAG}_eco_preeco_study.json")
reapply_entries = [
    e for stage_entries in study.values() if isinstance(stage_entries, list)
    for e in stage_entries
    if e.get("force_reapply") and not e.get("manual_only")
]
if not reapply_entries:
    if NEXT_ROUND >= max_rounds:
        # Truly exhausted — exit with MANUAL_LIMIT
        update_handoff(status="MANUAL_LIMIT")
        spawn FINAL_ORCHESTRATOR with TOTAL_ROUNDS=<NEXT_ROUND>
        EXIT
    else:
        # No fixable work THIS round but rounds remain — eco_fm_analyzer may have
        # queued a progressive strategy (invert_cmux_constants etc.) for next round.
        # Spawn next ROUND_ORCHESTRATOR to try the next strategy.
        # Do NOT exit early — always use available rounds.
        update_handoff(status="FM_FAILED")
        spawn ROUND_ORCHESTRATOR
        EXIT
```

This catches the case where eco_netlist_studier found no immediately fixable work. If rounds remain, continue trying — eco_fm_analyzer will attempt a different strategy. Only exit at max_rounds.

---

## STEP 4 — Apply ECO Fix (eco_apply_fix_round_N)

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND=<NEXT_ROUND>`, `AI_ECO_FLOW_DIR`
- PreEco study JSON: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` (updated by Step 6f)
- Output: `<BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json`

This agent is `eco_apply_fix_round_N` — it applies the fix strategy identified by eco_fm_analyzer and refined by eco_netlist_studier_round_N. It reads `force_reapply: true` flags and applies port declarations unconditionally when set.

**CHECKPOINT:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json
```

**Generate Step 4 RPT from JSON — do this yourself, do NOT rely on eco_applier. Use the same detailed format as ORCHESTRATOR.md Step 4 RPT (show reason/detail for every status, not just SKIPPED):**

```python
applied = load("data/<TAG>_eco_applied_round<NEXT_ROUND>.json")
s = applied["summary"]
with open("data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt", "w") as f:
    f.write(f"STEP 4 — ECO APPLIED (Round <NEXT_ROUND>)\nTag: <TAG>\n{'='*80}\n")
    f.write(f"Summary: {s['applied']} applied / {s['inserted']} inserted / "
            f"{s.get('already_applied',0)} already_applied / "
            f"{s['skipped']} skipped / {s['verify_failed']} verify_failed\n\n")
    for stage in ["Synthesize", "PrePlace", "Route"]:
        f.write(f"[{stage}]\n")
        for e in applied[stage]:
            ct = e.get('change_type', '?')
            status = e['status']
            name = (e.get('instance_name') or e.get('cell_name') or
                    e.get('signal_name') or e.get('port_name') or '?')
            f.write(f"  {status:15s} {name:40s} type={ct}\n")
            if status == 'INSERTED':
                f.write(f"    → cell_type={e.get('cell_type','?')}  output={e.get('output_net','?')}  scope={e.get('instance_scope','?')}\n")
                if e.get('reason'): f.write(f"    → {e['reason']}\n")
            elif status == 'APPLIED':
                if e.get('reason'): f.write(f"    → {e['reason']}\n")
                elif ct == 'rewire': f.write(f"    → {e.get('old_net','?')} → {e.get('new_net','?')} on pin {e.get('pin','?')}\n")
                elif ct in ('port_declaration','port_promotion'): f.write(f"    → module={e.get('module_name','?')}  decl_type={e.get('declaration_type','?')}\n")
                elif ct == 'port_connection': f.write(f"    → .{e.get('port_name','?')}({e.get('net_name','?')}) on instance {e.get('instance_name','?')}\n")
            elif status == 'ALREADY_APPLIED':
                ar = e.get('already_applied_reason', e.get('reason', 'no reason recorded'))
                f.write(f"    → {ar}\n")
            elif status == 'SKIPPED':
                f.write(f"    → REASON: {e.get('reason', 'no reason recorded')}\n")
            elif status == 'VERIFY_FAILED':
                f.write(f"    → VERIFY FAILED: {e.get('reason', 'no reason recorded')}\n")
        f.write("\n")
```

```bash
# Write RPT
# ... (read JSON and format RPT content)

# Copy to AI_ECO_FLOW_DIR immediately
cp <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt <AI_ECO_FLOW_DIR>/

# Verify copy
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt
```

Do NOT proceed to Step 4c until the RPT is confirmed in both data/ and AI_ECO_FLOW_DIR.

---

## STEP 4c — Pre-FM Cross-Stage Consistency Check

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_pre_fm_checker.md` prepended. Pass:
- `TAG`, `REF_DIR`, `BASE_DIR`, `ROUND=<NEXT_ROUND>`, `AI_ECO_FLOW_DIR`
- Path to applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round<NEXT_ROUND>.json`

Wait for sub-agent to complete.

**Read result — gate FM submission:**
```python
check = load(f"data/{TAG}_eco_pre_fm_check_round{NEXT_ROUND}.json")
if check["passed"]:
    # All checks passed (fixes applied inline if needed) → proceed to Step 5
    pass
else:
    # Inline fixes exhausted after MAX_RETRIES — escalate to next ROUND_ORCHESTRATOR
    update_round_handoff(status="FM_FAILED", pre_fm_check_failed=True)
    update_eco_fixer_state(strategies_tried=[{
        "round": NEXT_ROUND, "failure_mode": "PRE_FM_CHECK_UNRESOLVED",
        "unresolved_issues": check["issues_unresolved"]
    }])
    spawn ROUND_ORCHESTRATOR (next instance)
    EXIT  # Step 5 skipped — FM never submitted this round
```

---

## STEP 5 — PostEco Formality Verification

> **HARD RULE: Each ROUND_ORCHESTRATOR instance runs PostEco FM EXACTLY ONCE for its round.**
> If FM fails after this one run: do NOT re-run FM. Do NOT spawn another eco_fm_runner.
> Instead: update round_handoff.json → spawn next agent (FINAL_ORCHESTRATOR or new ROUND_ORCHESTRATOR) → EXIT.
> The next ROUND_ORCHESTRATOR instance will handle the next fix cycle and its own single FM run.

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fm_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`, `ROUND=<NEXT_ROUND>`
- `ECO_TARGETS=<space-separated failing targets from previous round>` (only failing targets, not all 3)
- Path to existing `data/<TAG>_eco_fm_verify.json` (for merge with previous round results)
- Task: write FM config, submit FM, block until complete, parse+merge results, write verify JSON + RPT

Wait for the sub-agent to complete. **Do NOT spawn another eco_fm_runner if results are not what you expected — read them as-is and hand off.**

> **CRITICAL: When eco_fm_runner returns — regardless of PASS, FAIL, or ABORT — go directly to "After Step 5" and spawn the correct next agent. NEVER attempt to diagnose, patch, or re-submit FM within this same ROUND_ORCHESTRATOR instance. ABORT results (N/A, no matching/failing points) go to the NEXT ROUND_ORCHESTRATOR, not handled inline.**

**CHECKPOINT:** Verify ALL of the following:
```bash
ls <BASE_DIR>/data/<TAG>_eco_fm_verify.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt
```
Read `data/<TAG>_eco_fm_tag_round<NEXT_ROUND>.tmp` to get `eco_fm_tag` — save to `eco_fixer_state.fm_results_per_round`.

**CHECKPOINT:** Verify `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt` both exist. Verify `eco_fm_tag` is recorded.

---

## After Step 5 — Spawn Next Agent

> **HARD RULE: Read eco_fm_verify.json ONCE and spawn the next agent. Do not loop.**
> - `status: "PASS"` on ALL targets → FINAL_ORCHESTRATOR
> - `status: "FAIL"` on ANY target → new ROUND_ORCHESTRATOR (if rounds remain)
> - `status: "ABORT"` on ANY target → treated as FAIL → new ROUND_ORCHESTRATOR (eco_fm_analyzer will diagnose the abort in Step 6d of the next round)
> - NEVER re-submit FM here. NEVER apply patches here. NEVER re-run eco_applier here.

Update `<BASE_DIR>/data/<TAG>_round_handoff.json`:
```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "ai_eco_flow_dir": "<AI_ECO_FLOW_DIR>",
  "round": "<NEXT_ROUND>",
  "fenets_tag": "<fenets_tag>",
  "eco_fm_tag": "<new eco_fm_tag>",
  "status": "<FM_PASSED|FM_FAILED|MAX_ROUNDS>"
}
```

**CRITICAL: `ai_eco_flow_dir` MUST be in every round_handoff.json.** Every subsequent ROUND_ORCHESTRATOR and FINAL_ORCHESTRATOR reads `ai_eco_flow_dir` from this file. If it is missing, all file copies to AI_ECO_FLOW_DIR will fail in subsequent rounds. The value never changes across rounds — always `<REF_DIR>/AI_ECO_FLOW_<TAG>`.

### If FM RESULT = PASS

**Spawn FINAL_ORCHESTRATOR agent** with `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: `<NEXT_ROUND>`

**Then EXIT — your work is done.**

### If FM RESULT = FAIL and NEXT_ROUND ≤ 6

> **GUARD:** Before spawning, verify `NEXT_ROUND ≤ max_rounds (6)`. If `NEXT_ROUND > 6` — this should never happen, but if it does: do NOT spawn another ROUND_ORCHESTRATOR. Treat as MAX_ROUNDS exceeded → spawn FINAL_ORCHESTRATOR with `status: MAX_ROUNDS`.

Update `eco_fixer_state.fm_results_per_round` with this round's result.

**Spawn ROUND_ORCHESTRATOR agent** (fresh instance) with `config/eco_agents/ROUND_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `AI_ECO_FLOW_DIR`: `<AI_ECO_FLOW_DIR>` (pass explicitly so the new instance has it without needing to read handoff first)

**Then EXIT — your work is done.**

### If FM RESULT = FAIL and NEXT_ROUND > 6 (max rounds exceeded)

Update handoff: `"status": "MAX_ROUNDS"`

**Spawn FINAL_ORCHESTRATOR agent** with `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: 6

**Then EXIT — your work is done.**

---

## Output Files (this agent produces per round)

| File | Written by | Content |
|------|-----------|---------|
| `data/<TAG>_eco_report_round<ROUND>.html` | ROUND_ORCHESTRATOR (Step 6a) | Per-round HTML summary before revert |
| `data/<TAG>_eco_fm_analysis_round<ROUND>.json` | eco_fm_analyzer (Step 6d) | FM failure diagnosis + revised_changes |
| `data/<TAG>_eco_fenets_rerun_round<ROUND>.json` | eco_fenets_runner RERUN_MODE (Step 6f-FENETS) | condition_input_resolutions from re-queried signals |
| `data/<TAG>_eco_step2_fenets_rerun_round<ROUND>.rpt` | eco_fenets_runner RERUN_MODE (Step 6f-FENETS) | Per-signal FM results from rerun |
| `data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` | eco_netlist_studier_round_N (Step 6f) | What was re-studied, what was updated in study JSON |
| `data/<TAG>_eco_preeco_study.json` | eco_netlist_studier_round_N (Step 6f) | Updated study — force_reapply flags, corrected nets |
| `data/<TAG>_eco_fixer_state` | ROUND_ORCHESTRATOR (Step 6e) | Incremented round + strategies_tried |
| `data/<TAG>_eco_applied_round<NEXT_ROUND>.json` | eco_apply_fix_round_N (Step 4) | ECO changes applied in fix round |
| `data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt` | ROUND_ORCHESTRATOR (Step 4) | Detailed application report |
| `data/<TAG>_eco_fm_verify.json` | eco_fm_runner (Step 5) | Merged FM results cumulative across rounds |
| `data/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt` | eco_fm_runner (Step 5) | Step 5 FM result RPT |
| `data/<TAG>_round_handoff.json` | ROUND_ORCHESTRATOR (After Step 5) | Updated handoff for next agent |
