# ECO Analyze Orchestrator Guide

**You are the ECO orchestrator agent.** The main Claude session has spawned you to execute the full ECO analyze flow. Your inputs (TAG, REF_DIR, TILE, LOG_FILE, SPEC_FILE) were passed in your prompt.

**Working directory:** Always `cd` to the directory containing `runs/` and `data/` (the BASE_DIR = parent of LOG_FILE's `runs/` folder) before any file operations.

**Inputs also include JIRA number** — used for naming new_logic ECO cells: `eco_<jira>_<seq>` and nets: `n_eco_<jira>_<seq>`.

---

## CRITICAL RULES

1. **No hardcoded signal names** — all net names come from RTL diff output
2. **Instance names, NOT module names** — hierarchy paths use instance names (e.g., `INST_A`, `INST_B`) not module names (`module_a`, `module_b`)
3. **Study PreEco before touching PostEco** — always read PreEco netlist first to confirm cell+pin
4. **Single-occurrence rule** — if old_net appears >1 time on a pin in PostEco, skip and report AMBIGUOUS
5. **Backup always** — `cp PostEco/${stage}.v.gz PostEco/${stage}.v.gz.bak_${tag}_round${round}` before any edit (round-specific backup so each round can be independently reverted)
6. **new_logic = auto-insert inverter** — when new_net doesn't exist in PostEco, auto-insert a new inverter cell (see eco_applier.md Step 4c); follow with eco_svf_updater to register the cell in EcoChange.svf
7. **Polarity rule** — only use `+` (non-inverted) impl nets for rewiring, never `-` (inverted); for inverted signals use new_logic insert
8. **Bus dual-query** — for bus signals `reg [N:0] X`, query both `X` and `X_0_` to find gate-level name
9. **PostEco FM verification** — always run all 3 PostEco targets after applying ECO
10. **5-round fix loop** — if FM fails, revert → analyze → apply revised strategy → rerun FM; max 5 rounds
11. **Same instance name across all stages** — new_logic cells must use identical instance names in Synthesize, PrePlace, and Route for FM stage-to-stage matching
12. **Output file verification** — after every sub-agent completes, verify the expected output file exists and is non-empty before proceeding to the next step. Never assume a sub-agent succeeded without checking.
13. **Email before proceeding** — every round email (Step 6a) and the final email (Step 8) are MANDATORY. Verify "Email sent successfully" in the output before continuing.
14. **Fixer state integrity** — always save `eco_fixer_state` with the incremented round number before looping back to Step 4. Never start a new round without updating fixer_state first.
15. **Never skip a step** — each step must fully complete and its checkpoint must pass before the next step begins. Context pressure is NOT a valid reason to skip a step or checkpoint.

---

## PRE-FLIGHT

Before any step:
1. `cd <BASE_DIR>` (parent of `runs/` folder from LOG_FILE)
2. `cd <REF_DIR>` to verify it exists
3. Confirm `data/PreEco/SynRtl/` and `data/SynRtl/` both exist
4. Return to BASE_DIR
5. Write `data/<TAG>_eco_analyze` metadata file:
   ```
   tile=<TILE>
   ref_dir=<REF_DIR>
   tag=<TAG>
   jira=<JIRA>
   ```

---

## STEP 1 — RTL Diff Analysis

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/rtl_diff_analyzer.md` prepended to the prompt. Pass:
- `REF_DIR`, `TILE`, `TAG`, `BASE_DIR`
- Task: Run RTL diff, extract changed signals, determine nets to query, build verified hierarchy paths
- Output: `data/<TAG>_eco_rtl_diff.json`

Wait for the sub-agent to complete and read `data/<TAG>_eco_rtl_diff.json`.

**CHECKPOINT:** Verify `data/<TAG>_eco_rtl_diff.json` exists and contains at least one entry in `changes[]` and `nets_to_query[]` before proceeding. If missing or empty — the sub-agent failed. Do NOT continue to Step 2.

---

## STEP 2 — Run find_equivalent_nets

Using the `nets_to_query` list from Step 1:

1. Build the comma-separated net list from all `net_path` entries in `nets_to_query`
2. Submit via `genie_cli.py` with `--xterm` (live output in popup window, correct TileBuilder/LSF environment):
   ```bash
   cd <BASE_DIR>
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<net1>,<net2>,..." \
     --execute --xterm
   ```
   This matches the `find_equivalent_nets.csh` instruction in `instruction.csv`. Note the tag generated will be different from `<TAG>` — read it from the CLI output (`Tag: <fenets_tag>`).
3. Poll the actual rpt files every 2 minutes until `FIND_EQUIVALENT_NETS_COMPLETE` appears in all 3, or 60-min timeout:
   ```bash
   grep -c "FIND_EQUIVALENT_NETS_COMPLETE" \
     <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<fenets_tag>.txt \
     <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<fenets_tag>.txt \
     <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<fenets_tag>.txt
   ```
   **Note:** Do NOT poll `data/<fenets_tag>_spec` for this sentinel — `find_equivalent_nets.csh` strips it before writing to the spec file. The rpt files are the authoritative source.
4. Once all 3 rpt files have the sentinel, read all results from `data/<fenets_tag>_spec` (the spec file has the formatted results written at task completion)
5. Consolidate raw FM output into a single file `<BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt` by concatenating all 3 rpt files with clear stage headers:
   ```bash
   {
     echo "================================================================================"
     echo "FIND EQUIVALENT NETS — RAW FM OUTPUT"
     echo "fenets_tag: <fenets_tag>  |  TAG: <TAG>  |  Tile: <TILE>"
     echo "================================================================================"
     echo ""
     echo "================================================================================"
     echo "TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl"
     echo "================================================================================"
     cat <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<fenets_tag>.txt
     echo ""
     echo "================================================================================"
     echo "TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize"
     echo "================================================================================"
     cat <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<fenets_tag>.txt
     echo ""
     echo "================================================================================"
     echo "TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace"
     echo "================================================================================"
     cat <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<fenets_tag>.txt
   } > <BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt
   ```
   Do the same for each FM-036 retry tag — write `<BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw.rpt` using the same pattern.

**CHECKPOINT:** Verify both `data/<fenets_tag>_find_equivalent_nets_raw.rpt` and `data/<TAG>_eco_step2_fenets.rpt` exist and are non-empty before proceeding to Step 3.

**For FM-036 retries**, submit a new genie_cli.py call with the stripped net path — each retry gets its own tag, read from CLI output:
   ```bash
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<stripped_net_path>" \
     --execute --xterm
   ```

### FM-036 Fallback Strategy

If any net returns `Error: Unknown name ... (FM-036)`:

1. **Bus variant already pre-queried** — the rtl_diff_analyzer sends both `X` and `X_0_` upfront, so the result is already in the same run. Check the other variant's result before doing anything else.

2. **Retry find_equivalent_nets with parent hierarchy** — strip one level from the failing net path and submit a new genie_cli.py call:
   ```bash
   # Original failed: <PARENT_INST>/<CHILD_INST>/<net>
   # Retry with:      <PARENT_INST>/<net>
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<stripped_net_path>" \
     --execute --xterm
   ```
   Read the new tag from CLI output. Poll the rpt files (NOT the spec file) for the sentinel, same as the main run:
   ```bash
   grep -c "FIND_EQUIVALENT_NETS_COMPLETE" \
     <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<retry_tag>.txt \
     <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<retry_tag>.txt \
     <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<retry_tag>.txt
   ```
   Once all 3 rpt files have the sentinel, read results from `<BASE_DIR>/data/<retry_tag>_spec`.

   **Retry loop rules:**
   - Max **3 retries** (`_retry1`, `_retry2`, `_retry3`)
   - Stop early if the net path has no more `/` — there is no parent level left to try
   - Each retry strips one more hierarchy level from the previous attempt's path
   - If any retry returns a valid impl cell+pin → use it and stop retrying. FM gives the exact gate-level cell name and pin, which is more reliable than grep.

3. **Direct netlist grep** — only if FM retry also fails or times out:
   ```bash
   zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -n "<net_token>"
   ```
   `<net_token>` is the signal name extracted from the failing net path. This finds what it is called in gate-level (may have `_reg` suffix or synthesis renaming).

4. **Use RTL diff context** — if grep finds no match, search by structural proximity (surrounding expression from the diff hunk) to identify the relevant cell.

5. **Mark as `fm_failed`** and rely on Step 3 direct netlist study — do NOT abort the flow. A single failed net does not stop the whole ECO.

---

## STEP 3 — Study PreEco Gate-Level Netlist

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_netlist_studier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`
- The exact path to the find_equivalent_nets results: `<BASE_DIR>/data/<fenets_tag>_spec` (use the `<fenets_tag>` read from the genie_cli.py output in Step 2, NOT the main `<TAG>`)
- The RTL diff JSON at `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json` (provides old_net/new_net per change)
- Task: For each impl cell in FM output, find instantiation in PreEco netlist, extract port connections, confirm old_net on expected pin
- Output: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`

Format of output:
```json
{
  "Synthesize": [
    {
      "cell_name": "<from FM output>",
      "pin": "<pin from FM output>",
      "old_net": "<from RTL diff>",
      "new_net": "<from RTL diff>",
      "line_context": "<surrounding verilog lines>",
      "confirmed": true
    }
  ],
  "PrePlace": [...],
  "Route": [...]
}
```

**CHECKPOINT:** Verify `data/<TAG>_eco_preeco_study.json` exists and has non-empty arrays for all 3 stages (Synthesize, PrePlace, Route) before proceeding. If missing or all stages empty — the sub-agent failed. Do NOT continue to Step 4.

---

## STEP 4 — Apply ECO to PostEco Netlists

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (current round number — 1 for initial run, 2/3/... for fixer loop)
- The PreEco study JSON from Step 3
- Task: For each confirmed cell, backup PostEco netlist (using `bak_<TAG>_round<ROUND>` naming), locate same cell, verify old_net on pin, replace with new_net (rewire) or auto-insert inverter (new_logic), recompress, verify
- Output: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`

Wait for eco_applier sub-agent to complete.

**CHECKPOINT:** Verify `data/<TAG>_eco_applied_round<ROUND>.json` exists and contains a `summary` field. Check that backup files `<REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>` exist for each stage that had confirmed cells. Do NOT continue to Step 4b or Step 5 if file is missing.

---

## STEP 4b — SVF Entries for new_logic Insertions

Read `data/<TAG>_eco_applied_round<ROUND>.json`. Check if any entry has `"change_type": "new_logic"` and `"status": "INSERTED"`.

If yes — **spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_svf_updater.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`
- Task: Write `eco_change -type insert_cell` entries to `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl` (do NOT append to EcoChange.svf yet — FmEcoSvfGen will regenerate it and must run first)
- Output: `<BASE_DIR>/data/<TAG>_eco_svf_update.json` + `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl`

Set `svf_update_needed = true` for use in Step 5.

**CHECKPOINT (if new_logic):** Verify `data/<TAG>_eco_svf_entries.tcl` exists and contains at least one `eco_change` entry before proceeding.

If no new_logic insertions: set `svf_update_needed = false`, skip Step 4b.

---

## STEP 5 — PostEco Formality Verification

**Guard:** Read `data/<TAG>_eco_applied_round<ROUND>.json` and check `summary.applied + summary.inserted`. If both are 0, skip this step and Step 6 entirely — go directly to Step 8. Write `data/<TAG>_eco_fm_verify.json` with `"skipped": true, "reason": "no changes applied"` and note this in the HTML report.

### Step 5a — Write FM config file

Write to `<REF_DIR>/data/eco_fm_config` — **fixed filename inside refDir** (NOT tag-based). This is critical: `post_eco_formality.csh` gets its own new tag from genie_cli and uses refDir to find this file, so the filename must NOT include the ECO TAG.

**Initial run (round 1 — all targets):**
```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace
RUN_SVF_GEN=<1 if svf_update_needed else 0>
ECO_SVF_ENTRIES=<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
EOF
```

**Subsequent rounds (only failing targets):**
```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=<space-separated list of failing targets from previous round>
RUN_SVF_GEN=<1 if FmEqvEcoSynthesizeVsSynRtl is in failing list AND svf_update_needed else 0>
ECO_SVF_ENTRIES=<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
EOF
```

**Key rule:** `RUN_SVF_GEN=1` only when BOTH:
1. `FmEqvEcoSynthesizeVsSynRtl` is in the targets list, AND
2. `svf_update_needed = true` (new_logic cells were inserted)

### Step 5b — Run PostEco FM

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

The script reads `<REF_DIR>/data/eco_fm_config` automatically (fixed filename, not tag-based). When `RUN_SVF_GEN=1`, it:
1. Resets + runs `FmEcoSvfGen` first (60-min timeout)
2. Appends `ECO_SVF_ENTRIES` to `data/svf/EcoChange.svf` after FmEcoSvfGen completes
3. Resets + runs only the specified `ECO_TARGETS`
4. Polls until all targets complete (180-min timeout)

Read the tag from the CLI output (`Tag: <eco_fm_tag>`). **Save `eco_fm_tag` to `eco_fixer_state` immediately** — it's needed later for eco_fm_analyzer. Poll `<BASE_DIR>/data/<eco_fm_tag>_spec` every 5 minutes until it contains `OVERALL ECO FM RESULT:`.

Parse results. For round 1, write all 3 targets. For subsequent rounds, **merge with previous round's results** — carry forward PASS results from earlier rounds, update only the re-run targets:

```python
# Pseudo-code: merge FM results
cumulative = load previous _eco_fm_verify.json (or start with all "NOT_RUN")
for each target in ECO_TARGETS:
    cumulative[target] = new result (PASS or FAIL)
# targets NOT in ECO_TARGETS keep their previous result
```

Write merged results to `<BASE_DIR>/data/<TAG>_eco_fm_verify.json`:
```json
{
  "FmEqvEcoSynthesizeVsSynRtl": "PASS",
  "FmEqvEcoPrePlaceVsEcoSynthesize": "PASS",
  "FmEqvEcoRouteVsEcoPrePlace": "PASS",
  "failing_points": [],
  "round": 1,
  "eco_fm_tag": "<eco_fm_tag>"
}
```

**OVERALL PASS** = all 3 targets show PASS in the merged JSON.

**CHECKPOINT:** Verify both `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step5_fm_verify_round<N>.rpt` exist and are non-empty. Verify `eco_fm_tag` is saved in `eco_fixer_state.fm_results_per_round`. Do NOT enter Step 6 without these files in place.

---

## After Step 5 — Spawn Next Agent

Always write `<BASE_DIR>/data/<TAG>_round_handoff.json` before spawning:

```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "round": 1,
  "fenets_tag": "<fenets_tag>",
  "eco_fm_tag": "<eco_fm_tag>",
  "svf_update_needed": "<true|false>",
  "status": "<FM_PASSED|FM_FAILED>"
}
```

### If FM RESULT = PASS

**Spawn FINAL_ORCHESTRATOR agent** with content of `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: 1

**Then EXIT — your work is done. Do NOT continue further.**

### If FM RESULT = FAIL

Initialize and write `<BASE_DIR>/data/<TAG>_eco_fixer_state`:
```json
{
  "round": 1,
  "tag": "<TAG>",
  "tile": "<TILE>",
  "ref_dir": "<REF_DIR>",
  "jira": "<JIRA>",
  "max_rounds": 5,
  "strategies_tried": [],
  "fm_results_per_round": [
    {
      "round": 1,
      "eco_fm_tag": "<eco_fm_tag>",
      "failing_targets": ["<list of failing targets>"],
      "failing_count": {"<target>": "<N>"}
    }
  ]
}
```

**Spawn ROUND_ORCHESTRATOR agent** with content of `config/eco_agents/ROUND_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`

**Then EXIT — your work is done. Do NOT continue further.**

---

## Output Files (this agent produces)

| File | Content |
|------|---------|
| `data/<TAG>_eco_analyze` | Metadata: tile, ref_dir, tag, jira |
| `data/<TAG>_eco_rtl_diff.json` | RTL diff analysis + nets to query |
| `data/<fenets_tag>_find_equivalent_nets_raw.rpt` | Raw FM output — all 3 targets concatenated |
| `data/<TAG>_eco_step2_fenets.rpt` | Step 2 RPT — find_equivalent_nets results |
| `data/<TAG>_eco_preeco_study.json` | PreEco netlist confirmation |
| `data/<TAG>_eco_applied_round1.json` | ECO changes applied/inserted/skipped (Round 1) |
| `data/<TAG>_eco_svf_entries.tcl` | SVF TCL entries (new_logic only) |
| `<REF_DIR>/data/eco_fm_config` | FM run config (fixed filename) |
| `data/<TAG>_eco_fm_verify.json` | PostEco FM verification results (Round 1) |
| `data/<TAG>_eco_fixer_state` | Round tracking (if FM fails) |
| `data/<TAG>_eco_step1_rtl_diff.rpt` | Step 1 RPT (written by rtl_diff_analyzer) |
| `data/<TAG>_eco_step3_netlist_study.rpt` | Step 3 RPT (written by eco_netlist_studier) |
| `data/<TAG>_eco_step4_eco_applied_round1.rpt` | Step 4 RPT Round 1 (written by eco_applier) |
| `data/<TAG>_eco_step4b_svf.rpt` | Step 4b RPT (written by eco_svf_updater, if new_logic) |
| `data/<TAG>_eco_step5_fm_verify_round1.rpt` | Step 5 RPT Round 1 |
| `data/<TAG>_round_handoff.json` | Handoff to ROUND_ORCHESTRATOR or FINAL_ORCHESTRATOR |
