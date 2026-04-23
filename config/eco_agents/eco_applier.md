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

### Step 3 — Decompress (once per stage)

```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz > /tmp/eco_apply_<TAG>_<Stage>.v
```

### Step 4 — Process each confirmed cell (loop over stage array)

For each entry in the stage array where `"confirmed": true`, perform steps 4a–4e on the **same temp file**:

#### 4a — Detect change type

**CRITICAL — Which `new_net` value to use:**
- If `new_net_alias` is **null** → use `new_net` (direct signal name) for all checks and rewires
- If `new_net_alias` is **non-null** → use `new_net_alias` (HFS alias) instead of `new_net`

Check if the effective `new_net` exists in the PostEco temp file:

```bash
grep -cw "<effective_new_net>" /tmp/eco_apply_<TAG>_<Stage>.v
```

- If count ≥ 1 → **rewire** (normal path, go to 4b)
- If count = 0 → **new_logic** (new_net doesn't exist, go to 4c)

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

The `new_net` is the inverted form of some existing net. Determine `source_net`:
- If RTL diff shows `~<signal>` → `source_net = <signal>` (strip the `~`)
- Verify `source_net` exists in the PostEco temp file: `grep -cw "<source_net>"` ≥ 1

If `source_net` not found: SKIPPED, reason="source_net not found in PostEco — cannot insert inverter"

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
If no neighbour DFF found → use the value from the Synthesize entry of `port_connections_per_stage` as a fallback. Record the fallback reason.

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

**Step 1 — Verify all input signals exist:**
```bash
for each input_net in port_connections.values() (excluding output pin):
    grep -cw "<input_net>" /tmp/eco_apply_<TAG>_<Stage>.v  # must be ≥ 1
```
If any input is a new_logic output (`n_eco_<jira>_<seq>`) — verify that new_logic entry was already processed in Pass 1.

**Step 2 — Find gate cell type from PreEco netlist matching `gate_function`:**
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -E "^[[:space:]]*(NAND2|ND2|NR2|NOR2|AND2|OR2)[A-Z0-9]* [a-z]" | head -3
```
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

For entries with `change_type: "port_declaration"` (new input or output port, NOT previously in port list):

> **MANDATORY pre-check:** Confirm `netlist_type` from Step 0. If hierarchical — always apply, regardless of any `flat_net_confirmed` or `no_gate_needed` flags. If flat — use `port_promotion` path instead.

**Step 1 — Find module definition line:**
```bash
grep -n "^module <module_name> \|^module <module_name>(" /tmp/eco_apply_<TAG>_<Stage>.v | head -3
```

**Step 2 — Add signal to module port list (handles multi-line port lists):**

```python
mod_idx = next(i for i, l in enumerate(lines) if 'module <module_name>' in l)
# Find the closing ');' of the port list — may be many lines after 'module' line
close_idx = next(
    i for i in range(mod_idx + 1, len(lines))
    if lines[i].strip() in (');', ') ;', ');  // end of port list')
)
# Insert signal name before the closing ')'
lines[close_idx] = lines[close_idx].replace(');', f', <signal_name>\n);')
```

**Step 3 — Add declaration in module body:**

Add declaration line after existing declarations (before the first cell instantiation):
```verilog
  input/output  <signal_name> ;
```

Record: `status=APPLIED`, `change_type=port_declaration`.

---

#### Shared — Find Module Boundary

The following procedure is used identically by PORT_PROMO and PORT_CONN. Apply it as Step 1 in each:

```python
# Find start: exact module name match (full line, not substring)
mod_idx = next(
    i for i, l in enumerate(lines)
    if re.match(rf'^module\s+{re.escape(module_name)}\s*[(\s]', l)
)

# Find end: first 'endmodule' AFTER mod_idx
endmodule_idx = next(
    i for i in range(mod_idx + 1, len(lines))
    if lines[i].strip() == 'endmodule'
)
```

**CRITICAL — exact module name match:** Use `^module\s+<name>\s*[(\s]` — anchored at start of line, requiring whitespace or `(` after the module name. This prevents `<module_name>` from matching `<module_name>_submodule` or `<module_name>_variant`.

**CRITICAL — endmodule boundary:** All subsequent steps MUST only search and replace within `lines[mod_idx:endmodule_idx]`. Never search the entire file — sibling modules may have identical wire names in completely different contexts, causing mass failures across unrelated module variants.

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

### Step 5 — Recompress (once per stage, after ALL cells processed)

```bash
gzip -c /tmp/eco_apply_<TAG>_<Stage>.v > <REF_DIR>/data/PostEco/<Stage>.v.gz
```

### Step 6 — Verify all applied/inserted cells (once per stage)

**IMPORTANT:** Verification must be **scoped to the specific cell instance block**, not a global file-wide grep. `old_net` may legitimately appear on other cells' pins.

For each APPLIED cell — verify the specific cell's pin no longer has old_net:
```python
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz > /tmp/eco_verify_<TAG>_<Stage>.v
cell_start = grep -n "<cell_name>" /tmp/eco_verify file → get line number
# Read from cell_start to next ");" → extract instance block
# Check ".<pin>(<old_net>)" is NOT in that block
if ".<pin>(<old_net>)" in instance_block: VERIFY_FAILED
else: verified=true
```

For each INSERTED cell — verify the new inverter instance exists anywhere in the file:
```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | grep -c "<inv_inst>"
```
Expected: ≥ 1. If 0: mark VERIFY_FAILED.

Cleanup temp verify file:
```bash
rm -f /tmp/eco_verify_<TAG>_<Stage>.v
```

### Step 6b — Structural comparison (Synthesize only)

Compare PreEco vs PostEco driver of old_net vs new_net. Record old driver cell type, fanout, new driver cell type, fanout. Estimate timing impact as BETTER/LIKELY_BETTER/NEUTRAL/RISK/LOAD_RISK/UNCERTAIN with 1-sentence reasoning.

### Step 7 — Cleanup (once per stage)

```bash
rm -f /tmp/eco_apply_<TAG>_<Stage>.v
```

---

## Special Cases

| Case | Action |
|------|--------|
| `change_type=rewire`, `new_net` exists in PostEco | Rewire path (4b) |
| `change_type=rewire`, `new_net` absent, source_net found | Inverter path (4c) — auto-insert INV cell |
| `change_type=rewire`, `new_net` absent, source_net also absent | SKIPPED — "source_net not found" |
| `change_type=new_logic_dff` | DFF insertion path (4c-DFF) — Pass 1 |
| `change_type=new_logic_gate` | Gate insertion path (4c-GATE) — Pass 1 |
| `change_type=port_declaration` | Port list + declaration update (4c-PORT_DECL) — Pass 2 |
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

Write `data/<TAG>_eco_applied_round<ROUND>.json`. Each stage is an array — one entry per cell from the PreEco study:

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
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    }
  ],
  "PrePlace": [...],
  "Route": [...],
  "summary": {
    "total": 6,
    "applied": 3,
    "inserted": 1,
    "skipped": 2,
    "verify_failed": 0
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
