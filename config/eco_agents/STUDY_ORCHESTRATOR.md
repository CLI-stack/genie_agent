# ECO STUDY Orchestrator (Phase A — Steps 1-3)

**You are the ECO STUDY phase orchestrator.** The main Claude session spawned you to execute Steps 1-3 of the ECO flow (RTL diff analysis → fenets → netlist study). After Step 3, you write a phase handoff and emit a signal so the main session can spawn APPLY_ORCHESTRATOR (Phase B, Steps 4-6) with a fresh context.

> **MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md`, then `config/eco_agents/CRITICAL_RULES.md` before doing anything else.

**Inputs (from prompt):** TAG, REF_DIR, TILE, JIRA, LOG_FILE, SPEC_FILE, BASE_DIR, AI_ECO_FLOW_DIR.

**Scope restriction (CRITICAL):** Only read guidance files from `config/eco_agents/`. NOT `config/analyze_agents/` (different flow).

**MANDATORY: Task-tracking for live progress visibility.**

Immediately after pre-flight passes, create one task per step you will execute:

```python
TaskCreate(subject="Step 1: RTL Diff Analysis",       activeForm="Running RTL Diff Analysis")
TaskCreate(subject="Step 2: Find Equivalent Nets",    activeForm="Submitting find_equivalent_nets")
TaskCreate(subject="Step 3: Netlist Study",           activeForm="Studying PreEco gate-level netlist")
```

Before invoking each step's sub-agent: `TaskUpdate(taskId=<step_task>, status="in_progress")`.
After step's checkpoint passes: `TaskUpdate(taskId=<step_task>, status="completed")`.
For Step 2's long FM polling, refresh `activeForm` periodically:
`TaskUpdate(taskId=step2_task, activeForm=f"find_equivalent_nets polling — {elapsed_min} min, queries={n_complete}/{n_total}")`.

---

## RESUMPTION CHECK — BEFORE PRE-FLIGHT

> **Root cause this solves:** The ORCHESTRATOR agent can exhaust its context window after eco_fm_runner returns and after writing round_handoff.json + eco_fixer_state, but BEFORE it can make the Agent() tool call to spawn ROUND_ORCHESTRATOR. Both state files are written but the spawn never happens. This check detects that situation and completes the spawn on restart.

**Check for existing round_handoff.json FIRST — before PRE-FLIGHT, before any step:**

```bash
ls data/<TAG>_round_handoff.json 2>/dev/null && echo EXISTS
```

**Check for pending_spawn sentinel FIRST** — this means a previous agent claimed to spawn but context ran out before the spawn executed:
```bash
ls data/<TAG>_pending_spawn.txt 2>/dev/null && cat data/<TAG>_pending_spawn.txt
```
If sentinel exists → the spawn was never executed → spawn the agent indicated in the sentinel NOW → delete sentinel → HARD STOP.

**If `round_handoff.json` EXISTS:**

Read it and branch immediately:

| `status` field | `eco_fixer_state` exists? | Action |
|----------------|--------------------------|--------|
| `FM_FAILED` | YES | Spawn ROUND_ORCHESTRATOR → HARD STOP |
| `FM_FAILED` | NO | Something is wrong — write eco_fixer_state from round_handoff data (round=1, failing_targets from eco_fm_verify.json), then spawn ROUND_ORCHESTRATOR → HARD STOP |
| `FM_PASSED` | — | Spawn FINAL_ORCHESTRATOR → HARD STOP |
| `FM_FAILED` (pre_fm_check_failed=true) | YES | Spawn ROUND_ORCHESTRATOR → HARD STOP |

**SKIP ALL STEPS including PRE-FLIGHT** when any row above matches — the flow is already complete up to the spawn. Do NOT re-run Steps 1–6.

**If `round_handoff.json` does NOT exist:** Continue normally to PRE-FLIGHT below.

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

**MANDATORY validate:**
```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_validate_step1.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --ref-dir  <REF_DIR> \
    --output   data/<TAG>_eco_validate_step1.json
STEP1_EXIT=$?
```

**HARD GATE — check exit code before ANY further action:**
```python
import json
v = json.load(open(f"data/{TAG}_eco_validate_step1.json"))
if not v.get("overall_pass"):
    issues = [i for k,vals in v.items() if "issues" in k and "count" not in k
              for i in (vals if isinstance(vals,list) else [])]
    raise RuntimeError(f"Step 1 validator FAIL ({len(issues)} issues) — "
                       f"DO NOT proceed to Step 2. Re-spawn rtl_diff_analyzer.")
```
**Step 2 MUST NOT run if `overall_pass != true`. No exceptions.**

**Retry-on-fail policy (MAX 2 retries):**
- Exit 1 with `chain_compactness_issues` containing `FAIL/9d-OVERSIZED` or `FAIL/9c-MULTI-INV-NO-REUSE`:
  → re-spawn rtl_diff_analyzer with explicit instruction "apply §E2.5 boolean simplification (De Morgan + bus equality fold + existing-INV reuse) and emit `simplification_applied: true`"
- Other failures: re-spawn rtl_diff_analyzer with the full failing-issue list as context and instruction to fix each issue
- After 2 failed retries on the same root issue → write `STUDY_VALIDATOR_UNFIXABLE` to SPEC_FILE, block flow, EXIT.

---

## STEP 2 — Run find_equivalent_nets

**ORCHESTRATOR FIRST — derive the canonical query list (deterministic, do NOT delegate):**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_fenets_derive_queries.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --tile     <TILE> \
    --output   data/<TAG>_eco_fenets_queries_raw.json
```

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fenets_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- Path to RTL diff JSON: `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`
- Pre-derived raw query list: `<BASE_DIR>/data/<TAG>_eco_fenets_queries_raw.json`
- Task: full Step 2 execution per `eco_fenets_runner.md` (sanitize, submit fenets, retries, RPT generation).

Wait for the sub-agent to complete.

**Generate the per-stage rename map JSON (ORCHESTRATOR responsibility — do NOT delegate to sub-agent):**
```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_fenets_rename_map.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --raw-dir  data/ \
    --tag      <TAG> --tile <TILE> \
    --output   data/<TAG>_eco_fenets_rename_map.json
cp <BASE_DIR>/data/<TAG>_eco_fenets_rename_map.json <AI_ECO_FLOW_DIR>/
```

**CHECKPOINT — Verify ALL of the following before proceeding to Step 3:**
```bash
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_fenets_rename_map.json
```
If any file is missing — eco_fenets_runner failed. Do NOT continue.

**MANDATORY VALIDATOR GATE — block Step 3 spawn until Step 2 validator PASSES:**

eco_fenets_runner is required by `eco_fenets_runner.md` STEP F to run `eco_validate_step2.py` as a BLOCKING handoff. The orchestrator must NOT trust that the runner did its job (silent skip is a known failure mode under context pressure); assert it directly:

```bash
# 1. Validator output JSON must exist
ls <BASE_DIR>/data/<TAG>_eco_validate_step2.json || { echo "FAIL: Step 2 validator did not run"; exit 1; }

# 2. overall_pass must be true
python3 -c "
import json, sys
d = json.loads(open('<BASE_DIR>/data/<TAG>_eco_validate_step2.json').read())
if not d.get('overall_pass'):
    print(f'FAIL: Step 2 validator overall_pass=False, {len(d.get(\"issues\",[]))} issues:')
    for i in d.get('issues', [])[:5]:
        print(f'  - {i}')
    sys.exit(1)
print('Step 2 validator PASSED — proceeding to Step 3')
"

# 3. Sanitize marker must exist (proves eco_fenets_sanitize_queries.py ran, not agent panic-rewrite)
ls <BASE_DIR>/data/<TAG>_eco_fenets_queries_sanitize_marker.txt || \
  { echo "FAIL: sanitize marker missing — runner skipped sanitize step"; exit 1; }
```

If ANY of the 3 assertions fail → **HARD STOP**, do NOT spawn Step 3. Re-spawn `eco_fenets_runner` with explicit instruction to re-run STEP A (sanitize) and STEP F (validator). If the runner still skips them after a retry → write phase_a_handoff.json with `phase_a_status: "BLOCKED_STEP2_VALIDATOR"` + emit error to SPEC_FILE → orchestrator EXIT.

**Extract SPEC_SOURCES from `data/<TAG>_eco_step2_fenets.rpt`** (read the SPEC_SOURCES section at the bottom of the RPT) — pass these to the Step 3 sub-agent prompt.

---

---

## STEP 3 — Study PreEco Gate-Level Netlist

**MANDATORY pre-Step 3: Run GAP-15 check script (do this BEFORE spawning eco_netlist_studier):**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_and_term_port_check.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --ref-dir  <REF_DIR> \
    --output   data/<TAG>_eco_and_term_port_check.json
```
Read the output JSON and **pass it explicitly to the eco_netlist_studier sub-agent prompt** as `GAP15_CHECK_PATH=data/<TAG>_eco_and_term_port_check.json`. The studier reads this file to get `is_output_port` and `strategy` for each `and_term` change — it does NOT re-derive these itself.

**Verify script ran:** The script prints `ECO_SCRIPT_LAUNCHED: eco_and_term_port_check.py` to stdout and writes a `_marker.txt` sidecar. The Step 3 RPT MUST contain a line starting with `ECO_SCRIPT_LAUNCHED: eco_and_term_port_check.py`. If this line is absent from the RPT, the script was NOT called — the agent must re-run it before spawning eco_netlist_studier.

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
- Output: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` (schema defined in `eco_netlist_studier.md`)

**CHECKPOINT 3a (MANDATORY — verify before spawning verifier):**
```bash
ls -la <BASE_DIR>/data/<TAG>_eco_preeco_study.json
python3 -c "import json; d=json.load(open('data/<TAG>_eco_preeco_study.json')); assert any(d.get(s) for s in ['Synthesize','PrePlace','Route']), 'all stages empty'"
ls <BASE_DIR>/data/<TAG>_eco_step3_collect.rpt
```
If any check fails — eco_netlist_studier failed. Do NOT spawn verifier. Re-spawn eco_netlist_studier first.

**MANDATORY Step 3b — Spawn eco_netlist_verifier (Deep Verify + Enrich Pass):**

> **Sequential contract:** eco_netlist_studier MUST complete and write `eco_preeco_study.json` before eco_netlist_verifier is spawned. They run sequentially — verifier reads the JSON studier produced. Never spawn both in parallel.

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_netlist_verifier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `GAP15_CHECK_PATH=data/<TAG>_eco_and_term_port_check.json`
- `SPEC_SOURCES` (same mapping passed to eco_netlist_studier — verifier uses it for per-stage net resolution in Check 2 and cone verification in Check 10)
- Task: Enrich every entry in `eco_preeco_study.json` — 14 checks covering GAP-15, per-stage nets, port boundary, consumer cascade, CTS, cone verification, missing entry detection

Wait for eco_netlist_verifier to complete.

**CHECKPOINT 3b (MANDATORY — verify both verifier outputs before continuing):**
```bash
ls <BASE_DIR>/data/<TAG>_eco_step3_netlist_verify.rpt
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_verify.rpt
```
If either missing — verifier failed. Re-spawn before continuing to eco_expand_chains.py. Do NOT proceed to Step 4 without a passing verifier.

**MANDATORY post-Step 3: Run eco_expand_chains.py to inject missing D-input gate chains:**

The eco_netlist_studier sometimes produces DFF entries (new_logic_dff) with `.D` referencing intermediate nets (e.g. `n_eco_<jira>_d007`) but omits the actual gate chain entries. This script reads `d_input_gate_chain` from the RTL diff and injects the missing gates into the study JSON before Step 4 runs.

```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_expand_chains.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --study    data/<TAG>_eco_preeco_study.json \
    --ref-dir  <REF_DIR> \
    --jira     <JIRA> \
    --output   data/<TAG>_eco_preeco_study.json
```

Check output for `ECO_SCRIPT_LAUNCHED: eco_expand_chains.py` and `chains_expanded: N`. If N=0, no chains were missing (OK). If N>0, gates were injected — verify the study JSON now has the correct chain entries before proceeding.

**MANDATORY post-Step 3: Run eco_validate_step3.py to enforce completeness contract:**
```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_validate_step3.py \
    --study    data/<TAG>_eco_preeco_study.json \
    --rtl-diff data/<TAG>_eco_rtl_diff.json \
    --ref-dir  <REF_DIR> \
    --tag      <TAG> \
    --output   data/<TAG>_eco_validate_step3.json
```
**CATCH-AND-FIX LOOP (max 3 iterations):** If validator returns `passed: false`, run `eco_study_fixer.py` to auto-apply deterministic fixes, then re-validate:

```bash
for i in 1 2 3; do
  # Run validator
  python3 script/eco_scripts/eco_validate_step3.py \
      --study data/<TAG>_eco_preeco_study.json \
      --rtl-diff data/<TAG>_eco_rtl_diff.json \
      --ref-dir <REF_DIR> --tag <TAG> \
      --output data/<TAG>_eco_validate_step3.json
  [ $? -eq 0 ] && break  # PASS — exit loop

  # Auto-fix deterministic issues
  python3 script/eco_scripts/eco_study_fixer.py \
      --study   data/<TAG>_eco_preeco_study.json \
      --issues  data/<TAG>_eco_validate_step3.json \
      --rtl-diff data/<TAG>_eco_rtl_diff.json \
      --ref-dir <REF_DIR> \
      --raw-rpts data/*_find_equivalent_nets_raw*.rpt \
      --step2-rpt data/<TAG>_eco_step2_fenets.rpt \
      --output  data/<TAG>_eco_preeco_study.json
done
```

**eco_study_fixer.py** handles deterministic issues automatically:
- `ANDTERM-WRONG-POLARITY` — flips NOR2↔INR2 based on FM raw rpt polarity
- `NET-ABSENT-IN-STAGE` — runs `eco_resolve_synth_internal.py` to find correct P&R net
- `PENDING-UNRESOLVED` — same; runs resolve script
- `CONDITION-POLARITY` — replaces wrong Synth net with condition_input_resolutions value

After 3 iterations: if `passed: false` remains → only non-deterministic issues left (e.g. UNRESOLVABLE requiring manual F1-F3 forward consumer search). Read remaining issues and fix manually, then re-validate once more.

**HARD GATE.** Any `passed: false` after the catch-and-fix loop BLOCKS Phase A handoff. If issues cannot be resolved, write `phase_a_handoff.json` with `phase_a_status: "BLOCKED_STEP3_VALIDATOR"` and EXIT.

Remaining manual fixes for non-deterministic issues:
- Incomplete chain entries → re-run `eco_expand_chains.py`
- Missing fields (module_name, port_connections_per_stage, etc.) → re-spawn `eco_netlist_studier`
- **Mode I gap** (`parent rename ... but no paired child-scope port_connection`) → the validator message includes the exact JSON entry to add: `module_name=<child>`, `bus_bit_index=<N>`, `net_name=<port>[<bit>]`. Append it to the study JSON OR re-spawn studier with `MODE_I_HINT="add paired child-scope port_connection per validator output"`.
- **Per-stage CP/SE/SI not from neighbor** (Check 16, `not used by any existing DFF`) → the validator message lists 3 sample neighbor values. Pick one of those for the failing pin/stage and patch `port_connections_per_stage[<stage>][<pin>]` in the study JSON OR re-spawn studier with `NEIGHBOR_LOOKUP_HINT="<inst>:<pin>:<stage> use one of <samples>"`.
- **Signal-in-scope failure** (Step 1 `signal_in_scope_issues`, `input X NOT in scope of module Y`) → look for a local DFF whose Q drives the same logical signal in the target module; use its per-stage Q net name as the chain input. If no local source exists, propose a `new_port` change to promote the signal in.
- **ECO input pin undriven** (Step 5 Check 13, `[INPUT_UNDRIVEN]`) → the per-stage net the studier picked doesn't have a driver in that stage's netlist. Re-look up the neighbor DFF's per-stage value (most likely a stale name), patch `port_connections_per_stage`, re-run Step 4.
**MANDATORY advisory — Run eco_lol_impact.py (Levels-of-Logic impact):**

After the Step 3 validator passes, ALWAYS run the LOL impact analyzer. It is **advisory** (never
gates the flow) but MUST run on every study so the report reflects the latest logic depth.

```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_lol_impact.py \
    --study   data/<TAG>_eco_preeco_study.json \
    --ref-dir <REF_DIR> \
    --tag     <TAG> \
    --output  data/<TAG>_eco_lol_impact.json
cp data/<TAG>_eco_lol_impact.json <AI_ECO_FLOW_DIR>/
```

Verify stdout contains `ECO_SCRIPT_LAUNCHED: eco_lol_impact.py`. This measures the combinational
**Levels of Logic** (inverters/buffers excluded) added to each affected register D-pin — before vs
after the ECO — with an estimated added delay. FINAL_ORCHESTRATOR renders it in the report's
"Levels of Logic (LOL) Impact" slot. Do not fail the flow on its result; it is visibility only.

**Generate Step 3 RPT from JSON (ORCHESTRATOR responsibility):**

```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_rpt_generator.py step3 \
    --study  data/<TAG>_eco_preeco_study.json \
    --tag    <TAG> --jira <JIRA> --tile <TILE> \
    --output data/<TAG>_eco_step3_netlist_study_round1.rpt
```
Output format: `[stage] — N confirmed, M excluded` header per stage, then one `CONFIRMED:` / `EXCLUDED:` line per entry showing label, type, and detail (per-change_type formatter inside the script).

Then copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round1.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study_round1.rpt
```

---


---

## After Step 3 — Write Phase A handoff + emit APPLY_PHASE_READY signal + HARD STOP

After Step 3 (eco_netlist_studier + eco_netlist_verifier) completes and Step 3 validator passes, your remaining work is:
1. Write `<TAG>_phase_a_handoff.json` — describes Phase A artifacts for APPLY_ORCHESTRATOR pre-flight
2. Emit `APPLY_PHASE_READY` signal block to `<SPEC_FILE>` so the main Claude session can spawn APPLY_ORCHESTRATOR
3. Mark Step 3 task completed
4. EXIT — per CRITICAL_RULES.md Rule 2 (spawn-then-stop). DO NOT run Step 4 / Step 5 / Step 6 yourself.

### Step A — Write phase_a_handoff.json

```bash
cat > <BASE_DIR>/data/<TAG>_phase_a_handoff.json <<JSON_EOF
{
  "tag":             "<TAG>",
  "ref_dir":         "<REF_DIR>",
  "tile":            "<TILE>",
  "jira":            "<JIRA>",
  "base_dir":        "<BASE_DIR>",
  "ai_eco_flow_dir": "<AI_ECO_FLOW_DIR>",
  "fenets_tag":      "<FENETS_TAG from Step 2>",
  "phase_a_status":  "READY_FOR_PHASE_B",
  "artifacts": {
    "rtl_diff":          "data/<TAG>_eco_rtl_diff.json",
    "fenets_rename_map": "data/<TAG>_eco_fenets_rename_map.json",
    "preeco_study":      "data/<TAG>_eco_preeco_study.json"
  }
}
JSON_EOF
```

Verify the file exists and is valid JSON:
```bash
ls -la <BASE_DIR>/data/<TAG>_phase_a_handoff.json
python3 -c "import json; json.loads(open('<BASE_DIR>/data/<TAG>_phase_a_handoff.json').read())" && echo "handoff valid"
```

### Step B — Emit APPLY_PHASE_READY signal block

Append to `<SPEC_FILE>` (the same spec file the launcher gave you):

```
========================================================================
APPLY_PHASE_READY
TAG=<TAG>
REF_DIR=<REF_DIR>
TILE=<TILE>
JIRA=<JIRA>
BASE_DIR=<BASE_DIR>
AI_ECO_FLOW_DIR=<AI_ECO_FLOW_DIR>
LOG_FILE=<LOG_FILE>
SPEC_FILE=<SPEC_FILE>
HANDOFF_PATH=<BASE_DIR>/data/<TAG>_phase_a_handoff.json
========================================================================
```

The main Claude session detects this signal block (analogous to `ECO_ANALYZE_MODE_ENABLED`) and spawns APPLY_ORCHESTRATOR.

### Step C — Write EXIT sentinel marker (MANDATORY mechanical enforcement)

The main session uses this marker to verify you honored the EXIT CONTRACT (per CLAUDE.md ECO Analyze Mode block). Without this marker, the main session refuses to spawn APPLY and flags the round for engineer review (assumes you violated the EXIT CONTRACT and ran Steps 4-6 by accident).

```bash
date -Iseconds | xargs -I{} echo "exited {}" > <BASE_DIR>/data/<TAG>_study_phase_exited.marker
ls -la <BASE_DIR>/data/<TAG>_study_phase_exited.marker
```

This is the LAST file you write. After this:
- No more tool calls
- One final message
- Process terminates

### Step D — Mark task done

```python
TaskUpdate(taskId=step3_task, status="completed")
```

### Step E — HARD STOP — final message and exit

Per RULE 2 + the EXIT CONTRACT in CLAUDE.md: write the handoff, emit the signal, write the sentinel, then issue ONE final summary message and STOP. Do NOT:
- Spawn APPLY_ORCHESTRATOR yourself (the main session does that based on the sentinel + handoff)
- Run Step 4 / Step 5 / Step 6
- Read any APPLY-phase MD or script (see CLAUDE.md FORBIDDEN list)
- Write `round_handoff.json` (APPLY_ORCHESTRATOR owns that after Step 6)
- Write `eco_summary.rpt` or `eco_report.html` (FINAL_ORCHESTRATOR owns those)

Your final message — exactly this format, nothing more:
```
STUDY phase complete (Steps 1-3).
  phase_a_handoff: <BASE_DIR>/data/<TAG>_phase_a_handoff.json
  exit_sentinel:   <BASE_DIR>/data/<TAG>_study_phase_exited.marker
  signal:          APPLY_PHASE_READY emitted to <SPEC_FILE>
EXITING — main session spawns APPLY_ORCHESTRATOR in fresh context.
```

**If you find yourself at this point about to call any tool (Read/Bash/Agent/etc) — STOP. The job is done. The EXIT CONTRACT explicitly forbids further activity.**
