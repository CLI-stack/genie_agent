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

**Statistics calculation — concrete algorithm:**
```python
cells_added = set()    # deduplicated by instance_name (same cell in 3 stages = 1 count)
cells_removed = set()  # deduplicated by instance_name
pins_rewired = 0       # cumulative across all rounds and stages

for round_n in range(1, TOTAL_ROUNDS + 1):
    data = json.load(open(f"data/{TAG}_eco_applied_round{round_n}.json"))
    for stage_entries in data.values():       # each top-level key = stage name
        if not isinstance(stage_entries, list): continue
        for entry in stage_entries:
            ct, st, name = entry.get("change_type",""), entry.get("status",""), entry.get("instance_name") or entry.get("signal_name","")
            if ct in ("new_logic","new_logic_dff","new_logic_gate") and st == "INSERTED":
                cells_added.add(name)         # set deduplicates across 3 stages automatically
            if st == "SKIPPED" and "not found in PostEco" in entry.get("reason",""):
                cells_removed.add(name)
            if st in ("APPLIED","INSERTED") and ct in ("rewire","port_connection"):
                pins_rewired += 1
```
Use `len(cells_added)`, `len(cells_removed)`, `pins_rewired` in summary.

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

<Status classification:>
  FM_PASSED                       → PASS — All 3 Formality targets verified clean.
  MANUAL_LIMIT (all 3 pass)       → PASS — All 3 targets passed (pre-existing failures waived).
  MANUAL_LIMIT (some fail)        → FAIL — MANUAL FIX NEEDED. Pre-existing failures remain; engineer must apply SVF.
  MAX_ROUNDS (any fail)           → FAIL — MAX ROUNDS REACHED. <N> rounds attempted, failures persist.
  Partial pass (1-2 targets PASS) → FAIL — PARTIAL. List which targets pass/fail.

<If PASS:>  All 3 Formality targets passed. ECO is clean.
<If FAIL:>  Manual fix required. See step5 RPT for failing points.
<If MAX:>   Rounds attempted. See per-round step5 RPTs for details.

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
  LOL = Lines Of Logic: combinational gate levels from new cell output to first register input.
  Compute by tracing forward cone from ECO gate output; count gate levels until reaching a DFF .D pin.
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
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round1.rpt
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round<N>.rpt  <- N=2..TOTAL_ROUNDS  <- one line per fix round (ROUND=1..N-1)
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt  <- one line per round
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt <- one line per round
  <AI_ECO_FLOW_DIR>/<TAG>_eco_step6_fm_verify_round<ROUND>.rpt  <- one line per round
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

Write `<BASE_DIR>/data/<TAG>_eco_report.html`. The HTML must contain the **same level of detail as eco_summary.rpt** — every step documented, not just a high-level summary.

**Generation algorithm — follow this order exactly:**
1. Read `eco_summary.rpt` (already written in Step 7a) — use its content as the canonical text source for all sections
2. For each round N = 1 to TOTAL_ROUNDS: read `eco_step4_eco_applied_roundN.rpt`, `eco_step5_pre_fm_check_roundN.rpt`, `eco_step6_fm_verify_roundN.rpt`, `eco_fm_analysis_roundN.json`
3. For missing files: write `<section> — not available` in that section; never skip a section entirely
4. Build HTML by wrapping each RPT section in `<pre>` tags with appropriate heading; embed JSON fields in readable table rows
5. Mandatory: header, final status, RTL changes, per-round table (R1…RN), statistics, timing, step file index

**Content source for each section:**

| Section | Read from |
|---------|-----------|
| Header | round_handoff.json, eco_analyze metadata |
| Final Status | eco_fm_verify.json |
| Step 1 — RTL Changes | eco_rtl_diff.json + eco_step1_rtl_diff.rpt |
| Step 2 — Net Analysis | eco_step2_fenets.rpt (full content including retries) |
| Step 3 — Netlist Study | eco_step3_collect.rpt + eco_step3_netlist_verify.rpt (all rounds) |
| Step 4 — ECO Applied | eco_step4_eco_applied_roundN.rpt (all rounds) |
| Step 5 — Pre-FM Check | eco_step5_pre_fm_check_roundN.rpt (all rounds) |
| Step 6 — FM Results | eco_step6_fm_verify_roundN.rpt + eco_fm_analysis_roundN.json (all rounds) |
| Statistics | eco_applied_roundN.json aggregated |
| Timing/LOL | timing_lol_analysis in eco_preeco_study.json |
| Step Reports | file paths to AI_ECO_FLOW_DIR |

**HTML structure — produce all sections below with full detail:**

```html
<!DOCTYPE html>
<html><head><style>
body{font-family:Arial,sans-serif;margin:20px;background:#f5f5f5;color:#333;max-width:1100px}
h1{color:#2c3e50;border-bottom:3px solid #3498db;padding-bottom:10px}
h2{color:#34495e;border-bottom:2px solid #bdc3c7;padding-bottom:6px;margin-top:24px}
h3{color:#555;margin-top:16px;border-left:4px solid #3498db;padding-left:8px}
table{border-collapse:collapse;width:100%;margin:10px 0;background:white}
th{background:#3498db;color:white;padding:8px 12px;text-align:left;font-size:12px}
td{padding:7px 12px;border-bottom:1px solid #eee;font-size:12px}
.pass{color:#27ae60;font-weight:bold} .fail{color:#e74c3c;font-weight:bold}
.warn{color:#e67e22;font-weight:bold} .abort{color:#e67e22;font-weight:bold}
.applied{color:#27ae60} .skipped{color:#999} .inserted{color:#2980b9;font-weight:bold}
.box{background:white;border:1px solid #ddd;padding:14px;margin:10px 0;border-radius:4px}
.success{background:#d4edda;border:1px solid #c3e6cb;padding:12px;margin:10px 0}
.alert{background:#fef3cd;border:1px solid #ffc107;padding:12px;margin:10px 0}
.error{background:#f8d7da;border:1px solid #f5c6cb;padding:12px;margin:10px 0}
pre,code{background:#f4f4f4;font-family:monospace;font-size:11px}
pre{padding:10px;overflow-x:auto} code{padding:2px 5px}
.section-meta{color:#666;font-size:11px;margin-bottom:4px}
</style></head><body>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- HEADER -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h1>ECO Analysis Report — JIRA <JIRA> (<TILE>)</h1>
<div class="box">
<table><tr>
  <td><b>Tag:</b> <TAG></td>
  <td><b>Tile:</b> <TILE></td>
  <td><b>JIRA:</b> <JIRA></td>
  <td><b>Rounds:</b> <TOTAL_ROUNDS></td>
  <td><b>Generated:</b> <YYYY-MM-DD HH:MM></td>
</tr><tr>
  <td colspan="5"><b>TileBuilder:</b> <REF_DIR></td>
</tr></table>
</div>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- FINAL STATUS -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Final Status</h2>
<!-- Use .success for PASS, .alert for PARTIAL/MAX_ROUNDS, .error for FAIL -->
<div class="[success|alert|error]">
  <b>FINAL STATUS: [PASS | FAIL — MANUAL FIX NEEDED | MAX ROUNDS REACHED]</b><br>
  <i>[1-2 sentence explanation of what passed, what failed, and why]</i>
</div>

<h3>FM Verification Results</h3>
<table>
<tr><th>Target</th><th>Status</th><th>Equiv Points</th><th>Failing Points</th><th>FM Tag</th></tr>
<tr>
  <td>FmEqvEcoSynthesizeVsSynRtl</td>
  <td class="[pass|fail|abort]">[PASS|FAIL|ABORT]</td>
  <td>[N]</td><td>[M]</td><td>[eco_fm_tag]</td>
</tr>
<tr>
  <td>FmEqvEcoPrePlaceVsEcoSynthesize</td>
  <td class="[pass|fail|abort]">[PASS|FAIL|ABORT]</td>
  <td>[N]</td><td>[M]</td><td>[eco_fm_tag]</td>
</tr>
<tr>
  <td>FmEqvEcoRouteVsEcoPrePlace</td>
  <td class="[pass|fail|abort]">[PASS|FAIL|ABORT]</td>
  <td>[N]</td><td>[M]</td><td>[eco_fm_tag]</td>
</tr>
</table>

<!-- If any FAIL: show failing points + mode E proof if applicable -->
<!-- [Failing points block — one row per failing DFF path, same detail as eco_summary.rpt] -->

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 1 — RTL CHANGES -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 1 — RTL Diff Analysis</h2>
<p class="section-meta">Source: eco_step1_rtl_diff.rpt | Files changed: [N] | Total changes: [N]</p>
<!-- One sub-section per RTL change, same detail as eco_summary.rpt RTL CHANGE section -->
<!-- For each change: File, Module, Change Type, Old/New Signal, Instances, Context line, Strategy -->
<h3>Change [N/TOTAL]: [change_type] — [module_name]</h3>
<table>
<tr><td><b>File</b></td><td>[rtl_file.v]</td></tr>
<tr><td><b>Module</b></td><td>[module_name] (instance: [INST_PATH])</td></tr>
<tr><td><b>Change Type</b></td><td>[wire_swap | and_term | new_logic | new_port | port_connection]</td></tr>
<tr><td><b>Old Signal</b></td><td>[old_token]</td></tr>
<tr><td><b>New Signal</b></td><td>[new_token]</td></tr>
<tr><td><b>Target Register</b></td><td>[target_register] (if applicable)</td></tr>
<tr><td><b>Instances</b></td><td>[INST_A] ([flat_net_A]), [INST_B] ([flat_net_B])</td></tr>
<tr><td><b>Strategy</b></td><td>[d_input decomposition | intermediate_net_insertion | module_port_direct_gating | direct_rewire]</td></tr>
</table>
<pre>[context_line from RTL diff]</pre>
<!-- If new_logic with gate chain: show the decomposed gate chain -->
<!-- eco_jira_d001: GATE_FUNCTION(inputs) → output_net, eco_jira_d002: ... -->

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 2 — FIND EQUIVALENT NETS -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 2 — Find Equivalent Nets</h2>
<p class="section-meta">Source: eco_step2_fenets.rpt | fenets_tag: [tag]</p>
<!-- One sub-section per net queried -->
<h3>Net [N/TOTAL]: [net_path]</h3>
<table>
<tr><th>Stage</th><th>Result</th><th>Qualifying Cells</th><th>Retries</th></tr>
<tr><td>Synthesize</td><td>[N cells | No Equiv Nets | FM-036]</td><td>[cell/pin list]</td><td>[none | retry1: deeper_path → N cells]</td></tr>
<tr><td>PrePlace</td><td>[N cells | FALLBACK]</td><td>[cell/pin list]</td><td>[none | fallback from Synthesize]</td></tr>
<tr><td>Route</td><td>[N cells | FALLBACK]</td><td>[cell/pin list]</td><td>[none | fallback from Synthesize]</td></tr>
</table>
<!-- If FM-036 pivot: explain pivot strategy and target register used -->
<!-- If No-Equiv-Nets retry: show original query, retry path, outcome -->

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 3 — NETLIST STUDY (all rounds) -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 3 — PreEco Netlist Study</h2>
<!-- Round 1: from eco_step3_collect.rpt + eco_step3_netlist_verify.rpt -->
<h3>Round 1 — Collect Pass</h3>
<p class="section-meta">Source: eco_step3_collect.rpt</p>
<table>
<tr><th>Entry Type</th><th>Count</th><th>Confirmed</th><th>Excluded</th></tr>
<tr><td>new_logic_gate</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>new_logic_dff</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>port_declaration</td><td>[N]</td><td>[N]</td><td>[N] ([N] skipped implicit wire)</td></tr>
<tr><td>port_connection</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>rewire</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>d_input_chains</td><td>[N chains, N gates]</td><td>—</td><td>[N decompose_failed]</td></tr>
</table>
<!-- Key notes from collect.rpt: per-stage net aliases, CTS clock renames, reset signal BBNet risks, etc. -->
<h3>Round 1 — Verifier Enrich (14 Checks)</h3>
<p class="section-meta">Source: eco_step3_netlist_verify.rpt</p>
<table>
<tr><th>Check</th><th>Description</th><th>Result</th></tr>
<tr><td>1 GAP-15</td><td>and_term module_port_direct_gating</td><td>[N checked, N corrected]</td></tr>
<tr><td>2 Per-stage nets</td><td>Gate input resolution all stages</td><td>[N enriched, N UNRESOLVED]</td></tr>
<tr><td>3 DFF pins</td><td>Clock/scan/data per stage</td><td>[N enriched, N CTS renames]</td></tr>
<tr><td>4 Wire decls</td><td>needs_explicit_wire_decl flags</td><td>[N set]</td></tr>
<tr><td>5–14 ...</td><td>...</td><td>...</td></tr>
<tr><td><b>TOTAL</b></td><td>Auto-added entries by verifier</td><td>[N new entries]</td></tr>
</table>
<!-- If Round 2+ re-study: show what changed per round -->
<!-- [Round N re-study: failure_mode, entries updated, force_reapply entries] -->

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 4 — ECO APPLIED (all rounds) -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 4 — ECO Changes Applied</h2>
<!-- One sub-section per round -->
<h3>Round [N] — [mode: Fresh | Surgical Patch]</h3>
<p class="section-meta">Source: eco_step4_eco_applied_roundN.rpt | applied=[N] inserted=[N] skipped=[N] vf=[N]</p>
<table>
<tr><th>Stage</th><th>Cell / Signal</th><th>Type</th><th>Status</th><th>Detail</th></tr>
<!-- One row per APPLIED/INSERTED/SKIPPED/VERIFY_FAILED entry -->
<!-- ALREADY_APPLIED entries: group and show as "N entries ALREADY_APPLIED (carried from Round 1)" -->
<tr><td>All</td><td>[instance_name]</td><td>[new_logic_gate|rewire|port_declaration...]</td>
    <td class="[applied|inserted|skipped]">[APPLIED|INSERTED|SKIPPED]</td>
    <td>[reason or detail]</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 5 — PRE-FM CHECK (all rounds) -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 5 — Pre-FM Quality Check</h2>
<h3>Round [N]</h3>
<p class="section-meta">Source: eco_step5_pre_fm_check_roundN.rpt</p>
<table>
<tr><th>Check</th><th>Result</th><th>Issues Found</th><th>Issues Fixed</th></tr>
<tr><td>Check 8 — Verilog Validator</td><td class="[pass|fail]">[PASS|FAIL]</td>
    <td>[Synth: PASS, PP: PASS, Route: PASS | errors]</td><td>[N fixed inline]</td></tr>
<tr><td>Check A — Stage consistency</td><td class="pass">PASS</td><td>0</td><td>0</td></tr>
<!-- All checks A-I with their result -->
<tr><td><b>OVERALL</b></td><td class="[pass|fail]"><b>[PASS|FAIL]</b></td>
    <td>[N total]</td><td>[N fixed, N unresolved]</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- STEP 6 — FM VERIFICATION (all rounds) -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Step 6 — PostEco Formality Verification</h2>
<!-- One sub-section per round -->
<h3>Round [N] — FM Tag: [eco_fm_tag]</h3>
<p class="section-meta">Source: eco_step6_fm_verify_roundN.rpt</p>
<table>
<tr><th>Target</th><th>Status</th><th>Failing</th><th>Root Cause / Action</th></tr>
<tr><td>FmEqvEcoSynthesizeVsSynRtl</td>
    <td class="[pass|fail|abort]">[PASS|FAIL|ABORT]</td>
    <td>[N | N/A]</td>
    <td>[none | failure_mode: X — revised_changes: Y]</td></tr>
<!-- ... -->
</table>
<!-- If ABORT: show abort_type and what STEP F attempted (SVR-14 fix, pin fix, etc.) -->
<!-- If FAIL: show eco_fm_analysis diagnosis, failure_mode, sample failing points -->
<!-- If FAIL round 2+: show what re_studier changed and why -->

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- ECO STATISTICS -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>ECO Statistics</h2>
<div class="box">
<table>
<tr><th>Metric</th><th>Synthesize</th><th>PrePlace</th><th>Route</th></tr>
<tr><td>Cells Added</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>Cells Removed</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>Pins Applied (all rounds)</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>Pins Skipped</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
<tr><td>Verify Failed</td><td>[N]</td><td>[N]</td><td>[N]</td></tr>
</table>
</div>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- TIMING & LOL -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Timing & LOL Impact</h2>
<p class="section-meta">LOL = Lines Of Logic: gate levels from ECO cell output to first register input</p>
<!-- One row per ECO change with timing_lol_analysis -->
<table>
<tr><th>Signal</th><th>Old Driver</th><th>New Driver</th><th>LOL Added</th><th>Estimate</th><th>Reasoning</th></tr>
<tr><td>[signal]</td><td>[cell (type)]</td><td>[eco_cell (type)]</td>
    <td>[N levels]</td>
    <td class="[pass|warn|fail]">[BETTER|NEUTRAL|RISK]</td>
    <td>[1-sentence reasoning]</td></tr>
</table>

<!-- ═══════════════════════════════════════════════════════════ -->
<!-- PER-STEP REPORT INDEX -->
<!-- ═══════════════════════════════════════════════════════════ -->
<h2>Per-Step Report Files</h2>
<p class="section-meta">All files at: <AI_ECO_FLOW_DIR>/</p>
<table>
<tr><th>Step</th><th>File</th></tr>
<tr><td>Step 1 RTL Diff</td><td><code>[TAG]_eco_step1_rtl_diff.rpt</code></td></tr>
<tr><td>Step 2 Fenets</td><td><code>[TAG]_eco_step2_fenets.rpt</code></td></tr>
<tr><td>Step 2 Raw FM</td><td><code>[fenets_tag]_find_equivalent_nets_raw.rpt</code></td></tr>
<!-- [One row per retry rpt if any] -->
<tr><td>Step 3 Collect R1</td><td><code>[TAG]_eco_step3_collect.rpt</code></td></tr>
<tr><td>Step 3 Verify R1</td><td><code>[TAG]_eco_step3_netlist_verify.rpt</code></td></tr>
<tr><td>Step 3 Study R1</td><td><code>[TAG]_eco_step3_netlist_study_round1.rpt</code></td></tr>
<!-- [One row per round for steps 3-6] -->
<tr><td>Summary RPT</td><td><code>[TAG]_eco_summary.rpt</code></td></tr>
</table>

</body></html>
```

**Rules for content population:**
- Every `[placeholder]` must be filled from actual data files — never leave placeholders in the output
- ALREADY_APPLIED entries in Step 4: group as a single summary row per round (e.g., "75 entries ALREADY_APPLIED — carried from Round 1") rather than listing each one
- If a check/step produced 0 issues → show "0 — PASS" not "N/A"
- Round loop sections (Steps 3-6): repeat sub-sections for each round that ran
- Failing points: show up to 10 sample paths; if more, show count and note "(see Step 6 RPT for full list)"

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
