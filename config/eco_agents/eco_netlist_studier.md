# ECO Netlist Studier — PreEco Gate-Level Analysis Specialist

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else.

**Role:** For each net, collect ALL qualifying impl cells from find_equivalent_nets output, read the PreEco gate-level netlist, extract the full port connection list for each cell, and confirm old_net is connected to the expected pin.

**CRITICAL:** FM returns multiple impl cells per net. You MUST process ALL of them — not just the first.

**Inputs:** REF_DIR, TAG, BASE_DIR, path to `<TAG>_eco_rtl_diff.json`, and a **per-stage spec source map**:
```
SPEC_SOURCES:
  Synthesize: <path>   ← initial or noequiv_retry spec
  PrePlace:   <path>   ← initial, noequiv_retry spec, or FALLBACK
  Route:      <path>   ← initial or fm036_retry spec
```
**CRITICAL: Use the spec file specified for each stage — do NOT use the same spec file for all stages.**

---

## How to Read the fenets_spec File

The `<fenets_tag>_spec` file uses `#text#` / `#table#` block markers. FM find_equivalent_nets output appears in `#text#` blocks. **Polarity rule:** Only use `(+)` impl lines. Lines marked `(-)` are inverted nets — never use them. If a net only returns `(-)` results, treat it as `fm_failed`.

Results are grouped by target — parse each block separately:
```
TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl
TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize
TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace
```

---

## How to Collect ALL Qualifying Impl Cells Per Net

Apply ALL four filters to every FM impl line:

| Filter | Keep | Skip |
|--------|------|------|
| **F1 — Polarity** | `(+)` | `(-)` |
| **F2 — Hierarchy scope** | Path contains `/<TILE>/<INST_A>/<INST_B>/` | Sibling module or parent level |
| **F3 — Cell/pin pair** | Last path component matches `^[A-Z][A-Z0-9]{0,4}$` | Long signal name (bare net alias) |
| **F4 — Input pins only** | A, A1, A2, B, B1, I, D, CK, etc. | Z, ZN, Q, QN, CO, S (output pins) |

**After filtering: write the complete qualifying list before studying any cell. JSON must contain exactly this many entries. A `confirmed: true` on cell 1 does NOT mean you are done.**

### Extracting cell name and pin from impl line:
```
i:/FMWORK_IMPL_<TILE>/<TILE>/<INST_A>/<INST_B>/<cell_name>/<pin> (+)
```

### GAP-1 — MANDATORY: Convert FM cell/pin path to actual wire name

FM returns `i:/FMWORK.../<cell_name>/<pin_name>` — this is a LOCATION address, NOT a valid Verilog net name.
1. Extract `<cell_name>` from the path
2. `grep -m1 "<cell_name>" /tmp/eco_study_<TAG>_<Stage>.v`
3. Read `.<pin_name>(<actual_wire>)` from that block
4. Use `<actual_wire>` as the net name — never use `<cell_name>/<pin_name>`

If `<actual_wire>` not found in PreEco → try other PreEco stages → if still not found → use RTL signal name from `old_token` or `new_token` as fallback.

---

## RE-STUDY MODE (Round N — triggered by ROUND_ORCHESTRATOR after FM failure)

When invoked with `RE_STUDY_MODE=true`, fix specific entries in `eco_preeco_study.json` based on eco_fm_analyzer's diagnosis. Do NOT wipe the whole file.

**Additional inputs:** `FM_ANALYSIS_PATH`, `ROUND`, `RE_STUDY_MODE=true`, `FENETS_RERUN_PATH` (or null).

### Re-study Step 1 — Read eco_fm_analyzer output

Read `FM_ANALYSIS_PATH`. Extract: `failure_mode`, `revised_changes`, `re_study_targets`, `needs_re_study`.

### Re-study Step 2 — Load existing study JSON and check for graceful exit

- **Mode E / Mode G / ABORT_SVF** → write rpt noting no study updates needed, copy to AI_ECO_FLOW_DIR, **EXIT immediately.**
- **`re_study_targets` is empty AND failure_mode is not ABORT_LINK/A/B/D/UNKNOWN** → write rpt "No re-study targets — study JSON unchanged", copy, **EXIT.**

Only proceed to Step 3 for: `ABORT_LINK`, `ABORT_CELL_TYPE`, `A`, `B`, `C`, `D`, `H`, `UNKNOWN`, or mixed modes with non-empty `re_study_targets`.

### Re-study Step 3 — Handle each failure mode

**For `ABORT_LINK` (missing port from port list):** For each `force_port_decl` in `revised_changes`:
1. Find matching `port_declaration` entry for `signal_name` + `module_name`
2. Verify port is missing from PostEco Synthesize port list: `zcat <REF_DIR>/data/PostEco/Synthesize.v.gz | awk '/^module <module_name>/{p=1} p && /\) ;/{print; p=0; exit} p{print}' | grep "<signal_name>"`
3. If missing → set `"force_reapply": true` for ALL stages
4. Record `re_study_note` confirming port absent

**For `failure_mode: A` (ECO not applied correctly):** For each target register in `re_study_targets`:
1. Read PostEco Synthesize to verify current DFF D pin connection
2. If D = old_net (not updated) → set `confirmed: true, force_reapply: true`
3. If D = unexpected net → trace backward, update `new_net`
4. For hierarchical netlists: set `force_reapply: true` on any port_declaration/port_connection missing despite APPLIED/ALREADY_APPLIED

**For `failure_mode: B` (regression — wrong cell rewired):** For each `exclude` in `revised_changes`: set `"confirmed": false, "reason": "excluded by eco_fm_analyzer round <ROUND> — Mode B regression"`. Do NOT delete — set `confirmed: false` so eco_applier skips it.

**For `failure_mode: D` (stage mismatch — cell name differs in P&R):** Grep correct PostEco stage for new cell name, update `cell_name` for that specific stage, re-verify old_net on correct pin.

**For `rerun_fenets` actions (condition inputs re-queried in Step 6f-FENETS):** Build resolution map from `condition_input_resolutions[]` where `resolved_gate_level_net` is set. For each gate entry where any input is `PENDING_FM_RESOLUTION:<signal>`, resolve in this order:

1. **Rerun fenets result** — if resolved with direct driver → use directly; if `needs_named_wire` → set `NEEDS_NAMED_WIRE`
2. **Priority 3 structural driver trace** (if rerun returned FM-036 or no rerun done): find driver cell of the Synthesize-resolved net → search for same driver in failing P&R stage → read its output net as the P&R alias
   ```bash
   grep -n "\.<output_pin>( <synth_resolved_net> )" /tmp/eco_study_<TAG>_Synthesize.v | head -3
   grep -n "\b<driver_cell_name>\b" /tmp/eco_study_<TAG>_<FailingStage>.v | head -3
   ```
3. **If Priority 3 also fails** → mark `UNRESOLVABLE:<signal>` (NOT `PENDING_FM_RESOLUTION`). Record: "Signal absent from <stage> PreEco after Priority 1/2/3 — P&R optimization eliminated driver chain."

**CRITICAL: Do NOT leave as `PENDING_FM_RESOLUTION` after a rerun returned FM-036.** After the first FM-036 rerun, escalate to Priority 3. If Priority 3 fails, mark `UNRESOLVABLE` and let eco_fm_analyzer decide (Mode G-P&R).

**For `failure_mode: ABORT_CELL_TYPE`:** For each `fix_cell_type` entry:

**CT-1 — Find correct cell type in PreEco Synthesize:**
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
  awk '/^module <scope_module>/{p=1} p && /\.<pin1>.*\.<pin2>.*\.<output_pin>/{print; exit} /^endmodule/{p=0}' | \
  grep -oE "^[[:space:]]*[A-Z][A-Z0-9]+" | head -3
```
**CT-2 — Update study JSON:** For all stages where `entry["instance_name"] == gate_instance`, set `cell_type` to the correct value and add `re_study_note`.

**For `failure_mode: H` (gate input inaccessible in P&R stage):** Two sub-cases: **H-BUS** (net driven only through submodule output port bus) and **H-RENAME** (P&R renamed the net).

For each `fix_named_wire` entry (`gate_instance`, `input_pin`, `source_net`, `stage`):

**H1 — Confirm the structural issue:**
```bash
par_count=$(zcat <REF_DIR>/data/PreEco/<stage>.v.gz | grep -cw "<source_net>")
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<source_net>")
# par_count=0, synth_count>0 → H-RENAME
# par_count=0, synth_count=0 → H-BUS
```

**H2 — Find the P&R alias:** For H-RENAME: find cell driving `source_net` in Synthesize PostEco → search same cell instance in P&R PostEco → read its output net. For H-BUS: `source_net` stays as-is; named wire approach handles the port bus.

**H3 — Update study JSON for the specific gate entry:**
```python
if entry.get("port_connections_per_stage") is None:
    entry["port_connections_per_stage"] = {
        stage: dict(entry.get("port_connections", {}))
        for stage in ["Synthesize", "PrePlace", "Route"]
    }
if par_alias_found:
    entry["port_connections_per_stage"][stage][input_pin] = par_alias
else:
    entry["port_connections_per_stage"][stage][input_pin] = f"NEEDS_NAMED_WIRE:{source_net}"
    entry["needs_named_wire"] = True
    entry["port_bus_source_net"] = source_net
entry["force_reapply"] = True
entry["re_study_note"] = f"Mode H fix on pin {input_pin}: {source_net} inaccessible in {stage}"
```

**H4 — Do NOT set `force_reapply` for Synthesize** unless that stage was also diagnosed with the same issue.

**GAP-19 — Original register preference in Mode H:** When searching for a P&R-renamed register output net:
- **Skip** cells with `_dup<N>` suffix (scan-chain duplicates)
- **For `_MB_` merged cells:** the LAST `_MB_<reg_name>` segment identifies the original register. Verify via FM `reg_map` or `matched_points`. Record selection rationale in `re_study_note`.

**H5 — Re-read `mode_H_risk` flags from eco_rtl_diff.json (MANDATORY at RE_STUDY start):** For each gate with `mode_H_risk: true` whose `port_connections_per_stage` has NOT been updated for listed stages → apply Priority 3 structural trace and update NOW:
```python
rtl_diff = load(f"data/{TAG}_eco_rtl_diff.json")
for change in rtl_diff.get("changes", []):
    for gate in change.get("d_input_gate_chain", []):
        if gate.get("mode_H_risk") and gate.get("missing_in_stages"):
            for stage in gate["missing_in_stages"]:
                entry = find_entry_by_instance(gate["instance_name"], eco_preeco_study)
                if entry and not already_updated(entry, stage, gate["inputs"]):
                    alias = priority3_structural_trace(gate["inputs"][0], stage)
                    entry["port_connections_per_stage"][stage][gate["pin"]] = alias or f"NEEDS_NAMED_WIRE:{gate['inputs'][0]}"
                    entry["force_reapply"] = True
```

**For `action: update_gate_function`:** GF-1 — Find correct real library cell in PreEco Synthesize (same port-structure search as eco_applier Step 2). GF-2 — Update `gate_function`, `cell_type`, and `re_study_note` for ALL stages.

**For `failure_mode: UNKNOWN`:** For each target_register: read the failing point path from `diagnosis`, trace full forward/backward cone from DFF in PostEco Synthesize, re-run FM result parsing for this net, update study entry.

### Re-study Step 4 — Save updated study JSON

Write back `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` with ONLY modified entries changed. Verify `wc -l` ≥ original line count.

Write `<BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` with per-change-type format (from ORCHESTRATOR.md Step 3). RPT must include: identifier per entry type, old→new for rewires, gate_function/output_net/cell_type for new_logic, direction for port_declaration, parent/port/net for port_connection, full reason for EXCLUDED entries, what changed for updated entries, and a SUMMARY of all `force_reapply` entries set.

```bash
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt <AI_ECO_FLOW_DIR>/
```
**Exit after writing and copying the RPT.**

---

## Phase 0 — Process new_logic and new_port Changes FIRST

**Before studying any FM-returned cells, process ALL entries in `changes[]` by type:**
- `"new_logic"` / `"and_term"` → process as gate/DFF insertion (steps 0a–0f)
- `"new_port"` → create `port_declaration` study entry (step 0g)
- `"port_connection"` → create `port_connection` study entry (step 0h)
- `"port_promotion"` → create `port_promotion` study entry (step 0i — flat netlist only)
- `"wire_swap"` → skip (handled by FM find_equivalent_nets in Phase 1)

**CRITICAL: For hierarchical PostEco netlists, `new_port` and `port_connection` changes require explicit port list updates and instance connection additions.**

**`port_promotion` — FLAT NETLIST ONLY:** Only when `grep -c "^module " Synthesize.v` = 1. Verify net exists; if hierarchical use `port_declaration` + `port_connection` instead.

**MODULE PORT DIRECT GATING — STEP 0 (ABSOLUTE MANDATORY — cannot be bypassed under any circumstances):**

This check runs FIRST. If it fires, RETURN IMMEDIATELY and HARD STOP all other strategy evaluation. There is no else branch, no fallthrough, no "also consider parent_scope". The RETURN is unconditional.

```python
# STEP 0 — ABSOLUTE GATE (runs before anything else, always)
# Check 1: RTL source declares old_token as output port
rtl_is_output = (
    grep_count(rf"^\s*output\b.*\b{re.escape(old_token)}\b", module_rtl_lines) >= 1
)
# Check 2: Gate-level PreEco declares old_token as output port in the module header
gatelvl_is_output = (
    grep_count(rf"output\b.*\b{re.escape(old_token)}\b", preeco_module_header_lines) >= 1
    or old_token in parse_port_list_names(preeco_module_header)
)
# Check 3: Gate-level PostEco has old_token as a net driven by a cell inside this module
# (confirms old_token is visible as a named net — not just an internal alias)
gatelvl_driven = (
    grep_count(rf"\.\w+\s*\(\s*{re.escape(old_token)}\s*\)", preeco_module_body_lines) >= 1
)

is_output_port = rtl_is_output or gatelvl_is_output

if is_output_port:
    # ══════════════════════════════════════════════════════════
    # MODULE PORT DIRECT GATING — the ONLY valid strategy
    # ══════════════════════════════════════════════════════════
    and_term_strategy = "module_port_direct_gating"

    # Mandatory steps — ALL must be completed:
    # 1. Find original driver cell of <old_token> in PreEco gate-level
    #    grep: zcat PreEco/Synthesize.v.gz | grep -n "\.<output_pin>(\s*<old_token>\s*)"
    # 2. Create rewire entry: rename driver cell output from <old_token> → eco_<jira>_<seq>_orig
    #    (implicit wire — do NOT add wire decl for this intermediate net)
    # 3. Create new_logic_gate entry: new AND/NAND/IND2 gate
    #    - instance_scope = <declaring_module_scope>
    #    - output_net = <old_token>   ← drives port directly (same name as port)
    #    - port_connections: all inputs including the new AND-term
    # 4. No individual consumer rewires — ALL consumers see gated value via port
    # 5. needs_explicit_wire_decl = False for <old_token> (it IS the port — already declared)
    # 6. needs_explicit_wire_decl = False for eco_<jira>_<seq>_orig (implicit from driver rename)

    # VERIFICATION: after building entries, confirm:
    assert output_net == old_token, f"FATAL: module_port_direct_gating must use output_net=old_token, got {output_net}"

    # MANDATORY — Identify expected cascade DFFs:
    # Any DFF in the declaring module whose D-input cone reaches <old_token> will
    # intentionally see a different value after this ECO. FM vs OLD SynRtl baseline
    # will correctly flag them as non-equivalent. They are NOT bugs — they are the
    # expected downstream effect of gating <old_token>. Record them so eco_fm_analyzer
    # can classify them as INTENTIONAL_CASCADE immediately (no rounds wasted).
    expected_cascade_dffs = []
    for dff_instance in grep_all_dffs_in_module(declaring_module_lines):
        d_input_cone = trace_D_input_cone(dff_instance, declaring_module_lines, max_hops=10)
        if old_token in d_input_cone or f"{old_token}_orig" in d_input_cone:
            expected_cascade_dffs.append(dff_instance)

    # Write to study JSON on the and_term change entry:
    and_term_change["expected_cascade_dffs"] = expected_cascade_dffs
    and_term_change["expected_cascade_net"] = old_token
    and_term_change["expected_cascade_reason"] = (
        f"{old_token} is now gated by the and_term ECO. All DFFs whose D-input "
        f"cone reaches {old_token} will have different values vs OLD SynRtl — "
        f"this is intentional. eco_fm_analyzer must classify these as INTENTIONAL_CASCADE."
    )
    log(f"EXPECTED_CASCADE: {len(expected_cascade_dffs)} DFFs identified downstream of {old_token}")

    # HARD RETURN — no further strategy evaluation
    return and_term_entries  # EXIT and_term processing

# ── Only reaches here when is_output_port is definitively False ──
# Verify: if failing_count >= 500 in FM and strategy chosen is NOT module_port_direct_gating,
# double-check is_output_port before proceeding — large cascade counts are a strong signal
# that STEP 0 should have fired.
```

**Only if `is_output_port == False`** → proceed to normal strategy selection (direct_rewire or parent_scope).

**SELF-CHECK (write this to RPT before proceeding):**
```
STEP 0 result: is_output_port=<True/False>
  rtl_is_output=<True/False>  gatelvl_is_output=<True/False>
  → strategy=<module_port_direct_gating | proceeding to selection>
```
If strategy is NOT `module_port_direct_gating` and FM later shows 500+ cascade failures from this module, the STEP 0 check was incorrectly False — re-examine.

**`and_term` gate input scope:** Gate inputs must use names as they appear INSIDE the declaring module. If the new term is a `new_port` → use the PORT NAME (`new_token` as declared). Do NOT use `flat_net_name`. Read `and_term_gate_input` from RTL diff JSON if present — it already stores the correct module-internal name.

**`and_term` strategies:**
- **Strategy A — DIRECT REWIRE:** cells found inside declaring module → `new_logic_gate` + `rewire` entries
- **Strategy B — PARENT SCOPE:** all FM cells excluded (parent/sibling scope) → create AND gate at PARENT scope, no rewire entries inside declaring module; set `and_term_strategy: "parent_scope"`

**CRITICAL — `and_term` scope validation for hierarchical PostEco:** Verify each FM-returned cell appears between `module <posteco_module_name>` and its `endmodule`. If not → mark excluded with reason "and_term: cell found in FM but exists outside module scope in hierarchical PostEco."

---

### 0a — Classify the new cell type

From RTL diff `context_line`:
- `always @(posedge <clk>)` with reset/data pattern → **DFF** (sequential)
- `wire/assign <signal> = <expr>` → **combinational gate**
- Bare `reg <signal>` with no always block → skip

### 0b — Identify input signals

Parse `context_line` to extract clock, reset, data expression (DFF) or input signals (combinational). Verify each: `grep -cw "<input_signal>" /tmp/eco_study_<TAG>_Synthesize.v`. If count = 0 → record `input_from_change: <N>`.

### 0b-GATE-STAGE-NETS — Per-Stage Input Net Resolution for Combinational Gates (MANDATORY)

For every input net of any `new_logic_gate` entry (including `and_term` gates), resolve for every stage (Synthesize, PrePlace, Route):

| Priority | Method |
|----------|--------|
| 0 | **RTL-named primary input port** — `grep -cw "input.*\b<net_name>\b" /tmp/eco_study_<TAG>_<Stage>.v` — if declared as `input <net_name>;` in the target module → use directly. **HIGHEST PRIORITY — takes precedence over all other methods.** Primary input ports are visible to FM in ALL stages by name. |
| 1 | Direct name match in stage PreEco — use directly |
| 2 | Trace driver cell in Synthesize → find same cell output in this stage |
| 3 | P&R alias search (partial name, exclude declarations) |
| 4 | Backward cone trace from target DFF `.D(<net>)` — walk driver chain up to 10 hops; check gate inputs by instance name across stages |
| — | Unresolved → `UNRESOLVED_IN_<Stage>:<net>` |

**GAP-NEW — Scan alias must NEVER be used when a primary input port exists:**
P&R stages introduce `test_so*` and `dftopt*` nets in the scan chain. These are scan outputs/test nets — they are NOT valid functional inputs even if they appear to be connected to the same DFF family as the RTL signal. Using a `test_so*` net as a gate input causes FM stage-to-stage mismatch (`FmEqvEcoRouteVsEcoPrePlace`) when the same gate uses a different alias in another stage.

**Before accepting any resolved net for a P&R stage, validate it is NOT a scan alias:**
```python
scan_alias_patterns = [r'^test_so\d+', r'^dftopt\d+', r'^scan_\w+', r'^si_\w+']
if any(re.match(p, resolved_net) for p in scan_alias_patterns):
    # REJECT — scan alias is not a valid functional input
    # Fall back to next priority or look for primary input port first
    log(f"REJECTED scan alias {resolved_net} — not a valid functional input")
    resolved_net = None  # force next priority
```

**Cross-stage consistency check (MANDATORY after resolving all stages):**
After resolving all 3 stages, verify the resolved nets across stages are functionally consistent:
```python
# If the same signal resolves to different names across stages, check if they share
# a common primary input port name that exists in ALL stages:
if resolved_nets["PrePlace"] != resolved_nets["Route"]:
    common_input = None
    for candidate in grep_all_input_ports(module_lines["PrePlace"]):
        if (grep_count(candidate, preplace_lines) >= 1 and
            grep_count(candidate, route_lines) >= 1):
            # candidate input port exists in both stages
            if is_same_rtl_signal(candidate, original_rtl_net, preeco_synrtl):
                common_input = candidate
                break
    if common_input:
        # Use the common primary input port for ALL stages where it exists
        for stage in ["PrePlace", "Route"]:
            resolved_nets[stage] = common_input
        log(f"CROSS_STAGE_NORMALIZE: {resolved_nets['PrePlace']} / {resolved_nets['Route']} → {common_input}")
```
This prevents the failure mode where PrePlace uses `test_so4927` and Route uses `IReset_m1` for the same functional signal — FM sees different D-input cones → NeedFreqAdj_reg non-equivalent.

> **GAP-13 — UNRESOLVABLE vs manual_only distinction:**
> - `UNRESOLVABLE`: signal and its entire driver cone are genuinely absent from the failing stage PreEco after all 4 priorities — P&R eliminated the cone. Use `1'b0` as a conservative placeholder if the gate still has valid other inputs.
> - `manual_only`: fix is structurally achievable but requires SVF — prohibited by RULE 27.
>
> Never use `manual_only` when `UNRESOLVABLE` is correct. Only declare `UNRESOLVABLE` after Priority 4 fails.

Record `port_connections_per_stage`. **Do NOT use Synthesize nets for all stages without verification.** If any input is `UNRESOLVED_IN_<Stage>:<net>` after all priorities: check if FM can resolve it → add to `condition_inputs_to_query`; if still unresolved → mark gate `"confirmed": false` for that stage.

### 0b-STAGE-NETS — Per-Stage Pin Verification for DFF (MANDATORY)

**Step A — Read full DFF port map from PreEco Synthesize.** Classify each pin: **Functional** (clock, data, Q) from RTL context; **Auxiliary** (scan input, scan enable, etc.) from a neighbour DFF.

**Step B — For each stage, resolve functional pin net names:**
- Priority 1 — `grep -cw "<net_name>" /tmp/eco_study_<TAG>_<Stage>.v` — if ≥ 1, use it
- Priority 2 — P&R alias (only if direct absent)
- Priority 3 — Structural driver trace (only if P1 and P2 both fail)

**Step C — For each stage, resolve auxiliary pin net names from a neighbour DFF** in same module scope (widen to parent if needed). Do NOT fall back to hardcoded constants without a neighbour.

**Step D — Write `port_connections_per_stage`** combining functional + auxiliary pins. Keep flat `port_connections` (Synthesize values) for backward compatibility.

### 0b-DFF — Process `d_input_gate_chain` (MANDATORY when present)

For each gate in `d_input_gate_chain` (d001→d00N), create a `new_logic_gate` entry:
1. Find cell type in PreEco Synthesize matching the gate_function
2. Resolve bit-select names (`A[i]` → check if netlist uses `A_i_` or `A[i]`)
3. Verify all inputs exist; if input is `n_eco_<jira>_d<prev>` → set `input_from_change: <prev_gate_id>`
4. If any signal not found → set `d_input_decompose_failed: true`, skip rest of chain

**CRITICAL — seq counter is per-JIRA across ALL DFF chains, not per-chain:**
If a design has multiple DFF insertions in different module scopes, the seq counter CONTINUES across all chains:
- Chain 1 (e.g., DFF in ModuleA): eco_<jira>_d001, d002, ... d007
- Chain 2 (e.g., DFF in ModuleB): eco_<jira>_d008, d009  ← continues from d007, never resets
Never restart from d001 for a second DFF chain. Each eco_<jira>_d<N> name must be globally unique across the entire ECO.

After all chain gates: set DFF entry `port_connections.D = "n_eco_<jira>_d<last>"`. If `d_input_decompose_failed: true`: set `d_input_net = "SKIPPED_DECOMPOSE_FAILED"`, `confirmed: false`.

**GAP-14 — Wire declaration flag:** For each new gate in the chain whose **output net** does not exist anywhere in the PreEco netlist (freshly coined name), set `needs_explicit_wire_decl: true`. eco_applier uses this to add `wire <net_name>;`.

**CRITICAL — output net ONLY:** `needs_explicit_wire_decl: true` MUST ONLY be set for the net driven by the gate's output pin (ZN, Z, or Q — the value of `port_connections[<output_pin>]`). NEVER set it for input nets. Input nets are existing nets driven by other cells; they are already present in the netlist and never need an explicit wire declaration. Setting `needs_explicit_wire_decl: true` for an input net causes eco_applier to insert `wire <input_net>;` before the gate, which creates an SVR-9 duplicate wire declaration when FM reads the netlist (the existing driver's instantiation already implicitly declares the net).

Do NOT set `needs_explicit_wire_decl: true` for:
- Gate inputs (any port other than ZN/Z/Q)
- Gate outputs that drive port connections (the port connection implicitly declares the wire)
- Renamed original driver output nets (already present as the old_net)
- Any net that already appears in the PreEco netlist via `grep -cw "<net_name>" <Stage_PreEco>` ≥ 1

### 0c — Find suitable cell type from PreEco netlist

**For DFF:** `zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -E "^[[:space:]]*(DFF|DFQD|SDFQD|SDFFQ|DFFR|DFFRQ)[A-Z0-9]* [a-z]" | head -5`

**For combinational gate:** Determine function from RTL expression (`A & B` → AND2, `~A | ~B` → NAND2, etc.), then search PreEco for matching cell pattern.

**MANDATORY — extract actual pin names from PreEco example (ALL pins, not just output):**
After finding `<cell_type>` in PreEco, read its full instantiation:
```bash
grep -m1 "<cell_type>" /tmp/eco_study_<TAG>_<Stage>.v
```
Parse every `.<PIN>(` from that instantiation → these are the ONLY valid pin names for this cell.
Use these exact names in `port_connections` — never assume input pin names from the gate function name.
Example: NOR3 cell may use `A1,A2,A3` NOT `A1,B1,B2`. AND2 may use `A1,A2` NOT `A,B`. Always read from PreEco.

**GAP-20 — SE/SI scan chain mismatch detection:** After resolving auxiliary pins for all 3 stages, compare the SE net in PrePlace vs Route. If both differ AND both match P&R-generated alias patterns (not in RTL source — verify `grep -rw "<se_net>" data/PreEco/SynRtl/` → count = 0):
- Set `needs_se_tune: true` in the DFF study JSON entry
- Do not attempt to unify SE across stages
- eco_fm_analyzer reads `needs_se_tune: true` to classify SE cone mismatch as `SCAN_CHAIN_MISMATCH` and auto-generate tune file entries

---

### CELL OUTPUT PIN TABLE — MANDATORY REFERENCE

**Always use this table to set the output pin name in `port_connections`. Wrong pin causes FM FE-LINK-7 → ABORT_LINK on ALL stage comparisons.**

| Gate Function | Output Pin | Notes |
|--------------|-----------|-------|
| AND2, AND3, AND4 | `Z` | Non-inverting |
| OR2, OR3, OR4 | `Z` | Non-inverting |
| **MUX2, MUX4** | **`Z`** | **NOT `ZN` — MUX output is non-inverting** |
| XOR2 | `Z` | Non-inverting |
| INV | `ZN` | Inverting |
| NAND2, NAND3, NAND4 | `ZN` | Inverting |
| NOR2, NOR3, NOR4 | `ZN` | Inverting |
| XNOR2 | `ZN` | Inverting |
| IND2, IND3 | `ZN` | AND-NOT (inverting AND) |
| DFF, SDFF | `Q` | Sequential |

**Verification:** After selecting cell type, confirm output pin by examining an actual instance:
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep "<cell_type>" | head -1
# Read the .PIN(net) at the END of the port list — that is the output pin
```
PreEco netlist instance is authoritative over this table.

---

### 0c — Handle `d_input_decompose_failed` with `fallback_strategy: intermediate_net_insertion`

**Strategy A — CHAIN MODIFICATION (preferred):** When new conditions can be expressed by modifying inputs of an existing intermediate cell between the DFF D-input and source logic:
1. Trace backward from target_register.D to find the intermediate cell
2. Insert ECO cells producing the new condition expression
3. Create rewire entry: change intermediate cell's input from `<old_net>` to `<eco_output_net>`
4. Record: `intermediate_net_strategy: "chain_modification"`

**Strategy B — MUX CASCADE (fallback):** When new conditions have no connection to any existing intermediate cell. Decision: Try Strategy A first; if no modifiable cell found → use Strategy B.

**Step 0c-1 — Find the pivot net (Strategy B):** Trace backward from `target_register.D` (up to 5 hops). Stop at first net whose driver has fanout ≥ 2 (`grep -c "( <net> )" /tmp/eco_study_<TAG>_Synthesize.v` ≥ 2). Record as `<pivot_net>`.

**Step 0c-2 — Verify pivot net per stage:**

| Priority | Method |
|----------|--------|
| 1 | `grep -cw "<pivot_net>" /tmp/eco_study_<TAG>_<Stage>.v` — use if ≥ 1 |
| 2 | Grep `<driver_cell_name>` in P&R stage → read its output pin |
| Fallback | Use Synthesize pivot net + mark `source: "synthesize_fallback"` |

**NEVER mark MANUAL_ONLY just because pivot net name changed in a P&R stage.** Instance names are preserved; always try driver cell lookup first.

**Step 0c-3b — MANDATORY: Validate pivot driver cell polarity:**
```bash
grep -n "\.<output_pin>( <pivot_net> )" /tmp/eco_study_<TAG>_Synthesize.v | head -3
```
- **INVERTING** (NOR, NAND, INV, NR, ND, IN prefixes): `1'b0`/`1'b1` constants in c_mux gates must be determined by working backward from desired RTL output through the inverting driver.
- **NON-INVERTING** (AND, OR, BUF, AN, OR prefixes): c_mux constants directly reflect RTL `? 1'b0 : 1'b1` values.

```python
entry["pivot_driver_cell_type"] = driver_cell_type
entry["pivot_is_inverting"] = is_inverting(driver_cell_type)
```
If pivot is INVERTING: flip constants relative to RTL values. Verify: condition fires → c_mux output → pivot driver output → DFF.D matches RTL intent.

**Step 0c-4 — Build entries:**
- **Entry A (rewire):** Redirect driver output from `<pivot_net>` → `<pivot_net>_orig`
- **Entry B (new_logic_gate chain):** Read `new_condition_gate_chain` from `eco_rtl_diff.json`. If null → mark MANUAL_ONLY. Otherwise create per-stage verified `new_logic_gate` entries. Last gate outputs to `<pivot_net>_orig`.
- **Validate cascade polarity before recording.** If polarity inconsistent → swap `1'b0`/`1'b1` constants.

**Step B-P3 — Structural Driver Trace (Priority 3 fallback for P&R-renamed nets):**
```python
# Find driver cell of synth_resolved_net in Synthesize
for line in synth_stage_lines:
    if f".ZN( {synth_resolved_net} )" in line or f".Z( {synth_resolved_net} )" in line:
        driver_cell = extract_cell_instance_name(line); break
# Search same driver cell instance in P&R stage
if driver_cell:
    for line in par_stage_lines:
        if re.search(rf'\b{re.escape(driver_cell)}\b', line):
            output_net = extract_output_pin_net(line)
            if output_net and output_net != synth_resolved_net:
                par_alias = output_net; break
```
**Why this works:** P&R renames internal nets but keeps the same cell instance names. If driver cell is also renamed → search by cell type + known input net: `grep -n "\.<input_pin>( <known_input_net> )" /tmp/eco_study_<TAG>_<Stage>.v | head -3`

If not found after Priority 3 → mark `UNRESOLVABLE:<signal>`. Use `1'b0` as conservative constant only if the gate has at least one valid input and the unresolvable input controls a non-critical condition.

---

**Resolving `PENDING_FM_RESOLUTION` inputs before creating study entries:**
1. FM fenets result (from SPEC_SOURCES or rerun JSON)
2. Priority 3 structural driver trace
3. Still unresolved → mark `UNRESOLVABLE` and document

Then apply `needs_named_wire()` on the resolved net.

**`needs_named_wire(net_name, stage_lines)` function — MANDATORY, keep full logic:**
```python
def needs_named_wire(net_name, stage_lines):
    """
    Returns True if this net's only driver is a hierarchical submodule output port bus.
    True when ALL: (1) no direct cell driver, (2) IS connected in a module output port bus.
    """
    import re
    direct_driver = any(
        re.search(rf'\.\w+\(\s*{re.escape(net_name)}\s*\)', line)
        and '{' not in line
        and not line.strip().startswith('//')
        for line in stage_lines
    )
    if direct_driver:
        return False
    in_port_bus = any(
        re.search(rf'\.\w+\s*\(\s*\{{[^}}]*\b{re.escape(net_name)}\b[^}}]*\}}\s*\)', line)
        or re.search(rf'\.\w+\s*\(\s*{re.escape(net_name)}\s*\)', line)
        for line in stage_lines
        if not line.strip().startswith('//')
    )
    return in_port_bus
```

**GAP-18 — Submodule bus output check (run before declaring any net undriven):**
```python
re.search(rf'\.\s*\w+\s*\(\s*\{{[^}}]*\b{re.escape(signal)}\b', module_line)
```
If found → set `driven_by_submodule: true`, `driver_type: "submodule_bus_output"`, `confirmed: true`, `needs_named_wire: true`. The signal IS driven; the fix is to rename the `UNCONNECTED_<N>` placeholder to a meaningful eco name.

**CRITICAL:** Nets driven only through hierarchical submodule output port buses must never be used directly as gate inputs in P&R stages. Declare a named wire, connect it in the port bus, use the named wire as gate input. eco_applier handles this when `needs_named_wire: true`.

**UNCONNECTED BUS BIT — MANDATORY PORT_CONNECTION FIX:**

When a DFF D-input or gate input uses an `UNCONNECTED_<N>` net (a tie-off placeholder in a child module's bus output), the studier MUST add a `port_connection` change to rename that bus slot to an explicit named wire. Without this, the DFF D-input is tied to constant-0 across all stages → FM non-equivalence.

```python
# Detect UNCONNECTED_* pattern in D-input or gate input net name
if re.match(r'^UNCONNECTED_\d+$', resolved_net) or re.match(r'^SYNOPSYS_UNCONNECTED_\d+$', resolved_net):
    # 1. Identify the child module instance that drives this bus bit
    #    grep -n ".<output_port_bus>.*{.*<resolved_net>" PreEco/Synthesize.v → find instance + port + bit index
    # 2. Create a named wire for this bit:
    eco_wire_name = f"eco_{JIRA}_{<signal_alias>}"  # e.g. eco_<jira>_<reg_name>_bit<N>

    # 3. Add port_connection change to rename the UNCONNECTED_<N> slot in the child instance:
    add_study_entry({
        "change_type": "port_connection",
        "parent_module": <parent_module_name>,
        "instance_name": <child_instance_name>,  # the instance whose bus has UNCONNECTED_<N>
        "port_name": <bus_port_name>,            # e.g. the output bus port name
        "net_name": eco_wire_name,               # replace UNCONNECTED_<N> with named wire
        "bus_bit_index": <N>,                    # which bit in the bus
        "force_reapply": True,
        "reason": f"UNCONNECTED_{N} renamed to {eco_wire_name} for D-input traceability across stages"
    })

    # 4. Use eco_wire_name as the DFF/gate input (not UNCONNECTED_<N>)
    port_connections[input_pin] = eco_wire_name
    needs_explicit_wire_decl = True  # eco_wire_name is genuinely new — no existing driver in netlist

    # 5. Verify eco_wire_name does NOT already exist:
    assert grep_count(eco_wire_name, stage_lines) == 0, f"{eco_wire_name} already exists — pick different alias"
```

This pattern is required any time a DFF's D-input chain must trace through a submodule output bus where the relevant bit was unconnected pre-ECO. Both the `port_connection` rename AND the `needs_explicit_wire_decl=True` for the new named wire must be set together.

Apply `needs_named_wire()` to any net found by any means.

**Step 0c-5 — Per-stage net verification for each new condition signal:**

**Check A — Is the signal a `new_port` from this ECO?**
```python
new_ports = [c["new_token"] for c in rtl_diff["changes"] if c["change_type"] in ("new_port", "port_declaration")]
if signal_name in new_ports:
    entry["input_from_change"] = "<port_declaration_change_index>"
    entry["new_port_dependency"] = True
    continue  # do NOT flag as SKIPPED
```

**Check B — If not a new_port:** apply Priority 1/2 per stage. Still unresolved → record SKIPPED with reason. After any Priority 1/2 lookup, apply `needs_named_wire` check and set `NEEDS_NAMED_WIRE:<found_net>` if triggered.

**Step 0c-6 — Record** with `source: "intermediate_net_fallback"`.

### 0d — Assign instance and output net names

**For `new_logic_dff`:**
```
instance_name = <target_register>_reg
output_net    = <target_register>
```
This matches the instance name FM synthesizes from RTL — enabling auto-matching in `FmEqvEcoSynthesizeVsSynRtl` without `set_user_match`. Same name in all 3 stages.

**For `new_logic_gate` (including D-input chain and MUX cascade gates):**
```
instance_name = eco_<jira>_<seq>   (e.g., eco_<jira>_001, eco_<jira>_d001)
output_net    = n_eco_<jira>_<seq>
```
Same seq across all 3 stages.

### 0e — Record as new_logic_insertion entry in study JSON

**instance_scope is MANDATORY for every gate entry — never leave it blank:**
- Gates in a sub-module instance (e.g., ARB/CTRLSW): `instance_scope = "ARB/CTRLSW"`
- Gates in the tile-root module (e.g., the top tile module itself, not a sub-instance): `instance_scope = "<tile_module_name>"` — use the gate-level module name (e.g., `"umccmd"` or whatever the top tile module is named in the netlist). Check: `grep -m1 "^module ddrss_<tile>_t_<tile>" PreEco/Synthesize.v.gz` to get the exact module name.
- NEVER leave instance_scope = "" or None. eco_applier cannot find the insertion point without it.

**`instance_scope` rules — MANDATORY:**
- Submodule declaring module: `instance_scope = "<INST_A>/<INST_B>"`
- Tile root declaring module: `instance_scope = ""` (empty string) AND `"scope_is_tile_root": true`
- NEVER leave `instance_scope` as null — use `""` explicitly for tile-root scope

**`mode_H_risk` propagation (MANDATORY — before running `needs_named_wire()`):**
```python
for gate in rtl_change.get("d_input_gate_chain", []):
    if gate.get("mode_H_risk"):
        for stage in gate.get("missing_in_stages", []):
            entry_per_stage[stage]["needs_named_wire"] = True
            entry_per_stage[stage]["port_bus_source_net"] = gate["inputs"][0]
        # Skip needs_named_wire() structural check for these stages — already known
```

### 0e-PORT — Port Boundary Analysis (MANDATORY after every new cell insertion)

After building any `new_logic_dff` or `new_logic_gate` entry, check whether the cell's output net escapes the module scope. If it does, a `port_declaration` entry is REQUIRED — without it FM gets FE-LINK-7 (port not found on instance).

```python
output_net = entry["output_net"]  # Q net for DFF, ZN/Z net for gate
declaring_module = entry["module_name"]

# Check if parent module instance connects this net
# grep the parent module's PostEco scope for: .<any_port>(<output_net>)
# where the instance is the child containing this gate
parent_module = find_parent_module(declaring_module, preeco_hierarchy)
parent_connections = grep_parent_for_output_net(output_net, parent_module, preeco_lines)

if parent_connections:
    # output_net is used in parent scope → must be a port of declaring_module
    port_already_declared = any(
        e["change_type"] == "port_declaration"
        and e["signal_name"] == output_net
        and e["module_name"] == declaring_module
        for e in study_entries
    )
    if not port_already_declared:
        add_study_entry({
            "change_type": "port_declaration",
            "signal_name": output_net,
            "module_name": declaring_module,
            "declaration_type": "output",
            "instance_scope": entry["instance_scope"],
            "confirmed": True,
            "force_reapply": True,
            "reason": f"Cell output {output_net} used in parent module {parent_module} — must be declared as output port of {declaring_module}"
        })
        log(f"PORT_BOUNDARY: auto-added output port_declaration for {output_net} in {declaring_module}")
```

### 0e-CASCADE — Consumer Cascade Tracing (MANDATORY after every driver output rename)

When an existing cell's output is renamed (e.g. for `module_port_direct_gating` or `and_term` Strategy A), ALL consumers of the original output net in the same module must be identified and added as explicit `rewire` entries. Failing to do this leaves DFF inputs undriven.

```python
# For every rewire entry that renames a driver output (old_net → old_net_orig or similar):
for rewire_entry in study_entries_with_driver_rename:
    renamed_from = rewire_entry["old_net"]   # original net name (now abandoned)
    renamed_to   = rewire_entry["new_net"]   # new intermediate name

    # Find ALL cells in the same module that reference renamed_from as an INPUT
    consumers = grep_all_consumers(renamed_from, declaring_module_lines)
    # consumers = [(cell_name, pin_name), ...] for all .pin(<renamed_from>) references

    for (cell_name, pin_name) in consumers:
        # Check if a rewire for this cell+pin is already in study_entries
        already_covered = any(
            e["change_type"] == "rewire"
            and e.get("cell_name") == cell_name
            and e.get("pin") == pin_name
            for e in study_entries
        )
        if not already_covered:
            # Determine what this consumer should be rewired to
            # If the new gate drives old_token directly (module_port_direct_gating):
            #   → rewire consumer to old_token (the port name = new gate output)
            # If using intermediate net strategy:
            #   → rewire consumer to the new ECO gate output net
            new_target_net = determine_consumer_target(cell_name, pin_name, rewire_entry, study_entries)
            add_study_entry({
                "change_type": "rewire",
                "cell_name": cell_name,
                "pin": pin_name,
                "old_net": renamed_from,
                "new_net": new_target_net,
                "instance_scope": rewire_entry["instance_scope"],
                "module_name": rewire_entry["module_name"],
                "confirmed": True,
                "force_reapply": True,
                "reason": f"Consumer of renamed driver {renamed_from} → must be updated to {new_target_net}. Auto-added by consumer cascade trace."
            })
            log(f"CASCADE_TRACE: auto-added rewire for consumer {cell_name}.{pin_name} ({renamed_from} → {new_target_net})")
```

**This prevents the pattern seen in multiple rounds where a driver output is renamed but downstream DFF inputs are left pointing to the old (now undriven) net, causing FM DFF0X failures.**

### 0f — Mark wire_swap entries that depend on new_logic outputs

For each `wire_swap` whose `new_token` matches a `new_logic` output net, add `"new_logic_dependency": [<seq>]`.

**MUX select polarity validation:** If `mux_select_gate_function` is set in the RTL diff JSON, verify `mux_select_i0_net` and `mux_select_i1_net` are both resolved (not null) before encoding into the study JSON. If FM results (from Step 2 spec) show the MUX cell but the FM-resolved net does not match either `mux_select_i0_net` or `mux_select_i1_net` → flag `mux_polarity_conflict: true` and add to `revised_changes` in the re-study so eco_fm_analyzer can prescribe a gate re-check.

**Phase 0 / Phase 1 interaction guarantees:**
- **Instance naming:** Phase 0 (new_logic) and Phase 1 (wire_swap) both use the shared seq_counter — no name collision possible.
- **Wire declarations:** Before adding any `wire_decls` entry, check if that net already appears in `wire_decls` from Phase 0; skip duplicates.
- **Port list stability:** eco_applier processes Phase 0 `port_declaration` entries in Pass 2 (before Pass 4 rewires). Phase 1 entries may reference those ports safely.

For wire_swap changes requiring a new MUX select gate: read `mux_select_gate_function` from RTL diff JSON. If non-null → create `new_logic_gate` entry directly (skip Step 4c-POLARITY). If null → do NOT create entry in Phase 0; let Step 4c-POLARITY determine the gate function. **Do NOT derive gate function from RTL condition text** — only equal to condition expression when true-branch maps to I1.

### 0g — Process `new_port` changes → `port_declaration` study entries

**CRITICAL — Determine `declaration_type` first:**
- `context_line` has `input`/`output` → `declaration_type: "input"` or `"output"` — eco_applier adds to port list AND adds direction declaration
- `context_line` has only `wire` → `declaration_type: "wire"` — eco_applier does NOT add explicit `wire <signal_name>;`; port_connection entries implicitly declare it

**IMPLICIT WIRE CHECK (MANDATORY for `declaration_type: "wire"`):**
```python
port_conn_refs = [c for c in rtl_diff["changes"]
                  if c["change_type"] == "port_connection"
                  and c["module_name"] == module_name
                  and c["new_token"] == signal_name]
if len(port_conn_refs) >= 2:
    # Wire declared implicitly by port connections — explicit 'wire X;' causes FM-599 ABORT_NETLIST
    for c in port_conn_refs: c["no_wire_decl_needed"] = True
    skip_port_declaration_entry()
    # Note in RPT: "IMPLICIT WIRE: <signal_name> — wire declaration skipped"
```
Also check `implicit_wire` or `no_wire_decl_needed` flag from RTL diff JSON.

Steps:
1. Identify `module_name`, `signal_name` (`new_token`), `declaration_type`, `flat_net_name`, `instance_scope`
2. Detect netlist type once (reuse): `grep -c "^module " /tmp/eco_study_<TAG>_Synthesize.v` — count > 1 = hierarchical
3. Run implicit wire check BEFORE proceeding. If detected → skip to next change
4. If hierarchical: validate module name — `grep -c "^module <module_name>\b" /tmp/eco_study_<TAG>_Synthesize.v`. If 0 → try `<module_name>_0`. Still not found → `confirmed: false`. Never write `module_name?`.

### 0h — Process `port_connection` changes → `port_connection` study entries

1. Identify `parent_module`, `instance_name`, `port_name`, `net_name`, `submodule_type`
2. Re-use netlist type from 0g
3. **MANDATORY — Validate `submodule_pattern`:** `grep -c "<submodule_type> <instance_name>" /tmp/eco_study_<TAG>_Synthesize.v`. If 0 → check PrePlace and Route; record per-stage `instance_confirmed` flags. eco_applier skips stages where `instance_confirmed: false`. **NEVER record an unverified `submodule_pattern`.**

### 0i — Process `port_promotion` changes → `port_promotion` study entries

Verify net exists: `grep -cw "<signal_name>" /tmp/eco_study_<TAG>_Synthesize.v`. Record with `declaration_type: "output"`, `flat_net_confirmed: true`.

---

## Process Per Stage (Synthesize, PrePlace, Route)

**Multi-instance handling:** When `instances` is non-null, process each instance's FM results INDEPENDENTLY with separate confirmed cells, backward cone trace, and `new_logic_gate` entry.

**IMPORTANT — Fallback for missing FM results:** If no qualifying cells for a stage, apply Stage Fallback. Every stage must be studied.

### 1. Read the PreEco netlist (once per stage)
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz > /tmp/eco_study_<TAG>_<Stage>.v
grep -n "<cell_name>" /tmp/eco_study_<TAG>_<Stage>.v | head -20
```

### 2–3. Find and extract cell instantiation block

Read from the line with the cell name through the closing `);`. Extract all `.portname(netname)` entries.

### 4. Confirm old_net is present

**Step 1 — Try direct old_net name:** `grep -c "\.<pin>(<old_token>)" /tmp/eco_study_<TAG>_<Stage>.v`. If ≥ 1 → `"old_net": "<old_token>"`, `"confirmed": true`.

**Step 2 — If not found, check for HFS alias on that pin.** Read actual net on `<pin>`, verify alias via parent module port connection. If confirmed: set `"old_net": "<P&R_alias>"`, `"old_net_alias": true`, `"old_net_alias_reason"`. Do NOT drop a cell because direct old_net is not on the pin — always check HFS alias first.

If neither found: `"confirmed": false`.

### UNIVERSAL REAL-NET PREFERENCE RULE

> **Applies to ALL net selections in ALL port_connections_per_stage entries — rewires, gate inputs, port connections, DFF D-input chains. Without exception.**

**Always prefer the real RTL-named net over any P&R-generated alias, for every stage.**
- **Real net** = signal name from RTL diff (`old_token` or `new_token`). Exists in RTL source (`data/PreEco/SynRtl/*.v`). Stable across P&R runs.
- **P&R alias** = net created by P&R tools. Not in RTL source. Detect: `grep -rw "<net_name>" <REF_DIR>/data/PreEco/SynRtl/` → count = 0.

**For every net in port_connections_per_stage[stage]:**
1. `grep -cw "<real_net>" /tmp/eco_study_<TAG>_<Stage>.v`
2. If ≥ 1 → use the real net; record `"net_source": "real_rtl_name"`
3. If = 0 → fall back to P&R alias (Priority 2); record `"net_source": "hfs_alias"` and `"net_alias": "<alias>"`

**Exception:** If the real net is itself P&R-renamed (confirmed by FM-036 or Priority 3 structural trace), use the P&R alias from structural trace.

---

### 4b. Verify new_net is reachable (Priority 1/2)

**Priority 1 — Direct signal name (ALWAYS try first):** `grep -cw "<new_token>" /tmp/eco_study_<TAG>_<Stage>.v`. If ≥ 1 → `"new_net": "<new_token>"`. STOP. Do NOT search for alias.

**Priority 2 — HFS alias (ONLY if direct absent):** Search for net root excluding wire/input/output/reg declarations. If found: set `"new_net_alias": "<P&R_alias>"`, `"new_net_reachable": true`. If not: `"new_net_reachable": false`, `"confirmed": false`.

### Cone Verification (MANDATORY for wire_swap)

#### Backward Cone (max 8 hops — covers typical 4-6 hop driver chains with margin)

**Step 1 — Find target register D-input net.** Gate-level instance for `target_register` bit `[N]` may appear as `<target_register>_reg_<N>_`. If `target_bit` is null (scalar), search without bit suffix. In the matching block, locate `.D(<net>)` → `<target_d_net>`.

**Step 2 — Trace backward (max 8 hops):** Find driver of `<target_d_net>` (pin ZN/Z/Q/CO/S), read its input nets, repeat until `old_net` appears (FOUND) or reach a primary input/clock net (NOT FOUND).

**Step 3 — Decision:** In cone → `"in_backward_cone": true`. Not in cone → `"confirmed": false`, `"in_backward_cone": false`.

#### Forward Trace Verification (MANDATORY for cells marked in_backward_cone: false, max 6 hops — covers typical 2-4 hop fanout chains; if limit exceeded, log WARNING and assume valid rather than marking VERIFY_FAILED)

**Step 1 — Find cell's output net** (pin Z/ZN/Q) → `<cell_output_net>`.

**Step 2 — Trace forward (max 6 hops):** `grep -n "( <cell_output_net> )" /tmp/eco_study_<TAG>_<Stage>.v | grep -v "\.ZN\|\.Z\b\|\.Q\b" | head -5`. Repeat until `<target_d_net>` reached (UPGRADED) or terminates at unrelated logic.

**Step 3 — Update JSON:**
- UPGRADED: `"in_backward_cone": true`, `"confirmed": true`, `"forward_trace_verified": true`, `"forward_trace_result": "UPGRADED — output reaches <target_register><target_bit> via <hop_chain>"`
- CONFIRMED EXCLUDED: `"confirmed": false`, `"forward_trace_result": "CONFIRMED EXCLUDED — output feeds <actual_destination>"`

### 4c-POLARITY — MUX Select Pin Polarity Check (FALLBACK when `mux_select_gate_function` is null)

**Run ONLY when `mux_select_gate_function` in RTL diff JSON is null.**

**Step 1 — Read MUX port block from PreEco Synthesize:** Record I0_net, I1_net, output net, current select net.

**Step 2 — Parse RTL expression** from `context_line`: `<register> <= (<condition>) ? <branch_true> : <branch_false>`

**Step 3 — Match RTL branches to MUX inputs:** Determine which input carries `branch_true`.

**Step 4 — Compute gate function for new select:**
- true-branch maps to **I1**: S = condition → implement directly
- true-branch maps to **I0**: S = NOT(condition) → implement logical complement

| Boolean for S | Gate |
|--------------|------|
| `E & A` | AND2 |
| `~(E & A)` | NAND2 |
| `E \| A` | OR2 |
| `~(E \| A)` | NOR2 |
| `~E` | INV |
| More inputs | AND3, NAND3, OR3, NOR3, etc. |

**CRITICAL:** Never read gate function from RTL condition text alone — complete Step 3 before Step 4.

**Step 5 — Create or override the `new_logic_gate` entry** with the correct gate function. Record `mux_select_polarity` fields (i0_net, i1_net, branch_true_maps_to, s_expression, gate_function_for_new_select, reasoning).

---

### 4d. Structural Analysis — Timing & LOL Estimation (Synthesize only)

Compare driver structure of `old_net` vs `new_net` in PreEco Synthesize. Find driver (cell on Z/ZN/Q). Compare fanout. Record:
```json
"timing_lol_analysis": {
  "old_net_driver": "<cell> (<type>) pin=<Z/ZN/Q>",
  "new_net_driver": "<cell> (<type>) pin=<Z/ZN/Q>",
  "old_net_fanout": <N>, "new_net_fanout": <N>,
  "timing_estimate": "<BETTER|LIKELY_BETTER|NEUTRAL|RISK|LOAD_RISK|UNCERTAIN>",
  "reasoning": "<1-sentence explanation>"
}
```
FF.Q driver → BETTER; shallower comb → LIKELY_BETTER; same depth → NEUTRAL; deeper cone → RISK; higher fanout → LOAD_RISK; unclear → UNCERTAIN.

### 5. Verify output count before moving to next stage

```
Qualifying list had: N cells
Output JSON has:     N entries  ← must match
```

### 6. Clean up temp file (after processing all cells for that stage)
```bash
rm -f /tmp/eco_study_<TAG>_<Stage>.v
```

---

## GAP-5 — Stage Fallback — For Any Stage with No FM Result

**Step F1 — Find best reference stage** (priority: Synthesize → PrePlace → Route). Take all `"confirmed": true` entries.

**Step F2 — Grep each cell in missing stage:**
```bash
zcat <REF_DIR>/data/PreEco/<MissingStage>.v.gz > /tmp/eco_study_<TAG>_<MissingStage>.v
grep -n "<cell_name>" /tmp/eco_study_<TAG>_<MissingStage>.v | head -20
```

**Step F3 — Verify old_net on expected pin:** `grep -c "\.<pin>(<old_net>)" /tmp/eco_study_<TAG>_<MissingStage>.v`
- count = 1 → `"confirmed": true`, `"source": "<ref>_fallback"`
- count = 0 → check for P&R-renamed net; read actual net on pin; record with `"net_name_differs": true`
- count > 1 → `"confirmed": false`, `"reason": "AMBIGUOUS"`

**GAP-5 Priority 4 — Secondary structural trace for Stage Fallback (rewire entries only):**
When rewire cell cannot be confirmed in P&R stage (count = 0 after Priorities 1–3):
1. Find Synthesize driver cell of confirmed `<old_net>`: `grep -n "\.ZN\|\.Z\|\.Q" <PreEco/Synth> | grep "<old_net>"`
2. Read driver's cell instance name → `<driver_cell>`
3. Grep `<driver_cell>` in missing stage's PreEco netlist
4. If found → read output pin net → use as `<old_net>` for that stage with `net_name_differs: true`

Only after Priority 4 fails → use Synthesize result, mark `source: "stage_fallback"`.

**Step F4 — Cleanup** and repeat F1–F4 for every missing stage independently. Never leave any stage array empty if any other stage has confirmed cells.

---

## Output JSON

Write `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`. Each stage is an array — one entry per qualifying cell.

**`change_type` translation:** `wire_swap` → `rewire`; `new_logic` → `new_logic_dff` or `new_logic_gate` based on cell type.

**MANDATORY: Sort each stage array by processing order before writing:**
```python
PASS_ORDER = {
    "new_logic": 1, "new_logic_dff": 1, "new_logic_gate": 1,
    "port_declaration": 2, "port_promotion": 2,
    "port_connection": 3,
    "rewire": 4,
}
for stage in ["Synthesize", "PrePlace", "Route"]:
    study[stage].sort(key=lambda e: PASS_ORDER.get(e.get("change_type", "rewire"), 4))
```

---

## Representative JSON Examples

### new_logic_dff with port_connections_per_stage:
```json
{
  "change_type": "new_logic_dff",
  "target_register": "<signal_name>",
  "instance_scope": "<INST_A>/<INST_B>",
  "scope_is_tile_root": false,
  "cell_type": "<DFF_cell_type>",
  "instance_name": "<target_register>_reg",
  "output_net": "<target_register>",
  "port_connections": {"<clk_pin>": "<clk_net_synthesize>", "<data_pin>": "<data_net_synthesize>", "<q_pin>": "<target_register>", "<aux_pin1>": "<aux_net_synthesize>"},
  "port_connections_per_stage": {
    "Synthesize": {"<clk_pin>": "<clk_net_synthesize>", "<data_pin>": "<data_net_synthesize>", "<q_pin>": "<target_register>", "<aux_pin1>": "<aux_net_synthesize>"},
    "PrePlace":   {"<clk_pin>": "<clk_net_preplace>",   "<data_pin>": "<data_net_preplace>",   "<q_pin>": "<target_register>", "<aux_pin1>": "<neighbour_aux_preplace>"},
    "Route":      {"<clk_pin>": "<clk_net_route>",      "<data_pin>": "<data_net_route>",      "<q_pin>": "<target_register>", "<aux_pin1>": "<neighbour_aux_route>"}
  },
  "confirmed": true
}
```

### new_logic_gate:
```json
{
  "change_type": "new_logic_gate",
  "target_register": "<signal_name>_d001",
  "instance_scope": "<INST_A>/<INST_B>",
  "scope_is_tile_root": false,
  "cell_type": "<AND2_cell_type>",
  "instance_name": "eco_<jira>_d001",
  "output_net": "n_eco_<jira>_d001",
  "gate_function": "AND2",
  "port_connections": {"<A1>": "<in1>", "<A2>": "<in2>", "<Z>": "n_eco_<jira>_d001"},
  "port_connections_per_stage": {
    "Synthesize": {"<A1>": "<in1_syn>", "<A2>": "<in2_syn>", "<Z>": "n_eco_<jira>_d001"},
    "PrePlace":   {"<A1>": "<in1_pp>",  "<A2>": "<in2_pp>",  "<Z>": "n_eco_<jira>_d001"},
    "Route":      {"<A1>": "<in1_rt>",  "<A2>": "<in2_rt>",  "<Z>": "n_eco_<jira>_d001"}
  },
  "confirmed": true
}
```

### rewire with backward cone fields:
```json
{
  "change_type": "rewire",
  "cell_name": "<cell_name>",
  "cell_type": "<cell_type>",
  "pin": "<pin>",
  "old_net": "<old_signal>",
  "new_net": "<new_signal>",
  "full_port_connections": {"<port_A>": "<net_A>", "<port_B>": "<old_signal>", "<port_Z>": "<output_net>"},
  "confirmed": true,
  "in_backward_cone": true,
  "forward_trace_verified": true,
  "forward_trace_result": "CONFIRMED EXCLUDED — output feeds <actual_destination>",
  "new_net_reachable": true,
  "new_net_alias": null,
  "source": "synthesize_fallback",
  "fm_result_available": false
}
```

### port_declaration:
```json
{
  "change_type": "port_declaration",
  "module_name": "<module>",
  "signal_name": "<signal>",
  "declaration_type": "input|output|wire",
  "flat_net_name": "<net>",
  "instance_scope": "<path>",
  "netlist_type": "hierarchical",
  "confirmed": true
}
```

### port_connection:
```json
{
  "change_type": "port_connection",
  "parent_module": "<module>",
  "submodule_pattern": "<verified_submodule_type>",
  "instance_name": "<INST>",
  "port_name": "<port>",
  "net_name": "<net>",
  "netlist_type": "hierarchical",
  "confirmed": true,
  "instance_confirmed": {"Synthesize": true, "PrePlace": true, "Route": true}
}
```

---

**Confirmed-false notes:**
- Cell not found in PreEco: `"confirmed": false, "reason": "cell not found in PreEco netlist"`
- Old net not on expected pin: `"confirmed": false, "reason": "pin <pin> has net <actual_net> not expected <old_net>"`
- Multiple instances: `"confirmed": false, "reason": "AMBIGUOUS — multiple occurrences"`
- Name mangling: if `grep -n "<cell_name>"` returns zero results, retry with `"<cell_name>_reg"`. If found, use that. If neither: `confirmed: false` noting both attempts.
- All stages have no FM results: mark all `"confirmed": false`, report for manual review.

Your final output is `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`. After writing, verify it is non-empty with at least one confirmed entry, then exit. **RPT is generated by ORCHESTRATOR, not this agent.**
