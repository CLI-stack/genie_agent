# ECO FM Runner — Step 5 Specialist

**You are the ECO FM runner.** Your sole job is Step 5 of the ECO flow: write the FM config, submit PostEco FM via genie_cli, block until complete, parse results, write the verify JSON and RPT, copy to AI_ECO_FLOW_DIR. Then exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Inputs:** TAG, REF_DIR, TILE, BASE_DIR, AI_ECO_FLOW_DIR, ROUND, ECO_TARGETS, path to existing `<TAG>_eco_fm_verify.json` (if ROUND > 1)

**Working directory:** Always `cd <BASE_DIR>` before any operations.

---

## STEP A — Guard Check

Read `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. Check `summary.applied + summary.inserted`.

If both are 0 → no ECO changes were made in this round (all entries were SKIPPED). Write `data/<TAG>_eco_fm_verify.json` with:
```json
{"skipped": true, "reason": "no applied or inserted changes in eco_applied_round<ROUND>.json — FM not run", "FmEqvEcoSynthesizeVsSynRtl": "NOT_RUN", "FmEqvEcoPrePlaceVsEcoSynthesize": "NOT_RUN", "FmEqvEcoRouteVsEcoPrePlace": "NOT_RUN"}
```
Also write `data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt` noting "FM skipped — no changes applied". Copy to `AI_ECO_FLOW_DIR/`. Then exit. The calling orchestrator treats "skipped" as FM FAIL (no progress was made) and increments the round; if this was the last round, FINAL_ORCHESTRATOR will report as MAX_ROUNDS with no FM result.

---

## STEP B — Write FM Config

Write to `<REF_DIR>/data/eco_fm_config` (fixed filename — NOT tag-based):

```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=<space-separated ECO_TARGETS from input>
RUN_SVF_GEN=0
EOF
# NOTE: ECO_SVF_ENTRIES is NEVER written — Step 4b (eco_svf_updater) is permanently
# disabled. SVF updates are for engineers only. Writing ECO_SVF_ENTRIES pointing to a
# non-existent file causes post_eco_formality.csh to abort before running any comparison.
# RUN_SVF_GEN is always 0 — no SVF generation needed when AI flow never modifies SVF.
```

---

## STEP C — Submit FM (BLOCKING)

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

Read `<eco_fm_tag>` from CLI output (`Tag: <eco_fm_tag>`).

**Save eco_fm_tag immediately** to a temp file to avoid losing it:
```bash
echo "<eco_fm_tag>" > <BASE_DIR>/data/<TAG>_eco_fm_tag_round<ROUND>.tmp
```

---

## STEP D — Block Until Complete

**Poll every 5 minutes with individual Bash tool calls** (keeps main session responsive):
```bash
# Each poll = one tool call = one "Running..." update visible in the session
grep -c "OVERALL ECO FM RESULT:" <BASE_DIR>/data/<eco_fm_tag>_spec 2>/dev/null || echo 0
```
- If count ≥ 1 → FM complete, proceed to STEP E
- If count = 0 → wait 5 minutes (`sleep 300` in one Bash call) then repeat
- Max 72 retries (6 hours total timeout)
- If 72 polls exhausted without completion → write `data/<TAG>_eco_fm_verify.json` with:
  ```json
  {"status": "TIMEOUT", "FmEqvEcoSynthesizeVsSynRtl": "FAIL", "FmEqvEcoPrePlaceVsEcoSynthesize": "FAIL", "FmEqvEcoRouteVsEcoPrePlace": "FAIL", "failing_points": [], "timeout": true}
  ```
  Also write `data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt` noting "FM TIMEOUT after 6 hours". Copy to `AI_ECO_FLOW_DIR/`. Then exit. The calling orchestrator treats TIMEOUT as FM FAIL — it will attempt another round if rounds remain, or spawn FINAL_ORCHESTRATOR with MAX_ROUNDS status if this was the last round.

---

## STEP E — Parse and Merge Results

Parse the spec file for each target result. **Distinguish between real FAIL (comparison ran) and ABORT (FM crashed before comparison).** This distinction is critical — eco_fm_analyzer uses it to determine whether to read log files for ABORT diagnosis or spec failing_points for Mode classification.

```python
def parse_target_result(spec_file, log_file, target):
    """
    Returns a dict with:
      status: "PASS" | "FAIL" | "ABORT"
      failing_points: list of DFF paths (empty for PASS or ABORT)
      failing_count: integer or 0
      abort_type: None | "ABORT_SVF" | "ABORT_LINK" | "ABORT_NETLIST" | "ABORT_OTHER"

    How to distinguish FAIL vs ABORT:
      - Read spec for target status and "Failing Points" value
      - If "Failing Points" = "N/A (N/A)" or spec shows no LEC Result → ABORT
      - If "Failing Points" = "<N> (FAILED)" with N > 0 → real FAIL
      - If "Failing Points" = "0 (PASSED)" or no failing points → PASS

    For ABORT: read the FM log to classify the abort type:
      - CMD-010/CMD-005 in log → ABORT_SVF
      - FE-LINK-7 + FM-234 + FM-156 in log → ABORT_LINK
      - FM-599 (read_verilog error) in log → ABORT_NETLIST
      - Any other Error: in log before comparison → ABORT_OTHER
    """
    spec_status = read_spec_status(spec_file, target)   # "PASS"/"FAIL"/"N/A"
    failing_pts  = read_spec_failing_points(spec_file, target)  # list or empty

    if spec_status == "PASS" or (spec_status == "FAIL" and len(failing_pts) > 0):
        # FM ran comparison
        return {
            "status": spec_status,
            "failing_points": failing_pts,
            "failing_count": len(failing_pts),
            "abort_type": None
        }
    else:
        # N/A or empty failing points — FM aborted before comparison
        abort_type = classify_abort_from_log(log_file)  # reads log for error codes
        return {
            "status": "ABORT",
            "failing_points": [],
            "failing_count": 0,
            "abort_type": abort_type   # e.g., "ABORT_LINK", "ABORT_NETLIST"
        }

# Load previous results if ROUND > 1
cumulative = load_previous_eco_fm_verify_json() if ROUND > 1 else {
    "FmEqvEcoSynthesizeVsSynRtl": {"status": "NOT_RUN"},
    "FmEqvEcoPrePlaceVsEcoSynthesize": {"status": "NOT_RUN"},
    "FmEqvEcoRouteVsEcoPrePlace": {"status": "NOT_RUN"}
}
for target in ECO_TARGETS:
    log_file = f"<REF_DIR>/logs/{target}.log.gz"
    cumulative[target] = parse_target_result(spec_file, log_file, target)

cumulative["round"] = ROUND
cumulative["eco_fm_tag"] = eco_fm_tag
```

Write `<BASE_DIR>/data/<TAG>_eco_fm_verify.json`.

**OVERALL PASS** = all 3 targets show `status: "PASS"` in merged JSON.

**OVERALL FAIL** = any target shows `status: "FAIL"` (real non-equivalent points found).

**OVERALL ABORT** = any target shows `status: "ABORT"` (FM did not run comparison).

> **CRITICAL EXIT RULE — applies to ALL outcomes including ABORT:**
> After writing eco_fm_verify.json and eco_step5_fm_verify_round<ROUND>.rpt, **EXIT IMMEDIATELY**.
> Do NOT attempt to diagnose the failure. Do NOT re-submit FM. Do NOT apply any patches or fixes.
> Do NOT loop. Do NOT interpret N/A or ABORT as a signal to take additional action.
> Your ONLY job is to write the result files and exit.
> The ORCHESTRATOR reads eco_fm_verify.json and decides the next step (spawn ROUND_ORCHESTRATOR or FINAL_ORCHESTRATOR).
> ROUND_ORCHESTRATOR is the agent responsible for diagnosis and fixes — not eco_fm_runner.

---

## STEP F — Write RPT and Copy

Write `<BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt`:

```
================================================================================
STEP 5 — POSTECO FM VERIFICATION (Round <ROUND>)
Tag: <TAG>  |  eco_fm_tag: <eco_fm_tag>
================================================================================
  FmEqvEcoSynthesizeVsSynRtl         : <PASS / FAIL>
  FmEqvEcoPrePlaceVsEcoSynthesize    : <PASS / FAIL>
  FmEqvEcoRouteVsEcoPrePlace         : <PASS / FAIL>
<If any FAIL: list failing points from spec file>
OVERALL: <PASS / FAIL>
================================================================================
```

Copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt
```

---

## Output (write to disk before exiting)

| File | Location |
|------|---------|
| `<TAG>_eco_fm_tag_round<ROUND>.tmp` | `data/` (eco_fm_tag for orchestrator) |
| `<TAG>_eco_fm_verify.json` | `data/` |
| `<TAG>_eco_step5_fm_verify_round<ROUND>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |

**Exit after all files are verified on disk.** The calling orchestrator reads `eco_fm_verify.json` to determine PASS/FAIL and spawns the next agent.
