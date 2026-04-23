# ECO Round Orchestrator

**You are the ROUND_ORCHESTRATOR agent.** You handle exactly ONE fix loop round then spawn the next agent and EXIT. Your context stays small because you start fresh every round.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else. Every rule in that file addresses a known failure mode. Acknowledge each rule before proceeding.

**SCOPE RESTRICTION — CRITICAL:** Only read agent guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis and contain rules that are wrong for ECO gate-level netlist editing. `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

**Working directory:** Always `cd <BASE_DIR>` before any file operations.

---

## CRITICAL RULES

1. **You handle ONE round only** — do not loop. After Step 5, spawn the next agent and EXIT.
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
- `svf_update_needed` — whether new_logic cells were inserted
- `status` — should be `FM_FAILED`

Set `AI_ECO_FLOW_DIR = ai_eco_flow_dir` from handoff.

Read `<BASE_DIR>/data/<TAG>_eco_fixer_state` to confirm current round and get `strategies_tried`.

---

## STEP 6a — Write Per-Round HTML and Send Email

Write `<BASE_DIR>/data/<TAG>_eco_report_round<ROUND>.html` covering:
- Round N summary: which targets failed, failing point count per target
- ECO changes attempted: cell name, pin, old_net → new_net, status (APPLIED/INSERTED/SKIPPED) — read from `data/<TAG>_eco_applied_round<ROUND>.json`
- FM failing points detail: hierarchy paths of failing DFFs — read from `data/<eco_fm_tag>_spec`
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

## STEP 6b — Revert PostEco Netlists

Restore from round-specific backup:
```bash
for stage in Synthesize PrePlace Route:
    bak = <REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>
    if bak exists:
        cp bak <REF_DIR>/data/PostEco/<Stage>.v.gz
    else:
        print("No backup for <Stage> round <ROUND> — skipping revert")
```

**CHECKPOINT:** For each stage that had a backup, verify the restore succeeded — gz file is non-zero. Do NOT proceed to Step 6c if any restore failed.

---

## STEP 6c — Clean Up SVF Entries

```bash
rm -f <BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
```

---

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
- `failure_mode: ABORT_SVF` → NOT a reason to stop — fix SVF issue (set `svf_update_needed=false`), continue to next round
- `failure_mode: ABORT_LINK` → NOT a reason to stop — `revised_changes` contains `force_port_decl` entries; apply them in Step 6e (`force_reapply: true` in study JSON), continue to next round
- `failure_mode: ABORT_NETLIST` → NOT a reason to stop — eco_applier corrupted the netlist; revert is already done in 6b; revised_changes will re-apply the affected entries correctly
- `failure_mode: E` (pre-existing) → NOT a reason to stop — revised_changes contains `set_dont_verify`; apply and continue
- `failure_mode: G` (structural stage mismatch) → NOT a reason to stop — revised_changes contains `set_dont_verify` entries for the affected scope. Write these entries to `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl` using the eco_svf_updater sub-agent (Step 4b), then set `svf_update_needed=true` for Step 5. Do NOT modify `eco_preeco_study.json` — Mode G failures are suppressed in FM, not fixed by ECO rewiring. Continue to next round.
- `failure_mode: F` (manual_only — `d_input_decompose_failed`) → **check if ALL `revised_changes` entries have `action: manual_only`**:
  - If YES → **exit the loop early right here** — set handoff `status: MANUAL_LIMIT`, NEXT_ROUND = ROUND + 1, spawn FINAL_ORCHESTRATOR with `TOTAL_ROUNDS: <ROUND>`. Do NOT run Steps 6e/6f/4/5 — those are wasted rounds. These points cannot be automated.
  - If NO (mixed — some manual_only, some fixable) → continue; apply the fixable changes in Steps 6e/6f/4/5, leave manual_only points in engineer report
- If `revised_changes` is empty → **exit the loop early** — treat same as MAX_ROUNDS; spawn FINAL_ORCHESTRATOR with `status: MAX_ROUNDS`, `TOTAL_ROUNDS: <ROUND>`

**RULE: Early-exit decisions (Mode F all-manual, empty revised_changes) happen HERE immediately after Step 6d — before Steps 6e/6f/4/5. Do NOT continue to Steps 6e/6f/4/5 if exiting early.**

**RULE: "After Step 5" only handles 3 outcomes: FM PASS → FINAL_ORCHESTRATOR, FM FAIL+NEXT_ROUND<5 → new ROUND_ORCHESTRATOR, FM FAIL+NEXT_ROUND=5 → FINAL_ORCHESTRATOR(MAX_ROUNDS). Mode F all-manual is never seen in "After Step 5" because it exits at Step 6d.**

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

## STEP 6f — Re-Study (eco_netlist_studier_round_N)

**ALWAYS spawn eco_netlist_studier in RE_STUDY_MODE regardless of failure mode.** The studier reads the eco_fm_analyzer output, inspects the actual PostEco netlist, and updates `eco_preeco_study.json` with corrected/forced entries. This replaces the previous approach of manually patching the study JSON in ROUND_ORCHESTRATOR.

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_netlist_studier.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `RE_STUDY_MODE=true`
- `ROUND=<ROUND>` (the round that just failed — studier reads `<TAG>_eco_fm_analysis_round<ROUND>.json`)
- `FM_ANALYSIS_PATH=<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`
- Task: update `eco_preeco_study.json` for failing entries only; write `eco_step3_netlist_study_round<ROUND>.rpt`

Wait for sub-agent to complete.

**CHECKPOINT:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<ROUND>.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<ROUND>.rpt
```
Verify `eco_preeco_study.json` modified time is after Step 6d completed. Do NOT proceed to Step 4 without both.

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

Do NOT proceed to Step 4b until the RPT is confirmed in both data/ and AI_ECO_FLOW_DIR.

---

## STEP 4b — SVF Entries (if pre-existing FM failures require suppression)

> **CRITICAL:** `guide_eco_change -type insert_cell` is NOT a valid SVF command and must NEVER be written (RULE 11). FM auto-matches inserted cells by instance path. Step 4b is ONLY for `set_dont_verify` / `set_user_match` entries suppressing pre-existing failures — it is NOT triggered by new_logic insertions alone.

Read `data/<TAG>_eco_fm_analysis_round<ROUND>.json`. Check if `revised_changes` contains any entry with `action: set_dont_verify` (Mode E pre-existing, or Mode G structural mismatch).

If yes — **Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_svf_updater.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND=<NEXT_ROUND>`, `AI_ECO_FLOW_DIR`
- Task: Write `set_dont_verify` / `set_user_match` entries (NEVER `guide_eco_change -type insert_cell`) to `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl`
- Output: `<BASE_DIR>/data/<TAG>_eco_svf_update.json` + `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl`

Set `svf_update_needed = true` only when the TCL file was written with entries.

**CHECKPOINT (if spawned):** Verify `_eco_svf_entries.tcl` exists and contains only `set_dont_verify` or `set_user_match` entries.

If no pre-existing failures requiring suppression: set `svf_update_needed = false`, skip Step 4b.

---

## STEP 5 — PostEco Formality Verification

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fm_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`, `ROUND=<NEXT_ROUND>`
- `ECO_TARGETS=<space-separated failing targets from previous round>` (only failing, not all 3)
- `svf_update_needed=<true|false>` (from Step 4b)
- Path to existing `data/<TAG>_eco_fm_verify.json` (for merge with previous round results)
- Task: write FM config, submit FM, block until complete, parse+merge results, write verify JSON + RPT

Wait for the sub-agent to complete.

**CHECKPOINT:** Verify ALL of the following:
```bash
ls <BASE_DIR>/data/<TAG>_eco_fm_verify.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt
```
Read `data/<TAG>_eco_fm_tag_round<NEXT_ROUND>.tmp` to get `eco_fm_tag` — save to `eco_fixer_state.fm_results_per_round`.

**CHECKPOINT:** Verify `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt` both exist. Verify `eco_fm_tag` is recorded.

---

## After Step 5 — Spawn Next Agent

Update `<BASE_DIR>/data/<TAG>_round_handoff.json`:
```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "round": "<NEXT_ROUND>",
  "fenets_tag": "<fenets_tag>",
  "eco_fm_tag": "<new eco_fm_tag>",
  "svf_update_needed": "<true|false>",
  "status": "<FM_PASSED|FM_FAILED|MAX_ROUNDS>"
}
```

### If FM RESULT = PASS

**Spawn FINAL_ORCHESTRATOR agent** with `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: `<NEXT_ROUND>`

**Then EXIT — your work is done.**

### If FM RESULT = FAIL and NEXT_ROUND < 5

Update `eco_fixer_state.fm_results_per_round` with this round's result.

**Spawn ROUND_ORCHESTRATOR agent** (fresh instance) with `config/eco_agents/ROUND_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`

**Then EXIT — your work is done.**

### If FM RESULT = FAIL and NEXT_ROUND = 5 (max rounds reached — FM still failing, not manual_only)

Update handoff: `"status": "MAX_ROUNDS"`

**Spawn FINAL_ORCHESTRATOR agent** with `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: 5

**Then EXIT — your work is done.**

---

## Output Files (this agent produces per round)

| File | Written by | Content |
|------|-----------|---------|
| `data/<TAG>_eco_report_round<ROUND>.html` | ROUND_ORCHESTRATOR (Step 6a) | Per-round HTML summary before revert |
| `data/<TAG>_eco_fm_analysis_round<ROUND>.json` | eco_fm_analyzer (Step 6d) | FM failure diagnosis + revised_changes |
| `data/<TAG>_eco_step3_netlist_study_round<ROUND>.rpt` | eco_netlist_studier_round_N (Step 6f) | What was re-studied, what was updated in study JSON |
| `data/<TAG>_eco_preeco_study.json` | eco_netlist_studier_round_N (Step 6f) | Updated study — force_reapply flags, corrected nets |
| `data/<TAG>_eco_fixer_state` | ROUND_ORCHESTRATOR (Step 6e) | Incremented round + strategies_tried |
| `data/<TAG>_eco_applied_round<NEXT_ROUND>.json` | eco_apply_fix_round_N (Step 4) | ECO changes applied in fix round |
| `data/<TAG>_eco_step4_eco_applied_round<NEXT_ROUND>.rpt` | ROUND_ORCHESTRATOR (Step 4) | Detailed application report |
| `data/<TAG>_eco_svf_entries.tcl` | eco_svf_updater (Step 4b) | SVF entries for pre-existing failures |
| `data/<TAG>_eco_fm_verify.json` | eco_fm_runner (Step 5) | Merged FM results cumulative across rounds |
| `data/<TAG>_eco_step5_fm_verify_round<NEXT_ROUND>.rpt` | eco_fm_runner (Step 5) | Step 5 FM result RPT |
| `data/<TAG>_round_handoff.json` | ROUND_ORCHESTRATOR (After Step 5) | Updated handoff for next agent |
