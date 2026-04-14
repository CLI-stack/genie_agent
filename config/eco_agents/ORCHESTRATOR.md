# ECO Analyze Orchestrator Guide

**You are the ECO orchestrator agent.** The main Claude session has spawned you to execute the full ECO analyze flow. Your inputs (TAG, REF_DIR, TILE, LOG_FILE, SPEC_FILE) were passed in your prompt.

**Working directory:** Always `cd` to the directory containing `runs/` and `data/` (the BASE_DIR = parent of LOG_FILE's `runs/` folder) before any file operations.

---

## CRITICAL RULES

1. **No hardcoded signal names** — all net names come from RTL diff output
2. **Instance names, NOT module names** — hierarchy paths use instance names (e.g., `ARB`, `TIM`) not module names (`umcarb`, `umctim`)
3. **Study PreEco before touching PostEco** — always read PreEco netlist first to confirm cell+pin
4. **Single-occurrence rule** — if old_net appears >1 time on a pin in PostEco, skip and report AMBIGUOUS
5. **Backup always** — `cp PostEco/${stage}.v.gz PostEco/${stage}.v.gz.bak_${tag}` before any edit
6. **new_logic = report only** — do NOT auto-insert cells; only rewire existing connections
7. **Polarity rule** — only use `+` (non-inverted) impl nets for rewiring, never `-` (inverted)
8. **Bus dual-query** — for bus signals `reg [N:0] X`, query both `X` and `X_0_` to find gate-level name
9. **PostEco FM verification** — always run all 3 PostEco targets after applying ECO

---

## PRE-FLIGHT

Before any step:
1. `cd <BASE_DIR>` (parent of `runs/` folder from LOG_FILE)
2. `cd <REF_DIR>` to verify it exists
3. Confirm `data/PreEco/SynRtl/` and `data/SynRtl/` both exist
4. Return to BASE_DIR

---

## STEP 1 — RTL Diff Analysis

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/rtl_diff_analyzer.md` prepended to the prompt. Pass:
- `REF_DIR`, `TILE`, `TAG`, `BASE_DIR`
- Task: Run RTL diff, extract changed signals, determine nets to query, build verified hierarchy paths
- Output: `data/<TAG>_eco_rtl_diff.json`

Wait for the sub-agent to complete and read `data/<TAG>_eco_rtl_diff.json`.

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
   Read the new tag from CLI output. Poll `data/<retry_tag>_spec` until `FIND_EQUIVALENT_NETS_COMPLETE` appears or 60-min timeout.

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
- Output: `data/<TAG>_eco_preeco_study.json`

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

---

## STEP 4 — Apply ECO to PostEco Netlists

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`
- The PreEco study JSON from Step 3
- Task: For each confirmed cell, backup PostEco netlist, locate same cell, verify old_net on pin, replace with new_net, recompress, verify
- Output: `data/<TAG>_eco_applied.json`

Format of output:
```json
{
  "Synthesize": [
    {"cell_name": "...", "pin": "...", "old_net": "...", "new_net": "...", "status": "APPLIED"},
    {"cell_name": "...", "pin": "...", "old_net": "...", "new_net": "...", "status": "SKIPPED", "reason": "AMBIGUOUS"}
  ],
  "PrePlace": [...],
  "Route": [...]
}
```

---

## STEP 5 — PostEco Formality Verification

**Guard:** Read `data/<TAG>_eco_applied.json` and check `summary.applied`. If `summary.applied == 0`, skip this step and Step 6 entirely — go directly to Step 7. Write `data/<TAG>_eco_fm_verify.json` with `"skipped": true, "reason": "no changes applied"` and note this in the HTML report.

Run via `genie_cli.py` (handles TileBuilder/LSF environment automatically, same as `report_formality.csh`):

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

This invokes `script/rtg_oss_feint/supra/post_eco_formality.csh`, which:
1. Sources `lsf_tilebuilder.csh` for the TileBuilder/LSF environment
2. Resets all 3 PostEco FM targets via `serascmd --action reset`
3. Runs all 3 via `serascmd --action run`
4. Polls `TileBuilderShow` every 15 min (180-min timeout) until all complete
5. Reads `.dat` and `__failing_points.rpt.gz` per target
6. Writes results to `data/<eco_fm_tag>_spec`

Read the tag from the CLI output (`Tag: <eco_fm_tag>`). Poll `data/<eco_fm_tag>_spec` every 5 minutes until it contains `OVERALL ECO FM RESULT:` — that is the last line written by the script before it exits.

Parse results from `data/<eco_fm_tag>_spec` and write `data/<TAG>_eco_fm_verify.json`:
```json
{
  "FmEqvEcoSynthesizeVsSynRtl": "PASS",
  "FmEqvEcoPrePlaceVsEcoSynthesize": "PASS",
  "FmEqvEcoRouteVsEcoPrePlace": "PASS",
  "failing_points": []
}
```

If FAIL: failing point details are already in `data/<eco_fm_tag>_spec` — extract and include in the JSON `failing_points` array.

---

## STEP 6 — Generate HTML Report

Write `data/<TAG>_eco_report.html` with sections:
1. **RTL Diff Summary** — files changed, change types, signals involved
2. **Net Analysis** — find_equivalent_nets results per net per stage
3. **PreEco Netlist Study** — confirmed cell/pin/context per stage
4. **ECO Actions Applied** — before/after, APPLIED vs SKIPPED with reasons
5. **PostEco FM Verification** — PASS/FAIL per target, failing points if any

---

## STEP 7 — Send Email

Send email using genie_cli.py's email function directly:

```bash
cd <BASE_DIR>
python3 -c "
import sys, json
sys.path.insert(0, 'script')
from genie_cli import GenieCLI
cli = GenieCLI()
recipients = cli.get_email_recipients()

# Build summary from applied JSON
with open('data/<TAG>_eco_applied.json') as f:
    applied = json.load(f)
summary = applied.get('summary', {})

subject = '[ECO Analysis Complete] <TILE> @ <REF_DIR> (<TAG>)'
body = open('data/<TAG>_eco_report.html').read()
cli.send_email(recipients, subject, body)
print('Email sent to:', recipients)
"
```

If `get_email_recipients()` is unavailable, fall back to reading `assignment.csv` directly:
```bash
grep "^debugger" <BASE_DIR>/assignment.csv | cut -d',' -f2
```
Then use `sendmail` or the AMD internal relay as appropriate for the environment.

---

## Output Files Summary

| File | Content |
|------|---------|
| `data/<TAG>_eco_rtl_diff.json` | RTL diff analysis + nets to query |
| `data/<fenets_tag>_spec` | find_equivalent_nets results (fenets_tag ≠ TAG — read from genie_cli.py output) |
| `data/<TAG>_eco_preeco_study.json` | PreEco netlist confirmation |
| `data/<TAG>_eco_applied.json` | ECO changes applied/skipped |
| `data/<TAG>_eco_fm_verify.json` | PostEco FM verification |
| `data/<TAG>_eco_report.html` | Full HTML report |
