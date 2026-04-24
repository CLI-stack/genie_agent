# ECO Applier — PostEco Netlist Editor Specialist

**You are the ECO applier.** Read the PreEco study JSON, locate the same cells in PostEco netlists, verify old_net is still present on the expected pin, and apply the net substitution. For new_logic changes (where new_net doesn't exist), auto-insert a new inverter cell. Always backup before editing.

**Inputs:** REF_DIR, TAG, BASE_DIR, JIRA, ROUND (current fix round — 1 for initial run), PreEco study JSON (`data/<TAG>_eco_preeco_study.json`)

---

## CRITICAL: Processing Order — 4 passes per stage

The PreEco study JSON may contain entries of multiple change types. Process in this strict order within each stage's decompress/edit/recompress cycle:

1. **Pass 1 — new_logic insertions** (`new_logic_dff`, `new_logic_gate`, `new_logic`): insert all new cells so their output nets exist in the temp file
2. **Pass 2 — port_declaration** (`port_declaration`, `port_promotion`): update module port lists and change wire/output/input declarations
3. **Pass 3 — port_connection** (`port_connection`): add `.port(net)` connections to module instance blocks
4. **Pass 4 — rewire** (`rewire`): change pin connections on existing cells

**Why this order:** Port declarations must exist before connections reference them. New_logic cells must exist before rewires reference their output nets. Rewires come last — they may depend on both new cells AND new port connections.

**ONE decompress per stage** — decompress once, apply ALL confirmed cells to the same temp file, then recompress once. Do NOT decompress/recompress per cell.

---

## Process Per Stage (Synthesize, PrePlace, Route)

### Step 0 — Detect netlist type (MANDATORY, once per stage before any edits)

After decompressing the stage to a temp file, count the number of module definitions:

```bash
grep -c "^module " /tmp/eco_apply_<TAG>_<Stage>.v
```

- Count > 1 → **hierarchical netlist**. Record `netlist_type = hierarchical` for this stage.
  - `port_declaration` and `port_connection` entries are **MANDATORY** — NEVER skip them.
  - `no_gate_needed: true` or `flat_net_confirmed: true` flags from the study JSON are **ignored** for hierarchical netlists.
- Count = 1 → **flat netlist**. `port_promotion` path applies; `port_declaration`/`port_connection` entries may use the flat-net shortcut.

> **This rule prevents:** skipping `port_declaration` and `port_connection` entries with reason "flat netlist" when the PostEco netlist is actually hierarchical. Always detect netlist type first, decide after.

### Step 1 — Check for confirmed entries

Before doing any file I/O, scan the stage array for entries where `"confirmed": true`.

- If the stage array is empty or has no confirmed entries: write all as SKIPPED with reason "no confirmed cells from PreEco study", skip to next stage.
- If any confirmed entries exist: proceed to Step 2.

### Step 2 — Backup (once per stage)

Include the round number in the backup name so each round has its own backup:

```bash
cp <REF_DIR>/data/PostEco/<Stage>.v.gz \
   <REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>
```

Example: `Synthesize.v.gz.bak_<TAG>_round1`, `Synthesize.v.gz.bak_<TAG>_round2`

### Step 3 — Module-grouped decompress strategy

PostEco netlists for P&R stages are large (often hundreds of MB decompressed). Working on the full file with sequential line-based edits causes:
- **False positives** from grep matching the same signal in other modules
- **Stale line numbers** — each edit shifts all subsequent line numbers
- **Missed changes** — edits silently applied to the wrong module scope

**Use the module-extraction approach instead of full-file sequential edits:**

```python
import gzip, re, os

stage_file = f"<REF_DIR>/data/PostEco/{Stage}.v.gz"
tmp_file   = f"/tmp/eco_apply_{TAG}_{Stage}.v"

# Step 3a — Group all confirmed changes by module_name
# This determines which modules need extraction
from collections import defaultdict
changes_by_module = defaultdict(list)
for entry in stage_array:
    if entry.get("confirmed"):
        mod = entry.get("module_name") or entry.get("instance_scope","").split("/")[0]
        changes_by_module[mod].append(entry)

# Step 3b — Stream through the compressed file module by module
# Extract each target module, apply ALL its changes, patch back inline
# For modules with no changes: copy through unchanged (streaming, no load)

with gzip.open(stage_file, 'rt') as f_in, \
     open(tmp_file, 'w') as f_out:

    current_module = None
    module_lines   = []
    in_target      = False

    for line in f_in:
        # Detect module start
        m = re.match(r'^module\s+(\S+)[\s(]', line)
        if m:
            current_module = m.group(1).rstrip('(').strip()
            in_target = current_module in changes_by_module
            module_lines = [line] if in_target else []
            if not in_target:
                f_out.write(line)
            continue

        # Detect module end
        if line.strip().split('//')[0].strip() == 'endmodule':
            if in_target:
                module_lines.append(line)
                # Apply ALL changes for this module on the isolated buffer
                edited = apply_all_changes_to_module(
                    module_lines,
                    changes_by_module[current_module],
                    current_module
                )
                f_out.writelines(edited)
                module_lines = []
                in_target = False
            else:
                f_out.write(line)
            current_module = None
            continue

        # Accumulate or pass through
        if in_target:
            module_lines.append(line)
        else:
            f_out.write(line)
```

**`apply_all_changes_to_module(lines, changes, module_name)`** applies all change types (port_decl, port_conn, new_logic gate/DFF, rewire, named_wire) in the correct Pass order (1→2→3→4) on the isolated module line list. Since the module is isolated, line numbers are stable throughout all changes for that module.

**Why this is better:**
- Each module is processed as a self-contained unit — no cross-module contamination
- All changes for a module apply to a stable, small line buffer (not the 500k-line file)
- Modules with no changes are streamed through without loading into memory
- Grep and line-number tracking are accurate because the scope is bounded

**For FLAT netlists** (single module — `grep -c "^module " netlist = 1`): the entire file IS one module — fall back to full-file load since there is no streaming advantage.

```python
# Flat netlist fallback
if netlist_type == "flat":
    with open(tmp_file, 'w') as f:
        f.write(gzip.open(stage_file, 'rt').read())
    # Apply all changes sequentially on the full file
```

### Step 4 — Process each confirmed change within `apply_all_changes_to_module`

Process changes IN PASS ORDER within the isolated module buffer. Pass order is critical — port declarations must exist before port connections reference them, and new_logic cells must exist before rewires reference their outputs.

**Pass order within each module:**
1. Pass 1 — new_logic insertions (new_logic_gate, new_logic_dff, new_logic — insert cells)
2. Pass 2 — port_declaration changes (port list + direction declaration)
3. Pass 3 — port_connection changes (add connections to submodule instances)
4. Pass 4 — rewire changes (change net on existing cell pin)

For each entry in the stage array where `"confirmed": true` (already grouped by module), perform steps 4a–4e on the **isolated module line buffer**:

#### 4a — Detect change type

**CRITICAL — Which `new_net` value to use:**
- If `new_net_alias` is **null** → use `new_net` (direct signal name) for all checks and rewires
- If `new_net_alias` is **non-null** → use `new_net_alias` (HFS alias) instead of `new_net`

Check if the effective `new_net` exists in the PostEco temp file:

```bash
grep -cw "<effective_new_net>" /tmp/eco_apply_<TAG>_<Stage>.v
```

- If count ≥ 1 → **rewire** (normal path, go to 4b)
- If count = 0 AND `change_type` is `"rewire"` → check `change_type` in the study entry:
  - If `change_type` is `"new_logic_dff"` → go to 4c-DFF (DFF insertion)
  - If `change_type` is `"new_logic_gate"` → go to 4c-GATE (combinational gate insertion)
  - If `change_type` is `"rewire"` and new_net is absent → this is a simple inversion case; go to 4c (inverter insertion) only if `new_net` is derived from `~<source_net>`. If `new_net` is NOT an inversion of an existing net (no `~` prefix implied by the RTL diff), mark SKIPPED with reason "new_net not found in PostEco — not an inversion; check eco_netlist_studier output"

#### 4b — Rewire path (new_net exists)

**Find the cell:**
```bash
grep -n "<cell_name>" /tmp/eco_apply_<TAG>_<Stage>.v | head -20
```

**Verify preconditions:**
1. Cell exists — if no match: SKIPPED, reason="cell not found in PostEco"
2. old_net on expected pin — `grep -c "\.<pin>(<old_net>)"` count must = 1
3. If count > 1: SKIPPED, reason=AMBIGUOUS

**Apply:**
```
From: .<pin>(<old_net>)
To:   .<pin>(<new_net>)
```
Scope replacement to the specific cell instance block only (by line range). Record: status=APPLIED, change_type=rewire.

#### 4c — new_logic path (new_net does not exist — insert inverter)

The new_net requires inversion of an existing net. Auto-insert a new inverter cell:

**Step 4c-1: Find inverter cell type from PreEco netlist**

Use a pattern that specifically matches cell instantiation lines (not port declarations, net declarations, or comments):

```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -m 5 "INV" | grep -v "//" | grep -E "^[[:space:]]*INV[A-Z0-9]+ [a-z]"
```

Pattern explanation:
- `^[[:space:]]*INV[A-Z0-9]+` — line starts with optional whitespace then `INV` followed by uppercase/digits (cell type name)
- ` [a-z]` — followed by a space then lowercase letter (start of instance name)
- `grep -v "//"` — exclude comments

```bash
cell_type=$(echo "<matched_line>" | awk '{print $1}')
```

If no INV cell found in this stage, try another stage (Synthesize is most likely to have one).

**Step 4c-2: Derive the source net**

The `new_net` does not exist in PostEco — it must be created by inverting an existing net. Derive `source_net` from the study JSON entry's `old_net` field: the old net is the signal currently on the target pin, and the new desired state is its logical inverse. Use `old_net` as `source_net`.

Do NOT read the RTL diff — the study JSON is the authoritative source for pin-level net names. The applier only reads `data/<TAG>_eco_preeco_study.json`.

Verify `source_net` exists in the PostEco temp file:
```bash
grep -cw "<source_net>" /tmp/eco_apply_<TAG>_<Stage>.v
```
If count = 0: SKIPPED, reason="source_net (old_net from study JSON) not found in PostEco — cell may have been optimized away or renamed"

**Step 4c-3: Generate instance and output net names**

Use the JIRA number and a sequence counter. The counter is assigned **per distinct (old_net, new_net) pair** — NOT per stage, NOT per cell occurrence:

```
inv_inst = eco_<jira>_<seq>    (e.g., eco_<jira>_001, eco_<jira>_002)
inv_out  = n_eco_<jira>_<seq>  (e.g., n_eco_<jira>_001, n_eco_<jira>_002)
```

**Seq counter rules:**
- Build a mapping table at the start: `{(old_net, new_net): seq}`, starting at 001
- Before assigning a new seq, check if this (old_net, new_net) pair already has one
- If yes: **reuse the same seq** (same logical change across different stages → same cell name)
- If no: assign next seq and add to the table

**Example — same change in 3 stages (most common):**
```
Synthesize: old=<old_signal_A>, new=~<new_signal_A> → eco_<jira>_001
PrePlace:   old=<old_signal_A>, new=~<new_signal_A> → eco_<jira>_001  ← same!
Route:      old=<old_signal_A>, new=~<new_signal_A> → eco_<jira>_001  ← same!
```

This ensures consistent naming across stages for FM stage-to-stage matching.

**Step 4c-4: Insert inverter instantiation**

Find the correct module scope — the inverter must go inside the **same module that contains the target cell**, not the last `endmodule` in the file.

Use Python to insert at the correct line number:
```python
with open('/tmp/eco_apply_<TAG>_<Stage>.v', 'r') as f:
    lines = f.readlines()

# Find target cell line
cell_line_idx = next(i for i, l in enumerate(lines) if '<cell_name>' in l)

# Find first endmodule AFTER the target cell (correct module scope)
endmodule_idx = next(i for i in range(cell_line_idx, len(lines)) if 'endmodule' in lines[i])

new_lines = [f'  // ECO new_logic insert — TAG=<TAG> JIRA=<JIRA>\n',
             f'  <CellType> <inv_inst> (.I(<source_net>), .ZN(<inv_out>));\n']
lines[endmodule_idx:endmodule_idx] = new_lines

with open('/tmp/eco_apply_<TAG>_<Stage>.v', 'w') as f:
    f.writelines(lines)
```

**Step 4c-5: Rewire target pin to use inv_out**

Now rewire the original target cell's pin from `old_net` to `inv_out` using the same scoped replacement as 4b:
```
From: .<pin>(<old_net>)
To:   .<pin>(<inv_out>)
```

Record: status=INSERTED, change_type=new_logic, inv_inst=`<inv_inst>`, inv_out=`<inv_out>`, source_net=`<source_net>`, cell_type=`<CellType>`.

Also record `inv_inst_full_path` — the full hierarchy path needed for the SVF `-instance` entry:

```python
rtl_diff = load("<BASE_DIR>/data/<TAG>_eco_rtl_diff.json")
# Find the entry matching old_net
hierarchy = next(n['hierarchy'] for n in rtl_diff['nets_to_query']
                 if n['net_path'].endswith(old_net) or old_net in n['net_path'])
hierarchy_path = "/".join(hierarchy)   # "<INST_A>/<INST_B>"

inv_inst_full_path = f"{TILE}/{hierarchy_path}/{inv_inst}"
# e.g. "<TILE>/<INST_A>/<INST_B>/eco_<jira>_001"
```

#### 4c-DFF — new_logic_dff path (insert new flip-flop)

For entries with `change_type: "new_logic_dff"` from the PreEco study JSON:

**Step 1 — Resolve stage-specific port connections (MANDATORY):**

```python
if "port_connections_per_stage" in entry and stage in entry["port_connections_per_stage"]:
    port_map = entry["port_connections_per_stage"][stage]
else:
    # Fallback: use flat port_connections (Synthesize-derived)
    port_map = entry["port_connections"]
```

`port_map` now contains the correct net names for this specific stage (e.g., the clock net may be different in PrePlace vs Synthesize).

> **This rule prevents:** using the Synthesize-derived `port_connections` for all 3 stages. In PrePlace/Route, clock and reset nets may be renamed by P&R tools, causing the inserted DFF to appear unmatched in FM stage-to-stage comparison.

**Step 2 — Classify pins and verify nets in PostEco temp file:**

**Functional pins** (clock, data, and D-input chain nets — all except output and auxiliary):
```bash
grep -cw "<net_from_port_map>" /tmp/eco_apply_<TAG>_<Stage>.v   # must be ≥ 1
```
If a net is not found AND it is produced by another `new_logic` entry → process that entry first (`input_from_change` dependency).
If a net is not found with no dependency → try a P&R alias search:
```bash
grep -n "<net_root>" /tmp/eco_apply_<TAG>_<Stage>.v | \
  grep -v "^\s*\(wire\|input\|output\|reg\)" | head -5
```
If alias found: use it, record `"alias_used": "<found_alias>"` in the applied JSON.
If no alias found: SKIPPED, reason="functional pin net not found in <Stage> PostEco — manual fix required".

**Auxiliary pins** (scan input, scan enable, and any other non-functional pins):
The net names in `port_map` were derived from a neighbour DFF in the same scope. Verify they exist:
```bash
grep -cw "<aux_net_from_port_map>" /tmp/eco_apply_<TAG>_<Stage>.v   # must be ≥ 1
```
If not found → find an existing DFF of the same cell type in the same module scope in the **PostEco** temp file:
```bash
grep -A6 "<dff_cell_type>" /tmp/eco_apply_<TAG>_<Stage>.v | grep "\.<aux_pin>" | head -3
```
Use that neighbour's net for this auxiliary pin. Record `"aux_pin_from_neighbour": true`.
If no neighbour DFF of the same cell type is found in the same module scope: search the entire PostEco stage file for any instance of `<dff_cell_type>` and read its auxiliary pin value. Record `"aux_pin_from_neighbour": true, "neighbour_scope": "global_fallback"`. Only if NO instance of the DFF cell type exists anywhere in the PostEco file: use the Synthesize-derived value from `port_connections_per_stage["Synthesize"]` as a last resort — this is only safe for Synthesize-stage runs (before scan insertion); for PrePlace and Route stages, if no DFF instance is found, mark SKIPPED with reason "no DFF cell instance found in PostEco — cannot determine auxiliary pin nets for this stage" rather than silently using wrong Synthesize constants.

**Step 3 — Find DFF cell type from PreEco netlist (confirm it exists in this stage):**
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -m1 "<dff_cell_type>" | head -1
```
Confirm the cell type from the study JSON exists in this stage. If a different variant was used (e.g., lower drive strength), update accordingly.

**Step 4 — Build complete port connection string from `port_map` and insert:**
```verilog
  // ECO new_logic_dff insert — TAG=<TAG> JIRA=<JIRA>
  <cell_type> <instance_name> (.<pin1>(<net1>), .<pin2>(<net2>), ...);
```
Include **every pin** from `port_map` — functional and auxiliary. Do NOT hardcode any pin name or net name. Do NOT omit auxiliary pins — omitting scan pins leaves them undriven, causing DRC and LEC failures.

Find correct module scope and insert (same pattern as Step 4c-4 for inverters):
```python
cell_line_idx = next(i for i, l in enumerate(lines) if '<any_existing_cell_in_same_scope>' in l)
endmodule_idx = next(i for i in range(cell_line_idx, len(lines)) if 'endmodule' in lines[i])
new_lines = ['  // ECO new_logic_dff — TAG=<TAG> JIRA=<JIRA>\n',
             '  <cell_type> <instance_name> (<port_connection_string>);\n']
lines[endmodule_idx:endmodule_idx] = new_lines
```

**Step 5 — Compute `inv_inst_full_path`** (same formula as inverter — needed by SVF updater):
```python
instance_scope = entry["instance_scope"]   # e.g., "<INST_A>/<INST_B>"
inv_inst_full_path = f"{TILE}/{instance_scope}/{instance_name}"
```

**Step 6 — Verify:** `grep -c "<instance_name>"` in recompressed file ≥ 1.

Record: `status=INSERTED`, `change_type=new_logic_dff`, `instance_name`, `inv_inst_full_path`, `output_net`, `cell_type`.

---

#### 4c-GATE — new_logic_gate path (insert new combinational gate)

For entries with `change_type: "new_logic_gate"` from the PreEco study JSON:

**Step 0 — Handle `needs_named_wire` inputs BEFORE any other steps:**

If any input in `port_connections` starts with `NEEDS_NAMED_WIRE:`, resolve it first. This flag means the eco_netlist_studier determined the net has no direct primitive cell driver — its only driver is a hierarchical submodule output port bus. FM in P&R stages black-boxes hierarchical submodules and cannot trace the net's value through the port bus, causing downstream DFFs to be classified as `DFF0X`. A proper named wire must be declared and the source bus explicitly rewired so FM can see the connection.

For each `NEEDS_NAMED_WIRE:<source_net>` input:

```python
source_net = input_value.split(":", 1)[1]   # the net currently in the port bus
named_wire = f"eco_{JIRA}_{signal_alias}"   # new wire name — descriptive, unique within module
```

**Sub-step A — Declare the named wire in the module body:**
```python
# Find module scope (mod_idx to endmodule_idx)
# Insert wire declaration after the last existing wire declaration in scope
lines.insert(wire_insert_idx, f"  wire {named_wire} ;\n")
```

**Sub-step B — Find the source port bus and rewire it:**

Find which hierarchical instance drives `source_net` in its output port bus:
```bash
grep -n "\b<source_net>\b" /tmp/eco_apply_<TAG>_<Stage>.v | head -5
```

The result will show a port connection on a hierarchical instance:
```verilog
.SomeOutputPort( { ..., <source_net>, ... } )
```

Replace `<source_net>` with `<named_wire>` at that exact position (scoped to the instance block — do NOT global replace):
```python
lines[bus_line_idx] = lines[bus_line_idx].replace(
    f" {source_net} ", f" {named_wire} ", 1  # replace exactly once in the bus
)
```

**Sub-step C — Update the gate input:**
```python
port_connections[input_pin] = named_wire  # use the new named wire
```

**Sub-step D — Verify both changes:**
```bash
grep -cw "<named_wire>" /tmp/eco_apply_<TAG>_<Stage>.v  # must be ≥ 2: wire decl + bus conn + gate input
grep -cw "<source_net>" /tmp/eco_apply_<TAG>_<Stage>.v  # count should have decreased by 1 (removed from bus)
```

Record in JSON: `"named_wire_inserted": true, "named_wire": "<named_wire>", "source_net_rewired": "<source_net>"`.

> **Why this is necessary:** A net driven only through a hierarchical submodule output port bus is not directly traceable by FM in P&R stages — the submodule is treated as a black box. FM cannot look inside the black box to determine whether the output is driven or constant. Declaring an explicit named wire and replacing the original net in the port bus creates a visible, traceable connection that FM can follow even across the black-box boundary. This is a structural requirement that applies regardless of net naming conventions.

**Step 1 — Verify ALL input signals exist before any insertion:**

Use `port_connections_per_stage[<Stage>]` if available; fall back to `port_connections` only if the per-stage map is absent.

```python
stage_pcs = entry.get("port_connections_per_stage", {}).get(Stage) or entry.get("port_connections", {})

missing_inputs = []
for pin, net_name in stage_pcs.items():
    if pin == output_pin:
        continue  # skip output
    if net_name.startswith("NEEDS_NAMED_WIRE:"):
        continue  # handled in Step 0 — already resolved
    if net_name.startswith("UNRESOLVED_IN_"):
        missing_inputs.append((pin, net_name.split(":", 1)[1]))
        continue
    if isinstance(net_name, str) and net_name.startswith("1'b"):
        continue  # constant — always valid
    count = grep_count(net_name, module_buffer)
    if count == 0:
        missing_inputs.append((pin, net_name))

if missing_inputs:
    # SKIP — do NOT insert gate with missing inputs
    # A gate with a non-existent input produces a floating pin:
    # FM sees the gate as undriven → downstream DFF classified as DFF0X or non-equivalent
    record(status="SKIPPED", reason=(
        f"gate input(s) not found in {Stage} PostEco: "
        + ", ".join(f"pin={p} net={n}" for p, n in missing_inputs)
        + " — use per-stage net resolution in eco_netlist_studier (0b-GATE-STAGE-NETS)"
    ))
    continue  # skip this gate, process next confirmed entry
```

**CRITICAL: NEVER insert a gate with a missing input net.** A gate with a non-existent or floating input:
- Produces a floating input pin in the PostEco netlist
- FM classifies any downstream DFF as `DFF0X` or non-equivalent
- The insertion APPEARS successful (instance grep finds 1 result) but the logic is broken
- This produces misleading INSERTED status with 0 verify_failed despite wrong netlist

If any input is a `new_logic` output (`n_eco_<jira>_<seq>`) — verify that new_logic entry was already processed in Pass 1 (it should exist in the module buffer from an earlier Pass 1 step).

**Step 2 — Find gate cell type from PreEco netlist matching `gate_function`:**
```bash
# Use gate_function as a prefix pattern — find first matching real library cell
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -E "^[[:space:]]*<gate_function>[A-Z0-9]* [a-z]" | head -3
```

Replace `<gate_function>` with the actual value (e.g., `NAND2`, `AND2`, `OR2`, `MUX2`, `INV`, etc.). The pattern `<gate_function>[A-Z0-9]*` matches the full library cell name that starts with the gate function prefix.

**For `MUX2` gate_function specifically:** The library cell will have a longer technology-specific name (e.g., `MUX2<drive_strength><tech_suffix>`). Never use the bare `MUX2` primitive — it is a generic Verilog behavioral construct not present in the technology library. FM will fail to elaborate any netlist containing bare `MUX2` instances. Always grep the PreEco netlist with the `MUX2[A-Z0-9]*` pattern to find the real cell name used in the current netlist.

**Step 2b — Handle constant inputs (`1'b0`, `1'b1`) in gate port connections:**

For gates from `new_condition_gate_chain`, inputs may include constant values (`1'b0`, `1'b1`). Gate-level netlists require these to be driven by tie-high/tie-low cells or connected directly as constants in the instantiation. Use the following approach:

- **Direct constant connection:** Write `1'b0` or `1'b1` directly in the port connection — most synthesis tools and FM accept this in gate-level netlists: `.I1( 1'b0 )`
- **Alternatively, find tie cells:** `grep -E "^[[:space:]]*(TIEH|TIEL)[A-Z0-9]* [a-z]" /tmp/eco_apply_<TAG>_<Stage>.v | head -3` — use tie cell output if required by the design style
- If unsure, use the direct constant form — it is universally valid in Verilog gate-level netlists and Formality accepts it

> **This rule prevents:** using bare `MUX2` as a cell type in the gate-level netlist. `MUX2` is not a library cell — it is a Verilog language primitive. FM reads the PostEco netlist and cannot elaborate undefined cell types, causing all FM targets to return N/A. Always resolve `gate_function` to a real library cell name from the PreEco netlist using the prefix pattern above.
Use the `cell_type` from the study JSON `port_connections`.

**Step 3 — Build port connection string from study JSON:**
```verilog
  // ECO new_logic_gate insert — TAG=<TAG> JIRA=<JIRA>
  <cell_type> <instance_name> (.<A>(<input_net_1>), .<B>(<input_net_2>), .<ZN>(<output_net>));
```

**Step 4 — Insert before correct endmodule** (same pattern as 4c-DFF).

**Step 5 — Compute `inv_inst_full_path`:**
```python
instance_scope = entry["instance_scope"]
inv_inst_full_path = f"{TILE}/{instance_scope}/{instance_name}"
```

**Step 6 — Verify:** `grep -c "<instance_name>"` in recompressed file ≥ 1.

Record: `status=INSERTED`, `change_type=new_logic_gate`, `instance_name`, `inv_inst_full_path`, `output_net`, `gate_function`, `cell_type`.

---

#### 4c-PORT_DECL — port_declaration path (Pass 2)

For entries with `change_type: "port_declaration"`:

> **MANDATORY pre-check:** Confirm `netlist_type` from Step 0. If hierarchical — always apply, regardless of any `flat_net_confirmed` or `no_gate_needed` flags. If flat — use `port_promotion` path instead.

**CRITICAL — Distinguish wire declarations from port declarations BEFORE doing anything:**

Read `declaration_type` from the study JSON entry:
- `"input"` or `"output"` → **TRUE PORT DECLARATION** — the signal is a port of the module. Apply Steps 2–4 (port list modification + direction declaration in body).
- `"wire"` → **WIRE DECLARATION** — the signal is a local wire inside the module connecting submodule instances. It does NOT appear in the module port list. Apply Step 4-WIRE only (add `wire <signal_name>;` to module body). Skip Steps 2 and 3 entirely.

**Why this distinction matters:** Port lists in P&R stages can be thousands of lines long due to scan/test ports added by P&R. The depth-tracking port list modification (Step 2) fails silently for extremely long port lists. Wire declarations never need port list modification — they only need a single `wire` line added to the module body, which can be done reliably regardless of port list length.

**Step 4-WIRE — For wire declarations only (skip Steps 2 and 3):**

Find the module start and `endmodule`, then insert `wire <signal_name>;` in the module body after the last existing `wire` declaration:
```python
# Find existing wire declarations in module body
wire_lines = [(i, l) for i, l in enumerate(lines[mod_idx:endmodule_idx], mod_idx)
              if re.match(r'^\s*wire\b', l)]

if wire_lines:
    # Insert after the last existing wire declaration
    insert_idx = wire_lines[-1][0] + 1
else:
    # No existing wires — insert right after port list close (port_list_close_idx + 1)
    insert_idx = port_list_close_idx + 1

lines.insert(insert_idx, f'  wire  <signal_name> ;\n')
```

**Verify:** `grep -cw "<signal_name>" /tmp/eco_apply_<TAG>_<Stage>.v` ≥ 1 after insertion.

Record: `status=APPLIED`, `change_type=port_declaration`, `declaration_type=wire`.

---

**FORCE_REAPPLY override:** If the study JSON entry has `"force_reapply": true` — skip the ALREADY_APPLIED check entirely and apply Steps 2–4 unconditionally. This flag is set by ROUND_ORCHESTRATOR when eco_fm_analyzer diagnoses `ABORT_LINK` due to a false ALREADY_APPLIED on a port_declaration. Record status as `APPLIED` (not ALREADY_APPLIED) and note `"forced": true` in the JSON entry.

**CRITICAL — BATCH all PORT_DECL changes for the same module in ONE port list modification:**

When multiple PORT_DECL entries (declaration_type=input or output) target the **same module**, do NOT apply them one-by-one with separate port list modifications. Each sequential modification shifts line numbers, causing subsequent depth-tracking to find the wrong `port_list_close_idx` — a line without `)` — and Python's `rfind(')')` = -1 corrupts it catastrophically.

**Correct approach — batch before starting:**
```python
# Before processing any PORT_DECL for a module, collect ALL signal names for that module
port_decl_by_module = defaultdict(list)
for entry in stage_array:
    if entry.get("change_type") == "port_declaration" and entry.get("declaration_type") in ("input", "output"):
        port_decl_by_module[entry["module_name"]].append(entry)

# For each module, run port list modification ONCE with ALL signals
for module_name, entries in port_decl_by_module.items():
    signal_names = [e["signal_name"] for e in entries]
    directions   = {e["signal_name"]: e["declaration_type"] for e in entries}

    # Step 2: Add ALL signal names to port list in one modification
    # Find port_list_close_idx once
    # Modify close line: close_line[:last_paren] + ''.join(f', {s}' for s in signal_names) + ')' + close_line[last_paren+1:]

    # Step 3: Add ALL direction declarations after port list close in one batch
    for sig in signal_names:
        lines.insert(port_list_close_idx + 1, f'  {directions[sig]}  {sig} ;\n')
        port_list_close_idx += 1  # update index after each insertion

    # Mark all entries as APPLIED
    for e in entries:
        e["status"] = "APPLIED"
```

**Why rfind=-1 is catastrophic:** `rfind(')')` = -1 means no `)` found. Python index -1 selects the LAST character: `line[:-1]` removes the last char (e.g., `;`), and `line[-1+1:]` = `line[0:]` = the entire line repeated. This produces a corrupted double-line that FM-599 rejects. The validation after Step 2 MUST confirm the found close line contains `)` before modifying:

```python
assert ')' in lines[port_list_close_idx], (
    f"PORT_DECL: port_list_close_idx={port_list_close_idx} points to line without ')': "
    f"'{lines[port_list_close_idx][:60]}...' — depth tracking found wrong line. "
    f"Mark all PORT_DECL for module '{module_name}' as SKIPPED."
)
```

**Steps 2–4 below apply ONLY to true port declarations (`declaration_type: "input"` or `"output"`):**

**Step 1 — Find module definition line:**
```bash
grep -n "^module <module_name> \|^module <module_name>(" /tmp/eco_apply_<TAG>_<Stage>.v | head -3
```

**Step 2 — Add signal to module port list using parenthesis depth tracking:**

```python
# Step 2a — Find module start (exact name match)
mod_idx = next(
    i for i, l in enumerate(lines)
    if re.match(rf'^module\s+{re.escape(module_name)}\s*[(\s]', l)
)

# Step 2b — Find endmodule boundary (handles trailing comments)
# Strip comments before comparing — 'endmodule // note' must also match
endmodule_idx = next(
    i for i in range(mod_idx + 1, len(lines))
    if lines[i].strip().split('//')[0].strip() == 'endmodule'
)

# Step 2c — Find port list close using parenthesis depth tracking
# IMPORTANT: Port lists in P&R stages can be very long (thousands of lines) because
# P&R adds scan and test ports that are absent in Synthesize. The depth tracking
# MUST search the full range from mod_idx to endmodule_idx without early exit.
depth = 0
port_list_close_idx = None
for i in range(mod_idx, endmodule_idx):
    for ch in lines[i]:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                port_list_close_idx = i
                break
    if port_list_close_idx is not None:
        break

# Step 2d — MANDATORY checkpoint before proceeding
if port_list_close_idx is None:
    # Depth tracking failed — module structure is unexpected. Do NOT silently skip.
    # Record as SKIPPED with reason so the issue is visible in the Step 4 RPT.
    raise RuntimeError(
        f"PORT_DECL: Could not find port list close for module '{module_name}' "
        f"(searched lines {mod_idx}–{endmodule_idx}). "
        f"Possible causes: (1) endmodule_idx found too early due to comment parsing, "
        f"(2) mismatched parentheses in port list, "
        f"(3) module_name mismatch between stages (check stage-specific module suffixes). "
        f"Mark this port_declaration entry as SKIPPED."
    )

# Insert signal name before the last ')' on port_list_close_idx
close_line = lines[port_list_close_idx]
last_paren = close_line.rfind(')')
lines[port_list_close_idx] = close_line[:last_paren] + f', <signal_name>\n)' + close_line[last_paren+1:]
```

> **Why P&R port lists are much longer than Synthesize:** The Synthesis stage netlist only includes functional ports. P&R stages insert scan chain ports, clock distribution ports, and test ports, making the port list potentially 5–10× longer. The depth tracking loop MUST NOT be limited to a shorter range — it must always search the full module scope (`mod_idx` to `endmodule_idx`).

**Step 3 — Add declaration in module body AFTER the port list closes:**

The declaration (`input/output <signal_name>;`) must be inserted AFTER the port list `);` — not before it. Use `port_list_close_idx` from Step 2 to determine where the module body starts:

```python
# Insert declaration on the line AFTER port_list_close_idx
# Find first blank line or non-comment line after port list close — insert there
insert_idx = port_list_close_idx + 1
# Skip any blank lines or comments immediately after the port list
while insert_idx < len(lines) and lines[insert_idx].strip() in ('', '//'):
    insert_idx += 1
lines.insert(insert_idx, f'  input/output  <signal_name> ;\n')
```

> **This rule prevents:** inserting the declaration inside the still-open port list. The port list must be fully closed (depth=0 confirmed in Step 2) before Step 3 inserts the declaration. Using `mod_idx+1` as the insertion point without checking the port list closure causes the declaration to land inside `(port1, port2, ...)` where only port names are valid.

Record: `status=APPLIED`, `change_type=port_declaration`.

---

#### Shared — Module Boundary in Isolated Buffer

In the module-extraction approach (Step 3), each module's changes are applied to an **isolated line buffer** containing ONLY that module's text (from `module <name>` to `endmodule`). This means:

```python
# The buffer already contains exactly one module — no search needed
mod_idx       = 0              # buffer starts at the module declaration
endmodule_idx = len(lines) - 1 # buffer ends at endmodule
```

There is no risk of matching a sibling module — the buffer is already scoped. All subsequent steps search `lines[0:len(lines)]` (the entire buffer = exactly this module).

**For FLAT netlists only** (full file loaded): apply the explicit boundary search:
```python
mod_idx = next(
    i for i, l in enumerate(lines)
    if re.match(rf'^module\s+{re.escape(module_name)}\s*[(\s]', l)
)
endmodule_idx = next(
    i for i in range(mod_idx + 1, len(lines))
    if lines[i].strip().split('//')[0].strip() == 'endmodule'
)
```

---

#### 4c-PORT_PROMO — port_promotion path (Pass 2)

For entries with `change_type: "port_promotion"` (signal was `reg`, now promoted to `output reg`):

**The signal is ALREADY in the module port list — do NOT add it again.**

**Step 1 — Apply the Find Module Boundary procedure above.**

**Step 2 — Change the declaration keyword within the module boundary only:**
```python
for i in range(mod_idx, endmodule_idx):
    line = lines[i]
    if re.search(rf'\b(wire|reg)\s+{re.escape(signal_name)}\s*;', line):
        lines[i] = re.sub(rf'\b(wire|reg)\b', 'output', line, count=1)
        break
```

Use `re.sub` with word-boundary `\b` — do NOT use plain `str.replace('wire ', 'output ')` which would match any occurrence of "wire" in the line, including within net names.

**Step 3 — Verify within module boundary:**
```python
scope = lines[mod_idx:endmodule_idx]
assert any(f'output' in l and signal_name in l for l in scope), \
    f"port_promotion failed: 'output {signal_name}' not found in {module_name}"
```

Record: `status=APPLIED`, `change_type=port_promotion`, `signal_name`, `module_name`.

> **This rule prevents:** applying `replace('wire ', 'output ')` across the entire file without stopping at `endmodule`. In a netlist with many module variants sharing the same internal wire name, this corrupts every matching module.

---

#### 4c-PORT_CONN — port_connection path (Pass 3)

For entries with `change_type: "port_connection"`:

**Read from study JSON entry:**
```python
parent_module    = entry["parent_module"]     # full module name of the parent
submodule_pattern= entry["submodule_pattern"] # grep pattern for the submodule type
instance_name    = entry["instance_name"]     # instance name inside parent module
port_name        = entry["port_name"]         # new port being connected
net_name         = entry["net_name"]          # net to connect to the port
```

**Step 1 — Apply the Find Module Boundary procedure above** (using `parent_module` as `module_name`). Variables become `parent_mod_idx` and `parent_endmodule_idx`.

**Step 2 — Find the instance declaration line within the parent module:**
```python
inst_line = next(
    (i for i in range(parent_mod_idx, parent_endmodule_idx)
     if re.search(rf'\b{re.escape(submodule_pattern)}\s+{re.escape(instance_name)}\b', lines[i])),
    None
)
if inst_line is None:
    # SKIPPED: instance not found in parent module scope
```

**Step 3 — Find the TRUE closing `);` using parenthesis depth tracking:**

Do NOT use simple string pattern matching on `);` — a module instance block may span many lines and contain nested expressions with their own parentheses. Track depth:

```python
depth = 0
close_idx = None
for i in range(inst_line, parent_endmodule_idx):
    for ch in lines[i]:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    if close_idx is not None:
        break

if close_idx is None:
    # SKIPPED: could not find matching closing ')' — malformed instance block
```

> **This rule prevents:** a simple `);` pattern matching a mid-block line like `.last_port( <net> ) ) ;` (which has `))` closing both the port value and the instance) and inserting the new connection at the wrong position, corrupting the port list.

**Step 4 — Insert new port connection at the close line:**

```python
close_line = lines[close_idx]
last_paren = close_line.rfind(')')
new_conn = f', .{port_name}( {net_name} )'
lines[close_idx] = close_line[:last_paren] + new_conn + close_line[last_paren:]
```

**Step 5 — Verify within parent module boundary:**
```bash
grep -c ".{port_name}( {net_name} )" /tmp/eco_apply_<TAG>_<Stage>.v
# Must = 1
```

**Step 6 — If `net_name` doesn't exist as a wire/signal in the parent module**, add a wire declaration inside the parent module scope (after the module header, before the first instance):
```verilog
  wire  <net_name> ;
```

Record: `status=APPLIED`, `change_type=port_connection`, `port_name`, `net_name`, `instance_name`.

---

### Step 5 — Recompress (once per stage, after ALL modules processed)

```bash
gzip -c /tmp/eco_apply_<TAG>_<Stage>.v > <REF_DIR>/data/PostEco/<Stage>.v.gz
```

Verify the output is non-zero and parseable:
```bash
ls -lh <REF_DIR>/data/PostEco/<Stage>.v.gz
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | grep -c "^module "   # must be ≥ 1
```

### Step 6 — Verify all applied/inserted cells (once per stage)

**Verify from the module-level buffers you already have in memory** — do NOT decompress again. All verification runs on the edited module lines that were returned by `apply_all_changes_to_module`, before Step 5 recompress. This is fast and zero-cost because the buffers are already in memory.

For each APPLIED rewire — verify within the **cell instance block** (not file-wide):
```python
# inst_block = lines from cell_start to next ');' in the module buffer
inst_block_text = ''.join(inst_block)
if f".<pin>({old_net})" in inst_block_text:
    status = "VERIFY_FAILED"
    reason = f"old_net '{old_net}' still on pin '{pin}' after edit"
else:
    verified = True
```

For each INSERTED cell — verify the instance appears in the module buffer:
```python
module_text = ''.join(edited_module_lines)
if instance_name not in module_text:
    status = "VERIFY_FAILED"
    reason = f"instance '{instance_name}' not found in module buffer after insert"
```

For each PORT_DECL — verify signal appears in the port list range of the module buffer:
```python
port_list_text = ''.join(edited_module_lines[mod_start:port_list_close+1])
if signal_name not in port_list_text:
    status = "VERIFY_FAILED"
    reason = f"port '{signal_name}' not found in port list after insert"
```

**If any VERIFY_FAILED after in-memory check:** do NOT recompress. Retry the change on the module buffer. Only recompress (Step 5) when all in-memory verifications pass.

### Step 6b — Structural comparison (Synthesize only)

Compare PreEco vs PostEco driver of old_net vs new_net. Record old driver cell type, fanout, new driver cell type, fanout. Estimate timing impact as BETTER/LIKELY_BETTER/NEUTRAL/RISK/LOAD_RISK/UNCERTAIN with 1-sentence reasoning.

### Step 7 — Cleanup (once per stage)

```bash
rm -f /tmp/eco_apply_<TAG>_<Stage>.v
```

---

## ALREADY_APPLIED Detection — Per-Type Rules

**CRITICAL:** `ALREADY_APPLIED` is a valid status but MUST be based on a specific, type-appropriate check — NOT a broad grep that finds the signal anywhere in the file. A signal name can exist in the file as a wire without being in the right place (e.g., `NeedFreqAdj` appears as a DFF output but is NOT in the module port list). Always record `already_applied_reason` in the JSON with exactly what check was performed and what was found.

| change_type | ALREADY_APPLIED condition | What to check |
|-------------|--------------------------|---------------|
| `new_logic_dff` / `new_logic_gate` / `new_logic` | Instance name already in the file | `grep -c "^\s*<cell_type>\s*<instance_name>\s*(" netlist` ≥ 1 |
| `rewire` | new_net already on the target pin | `grep -c "\.<pin>(<new_net>)" <cell_block>` = 1 (scoped to cell block) |
| `port_declaration` (`input`\|`output`) | Signal already in the MODULE PORT LIST | Parse port list from `mod_idx` to `port_list_close_idx` — check if `<signal_name>` appears in that range. Signal present ONLY in the module body (as a wire or DFF output) does NOT count. |
| `port_declaration` (`wire`) | Wire declaration already in module body | `grep -c "^\s*wire\s\+<signal_name>\s*;" <module_scope>` ≥ 1 |
| `port_promotion` | Declaration already changed to `output` | `grep -c "output\s\+<signal_name>\s*;" <module_scope>` ≥ 1 |
| `port_connection` | Port connection already in instance block | `grep -c "\.<port_name>(\s*<net_name>\s*)" <instance_block>` ≥ 1 (scoped to instance block) |

**PORT_DECL ALREADY_APPLIED example (correct):**
```python
# Read lines in port list range (mod_idx to port_list_close_idx inclusive)
port_list_text = ''.join(lines[mod_idx:port_list_close_idx + 1])
if re.search(rf'\b{re.escape(signal_name)}\b', port_list_text):
    # Signal is in the port list header — ALREADY_APPLIED
    record(status="ALREADY_APPLIED",
           already_applied_reason=f"signal '{signal_name}' found in port list of module '{module_name}' (lines {mod_idx}–{port_list_close_idx})")
else:
    # Signal is NOT in the port list — apply Steps 2–4
    ...
```

**PORT_DECL ALREADY_APPLIED example (WRONG — do not do this):**
```python
count = int(run(f"grep -cw '{signal_name}' {temp_file}"))
if count >= 1:
    record(status="ALREADY_APPLIED")  # WRONG — finds the DFF output, not the port
```

---

## Special Cases

| Case | Action |
|------|--------|
| `change_type=rewire`, `new_net` exists in PostEco | Rewire path (4b) |
| `change_type=rewire`, `new_net` absent, `old_net` found (implies inversion of old_net) | Inverter path (4c) — auto-insert INV cell using `old_net` as source_net |
| `change_type=rewire`, `new_net` absent, `old_net` also absent | SKIPPED — "source_net (old_net) not found in PostEco" |
| `change_type=new_logic_dff` | DFF insertion path (4c-DFF) — Pass 1 |
| `change_type=new_logic_gate` | Gate insertion path (4c-GATE) — Pass 1 |
| `change_type=port_declaration`, `declaration_type=input\|output` | Port list + direction declaration update (4c-PORT_DECL Steps 2–4) — Pass 2 |
| `change_type=port_declaration`, `declaration_type=wire` | Wire declaration in module body only (4c-PORT_DECL Step 4-WIRE) — NO port list change — Pass 2 |
| `change_type=port_promotion` | Wire → output promotion (4c-PORT_PROMO) — Pass 2 |
| `change_type=port_connection` | Instance port connection addition (4c-PORT_CONN) — Pass 3 |
| `change_type=rewire` with `new_logic_dependency` | Must be in Pass 4 — after Pass 1 new_logic insertions |
| Input signal missing in PostEco, `input_from_change` set | Process the dependency change first, then retry |
| Input signal missing, no dependency | SKIPPED — "input signal not found in PostEco" |
| Cell not in PostEco | SKIPPED — cell may have been optimized away |
| old_net not on pin | SKIPPED — PostEco may differ from PreEco structurally |
| Occurrence count > 1 | SKIPPED + AMBIGUOUS — cannot safely change without risk |
| Backup already exists | Overwrite — always back up to `<Stage>.v.gz.bak_<TAG>_round<ROUND>` |

---

## Output JSON

Write `data/<TAG>_eco_applied_round<ROUND>.json`. Each stage is an array — one entry per cell from the PreEco study.

**Every entry MUST include a `reason` (or `already_applied_reason`) field** regardless of status. This is used by the ORCHESTRATOR to generate a human-readable RPT that explains every decision.

```json
{
  "Synthesize": [
    {
      "cell_name": "<cell_name>",
      "cell_type": "<cell_type>",
      "pin": "<pin>",
      "old_net": "<old_signal>",
      "new_net": "<new_signal>",
      "change_type": "rewire",
      "status": "APPLIED",
      "reason": "pin .<pin>(<old_signal>) found at line <N>, replaced with .<pin>(<new_signal>)",
      "occurrence_count": 1,
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff",
      "target_register": "<signal_name>",
      "instance_scope": "<INST_A>/<INST_B>",
      "cell_type": "<DFF_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<INST_A>/<INST_B>/eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "port_connections": {"<clk_pin>": "<clk_net>", "<data_pin>": "<data_net>", "<reset_pin>": "<reset_net>", "<q_pin>": "n_eco_<jira>_<seq>"},
      "status": "INSERTED",
      "reason": "DFF <cell_type> eco_<jira>_<seq> inserted at line <N> in scope <INST_A>/<INST_B>; output Q → <output_net>",
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff",
      "instance_name": "eco_<jira>_<seq>",
      "status": "ALREADY_APPLIED",
      "already_applied_reason": "instance 'eco_<jira>_<seq>' already present at line <N> — found by grep '^\\s*<cell_type>\\s*eco_<jira>_<seq>\\s*(' = 1"
    },
    {
      "change_type": "port_declaration",
      "signal_name": "<signal_name>",
      "module_name": "<module_name>",
      "declaration_type": "output",
      "status": "ALREADY_APPLIED",
      "already_applied_reason": "signal '<signal_name>' found in port list of module '<module_name>' (lines <mod_idx>–<port_list_close_idx>)"
    },
    {
      "change_type": "port_declaration",
      "signal_name": "<signal_name>",
      "module_name": "<module_name>",
      "declaration_type": "output",
      "status": "APPLIED",
      "reason": "added '<signal_name>' to port list at line <N>; added 'output <signal_name> ;' at line <M>"
    },
    {
      "change_type": "port_connection",
      "port_name": "<port_name>",
      "net_name": "<net_name>",
      "instance_name": "<instance_name>",
      "status": "SKIPPED",
      "reason": "instance '<instance_name>' not found in module '<parent_module>' scope (lines <mod_idx>–<endmodule_idx>)"
    }
  ],
  "PrePlace": [...],
  "Route": [...],
  "summary": {
    "total": <count of all entries across all stages>,
    "applied": <count of APPLIED entries>,
    "inserted": <count of INSERTED entries>,
    "already_applied": <count of ALREADY_APPLIED entries>,
    "skipped": <count of SKIPPED entries>,
    "verify_failed": <count of VERIFY_FAILED entries>
  }
}
```

**Your final output is `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`.** After writing it, verify it is non-empty and contains a `summary` field, then exit. Do NOT write the RPT — the calling orchestrator reads the JSON and generates the RPT.

---

## Critical Safety Rules

1. **NEVER edit if occurrence count > 1** — ambiguity means you cannot be sure which instance to change; mark SKIPPED + AMBIGUOUS instead
2. **NEVER do global search-replace** — scope all changes to the specific cell instance block; `old_net` may legitimately appear on other pins
3. **ALWAYS backup before decompressing** — one backup per stage per round, before any edits; include round number in the backup name
4. **Consistent instance naming across stages** — `eco_<jira>_<seq>` must be the same name in Synthesize, PrePlace, and Route for the same logical change; FM stage-to-stage matching requires identical instance names; D-input chain gates use `eco_<jira>_d<seq>` (with `d` prefix) and nets `n_eco_<jira>_d<seq>`
5. **ALWAYS verify after recompressing** — confirm old_net count drops to 0 in the scoped block and new cell is present; global grep gives false results
6. **Keep processing remaining cells if one is SKIPPED** — a SKIPPED cell does not abort the stage; continue with all remaining confirmed entries
7. **Polarity rule** — only use Step 4c (inverter) when new_net is an inverted signal (`~source_net`); for DFF or gate new_logic, use 4c-DFF or 4c-GATE respectively — never SKIPPED simply because it is not a simple inversion
8. **Dependency order** — always insert new_logic cells (Pass 1) before rewires that depend on their output nets (Pass 4); never attempt rewire when new_net is a `n_eco_<jira>_<seq>` that hasn't been inserted yet; `input_from_change` dependencies within D-input chains are guaranteed by eco_netlist_studier
9. **Use per-stage port_connections for DFF** — always read `port_connections_per_stage[<Stage>]` from the study JSON; fall back to flat `port_connections` only if absent; never assume signal names valid in Synthesize are also present in PrePlace or Route
10. **Detect netlist type before every stage** — run `grep -c "^module " <temp_file>` before processing; if count > 1 (hierarchical), `port_declaration` and `port_connection` entries are mandatory and `flat_net_confirmed`/`no_gate_needed` flags are ignored
