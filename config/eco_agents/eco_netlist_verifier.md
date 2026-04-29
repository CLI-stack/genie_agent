# ECO Netlist Verifier — Deep Verify + Enrich Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Role:** Reads the initial `eco_preeco_study.json` written by eco_netlist_studier (collect pass) and enriches every entry with per-stage net resolution, gap checks, missing entry detection, and cross-entry validation. This agent is the quality gate before eco_applier runs — every gap caught here prevents a wasted round.

**Inputs:** REF_DIR, TAG, BASE_DIR, GAP15_CHECK_PATH, SPEC_SOURCES (same as passed to studier).

**Input file:** `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` (written by eco_netlist_studier)
**Output file:** Same path — enriched in-place. Verify `wc -l` ≥ input line count after writing.

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

For every `new_logic_gate` entry, resolve ALL input nets for ALL 3 stages using the priority table. Studier-1 only recorded Synthesize values — this check fills PrePlace and Route.

For each input pin in `port_connections`:

| Priority | Method |
|----------|--------|
| 0 | RTL-named primary input port — `grep -cw "input.*\b<net>\b" /tmp/eco_verify_<TAG>_<Stage>.v` — if declared as `input` in the target module → **HIGHEST PRIORITY** |
| 1 | Direct name match in stage PreEco |
| 2 | Trace driver cell in Synthesize → find same cell output in this stage |
| 3 | P&R alias search (partial name, exclude declarations) |
| 4 | Backward cone trace from target DFF `.D` (max 10 hops) |
| — | Unresolved → `UNRESOLVED_IN_<Stage>:<net>` |

**Scan alias rejection — MANDATORY before accepting any resolved net:**
```python
scan_alias_patterns = [r'^test_so\d+', r'^dftopt\d+', r'^scan_\w+', r'^si_\w+']
if any(re.match(p, resolved_net) for p in scan_alias_patterns):
    log(f"REJECTED scan alias {resolved_net} — not a valid functional input")
    resolved_net = None  # force next priority
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

For every `new_logic_dff` entry, resolve ALL pins per stage. Studier-1 records Synthesize only.

**Step A — Classify each pin:** Functional (clock, data, Q) vs Auxiliary (scan SE/SI) from RTL context.

**Step B — Resolve functional pins per stage:**
- Priority 1: `grep -cw "<net>" /tmp/eco_verify_<TAG>_<Stage>.v` — if ≥ 1, use it
- Priority 2: P&R alias (only if Priority 1 absent)
- Priority 3: Structural driver trace

**Step C — Resolve auxiliary pins from neighbour DFF** in same module scope (widen to parent if needed). Never fall back to hardcoded constants.

**GAP-CTS-1 — Verify CP net exists in Route before recording:**
```bash
grep -cw "<resolved_cp_net>" /tmp/eco_verify_<TAG>_Route.v
# If 0 → CP renamed by CTS → find CTS-assigned clock net from neighbour DFF in Route:
zcat <REF_DIR>/data/PostEco/Route.v.gz | awk '/<neighbour_dff>/{p=1} p && /\.CP\s*\(/{print; exit}'
```
Set `cts_clock_renamed: true` when CP differs between PrePlace and Route.

**GAP-20 — SE pin mismatch detection:**
After resolving SE for all stages: if PrePlace SE ≠ Route SE AND neither exists in RTL source (`grep -rw "<se_net>" data/PreEco/SynRtl/` → 0), set `needs_se_tune: true`.

Update `port_connections_per_stage` for all 3 stages.

---

## Check 4 — GAP-14: Wire Declaration Flag (Every new_logic_gate Entry)

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

For every `rewire` entry, run backward cone then forward trace to confirm the cell is in the target DFF's cone.

**Backward cone (max 8 hops):**
- Find target DFF `.D(<net>)` → trace driver chain backward
- If `old_net` appears in chain → `in_backward_cone: true`, `confirmed: true`
- If not found → run forward trace

**Forward trace (max 6 hops):**
```bash
grep -n "( <cell_output_net> )" /tmp/eco_verify_<TAG>_<Stage>.v | grep -v "\.ZN\|\.Z\b\|\.Q\b" | head -5
```
- If forward trace reaches target DFF → `in_backward_cone: true`, `forward_trace_verified: true`
- If forward trace confirms unrelated logic → `confirmed: false`, record destination

**Stage Fallback (GAP-5) — for any stage with no FM result:**
- Take all `confirmed: true` entries from best reference stage (Synthesize → PrePlace → Route)
- Grep each cell in missing stage, verify old_net on pin
- Count = 1 → `confirmed: true`, `source: "<ref>_fallback"`
- Count = 0 → run Priority 4 structural trace (find driver cell in missing stage)
- Still absent → `source: "stage_fallback"` using Synthesize result

---

## Check 11 — needs_named_wire (All Resolved Nets)

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

For every entry where any input net is still `PENDING_FM_RESOLUTION:<signal>`:

1. Check SPEC_SOURCES for rerun fenets result — if resolved → use directly
2. If rerun returned FM-036 or no rerun: run Priority 3 structural driver trace
3. Still unresolved → mark `UNRESOLVABLE:<signal>` (NOT `PENDING_FM_RESOLUTION`)

**CRITICAL: Do NOT leave `PENDING_FM_RESOLUTION` after a rerun returned FM-036.** After the first FM-036, mark `UNRESOLVABLE` and let eco_fm_analyzer decide.

---

## Check 13 — Universal Real-Net Preference (All Entries)

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

## Check 14 — Strategy A/B Fallback for d_input_decompose_failed

For every entry with `d_input_decompose_failed: true` that has no `intermediate_net_strategy` set:

**Strategy A — Structural insertion (preferred):**
```bash
# Trace backward from target_register.D (up to 8 hops)
# Look for compound gates with at least one replaceable input
zcat PreEco/Synthesize.v.gz | awk "/\b<target_register>_reg\b/,/\) ;/" | \
  grep -E "[A-Z]+[0-9]" | head -5
```
- If found → create rewire + new_logic_gate entries using compound gates discovered from PreEco
- Never use MUX2 — causes structural non-equivalence
- Set `intermediate_net_strategy: "structural_insertion"`

**Strategy B — Pivot approach (fallback when A fails):**
- Trace backward from `target_register.D` (max 5 hops) to first net with fanout ≥ 2
- Verify pivot per stage using Priority 1/2 + structural trace
- Validate pivot driver polarity (inverting vs non-inverting)
- Set `intermediate_net_strategy: "pivot"`

Verify gate types exist in PreEco: `grep -cm1 "<gate_type>" PreEco/Synthesize.v.gz > 0`

---

## Step Final — Write Enriched JSON and RPT

**Sort all entries by PASS_ORDER before writing:**
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

Write enriched JSON back to `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`.
Verify `wc -l` ≥ original line count.

**Write verification RPT** to `<BASE_DIR>/data/<TAG>_eco_step3_netlist_verify.rpt`:
```
ECO NETLIST VERIFIER REPORT — TAG=<TAG>
========================================
CHECK 1  GAP-15:        <N> entries checked, <N> corrected
CHECK 2  Per-stage nets: <N> gate entries enriched, <N> UNRESOLVED remaining
CHECK 3  DFF pins:       <N> DFF entries enriched, <N> CTS clock renames found
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

Copy RPT to `AI_ECO_FLOW_DIR/`.

**Cleanup temp files:**
```bash
rm -f /tmp/eco_verify_<TAG>_Synthesize.v /tmp/eco_verify_<TAG>_PrePlace.v /tmp/eco_verify_<TAG>_Route.v
```

**Exit after writing and copying RPT.**
