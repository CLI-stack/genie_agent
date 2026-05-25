# ECO Pre-FM Checker — Cross-Stage Consistency Validator

**You are the ECO pre-FM checker.** You run AFTER eco_applier (Step 4) and BEFORE FM submission (Step 6).

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in whichever orchestrator spawned you:
- Initial Round 1: `config/eco_agents/APPLY_ORCHESTRATOR.md` **§STEP 5 — Pre-FM Quality Checker**
- Per-round (Round 2+): `config/eco_agents/ROUND_ORCHESTRATOR.md` **§STEP 5 — Pre-FM Quality Checker**

Do NOT read other STEP sections; they belong to other agents.

---

## PRIMARY FLOW — RUN THE SCRIPT, TRUST THE OUTPUT

**Your first and primary action is to run the deterministic check script:**

```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_pre_fm_check.py \
    --tag <TAG> \
    --round <ROUND> \
    --base-dir <BASE_DIR> \
    --ref-dir <REF_DIR> \
    --jira <JIRA>
```

**If exit code = 0 (PASS):** FM is safe to submit. Copy RPT to AI_ECO_FLOW_DIR and exit.

**If exit code = 1 (FAIL):** Read the failures from the JSON. Apply inline fixes ONLY for these specific failure types — then re-run the script to confirm PASS before FM submission:

| Failure type | Inline fix |
|---|---|
| `[PORT_SKIP]` or `[DEFERRED]` | Re-apply the skipped port_declaration using eco_applier Pass 2a logic on that specific entry. |
| `[STAGE_MISMATCH]` | Re-run eco_applier Pass 1 for the missing stage with `force_reapply: true`. |
| `[SVR4_SVR9]` | Apply eco_verilog_validator.sh fixes (duplicate wire removal, missing cell type). |
| `[ZERO_CELLS]` | Re-run eco_applier for the zero-cell stage. |
| `[UNHANDLED]` | Log for ROUND_ORCHESTRATOR — cannot fix inline. |

**Max inline fix retries: 3.** If script still exits 1 after 3 attempts → write JSON with `passed: false`, copy RPT to AI_ECO_FLOW_DIR, exit. ROUND_ORCHESTRATOR handles it.

> **ROLLBACK INVARIANT** — when self-healing fails and `passed: false` is emitted, PostEco netlists are left in mid-applied state from Step 4. Log this fact in the RPT (`WARN: PostEco mid-applied; ROUND_ORCH Step 6b backup of THIS round preserves this state for surgical re-apply next round`). Do NOT attempt netlist revert here — that's ROUND_ORCHESTRATOR's responsibility via Step 6b.

**DO NOT** add agent judgment on top of script results. **DO NOT** reclassify FAIL as WARNING. **DO NOT** let FM proceed if exit code = 1.

**MANDATORY COPY — always copy BOTH files to AI_ECO_FLOW_DIR regardless of PASS/FAIL:**
```bash
cp <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
cp <BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<ROUND>.json      <AI_ECO_FLOW_DIR>/
```
Do this BEFORE exiting. Failure to copy means ROUND_ORCHESTRATOR and FINAL_ORCHESTRATOR cannot read Step 5 results.

**Inputs:** TAG, REF_DIR, BASE_DIR, ROUND, JIRA, AI_ECO_FLOW_DIR

**Outputs (BOTH required before exiting):**
- `<BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt` → copied to `AI_ECO_FLOW_DIR/`
- `<BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<ROUND>.json`

---

## SECONDARY CHECKS — Only when script PASS but FM ABORT in prior round

The checks below supplement the script for patterns the script cannot detect (requires netlist decompression). Only run these if the script passes but you have evidence from a prior-round FM ABORT that something was missed.

---

## MANDATORY OUTPUT CONTRACT — JSON Schema

> **Read this BEFORE running any checks.** Your final JSON MUST match this exact structure. Do NOT invent fields. Do NOT omit `check_summary` or `check8_verilog_validator`.

```json
{
  "tag": "<TAG>",
  "round": <ROUND>,
  "passed": true,
  "attempts": <N of 3>,
  "issues_found": [],
  "issues_fixed": [],
  "issues_unresolved": [],
  "warnings": [],
  "check_summary": {
    "A_stage_consistency":           "PASS | FAIL | FIXED",
    "B_port_declarations":           "PASS | FAIL | FIXED | N/A",
    "B2_missing_output_ports":       "PASS | FAIL | FIXED | N/A",
    "C_cell_insertions":             "PASS | FAIL | FIXED | N/A",
    "D_duplicate_ports":             "PASS | FAIL | FIXED | N/A",
    "E_rewire_warnings":             "PASS | WARN | N/A",
    "F_wire_dup_implicit":           "PASS | FAIL | FIXED | N/A",
    "F2_position_conflict":          "PASS | FAIL | FIXED | N/A",
    "G_port_direction_completeness": "PASS | FAIL | FIXED | N/A",
    "H_eco_cell_pin_names":          "PASS | FAIL | FIXED | N/A",
    "UNDRIVEN_dff_inputs":           "PASS | FAIL | N/A",
    "I_se_cone_mismatch":            "WARN | N/A",
    "check8_verilog_validator": {
      "Synthesize": "PASS | FAIL",
      "PrePlace":   "PASS | FAIL",
      "Route":      "PASS | FAIL",
      "errors":     []
    }
  }
}
```

**Rules for `check_summary` values:**
- `PASS` — check ran, no issues found
- `FAIL` — check found issues that could NOT be fixed inline (blocks FM)
- `FIXED` — check found issues, all were fixed inline (FM proceeds)
- `WARN` — non-blocking issue noted (FM proceeds but eco_fm_analyzer may diagnose in next round)
- `N/A` — check not applicable to this ECO (e.g., no new ports → Check G is N/A)
- `SKIPPED` — **INVALID** — validator must always run. If script not found → RuntimeError. Never SKIPPED.

**MANDATORY SELF-CHECK before writing JSON:**
```python
assert "check_summary" in result, "MISSING check_summary — do not exit without it"
assert "check8_verilog_validator" in result["check_summary"], "MISSING validator result"
assert result["check_summary"]["check8_verilog_validator"]["Synthesize"] in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
assert result["check_summary"]["check8_verilog_validator"]["PrePlace"]   in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
assert result["check_summary"]["check8_verilog_validator"]["Route"]      in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
```
If any assertion fails — **complete the missing sections before writing**. Do not exit with an incomplete JSON.

---

**ABSOLUTE RULE — Check 8 (Verilog Validator) ALWAYS runs and ALWAYS gates FM:**
Check 8 runs regardless of any other check results. A FAIL in Check 8 always blocks FM submission. Set `passed: false` if Check 8 fails, even if all other checks are PASS or N/A. Note: `manual_only` is abolished — do not reference it.

---

---

## STEP 0 — Verify eco_applier Completed Successfully

**Before any netlist scanning, read the applied JSON for this round:**
```python
applied = load(f"data/{TAG}_eco_applied_round{ROUND}.json")
verify_failed = [
    e for stage_entries in applied.values() if isinstance(stage_entries, list)
    for e in stage_entries if e.get("status") == "VERIFY_FAILED"
]
if verify_failed:
    # eco_applier hit a Checks 1-7 self-validation failure and did NOT recompress.
    # The PostEco netlist on disk is stale — do NOT submit to FM.
    first_reason = verify_failed[0].get("reason", "unknown")
    write_result(passed=False, issues_unresolved=[{
        "check": "STEP0_APPLIER_FAILED",
        "severity": "CRITICAL",
        "detail": f"{len(verify_failed)} VERIFY_FAILED entries in eco_applied_round{ROUND}.json. Reason: {first_reason}. eco_applier aborted before recompress. Escalate to ROUND_ORCHESTRATOR."
    }])
    EXIT  # Do not proceed to FM
```

**CHECKPOINT:** Only proceed to Step 1 if zero VERIFY_FAILED entries in the applied JSON.

---

## STEP 1 — Load Data

```python
applied = load(f"data/{TAG}_eco_applied_round{ROUND}.json")
study   = load(f"data/{TAG}_eco_preeco_study.json")

# Build cross-stage map: {change_name → {stage → {status, change_type, reason}}}
change_map = {}
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in applied.get(stage, []):
        name = (entry.get("instance_name") or entry.get("cell_name") or
                entry.get("signal_name") or entry.get("port_name") or "?")
        change_map.setdefault(name, {})[stage] = {
            "status": entry.get("status"), "change_type": entry.get("change_type"),
            "reason": entry.get("reason", ""), "entry": entry
        }
```

---

## STEP 2 — Checks run inside `eco_pre_fm_check.py`

**DESIGN RULE:** Every syntax issue that would cause FM ABORT (SVR-9, FM-599, FE-LINK-7, FE-LINK-2) MUST be caught and fixed here. Syntax errors MUST NEVER consume a round.

The script runs all 16 checks. If exit code = 1, read the `failures[]` array in the JSON — each entry shows which check failed and why. Apply inline fixes per the PRIMARY FLOW table, then re-run. MAX_RETRIES = 3.

| Check | What | Script function |
|-------|------|-----------------|
| 1 no_deferred_ports | No port_declaration deferred from prior round | `check_no_deferred()` |
| 2 port_declarations_applied | All port_decls landed | `check_port_declarations_applied()` |
| 3 stage_consistency | Gates inserted in all 3 stages | `check_stage_consistency()` |
| 4 no_unhandled | No UNHANDLED entries | `check_no_unhandled()` |
| 5 check8_verilog_validator | Verilog syntax clean per stage (eco_verilog_validator.sh) | `check_check8()` |
| 6 eco_cell_counts | Cell count balanced across stages | `check_eco_cell_counts()` |
| 7 cells_in_netlist | Every INSERTED cell physically present | `check_cells_in_netlist()` |
| 8 undriven_eco_nets | Every n_eco_* net has ≥ 2 references | `check_undriven_eco_nets()` |
| 9 bus_concat_intact | Bus-position renames preserved {} concat | `check_bus_concat_intact()` |
| 10 port_edits_in_netlist | All port_decl/port_conn edits in netlist | `check_port_edits_in_netlist()` |
| 11 rewires_in_netlist | All rewire edits cell.pin → new_net in netlist | `check_rewires_in_netlist()` |
| 12 semantic_verify | Full semantic study-JSON-vs-netlist equivalence | `check_semantic_verify()` |
| 13 eco_input_drivers | Per-stage ECO input pin nets have real drivers | `check_eco_input_drivers()` |
| 14 duplicate_ports | No duplicate port names in any module header | `check_duplicate_ports()` |
| 15 eco_output_pin_names | ECO cell output pin matches cell library (FE-LINK-7 prevention) | `check_eco_output_pin_names()` |
| 16 missing_output_port_decls | ECO output nets escaping module scope have port_declaration applied | `check_missing_output_port_decls()` |

**STEP 3 — RPT and STEP 4 — JSON** are written by the script automatically. No agent code required. Verify both files exist before exiting (see PRIMARY FLOW MANDATORY COPY above).

---

## Output Files

| File | Location | Purpose |
|------|---------|---------|
| `<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` | Human-readable: what was found, what was fixed, what was warned |
| `<TAG>_eco_pre_fm_check_round<ROUND>.json` | `data/` | Machine-readable: passed/failed, issues list, for orchestrators |
