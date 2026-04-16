# ECO Final Orchestrator

**You are the FINAL_ORCHESTRATOR agent.** You generate all final reports, send the final email, and clean up. You start with a fresh context and read everything from disk.

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
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `TOTAL_ROUNDS` — total number of rounds run
- `status` — `FM_PASSED`, `FM_FAILED`, or `MAX_ROUNDS`

---

## STEP 7a — Write Summary RPT

Read all `data/<TAG>_eco_applied_round<N>.json` files (N = 1 to TOTAL_ROUNDS) and combine statistics.

**Statistics calculation:**
- **Cells Added**: count unique `inv_inst` values where `change_type=new_logic` AND `status=INSERTED`, deduplicated across stages (same logical cell appears in 3 stages — count once)
- **Cells Removed**: count SKIPPED entries where `reason` contains "not found in PostEco"
- **Pins Disconnected / Nets Connected**: count APPLIED + INSERTED entries per stage per round (cumulative)

Write `<BASE_DIR>/data/<TAG>_eco_summary.rpt`:

```
================================================================================
ECO ANALYSIS SUMMARY
================================================================================
Tag         : <TAG>
Tile        : <TILE>
JIRA        : DEUMCIPRTL-<JIRA>
TileBuilder : <REF_DIR>
Generated   : <YYYY-MM-DD HH:MM:SS>
Rounds      : <TOTAL_ROUNDS>
================================================================================

FINAL STATUS : <PASS / FAIL — MANUAL FIX NEEDED / MAX ROUNDS REACHED>

<If PASS:>  All 3 Formality targets passed. ECO is clean.
<If FAIL:>  Manual fix required. See step5 RPT for failing points.
<If MAX:>   5 rounds attempted. See per-round step5 RPTs for details.

--------------------------------------------------------------------------------
ECO STATISTICS  (from eco_applied_round<N>.json — all rounds combined)
--------------------------------------------------------------------------------

  Cells Added      : <N>  (new inverter cells inserted for new_logic changes)
  Cells Removed    : <N>  (cells not found in PostEco — optimized away by P&R)
                          <If 0: "(none — all target cells present in PostEco)">

  Pins Disconnected / Nets Connected (per stage, all rounds):

                     Synthesize    PrePlace    Route
                     ----------    --------    -----
  Pins Disconnected: <N>           <N>         <N>
  Nets Connected   : <N>           <N>         <N>
  Skipped          : <N>           <N>         <N>
  Verify Failed    : <N>           <N>         <N>

--------------------------------------------------------------------------------
TIMING & LOL ESTIMATION  (structural analysis — Synthesize PreEco netlist)
--------------------------------------------------------------------------------

  Signal Change : <old_net>  →  <new_net>
  Old Net Driver: <driver_cell_name>  (<cell_type>)  pin=<Z/ZN/Q>
  New Net Driver: <driver_cell_name>  (<cell_type>)  pin=<Z/ZN/Q>
  Old Net Fanout: <N>
  New Net Fanout: <N>
  LOL Impact    : <description>
  Timing Estimate: <BETTER / LIKELY_BETTER / NEUTRAL / RISK / LOAD_RISK / UNCERTAIN>
  Reasoning     : <plain English>

--------------------------------------------------------------------------------
Per-Step Reports
--------------------------------------------------------------------------------
  <BASE_DIR>/data/<TAG>_eco_step1_rtl_diff.rpt
  <BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt
  <BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt
  <BASE_DIR>/data/<TAG>_eco_step3_netlist_study.rpt
  <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<N>.rpt  <- one line per round
  <BASE_DIR>/data/<TAG>_eco_step4b_svf.rpt          <- omit if no new_logic
  <BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<N>.rpt  <- one line per round
  <BASE_DIR>/data/<TAG>_eco_summary.rpt

================================================================================
```

**CHECKPOINT:** Verify `data/<TAG>_eco_summary.rpt` written and non-empty before proceeding.

---

## STEP 7b — Write Final HTML Report

Write `<BASE_DIR>/data/<TAG>_eco_report.html` with these sections:

1. **ECO Summary** — tile, ref_dir, tag, final FM result (PASS/FAIL/MAX_ROUNDS), total rounds
2. **RTL Diff Summary** — read from `data/<TAG>_eco_rtl_diff.json`
3. **Net Analysis** — read from `data/<TAG>_eco_step2_fenets.rpt`
4. **PreEco Netlist Study** — read from `data/<TAG>_eco_preeco_study.json`
5. **ECO Actions Applied** — read from `data/<TAG>_eco_applied_round<N>.json` for each round
6. **Timing & LOL Impact** — from `timing_lol_analysis` in `_eco_preeco_study.json`
7. **PostEco FM Verification** — read from `data/<TAG>_eco_fm_verify.json` and per-round step5 RPTs
8. **Fix Loop History** (if TOTAL_ROUNDS > 1) — read from `data/<TAG>_eco_fm_analysis_round<N>.json`
9. **Final Status** — PASS / MANUAL FIX NEEDED / MAX ROUNDS with guidance
10. **Step Reports** — file paths only:

```html
<h2>Step Reports</h2>
<p><code><BASE_DIR>/data/<TAG>_eco_step1_rtl_diff.rpt</code></p>
<p><code><BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt</code></p>
<p><code><BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt</code></p>
<!-- One line per retry tag if FM-036 retries occurred: -->
<p><code><BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw.rpt</code></p>
<p><code><BASE_DIR>/data/<TAG>_eco_step3_netlist_study.rpt</code></p>
<!-- One line per round: -->
<p><code><BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<N>.rpt</code></p>
<!-- Include only if new_logic insertions exist: -->
<p><code><BASE_DIR>/data/<TAG>_eco_step4b_svf.rpt</code></p>
<p><code><BASE_DIR>/data/<TAG>_eco_svf_entries.tcl</code></p>
<!-- One line per round: -->
<p><code><BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<N>.rpt</code></p>
<p><code><BASE_DIR>/data/<TAG>_eco_summary.rpt</code></p>
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

```bash
cd <BASE_DIR>

# FM PASSED
python3 script/genie_cli.py --send-eco-email <TAG> --eco-result PASS

# Max rounds reached (FM still failing)
python3 script/genie_cli.py --send-eco-email <TAG> --eco-result MAX_ROUNDS_REACHED

# No changes were applied at all
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
