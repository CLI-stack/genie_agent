# ECO Analyze Orchestrator Guide

**You are the ECO orchestrator agent.** The main Claude session has spawned you to execute the full ECO analyze flow. Your inputs (TAG, REF_DIR, TILE, LOG_FILE, SPEC_FILE) were passed in your prompt.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else. Every rule in that file addresses a known failure mode. Acknowledge each rule before proceeding to PRE-FLIGHT.

**Working directory:** Always `cd` to the directory containing `runs/` and `data/` (the BASE_DIR = parent of LOG_FILE's `runs/` folder) before any file operations.

**Inputs also include JIRA number** — used for naming new_logic ECO cells: `eco_<jira>_<seq>` and nets: `n_eco_<jira>_<seq>`.

**SCOPE RESTRICTION — CRITICAL:** Only read agent guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis (CDC/RDC, Lint, SpgDFT) and contain rules that are wrong for ECO gate-level netlist editing. In particular, `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

---

## CRITICAL RULES

1. **No hardcoded signal names** — all net names come from RTL diff output
2. **Instance names, NOT module names** — hierarchy paths use instance names (e.g., `INST_A`, `INST_B`) not module names (`module_a`, `module_b`)
3. **Study PreEco before touching PostEco** — always read PreEco netlist first to confirm cell+pin
4. **Single-occurrence rule** — if old_net appears >1 time on a pin in PostEco, skip and report AMBIGUOUS
5. **Backup always** — `cp PostEco/${stage}.v.gz PostEco/${stage}.v.gz.bak_${tag}_round${round}` before any edit (round-specific backup so each round can be independently reverted)
6. **new_logic = cell insertion** — when new_net doesn't exist in PostEco, insert a new cell: inverter (Step 4c) for simple inversion, DFF (Step 4c-DFF) for sequential registers, or combinational gate (Step 4c-GATE) for multi-input logic; follow with eco_svf_updater to register all inserted cells in EcoChange.svf
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

**Rule loading for this flow:** This flow does NOT use `config/analyze_agents/shared/CRITICAL_RULES.md`. Do NOT read it. Do NOT prepend it to sub-agent prompts. The only guidance files for this flow are the md files inside `config/eco_agents/`. If you have read `config/analyze_agents/ORCHESTRATOR.md`, discard its Pre-Flight instructions — they do not apply here.

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
6. Create the AI ECO flow directory at REF_DIR and set `AI_ECO_FLOW_DIR`:
   ```bash
   AI_ECO_FLOW_DIR=<REF_DIR>/AI_ECO_FLOW_<TAG>
   mkdir -p <AI_ECO_FLOW_DIR>
   ```
   This directory collects all step RPTs in one place under REF_DIR for easy access.

   > **ANTI-PATTERN WARNING:** REF_DIR may contain older `AI_ECO_FLOW_<OLDER_TAG>/` directories from previous runs. Do NOT read, copy, or reuse any files from those directories. They belong to different TAGs and their fenets results, netlist study JSONs, and ECO applied JSONs are NOT valid inputs for this run. Step 2 (find_equivalent_nets) MUST always be submitted fresh for a new TAG — never skipped by copying RPTs from a pre-existing `AI_ECO_FLOW_*` directory. Treat all older `AI_ECO_FLOW_*` directories as read-only historical artifacts that do not affect this run.

---

## STEP 1 — RTL Diff Analysis

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/rtl_diff_analyzer.md` prepended to the prompt. Pass:
- `REF_DIR`, `TILE`, `TAG`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- Task: Run RTL diff, extract changed signals, determine nets to query, build verified hierarchy paths
- Output: `data/<TAG>_eco_rtl_diff.json`

Wait for the sub-agent to complete and read `data/<TAG>_eco_rtl_diff.json`.

**CHECKPOINT:** Verify `data/<TAG>_eco_rtl_diff.json` exists and contains at least one entry in `changes[]` and `nets_to_query[]` before proceeding. If missing or empty — the sub-agent failed. Do NOT continue to Step 2.

---

## STEP 2 — Run find_equivalent_nets

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fenets_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- Path to RTL diff JSON: `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`
- Task: validate nets, submit fenets, block until complete, handle all retries, write all raw rpts + step2 fenets RPT

Wait for the sub-agent to complete.

**CHECKPOINT — Verify ALL of the following before proceeding to Step 3:**
```bash
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
```
If any file is missing — eco_fenets_runner failed. Do NOT continue.

**Extract SPEC_SOURCES from `data/<TAG>_eco_step2_fenets.rpt`** (read the SPEC_SOURCES section at the bottom of the RPT) — pass these to the Step 3 sub-agent prompt.

---

### Step 2 Notes (reference — do NOT execute yourself)

Full implementation is in `eco_fenets_runner.md`. Key rules for the sub-agent:
- Validate nets (filter port_promotion/new_port/port_connection types)
- Do NOT reuse previous run scope — submit fresh if paths differ
- No-Equiv-Nets retry: always DEEPER (not shallower)
- FM-036 retry: classify first — port-level → strip shallower; internal wire → pivot to target register query
- Use single Bash blocking call for all polls (no repeated tool calls)
- Copy all rpts to AI_ECO_FLOW_DIR before exiting

The following detail is for sub-agent reference. **Validate `nets_to_query` before submitting.** Whether Step 1 was just run or reused from a previous tag, always check the JSON before building the net list:

```python
# Load the RTL diff JSON
rtl_diff = load("<BASE_DIR>/data/<TAG>_eco_rtl_diff.json")

# Collect all valid signal names to query: only old_token and new_token from wire_swap/and_term changes
# port_promotion, new_port, port_connection changes have no FM query — skip them
no_fm_types = {"port_promotion", "new_port", "port_connection"}

valid_tokens = set()
for c in rtl_diff["changes"]:
    if c.get("change_type") in no_fm_types:
        continue   # these change types never generate FM queries
    if c.get("old_token"): valid_tokens.add(c["old_token"])
    if c.get("new_token"): valid_tokens.add(c["new_token"])

# Keep only nets whose signal name (last path component, strip _0_ bus suffix) matches a valid token
def net_signal(net_path):
    name = net_path.split("/")[-1]
    return name[:-3] if name.endswith("_0_") else name  # strip bus suffix if present

valid_nets = [n for n in rtl_diff["nets_to_query"]
              if net_signal(n["net_path"]) in valid_tokens]
```

If any `nets_to_query` entry does not correspond to `old_token` or `new_token` from the change list, **drop it silently** — do NOT submit it to FM. Only `old_token` and `new_token` nets are valid queries. This validation catches bugs from reused Step 1 results that predate the current md rules.

**MANDATORY: Do NOT reuse a previous fenets run if its queried scope differs from Step 1 `net_path` values.**

When considering reusing a previous tag's fenets results, compare the net paths that tag actually queried against the `net_path` values in `nets_to_query` from the Step 1 JSON. If they differ (e.g., previous run used a deeper or shallower hierarchy than Step 1 specifies), do NOT reuse it as the initial fenets — they are different scopes. Submit a fresh fenets using the Step 1 paths. A previous run at a different hierarchy level may only be used as a **retry result** (see retry strategy below), not as the initial run.

**MANDATORY: All retry rpt files MUST be copied to `AI_ECO_FLOW_DIR` before proceeding to Step 3.** If a retry was submitted but its raw rpt is not yet in `AI_ECO_FLOW_DIR`, wait for it and copy it. Do not proceed with an incomplete retry.

**MANDATORY: No-Equiv-Nets retry direction is always DEEPER (add sub-instance level), never shallower.**
- Initial query: `<net_path>` from Step 1 (declaring module scope)
- Retry 1: add one sub-instance level deeper than the initial path
- Retry 2: add one more sub-instance level deeper than retry 1
Going shallower (removing a hierarchy level from the initial path) is NOT a valid No-Equiv-Nets retry. If a previous run's query is already at a deeper level than Step 1's `net_path`, treat that previous run as a retry result — NOT as the initial run.

The sequence below is strictly sequential — each step BLOCKS on the previous. Do NOT submit a retry before the current run is complete and its raw rpt is written and copied.

### Phase A — Initial Run (BLOCKING)

**A1. Submit:**
```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<net1>,<net2>,..." \
  --execute --xterm
```
Read `<fenets_tag>` from CLI output (`Tag: <fenets_tag>`).

**A2. Wait — poll every 2 minutes until `FIND_EQUIVALENT_NETS_COMPLETE` appears in ALL 3 rpt files (60-min timeout):**
```bash
grep -c "FIND_EQUIVALENT_NETS_COMPLETE" \
  <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<fenets_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<fenets_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<fenets_tag>.txt
```
**Do NOT proceed until all 3 show count=1.** Do NOT poll `data/<fenets_tag>_spec` — the rpt files are authoritative.

**A3. Read — once all 3 sentinel counts = 1:**
```bash
cat <BASE_DIR>/data/<fenets_tag>_spec
```

**A4. Write and copy raw rpt immediately:**
```bash
{
  echo "FIND EQUIVALENT NETS — RAW FM OUTPUT"
  echo "fenets_tag: <fenets_tag>  |  TAG: <TAG>  |  Tile: <TILE>"
  echo "TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl"
  cat <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<fenets_tag>.txt
  echo "TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize"
  cat <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<fenets_tag>.txt
  echo "TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace"
  cat <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<fenets_tag>.txt
} > <BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt
cp <BASE_DIR>/data/<fenets_tag>_find_equivalent_nets_raw.rpt <AI_ECO_FLOW_DIR>/
```

**A5. Verify copy succeeded:**
```bash
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
```
If missing — re-run the cp before continuing.

**A6. NOW analyze the raw rpt** — only after A1–A5 are complete:
- For each stage, check the results: FM found cells / No Equivalent Nets / FM-036
- Identify which stages and nets need retries (No-Equiv-Nets or FM-036)
- **Do NOT submit any retry until this analysis is done**

---

### Phase B — Retries (each retry is its own BLOCKING cycle)

For each stage/net that requires a retry (No-Equiv-Nets or FM-036), repeat this pattern — **one retry at a time, sequentially**:

**B1. Submit retry:**
```bash
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<retry_net_path>" \
  --execute --xterm
```
Read `<retry_tag>` from CLI output.

**B2. Wait — same polling pattern as A2, using `<retry_tag>`:**
```bash
grep -c "FIND_EQUIVALENT_NETS_COMPLETE" \
  <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<retry_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<retry_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<retry_tag>.txt
```
**Do NOT proceed until all 3 = 1.**

**B3. Read:** `cat <BASE_DIR>/data/<retry_tag>_spec`

**B4. Write and copy retry raw rpt immediately (with type suffix):**
```bash
{
  echo "TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl"
  cat <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<retry_tag>.txt
  echo "TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize"
  cat <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<retry_tag>.txt
  echo "TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace"
  cat <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<retry_tag>.txt
} > <BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw_<type>_retry<N>.rpt
cp <BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw_<type>_retry<N>.rpt <AI_ECO_FLOW_DIR>/
```
Where `<type>` = `noequiv` or `fm036`, `<N>` = retry number.

**B5. Verify copy:** `ls <AI_ECO_FLOW_DIR>/<retry_tag>_find_equivalent_nets_raw_<type>_retry<N>.rpt`

**B6. Analyze retry results** — only then decide if another retry is needed.

**Repeat B1–B6 for each additional retry, one at a time.**

   Write `<BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt` using this format:

   ```
   ================================================================================
   STEP 2 — FIND EQUIVALENT NETS
   Tag: <TAG>  |  fenets_tag: <fenets_tag>  |  Tile: <TILE>
   ================================================================================

   <For each net queried, one block:>
   ────────────────────────────────────────────────────────────────────────────────
   Net [<n>/<total>]: <net_path>
   ────────────────────────────────────────────────────────────────────────────────
   RTL Context   : <change_type> in <module_name> — <old_token> → <new_token>

   <If "No Equivalent Nets" 2nd iteration was performed for any stage:>
   2nd Iteration (No Equiv Nets Retry):
     Original query : <original_net_path> → No Equivalent Nets in <Stage>
     Retry 1 (<noequiv_retry1_tag>): <retry1_net_path> → <FOUND <N> cells / Still no results>
     Retry 2 (<noequiv_retry2_tag>): <retry2_net_path> → <FOUND <N> cells / All retries exhausted>
     Outcome        : <Used retry <N> results for <Stage> / Stage fallback applied>

   <If FM-036 pivot to target register was performed:>
   FM-036 Internal Wire Pivot:
     Failing net    : <original_net_path> → FM-036 at all hierarchy levels (internal wire)
     Classification : Internal wire — not a module port, invisible to FM at any depth
     Pivot query    : <target_register_path> → <FOUND <N> cells / FM-036 again>
     Outcome        : <Used pivot results — backward cone trace identifies MUX/cell to rewire>

   FM Results per Stage:
     [Synthesize] : <N> qualifying cells  (or: No Equiv Nets → retry<N> used / fallback)
     [PrePlace]   : <N> qualifying cells  (or: No Equiv Nets → retry<N> used / fallback)
     [Route]      : <N> qualifying cells  (or: FM-036 → stripped path / fallback)

   Qualifying cells passed to Step 3:
     Synthesize : <cell_name>/<pin>, ...
     PrePlace   : <cell_name>/<pin>, ...  (or: fallback from Synthesize)
     Route      : <cell_name>/<pin>, ...  (or: fallback from Synthesize)

   <Repeat for each net>
   ================================================================================
   ```

   After writing, copy it:
   ```bash
   cp <BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt <AI_ECO_FLOW_DIR>/
   ```

**CHECKPOINT — STRICT FILE VERIFICATION before proceeding to Step 3:**

Every raw rpt file that was written to `data/` MUST also exist in `AI_ECO_FLOW_DIR/`. Verify each one individually:

```bash
# Initial run — MUST exist
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt

# Each No-Equiv-Nets retry submitted — MUST exist (if retry was run)
ls <AI_ECO_FLOW_DIR>/<noequiv_retry1_tag>_find_equivalent_nets_raw_noequiv_retry1.rpt
ls <AI_ECO_FLOW_DIR>/<noequiv_retry2_tag>_find_equivalent_nets_raw_noequiv_retry2.rpt

# Each FM-036 retry submitted — MUST exist (if retry was run)
ls <AI_ECO_FLOW_DIR>/<fm036_retry1_tag>_find_equivalent_nets_raw_fm036_retry1.rpt
ls <AI_ECO_FLOW_DIR>/<fm036_retry2_tag>_find_equivalent_nets_raw_fm036_retry2.rpt
ls <AI_ECO_FLOW_DIR>/<fm036_retry3_tag>_find_equivalent_nets_raw_fm036_retry3.rpt

# Step 2 summary RPT — MUST exist
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
```

If ANY submitted run's raw rpt is missing from `AI_ECO_FLOW_DIR`, **copy it before proceeding**:
```bash
cp <BASE_DIR>/data/<tag>_find_equivalent_nets_raw*.rpt <AI_ECO_FLOW_DIR>/
```

**Do NOT proceed to Step 3 if any submitted run's raw rpt is absent from `AI_ECO_FLOW_DIR`.** A missing file means either the copy was skipped or the run did not complete — investigate before continuing.

**MANDATORY: Retries MUST be attempted before fallback.** Do NOT skip straight to Stage Fallback or grep fallback when FM returns No Equivalent Nets or FM-036. The retry strategies below are NOT optional — they must be executed in order. Only after all retries are exhausted may fallback be applied.

**For FM-036 retries**, submit a new genie_cli.py call with the stripped net path — each retry gets its own tag, read from CLI output:
   ```bash
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<stripped_net_path>" \
     --execute --xterm
   ```

### "No Equivalent Nets" Retry Strategy  ← MANDATORY before Stage Fallback

If a stage returns `--- No Equivalent Nets:` (not FM-036 — the net path was valid but FM found no gate-level equivalents):

**You MUST attempt the retries below before applying Stage Fallback.** Skipping retries and going directly to fallback is a protocol violation — retries often resolve the issue (e.g., adding `TIM/` sub-hierarchy resolves PrePlace No-Equiv-Nets in many designs).

This typically happens when:
- The hierarchy path is at the wrong level (too high or too low)
- The P&R stage restructured the signal into HFS aliases not visible at the queried scope

**Retry steps (max 2 retries, each gets its own genie_cli call):**

1. **Retry with deeper hierarchy** — add one more instance level from the declaring module trace (e.g., `<INST_A>/<signal>` → `<INST_A>/<INST_B>/<signal>` if `<INST_B>` is the sub-module containing the declaration):
   ```bash
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<deeper_net_path>" \
     --execute --xterm
   ```
   Read new `<noequiv_retry1_tag>` from CLI output. Poll rpt files for sentinel. Once complete:
   ```bash
   # Write and copy raw rpt for this retry
   {
     echo "TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl"
     cat <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<noequiv_retry1_tag>.txt
     echo "TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize"
     cat <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<noequiv_retry1_tag>.txt
     echo "TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace"
     cat <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<noequiv_retry1_tag>.txt
   } > <BASE_DIR>/data/<noequiv_retry1_tag>_find_equivalent_nets_raw_noequiv_retry1.rpt
   cp <BASE_DIR>/data/<noequiv_retry1_tag>_find_equivalent_nets_raw_noequiv_retry1.rpt <AI_ECO_FLOW_DIR>/
   ```
   Read results from `<BASE_DIR>/data/<noequiv_retry1_tag>_spec`. If results found → use them and stop retrying.

2. **Retry with parent hierarchy** — strip one level from original path (e.g., `<INST_A>/<INST_B>/<signal>` → `<INST_A>/<signal>`):
   ```bash
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<parent_net_path>" \
     --execute --xterm
   ```
   Read new `<noequiv_retry2_tag>` from CLI output. Same output handling as retry 1:
   ```bash
   # Write and copy raw rpt
   { ... } > <BASE_DIR>/data/<noequiv_retry2_tag>_find_equivalent_nets_raw_noequiv_retry2.rpt
   cp <BASE_DIR>/data/<noequiv_retry2_tag>_find_equivalent_nets_raw_noequiv_retry2.rpt <AI_ECO_FLOW_DIR>/
   ```
   Read results from `<BASE_DIR>/data/<noequiv_retry2_tag>_spec`.

**Output files per retry:**
- `data/<noequiv_retry<N>_tag>_find_equivalent_nets_raw_noequiv_retry<N>.rpt` — raw FM output for each No-Equiv-Nets retry (N=1,2)
- Copied to `<AI_ECO_FLOW_DIR>/` alongside the main fenets raw rpt
- The `_noequiv_retry<N>` suffix distinguishes these from FM-036 retries at a glance
- Referenced in `data/<TAG>_eco_step2_fenets.rpt` with note: `NO_EQUIV_NETS retry<N> tag: <noequiv_retry<N>_tag>`

**If all retries still return "No Equivalent Nets":**
- Apply Stage Fallback in Step 3 (eco_netlist_studier) — grep confirmed cell names from another stage
- Record reason in step2 fenets rpt: `NO_EQUIV_NETS — all retries exhausted, fallback required for <Stage>`

---

### FM-036 Fallback Strategy

If any net returns `Error: Unknown name ... (FM-036)`:

1. **Bus variant already pre-queried** — the rtl_diff_analyzer sends both `X` and `X_0_` upfront, so the result is already in the same run. Check the other variant's result before doing anything else.

1b. **Classify the FM-036 cause BEFORE stripping hierarchy levels:**

   FM-036 has two distinct root causes that require different strategies:

   | Cause | Symptom | Correct Action |
   |-------|---------|----------------|
   | **Wrong hierarchy level** — net IS a module port, just queried at wrong depth | FM-036 fires at this level but net exists as a port at another level | Strip/add levels (step 2 below) |
   | **Internal wire** — net is a submodule-internal wire, NOT a module port | FM-036 fires at ALL hierarchy levels — the net is never exposed in FM's reference namespace | Skip level-stripping; **PIVOT to target register query** (step 2b below) |

   **How to classify — check the RTL diff JSON:**
   - Read `eco_rtl_diff.json` for this net's `change_type` and `context_line`
   - If `change_type = "wire_swap"` and the `old_token` is an internal combinational net (not declared as `input`/`output` in any module port list in the RTL) → it is an **internal wire** — pivot to step 2b
   - Confirm by checking if stripping one level from the net path gives FM-036 too (if so, it's an internal wire, not a hierarchy problem)

2b. **Pivot to target register query (when net is an internal wire):**

   When the failing net is an internal wire inside a submodule (not a port at any hierarchy level), FM will return FM-036 at every depth. Do NOT waste retries stripping levels. Instead:

   - Read `target_register` from `eco_rtl_diff.json` for this `wire_swap` change
   - Query the **output signal of the target register** — this IS visible to FM as a named RTL signal:
   ```bash
   python3 script/genie_cli.py \
     -i "find equivalent nets at <REF_DIR> for <TILE> netName:<hierarchy_path>/<target_register>" \
     --execute --xterm
   ```
   Where `<hierarchy_path>` is the instance path of the module containing `target_register` (from `hierarchy` field in `eco_rtl_diff.json`).

   **Why this works:** FM's reference namespace exposes the register's output signal (the Q net). The eco_netlist_studier will then trace backward from `<target_register>.D` through the gate-level backward cone to find the actual cell and pin that drives the D input — which is where the internal wire (`old_net`) connects. The backward cone trace (Step 4c in eco_netlist_studier.md) identifies the exact cell to rewire without needing FM to directly name the internal wire.

   Write and copy the raw rpt with the `_fm036_retry<N>` suffix as normal.

   **If the target register pivot also returns FM-036:** try the register name with `_reg` suffix appended, then with a `_0_` bus notation, then apply direct netlist grep (step 3).

2. **Retry find_equivalent_nets with parent hierarchy (for port-level signals only)** — strip one level from the failing net path and submit a new genie_cli.py call:
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
   Write and copy the raw rpt with the retry number suffix:
   ```bash
   {
     echo "TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl"
     cat <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<retry_tag>.txt
     echo "TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize"
     cat <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<retry_tag>.txt
     echo "TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace"
     cat <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<retry_tag>.txt
   } > <BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw_fm036_retry<N>.rpt
   cp <BASE_DIR>/data/<retry_tag>_find_equivalent_nets_raw_fm036_retry<N>.rpt <AI_ECO_FLOW_DIR>/
   ```
   Where N=1 for first retry, N=2 for second, N=3 for third. The `_fm036_retry<N>` suffix distinguishes these from No-Equiv-Nets retries — both can coexist in the same run without ambiguity.

   **Retry loop rules:**
   - Max **3 retries** (`_retry1`, `_retry2`, `_retry3`) — but only for **port-level signals** (step 2 above). For **internal wires**, use the target register pivot (step 2b) instead — do NOT waste retries stripping levels.
   - Stop early if the net path has no more `/` — there is no parent level left to try
   - Each retry strips one more hierarchy level from the previous attempt's path
   - If any retry returns a valid impl cell+pin → use it and stop retrying. FM gives the exact gate-level cell name and pin, which is more reliable than grep.
   - **If FM-036 fires at EVERY level after 2 retries:** stop stripping. The net is likely an internal wire — apply step 2b (target register pivot) even if you initially treated it as a port-level signal.

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
- `REF_DIR`, `TAG`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- The RTL diff JSON at `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json` (provides old_net/new_net per change)
- **ALL spec file paths** from Step 2 — initial run AND every retry:
  - Initial: `<BASE_DIR>/data/<fenets_tag>_spec`
  - No-Equiv-Nets retries: `<BASE_DIR>/data/<noequiv_retry1_tag>_spec`, `<BASE_DIR>/data/<noequiv_retry2_tag>_spec` (if they exist)
  - FM-036 retries: `<BASE_DIR>/data/<fm036_retry1_tag>_spec`, `<BASE_DIR>/data/<fm036_retry2_tag>_spec`, `<BASE_DIR>/data/<fm036_retry3_tag>_spec` (if they exist)
- **Per-stage spec source mapping** — build and pass to Step 3 which spec file to use for each stage.

  **How to build SPEC_SOURCES (algorithm):**
  ```python
  # Start: all stages use initial run spec
  spec_sources = {
      "Synthesize": f"{BASE_DIR}/data/{fenets_tag}_spec",
      "PrePlace":   f"{BASE_DIR}/data/{fenets_tag}_spec",
      "Route":      f"{BASE_DIR}/data/{fenets_tag}_spec",
  }

  # For each No-Equiv-Nets retry that was run:
  for retry_tag, retry_spec_path in noequiv_retries:   # in order retry1, retry2
      retry_raw = read_raw_rpt(retry_tag)
      for stage in ["Synthesize", "PrePlace", "Route"]:
          if stage_has_qualifying_cells(retry_raw, stage):
              spec_sources[stage] = f"{BASE_DIR}/data/{retry_tag}_spec"
              break   # first retry that resolved this stage wins

  # For each FM-036 retry:
  for retry_tag, retry_spec_path in fm036_retries:
      retry_raw = read_raw_rpt(retry_tag)
      for stage in ["Synthesize", "PrePlace", "Route"]:
          if stage_has_qualifying_cells(retry_raw, stage):
              spec_sources[stage] = f"{BASE_DIR}/data/{retry_tag}_spec"
              break

  # Mark stages with no FM results as FALLBACK
  for stage in ["Synthesize", "PrePlace", "Route"]:
      initial_raw = read_raw_rpt(fenets_tag)
      if not stage_has_qualifying_cells(initial_raw, stage) and spec_sources[stage] == initial_spec:
          spec_sources[stage] = "FALLBACK"
  ```

  Where `stage_has_qualifying_cells(raw_rpt, stage)` = True if the raw rpt for that stage returns at least one `(+)` impl cell/pin pair (not FM-036 and not No Equivalent Nets).

  Pass the final mapping:
  ```
  SPEC_SOURCES:
    Synthesize: <resolved_spec_path_or_FALLBACK>
    PrePlace:   <resolved_spec_path_or_FALLBACK>
    Route:      <resolved_spec_path_or_FALLBACK>
  ```
  This prevents Step 3 from reading the wrong spec for a given stage — each stage uses the spec from the run that actually resolved its results.
- Task: For each impl cell in FM output, find instantiation in PreEco netlist, extract port connections, confirm old_net on expected pin
- Output: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`

Format of output (each stage array may contain both wire_swap rewire entries AND new_logic_insertion entries):
```json
{
  "Synthesize": [
    {
      "change_type": "rewire",
      "cell_name": "<from FM output>",
      "pin": "<pin from FM output>",
      "old_net": "<from RTL diff>",
      "new_net": "<from RTL diff>",
      "new_net_alias": null,
      "line_context": "<surrounding verilog lines>",
      "confirmed": true
    },
    {
      "change_type": "new_logic_dff",
      "target_register": "<signal_name>",
      "instance_scope": "<INST_A>/<INST_B>",
      "cell_type": "<DFF_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "port_connections": {"CK": "<clk>", "D": "<data>", "RN": "<reset>", "Q": "n_eco_<jira>_<seq>"},
      "input_from_change": null,
      "confirmed": true
    },
    {
      "change_type": "new_logic_gate",
      "target_register": "<output_signal>",
      "instance_scope": "<INST_A>/<INST_B>",
      "cell_type": "<gate_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "gate_function": "<NAND2|NOR2|AND2|...>",
      "port_connections": {"A": "<input1>", "B": "<input2>", "ZN": "n_eco_<jira>_<seq>"},
      "input_from_change": null,
      "confirmed": true
    }
  ],
  "PrePlace": [...],
  "Route": [...]
}
```

**CHECKPOINT:** Verify `data/<TAG>_eco_preeco_study.json` exists and has non-empty arrays for all 3 stages (Synthesize, PrePlace, Route) before proceeding. If missing or all stages empty — the sub-agent failed. Do NOT continue to Step 4.

**Generate Step 3 RPT from JSON (ORCHESTRATOR responsibility):**

Read `data/<TAG>_eco_preeco_study.json` and write `data/<TAG>_eco_step3_netlist_study.rpt`:

```python
study = load("data/<TAG>_eco_preeco_study.json")
with open("data/<TAG>_eco_step3_netlist_study.rpt", "w") as f:
    f.write(f"STEP 3 — PREECO NETLIST STUDY\nTag: <TAG>\n{'='*80}\n\n")
    for stage in ["Synthesize", "PrePlace", "Route"]:
        confirmed = [e for e in study[stage] if e.get("confirmed")]
        excluded  = [e for e in study[stage] if not e.get("confirmed")]
        f.write(f"[{stage}] — {len(confirmed)} confirmed, {len(excluded)} excluded\n")
        for e in confirmed:
            f.write(f"  CONFIRMED: {e.get('cell_name','?')} pin={e.get('pin','?')} "
                    f"old={e.get('old_net','?')} new={e.get('new_net','?')} "
                    f"type={e.get('change_type','?')}\n")
        for e in excluded:
            f.write(f"  EXCLUDED:  {e.get('cell_name','?')} — {e.get('reason','?')}\n")
        f.write("\n")
```

Then copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_study.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study.rpt
```

---

## STEP 4 — Apply ECO to PostEco Netlists

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (current round number — 1 for initial run, 2/3/... for fixer loop), `AI_ECO_FLOW_DIR`
- The PreEco study JSON from Step 3
- Task: For each confirmed cell, backup PostEco netlist (using `bak_<TAG>_round<ROUND>` naming), locate same cell, verify old_net on pin, replace with new_net (rewire) or auto-insert inverter (new_logic), recompress, verify
- Output: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`

Wait for eco_applier sub-agent to complete.

**CHECKPOINT:** Verify `data/<TAG>_eco_applied_round<ROUND>.json` exists and contains a `summary` field. Check that backup files `<REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>` exist for each stage that had confirmed cells. Do NOT continue to Step 4b or Step 5 if file is missing.

**Generate Step 4 RPT from JSON (ORCHESTRATOR responsibility — NOT eco_applier):**

```python
applied = load("data/<TAG>_eco_applied_round<ROUND>.json")
s = applied["summary"]
with open("data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt", "w") as f:
    f.write(f"STEP 4 — ECO APPLIED (Round <ROUND>)\nTag: <TAG>  |  JIRA: <JIRA>\n{'='*80}\n")
    f.write(f"Summary: {s['applied']} applied / {s['inserted']} inserted / "
            f"{s['skipped']} skipped / {s['verify_failed']} verify_failed\n\n")
    for stage in ["Synthesize", "PrePlace", "Route"]:
        f.write(f"[{stage}]\n")
        for e in applied[stage]:
            f.write(f"  {e['status']:10s} {e.get('cell_name','?'):40s} "
                    f"pin={e.get('pin','?')} type={e.get('change_type','?')}\n")
            if e['status'] == 'SKIPPED':
                f.write(f"             Reason: {e.get('reason','?')}\n")
        f.write("\n")
```

Copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt
```

---

## STEP 4b — SVF Entries for new_logic Insertions

Read `data/<TAG>_eco_applied_round<ROUND>.json`. Check if any entry has `"status": "INSERTED"` and `change_type` in `["new_logic", "new_logic_dff", "new_logic_gate"]`.

If yes — **spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_svf_updater.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (current round number), `AI_ECO_FLOW_DIR`
- Task: Write `eco_change -type insert_cell` entries to `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl` (do NOT append to EcoChange.svf yet — FmEcoSvfGen will regenerate it and must run first)
- Output: `<BASE_DIR>/data/<TAG>_eco_svf_update.json` + `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl`

Set `svf_update_needed = true` for use in Step 5.

**CHECKPOINT (if new_logic):** Verify `data/<TAG>_eco_svf_entries.tcl` exists and contains at least one `eco_change` entry before proceeding.

If no new_logic insertions: set `svf_update_needed = false`, skip Step 4b.

---

## STEP 5 — PostEco Formality Verification

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fm_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`, `ROUND=1`
- `ECO_TARGETS=FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace`
- `svf_update_needed=<true|false>` (from Step 4b)
- Task: write FM config, submit FM, block until complete, parse results, write verify JSON + RPT

Wait for the sub-agent to complete.

**CHECKPOINT:** Verify ALL of the following:
```bash
ls <BASE_DIR>/data/<TAG>_eco_fm_verify.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round1.rpt
```
Also read `data/<TAG>_eco_fm_tag_round1.tmp` to get `eco_fm_tag` — save it to `eco_fixer_state` if FM failed.

---

### Step 5 Notes (reference — do NOT execute yourself)

Full implementation is in `eco_fm_runner.md`. Key rules: write eco_fm_config with fixed filename (not tag-based), use single Bash blocking call for FM wait (timeout 21600s = 6h), merge results with previous round, write tmp file with eco_fm_tag.

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

After writing `data/<TAG>_eco_step5_fm_verify_round1.rpt`, copy to AI_ECO_FLOW_DIR:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round1.rpt <AI_ECO_FLOW_DIR>/
```

**CHECKPOINT:** Verify both `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt` exist and are non-empty. Verify `eco_fm_tag` is saved in `eco_fixer_state.fm_results_per_round`. Do NOT enter Step 6 without these files in place.

---

## After Step 5 — Spawn Next Agent

> **ANTI-PATTERN WARNING — READ FIRST:**
> Your ONLY job here is: (A) write `round_handoff.json`, (B) spawn the correct next agent, (C) stop.
> Do NOT run Steps 7 or 8. Do NOT generate reports. Do NOT send emails. Do NOT write `eco_summary.rpt` or `eco_report.html`.
> Those files are FINAL_ORCHESTRATOR's responsibility. If you produce them yourself, you are violating the spawn-then-exit contract and breaking the multi-agent handoff chain.
> **The presence of `eco_report.html` written by THIS agent is a bug, not a success.**

### Mandatory Step A — Write round_handoff.json FIRST

Write `<BASE_DIR>/data/<TAG>_round_handoff.json` **before any spawn decision**:

```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "ai_eco_flow_dir": "<REF_DIR>/AI_ECO_FLOW_<TAG>",
  "round": 1,
  "fenets_tag": "<fenets_tag>",
  "eco_fm_tag": "<eco_fm_tag>",
  "svf_update_needed": "<true|false>",
  "status": "<FM_PASSED|FM_FAILED>"
}
```

**CHECKPOINT — MANDATORY:** Verify `data/<TAG>_round_handoff.json` exists on disk and is non-empty before proceeding:
```bash
ls -la <BASE_DIR>/data/<TAG>_round_handoff.json
```
If the file does not exist or is empty — write it again. Do NOT proceed to spawn until this file is confirmed on disk.

### Mandatory Step B — Spawn the correct next agent

#### If FM RESULT = PASS → Spawn FINAL_ORCHESTRATOR

**Spawn FINAL_ORCHESTRATOR agent** with content of `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: 1

#### If FM RESULT = FAIL → Spawn ROUND_ORCHESTRATOR

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

### Mandatory Step C — HARD STOP

**Your task ends here. Make no further tool calls. Return your status to the caller.**

You MUST stop after spawning. Do not:
- Run any bash commands after the spawn
- Write any more files
- Read any more files
- Generate any reports or emails
- "Help" FINAL_ORCHESTRATOR or ROUND_ORCHESTRATOR by doing their work early

The next agent has its own fresh context and instructions. Trust the handoff.

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
