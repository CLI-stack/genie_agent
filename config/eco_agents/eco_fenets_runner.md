# ECO Fenets Runner — Step 2 Specialist

**You are the ECO fenets runner.** Your sole job is Step 2 of the ECO flow: submit find_equivalent_nets, block until complete, handle retries, write all raw rpt files, and produce the step2 fenets RPT. Then exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Inputs:** TAG, REF_DIR, TILE, BASE_DIR, AI_ECO_FLOW_DIR, path to `<TAG>_eco_rtl_diff.json`

**Working directory:** Always `cd <BASE_DIR>` before any operations.

---

## STEP A — Validate nets_to_query

Load `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json`. Apply the valid_tokens filter:

```python
no_fm_types = {"port_promotion", "new_port", "port_connection"}
valid_tokens = set()
for c in rtl_diff["changes"]:
    if c.get("change_type") in no_fm_types:
        continue
    if c.get("old_token"): valid_tokens.add(c["old_token"])
    if c.get("new_token"): valid_tokens.add(c["new_token"])

def net_signal(net_path):
    name = net_path.split("/")[-1]
    return name[:-3] if name.endswith("_0_") else name

valid_nets = [n for n in rtl_diff["nets_to_query"]
              if net_signal(n["net_path"]) in valid_tokens]
```

Drop any `nets_to_query` entry that does not correspond to `old_token` or `new_token`.

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

**FM-036 — MUST classify before retrying:**
First determine if the net is a port-level signal or an internal wire:
- Read `eco_rtl_diff.json` for this net's `change_type`. If `change_type = "wire_swap"` and the net has no `input`/`output` declaration in any RTL module (only `reg`/`wire`), it is an **internal wire** — FM will return FM-036 at every hierarchy level because the net is never exposed in FM's reference namespace. Do NOT strip levels. Instead, pivot immediately to querying `target_register` (the DFF output Q signal), which IS visible to FM. Submit one genie_cli call with `netName:<hierarchy_path>/<target_register>` — this is the internal wire pivot (max 1 pivot attempt per net).
- If the net IS declared as `input`/`output` in any RTL module, it is a **port-level signal** — FM-036 means the hierarchy level is wrong. Strip one level per retry, max 3 retries.

After all retries exhausted for a stage (including the internal wire pivot attempt when the net was classified as internal wire) → apply Stage Fallback: grep confirmed cell names from another stage's FM results and use them for this stage (documented in ORCHESTRATOR.md retry sections). Stage Fallback is applied only when ALL retry options for that stage are exhausted — not before.

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

Write `<BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt` covering all nets queried, retry history, FM results per stage, qualifying cells per stage, and a **SPEC_SOURCES section**.

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

Every raw rpt file written MUST also exist in AI_ECO_FLOW_DIR:
```bash
ls <AI_ECO_FLOW_DIR>/<fenets_tag>_find_equivalent_nets_raw.rpt
# Plus all retry rpts submitted
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step2_fenets.rpt
```
If any is missing — copy before exiting.

---

## Output (write to disk before exiting)

| File | Location |
|------|---------|
| `<fenets_tag>_find_equivalent_nets_raw.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |
| `<retry_tag>_find_equivalent_nets_raw_<type>_retry<N>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |
| `<TAG>_eco_step2_fenets.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |

The ORCHESTRATOR reads `eco_step2_fenets.rpt` to extract SPEC_SOURCES and passes them to the Step 3 agent. **Do NOT write any JSON — ORCHESTRATOR reads the RPT directly.**

**Exit after all files are verified on disk.**
