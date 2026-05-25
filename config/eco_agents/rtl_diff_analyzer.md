# RTL Diff Analyzer — ECO Flow Specialist

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before doing anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in the parent orchestrator: `config/eco_agents/STUDY_ORCHESTRATOR.md` **§STEP 1 — RTL Diff Analysis**. You handle exactly what is documented there — no more, no less. Do NOT read other STEP sections; they belong to other agents.


**You are the RTL diff analyzer.** Extract ALL changes between PreEco and PostEco RTL, classify them, determine which gate-level nets to query, and build VERIFIED hierarchy paths.

**Inputs:** REF_DIR, TILE, TAG, BASE_DIR

---

## CRITICAL: Instance Names vs Module Names

**ALWAYS use instance names in hierarchy paths, NEVER module names.**

- Module name: what appears after `module` keyword in RTL (e.g., `module_b`)
- Instance name: what appears on instantiation line (e.g., `INST_B` in `module_b INST_B (...)`)
- Hierarchy path uses instance names: `<INST_A>/<INST_B>/signal_name` ✓
- WRONG: `<module_name_A>/<module_name_B>/signal_name` ✗

---

## Step A — Run RTL Diff

```bash
cd <REF_DIR>
diff -rq --exclude="*.vf" --exclude="*.vfe" --exclude="*.d" data/PreEco/SynRtl/ data/SynRtl/
```

For each file that differs, run full diff:
```bash
diff <REF_DIR>/data/PreEco/SynRtl/<file> <REF_DIR>/data/SynRtl/<file>
```

---

## Step B — Classify Each Change

For each diff hunk, classify as ONE of:

| Type | Description | Example |
|------|-------------|---------|
| `wire_swap` | Existing signal replaced by different signal | `old_sig` → `new_sig` in expression |
| `and_term` | New AND condition added to existing expression | `A & ~B` → `A & ~B & ~C` |
| `new_port` | New `input`/`output` port declaration added — **MUST set `declaration_type` ∈ `{input, output, wire}`**, never null. `wire` is for parent-scope connector signals (no port-list addition). If the same `(module_name, new_token)` already appears as a `wire`-only declaration in the parent tile, do NOT emit a duplicate `new_port` entry. | `input new_port_name` |
| `port_promotion` | Existing local `reg` promoted to `output reg` | `reg X` → `output reg X` |
| `new_logic` | New wire/always/assign/instance added | New always block |
| `port_connection` | Port connection added on module instance | `.new_port(net)` added |
| `enable_swap` | The clock-enable / write-enable condition of an existing DFF changes (the `else if (<condition>)` guard around the DFF assignment changes to a new expression). Emit this as a SEPARATE change entry alongside any `wire_swap`/`and_term` for the D-input if both change in the same always block. Fields: `old_enable_net`, `new_enable_net`, `new_enable_gate_chain` (MUX/AND/OR gates implementing the new enable condition), `dff_clock`. Step 2 queries `old_enable_net` via fenets to locate the CE pin; Step 3 emits a `rewire` entry for pin=CE/EN/WE on the discovered DFF cell. | `else if (en_old)` → `else if (en_new)` |

**Bus flag rule — `is_bus_dff` vs `is_bus_gate` (MANDATORY — never mix):**

| Context | Correct flag |
|---------|-------------|
| `new_logic` adding `reg [N:0] sig` | `is_bus_dff: true` |
| `new_logic_gate` adding `wire [N:0] sig = expr` | `is_bus_gate: true` |
| `wire_swap` whose `d_input_gate_chain` produces a bus-width net | `is_bus_gate: true` — the chain contains bus-width combinational cells |
| Any `wire_swap` | **NEVER `is_bus_dff: true`** — wire_swap never inserts sequential DFFs |

`is_bus_dff` is exclusively for sequential register insertions. `is_bus_gate` covers all bus-width combinational gate expansion, including gate chains embedded in `wire_swap` changes.

**Bus combinational gate detection (MANDATORY for `new_logic_gate` and `wire_swap` with bus chain):**

When a diff hunk adds a `wire` assignment with a range declaration (`` wire [`MACRO] X = expr `` or `wire [N:0] X = expr`), classify as `new_logic_gate` and additionally set `is_bus_gate: true` and `bus_width_expr: "<MACRO_or_integer>"`. Similarly, when a `wire_swap` change has a `d_input_gate_chain` that produces a bus-width intermediate net (identified by `bus_width_expr` on the new_token), set `is_bus_gate: true` (not `is_bus_dff`) on the wire_swap entry. In gate-level, synthesis expands this into N individual gate cells (one per bit). eco_netlist_studier calls `eco_resolve_bus_width.py` then emits N per-bit gate entries (each with `is_bus_gate_bit: true`, `bus_bit_index`, and bit-indexed input/output nets). Scalar inputs to the gate (e.g. a 1-bit select signal) are shared across all N entries unchanged; bus-width inputs get `[bit]` suffix per entry.

**Bus register detection (MANDATORY for `new_logic`):**

When a `new_logic` diff hunk adds a register with a range declaration (`reg [N:0] sig` or `` reg [`MACRO] sig ``), set:
- `is_bus_dff: true`
- `bus_width_expr: "<MACRO_name or integer N>"` — the range expression verbatim
- Skip D-input gate chain decomposition (bus DFFs pipeline a bus signal directly, no combinational cone)
- Skip `d_input_expected_function` (not applicable)
- `d_input_gate_chain: []`
- `d_input_resolved_net`: the source bus signal name from the always block D-assignment

eco_netlist_studier calls `eco_resolve_bus_width.py --macro <bus_width_expr>` to determine the integer N, then calls `eco_emit_dff_entry.py --bus-width N` to emit N per-bit DFF entries.


**`port_promotion` classification (Gap 1):**
When a diff shows BOTH:
- Old line: `reg <signal>;` (local register declaration)
- New line: `output reg <signal>;` (promoted to output port)

**MANDATORY disambiguation — `port_promotion` vs `new_port` (output):**

| Diff hunk type | Old line present? | New declaration | Correct classification |
|---|---|---|---|
| Change (`c`) | YES — `reg <signal>` | `output reg <signal>` | `port_promotion` |
| Addition (`a`) | NO — pure addition | `output wire/reg <signal>` | `new_port` (declaration_type=output) |
| Any | — | `output wire <signal>` | **always `new_port`** — `output wire` is never a promotion; a promotion only changes an existing `reg` to `output reg` |

A pure addition in the diff (no `<` old line, only `>` new line) means the signal **did not exist before** — classify as `new_port` (output). `port_promotion` ONLY applies when an existing `reg` is being CHANGED to `output reg` (diff shows a `c` hunk with both old and new lines).

Classify as `port_promotion` only when both conditions hold. The key property: **the gate-level net ALREADY EXISTS in the flat PreEco netlist** (it was a reg driving internal logic). No new cell insertion is needed. The promotion only affects port connectivity at module boundaries — in a flat netlist, the net is already accessible everywhere. Record `flat_net_exists: true`.

**CRITICAL — `port_promotion` is flat netlist only.** If the PostEco netlist is hierarchical (contains multiple `module` definitions — check `grep -c "^module " Synthesize.v.gz`), never classify as `port_promotion`. Use `new_port` (output) + `port_declaration` instead. Hierarchical netlists have explicit per-module port lists that must be updated.

**`and_term` classification (Gap 4):**
When a `wire_swap` diff adds an extra `& ~<NewSignal>` term to an existing expression but does NOT change the core logic:
- Old: `<expr_A> & ~<expr_B>`
- New: `<expr_A> & ~<expr_B> & ~<NewSignal>`

Classify as `and_term` (NOT `wire_swap`). `old_token` = the final output net of the existing expression, `new_token` = `<NewSignal>` (the new term being added). The applier will: find the existing gate driving the output net, insert a new AND/NAND gate in series with `~<NewSignal>` as additional input.

**MANDATORY — Record old driver polarity for `and_term` chains:**

After FM resolves the gate driving `old_token`, record in the change entry:
```json
"old_driver_cell_type": "<cell_type_from_FM>",
"old_driver_inverting": true|false
```
`old_driver_inverting` = best-effort at Step 1: grep PreEco Synthesize for the cell driving `old_token`, record `old_driver_cell_type`. Set `old_driver_inverting: true` if cell type starts with an inverting prefix (AOI/OAI/NOR/NAND/INV/NR/ND). This is a placeholder — **the definitive polarity is determined in Step 3 from the FM `(+)/(-)` result** (see `eco_netlist_studier.md` and_term gate chain rule).

**Gate chain selection (Step 3 uses FM polarity, not cell type prefix):**

The chain must compute: `output = old_expression & ~new_term`. Cell type prefix alone is unreliable — AOI12/INR3 can output `+old_expression` at FM `(+)` polarity. The studier reads the Step 2 fenets qualifying impl line polarity for the old driver to determine the correct gate:

| FM polarity on old driver | Renamed output value | Correct gate |
|---|---|---|
| `(-)` negative | `~old_expression` | `NOR2(renamed, new_term)` ✓ |
| `(+)` positive | `+old_expression` | `INR2(renamed, new_term)` ✓ |

**CRITICAL — `and_term` vs `wire_swap + intermediate_net_insertion`:**  
`and_term` is ONLY for simple single-gate gating (one new term added to one existing expression). If the RTL diff shows **multiple new conditions prepended before the old expression as a default fallback** (priority chain pattern: `new_cond_1 ? val1 : new_cond_2 ? val2 : <old_expr>`), this is **NOT `and_term`** — it MUST be classified as `wire_swap` with `fallback_strategy: "intermediate_net_insertion"`. The key test: if the `new_condition_gate_chain` requires MUX2 gates, it is `wire_swap + intermediate_net_insertion`, not `and_term`. Misclassifying as `and_term` causes the studier to do a simple gate modification and skip the full MUX cascade — the ECO logic is never applied.

For each change record:
```json
{
  "file": "<rtl_file.v>",
  "module_name": "<declaring_module>",
  "change_type": "<wire_swap|and_term|new_port|port_promotion|new_logic|port_connection|enable_swap>",
  "old_token": "<old_signal_name>",
  "new_token": "<new_signal_name>",
  "context_line": "<full RTL line containing the change>",
  "target_register": "<register_name>",
  "target_bit": "<[N] or null>",
  "flat_net_exists": "<true if port_promotion — net already in flat PreEco netlist | false otherwise>",
  "flat_net_name": "<actual net name in flat netlist for new_port inputs — resolved in Step C | null>",
  "instances": ["<INST_A>", "<INST_B>"],
  "is_bus_dff": "<true when new_logic declares a vector register (reg [N:0] or reg [`MACRO] sig) — N individual DFF cells needed in gate-level | false/null otherwise>",
  "bus_width_expr": "<the range macro name (e.g. UMC__WDBPTR_RANGE) or literal integer N — used by eco_resolve_bus_width.py | null>"
}
```

**`instances` field (Gap 2):** If the declaring module has multiple instances in the parent (e.g., two `<child_module>` instances `<INST_A>` and `<INST_B>`), list ALL instance names. Step C detects this and Step D generates separate `nets_to_query` entries for each instance. Leave as `null` if only one instance.

**`target_register` and `target_bit` extraction (MANDATORY for wire_swap):**

From `context_line`, extract the LHS register being assigned — this is the TARGET REGISTER that `eco_netlist_studier` uses for backward cone verification.

- Pattern: `<register_name>[<N>]  <=` or `<register_name>  <=`
- Example: `<TargetReg>[<N>]   <=` → `target_register: "<TargetReg>"`, `target_bit: "[<N>]"`
- Example: `<TargetReg>  <=` → `target_register: "<TargetReg>"`, `target_bit: null`
- If multiple always blocks changed (different bits of same register), record each separately with its own `target_bit`
- For `new_port`, `new_logic`, `port_connection` types: set both to `null`

**`dff_clock` extraction (MANDATORY for `new_logic_dff` change_type, recommended for all `target_register` entries):**

For each new DFF (and ideally for any `target_register` change), extract the clock signal from the enclosing `always @(posedge <clk> ...)` or `always @(clocked_on <clk> ...)` block. Step 2 fenets uses this to build clock-domain queries; Step 3 studier uses it to pick the per-stage CP wire — without it, studier has to guess and may pick a wrong-domain CTS-rebalanced clock in P&R stages.

Algorithm:
1. Locate the `always @(posedge <X>` or `always @(<X> or <Y>)` block enclosing the new register assignment
2. The first signal after `posedge` is the clock (if it's `<X>`, that's the clock; for sync/async resets like `posedge clk or negedge rst_n`, the clock is the first one)
3. Record as `dff_clock: "<clk_signal>"`
4. If multiple always blocks affect the same register at different bits → record per-change

```json
"dff_clock": "<clk_signal_name>",   // for new_logic_dff entries
                                    // null when not applicable (combinational, port_connection, etc.)
```

Failure mode if missing: studier has to infer the clock from neighboring DFFs in the netlist; in P&R stages the inferred clock may be a CTS-rebalanced antenna-fix net from the wrong clock tree. The new DFF then ends up on a different clock domain in Route vs Synth → FM logical mismatch.

**`module_name`** = the module that **declares** the changed signals as `reg` or `wire` — NOT necessarily the module in the changed file. The changed file's module is only the starting candidate. Step C will verify whether the signals are truly declared (`reg`/`wire`) in that module or merely passed through as input/output ports. If they are only ports, `module_name` must be updated to the parent module where the `reg`/`wire` declaration lives. Leave this field as the changed file's module initially — Step C is responsible for correcting it if needed.

---

## Step C — Hierarchy Tracing (MANDATORY)

**Trace to the DECLARING module, not the usage module.** The signal may pass through ancestors as a port; the hierarchy path MUST start at the module declaring it as `reg`/`wire`. Stopping too shallow makes the scope filter too wide.

For each signal in a change:

**1. Find the DECLARING module** — anchored grep finds declarations, not usages/port-connections:
```bash
grep -rn "^\s*reg\b.*<signal>\|^\s*wire\b.*<signal>" <REF_DIR>/data/PreEco/SynRtl/
```
Start with the changed file's module. If `reg`/`wire` of `<signal>` is found there → declaring module = changed file ✓. Else `<signal>` is only `input`/`output` in the changed file → declaring module is a PARENT; the file containing the declaration is the declaring module. **Update `module_name` in the JSON** to the declaring module.

Example: diff in `rtl_<module_X>.v` but `<signal>` is `reg` in `rtl_<declaring_module>.v` → `module_name = <declaring_module>`; hierarchy starts at the declaring module's instance, NOT the changed module's instance.

**2. Find that module's INSTANCE NAME in its parent:**
```bash
grep -n "<module_name>" <REF_DIR>/data/PreEco/SynRtl/rtl_<parent_module>.v
# extract instance from `<module_b> <INST_B> (`
```

**3. Repeat upward until parent IS the tile** (`<TILE>`). Stop there — tile is the boundary, NOT included in the path.

**4. Build path from instance names — declaring module's instance up to (but NOT including) the tile.**
E.g. tile → `<INST_A>` (`<module_A>`) → `<INST_B>` (`<module_B>`), signal declared in `<module_B>` → path `<INST_A>/<INST_B>/<signal>`; `hierarchy = ["<INST_A>","<INST_B>"]`.

**Never include the tile name in the path.** FM auto-scopes under the tile; including it produces a doubled prefix → FM-036 on every net. **Rule: `net_path[0]` MUST NEVER equal `<TILE>`.**

**5. Self-verify** — confirm reg/wire decl exists in the declaring file; instance name is right at each level; `net_path` doesn't start with the tile name. If decl missing in your chosen module → stopped too high, go deeper. If `net_path` starts with `<TILE>` → went too far up, drop the first component.

**6. Multiple instances of the same module (Gap 2)** — after identifying the declaring module, check the parent for repeats:
```bash
grep -c "<declaring_module>" <REF_DIR>/data/PreEco/SynRtl/rtl_<parent>.v
grep -n "<declaring_module>" <REF_DIR>/data/PreEco/SynRtl/rtl_<parent>.v   # extract all instances
```
Record all instance names in `instances: ["<INST_A>","<INST_B>"]`. Step D produces separate `nets_to_query` per instance.

**7. Resolve flat net name for new_port inputs (Gap 3)** — for each `new_port` input, find the parent net actually driving the port per instance:
```bash
grep -A 50 "<declaring_module> <INST_X>" <REF_DIR>/data/PreEco/SynRtl/rtl_<parent>.v | grep "<new_port_name>"
# If absent in PreEco (new connection), check PostEco data/SynRtl/ instead.
# Extract `.new_port_name(<actual_net>)` → flat_net_name
```

Record per-instance map:
```json
"flat_net_name_per_instance": {"<INST_A>": "<net_for_A>", "<INST_B>": "<net_for_B>"}
```

Required for `and_term` ECOs where a new AND term maps to an existing signal — the applier needs the actual flat net.

**8. Update `module_name` in JSON + RPT if declaring module differs from changed file** — also add a `Notes:` line explaining the redirect. Wrong `module_name` makes the hierarchy start at the wrong level → FM-036 / wrong scope filtering in Step 3.

---

## Step D — Net Selection

**`nets_to_query` building is owned by Step 2 (`eco_fenets_runner`).** Skip in this step. The patterns below stay for Step 2's reference (reads `changes[]` directly).

For EACH change, determine which gate-level nets reveal WHERE to ECO and HOW to rewire.

**Per change_type:**
- **`wire_swap`:** query both `old_token` (find current driver) and `new_token` (confirm exists). Special case: `new_token` is a NEW gate output — see MUX-select polarity below (resolve in Step 1, not the studier).
- **`and_term`:** query `old_token`. Gate input scope rule — the new term is inserted INSIDE the declaring module; `gate input` must use the in-module name: if `new_token` is a `new_port` of the declaring module → use the PORT NAME (do NOT use parent-scope `flat_net_name`); if existing wire/reg → use it directly. Record `and_term_gate_input: "<port_name_inside_module>"`.
- **`new_port` / `port_connection`:** **skip FM query** — wiring change handled by studier from `flat_net_name`.
- **`port_promotion`:** **skip FM query** — flat net already exists; set `flat_net_exists: true`.
- **`new_logic`:** skip the FM query for the new register's output (doesn't exist in PreEco). Instead query an EXISTING signal the D-input depends on (enable / driving signal from `context_line`). If D-input is entirely new with no existing reference, leave `nets_to_query` empty for this change.
- Avoid querying flip-flop Q outputs.

### MUX-select polarity (resolved here, NOT in the studier)

When `wire_swap` inserts a new MUX-select gate, the gate function MUST be derived from PreEco I0/I1 port mapping in Step 1. Deferring to the studier (which would derive from RTL condition text) is the persistent failure mode.

**D-MUX-1 — Find the MUX cell:**
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz > /tmp/preeco_study_rtldiff_Synthesize.v
grep -n "<target_register>_reg\b" /tmp/preeco_study_rtldiff_Synthesize.v | head -5
# trace backward from .D pin to MUX whose .Z drives the chain
grep -n "\.Z\b\s*(\s*<d_input_net>\s*)" /tmp/preeco_study_rtldiff_Synthesize.v | head -5
```

**D-MUX-2 — Read MUX `.I0` and `.I1`:** `grep -A8 "<mux_cell_name>"` → record `i0_net`, `i1_net`.

**D-MUX-3 — Old select driver inverting? (ONE question — do NOT read the new condition yet):**
```bash
grep -n "\.Z[N]\?\s*(\s*<old_select_net>\s*)" /tmp/preeco_study_rtldiff_Synthesize.v | head -3
```
Inverting prefixes (`NOR`/`NR`/`INR`/`INV`/`NAND`/`ND`/`IND`) → output LOW when inputs HIGH → **old S=0 when condition TRUE**. Non-inverting (`AND`/`AN`/`OR`/`BUF`) → **old S=1 when condition TRUE**. Record `old_S_when_condition_true`. STOP.

**D-MUX-4 — Commit to gate direction (still without reading the condition):**
- `old_S=0` → MUX picked I0 on TRUE → I0 = true-branch → new gate = `NOT(condition)`
- `old_S=1` → I1 = true-branch → new gate = `condition itself`

Record direction. STOP.

**D-MUX-5 — Now read the condition** from `context_line`, apply the committed direction (negate via De Morgan if `NOT(condition)`), map to a standard gate (AND2/NAND2/OR2/NOR2/AND3/…).

**D-MUX-5b — JSON (ALL fields MANDATORY):**

```json
"mux_select_gate_function": "<AND2|NAND2|OR2|NOR2|...>",
"mux_select_i0_net": "<net_on_I0_pin>",
"mux_select_i1_net": "<net_on_I1_pin>",
"mux_select_branch_true_on": "I0|I1",
"mux_select_old_driver_cell_type": "<first uppercase token of old select driver>",
"mux_select_old_driver_inverting": true|false,
"mux_select_old_S_when_condition_true": 0|1,
"mux_select_reasoning": "<one sentence: driver cell + inverting → old_S → branch → gate>"
```

**`mux_select_i{0,1}_net` source rule** — when the input is a `new_port` (no flat net yet), populate it DIRECTLY from `new_select_inputs[k]` (symbolic RTL name). Do NOT flat-net-resolve — there's nothing to resolve, and the resolver grabs unrelated CTS-renamed cone wires → wrong studier inputs → FM logical mismatch.

```python
mux_select_i0_net = new_select_inputs[0] if new_select_inputs_from_change[0] else <flat-net of existing>
# same for i1
```

Cross-check before writing JSON: `mux_select_i{0,1}_net == new_select_inputs[k]` when the corresponding flag is true. Step 1 Check 27 enforces.

Set `mux_select_polarity_pending: false`.

**D-MUX-6 — Self-consistency (MANDATORY; ANY fail → discard and retry from D-MUX-3):**
1. `mux_select_old_driver_inverting == true` iff cell type starts with an inverting prefix (`NOR`/`NR`/`INR`/`INV`/`NAND`/`ND`/`IND`/`XNOR`/`XNR`).
2. `old_S_when_condition_true == 0` iff `inverting==true`.
3. `branch_true_on == "I0"` iff `old_S==0` else `"I1"`.
4. Evaluating the chosen gate at `new_condition=TRUE` MUST equal `old_S_when_condition_true`.
5. If `mux_select_reasoning` contains backtracking words (`wait`/`actually`/`re-analyz`/`correcting`/`inverts`) → unstable derivation, retry.

Cleanup: `rm -f /tmp/preeco_study_rtldiff_Synthesize.v`.

**MUX cell not found after 5 hops:** set `mux_select_polarity_pending: true` and `mux_select_gate_function: null` — studier attempts Step 4c-POLARITY fallback. Do NOT guess from the RTL condition.
- For `and_term`: query `old_token` (the output net of the existing expression) to find the gate driving it. **CRITICAL — `and_term` gate input scope rule:** The new AND term is inserted as a gate INSIDE the declaring module. The gate input net must be the name as it appears INSIDE that module:
  - If the new term (`new_token`) is a `new_port` on the declaring module → gate input = the PORT NAME (`new_token`) as declared in the module header. Do NOT use `flat_net_name` (parent-scope net) as the gate input — `flat_net_name` is the connected net in the PARENT, invisible inside the child module.
  - If the new term is an existing wire/reg in the declaring module → gate input = the wire/reg name directly.
  - Record `and_term_gate_input: "<port_name_inside_module>"` explicitly in the JSON and use this (not `flat_net_name`) in nets_to_query reason and eco_netlist_studier guidance.
- For `new_port`: **skip FM query** — new input ports connect to existing nets (resolved as `flat_net_name` in Step C); no gate-level net to find equivalents for
- For `port_promotion`: **skip FM query entirely** — the net ALREADY EXISTS in the flat PreEco netlist under the signal's original name; `flat_net_exists: true`; the studier will verify existence directly
- For `new_logic`: skip the FM query for the NEW register itself — its output net does not exist in the PreEco netlist and FM cannot find equivalents for it. Instead, query any EXISTING signal that the new register's D-input depends on (the enable signal or the driving data signal from the RTL context_line). This gives eco_netlist_studier the gate-level scope so it can find where to insert the new DFF. If the D-input expression is entirely new with no existing signal reference, leave `nets_to_query` empty for this change — the studier will use the declaring module's gate-level scope directly.
- For `port_connection`: **skip FM query** — port connections are wiring changes handled by the studier using `flat_net_name` resolved from RTL
- For `enable_swap`: query `old_enable_net` (locates the gate-level CE/EN/WE pin to rewire). Also query each leaf input in `new_enable_gate_chain[]` that is not an `n_eco_*` net (per-stage rename resolution). **Do NOT query the target register** — same rule as `new_logic`.
- **Avoid querying flip-flop Q outputs** — focus on driving nets and inputs

**Per-instance net generation (Gap 2):** When `instances` field has multiple values, generate SEPARATE `nets_to_query` entries for each instance. Each entry uses the instance-specific hierarchy path:

```json
{ "net_path": "<INST_A>/<signal>", "hierarchy": ["<INST_A>"], "instance": "<INST_A>", "reason": "..." }
{ "net_path": "<INST_B>/<signal>", "hierarchy": ["<INST_B>"], "instance": "<INST_B>", "reason": "..." }
```

The `instance` field allows Step 3 to process each instance's cells independently and apply different `flat_net_name` values (e.g., different cross-connections per instance).

**Bus signals:** If `old_token` or `new_token` is declared as `reg [N:0] SignalName`, generate BOTH variants for that signal:
- `<INST_A>/<INST_B>/SignalName` (may work in some FM targets)
- `<INST_A>/<INST_B>/SignalName_0_` (gate-level bit-indexed form for bit 0)

Pass BOTH to find_equivalent_nets — FM-036 on one, the other may succeed.

### Step D-POST — Add condition_inputs_to_query signals to nets_to_query (MANDATORY)

**This step is MANDATORY and must run AFTER Step D (nets_to_query generation) completes.** Do not skip it. Do not merge it into E4d. It is a separate step.

Scan every change in `changes[]`. For any change that has a non-empty `condition_inputs_to_query` list, add one `nets_to_query` entry per signal so FM resolves the gate-level name in Step 2:

```python
for change in rtl_diff["changes"]:
    for ci in change.get("condition_inputs_to_query", []):
        signal = ci["signal"]   # e.g., "<condition_input_signal>"
        scope  = ci["scope"]    # e.g., "<declaring_module>"
        # Build hierarchy path: use the declaring module's instance hierarchy from RTL diff
        hierarchy = change.get("instances") or [scope]
        net_path  = "/".join(hierarchy) + "/" + signal
        rtl_diff["nets_to_query"].append({
            "net_path": net_path,
            "hierarchy": hierarchy,
            "reason": f"condition gate input '{signal}' not found by name in PreEco gate-level — FM resolves synthesis-renamed net",
            "is_condition_input_resolution": True,
            "original_signal": signal
        })
```

**CHECKPOINT:** After this step, verify `nets_to_query` count increased by the number of `condition_inputs_to_query` entries across all changes. If count is unchanged but `condition_inputs_to_query` was non-empty → this step was skipped → run it again.

The studier reads these FM results in Step 0c-5: when a chain entry has `"PENDING_FM_RESOLUTION:<signal>"` as an input, it substitutes the gate-level net name returned by FM for that signal.

**CRITICAL — `target_register` is NEVER queried via find_equivalent_nets.** `target_register` (the LHS register of the changed assignment) is only recorded in the JSON for Step 3 backward cone verification. Do NOT add it or any bus variant of it to `nets_to_query`. Only `old_token` and `new_token` (and their bus variants if applicable) go into `nets_to_query`.

---

### Step D-IMPLICIT-WIRE — Detect implicit wire chains (MANDATORY, run after all changes classified)

**Purpose:** Prevent eco_applier from adding explicit `wire <net>;` declarations for nets that Verilog already creates as implicit wires from port connections — which causes FM-599 ABORT_NETLIST.

An implicit wire chain occurs when the same `new_token` net appears in 2 or more `port_connection` changes within the **same parent module** (`module_name` field). One connection drives the wire (output port of one child instance into the parent scope), another consumes it (input port of a different child instance). Verilog creates the wire implicitly — no explicit declaration is needed or allowed.

```python
from collections import defaultdict

# Group port_connection changes by (module_name, new_token)
port_conn_by_net = defaultdict(list)
for change in changes:
    if change["change_type"] == "port_connection":
        key = (change["module_name"], change["new_token"])
        port_conn_by_net[key].append(change)

# Any net with 2+ port_connection entries in the same parent module = implicit wire
for (parent_module, net), conn_list in port_conn_by_net.items():
    if len(conn_list) >= 2:
        for c in conn_list:
            c["implicit_wire"] = True
            c["no_wire_decl_needed"] = True
        # Also mark any port_declaration entry for this net as skip: true
        # eco_applier must NOT add 'wire <net>;' for implicit wires (GAP-7)
        for c in changes:
            if (c.get("change_type") == "port_declaration"
                    and c.get("module_name") == parent_module
                    and c.get("new_token") == net
                    and c.get("declaration_type") == "wire"):
                c["skip"] = True
                c["no_wire_decl_needed"] = True
```

**Also flag single port_connection entries** where the `new_token` net is also the `new_token` of a `port_promotion` change in a child module — the promoted output creates an implicit wire in the parent when connected:
```python
promoted_nets = {c["new_token"] for c in changes if c["change_type"] == "port_promotion"}
for change in changes:
    if change["change_type"] == "port_connection" and change["new_token"] in promoted_nets:
        change["implicit_wire"] = True
        change["no_wire_decl_needed"] = True
```

**Record in RPT for each implicit wire detected:**
```
IMPLICIT WIRE: '<new_token>' in '<module_name>' — created implicitly by port connections; eco_applier must NOT add explicit wire declaration.
  Appears in <N> port_connection entries — Verilog creates this as an implicit wire.
  Any port_declaration entry for this net with declaration_type "wire" is marked skip: true.
  Explicit declaration alongside an implicit wire causes FM-599 ABORT_NETLIST.
```

> **GAP-7 note:** When a net appears as `<new_token>` in ≥ 2 `port_connection` entries within the same parent module scope, it is an implicit wire — `no_wire_decl_needed: true` is set on the net AND any matching `port_declaration` entry is marked `skip: true`. eco_applier must never add `wire <net>;` for implicit wires. There is no scenario where a port-connection-implicit wire requires an explicit wire declaration.

eco_applier reads `no_wire_decl_needed: true` on each `port_connection` entry and skips wire declaration for that net. eco_pre_fm_checker Check F (sub-check F2) is the safety net if eco_applier adds one anyway.

---

### Step D-STAGE-VERIFY — Verify gate chain inputs across all 3 PreEco stages (MANDATORY for new_logic DFFs)

**Purpose:** A gate chain input net found in PreEco Synthesize may not be accessible in PrePlace/Route stages if its driving cell is inside a hard macro that is black-boxed in P&R. Detecting this early lets eco_netlist_studier set `needs_named_wire: true` proactively rather than burning a full FM round to discover it.

For every input net in every `d_input_gate_chain` entry across all `new_logic` changes:

```bash
for each net in gate_chain_entry["inputs"]:

    # Skip — always valid, no stage check needed
    if net in ("1'b0", "1'b1"):
        continue
    if net starts with "n_eco_<jira>_":
        continue   # ECO-inserted intermediate net — present only after ECO is applied

    # Check presence in all 3 PreEco gate-level netlists
    synth_hits    = $(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<net>")
    preplace_hits = $(zcat <REF_DIR>/data/PreEco/PrePlace.v.gz   | grep -cw "<net>")
    route_hits    = $(zcat <REF_DIR>/data/PreEco/Route.v.gz      | grep -cw "<net>")

    missing_stages = []
    if preplace_hits == 0: missing_stages.append("PrePlace")
    if route_hits    == 0: missing_stages.append("Route")

    if missing_stages:
        # Net found in Synthesize but not in P&R stages → hard macro black-box risk
        gate_chain_entry["mode_H_risk"] = true
        gate_chain_entry["missing_in_stages"] = missing_stages
        # Add to RPT:
        # MODE H RISK: net '<net>' found in Synthesize PreEco but absent in <missing_stages> PreEco.
        # Likely driven from inside a hard macro black-boxed in P&R.
        # eco_netlist_studier will set needs_named_wire: true for <missing_stages>.
```

**JSON output per gate chain entry when Mode H risk detected:**
```json
{
  "seq": "<seq_id>",
  "gate_function": "<gate_function>",
  "inputs": ["<net>", "..."],
  "mode_H_risk": true,
  "missing_in_stages": ["PrePlace", "Route"]
}
```

eco_netlist_studier reads `mode_H_risk: true` on the gate chain entry and automatically applies `needs_named_wire: true` for the listed stages — no FM round wasted on Mode H discovery.

---

## Step E — RTL Expression Decomposer (MANDATORY for new_logic DFFs)

For every `new_logic` change that declares a new DFF register, parse its D-input expression from the always block and decompose it into a gate chain. This produces a `d_input_gate_chain` array that allows eco_applier to insert the full combinational D-input logic automatically — no placeholder nets, no manual synthesis needed.

### E1 — Extract the D-input expression

From the `context_line` always block:
- Locate the `else` clause: `else <target> <= <expression>;`
- The expression is the D-input logic

**Synchronous reset detection — DO NOT bake into D-input immediately:**

If `if (<rst_signal>) <target> <= 1'b0;` (or `1'b1` for active-low) is present:
1. Extract `reset_signal = <rst_signal>` and `reset_polarity = "active_high"` (active_low if `~<rst_signal>`)
2. Set `has_sync_reset: true` in the change JSON
3. **Remove the reset term from the D-input expression** — record it separately
4. The D-input expression becomes only the `else` clause logic (no `~<rst_signal>` term)
5. eco_netlist_studier will decide in Step 0c whether to:
   - **Use a DFF cell with explicit reset pin** (preferred — reset signal stays out of combinational cone → immune to CTS BBNet issues on reset nets)
   - **Fall back to baking into D-input** (only if no reset-pin cell exists in PreEco)

```
# CORRECT: separate reset from D-input
has_sync_reset: true
reset_signal:   <rst_signal>
reset_polarity: "active_high"        # or "active_low"
d_input_expr:   <sig_A> & ~<sig_B>  # reset term NOT included

# WRONG (old approach): always bake reset in
d_input_expr:   ~<rst_signal> & <sig_A> & ~<sig_B>  # exposes reset to CTS BBNet
```

```
Example always block (generic):
  if (<rst_signal>) <target_reg> <= 1'b0;
  else              <target_reg> <= <sig_A> & ~<sig_B> & ((<sig_C>[N:0] == <const_K1>) | (<sig_C>[N:0] == <const_K2>));

D-input expression = ~<rst_signal> & <sig_A> & ~<sig_B> & ((<sig_C>[N:0] == <const_K1>) | (<sig_C>[N:0] == <const_K2>))
```

### E2 — Resolve macro constants

If the expression contains backtick macros (`` `CONST_NAME ``), resolve them:
```bash
grep -rn "define.*CONST_NAME" <REF_DIR>/data/SynRtl/*.v <REF_DIR>/data/SynRtl/*.vh 2>/dev/null | head -5
```
Replace each macro with its numeric value before decomposing.

### E2.5 — Boolean simplification BEFORE decomposition (MANDATORY)

Before applying E3, you MUST rewrite the expression to minimize gate count. Each new gate widens the FM cone walk and increases cone-divergence risk across PP/Route stages. A literal text-to-cell decomposition (one INV per negated term, large outer AND) is FORBIDDEN.

**MANDATORY rewrites — apply in order until no rule fires:**

1. **De Morgan push-out (MANDATORY when ≥2 negated terms feed a common AND).** Collect every `~X` term that would otherwise appear as a separate INV cell into a single NOR-N (or OR-N + NR2) gate. The forbidden pattern is "≥2 INV cells whose outputs feed a common ANDN" — Step 1 validator Check 11 FAILs the chain when it detects this. NOR/NAND/AOI/OAI gates absorb negation in their truth table — use them.

2. **Bus equality fold.** For `(B[N:0]==K1) | (B[N:0]==K2)` where K1, K2 differ in 1-2 bits, identify the differing bits and use XOR2/XNOR2 for the equality test instead of decomposing per-bit AND chains.

2b. **Bus constant equality decode — MANDATORY.** For a condition of the form `~(bus[N:0] == K)` (a negated equality against a constant bit pattern): derive each gate input from the bit value in K — if K[i]==0, the NAND input for bit i is `~bus[i]` (requires INV cell for that bit); if K[i]==1, the input is `bus[i]` directly. Combine with a NAND-N gate. **Never substitute IND2/IND3 with raw bus bits** — `IND2(bus[1], bus[0])` computes `~(bus[1] AND bus[0])` which only matches `~(bus[1:0]==2'b11)`, not an arbitrary constant. Mismatch in the constant pattern is a logical error detectable by FM. Step 1 validator Check 9f-BUS-CONST-DECODE FAILs this. Example: `~(bus[1:0]==2'b01)` → `INV(bus[1])` then `ND2(~bus[1], bus[0])` (not `IND2(bus[1], bus[0])`).

3. **Reuse existing inverted signals (MANDATORY for every `~<RTL_signal>` term).** Before emitting any new INV cell, search PreEco for an existing wire whose driver is `INV(.I=<RTL_signal>)`. If found AND the inverted wire is stage-stable (or has known per-stage rename in fenets map), emit `inputs_per_stage` referencing the existing wire instead of inserting a new INV. The reuse claim MUST be backed by `inputs_per_stage[<stage>].use_existing_wire: true` for both PrePlace AND Route — Synth-only reuse does not satisfy the rule because PP/Route are where cone divergence happens.

4. **Compound cell preference.** When a sub-expression fits a library compound cell (OAI21, AOI22, NR2, NAND4, NR3, etc.), use the compound in one entry instead of decomposing into simple gates.

**Output requirements:**
- Set `simplification_applied: true` and list every applied rewrite in `simplification_log`.
- The chain MUST contain the reset signal when `reset_baked_in_d_input: true` (Check 10).
- The chain MUST satisfy: NEW INV cells on RTL data/reset signals ≤ 1 (Check 9c-MULTI-INV-NO-REUSE) AND total cells ≤ distinct RTL input count (Check 9d).

**On validator FAIL** (Check 9c, 9d, 10, or 11): re-run E2.5 from scratch with the failing pattern in mind. Do NOT bypass by claiming `reuse_existing_wire: true` without populated `inputs_per_stage` for both PP and Route.

### E3 — Decompose into gate chain (bottom-up)

**If the D-input expression has no boolean operators after reset removal** (it is a single net or bit-select like `REG_X[i]`) → emit `d_input_gate_chain: []` and record the source net in `d_input_resolved_net`. Do NOT fabricate a `WIRE` / `BUF` pseudo-entry — `gate_function: "WIRE"` is not a real cell and breaks downstream agents.

Parse the expression recursively. For each sub-expression, assign a gate type:

| RTL sub-expression | Gate function | Notes |
|-------------------|--------------|-------|
| `~A` | INV | Single inverter |
| `A & B` | AND2 | 2-input AND |
| `A & B & C` | AND3 | 3-input AND (or nested AND2s if AND3 unavailable) |
| `A & B & C & D` | AND4 | 4-input AND |
| `A \| B` | OR2 | 2-input OR |
| `A \| B \| C` | OR3 | 3-input OR |
| `A[N:0] == K'b0...0` | NOR-N | All bits zero: NOR of all N bits |
| `A[N:0] == K` (general) | Per-bit INV + AND-N | For each bit i: if K[i]=0 insert INV(A[i]); if K[i]=1 use A[i] directly; AND all N terms |
| `A ? B : C` | MUX2 | |
| Bit-select `A[i]` | Direct net | Use signal directly; gate-level name may be `A_i_` — verify by grep |

**Assign names for D-input gate chain (combinational gates only):** `eco_<jira>_d<seq>` for instances, `n_eco_<jira>_d<seq>` for output nets. Seq starts from `d001` per DFF target register.

**DFF instance naming (different from gate chain):** The DFF itself uses `<target_register>_reg` as instance name and `<target_register>` as Q output net — NOT `eco_<jira>_dff<N>`. This matches the name FM synthesizes from the RTL, enabling auto-matching in `FmEqvEcoSynthesizeVsSynRtl` without `set_user_match`. Record in JSON: `"dff_instance_name": "<target_register>_reg"`, `"dff_output_net": "<target_register>"`.

After decomposition, set `d_input_net: "n_eco_<jira>_d<last>"` (connected to DFF .D pin). Apply §E2.5 simplifications first to minimize chain length.

### E4 — Flag unsupported expressions and attempt intermediate net fallback

If the expression contains arithmetic (`+`, `-`, `*`, `/`), multi-cycle logic, or a complex priority mux chain whose new conditions depend on signals that do not exist in the PreEco netlist → set `d_input_decompose_failed: true`.

**Do NOT immediately mark as MANUAL_ONLY.** First attempt the intermediate net fallback strategy:

#### E4a — Detect if the new conditions are PREPENDED to an existing expression

When the RTL diff shows the OLD expression still present as the last/default condition in the new priority chain (e.g., `new_cond_1 ? val1 : new_cond_2 ? val2 : <old_expression>`):

- The existing gate-level logic already implements `<old_expression>` as some intermediate combinational net (the "pivot net")
- The ECO can insert the new conditions **before** the pivot net without touching the DFF D-input at all
- The DFF D-input (`target_register.D`) remains unchanged; only the pivot net's driver changes

**Set `fallback_strategy: "intermediate_net_insertion"` when this pattern is detected.**

#### E4b — Identify the fallback query signal

Add `target_register` (the DFF output Q signal) to `nets_to_query` with `fallback_for_decompose_failed: true`. The studier traces backward from `target_register.D` to find the pivot net.

#### E4b — Driver Substitution (PRIORITY 0 — check BEFORE compound gate discovery)

The most FM-friendly strategy: never touches the pivot path, no new intermediate wires for FM to trace.

**When to use:** `change_type == "wire_swap"` AND new conditions prepended before an old default expression AND `d_input_decompose_failed: true`. **Do NOT call for `new_logic` / `new_logic_dff` changes** — those insert NEW registers that don't exist in PreEco; the script will return "DFF not found" and waste time. Driver substitution is only for EXISTING registers whose D-input priority chain has changed.

**Target selection — USE THE SCRIPT, do NOT reason manually** (manual tracing produced wrong targets every prior run):

```bash
python3 script/eco_scripts/eco_find_drvsub_target.py \
    --ref-dir <REF_DIR> --register <target_register> --jira <JIRA> \
    --output  data/<TAG>_eco_drvsub_target.json
```

Read `driver_sub_target_net` + `driver_sub_target_cell_type` directly. The script walks pivot → MUX → compound consumers → first stage-stable simple-driver net. Script error (no DFF / no candidate) → fall through to E4c.

**Verify:** `stage_stable: true`; `driver_sub_target_cell_type` not AOI/OAI/MUX (script enforces); use returned `driver_sub_target_net` verbatim.

**Strategy selection — `stage_stable: true`:**

- **All conditions use stage-stable signals** (exist in all 3 PreEco stages, no `phfnn_*`/`N<6+digit>` synthesis-internals) → use `fallback_strategy: "driver_substitution"`.
- **Any condition contains a synthesis-internal signal** (RTL signal not found as a named net in Synth, synthesizes to `phfnn_*`/`N<6+digit>` internals) → use `fallback_strategy: "intermediate_net_insertion"` instead. Keep ALL conditions in the chain including those with synthesis-internal signals — mark them `PENDING_FM_RESOLUTION:<signal>` and add to `condition_inputs_to_query` for Step 2 Mode H resolution. Do NOT drop these conditions.

**Rules (eco_validate_step1.py):** target MUST NOT be the pivot (SEQMAP_NET_*); MUST NOT be synthesis-internal (`N\d{6+}`, `phfnn_*`); MUST exist in all 3 stages; driver MUST NOT be AOI/OAI compound (Check 9g-DRVSUB-CONSUMER-TARGET).

**On valid target — driver_substitution:**
1. Set `fallback_strategy: "driver_substitution"`, `driver_sub_target_net: "<script output>"`, `driver_sub_renamed_to: "ECO_<jira>_net_orig"`.
2. New chain renames target's driver output → `ECO_<jira>_net_orig`; adds compound gates (OA12/OAI21/AN3/ND3) that re-output the original net name.
3. Stage-stable signals only: ALLOWED — new ECO ports from `new_port`/`port_promotion` (may be `PENDING_ECO_PORT`), existing primary inputs, `ctmn_*` ONLY as `ECO_<jira>_net_orig` for the default.
4. **NO MUX2 cascade** — direct driver replacement. MUX cascade is for `intermediate_net_insertion` only.
4a. **Last gate MUST output `driver_sub_target_net`** — restores the original name; otherwise undriven → FM ABORT.
5. Old expression (`ECO_<jira>_net_orig`) feeds the chain as DEFAULT.
6. Pivot net (SEQMAP_NET_*, DFF.D) — untouched.

**On valid target — intermediate_net_insertion (synthesis-internal conditions):**
1. Set `fallback_strategy: "intermediate_net_insertion"`, record `driver_sub_target_net` from script (used only to identify the insertion point net).
2. Keep ALL conditions in `new_condition_gate_chain`. Mark synthesis-internal condition signals as `PENDING_FM_RESOLUTION:<signal>` and add to `condition_inputs_to_query`.
3. Last gate MUST output `driver_sub_target_net` directly — same net as the drvsub script identified. No rename of the original driver needed if using a fresh insertion point.
4. Step 2 (fenets) resolves the PENDING_FM_RESOLUTION signals via Mode H; Step 3 substitutes per-stage equivalents using `port_connections_per_stage`.
5. **Use compound gates (OA12/OAI21/AN3/ND3), NOT MUX2 cascade.** MUX2 cascade creates structural cone divergence from SynRtl synthesis output — FM cannot verify the cone because cut-point DFFs in the MUX select paths become globally unmatched. Compound gates directly match what synthesis produces for the same RTL priority chain → FM verifies cleanly.

**Final gate is a COMPOUND (OA12/OAI21/AO21)** combining condition trigger outputs + `ECO_<jira>_net_orig`. Pattern for 2 stage-stable conditions:
```
INV(Cond1_trigger) → n_eco_<jira>_inv_c1
OA12(Cond2_trigger, ECO_<jira>_net_orig, n_eco_<jira>_inv_c1) → driver_sub_target_net
# = (~Cond1) & (Cond2 | old_expr) → if Cond1:0 elif Cond2:1 else old_expr ✓
```
Chain is INCOMPLETE without this final compound — condition outputs alone compute WHEN, not the combined value.

Script error → E4c.

---

#### E4c — PreEco Compound Gate Discovery (MANDATORY FIRST — before any RTL decomposition)

**HARD RULE — PreEco gates FIRST, RTL decomposition NEVER unless PreEco has zero compound gates:**

Before writing ANY gate chain entry, run this grep against PreEco Synthesize scoped to the declaring module:
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
  awk "/^module <declaring_module>\b/,/^endmodule/" | \
  grep -E "^\s+(OA|OAI|AN3|AN4|ND3|ND4|NR3|INR3|IAOI)[A-Z0-9]" | head -20
```
- **If the grep returns ANY compound gates** → you MUST build the gate chain exclusively from those cell types. Pick the cell whose boolean function matches the RTL sub-expression. Set `cell_type_from_preeco: true` on every gate. Step 1 validator FAILS any chain that skips this when compound gates exist in scope.
- **If the grep returns nothing** → no compound gates exist in the declaring module → only then proceed to E4d (RTL decomposition with simple gates).

**Why this matters:** Synthesis chooses compound gate types (OA12, OAI21, AN3, ND3) that are structurally consistent across Synth/PP/Route stages and FM-verifiable without SVF. Using different-but-logically-equivalent simple gates (OR2+AND2, NR2+OR3+AN2) creates intermediate nodes that FM cannot match stage-to-stage → ToggleChn_reg-style failures even when the boolean is correct. The PreEco netlist is the ground truth — it already shows which compound types synthesis picked for this library and RTL pattern.

How: grep the PreEco Synthesize netlist for cells near the pivot cone that implement each sub-expression's boolean function (OA21 for `(A|B)&C`, OA12 for `(A|B)&~C`, etc.). Use that cell type verbatim — don't substitute a logically-equivalent alternative.

**Before decomposing conditions into simple gates from RTL, search the PreEco backward cone of the pivot net for existing compound gates.** This is always more reliable than RTL-decomposed simple gates because:
- Compound gates already exist in the library and in the PreEco netlist → cell types confirmed valid
- Compound gate inputs are already connected to the correct signal cones → no PENDING_FM_RESOLUTION
- FM verifies them as structurally equivalent stage-to-stage without SVF

**Search procedure:**
```bash
# 1. Find the pivot net's driver and the backward cone (up to 8 hops from target_register.D)
# 2. Look for compound gates in that cone whose inputs partially match the new conditions
#    (compound = cell with ≥3 inputs combining AND+OR or AND+NOT patterns: OA, OAI, AN3, ND3, etc.)
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
  awk "/\b<target_register>_reg\b/,/\) ;/" | \
  grep -E "^\s+[A-Z][A-Z0-9]+[0-9]\s+[a-z]" | \
  grep -v "^\s*DFF\|^\s*SDF\|^\s*MUX" | head -10
# For each candidate gate: check if one of its inputs is replaceable with a new condition expression
```

**If compound gate found in backward cone:**
1. Record as `compound_gate_target`: instance name, cell type, replaceable input pin
2. Set `intermediate_net_strategy: "compound_gate_insertion"`
3. The condition chain becomes: new condition gates → compound gate's replaceable pin (rewire)
4. Set `condition_inputs_use_preeco_nets: true` — inputs come from EXISTING PreEco nets, not RTL names → no PENDING_FM_RESOLUTION risk in P&R stages

**If no compound gate found:** fall through to E4d (RTL decomposition with simple gates).

#### E4d — RTL decomposition (LAST RESORT — only when E4c grep returned zero compound gates)

**Do NOT enter E4d if E4c found any compound gate. E4d is only for modules with no compound cells at all (rare).**

When `fallback_strategy: "intermediate_net_insertion"` AND E4c grep confirmed zero compound gates in scope, decompose from RTL. Studier Step 0c-4 Entry B inserts these gates at the pivot net.

Parse each new condition from `context_line` independently (cases BEFORE the last/default old expression). Decompose each into a sub-chain using E3 rules. Sequence numbers start at `c001` (condition gates) — separate from D-input chain `d001` numbering.

**Even in E4d: search PreEco for any COMPOUND cell before using a simple gate.** For each Boolean sub-expression, grep PreEco for a COMPOUND cell that implements it (OA12, OAI21, AN3, ND3, NR3, INR3, IAOI21, …). Record `cell_type_from_preeco: true` if found.

```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | awk "/^module <declaring_module>/,/^endmodule/" \
  | grep -E "^\s+(OA|OAI|AN|ND|NR|INR|IAOI)[A-Z0-9]" | head -10
```

**MANDATORY truth-table verification before recording any compound cell:** call `cell_function_matches(cell_type, gate_function)` from `script/eco_scripts/eco_cell_truth_tables.py`. `False` → cell does NOT compute the claimed function (cell name and logic don't always agree across libraries) — pick another cell or update `gate_function` to match; never commit a `False` choice. `None` → cell missing from `cell_libraries/<lib>.json`; extend the JSON with the verified expression from the library — do not guess. Step 3 validate enforces this as backstop.

**Scan stitching is OUT OF SCOPE.** New ECO DFFs get `SE=SI=1'b0` in all 3 stages. DFT team handles scan integration. Do NOT emit `requires_scan_stitching`, `mode_s_anchor`, or sibling/bridge fields.

**MANDATORY fields on every `new_logic` / `new_logic_dff`** (Step 1 validate REJECTS if missing):
- `scope` (or `instance_scope`) — full netlist hierarchy path (e.g. `umccmd/ARB/CTRLSW`). Needed when `module_name` is instantiated more than once.
- **Mode I source-port info** when `d_input_net` starts with `UNCONNECTED_*` — emit `submodule_instance` + `port_name` + `bus_bit_index` so Step 3 can pair a child-scope `port_connection`.

**MANDATORY MUX context on every `wire_swap`** (REJECT if missing): `mux_select_gate_function`, `mux_select_branch_true_on`, `mux_select_i0_net`, `mux_select_i1_net` — even when polarity is decided.

**FORBIDDEN: `UNCONNECTED_<N>` as a variable in chain inputs or `d_input_expected_function`.** It's an undriven-net marker, not a signal. Trace it back to the real RTL source (e.g. `REG_UmcCfgEco[1]`) and emit the chain against THAT.

**MANDATORY `d_input_expected_function` (Gap E) for every change with a non-empty `d_input_gate_chain`** — Step 1 REJECTS as HIGH if missing. It's the Python Boolean the DFF.D should compute, in the chain's primary input variables.

Procedure: (1) read the always block; (2) strip the reset clause (added back as `& ~Reset`); (3) resolve Verilog macros to bit values; (4) sanitize bit-selects (`Sig[N]` → `Sig_N_`; `src==3'b011` → `src_0_ & src_1_ & (~src_2_)`); (5) translate operators; (6) wrap with reset (`(<EXPR>) & (~Reset)` if baked, else `<EXPR>`); (7) emit as a single string.

Example — RTL `if (IReset) X<=0; else X<=A & ~B & ((src==3'b000) | (src==3'b011))`:
```json
{"change_type": "new_logic", "dff_instance_name": "X_reg", "d_input_gate_chain": [...],
 "d_input_expected_function": "A & (~B) & (~IReset) & (((~src_2_)&(~src_1_)&(~src_0_)) | (src_0_&src_1_&(~src_2_)))"}
```

**Why this matters:** per-cell truth-table check (Check 5) verifies each cell against its `gate_function`, but cells can each be valid yet compose to the wrong Boolean. A prior 6-cell chain was per-cell-correct but composed wrong — FM caught it after 30 min; Step 1 catches it in 1 sec via this field.

**Skip ONLY when** `d_input_gate_chain: []` (D-input is a single net, not decomposed).

**Per-input polarity (REQUIRED).** `d_input_expected_function` encodes polarity directly (`SIG` vs `~SIG`). `eco_synth_chain.py` parses it to derive cell topology + input form. When a literal appears negated, the studier MUST reuse an existing INV in scope whose output is `~SIG` (else instantiate a fresh INV) — never substitute the positive-form wire and rely on a downstream NR/NAND to flip it.

**MANDATORY signal-in-scope check before recording any chain input** — every input MUST exist in the target module's scope (port, wire decl, or cell output). If a registered version of an upstream port is referenced and not visible, reuse a local DFF whose Q produces the same logical signal (per-stage Q name); else propose a `new_port` + `port_connection` to wire it. Never reference an out-of-scope signal. Step 1 validate enforces.

**For each new condition `<cond_expr> ? <val> : <next_condition>`:**
1. Decompose `<cond_expr>` — prefer compound PreEco gates over simple-gate chains.
2. Each gate: instance `eco_<jira>_c<seq>`, output `n_eco_<jira>_c<seq>`.
3. Final gate of sub-chain: 1-bit (condition true/false).
4. `<val>` (`1'b0` / `1'b1`) → what the MUX outputs when this condition matches.

**Combining conditions with the old expression — MANDATORY.** `new_condition_gate_chain` MUST include condition gates AND the priority MUX cascade that connects them to the pivot. Without the cascade, nothing drives the pivot net.

Cascade:
```
c_mux1: MUX2(val_1, pivot_net_orig, sel=cond_1) → c_mux1
c_mux2: MUX2(val_2, c_mux1,         sel=cond_2) → c_mux2
…
c_muxN: MUX2(val_N, c_mux<N-1>,     sel=cond_N) → <pivot_net>   ← restores original name
```

Last MUX MUST output `<pivot_net>` (not a new net) — keeps downstream chain unchanged. Use `gate_function: "MUX2"` (eco_applier resolves to the library `MUX2[A-Z0-9]*`). Constants `1'b0`/`1'b1` are accepted directly in MUX inputs (`.I1(1'b0)`).

Record as `new_condition_gate_chain` (flat array of all gates):

```json
"new_condition_gate_chain": [
  {
    "seq": "c001",
    "instance_name": "eco_<jira>_c001",
    "output_net": "n_eco_<jira>_c001",
    "gate_function": "<INV|AND2|AND3|OR2|NOR2|NAND2|...>",
    "inputs": ["<input_net_1>", "<input_net_2>"],
    "role": "condition_<N>_term_<M>",
    "input_from_change": null
  },
  ...
  {
    "seq": "c_mux_final",
    "instance_name": "eco_<jira>_c_mux_final",
    "output_net": "<pivot_net>",
    "gate_function": "MUX2|priority_gate",
    "inputs": ["<condition_output>", "<pivot_net>_orig", "1'b0|1'b1"],
    "role": "pivot_net_output"
  }
]
```

**After decomposing, verify each input — V1–V4.**

Two pre-rules: `~X` → emit INV gate (not PENDING); only base `X` goes through V1–V4. `X[N:M] == K` → bit-decompose into AND/INV/NAND gates; each bit signal goes through V1–V4. `PENDING_FM_RESOLUTION` is ONLY for a raw RTL signal V3 grep can't find — never for gate ops.

```python
all_inputs_resolvable = True
for gate in new_condition_gate_chain:
    for idx, inp in enumerate(gate["inputs"]):
        # V1: constants
        if inp in ("1'b0", "1'b1"): continue

        # V2: same-ECO new tokens (RULE 23 — will exist after Pass 2)
        eco_new_tokens = [c["new_token"] for c in changes
                          if c["change_type"] in ("new_port","new_logic","port_promotion")]
        if inp in eco_new_tokens:
            gate["input_from_change"] = next((i for i,c in enumerate(changes)
                                              if c.get("new_token") == inp), None)
            continue

        # V3: resolve to gate-level name in PreEco Synthesize (synthesis renames RTL names)
        candidates = [inp, f"{inp}_reg", f"{inp}_0_", f"{inp}_reg/Q"]
        resolved = next((c for c in candidates
                         if grep_count_in_preeco(c, stage="Synthesize") >= 1), None)
        if resolved:
            gate["inputs"][idx] = resolved
            continue

        # V4: not found by text — FM find_equivalent_nets will resolve
        gate["inputs"][idx] = "PENDING_FM_RESOLUTION:" + inp
        change.setdefault("condition_inputs_to_query", [])
        if inp not in [q["signal"] for q in change["condition_inputs_to_query"]]:
            change["condition_inputs_to_query"].append({
                "signal": inp, "scope": "<INST_A>/<INST_B>",
                "reason": f"not in PreEco (tried {candidates}); FM will resolve"
            })
        # Do NOT mark unresolvable — FM resolves in Step 2

# null ONLY when decomposition itself failed (arithmetic / function calls)
if not all_inputs_resolvable:
    new_condition_gate_chain = null; fallback_strategy = null
```

**Preserve chain structure even when inputs are PENDING.** Two phases: (1) build full gate structure from RTL; (2) verify inputs — unresolvable ones get `PENDING_FM_RESOLUTION:<sig>` placeholders. The studier needs the structure to substitute FM-resolved names; setting `new_condition_gate_chain: null` for pending inputs forces MANUAL_ONLY unnecessarily.
```json
"new_condition_gate_chain": [
  {"seq": "c<N>", "gate_function": "<gate_type>",
   "inputs": ["PENDING_FM_RESOLUTION:<unresolvable_signal>", "<other_input>"],
   "output_net": "n_eco_<jira>_c<N>"},
  {"seq": "c<N+1>", "gate_function": "<gate_type>",
   "inputs": ["n_eco_<jira>_c<N>", "<resolved_signal>"],
   "output_net": "n_eco_<jira>_c<N+1>"},
  ...
]
```

**Only set `new_condition_gate_chain: null`** when the decomposition itself failed (arithmetic operators, function calls, unsupported RTL constructs that prevent building the gate structure). Signal name resolution failures (PENDING_FM_RESOLUTION) are NOT a reason to set null.

**If decomposition fails** (arithmetic, function calls) → `new_condition_gate_chain: null`, `fallback_strategy: null`. eco_netlist_studier will mark as MANUAL_ONLY.

#### E4c — When fallback is not possible

Set `fallback_strategy: null` when ANY of the following is true:
- The old always block's default case (`else <target> <= <old_expression>`) is ABSENT from the new RTL — meaning the new RTL replaced the entire expression with new logic that does not preserve the old expression as any branch. There is no pivot net to redirect because the old gate chain no longer drives the register at all.
- The condition decomposition failed in E4d (arithmetic, function calls, or unsupported operators).
- The expression is an arithmetic (`+`, `-`, `*`, `/`) or multi-cycle change that cannot be expressed as a gate chain.

When `fallback_strategy: null`, the eco_netlist_studier marks this change as MANUAL_ONLY — an engineer must synthesize the full D-input expression from scratch using synthesis tools.

### E4b — Submodule Input Scope Check (MANDATORY after decomposition)

For each resolved input signal in the gate chain, verify it is **directly accessible** in the declaring module's scope — not only reachable by crossing a child submodule boundary.

**Detection:**
```bash
# For each input signal in the gate chain:
# Step 1: is it declared in the declaring module's own RTL file?
grep -n "^\s*\(reg\|wire\|input\|output\)\b.*\b<signal>\b" <declaring_module_rtl_file>
# count = 0 → NOT in declaring module scope

# Step 2: if not found, is it declared as an output of a child module?
grep -rn "^\s*output\b.*\b<signal>\b" <REF_DIR>/data/SynRtl/*.v
# If match found in a DIFFERENT module → signal comes from a child submodule

# Step 3: if from child submodule, which instance in the declaring module?
grep -n "<child_module_type>\s\+<instance_name>" <declaring_module_rtl_file>
```

**If any gate chain input comes from a child submodule output:**
1. Set `preferred_insertion_scope: "<child_instance_path>"` — insert gate chain INSIDE the child, not at parent
2. Set `input_from_submodule: true`, `submodule_instance: "<instance>"`, `submodule_type: "<module_type>"`
3. The gate chain output net becomes a **new output port** of the child module:
   - Add `port_declaration` for `n_eco_<jira>_d<last>` (output) from child module
   - DFF at parent scope connects to this new port via child's instance connection
4. Record `preferred_insertion_reason: "input <signal> is output of <child_module> — black-boxed by FM in P&R; insert inside child to avoid DFF0X"`

**STEP 4 — Gate-level primitive driver check (MANDATORY even when signal found in declaring module scope):**

When a D-input signal resolves to a gate-level wire (e.g., `UNCONNECTED_<N>`) that IS declared as `wire` in the declaring module, it may still be driven ONLY through a submodule output bus — not by any primitive cell output in that scope. FM black-boxes such submodules in P&R → wire appears undriven (DFF0X).

```bash
# After resolving gate-level wire name (e.g., UNCONNECTED_<N>):
# Check if any primitive cell drives it directly in declaring module scope:
awk '/^module <declaring_module>\b/,/^endmodule/' \
    <REF_DIR>/data/PreEco/Synthesize.v.gz | \
    grep -E "\.(Z|ZN|Q|QN|CO|S)\s*\(\s*<resolved_wire>\s*\)"
# count = 0 → wire has NO direct primitive driver → only driven via submodule bus
```

If count = 0 (no primitive driver in scope):

**EXCEPTION — UNCONNECTED_* bus bit wires:** If the resolved wire matches `^(SYNOPSYS_)?UNCONNECTED_\d+$`, do NOT set `preferred_insertion_scope`. These wires come from submodule port bus outputs — the correct fix is 0b-UNCONNECTED rename at the declaring module (parent) scope. The studier renames `UNCONNECTED_N → n_eco_<jira>_<hint>` as an explicit wire at parent scope, and FM traces hierarchically from parent → submodule → internal DFF. Going INSIDE the submodule breaks FM's clock/cone analysis and causes LatCG mismatches.
- Set `preferred_insertion_scope: null`
- Set `submodule_bus_driven: true` (flags studier to apply 0b-UNCONNECTED rename instead)
- **Mode I pre-check:** if the bus port is `output` of the child AND the child body's matching bit slot is also `UNCONNECTED_M` (grep child for `UNCONNECTED_\d+` at same `bus_bit_index` of any sub-instance bus), set `needs_child_internal_wireup: true`. Studier emits a paired child-scope `port_connection` (net_name=`<port>[<bit>]`) so the child output pin gets an internal driver — without this the parent rename leaves FM seeing X.

**For all other signals (not UNCONNECTED_*):** → **MANDATORY: set preferred_insertion_scope**
- Set `preferred_insertion_scope` to the submodule instance that drives this wire via port bus
- Set `input_from_submodule: true`, `submodule_bus_driven: true`
- Reason: FM black-boxes submodule in P&R → bus output wire appears undriven → always DFF0X in P&R FM targets.
- **NEVER set `preferred_insertion_scope: null` when `submodule_bus_driven: true` and signal is not UNCONNECTED_***

Find the driving submodule by searching for the wire in a port bus concatenation:
```bash
awk '/^module <declaring_module>\b/,/^endmodule/' \
    <REF_DIR>/data/PreEco/Synthesize.v.gz | \
    grep "\.\w\+\s*(\s*{[^}]*<resolved_wire>" | head -3
# → shows which instance's port bus contains this wire → that instance is the submodule to insert into
```

**If inputs are all directly accessible in declaring module scope AND have primitive drivers:** `preferred_insertion_scope: null` (default — insert at declaring module level as before).

### E5 — Record in JSON

Add `d_input_gate_chain`, `d_input_net`, `d_input_decompose_failed`, `fallback_strategy`, `new_condition_gate_chain`, `preferred_insertion_scope`, `input_from_submodule` to the `new_logic` change entry. Eco_netlist_studier Phase 0 reads this to plan gate insertions (D-input chain or intermediate net with condition gates).

---

## Output JSON

Write to `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json` (always use the full absolute path — the agent may be cd'd to REF_DIR for diffs, but output always goes to BASE_DIR/data/):

```json
{
  "changes": [
    {
      "file": "<rtl_file.v>",
      "module_name": "<declaring_module>",
      "change_type": "<wire_swap|and_term|new_port|port_promotion|new_logic|port_connection>",
      "old_token": "<old_signal_name>",
      "new_token": "<new_signal_name>",
      "context_line": "<full RTL line containing the change>",
      "target_register": "<register_name from LHS of context_line>",
      "target_bit": "<[0] or null>",
      "flat_net_exists": false,
      "flat_net_name": null,
      "flat_net_name_per_instance": null,
      "instances": null,
      "d_input_gate_chain": null,
      "d_input_net": null,
      "d_input_decompose_failed": false,
      "fallback_strategy": null,
      "new_condition_gate_chain": null,
      "mux_select_polarity_pending": false,
      "mux_select_gate_function": null,
      "mux_select_i0_net": null,
      "mux_select_i1_net": null,
      "mux_select_branch_true_on": null,
      "mux_select_reasoning": null,
      "has_sync_reset": false,
      "reset_signal": null,
      "preferred_insertion_scope": null,
      "input_from_submodule": false,
      "submodule_instance": null,
      "submodule_type": null,
      "is_bus_dff": false,
      "bus_width_expr": null,
      "old_enable_net": null,
      "new_enable_net": null,
      "new_enable_gate_chain": null
    }
  ],
  "nets_to_query": [
    {
      "net_path": "<INST_A>/<INST_B>/<old_signal_name>",
      "hierarchy": ["<INST_A>", "<INST_B>"],
      "instance": null,
      "reason": "wire_swap: find current gate-level driver of old signal",
      "is_bus_variant": false
    },
    {
      "net_path": "<INST_A>/<old_signal_name>",
      "hierarchy": ["<INST_A>"],
      "instance": "<INST_A>",
      "reason": "and_term: find gate implementing existing expression output — per-instance",
      "is_bus_variant": false
    },
    {
      "net_path": "<INST_B>/<old_signal_name>",
      "hierarchy": ["<INST_B>"],
      "instance": "<INST_B>",
      "reason": "and_term: same expression in second instance of same module",
      "is_bus_variant": false
    }
  ]
}
```

**Field notes:**
- `flat_net_exists`: `true` for `port_promotion` — net already in flat netlist under original signal name
- `flat_net_name`: resolved actual net name in flat netlist for:
  - `new_port` inputs (single instance) — the net driving this new port in parent scope
  - `and_term` — the actual flat net name of the new AND term signal (resolved from the port connection in parent; this is the net eco_netlist_studier uses as the second input to the new AND/NAND gate)
- `flat_net_name_per_instance`: map of `{instance_name: flat_net_name}` for multi-instance modules where each instance has a different connection. Applies to both `new_port` and `and_term` when `instances` is non-null.
- `instances`: list of instance names when the declaring module is instantiated multiple times in the parent
- `instance`: in `nets_to_query`, identifies which instance this entry belongs to (null for single-instance)

**`flat_net_name` for `and_term` MUST be populated in Step C.7** — without it, eco_netlist_studier Phase 0 cannot create the `new_logic_gate` entry for the AND-term addition. If Step C.7 cannot resolve the connection (e.g., the new port connection is not yet in PreEco RTL), use the PostEco RTL parent module as the source.

All `net_path` values must be verified hierarchy paths using instance names. Do NOT include unverified paths.

---

## Self-Validation (MANDATORY before writing the RPT)

**First: ensure tile Liberty cache exists** (one-time per tile, ~8 min; skips automatically if cache already present):
```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_liberty_extractor.py --ref-dir <REF_DIR>
```
This writes `<REF_DIR>/data/eco_cell_library.json` — the authoritative cell truth-table source used by the truth-table and chain-equivalence checks. If the cache exists, this command exits instantly.

**Then: run the Step 1 validator:**
```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_validate_step1.py \
    --rtl-diff data/<TAG>_eco_rtl_diff.json --ref-dir <REF_DIR> --output data/<TAG>_eco_validate_step1.json
```
If the output JSON's `overall_pass` is `false`: read every issue list (`entries[].issues[]` for MUX polarity, plus the top-level `phantom_wire_issues`, `new_port_issues`, `port_conn_issues`, `truth_table_issues`), correct the affected entries in `eco_rtl_diff.json`, and re-invoke. Do NOT write the RPT until `overall_pass: true`.

---

## Output RPT

After writing the JSON, write `<BASE_DIR>/data/<TAG>_eco_step1_rtl_diff.rpt` then copy to `AI_ECO_FLOW_DIR`:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step1_rtl_diff.rpt <AI_ECO_FLOW_DIR>/
```

```
================================================================================
STEP 1 — RTL DIFF ANALYSIS
Tag: <TAG>  |  Tile: <TILE>  |  JIRA: <JIRA>
================================================================================

<For each entry in changes[]:>
Source File     : <file>
Module          : <module_name>
Change Type     : <change_type>
  Old Signal    : <old_token>
  New Signal    : <new_token>
  Target Reg    : <target_register><target_bit>
  Context       :
    <context_line>

<Repeat block if more than one change>

--------------------------------------------------------------------------------
Nets to Query (<N> nets):
--------------------------------------------------------------------------------
  [<n>] <net_path>
        Reason   : <reason>
        Bus Var  : <YES / NO>

<Repeat for each net>

================================================================================
```
