# ECO Netlist Studier — Collect Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else.

**Role:** For each ECO change, classify the change type, find the correct cell type from PreEco, assign instance names, confirm old_net presence, and write initial skeleton entries to `eco_preeco_study.json`. Per-stage net resolution, gap checks, port boundary analysis, and cone verification are handled by `eco_netlist_verifier` (spawned after this agent exits).

**Inputs:** REF_DIR, TAG, BASE_DIR, path to `<TAG>_eco_rtl_diff.json`, GAP15_CHECK_PATH, and a **per-stage spec source map**:
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

**After filtering: write the complete qualifying list before studying any cell. JSON must contain exactly this many entries.**

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

## Phase 0 — Process new_logic and new_port Changes FIRST

**Before studying any FM-returned cells, process ALL entries in `changes[]` by type:**
- `"new_logic"` / `"and_term"` → process as gate/DFF insertion (steps 0a–0i)
- `"new_port"` → create `port_declaration` study entry (step 0g)
- `"port_connection"` → create `port_connection` study entry (step 0h)
- `"port_promotion"` → create `port_promotion` study entry (step 0i — flat netlist only)
- `"wire_swap"` → skip (handled by FM find_equivalent_nets in Phase 1)

**CRITICAL: For hierarchical PostEco netlists, `new_port` and `port_connection` changes require explicit port list updates and instance connection additions.**

**`port_promotion` — FLAT NETLIST ONLY:** Only when `grep -c "^module " Synthesize.v` = 1. If hierarchical use `port_declaration` + `port_connection` instead.

---

### 0a — Classify the new cell type

From RTL diff `context_line`:
- `always @(posedge <clk>)` with reset/data pattern → **DFF** (sequential)
- `wire/assign <signal> = <expr>` → **combinational gate**
- Bare `reg <signal>` with no always block → skip

### 0b — Identify input signals (basic)

Parse `context_line` to extract clock, reset, data expression (DFF) or input signals (combinational). Verify each in Synthesize PreEco: `grep -cw "<input_signal>" /tmp/eco_study_<TAG>_Synthesize.v`. If count = 0 → record `input_from_change: <N>`.

**Note:** Full per-stage resolution (Priority 0–4) is handled by eco_netlist_verifier. Record what you can from Synthesize here.

### 0b-DFF — Process `d_input_gate_chain` (MANDATORY when present)

For each gate in `d_input_gate_chain` (d001→d00N), create a skeleton `new_logic_gate` entry:
1. Find cell type in PreEco Synthesize matching the gate_function
2. Resolve bit-select names (`A[i]` → check if netlist uses `A_i_` or `A[i]`)
3. Record basic port_connections from Synthesize only
4. If input is `n_eco_<jira>_d<prev>` → set `input_from_change: <prev_gate_id>`
5. If any signal not found → set `d_input_decompose_failed: true`, skip rest of chain

**CRITICAL — seq counter is per-JIRA across ALL DFF chains, not per-chain:**
- Chain 1: eco_<jira>_d001 ... d007
- Chain 2: eco_<jira>_d008 ... (never restarts at d001)

After all chain gates: set DFF entry `port_connections.D = "n_eco_<jira>_d<last>"`. If `d_input_decompose_failed: true`: set `d_input_net = "SKIPPED_DECOMPOSE_FAILED"`, `confirmed: false`.

**GAP-14 — Wire declaration flag:** For each new gate whose output net does not exist in PreEco (`grep -cw "<output_net>" /tmp/eco_study_<TAG>_Synthesize.v` = 0), set `needs_explicit_wire_decl: true`. **Output net ONLY — never set for input nets.**

### 0c — Find suitable cell type from PreEco netlist

**For DFF:** `zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -E "^[[:space:]]*(DFF|DFQD|SDFQD|SDFFQ|DFFR|DFFRQ)[A-Z0-9]* [a-z]" | head -5`

**For combinational gate:** Determine function from RTL expression (`A & B` → AND2, `~A` → INV, etc.), then search PreEco for matching cell pattern.

**MANDATORY — extract actual pin names from PreEco instance (ALL pins):**
```bash
grep -m1 "<cell_type>" /tmp/eco_study_<TAG>_<Stage>.v
```
Parse every `.<PIN>(` — these are the ONLY valid pin names. Never assume pin names from the gate function name.

### CELL OUTPUT PIN TABLE — MANDATORY REFERENCE

| Gate Function | Output Pin | Notes |
|--------------|-----------|-------|
| AND2, AND3, AND4 | `Z` | Non-inverting |
| OR2, OR3, OR4 | `Z` | Non-inverting |
| MUX2, MUX4 | `Z` | NOT `ZN` |
| XOR2 | `Z` | Non-inverting |
| INV | `ZN` | Inverting |
| NAND2, NAND3, NAND4 | `ZN` | Inverting |
| NOR2, NOR3, NOR4 | `ZN` | Inverting |
| XNOR2 | `ZN` | Inverting |
| IND2, IND3 | `ZN` | AND-NOT (inverting) |
| DFF, SDFF | `Q` | Sequential |

Verify output pin by examining an actual instance from PreEco — always authoritative over this table.

### 0d — Assign instance and output net names

**For `new_logic_dff`:**
```
instance_name = <target_register>_reg
output_net    = <target_register>
```

**For `new_logic_gate` (including D-input chain gates):**
```
instance_name = eco_<jira>_<seq>   (e.g., eco_<jira>_d001)
output_net    = n_eco_<jira>_<seq>
```
Same seq across all 3 stages. Seq counter is global across all chains.

### 0e — Record skeleton entry

**`instance_scope` rules — MANDATORY:**
- Submodule: `instance_scope = "<INST_A>/<INST_B>"`
- Tile root: `instance_scope = ""` (empty string) AND `"scope_is_tile_root": true`
- NEVER leave `instance_scope` as null — use `""` explicitly for tile-root scope

**`instance_scope` for tile-root detection:**
```bash
grep -m1 "^module ddrss_<tile>_t " /tmp/eco_study_<TAG>_Synthesize.v
```
Use trailing space to match the tile-root module name exactly.

Record skeleton entry with: `change_type`, `instance_scope`, `scope_is_tile_root`, `cell_type`, `instance_name`, `output_net`, `port_connections` (Synthesize only), `confirmed: true/false`.

eco_netlist_verifier will add `port_connections_per_stage`, GAP-15 correction, port boundary entries, and consumer cascade entries.

### 0f — Mark wire_swap entries that depend on new_logic outputs

For each `wire_swap` whose `new_token` matches a `new_logic` output net, add `"new_logic_dependency": [<seq>]`.

**MUX select polarity (when `mux_select_gate_function` is non-null in RTL diff):**
Read `mux_select_gate_function` directly → create `new_logic_gate` entry. If null → set `mux_select_gate_function: null` and record `mux_select_i0_net`, `mux_select_i1_net` for eco_netlist_verifier's Check 4c.

### 0g — Process `new_port` changes → `port_declaration` study entries

1. Identify `module_name`, `signal_name` (`new_token`), `declaration_type`, `instance_scope`
2. Detect netlist type: `grep -c "^module " /tmp/eco_study_<TAG>_Synthesize.v` — count > 1 = hierarchical
3. **Implicit wire check:** if `context_line` has only `wire` AND ≥ 2 `port_connection` changes reference it → skip port_declaration, set `no_wire_decl_needed: true` on those port_connection entries, note in entry.
4. If hierarchical: validate module name — `grep -c "^module <module_name>\b"`. If 0 → try `<module_name>_0`. Not found → `confirmed: false`.

### 0h — Process `port_connection` changes → `port_connection` study entries

1. Identify `parent_module`, `instance_name`, `port_name`, `net_name`, `submodule_type`
2. **MANDATORY — Validate `submodule_pattern`:** `grep -c "<submodule_type> <instance_name>" /tmp/eco_study_<TAG>_Synthesize.v`. If 0 → check PrePlace and Route; record per-stage `instance_confirmed` flags.

### 0i — Process `port_promotion` changes → `port_promotion` study entries

Verify net exists: `grep -cw "<signal_name>" /tmp/eco_study_<TAG>_Synthesize.v`. Record with `declaration_type: "output"`, `flat_net_confirmed: true`.

---

## Phase 1 — Process Per Stage (wire_swap FM Results)

For each `wire_swap` change, process FM fenets results per stage.

**Multi-instance handling:** When `instances` is non-null, process each instance's FM results independently.

### 1. Read the PreEco netlist (once per stage, reuse across all cells)
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz > /tmp/eco_study_<TAG>_<Stage>.v
```

### 2–3. Find and extract cell instantiation block

Read from the line with the cell name through the closing `);`. Extract all `.portname(netname)` entries.

### 4. Confirm old_net is present

**Step 1 — Try direct old_net name:** `grep -c "\.<pin>(<old_token>)" /tmp/eco_study_<TAG>_<Stage>.v`
- If ≥ 1 → `"old_net": "<old_token>"`, `"confirmed": true`

**Step 2 — If not found, check for HFS alias on that pin.** Read actual net on `<pin>`, verify alias via parent module port connection. If confirmed: set `"old_net_alias": true`, `"old_net_alias_reason"`.

If neither found: `"confirmed": false`. eco_netlist_verifier will run stage fallback (GAP-5).

### 4b. Basic new_net reachability

**Priority 1 — Direct name:** `grep -cw "<new_token>" /tmp/eco_study_<TAG>_<Stage>.v`. If ≥ 1 → `"new_net": "<new_token>"`.

**Priority 2 — HFS alias (only if direct absent):** Set `"new_net_alias": "<alias>"`, `"new_net_reachable": true`. If not found: `"new_net_reachable": false`.

Backward cone and forward trace verification are handled by eco_netlist_verifier Check 10.

### 4d. Timing estimate (Synthesize only)

Compare driver structure of `old_net` vs `new_net` in PreEco Synthesize. Record:
```json
"timing_lol_analysis": {
  "old_net_driver": "<cell> (<type>)",
  "new_net_driver": "<cell> (<type>)",
  "old_net_fanout": N, "new_net_fanout": N,
  "timing_estimate": "BETTER|LIKELY_BETTER|NEUTRAL|RISK|LOAD_RISK|UNCERTAIN"
}
```

### 5. Verify output count before moving to next stage
```
Qualifying list had: N cells
Output JSON has:     N entries  ← must match
```

### 6. Cleanup temp files (after all stages complete)
```bash
rm -f /tmp/eco_study_<TAG>_Synthesize.v /tmp/eco_study_<TAG>_PrePlace.v /tmp/eco_study_<TAG>_Route.v
```

---

## Output JSON

Write `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`.

**`change_type` translation:** `wire_swap` → `rewire`; `new_logic` → `new_logic_dff` or `new_logic_gate`.

**Sort each stage array by PASS_ORDER before writing:**
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

Verify output is non-empty with at least one confirmed entry.

**Write collect RPT** to `<BASE_DIR>/data/<TAG>_eco_step3_collect.rpt`:
```
ECO NETLIST STUDIER — COLLECT PASS
TAG=<TAG>  |  JIRA=<JIRA>  |  TILE=<TILE>
================================================================================
PHASE 0 — new_logic / port entries:
  new_logic_gate:   <N>  (confirmed: <N>  excluded: <N>)
  new_logic_dff:    <N>  (confirmed: <N>  excluded: <N>)
  port_declaration: <N>  (confirmed: <N>  excluded: <N>)
  port_connection:  <N>  (confirmed: <N>  excluded: <N>)
  d_input_chains:   <N> chains  <N> gates total  (<N> decompose_failed)

PHASE 1 — wire_swap rewire entries:
  [Synthesize]  <N> qualifying cells  confirmed: <N>  excluded: <N>
  [PrePlace]    <N> qualifying cells  confirmed: <N>  excluded: <N>
  [Route]       <N> qualifying cells  confirmed: <N>  excluded: <N>

EXCLUDED entries (need verifier or manual fix):
  <cell/signal>: <reason>
  ...

NOTE: port_connections_per_stage not yet resolved — eco_netlist_verifier handles this.
================================================================================
```
Copy RPT to `AI_ECO_FLOW_DIR/`.

**After writing, exit immediately.** eco_netlist_verifier is spawned by ORCHESTRATOR next.

---

## Confirmed-false Notes

- Cell not found in PreEco: `"confirmed": false, "reason": "cell not found in PreEco netlist"`
- Old net not on expected pin: `"confirmed": false, "reason": "pin <pin> has net <actual_net> not expected <old_net>"`
- Multiple instances: `"confirmed": false, "reason": "AMBIGUOUS — multiple occurrences"`
- Name mangling: retry with `"<cell_name>_reg"` before marking confirmed: false
- All stages have no FM results: mark all confirmed: false for manual review
