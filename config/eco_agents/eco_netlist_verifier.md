# ECO Netlist Verifier — Deep Verify + Enrich Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in the parent orchestrator: `config/eco_agents/ORCHESTRATOR.md` **§STEP 3 — Study PreEco Gate-Level Netlist** (you run as the verify+enrich pass after `eco_netlist_studier`, before `eco_expand_chains.py` + `eco_validate_step3.py`). Same anchor in `config/eco_agents/ROUND_ORCHESTRATOR.md` **§STEP 6f Pass 6f-B** for per-round re-runs. Do NOT read other STEP sections.

**Role:** Reads the initial `eco_preeco_study.json` written by eco_netlist_studier (collect pass) and enriches every entry with per-stage net resolution, gap checks, missing entry detection, and cross-entry validation. This agent is the quality gate before eco_applier runs — every gap caught here prevents a wasted round.

**Inputs:** REF_DIR, TAG, BASE_DIR, GAP15_CHECK_PATH, SPEC_SOURCES (same as passed to studier), AI_ECO_FLOW_DIR.

**SPEC_SOURCES usage:** Used in Check 2 (per-stage net resolution) and Check 10 (cone verification) when resolving FM fenets spec results per stage. Each stage reads from its designated spec file path, NOT from the initial run spec for all stages:
```
SPEC_SOURCES:
  Synthesize: <path>  ← spec file whose results apply to Synthesize stage
  PrePlace:   <path>  ← spec file whose results apply to PrePlace stage
  Route:      <path>  ← spec file whose results apply to Route stage
```
If a stage has `SPEC_SOURCES[stage] = "FALLBACK"` → no FM results exist for that stage → apply Stage Fallback (GAP-5) in Check 10 instead of reading the spec.

**Input file:** `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` (written by eco_netlist_studier)

---

## OWNED OUTPUTS — single source of truth

| Path | When I write it | Format |
|---|---|---|
| `data/<TAG>_eco_preeco_study.json` | Step Final | JSON (in-place enriched — wc -l ≥ input count) |
| `data/<TAG>_eco_step3_netlist_verify.rpt` | Step Final | RPT (per-check counts) |
| Copy of both → `AI_ECO_FLOW_DIR/` | Step Final | mirror (ORCHESTRATOR checkpoints) |

## INPUTS — what the orchestrator gives me

- `REF_DIR`, `TAG`, `BASE_DIR`, `AI_ECO_FLOW_DIR`
- `SPEC_SOURCES` — per-stage fenets spec map (Check 2 + Check 10 use it)
- `GAP15_CHECK_PATH` — pre-computed `is_output_port` per old_token
- `data/<TAG>_eco_preeco_study.json` — studier's skeleton (I enrich in place)
- `data/<TAG>_eco_rtl_diff.json` — Step 1 RTL diff (cross-reference for Check 5/6)
- `data/<TAG>_eco_fenets_rename_map.json` — for Check 2 cell_name_per_stage resolution

## CHECK EXECUTION ORDER — dependency table

Checks MUST run in this sequence — each row reads the column-1 outputs of all rows above it.

| Run order | Check | Reads | Writes | Why this order |
|---:|---|---|---|---|
| 1 | **Check 1** (GAP-15) | `and_term_strategy` field, GAP15_CHECK | corrected `and_term_strategy` + `output_net` | Fixes `module_port_direct_gating` strategy before downstream checks consume it |
| 2 | **Check 5** (mode_H_risk) | `eco_rtl_diff.json` gates | `port_connections_per_stage` for missing stages | Propagates Mode H aliases before Check 2 walks pins |
| 3 | **Check 6** (expected_cascade_dffs) | Check 1's corrected `and_term` entries | `expected_cascade_dffs[]` | Needs Check 1's `module_port_direct_gating` decision |
| 4 | **Check 2** (per-stage net resolution) | gate entries, rename_map | `port_connections_per_stage` (all 3 stages) | Must run before cone verify (Check 10) |
| 5 | **Check 3** (per-stage DFF pin resolution) | DFF entries, rename_map | `port_connections_per_stage` CP/SE/SI | Must run before cone verify (Check 10) |
| 6 | **Check 4** (GAP-14 wire decl) | new_logic_gate entries | `needs_explicit_wire_decl` flag | Independent — depends only on Synth check |
| 7 | **Check 7** (port boundary) | new_logic entries | auto-added `port_declaration` entries | Must precede Check 8 (cascade may reference new ports) |
| 8 | **Check 8** (consumer cascade) | rewire entries | auto-added `rewire` entries for consumers | Must precede Check 10 (cone verify sees all rewires) |
| 9 | **Check 9** (UNCONNECTED bus bit) | DFF/gate entries with UNCONNECTED_* inputs | auto-added `port_connection` entries | Must precede Check 11 (named_wire flag) |
| 10 | **Check 11** (needs_named_wire) | resolved nets | `needs_named_wire` flag | Reads Checks 7/9 output |
| 11 | **Check 12** (PENDING_FM cleanup) | nets still `PENDING_FM_RESOLUTION:*` | resolved nets OR `UNRESOLVABLE:*` | Must precede Check 10 (cone verify needs final nets) |
| 12 | **Check 13** (real-net preference) | all nets in port_connections_per_stage | `net_source` flag | Reads final resolved nets |
| 13 | **Check 10** (cone verification) | rewire entries (incl. auto-added by 7/8/9) | `in_backward_cone` / `forward_trace_verified` | Last — sees all auto-added entries |
| 14 | **Check 14** (decompose A/B fallback) | entries with `d_input_decompose_failed: true` | `intermediate_net_strategy` | Last — needs per-stage resolution complete |

## HARD RULES — break these = fail the round (top 5)

1. **Run checks in dependency order** (table above). Skipping the order causes stale data — e.g. Check 10 cone verify must see Check 7/8/9 auto-added entries.
2. **Never leave `PENDING_FM_RESOLUTION:*` after Check 12** — resolve to real net OR mark `UNRESOLVABLE:*`. Never substitute a constant (`1'b0`) — changes ECO logic.
3. **Module-scope net validation for every accepted net** — a net present in a child module body is NOT accessible at the parent. Using it causes SVR-14 / FM-599 ABORT on all 3 stages.
4. **`needs_explicit_wire_decl: true` ONLY on output pins (ZN/Z/Q)** — never on inputs (SVR-9 duplicate decl).
5. **Never default PP/Route cell_name_per_stage to Synth's value** — Synth cells are frequently renamed/absent in P&R. Verify `zgrep -cw "<cell>" PreEco/<stage>.v.gz ≥ 1` before writing.

## I HAND OFF TO

- Returns to **STUDY_ORCHESTRATOR / ROUND_ORCHESTRATOR**, which then invokes:
  - `eco_expand_chains.py` — splice gate-level chains for new DFFs
  - `eco_validate_step3.py` — validator with 38 checks (HARD GATE before applier spawn)
  - `eco_applier.md` — Step 4 (writes PostEco netlists)

---

## Step 0 — Load and Inventory

Read `eco_preeco_study.json`. Build working lists:
- `gate_entries[]` — all `new_logic_gate` / `new_logic_dff` entries across all stages
- `rewire_entries[]` — all `rewire` entries
- `port_decl_entries[]` — all `port_declaration` entries
- `port_conn_entries[]` — all `port_connection` entries
- `and_term_entries[]` — all entries where `and_term_strategy` is set

Extract `rtl_diff` from `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json` for cross-reference.

For each stage, extract the PreEco netlist once (reuse across all checks):
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz > /tmp/eco_verify_<TAG>_Synthesize.v
zcat <REF_DIR>/data/PreEco/PrePlace.v.gz   > /tmp/eco_verify_<TAG>_PrePlace.v
zcat <REF_DIR>/data/PreEco/Route.v.gz      > /tmp/eco_verify_<TAG>_Route.v
```

---

## Check 1 — GAP-15: MODULE PORT DIRECT GATING (Every and_term Entry)
> **SKIP IF** no entry has `and_term_strategy` set.
> **DONE WHEN** every and_term entry has its strategy re-verified against GAP15_CHECK; mismatches corrected to `module_port_direct_gating` with output_net → old_token AND driver rename rewire emitted.

For every entry where `and_term_strategy` is set, re-verify the strategy is correct.

**Read pre-computed result:**
```python
gap15 = json.load(open(GAP15_CHECK_PATH))
tok = entry["output_net_original"]  # the old_token this gate replaces
if tok in gap15:
    is_output_port = gap15[tok]["is_output_port"]
else:
    # Fallback bash checks:
    rtl_check    = grep_count(f"output.*\\b{tok}\\b", rtl_file)
    gatelvl_check = grep_count(f"output.*\\b{tok}\\b", synth_module_header_lines)
    is_output_port = (rtl_check >= 1 or gatelvl_check >= 1)
```

**If `is_output_port=True` AND `and_term_strategy != "module_port_direct_gating"`:**
1. Correct `and_term_strategy` → `"module_port_direct_gating"`
2. Correct `output_net` → `<old_token>` (NOT `n_eco_<jira>_<seq>`)
3. Add driver rename rewire: original driver `.ZN → eco_<jira>_<seq>_orig`
4. Remove all individual consumer rewires for `<old_token>` in this module
5. Set `force_reapply: true`
6. Add `re_study_note: "VERIFIER GAP-15: corrected from <wrong_strategy> to module_port_direct_gating. output_net corrected to <old_token>."`

**Write to RPT:**
```
CHECK 1 GAP-15: <old_token>  is_output_port=<True/False>  strategy=<result>
  → <CORRECTED | OK>
```

---

## Check 2 — Per-Stage Net Resolution (Every new_logic_gate Entry)
> **SKIP IF** no `new_logic_gate` entries exist.
> **DONE WHEN** every gate input pin has `port_connections_per_stage` populated for all 3 stages with a net that passes `net_in_scope()` AND `validate_bus_net()`; remaining unresolved nets are marked `UNRESOLVABLE:<net>` with `confirmed: false`.

For every `new_logic_gate` entry, resolve ALL input nets for ALL 3 stages using the priority table. Studier-1 only recorded Synthesize values — this check fills PrePlace and Route.

**CRITICAL — MODULE-SCOPE NET VALIDATION (MANDATORY before accepting any net):**

All net existence checks MUST be scoped to the **declaring module** of the gate entry (`entry["module_name"]`), not the entire stage file. A net present in a child module definition is NOT accessible in the declaring module — using it there causes SVR-14 and FM-599 ABORT on all targets.

```python
def extract_module_scope(module_name, stage_lines):
    """Extract only lines between 'module <module_name>' and its matching endmodule."""
    in_module = False
    result = []
    for line in stage_lines:
        if re.match(rf'^module\s+{re.escape(module_name)}\b', line):
            in_module = True
        if in_module:
            result.append(line)
            if re.match(r'^endmodule', line):
                break
    return result

def net_in_scope(net_name, module_scope_lines):
    """Return True only if net_name appears within the already-extracted module scope."""
    return any(re.search(rf'\b{re.escape(net_name)}\b', l) for l in module_scope_lines)
```

**Extract module scope ONCE per entry, reuse for all pin checks:**
```python
module_scope = extract_module_scope(entry["module_name"], stage_lines)
```

**BUS INDEXING SCOPE CHECK — for any net containing `[N]`:**

If a resolved net uses array indexing (`name[N]`), verify the base name is declared as a multi-bit type within the declaring module scope. If not declared as a bus there, `[N]` indexing will cause SVR-14.

```python
def validate_bus_net(net_name, module_scope_lines):
    """
    For nets like 'foo[2]', check that 'foo' is declared as a bus in module scope.
    If not, find the scalar wire connected at bit [N] via port bus concatenation.
    Returns the correct net name to use (may differ from the input net_name).
    """
    m = re.match(r'^(\w+)\[(\d+)\]$', net_name)
    if not m:
        return net_name  # not a bus-indexed net, no action needed

    base, bit_idx = m.group(1), int(m.group(2))

    # Check if base is declared as a bus in module scope
    bus_declared = any(
        re.search(rf'\b(wire|input|output)\s+\[', l) and
        re.search(rf'\b{re.escape(base)}\b', l)
        for l in module_scope_lines
    )
    if bus_declared:
        return net_name  # valid bus indexing in this scope

    # base is NOT a bus in this scope — [N] indexing causes SVR-14
    # Find the scalar wire connected at bit position [N] via port bus concatenation.
    # Port bus connections look like: .port_name( { wire_A, wire_B, wire_C, ... } )
    # where the order is MSB→LSB and each element is a scalar wire name.
    scalar = find_scalar_for_bus_bit(base, bit_idx, module_scope_lines)
    if scalar:
        log(f"SVR14_FIX: {net_name} not a bus in scope → using scalar wire '{scalar}' at bit[{bit_idx}]")
        return scalar
    else:
        log(f"SVR14_RISK: {net_name} not a bus in scope and no scalar found → UNRESOLVED")
        return f"UNRESOLVED_SVR14:{net_name}"

def find_scalar_for_bus_bit(base_name, bit_idx, module_scope_lines):
    """
    Locate the scalar wire used at bit position [bit_idx] of a port bus connection
    for base_name. Port bus: .any_port( { wire_N, ..., wire_1, wire_0 } ) where
    the port carries base_name from a child module.
    bit_idx=0 → last element in concatenation (LSB).
    """
    import re
    # Find lines that reference base_name inside a port connection
    for i, line in enumerate(module_scope_lines):
        if re.search(rf'\b{re.escape(base_name)}\b', line) and '.' in line and '{' in line:
            # Extract the concatenation block (may span multiple lines)
            block = ''
            depth = 0
            for j in range(i, min(i + 20, len(module_scope_lines))):
                block += module_scope_lines[j]
                depth += module_scope_lines[j].count('{') - module_scope_lines[j].count('}')
                if depth <= 0:
                    break
            # Extract elements inside { ... }
            m = re.search(r'\{([^}]+)\}', block)
            if m:
                elements = [e.strip() for e in m.group(1).split(',')]
                # elements[0] = MSB, elements[-1] = LSB
                # bit_idx=0 → elements[-1], bit_idx=1 → elements[-2], etc.
                pos = len(elements) - 1 - bit_idx
                if 0 <= pos < len(elements):
                    wire = elements[pos].strip()
                    # Return only if it looks like a valid scalar identifier
                    if re.match(r'^\w+$', wire):
                        return wire
    return None
```

For each input pin in `port_connections`:

**HFS alias (Priority 2/3 term):** A net renamed by Hierarchical Flattening Synthesis or P&R (scan insertion, CTS, optimization). The original RTL name still exists in SynRtl/ but appears under a tool-generated alias in gate-level stages. Detected by tracing the driver cell across stages.

| Priority | Method |
|----------|--------|
| 0 | RTL-named primary input — check `net_in_scope("input", module_scope)` → **HIGHEST** |
| 1 | Direct name match within module scope — `net_in_scope(net, module_scope)` |
| 2 | Trace driver cell in Synthesize → find same cell output in this stage, `net_in_scope` check (HFS alias) |
| 3 | P&R alias search via 0b-ALIAS driver trace, `net_in_scope` check |
| 4 | Backward cone trace (max 10 hops), `net_in_scope` check |
| — | All priorities exhausted → see net status taxonomy below |

**Net status taxonomy — use exactly one:**
| Status | When to use |
|--------|-------------|
| `UNRESOLVED_IN_<Stage>:<net>` | Priority 0-4 not yet tried for this stage — still searching |
| `UNRESOLVABLE:<net>` | All priorities (0-4) exhausted, no valid net found — set `confirmed: false` |
| `NEEDS_NAMED_WIRE:<net>` | Net exists as UNCONNECTED_* or bus bit — rename required (0b-UNCONNECTED) |
| `PENDING_FM_RESOLUTION:<net>` | Net not in PreEco gate-level; FM fenets rerun needed to resolve |

Never leave a net as `PENDING_FM_RESOLUTION` after a rerun completes — resolve or mark `UNRESOLVABLE`.

**After resolving any net — run `validate_bus_net()` before recording in `port_connections_per_stage`.**

**COMBINATIONAL GATE DRIVER PIN CHECK (rewire entries — ZN→ZN1 across stages):**

For `rewire` entries, the driver cell may use different output pin names in different P&R stages (e.g., `SPC2NR2D1` uses `.ZN` in Synthesize but `.ZN1` in PrePlace/Route). The rewire must use the CORRECT pin name per stage:

```python
# After finding driver cell in each stage:
def resolve_driver_pin(driver_cell_name, stage_lines, fallback_pin):
    """Find actual output pin name used by driver cell in this stage."""
    for line in stage_lines:
        if driver_cell_name in line:
            # Read the last .PIN(net) — that's the output pin
            pins = re.findall(r'\.(\w+)\s*\(', line)
            for candidate in reversed(pins):  # output pin is typically last
                if candidate in ('ZN','ZN1','Z','Q','QN','CO','S'):
                    return candidate
    return fallback_pin

# Set pin_per_stage for rewire entries:
entry['pin_per_stage'] = {
    'Synthesize': resolve_driver_pin(cell, synth_lines, entry.get('pin','')),
    'PrePlace':   resolve_driver_pin(cell, pp_lines,    entry.get('pin','')),
    'Route':      resolve_driver_pin(cell, route_lines, entry.get('pin',''))
}
if len(set(entry['pin_per_stage'].values())) > 1:
    log(f"DRIVER_PIN_CHANGE: {cell} pin differs across stages: {entry['pin_per_stage']}")
```

eco_applier reads `pin_per_stage` (or `per_stage_pin`) to use the correct pin name per stage.

**MANDATORY — `cell_name_per_stage` resolution for every rewire entry:**

The verifier MUST populate `cell_name_per_stage` for all 3 stages using the rename_map and step2 fenets rpt. **Never default PP/Route to the Synth cell name** — Synth cells are frequently renamed or absent in P&R stages.

Resolution priority per stage:
1. **Read rename_map** (`<TAG>_eco_fenets_rename_map.json`) — look up `<scope>/<old_token>` and use the per-stage cell extracted from the qualifying impl result
2. **Read step2 fenets rpt** "Qualifying cells passed to Step 3" section — this explicitly lists the per-stage driver cell for each net
3. **Stage Fallback (GAP-5)** — grep the stage PreEco for the old_token and find its driver cell

After setting each stage's cell, verify it exists:
```bash
zgrep -cw "<cell_name>" PreEco/<stage>.v.gz  # must be ≥ 1
```
If 0 → wrong cell, re-run resolution. Do NOT write a cell that has 0 occurrences in the target stage.

**Scan alias rejection — MANDATORY before accepting any resolved net:**

A resolved net is a scan alias if it matches tool-generated DFT naming conventions AND has no functional driver in PreEco Synthesize. Check both conditions — do NOT reject on name pattern alone (a user-named signal could match):

```python
# Step 1: pattern match (necessary but not sufficient condition)
scan_alias_patterns = [r'^test_so\d+', r'^dftopt\d+', r'^aps_rename_\d+',
                       r'^copt_net_\d+', r'^ctmn_\d+']
name_matches = any(re.match(p, resolved_net) for p in scan_alias_patterns)

# Step 2: confirm it has no driver in PreEco Synthesize (i.e., P&R-generated)
if name_matches:
    synth_driver_count = grep_module_scope(
        rf'\.(Q|Z|ZN|ZN1)\s*\(\s*{re.escape(resolved_net)}\s*\)',
        module_name, synthesize_preeco)
    if synth_driver_count == 0:
        log(f"SCAN_ALIAS_REJECTED: {resolved_net} — DFT name pattern + no Synth driver")
        resolved_net = None  # force next priority
    # else: name matches pattern but has a functional driver — keep it
```

**GAP-CTS-2 — CTS merged cell input check (Route stage only):**
After resolving any net for Route — verify its driver is not a CTS merged cell:
```bash
# Driver absent from Synthesize PreEco → CTS-created → merged cell risk
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -c "<driver_cell_instance>" → 0 means CTS-created
# REJECT → fall back to Priority 0 (primary input port)
grep "input.*\b<base_signal_name>\b" <Route_module_header>
```

**Cross-stage consistency check:**
```python
if resolved["PrePlace"] != resolved["Route"]:
    # Try to find a common primary input port name that exists in ALL stages
    for candidate in grep_all_input_ports(module_lines["PrePlace"]):
        if (grep_count(candidate, preplace_lines) >= 1 and
            grep_count(candidate, route_lines) >= 1 and
            is_same_rtl_signal(candidate, original_rtl_net)):
            for stage in ["PrePlace", "Route"]:
                resolved[stage] = candidate
            log(f"CROSS_STAGE_NORMALIZE: → {candidate}")
            break
```

Update `port_connections_per_stage` for all 3 stages. Set `confirmed: false` for any stage where input remains `UNRESOLVED_IN_<Stage>`.

---

## Check 3 — Per-Stage Pin Verification (Every new_logic_dff Entry)
> **SKIP IF** no `new_logic_dff` entries exist.
> **DONE WHEN** every DFF entry has `port_connections_per_stage` populated for CP/SE/SI/D/Q in all 3 stages; SE/SI follow the bridge-port-default rule (Synth=`1'b0`, PP/Route=bridge port); CTS clock rename flagged via `cts_clock_renamed: true` when CP differs across stages.

For every `new_logic_dff` entry, resolve ALL pins per stage. Studier-1 records Synthesize only.

**Step A — Classify each pin:** Functional (clock, data, Q) vs Auxiliary (scan SE/SI) from RTL context.

**Step B — Resolve functional pins per stage:**
- Priority 1: `grep -cw "<net>" /tmp/eco_verify_<TAG>_<Stage>.v` — if ≥ 1, use it
- Priority 2: P&R alias (only if Priority 1 absent)
- Priority 3: Structural driver trace

**Step C — SE/SI pins: Synth = `1'b0`; PP/Route = bridge port wires (default).**

Forcing `1'b0` in P&R isolates the new ECO DFF from the scan chain — FM sees a cone divergence (DFF appears as DFF0X). Wire PP and Route to the bridge ports emitted by `eco_emit_bridge_plumbing.py` so cone reach is identical across stages.

```python
for scan_pin in ('SE', 'SI'):
    port_connections_per_stage['Synthesize'][scan_pin] = "1'b0"   # RTL-clean
    for stage in ('PrePlace', 'Route'):
        # bridge_port is the default for both PP and Route. neighbor_dff is
        # permitted only when Route also resolved to neighbor_dff (rare).
        port_connections_per_stage[stage][scan_pin] = bridge_port_wire(scan_pin)
```

**GAP-CTS-1 — Verify CP net exists in Route before recording:**
```bash
grep -cw "<resolved_cp_net>" /tmp/eco_verify_<TAG>_Route.v
# If 0 → CP renamed by CTS → find CTS-assigned clock net from neighbour DFF in Route:
zcat <REF_DIR>/data/PostEco/Route.v.gz | awk '/<neighbour_dff>/{p=1} p && /\.CP\s*\(/{print; exit}'
```
Set `cts_clock_renamed: true` when CP differs between PrePlace and Route.

**GAP-20 — SE pin mismatch detection:**
After resolving SE for all stages: if PrePlace SE ≠ Route SE AND neither exists in RTL source (`grep -rw "<se_net>" <REF_DIR>/data/PreEco/SynRtl/` → 0), set `needs_se_tune: true`.

Update `port_connections_per_stage` for all 3 stages.

---

## Check 4 — GAP-14: Wire Declaration Flag (Every new_logic_gate Entry)
> **SKIP IF** no `new_logic_gate` entries exist.
> **DONE WHEN** every gate's output net has `needs_explicit_wire_decl` decided by Synth grep count (0 → true, ≥ 1 → false); input pins are NEVER flagged (SVR-9 prevention).

For every new gate whose output net is genuinely new (not pre-existing in PreEco), `needs_explicit_wire_decl` must be `true`.

```bash
grep -cw "<output_net>" /tmp/eco_verify_<TAG>_Synthesize.v
```
- Count = 0 → net is new → set `needs_explicit_wire_decl: true`
- Count ≥ 1 → net exists → set `needs_explicit_wire_decl: false`

**CRITICAL — output net ONLY:** `needs_explicit_wire_decl: true` applies ONLY to the pin driven by ZN/Z/Q. NEVER set it for input nets — this causes SVR-9 duplicate wire declaration.

Do NOT set `needs_explicit_wire_decl: true` for:
- Gate inputs (any port other than ZN/Z/Q)
- Renamed original driver output nets (already present in PreEco)
- Nets driven by port connections (implicitly declared)

---

## Check 5 — mode_H_risk Propagation (Every gate Entry)
> **SKIP IF** `eco_rtl_diff.json` has no gates with `mode_H_risk: true` and `missing_in_stages`.
> **DONE WHEN** every flagged gate has Priority-3 structural alias resolved into `port_connections_per_stage[stage][pin]` for each `missing_in_stages` stage AND `force_reapply: true` set with `re_study_note` updated.

Re-read `eco_rtl_diff.json` for all gates with `mode_H_risk: true` and `missing_in_stages`:
```python
for change in rtl_diff.get("changes", []):
    for gate in change.get("d_input_gate_chain", []):
        if gate.get("mode_H_risk") and gate.get("missing_in_stages"):
            entry = find_entry_by_instance(gate["instance_name"])
            if entry:
                for stage in gate["missing_in_stages"]:
                    if not already_updated(entry, stage):
                        alias = priority3_structural_trace(gate["inputs"][0], stage)
                        pc = entry.setdefault("port_connections_per_stage", {}).setdefault(stage, {})
                        pc[gate["pin"]] = alias or f"NEEDS_NAMED_WIRE:{gate['inputs'][0]}"
                        entry["force_reapply"] = True
                        entry.setdefault("re_study_note", "")
                        entry["re_study_note"] += f" mode_H_risk resolved for {stage}."
```

---

## Check 6 — expected_cascade_dffs (Every and_term Entry with module_port_direct_gating)
> **SKIP IF** no and_term entries have `and_term_strategy == "module_port_direct_gating"`.
> **DONE WHEN** every matching entry has `expected_cascade_dffs[]`, `expected_cascade_net`, and `expected_cascade_reason` populated.

For every `and_term` entry where `and_term_strategy == "module_port_direct_gating"` and `expected_cascade_dffs` is missing or empty:

```python
old_token = entry["output_net"]   # = old_token when module_port_direct_gating
module_lines = extract_module_lines(entry["module_name"], synth_preeco_lines)

expected_cascade_dffs = []
for dff_instance in grep_all_dffs_in_module(module_lines):
    d_input_cone = trace_D_input_cone(dff_instance, module_lines, max_hops=10)
    if old_token in d_input_cone or f"{old_token}_orig" in d_input_cone:
        expected_cascade_dffs.append(dff_instance)

entry["expected_cascade_dffs"] = expected_cascade_dffs
entry["expected_cascade_net"] = old_token
entry["expected_cascade_reason"] = (
    f"{old_token} is gated by this ECO. All DFFs whose D-input cone reaches "
    f"{old_token} will differ vs old SynRtl — INTENTIONAL. eco_fm_analyzer "
    f"must classify as INTENTIONAL_CASCADE immediately."
)
log(f"CHECK 6: {len(expected_cascade_dffs)} expected cascade DFFs identified for {old_token}")
```

---

## Check 7 — 0e-PORT: Port Boundary Analysis (Every new_logic Entry)
> **SKIP IF** all new_logic entries reside in the same module as their consumers (no cross-module reach).
> **DONE WHEN** every new_logic whose output escapes to the parent module has a `port_declaration` study entry added (declaration_type=output) with `force_reapply: true`.

For every `new_logic_gate` or `new_logic_dff` entry, check if its output net escapes the declaring module scope:

```python
output_net = entry["output_net"]
declaring_module = entry["module_name"]
parent_module = find_parent_module(declaring_module, preeco_hierarchy)

# grep parent scope for: .<any_port>(<output_net>) inside the child instance block
parent_uses_net = grep_parent_for_output_net(output_net, parent_module, preeco_lines)

if parent_uses_net:
    already_covered = any(
        e["change_type"] == "port_declaration"
        and e["signal_name"] == output_net
        and e["module_name"] == declaring_module
        for e in all_entries
    )
    if not already_covered:
        add_entry({
            "change_type": "port_declaration",
            "signal_name": output_net,
            "module_name": declaring_module,
            "declaration_type": "output",
            "instance_scope": entry["instance_scope"],
            "confirmed": True,
            "force_reapply": True,
            "reason": f"VERIFIER 0e-PORT: {output_net} used in parent {parent_module} — auto-added output port_declaration"
        })
        log(f"CHECK 7: auto-added port_declaration for {output_net} in {declaring_module}")
```

---

## Check 8 — 0e-CASCADE: Consumer Cascade Tracing (Every Driver Rename)
> **SKIP IF** no `rewire` entries rename a driver output (`old_net → new_net` where old_net was a cell output, not a pin input).
> **DONE WHEN** every consumer of a renamed driver in the same module has a corresponding `rewire` study entry — auto-add the missing ones.

For every `rewire` entry that renames a driver output (`old_net → new_net`), find ALL consumers of `old_net` in the same module and verify each has a corresponding rewire:

```python
for rewire in rewire_entries_with_driver_rename:
    renamed_from = rewire["old_net"]
    module_lines = extract_module_lines(rewire["module_name"], synth_preeco_lines)
    consumers = grep_all_consumers(renamed_from, module_lines)
    # consumers = [(cell_name, pin_name), ...]

    for (cell_name, pin_name) in consumers:
        already_covered = any(
            e["change_type"] == "rewire"
            and e.get("cell_name") == cell_name
            and e.get("pin") == pin_name
            for e in all_entries
        )
        if not already_covered:
            new_target = determine_consumer_target(cell_name, pin_name, rewire, all_entries)
            add_entry({
                "change_type": "rewire",
                "cell_name": cell_name, "pin": pin_name,
                "old_net": renamed_from, "new_net": new_target,
                "instance_scope": rewire["instance_scope"],
                "module_name": rewire["module_name"],
                "confirmed": True, "force_reapply": True,
                "reason": f"VERIFIER 0e-CASCADE: consumer of renamed {renamed_from} → auto-added"
            })
            log(f"CHECK 8: auto-added consumer rewire {cell_name}.{pin_name} ({renamed_from} → {new_target})")
```

---

## Check 9 — UNCONNECTED Bus Bit (Every DFF/gate with UNCONNECTED_* Input)
> **SKIP IF** no entry has any input matching `^(SYNOPSYS_)?UNCONNECTED_\d+$`.
> **DONE WHEN** every UNCONNECTED_* input is replaced by an `eco_<jira>_<signal>` named wire AND a `port_connection` entry renames the UNCONNECTED slot in the child instance.

For every entry where any input in `port_connections` matches `UNCONNECTED_<N>` or `SYNOPSYS_UNCONNECTED_<N>`:

```python
# 1. Find child module instance driving this bus bit
#    grep -n ".<output_port_bus>.*{.*<unconnected_net>" PreEco/Synthesize.v
# 2. Create named wire
eco_wire_name = f"eco_{JIRA}_{signal_alias}"

# 3. Add port_connection to rename the UNCONNECTED slot
add_entry({
    "change_type": "port_connection",
    "parent_module": parent_module_name,
    "instance_name": child_instance_name,
    "port_name": bus_port_name,
    "net_name": eco_wire_name,
    "bus_bit_index": N,
    "force_reapply": True,
    "reason": f"VERIFIER UNCONNECTED: UNCONNECTED_{N} renamed to {eco_wire_name} for D-input traceability"
})

# 4. Use eco_wire_name as the gate/DFF input
entry["port_connections"][input_pin] = eco_wire_name
entry["needs_explicit_wire_decl"] = True  # eco_wire_name is genuinely new

# 5. Verify eco_wire_name does NOT already exist
assert grep_count(eco_wire_name, synth_lines) == 0, f"{eco_wire_name} already exists"
```

---

## Check 10 — Cone Verification (Every rewire Entry)
> **SKIP IF** no `rewire` entries exist (including auto-added by Checks 7/8/9).
> **DONE WHEN** every rewire entry has `in_backward_cone` decided (max 8 hops with cycle detection); forward-trace fallback verified when backward miss; stage fallback (GAP-5) applied for stages without FM results.

For every `rewire` entry, run backward cone then forward trace to confirm the cell is in the target DFF's cone.

**Backward cone (max 8 hops) — with cycle detection:**
```python
visited = set()
queue = [target_dff_d_net]
for hop in range(8):
    if not queue: break
    net = queue.pop(0)
    if net in visited: continue   # cycle — skip, do not re-expand
    visited.add(net)
    driver = find_driver_cell(net, module_lines)
    if not driver: continue
    if old_net in get_cell_inputs(driver, module_lines):
        return True  # in_backward_cone
    queue.extend(get_cell_inputs(driver, module_lines))
# not found after 8 hops → run forward trace
```
If `old_net` appears → `in_backward_cone: true`, `confirmed: true`. If not found → forward trace.

**Forward trace (max 6 hops):**
```bash
grep -n "( <cell_output_net> )" /tmp/eco_verify_<TAG>_<Stage>.v | grep -v "\.ZN\|\.Z\b\|\.Q\b" | head -5
```
- If forward trace reaches target DFF → `in_backward_cone: true`, `forward_trace_verified: true`
- If forward trace confirms unrelated logic → `confirmed: false`, record destination

**Stage Fallback (GAP-5) — for any stage with no FM result:**

Take all `confirmed: true` entries from best reference stage (Synthesize → PrePlace → Route). For each, find the correct cell in the missing stage using this priority:

1. **Direct grep** — `zgrep "<cell_name>" PreEco/<stage>.v.gz | grep "<old_net>"`:
   - Exactly 1 hit → use it. `source: "<ref>_fallback"`.
   - 0 hits → `old_net` is likely an HFS alias in this stage → go to step 2.
   - 2+ hits → multiple candidates → go to step 3 to pick the right one.

2. **HFS alias search (0 hits)** — P&R renames high-fanout nets to `FxPrePlace_HFSNET_*`/`FxPlace_*`. The correct cell's pin may carry an alias, not the original net name:
   - From Synth confirmed cell: get its output net (`.ZN` / `.Z`)
   - In Synth: find the 1-hop downstream consumer of that output net
   - In the missing stage: find that same consumer by instance name, read its input pins → one input is the HFS alias
   - Trace back from the HFS alias to its driver cell → that is the correct cell. `source: "stage_fallback_hfs"`.

3. **Cone check (2+ hits)** — when multiple cells use `old_net`, pick the one whose output reaches the target DFF within 5 forward hops. Reject any candidate not in the target cone. **Never take the first grep hit by default.**

4. **Still unresolved** → `confirmed: false`, `source: "stage_fallback_unverified"` — flag for manual review.

---

## Check 11 — needs_named_wire (All Resolved Nets)
> **DONE WHEN** every resolved net in port_connections_per_stage has `needs_named_wire` flag decided via the helper rule (driven only by submodule output bus → true).

Apply `needs_named_wire()` to every resolved net in `port_connections_per_stage`:

```python
def needs_named_wire(net_name, stage_lines):
    """Returns True if net's only driver is a hierarchical submodule output port bus."""
    import re
    direct_driver = any(
        re.search(rf'\.\w+\(\s*{re.escape(net_name)}\s*\)', line)
        and '{' not in line and not line.strip().startswith('//')
        for line in stage_lines
    )
    if direct_driver:
        return False
    in_port_bus = any(
        re.search(rf'\.\w+\s*\(\s*\{{[^}}]*\b{re.escape(net_name)}\b[^}}]*\}}\s*\)', line)
        for line in stage_lines if not line.strip().startswith('//')
    )
    return in_port_bus
```

If `needs_named_wire()` returns True → set `needs_named_wire: true` and `port_bus_source_net: <net>` on the entry.

**GAP-18 — Submodule bus output check:**
```python
re.search(rf'\.\s*\w+\s*\(\s*\{{[^}}]*\b{re.escape(signal)}\b', module_line)
```
If found → set `driven_by_submodule: true`, `driver_type: "submodule_bus_output"`, `confirmed: true`, `needs_named_wire: true`.

---

## Check 12 — PENDING_FM_RESOLUTION Cleanup
> **SKIP IF** no entry has any input net marked `PENDING_FM_RESOLUTION:*`.
> **DONE WHEN** every PENDING signal is either resolved (via condition_input_resolutions OR forward-consumer search F1→F2→F3) OR marked `UNRESOLVABLE:<signal>`. NEVER use `1'b0` as fallback constant.

For every entry where any input net is still `PENDING_FM_RESOLUTION:<signal>`:

1. **Check condition_input_resolutions first** — read `<BASE_DIR>/data/<TAG>_eco_fenets_rerun_round<ROUND>.json` if it exists. For any entry whose `original_signal` matches the PENDING signal, immediately set `port_connections_per_stage[Synthesize][pin] = resolved_gate_level_net`. This avoids waiting for a re_study round. If file absent, fall through to step 2.
2. **For Synthesize stage**: use the `condition_input_resolutions` resolved net **directly** — do NOT trace one level deeper to its source. The resolved net is the correct gate-level name with the correct polarity; tracing to its driver input changes polarity (e.g. an INV output used as a gate input ≠ the INV input). Verify it exists (`zgrep -cw <resolved_net> PreEco/Synthesize.v.gz ≥ 1`) then use it verbatim.

   **For P&R stages** (not Synthesize): trace each PENDING_FM_RESOLUTION signal **independently** from its own Synth driver chain. **NEVER copy or reuse the P&R result from a different PENDING_FM_RESOLUTION signal** — different synthesis-internal signals come from different driver cells and must resolve to different P&R nets.

   ```bash
   # For EACH pending signal, independently:
   # Find driver cell of THIS signal's synth_net in Synthesize PreEco
   grep -n "\.<output_pin>( <synth_net> )" /tmp/eco_verify_<TAG>_Synthesize.v | head -1
   driver_cell = extract_instance_name(that_line)

   # Search THIS driver_cell in P&R stage
   grep -cw "<driver_cell>" /tmp/eco_verify_<TAG>_<Stage>.v
   ```
   - If driver cell found → read its output net → **verify the output net exists in the stage** (`zgrep -cw "<output_net>" PreEco/<stage>.v.gz` ≥ 1) → use as P&R alias ✓. If the output net has 0 occurrences → the driver cell was found but its output was renamed — search its input net's equivalent instead.
   - If driver cell **absent** in P&R (P&R renamed/merged it) → **search one level deeper**:
     find driver_cell's input nets in Synthesize → find their drivers → search those upstream cells in P&R. **After each step, verify the candidate output net actually exists in the stage** — accept only a net with ≥ 1 occurrences. Never accept a net that has 0 occurrences in the target stage.
   - If the entire backward cone is absent (all driver cells at every level are absent in P&R — PD completely restructured the logic) → **run `eco_resolve_synth_internal.py` first**:
     ```bash
     python3 script/eco_scripts/eco_resolve_synth_internal.py \
         --ref-dir <REF_DIR> --synth-net <resolved_synth_net> \
         --stage <PrePlace|Route> --output /tmp/resolve_<net>.json
     # Read resolved_net from JSON. Use directly if not UNRESOLVABLE.
     ```
     If script returns UNRESOLVABLE → **manual forward consumer search** (F1→F2→F3):

     **Step F1 — Find consumers of the Synth resolved net:**
     ```bash
     # Find input-pin usages of the resolved net in Synth (not its output — exclude .ZN/.Z/.Q lines)
     grep -n "\( <synth_net> \)" /tmp/eco_verify_<TAG>_Synthesize.v \
       | grep -v "\.ZN\|\.Z\s*(\|\.Q\s*(" | head -5
     # Each result line looks like:
     #   CELLTYPE CONSUMER_CELL ( ... .<INPUT_PIN> ( <synth_net> ) ... )
     # Extract CONSUMER_CELL name and INPUT_PIN name from each result.
     ```

     **Step F2 — Search each consumer cell in P&R stage:**
     ```bash
     grep -n "<CONSUMER_CELL>" /tmp/eco_verify_<TAG>_<Stage>.v | head -3
     # If found → the same cell exists in P&R. Read the full instantiation block.
     ```

     **Step F3 — Read the net on the same input pin in P&R:**
     ```bash
     # From the consumer cell block in P&R, read .<INPUT_PIN>(<actual_net>)
     grep -A20 "<CONSUMER_CELL>" /tmp/eco_verify_<TAG>_<Stage>.v \
       | grep "\.<INPUT_PIN>\s*(" | head -1
     # The net inside the parentheses is the P&R equivalent of <synth_net>.
     # Verify it exists: grep -cw "<actual_net>" /tmp/eco_verify_<TAG>_<Stage>.v ≥ 1
     ```

     Try multiple consumers until one is found in P&R. Accept the first net that exists in the stage.
3. **Still not found after both backward and forward search** → mark `UNRESOLVABLE:<signal>`. **Do NOT use `1'b0` as a constant** — substituting a constant changes the ECO logic and may be architecturally wrong (P&R may have optimized away the cell for reasons unrelated to the signal being constant).

**CRITICAL: Do NOT leave `PENDING_FM_RESOLUTION` after a rerun returned FM-036.** Resolve via structural trace or mark `UNRESOLVABLE` — never leave as PENDING.

---

## Check 13 — Universal Real-Net Preference (All Entries)
> **DONE WHEN** every net in port_connections_per_stage has `net_source` flag set (real_rtl_name OR P&R alias), AND every `port_connection` submodule pattern is validated per-stage with `instance_confirmed` flag.

For every net in every `port_connections_per_stage[stage]` entry:
1. `grep -cw "<net>" /tmp/eco_verify_<TAG>_<Stage>.v`
2. If ≥ 1 → mark `net_source: "real_rtl_name"` — already correct
3. If = 0 → flag as P&R alias — verify via structural trace before accepting

**Validate `port_connection` submodule patterns:**
```bash
grep -c "<submodule_type> <instance_name>" /tmp/eco_verify_<TAG>_Synthesize.v
```
If 0 → check PrePlace and Route. Record per-stage `instance_confirmed` flags. Set `confirmed: false` for stages where instance not found.

---

## Check 13b — BUS-CONST-DECODE: Bus Equality Constant Gate Verification
> **SKIP IF** no `new_logic_gate` Synth entry has `gate_function` ∈ {IND2, IND3, IND4} with ≥2 bus-bit inputs.
> **DONE WHEN** every matching IND-N gate generated from `~(bus[N:0] == K)` has companion INV cells emitted for every zero-bit of K (raw bus into IND-N is wrong unless K is all-ones).

For every `new_logic_gate` entry in Synthesize where `gate_function` is `IND2`, `IND3`, or `IND4` and the inputs include two or more bus-bit signals (contain `[`):

1. Locate the original RTL condition that generated this gate (check `rtl_condition` field or surrounding context in the rtl_diff `new_condition_gate_chain`).
2. If the condition is `~(bus[N:0] == K)` for some constant K:
   - Count zero-bits in K. Each zero-bit means that bus bit must be **inverted** before entering the NAND gate.
   - If K has any zero-bits AND the study entry has no companion INV gate for those bits → **FAIL `BUS-CONST-DECODE`**: the IND-N with raw bus bits computes `~(bus==all-ones)`, not `~(bus==K)`. Correct by emitting an `INV` entry for each zero-bit bus signal before the ND-N gate, and changing the ND-N inputs to use the INV outputs.
   - If K is all-ones → IND-N with raw bus bits is correct, skip.

**Example of wrong:** `IND2(bus[1], bus[0])` for `~(bus[1:0]==2'b01)` — computes `~(bus==2'b11)`.
**Example of correct:** `INV(bus[1])` → `ND2(~bus[1], bus[0])` for `~(bus[1:0]==2'b01)`.

---

## Check 14 — Strategy A/B Fallback for d_input_decompose_failed
> **SKIP IF** no entry has `d_input_decompose_failed: true` without `intermediate_net_strategy` set.
> **DONE WHEN** every matching entry has `intermediate_net_strategy` set to `compound_gate_insertion` (Strategy A, preferred) OR `pivot` (Strategy B, fallback). Strategy A always tried first.

For every entry with `d_input_decompose_failed: true` that has no `intermediate_net_strategy` set:

**Strategy A — Compound gate insertion from PreEco (PRIORITY 1 — always try first):**

Search the backward cone of `target_register.D` for existing compound gates (multi-input AND+OR combinations, NOT MUX2) whose inputs are already connected to the relevant signal cones:
```bash
zcat PreEco/Synthesize.v.gz | awk "/\b<target_register>_reg\b/,/\) ;/" | \
  grep -E "^\s+[A-Z][A-Z0-9]+[0-9]\s+[a-z]" | grep -v "DFF\|SDF\|MUX" | head -10
```
- If compound gate found → use it: rewire one of its replaceable inputs to the new condition output
- Gate inputs use EXISTING PreEco nets (already in the netlist, no PENDING_FM_RESOLUTION risk in P&R)
- Never use MUX2 — structural non-equivalence in FM
- Set `intermediate_net_strategy: "compound_gate_insertion"`

**Strategy B — Pivot approach (fallback when A fails):**
- Trace backward from `target_register.D` (max 5 hops) to first net with fanout ≥ 2
- Verify pivot per stage using Priority 1/2 + structural trace
- Validate pivot driver polarity (inverting vs non-inverting)
- Set `intermediate_net_strategy: "pivot"`

Verify gate types exist in PreEco: `grep -cm1 "<gate_type>" PreEco/Synthesize.v.gz > 0`

---

## Step Final — Write Enriched JSON and RPT

**Sort all entries by PASS_ORDER before writing (including auto-added entries from Checks 7/8/9):**
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
**NOTE:** Auto-added `port_declaration` entries (Check 7) sort before `rewire` entries (Check 8) by design — eco_applier must declare ports before applying rewires that reference them. The PASS_ORDER guarantees this.

Write enriched JSON back to `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`.
Verify `wc -l` ≥ original line count.

**Write verification RPT** to `<BASE_DIR>/data/<TAG>_eco_step3_netlist_verify.rpt`:
```
ECO NETLIST VERIFIER REPORT — TAG=<TAG>
========================================
CHECK 1  GAP-15:        <N> entries checked, <N> corrected
CHECK 2  Per-stage nets: <N> gate entries enriched, <N> UNRESOLVED remaining
CHECK 3  DFF pins:       <N> DFF entries enriched, <N> CTS clock renames found
         Reset pin approach: <N> DFFs used reset pin (GAP-CTS-2 avoided), <N> fell back to D-input baking
         [for each DFF: reset_pin_used=YES → cell=<type> pin=<name> | NO → GAP-CTS-2 risk]
CHECK 4  Wire decls:     <N> needs_explicit_wire_decl flags set
CHECK 5  mode_H_risk:    <N> entries updated
CHECK 6  Cascade DFFs:   <N> and_term entries populated
CHECK 7  PORT boundary:  <N> port_declaration entries auto-added
CHECK 8  CASCADE trace:  <N> consumer rewire entries auto-added
CHECK 9  UNCONNECTED:    <N> bus bit renames added
CHECK 10 Cone verify:    <N> rewire entries confirmed, <N> excluded
CHECK 11 named_wire:     <N> entries flagged
CHECK 12 PENDING_FM:     <N> resolved, <N> marked UNRESOLVABLE
CHECK 13 Real-net pref:  <N> P&R aliases detected
CHECK 14 Decompose:      <N> Strategy A, <N> Strategy B applied
----------------------------------------
TOTAL ENTRIES:   <N>   confirmed: <N>   confirmed_false: <N>
AUTO-ADDED:      <N> new entries inserted by verifier
FORCE_REAPPLY:   <N> entries flagged
WARNINGS:        <list any remaining UNRESOLVED or UNRESOLVABLE nets>
```

**Copy BOTH outputs to AI_ECO_FLOW_DIR (MANDATORY — ORCHESTRATOR checkpoints both):**
```bash
cp <BASE_DIR>/data/<TAG>_eco_preeco_study.json      <AI_ECO_FLOW_DIR>/
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_verify.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_verify.rpt  # verify copy succeeded
```

**Cleanup temp files** (created in Step 0 — `-f` flag handles cases where decompression failed):
```bash
rm -f /tmp/eco_verify_<TAG>_Synthesize.v /tmp/eco_verify_<TAG>_PrePlace.v /tmp/eco_verify_<TAG>_Route.v
```

**Exit after writing and copying. Do NOT spawn any further agents — ORCHESTRATOR or ROUND_ORCHESTRATOR handles what comes next.**
