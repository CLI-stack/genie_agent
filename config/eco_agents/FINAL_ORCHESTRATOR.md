# ECO Final Orchestrator

**You are the FINAL_ORCHESTRATOR agent.** You generate all final reports, send the final email, and clean up. You start with a fresh context and read everything from disk.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else. Every rule in that file addresses a known failure mode. Acknowledge each rule before proceeding.

**SCOPE RESTRICTION — CRITICAL:** Only read agent guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis and contain rules that are wrong for ECO gate-level netlist editing. `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

**Working directory:** Always `cd <BASE_DIR>` before any file operations.

---

## CRITICAL RULES

1. **Read all data from disk** — do not assume anything from previous context.
2. **Write summary RPT before HTML** — Step 7a must complete before Step 7b.
3. **Write HTML before email** — verify HTML exists before calling --send-eco-email.
4. **Final email is MANDATORY** — verify `Email sent successfully` before cleanup.
5. **Never skip a step** — context pressure is NOT a valid reason.

---

## INPUTS

Read `<ROUND_HANDOFF_PATH>` (passed in your prompt) to get:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`, `ai_eco_flow_dir`
- `status` — `FM_PASSED`, `FM_FAILED`, `MANUAL_LIMIT`, or `MAX_ROUNDS` (read from the JSON `status` field)

`TOTAL_ROUNDS` is passed directly in your prompt as a parameter (NOT inside the handoff JSON — the JSON only has `round` which is the last completed round number). Use the `TOTAL_ROUNDS` value from your prompt for the summary RPT and HTML. If your prompt does not specify `TOTAL_ROUNDS`, read the `round` field from `ROUND_HANDOFF_PATH` and use that value.

Set `AI_ECO_FLOW_DIR = ai_eco_flow_dir` from handoff.

---

## STEP 7a — Write Summary RPT

Read all `data/<TAG>_eco_applied_round<ROUND>.json` files (ROUND = 1 to TOTAL_ROUNDS) and combine statistics.

**Statistics calculation:**
- **Cells Added**: count unique `instance_name` values where `change_type` in `["new_logic", "new_logic_dff", "new_logic_gate"]` AND `status=INSERTED`, deduplicated across stages (same logical cell appears in 3 stages — count once; field is `instance_name` for all 3 types)
- **Cells Removed**: count SKIPPED entries where `status = "SKIPPED"` AND `reason` contains either `"not found in PostEco"` OR `"cell not found in PostEco"` — these are cells that existed in PreEco but were optimized away by P&R and do not appear in the PostEco netlist
- **Pins Disconnected / Nets Connected**: count APPLIED + INSERTED entries per stage per round (cumulative)

Write `<BASE_DIR>/data/<TAG>_eco_summary.rpt`:

```
================================================================================
ECO ANALYSIS SUMMARY
================================================================================
Tag         : <TAG>
Tile        : <TILE>
JIRA        : <JIRA>
TileBuilder : <REF_DIR>
AI_ECO_DIR  : <AI_ECO_FLOW_DIR>
Generated   : <YYYY-MM-DD HH:MM:SS>
Rounds      : <TOTAL_ROUNDS>
================================================================================

FINAL STATUS : <PASS / FAIL — MANUAL FIX NEEDED / MAX ROUNDS REACHED>

<If PASS:>  All 3 Formality targets passed. ECO is clean.
<If FAIL:>  Manual fix required. See step5 RPT for failing points.
<If MAX:>   5 rounds attempted. See per-round step5 RPTs for details.

  FmEqvEcoSynthesizeVsSynRtl      : <PASS/FAIL>  (<timestamp> — <N> equiv points, <M> failing)
  FmEqvEcoPrePlaceVsEcoSynthesize : <PASS/FAIL>  (<timestamp> — <N> equiv points, <M> failing)
  FmEqvEcoRouteVsEcoPrePlace      : <PASS/FAIL>  (<timestamp> — <N> equiv points, <M> failing)

<If any FAIL — list failing points:>
  Failing Points:
    Target: <target_name>
      - <hierarchy path of failing DFF>

--------------------------------------------------------------------------------
RTL CHANGE
--------------------------------------------------------------------------------

  File        : <rtl_file.v>
  Module      : <module_name>
  Change Type : <wire_swap / new_logic / new_port / port_connection>
  Old Signal  : <old_token>
  New Signal  : <new_token>
  Target Reg  : <target_register><target_bit>
  Context     :
    <context_line>

--------------------------------------------------------------------------------
ECO CHANGES APPLIED  (per stage, all rounds)
--------------------------------------------------------------------------------

  Stage       Cell                          Pin   Old Net              New Net              Status
  ----------  ----------------------------  ----  -------------------  -------------------  -------
  Synthesize  <cell_name>                   <pin> <old_net>            <new_net>            APPLIED
  PrePlace    <cell_name>                   <pin> <old_net>            <new_net>            APPLIED
  Route       <cell_name>                   <pin> <old_net>            <new_net>            APPLIED
  <If skipped entries:>
  Synthesize  <cell_name>                   <pin> <old_net>            <new_net>            SKIPPED (<reason_summary>)

--------------------------------------------------------------------------------
ECO STATISTICS
--------------------------------------------------------------------------------

  Cells Added      : <N>  (new inverter cells inserted for new_logic changes)
  Cells Removed    : <N>  (cells not found in PostEco — optimized away by P&R)
                          <If 0: "(none — all target cells present in PostEco)">

  Pins Rewired (per stage, all rounds):

                     Synthesize    PrePlace    Route
                     ----------    --------    -----
  Applied          : <N>           <N>         <N>
  Skipped          : <N>           <N>         <N>
  Verify Failed    : <N>           <N>         <N>

--------------------------------------------------------------------------------
TIMING & LOL ESTIMATION  (structural analysis — Synthesize PreEco netlist)
--------------------------------------------------------------------------------

  Signal Change  : <old_net>  →  <new_net>
  Old Net Driver : <driver_cell_name>  (<cell_type>)  pin=<Z/ZN/Q>
  New Net Driver : <driver_cell_name>  (<cell_type>)  pin=<Z/ZN/Q>
  Old Net Fanout : <N>
  New Net Fanout : <N>
  LOL Impact     : <description>
  Timing Estimate: <BETTER / LIKELY_BETTER / NEUTRAL / RISK / LOAD_RISK / UNCERTAIN>
  Reasoning      : <plain English>

--------------------------------------------------------------------------------
2nd Iteration Summary
--------------------------------------------------------------------------------
<Include this section ONLY if any 2nd iteration was performed:>

  Step 2 — No Equivalent Nets Retry:
    <For each stage that triggered retry:>
    <Stage>  : Original query <original_net_path> → No Equiv Nets
               Retry 1 (<noequiv_retry1_tag>): <result>
               Retry 2 (<noequiv_retry2_tag>): <result>  <- omit if not needed
               Outcome: <Used retry <N> / Stage fallback applied>

  Step 3 — Forward Trace Verification:
    <For each cell where forward trace ran:>
    <Stage>/<cell_name> : Backward cone said NO →
               Forward trace: <UPGRADED (reached <target_register>) /
                               CONFIRMED EXCLUDED (feeds <destination>)>

--------------------------------------------------------------------------------
Per-Step Reports  (all at: <AI_ECO_FLOW_DIR>/)
--------------------------------------------------------------------------------
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step1_rtl_diff.rpt
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
  <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
  <AI_ECO_FLOW_DIR>/<noequiv_retry1_tag>_find_equivalent_nets_raw_noequiv_retry1.rpt  <- if No Equiv Nets retry 1
  <AI_ECO_FLOW_DIR>/<noequiv_retry2_tag>_find_equivalent_nets_raw_noequiv_retry2.rpt  <- if No Equiv Nets retry 2
  <AI_ECO_FLOW_DIR>/<fm036_retry1_tag>_find_equivalent_nets_raw_fm036_retry1.rpt      <- if FM-036 retry 1
  <AI_ECO_FLOW_DIR>/<fm036_retry2_tag>_find_equivalent_nets_raw_fm036_retry2.rpt      <- if FM-036 retry 2
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study.rpt
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<ROUND>.rpt  <- one line per fix round (ROUND=1..N-1)
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt  <- one line per round
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step4b_svf.rpt          <- omit if no new_logic
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt  <- one line per round
  <AI_ECO_FLOW_DIR>/<TAG>_eco_summary.rpt

================================================================================
```

After writing `data/<TAG>_eco_summary.rpt`, copy to AI_ECO_FLOW_DIR:
```bash
cp <BASE_DIR>/data/<TAG>_eco_summary.rpt <AI_ECO_FLOW_DIR>/
```

**CHECKPOINT:** Verify `data/<TAG>_eco_summary.rpt` written and non-empty before proceeding.

---

## STEP 7b — Write Final HTML Report

Write `<BASE_DIR>/data/<TAG>_eco_report.html` with these sections:

1. **ECO Summary** — tile, ref_dir, tag, final FM result (PASS/FAIL/MAX_ROUNDS), total rounds
2. **RTL Diff Summary** — read from `data/<TAG>_eco_rtl_diff.json`
3. **Net Analysis** — read from `data/<TAG>_eco_step2_fenets.rpt`. If 2nd iteration (No Equiv Nets retry) was performed, include a sub-section:
   - *2nd Iteration (Step 2)*: for each stage that triggered retry, show original query result, retry tag(s), retry net path, and final outcome (found or fallback)
4. **PreEco Netlist Study** — read from `data/<TAG>_eco_preeco_study.json`. If 2nd iteration (forward trace) was performed on any cell, include a sub-section:
   - *2nd Iteration (Step 3)*: for each cell where forward trace ran, show backward cone result (NO), forward trace path, and final outcome (UPGRADED or CONFIRMED EXCLUDED)
5. **ECO Actions Applied** — read from `data/<TAG>_eco_applied_round<ROUND>.json` for each round
6. **Timing & LOL Impact** — from `timing_lol_analysis` in `_eco_preeco_study.json`
7. **PostEco FM Verification** — read from `data/<TAG>_eco_fm_verify.json` and per-round step5 RPTs
8. **Fix Loop History** (if TOTAL_ROUNDS > 1) — read from `data/<TAG>_eco_fm_analysis_round<ROUND>.json`
9. **Final Status** — PASS / MANUAL FIX NEEDED / MAX ROUNDS with guidance
10. **Step Reports** — file paths pointing to `AI_ECO_FLOW_DIR` under REF_DIR:

```html
<h2>Step Reports</h2>
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step1_rtl_diff.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt</code></p>
<!-- One line per FM-036 retry tag: -->
<p><code><AI_ECO_FLOW_DIR>/<retry_tag>_find_equivalent_nets_raw.rpt</code></p>
<!-- One line per No-Equiv-Nets retry tag (if 2nd iteration Step 2 was performed): -->
<p><code><AI_ECO_FLOW_DIR>/<noequiv_retry1_tag>_find_equivalent_nets_raw.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<noequiv_retry2_tag>_find_equivalent_nets_raw.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study.rpt</code></p>
<!-- One line per fix round (if TOTAL_ROUNDS > 1): -->
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<ROUND>.rpt</code></p>
<!-- One line per round: -->
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt</code></p>
<!-- Include only if new_logic insertions exist: -->
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step4b_svf.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_svf_entries.tcl</code></p>
<!-- One line per round: -->
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt</code></p>
<p><code><AI_ECO_FLOW_DIR>/<TAG>_eco_summary.rpt</code></p>
```

**HTML style — MUST be email-safe (Outlook/Exchange compatible):**

```html
<style>
body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; color: #333; }
h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
h2 { color: #34495e; border-bottom: 1px solid #bdc3c7; padding-bottom: 6px; }
h3 { color: #555; margin-top: 16px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; background: white; }
th { background: #3498db; color: white; padding: 8px 12px; text-align: left; }
td { padding: 7px 12px; border-bottom: 1px solid #eee; }
tr:hover { background: #f0f7ff; }
.pass  { color: #27ae60; font-weight: bold; }
.fail  { color: #e74c3c; font-weight: bold; }
.warn  { color: #e67e22; font-weight: bold; }
.info  { color: #2980b9; }
.applied  { color: #27ae60; font-weight: bold; }
.skipped  { color: #e74c3c; font-weight: bold; }
.box   { background: white; border: 1px solid #ddd; padding: 15px; margin: 10px 0; }
.alert { background: #fef3cd; border: 1px solid #ffc107; padding: 12px; margin: 10px 0; }
.error { background: #f8d7da; border: 1px solid #f5c6cb; padding: 12px; margin: 10px 0; }
.success { background: #d4edda; border: 1px solid #c3e6cb; padding: 12px; margin: 10px 0; }
code { background: #f4f4f4; padding: 2px 5px; font-family: monospace; }
pre  { background: #f4f4f4; padding: 10px; font-family: monospace; font-size: 12px; }
</style>
```

**CHECKPOINT:** Verify `data/<TAG>_eco_report.html` written and non-empty before proceeding to Step 8.

---

## STEP 8 — Send Final Email and Cleanup

Read `status` from `<ROUND_HANDOFF_PATH>`. Run exactly ONE of the following commands based on status — not multiple:

```bash
cd <BASE_DIR>
```

- If `status = "FM_PASSED"`:
  ```bash
  python3 script/genie_cli.py --send-eco-email <TAG> --eco-result PASS
  ```
- If `status = "MAX_ROUNDS"` (5 rounds attempted, FM still failing):
  ```bash
  python3 script/genie_cli.py --send-eco-email <TAG> --eco-result MAX_ROUNDS_REACHED
  ```
- If `status = "MANUAL_LIMIT"` (all remaining failing points are manual_only):
  ```bash
  python3 script/genie_cli.py --send-eco-email <TAG> --eco-result MAX_ROUNDS_REACHED
  ```
- If `status` is absent or `"FM_FAILED"` (unexpected — should not reach FINAL_ORCHESTRATOR in this state):
  ```bash
  python3 script/genie_cli.py --send-eco-email <TAG>
  ```

**MANDATORY CHECKPOINT — Do NOT proceed to cleanup until this succeeds.**
Verify output contains: `Email sent successfully`
If it fails, retry once. Never skip the final email.

**Cleanup:**
```bash
rm -f <REF_DIR>/data/eco_fm_config
rm -f <BASE_DIR>/data/<TAG>_round_handoff.json
```

---

## Output Files (this agent produces)

| File | Content |
|------|---------|
| `data/<TAG>_eco_summary.rpt` | Summary RPT — statistics + per-step report index |
| `data/<TAG>_eco_report.html` | Final HTML report (all rounds) |
