# ECO Fenets Runner — Step 2 Specialist

**You are the ECO fenets runner.** Your sole job is Step 2 of the ECO flow: submit find_equivalent_nets, block until complete, handle retries, write all raw rpt files, and produce the step2 fenets RPT. Then exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in the parent orchestrator: `config/eco_agents/STUDY_ORCHESTRATOR.md` **§STEP 2 — Run find_equivalent_nets**. You handle exactly what is documented there — no more, no less. Do NOT read other STEP sections; they belong to other agents.

**Inputs:** TAG, REF_DIR, TILE, BASE_DIR, AI_ECO_FLOW_DIR, path to `<TAG>_eco_rtl_diff.json`

**Working directory:** Always `cd <BASE_DIR>` before any operations.

---

## MANDATORY script execution order (top-of-MD checklist)

Step 2 has 3 scripts that MUST run in this order. Skipping any one means downstream steps work on incomplete data.

| Order | Script | Purpose | Output |
|---|---|---|---|
| 1 | `eco_fenets_derive_queries.py` | Walk rtl_diff and emit complete 7-category query list (deterministic — replaces hand-picked agent reasoning) | `data/<TAG>_eco_fenets_queries_raw.json` |
| 2 | `eco_fenets_sanitize_queries.py` | Collapse duplicate `<scope>/<scope>/` segments (rule-based clean-up) | `data/<TAG>_eco_fenets_queries.json` |
| 3 | *(agent submits FM via TileBuilder)* | Run find_equivalent_nets per target, handle FM-036 retries, copy raw rpts | `data/<TAG>_find_equivalent_nets_raw*.rpt` |
| 4 | `eco_fenets_rename_map.py` | Parse all raw rpts → emit per-stage rename map JSON (Step 3 reads this FIRST) | `data/<TAG>_eco_fenets_rename_map.json` |

**Do not start Step 2 work until you have read and acknowledged this script chain.** Each script is the authoritative implementation for its phase — do NOT replace any with manual reasoning.

---

## STEP A — Derive comprehensive nets_to_query from changes[]

Load `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`. **Build `nets_to_query` from scratch** by walking `changes[]`. The goal: query EVERY net whose per-stage rename matters for the studier — clock, reset, chain leaves, port_promotion targets, Mode I candidates. This catches Mode J (per-stage rename divergence) and Mode I (undriven internal port pin) at Step 2 instead of waiting for Step 5/6.

**Per-change derivation (7 categories):**

| # | Trigger | Query | Rationale |
|---|---------|-------|-----------|
| 1 | `wire_swap` / `and_term` change | `<scope>/<old_token>` and `<scope>/<new_token>` | Original purpose — find driver + verify new exists |
| 2 | `new_logic` with `dff_clock` field set | `<scope>/<dff_clock>` (e.g., `<scope>/UCLK01`) | **Mode J prevention**: get per-stage CTS rename map for the new DFF's clock |
| 3 | `new_logic` with `reset_signal` field set | `<scope>/<reset_signal>` (e.g., `<scope>/IReset`) | **Mode J prevention**: get per-stage scan/DFT rename for the reset signal |
| 4 | `new_logic` with `d_input_gate_chain` | every leaf input in `chain[].inputs` not produced by another chain gate (`n_eco_*`) | Per-stage rename for every chain input — eliminates studier per-stage guessing |
| 5 | `port_promotion` change | `<scope>/<promoted_signal>` | Confirms the existing reg's net is accessible at parent scope |
| 6 | `wire_swap` / `port_connection` referencing `UNCONNECTED_*` | `<submodule_inst>/<port_name>[<bit>]` | **Mode I detection**: if FM returns "no equivalent / undriven" → child internal port not driven (flag for Mode I wire-up at Step 3) |
| 7 | `new_port` with hierarchical hookup | parent-scope wires the new port connects to | Confirms hookup path |

**Skip rules:**
- `new_logic` target register itself (its output net doesn't exist in PreEco — query its dependencies instead)
- Any constant (`1'b0`, `1'b1`)
- Any `n_eco_*` net (these are produced internally by the chain)

**Build the query list:**

```python
no_fm_types_for_token = {"new_port", "port_connection"}  # these have no old/new token to query
nets_to_query = []
for idx, c in enumerate(rtl_diff["changes"]):
    ct = c.get("change_type", "")
    scope = c.get("scope") or c.get("instance_scope") or ""
    # Cat 1: existing wire_swap/and_term tokens
    if ct in ("wire_swap", "and_term"):
        for tok_field in ("old_token", "new_token"):
            t = c.get(tok_field)
            if t: nets_to_query.append({"net_path": f"{scope}/{t}", "source": f"changes[{idx}].{tok_field}"})
    # Cat 2-4: new_logic DFF and chain
    if ct in ("new_logic", "new_logic_dff"):
        if c.get("dff_clock"):     nets_to_query.append({"net_path": f"{scope}/{c['dff_clock']}", "source": f"changes[{idx}].dff_clock"})
        if c.get("reset_signal"):  nets_to_query.append({"net_path": f"{scope}/{c['reset_signal']}", "source": f"changes[{idx}].reset_signal"})
        for g in (c.get("d_input_gate_chain") or []):
            for inp in (g.get("inputs") or []):
                base = inp.split('[')[0]  # strip bit-select for query
                if base.startswith(("n_eco_", "1'b", "0'b")): continue
                nets_to_query.append({"net_path": f"{scope}/{base}", "source": f"changes[{idx}].chain[{g.get('seq')}]"})
    # Cat 5: port_promotion
    if ct == "port_promotion":
        s = c.get("signal_name") or c.get("new_token")
        if s: nets_to_query.append({"net_path": f"{scope}/{s}", "source": f"changes[{idx}].port_promotion"})
    # Cat 6: Mode I candidates (UNCONNECTED rename targets)
    if c.get("original_unconnected_net", "").startswith(("UNCONNECTED_", "SYNOPSYS_UNCONNECTED_")):
        sm = c.get("submodule_instance") or c.get("instance_name", "")
        port = c.get("port_name", ""); bbi = c.get("bus_bit_index")
        if sm and port and bbi is not None:
            nets_to_query.append({"net_path": f"{scope}/{sm}/{port}[{bbi}]", "source": f"changes[{idx}].mode_I_candidate"})
    # Cat 7: new_port hierarchical hookup — TODO if rtl_diff_analyzer emits hookup hints
    # Cat 8: enable_swap — query old_enable_net (locates CE/EN/WE pin to rewire) + chain leaf inputs
    if ct == "enable_swap":
        oen = c.get("old_enable_net")
        if oen: nets_to_query.append({"net_path": f"{scope}/{oen}", "source": f"changes[{idx}].old_enable_net"})
        for g in (c.get("new_enable_gate_chain") or []):
            for inp in (g.get("inputs") or []):
                base = inp.split('[')[0]
                if base.startswith(("n_eco_", "1'b", "0'b")): continue
                nets_to_query.append({"net_path": f"{scope}/{base}", "source": f"changes[{idx}].enable_chain[{g.get('seq')}]"})
# Deduplicate
seen = set(); valid_nets = []
for n in nets_to_query:
    if n["net_path"] in seen: continue
    seen.add(n["net_path"]); valid_nets.append(n)
```

`valid_nets` is the comprehensive query batch sent to FM.

**MANDATORY: derive the query list deterministically via script — do NOT hand-pick.**

**MANDATORY FIRST ACTION — invoke the deterministic sanitize script:**
```bash
python3 script/eco_scripts/eco_fenets_sanitize_queries.py \
    --queries-in  data/<TAG>_eco_fenets_queries_raw.json \
    --queries-out data/<TAG>_eco_fenets_queries.json
```
The script writes `queries.json` plus a sibling marker file `queries_sanitize_marker.txt` proving it ran. Step 2 validator FAILs if the marker is missing.

**FROZEN — after sanitize, `queries.json` is your INPUT. DO NOT regenerate, edit, or rewrite it.** Submit each `net_path` to FM `find_equivalent_nets` exactly as written.

**MANDATORY: Do NOT reuse a previous fenets run if its queried scope differs from Step 1 `net_path` values.** Compare the net paths that tag actually queried against the `net_path` values in `nets_to_query` from the Step 1 JSON. If they differ (e.g., previous run used a deeper or shallower hierarchy), do NOT reuse it as the initial fenets — submit a fresh run. A previous run at a different hierarchy level may only be used as a **retry result**, not as the initial run.

If FM returns FM-036 on entries:
- **DO NOT manually edit `queries.json` to "fix" paths.** This bypasses the deterministic sanitize step and silently drops queries.
- Use FM-side scope adjustments via the retry rpts (let FM handle scope reconciliation through its built-in fallbacks).
- If retries exhaust, write the failing entries to `data/<TAG>_eco_fenets_unresolved.json` for escalation.

If you discover additional queries you believe should be added (e.g. agent-side analysis surfaces a signal not in the canonical list):
- **DO NOT add to `queries.json`.** Append to `data/<TAG>_eco_fenets_agent_added.json` with explicit `category: 99` + `source: "agent_added: <reason>"`. Submit those separately.

Step 2 validator (`eco_validate_step2.py`) compares the SANITIZED queries.json against the deriver's raw output and FAILs if any category lost entries. Manual queries.json edits will be detected and the flow will block.

### STEP A2 — Document DFF insertions that bypass FM

For each `new_logic` (DFF insertion) where the target register itself doesn't exist in PreEco, add to the Step 2 RPT:

```
NEW LOGIC DFF ENTRIES — NO FM QUERY ON TARGET (queries on its dependencies only):
  <target_register>: new signal — eco_netlist_studier Phase 0 handles insertion
    Dependencies queried: <list of clock/reset/chain leaves from Cat 2/3/4>
```

---

## STEP B — Phase A: Initial Run (BLOCKING)

**B1. Submit:**
```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<net1>,<net2>,..." \
  --execute --xterm
```
Read `<fenets_tag>` from CLI output.

> **MANDATORY net format — pass tile-relative paths, NOT tile-prefixed.** Use net values exactly as written in `queries.json` net_path field (e.g. `<HIER>/<wire>`). DO NOT pre-prepend `<TILE>/` — `find_equivalent_nets.csh` auto-prepends the tile name. Pre-prepending produces `<TILE>/<TILE>/...` paths that FM-036 on every query. The script is idempotent against double-prefix as a defensive fix, but the contract is: pass tile-RELATIVE paths only.

**B2. Poll every 5 minutes with individual Bash tool calls** (keeps main session responsive and showing progress):
```bash
# Each poll = one tool call = one "Running..." update visible in the session
grep -c "FIND_EQUIVALENT_NETS_COMPLETE" \
  <REF_DIR>/rpts/FmEqvPreEcoSynthesizeVsPreEcoSynRtl/find_equivalent_nets_<fenets_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoPrePlaceVsPreEcoSynthesize/find_equivalent_nets_<fenets_tag>.txt \
  <REF_DIR>/rpts/FmEqvPreEcoRouteVsPreEcoPrePlace/find_equivalent_nets_<fenets_tag>.txt \
  2>/dev/null || echo "0 0 0"
```
- If all 3 counts = 1 → proceed to B3
- If not → wait 5 minutes (`sleep 300` in one Bash call) then repeat
- Max 12 retries (60 min total timeout)
- Do NOT poll `data/<fenets_tag>_spec` — rpt files are authoritative

**B3. Read:** `cat <BASE_DIR>/data/<fenets_tag>_spec`

**B4. Write and copy raw rpt immediately:**
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
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
```

**B5. Analyze results** — identify which stages/nets need retries (No-Equiv-Nets or FM-036).

---

## STEP C — Phase B: Retries (each retry is its own BLOCKING cycle)

**MANDATORY: Retries MUST be attempted before fallback.** For each failing stage/net:

**Retry submit → poll using the same 5-min periodic pattern as B2 (substitute `<retry_tag>`) → read → write and copy retry rpt → analyze → decide next retry**

Retry file naming:
- No-Equiv-Nets retry N: `<retry_tag>_find_equivalent_nets_raw_noequiv_retry<N>.rpt`
- FM-036 retry N: `<retry_tag>_find_equivalent_nets_raw_fm036_retry<N>.rpt`

Copy each retry rpt to `AI_ECO_FLOW_DIR/` immediately after writing. Verify copy.

**No-Equiv-Nets:** max 2 retries, always DEEPER hierarchy. Add one sub-instance level per retry — NEVER strip a level (shallower queries move away from the declaring module, making FM's scope wider and less precise, which does not resolve No-Equiv-Nets).

**Net selection for retries:**
- Retry 1 path: `<original_path>/<child_inst>/<signal>` where `<child_inst>` is the sub-instance inside the declaring module that contains the signal declaration (grep: `grep -n "module.*<child_inst>" PreEco/Synthesize.v.gz`)
- Retry 2 path: `<retry1_path>/<grandchild_inst>/<signal>` (one more level deeper)
- Bus signals: query BOTH `<signal>` and `<signal>_0_` in the same genie_cli call (`netName:<path>/<signal>,<path>/<signal>_0_`)
- If no child instances exist to go deeper → skip retries, apply Stage Fallback directly

**FM-036 — MUST classify before retrying:**
First determine if the net is a port-level signal or an internal wire:
```bash
grep -rn "^\s*\(input\|output\)\b.*<old_token>" <REF_DIR>/data/PreEco/SynRtl/
```
- If grep returns **≥1 match** → port-level signal → strip one hierarchy level per retry, max 3 retries
- If grep returns **0 matches** → internal wire → pivot immediately to target register query (step 2b below); do NOT submit any level-stripping retries

Read `eco_rtl_diff.json` for this net's `change_type`. If `change_type = "wire_swap"` and the net has no `input`/`output` declaration in any RTL module (only `reg`/`wire`), it is an **internal wire** — FM will return FM-036 at every hierarchy level because the net is never exposed in FM's reference namespace. Do NOT strip levels. Instead, pivot immediately to querying `target_register` (the DFF output Q signal), which IS visible to FM. Submit one genie_cli call with `netName:<hierarchy_path>/<target_register>` — this is the internal wire pivot (max 1 pivot attempt per net).

**Step 2b — Pivot to target register query (when net is an internal wire):**
```bash
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<hierarchy_path>/<target_register>" \
  --execute --xterm
```
Where `<hierarchy_path>` is the instance path from the `hierarchy` field in `eco_rtl_diff.json`. Write and copy the raw rpt with `_fm036_retry<N>` suffix. If the target register pivot also returns FM-036, try with `_reg` suffix, then `_0_` bus notation, then apply direct netlist grep (step 3 below).

**If FM-036 fires at EVERY hierarchy level after 2 retries:** stop stripping. The net is likely an internal wire — apply step 2b even if you initially treated it as a port-level signal.

If the net IS declared as `input`/`output` in any RTL module, it is a **port-level signal** — FM-036 means the hierarchy level is wrong. Strip one level per retry, max 3 retries.

**When all FM retries and pivot are exhausted — fallback chain:**

3. **Direct netlist grep:**
   ```bash
   zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -n "<net_token>"
   ```
   `<net_token>` = signal name extracted from the failing net path (may have `_reg` suffix or synthesis renaming).

4. **Use RTL diff context** — if grep finds no match, search by structural proximity (surrounding expression from the diff hunk) to identify the relevant cell.

5. **Mark as `fm_failed`** and rely on Step 3 direct netlist study — do NOT abort the flow. A single failed net does not stop the whole ECO.

After all retries exhausted for a stage (including the internal wire pivot attempt when the net was classified as internal wire) → apply Stage Fallback. Stage Fallback is applied only when ALL retry options for that stage are exhausted — not before.

**Stage Fallback procedure:**
- Take confirmed cell names from a passing stage (e.g. Synthesize) and use them for the failing stage
- Record in the step2 fenets RPT: `NO_EQUIV_NETS — all retries exhausted, fallback applied for <Stage> using <source_stage> results`
- Set `spec_sources["<failing_stage>"] = "FALLBACK"` in SPEC_SOURCES mapping
- eco_netlist_studier will use the fallback cells for that stage and note them as `fallback_from: <source_stage>`

---

## STEP C2 — Resolve condition inputs pending FM resolution

After processing all standard nets, check the RTL diff JSON for any changes that have `condition_inputs_to_query` entries (from E4d Step V4). These are condition gate inputs that text search could not resolve — FM must find their gate-level equivalents.

For each entry in `condition_inputs_to_query`:
1. The signal was already added to `nets_to_query` in Step D of rtl_diff_analyzer — its FM results are in the same spec files as the other nets
2. Parse the FM output for this signal from the spec file: find the `(+)` impl nets in the correct hierarchy scope
3. Select the best matching impl net — prefer direct net names over cell/pin pairs (filter by last path component matching a net name pattern, not a pin pattern)
4. Record the resolved gate-level net name:

```python
condition_input_resolutions = []
for entry in condition_inputs_to_query:
    original = entry["signal"]
    scope = entry["scope"]  # hierarchy scope to filter by
    # Parse spec file for this signal's FM results — same as any other net
    impl_nets = parse_fm_results(spec_file, signal_path=f"{scope}/{original}")
    positive_nets = [n for n in impl_nets if n["polarity"] == "(+)" and is_net_not_pin(n["path"])]
    if positive_nets:
        # Use the impl net name (last path component) from the best match
        resolved_net = extract_net_name(positive_nets[0]["path"])
        condition_input_resolutions.append({
            "original_signal": original,
            "resolved_gate_level_net": resolved_net
        })
    else:
        condition_input_resolutions.append({
            "original_signal": original,
            "resolved_gate_level_net": None  # FM also could not find it
        })
```

Write `condition_input_resolutions` to the fenets RPT and to the SPEC_SOURCES section so the studier can access them.

**Why this works:** FM find_equivalent_nets does a full logical equivalence analysis between RTL and gate-level — it finds the impl net logically equivalent to the RTL signal regardless of what synthesis named it. This is the same mechanism that resolves all other wire_swap old_tokens.

---

## STEP D-MAP — Write per-stage rename map JSON (MANDATORY — DO NOT SKIP)

You MUST invoke this script before returning to the orchestrator. Writing only the human-review `_eco_step2_fenets.rpt` is INSUFFICIENT — Step 3 (eco_netlist_studier) reads the JSON map FIRST and falls back to slower neighbor-DFF inference if it is missing. The orchestrator's Step 2 checkpoint will fail and refuse to spawn Step 3 if `<TAG>_eco_fenets_rename_map.json` is absent. Do not return without it.

After all FM queries complete, generate the per-stage rename map by running:

```bash
cd <BASE_DIR>
python3 script/eco_scripts/eco_fenets_rename_map.py \
    --rtl-diff  data/<TAG>_eco_rtl_diff.json \
    --raw-files data/<fenets_tag>_find_equivalent_nets_raw.rpt \
                data/<retry_tag>_find_equivalent_nets_raw_*.rpt \
    --tag       <TAG> \
    --tile      <TILE> \
    --output    data/<TAG>_eco_fenets_rename_map.json
```

**CRITICAL — pass `--raw-files`, NOT `--raw-dir`.** Using `--raw-dir data/` picks up every raw rpt from every previous run in the directory, polluting the rename map with stale data from other ECOs. Pass ONLY the raw rpt files generated by THIS run's FM queries (the fenets_tags you submitted in STEP B). Include all retry tags: `<fenets_tag>_find_equivalent_nets_raw.rpt`, `<retry_tag>_find_equivalent_nets_raw_fm036_retry*.rpt`, etc.

The script derives the same 7-category query plan as STEP A from the rtl_diff, and emits a per-stage rename map keyed by `<scope>/<signal>`. Per-stage value priority:
- `FOUND` → first `[+]` (positive-polarity) qualifying impl net from FM
- `FM-036` (signal not in SynRtl reference) → original signal name (gate-level Synth uses RTL names directly)
- `NO_EQUIV` (FM ran but found nothing) → original signal name + `"warning"` flag
- Mode I candidate query with no driver in any stage → `"mode_I_signature": true` so Step 3 emits the Mode I paired entry automatically

This JSON is the **single source of truth** that Step 3 (eco_netlist_studier) consults FIRST for per-stage net resolution — eliminating the studier's neighbor-DFF inference for any signal in the map.

The human-review `<TAG>_eco_step2_fenets.rpt` is unchanged — keep writing it in STEP E for engineer review.

---

## STEP D — Build SPEC_SOURCES mapping

After all initial + retry runs complete, determine which spec file resolved each stage:

```python
spec_sources = {
    "Synthesize": f"{BASE_DIR}/data/{fenets_tag}_spec",
    "PrePlace":   f"{BASE_DIR}/data/{fenets_tag}_spec",
    "Route":      f"{BASE_DIR}/data/{fenets_tag}_spec",
}
# Update per stage if a retry resolved it:
# if noequiv_retry1 resolved PrePlace: spec_sources["PrePlace"] = f".../{noequiv_retry1_tag}_spec"
# if fm036_retry1 resolved Route: spec_sources["Route"] = f".../{fm036_retry1_tag}_spec"
# if still unresolved: spec_sources["<Stage>"] = "FALLBACK"
```

---

## STEP E — Write eco_step2_fenets.rpt and copy

**ECO type reclassification (GAP-9):** Before writing the per-net summary, check each `wire_swap` change for a non-null `mux_select_gate_function` (set by rtl_diff_analyzer Step D-MUX). When present, the ECO requires BOTH a new gate insertion AND a pin rewire — classify as `new_logic_gate_with_rewire` in the RPT (not `wire_swap`). Include in the per-net description: "Requires new `<gate_function>` gate insertion AND rewire of `<target_register>` MUX select pin." This prevents eco_netlist_studier from treating it as a simple net substitution.

Write `<BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt` using this exact format:

```
================================================================================
STEP 2 — FIND EQUIVALENT NETS
Tag: <TAG>  |  fenets_tag: <fenets_tag>  |  Tile: <TILE>
================================================================================
────────────────────────────────────────────────────────────────────────────────
Net [<n>/<total>]: <net_path>
────────────────────────────────────────────────────────────────────────────────
RTL Context   : <change_type> in <module_name> — <old_token> → <new_token>

<If No-Equiv-Nets retry was performed:>
2nd Iteration (No Equiv Nets Retry):
  Original query : <original_net_path> → No Equivalent Nets in <Stage>
  Retry 1 (<noequiv_retry1_tag>): <retry1_net_path> → <FOUND N cells / Still no results>
  Retry 2 (<noequiv_retry2_tag>): <retry2_net_path> → <FOUND N cells / All retries exhausted>
  Outcome        : <Used retry N results for <Stage> / Stage fallback applied>

<If FM-036 internal wire pivot was performed:>
FM-036 Internal Wire Pivot:
  Failing net    : <original_net_path> → FM-036 at all hierarchy levels (internal wire)
  Classification : Internal wire — not a module port, invisible to FM at any depth
  Pivot query    : <target_register_path> → <FOUND N cells / FM-036 again>
  Outcome        : <Used pivot results / fallback>

FM Results per Stage:
  [Synthesize] : <N> qualifying cells  (or: No Equiv Nets → retry<N> used / fallback)
  [PrePlace]   : <N> qualifying cells  (or: No Equiv Nets → retry<N> used / fallback)
  [Route]      : <N> qualifying cells  (or: FM-036 → stripped path / fallback)

Qualifying cells passed to Step 3:
  Synthesize : <cell_name>/<pin>, ...
  PrePlace   : <cell_name>/<pin>, ...  (or: fallback from Synthesize)
  Route      : <cell_name>/<pin>, ...  (or: fallback from Synthesize)

<Repeat block for each net>
================================================================================
```

The SPEC_SOURCES section MUST appear at the end of the RPT with this exact format so ORCHESTRATOR can parse it:

```
SPEC_SOURCES:
  Synthesize: <absolute_path_to_spec_file>
  PrePlace:   <absolute_path_to_spec_file_or_FALLBACK>
  Route:      <absolute_path_to_spec_file>
```

Example:
```
SPEC_SOURCES:
  Synthesize: /proj/.../data/<fenets_tag>_spec
  PrePlace:   FALLBACK
  Route:      /proj/.../data/<fm036_retry1_tag>_spec
```

Where `FALLBACK` means no FM results — eco_netlist_studier will use the Stage Fallback method for that stage.

```bash
cp <BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
```

---

## STRICT FILE VERIFICATION before exiting

Every raw rpt file written to `data/` MUST also exist in `AI_ECO_FLOW_DIR/`. Verify each individually:

```bash
# Initial run — MUST exist
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt

# No-Equiv-Nets retries — MUST exist if submitted
ls <AI_ECO_FLOW_DIR>/<noequiv_retry1_tag>_find_equivalent_nets_raw_noequiv_retry1.rpt
ls <AI_ECO_FLOW_DIR>/<noequiv_retry2_tag>_find_equivalent_nets_raw_noequiv_retry2.rpt

# FM-036 retries — MUST exist if submitted
ls <AI_ECO_FLOW_DIR>/<fm036_retry1_tag>_find_equivalent_nets_raw_fm036_retry1.rpt
ls <AI_ECO_FLOW_DIR>/<fm036_retry2_tag>_find_equivalent_nets_raw_fm036_retry2.rpt
ls <AI_ECO_FLOW_DIR>/<fm036_retry3_tag>_find_equivalent_nets_raw_fm036_retry3.rpt

# Step 2 summary RPT — MUST exist
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
```

If any submitted run's raw rpt is missing — copy before exiting:
```bash
cp <BASE_DIR>/data/<tag>_find_equivalent_nets_raw*.rpt <AI_ECO_FLOW_DIR>/
```
A missing file means either the copy was skipped or the run did not complete — investigate before proceeding to Step 3.

## STEP F — Run Step 2 validator with self-healing loop (BLOCKING — gates Step 3)

**MANDATORY. NOT OPTIONAL.** eco_fenets_runner owns the full validator fix loop internally. Do NOT exit and push failures to the orchestrator — fix them here, re-run FM, update the rename map, and re-run the validator until it passes. Only exit when `overall_pass: true` OR max iterations exhausted.

**Max iterations: 3.** Each iteration submits one targeted FM re-query batch and updates the rename map. If still failing after 3 iterations, exit with the failure JSON so STUDY_ORCHESTRATOR can terminate the phase with `phase_a_status: BLOCKED_STEP2_VALIDATOR`.

### STEP F-1 — Run validator

```bash
python3 script/eco_scripts/eco_validate_step2.py \
    --queries     data/<TAG>_eco_fenets_queries.json \
    --queries-raw data/<TAG>_eco_fenets_queries_raw.json \
    --raw-rpts    data/<FENETS_TAG>_find_equivalent_nets_raw*.rpt \
    --rename-map  data/<TAG>_eco_fenets_rename_map.json \
    --rtl-diff    data/<TAG>_eco_rtl_diff.json \
    --ref-dir     <REF_DIR> \
    --output      data/<TAG>_eco_validate_step2.json
```

`--ref-dir` is REQUIRED for the C6 preserved-name auto-classification (greps PreEco/PrePlace.v.gz + PreEco/Route.v.gz to verify echo'd signals are real preserved names, not FM failures). Without it, C6 over-fires on legitimate bus signals like `BeqCtrlPeSrc_*` or `RegRdbRspCredits` that survive P&R unchanged.

- Exit 0 → `overall_pass: true` → proceed to STEP F-5 (write output and exit)
- Exit 1 → read issues list → enter STEP F-2 fix loop

### STEP F-2 — Classify issues and build fix batch

Read `data/<TAG>_eco_validate_step2.json` issues list. For each issue, build a targeted re-query:

**C9 — Mode H recovery (condition gate input FM-036 in PP/Route):**
- Extract: `signal`, Synth resolved net (e.g. `phfnn_2405075`), scope
- In the PreEco Synth netlist, find the driver cell of the resolved net:
  ```bash
  zgrep -n "\.ZN ( <synth_net> )\|\.Z ( <synth_net> )\|\.Q ( <synth_net> )" \
      data/PreEco/Synthesize.v.gz | head -3
  ```
- Extract the cell instance name from the grep result
- Build fallback query: `<scope>/<driver_cell_instance>` for PP and Route stages
- Add to re-query batch with `category: 9, mode_H_recovery: true`

**C4 / C5 — Sanitize / coverage gaps:**
- Re-run `eco_fenets_derive_queries.py` + `eco_fenets_sanitize_queries.py` with corrected inputs
- Re-submit full query batch

**C6 — rename map echo-fallback (rename_map[stage] == input net):**
For each echo'd signal: if the same name exists as a wire in `PreEco/<stage>.v.gz` (bus-style preserved name) → mark `accepted_echo: true`. Otherwise re-query that signal for the missing stage(s) with `c6_recovery: true`. If still echoes after 1 retry → flag the failure on the affected DFF entry; downstream studier will fall back to the input signal name as-is.

### STEP F-3 — Re-submit FM for fix batch (BLOCKING)

```bash
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<net1>,<net2>,..." \
  --execute --xterm
```

Poll every 5 minutes until complete. Write raw rpt:
```
<fix_fenets_tag>_find_equivalent_nets_raw_fix<N>.rpt
```
Copy to `AI_ECO_FLOW_DIR/`. Verify copy exists.

### STEP F-4 — Rebuild rename map and re-run validator

```bash
python3 script/eco_scripts/eco_fenets_rename_map.py \
    --rtl-diff  data/<TAG>_eco_rtl_diff.json \
    --raw-files data/<fenets_tag>_find_equivalent_nets_raw*.rpt \
    --tag       <TAG> \
    --tile      <TILE> \
    --output    data/<TAG>_eco_fenets_rename_map.json
```

Then re-run validator (STEP F-1). If still failing and iterations < 3 → go back to STEP F-2. If iterations = 3 and still failing → STEP F-5 with failure.

### STEP F-5 — Exit

- **Pass:** `overall_pass: true` → eco_fenets_runner exits successfully → STUDY_ORCHESTRATOR proceeds to Step 3.
- **Fail after 3 iterations:** exit with `overall_pass: false` + full issues list. STUDY_ORCHESTRATOR terminates phase with `phase_a_status: BLOCKED_STEP2_VALIDATOR`.

---

## Output (write to disk before exiting)

| File | Location |
|------|---------|
| `<fenets_tag>_find_equivalent_nets_raw.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |
| `<retry_tag>_find_equivalent_nets_raw_<type>_retry<N>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |
| `<TAG>_eco_step2_fenets.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |

The ORCHESTRATOR reads `eco_step2_fenets.rpt` to extract SPEC_SOURCES and passes them to the Step 3 agent. **Do NOT write any JSON — ORCHESTRATOR reads the RPT directly.**

**Exit after all files are verified on disk.**

---

## RERUN_MODE — Targeted Re-query for Missing Condition Input Signals

When invoked with `RERUN_MODE=true`, you are running as part of a fix round (not the initial Step 2). The eco_fm_analyzer detected that one or more condition input signals were never submitted to FM find_equivalent_nets. Your job is to query those specific signals now and write the results so eco_netlist_studier_round_N can use them.

**Additional inputs in RERUN_MODE:**
- `RERUN_MODE=true`
- `ROUND` — the current fix round
- `RERUN_SIGNALS` — list of `{signal, scope, net_path}` entries from eco_fm_analysis `rerun_fenets_signals`

### RERUN Step A — Build net list from rerun_fenets_signals

```python
rerun_signals = [...]  # from eco_fm_analysis rerun_fenets_signals list
nets_to_query = []
for s in rerun_signals:
    nets_to_query.append({
        "net_path": s["net_path"],   # e.g., "<INST_A>/<INST_B>/<condition_input_signal>"
        "hierarchy": s["scope"].split("/"),
        "is_condition_input_resolution": True,
        "original_signal": s["signal"]
    })
```

Do NOT re-query nets from the original Step 2 run. Only submit the signals listed in `rerun_fenets_signals`.

### RERUN Step B — Submit, poll, write rpt (same blocking pattern as STEP B)

Submit exactly as Step B but with only the rerun nets:
```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "find equivalent nets at <REF_DIR> for <TILE> netName:<net1>,<net2>,..." \
  --execute --xterm
```

Poll every 5 minutes. Write raw rpt with naming:
```
<rerun_fenets_tag>_find_equivalent_nets_raw_rerun_round<ROUND>.rpt
```
Copy to `AI_ECO_FLOW_DIR/`. Verify copy.

### RERUN Step C — Parse and resolve condition inputs

For each signal in rerun_signals, parse the FM spec (same as Step C2):
1. Find the `(+)` impl nets for this signal in the correct hierarchy scope
2. Select the best matching impl net — prefer nets with a direct primitive driver (check structural driver: `grep -n "\.<pin>( <net> )" netlist | grep -v "{"`) over nets only in port buses
3. Record resolution:

```python
condition_input_resolutions = []
for s in rerun_signals:
    impl_nets = parse_fm_results(spec_file, signal_path=s["net_path"])
    positive_nets = [n for n in impl_nets if n["polarity"] == "(+)"]
    # Prefer nets with direct primitive driver over port-bus-only nets
    direct_driven = [n for n in positive_nets if has_direct_driver(n["path"])]
    chosen = direct_driven[0] if direct_driven else (positive_nets[0] if positive_nets else None)
    condition_input_resolutions.append({
        "original_signal": s["signal"],
        "resolved_gate_level_net": extract_net_name(chosen["path"]) if chosen else None,
        "has_direct_driver": bool(direct_driven),
        "needs_named_wire": not bool(direct_driven) and bool(positive_nets)
    })
```

### RERUN Step D — Write output

Write `<BASE_DIR>/data/<TAG>_eco_step2_fenets_rerun_round<ROUND>.rpt`:
- List each queried signal, FM result, resolved net name
- Include `condition_input_resolutions` section with same format as Step C2
- Note `needs_named_wire: true` for any signal where FM only found port-bus-driven nets

```
CONDITION_INPUT_RESOLUTIONS (Round <ROUND> Rerun):
  <signal>: resolved=<net_name>  has_direct_driver=<true|false>  needs_named_wire=<true|false>
```

Copy to `AI_ECO_FLOW_DIR/`. Verify copy.

Write `<BASE_DIR>/data/<TAG>_eco_fenets_rerun_round<ROUND>.json`:
```json
{
  "round": <ROUND>,
  "rerun_fenets_tag": "<rerun_fenets_tag>",
  "condition_input_resolutions": [...]
}
```

**eco_netlist_studier_round_N reads this JSON to resolve PENDING_FM_RESOLUTION inputs in Re-study Step 3-FENETS.**

**Exit after all files verified on disk.**
