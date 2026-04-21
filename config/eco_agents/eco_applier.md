# ECO Applier — PostEco Netlist Editor Specialist

**You are the ECO applier.** Read the PreEco study JSON, locate the same cells in PostEco netlists, verify old_net is still present on the expected pin, and apply the net substitution. For new_logic changes (where new_net doesn't exist), auto-insert a new inverter cell. Always backup before editing.

**Inputs:** REF_DIR, TAG, BASE_DIR, JIRA, ROUND (current fix round — 1 for initial run), PreEco study JSON (`data/<TAG>_eco_preeco_study.json`)

---

## CRITICAL: Processing Order — new_logic BEFORE wire_swap

The PreEco study JSON may contain two types of entries:
- `new_logic_dff` / `new_logic_gate` — new cell insertions
- `rewire` — net substitutions (some may have `new_logic_dependency`)

**You MUST process in this order within each stage:**
1. **Pass 1 — new_logic insertions first** (DFF and gate entries): insert all new cells so their output nets (`n_eco_<jira>_<seq>`) exist in the temp file
2. **Pass 2 — rewire entries**: now `new_net` (which may be `n_eco_<jira>_<seq>`) exists → rewire path (4b) succeeds

If you attempt rewire before new_logic insertion, `grep -cw "<new_net>"` returns 0 → falls to new_logic path → fails. Always insert new cells first.

---

## CRITICAL: One Decompress/Recompress Per Stage

The PreEco study JSON contains an **array** of cells per stage. You MUST process ALL entries for a stage within a single decompress/recompress cycle — do NOT decompress, edit, and recompress per cell. The correct flow is:

1. **Once per stage**: backup → decompress to temp file
2. **For each confirmed cell**: find cell → verify → apply (all in the same temp file)
3. **Once per stage**: recompress from temp → verify all → cleanup

---

## Process Per Stage (Synthesize, PrePlace, Route)

For each stage key in the PreEco study JSON:

### Step 1 — Check for confirmed entries

Before doing any file I/O, scan the stage array for entries where `"confirmed": true`.

- If the stage array is empty or has no confirmed entries: write all as SKIPPED with reason "no confirmed cells from PreEco study", skip to next stage.
- If any confirmed entries exist: proceed to Step 2.

### Step 2 — Backup (once per stage)

Include the round number in the backup name so each round has its own backup. This allows reverting to the correct pre-round state when the fixer loop retries:

```bash
cp <REF_DIR>/data/PostEco/<Stage>.v.gz \
   <REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>
```

Example: `Synthesize.v.gz.bak_20260414021834_round1`, `Synthesize.v.gz.bak_20260414021834_round2`

### Step 3 — Decompress (once per stage)

```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz > /tmp/eco_apply_<TAG>_<Stage>.v
```

### Step 4 — Process each confirmed cell (loop over stage array)

For each entry in the stage array where `"confirmed": true`, perform steps 4a–4e on the **same temp file**:

#### 4a — Detect change type

**CRITICAL — Which `new_net` value to use:**
- If `new_net_alias` is **null** in the study JSON → use `new_net` (direct signal name) for all checks and rewires
- If `new_net_alias` is **non-null** in the study JSON → use `new_net_alias` (HFS alias) for all checks and rewires instead of `new_net`

In practice: if the netlist studier followed Priority 1 correctly, `new_net_alias` will always be null and you use the direct `new_net`. `new_net_alias` is only set when Priority 1 failed (direct name absent) and Priority 2 found an HFS alias.

Check if the effective `new_net` (direct or alias per above) exists in the PostEco temp file:

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
- ` [a-z]` — followed by a space then lowercase letter (start of instance name, which is typically lowercase in synthesis netlists)
- `grep -v "//"` — exclude comments

Extract the cell type from the first field of the matching line:
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

**Example — two distinct changes:**
```
Change A: old=<old_signal_A>, new=~<new_signal_A> → eco_<jira>_001 (all stages)
Change B: old=<old_signal_B>, new=~<new_signal_B> → eco_<jira>_002 (all stages)
```

This ensures consistent naming across stages for FM stage-to-stage matching.

**Step 4c-4: Insert inverter instantiation**

Find the correct module scope — the inverter must go inside the **same module that contains the target cell**, not the last `endmodule` in the file (which may belong to a different module).

```bash
# Step 1: Find the target cell's line number
cell_line=$(grep -n "<cell_name>" /tmp/eco_apply_<TAG>_<Stage>.v | head -1 | cut -d: -f1)

# Step 2: Find the next endmodule AFTER the target cell's line
endmodule_line=$(awk -v start=$cell_line 'NR > start && /endmodule/ {print NR; exit}' \
                 /tmp/eco_apply_<TAG>_<Stage>.v)
```

Insert the new cell instantiation **one line before** that `endmodule`:

```verilog
  // ECO new_logic insert — TAG=<TAG>
  <CellType> <inv_inst> (.I(<source_net>), .ZN(<inv_out>));
```

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

Also record `inv_inst_full_path` — the full hierarchy path needed for the SVF `-instance` entry.

Read the hierarchy from `<BASE_DIR>/data/<TAG>_eco_rtl_diff.json` — the `nets_to_query` array has a `hierarchy` field (list of instance names from tile root to declaring module):

```python
rtl_diff = load("<BASE_DIR>/data/<TAG>_eco_rtl_diff.json")
# Find the entry matching old_net
hierarchy = next(n['hierarchy'] for n in rtl_diff['nets_to_query']
                 if n['net_path'].endswith(old_net) or old_net in n['net_path'])
# hierarchy = ["<INST_A>", "<INST_B>"]
hierarchy_path = "/".join(hierarchy)   # "<INST_A>/<INST_B>"

inv_inst_full_path = f"{TILE}/{hierarchy_path}/{inv_inst}"
# e.g. "<TILE>/<INST_A>/<INST_B>/eco_<jira>_001"
```

#### 4c-DFF — new_logic_dff path (insert new flip-flop)

For entries with `change_type: "new_logic_dff"` from the PreEco study JSON:

**Step 1 — Verify all input signals exist in PostEco temp file:**
```bash
grep -cw "<clock_net>"  /tmp/eco_apply_<TAG>_<Stage>.v   # must be ≥ 1
grep -cw "<reset_net>"  /tmp/eco_apply_<TAG>_<Stage>.v   # must be ≥ 1
grep -cw "<data_net>"   /tmp/eco_apply_<TAG>_<Stage>.v   # must be ≥ 1
```
If any input is missing AND it is produced by another `new_logic` entry → process that entry first (respect `input_from_change` dependency). If missing with no dependency → SKIPPED, reason="input signal not found in PostEco".

**Step 2 — Find DFF cell type from PreEco netlist (if not already found in this stage):**
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -E "^[[:space:]]*(DFF|DFQD|SDFQD)[A-Z0-9]* [a-z]" | head -3
```
Use the cell type recorded in the study JSON (`port_connections` provides the correct port names).

**Step 3 — Build port connection string from study JSON `port_connections`:**
```verilog
  // ECO new_logic_dff insert — TAG=<TAG> JIRA=<JIRA>
  <cell_type> <instance_name> (.<CK>(<clock_net>), .<D>(<data_net>), .<RN>(<reset_net>), .<Q>(<output_net>));
```
Use the exact port names from the study JSON `port_connections` map.

**Step 4 — Find correct module scope and insert** (same as Step 4c-4 for inverters):
```python
cell_line_idx = next(i for i, l in enumerate(lines) if '<any_existing_cell_in_same_scope>' in l)
endmodule_idx = next(i for i in range(cell_line_idx, len(lines)) if 'endmodule' in lines[i])
new_lines = ['  // ECO new_logic_dff — TAG=<TAG> JIRA=<JIRA>\n',
             '  <cell_type> <instance_name> (<port_connection_string>);\n']
lines[endmodule_idx:endmodule_idx] = new_lines
```

**Step 5 — Compute `inv_inst_full_path`** (same formula as inverter — needed by SVF updater):
```python
rtl_diff = load("<BASE_DIR>/data/<TAG>_eco_rtl_diff.json")
# Use instance_scope from study JSON entry
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
# For NAND2: grep for ND2 or NAND2
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

#### 4d — Find the cell in PostEco (rewire path only)

Already done in 4b. For new_logic, cell finding is part of 4c-5.

#### 4e — Move to next confirmed cell

Repeat 4a–4c/4d for every remaining confirmed cell in this stage array.

### Step 5 — Recompress (once per stage, after ALL cells processed)

```bash
gzip -c /tmp/eco_apply_<TAG>_<Stage>.v > <REF_DIR>/data/PostEco/<Stage>.v.gz
```

### Step 6 — Verify all applied/inserted cells (once per stage)

**IMPORTANT:** Verification must be **scoped to the specific cell instance block**, not a global file-wide grep. `old_net` may legitimately appear on other cells' pins — a global count would give false VERIFY_FAILED.

For each APPLIED cell — verify the specific cell's pin no longer has old_net:
```python
# Decompress and find the specific cell instance block
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

### Step 6b — Structural Comparison: PostEco vs PreEco Netlist

After verifying the ECO was applied, compare the **PostEco** cell structure against **PreEco** to confirm the structural change matches the timing/LOL estimation made in Step 3 (eco_netlist_studier).

This is only needed for **Synthesize** stage (most logical representation).

**What to compare:**

1. **Old net driver** (from PreEco) — what drove `old_net` before the ECO
2. **New net driver** (from PostEco) — what now drives the rewired pin

```bash
# In PreEco: find old_net driver
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -n "( <old_net> )" | head -10

# In PostEco: find new_net driver
zcat <REF_DIR>/data/PostEco/Synthesize.v.gz | grep -n "( <new_net> )" | head -10
```

Confirm the driver structures match the estimation from Step 3. If the PostEco structure reveals something unexpected (e.g., new_net is driven by a deeper combinational chain than estimated), **revise the timing estimate** accordingly.

Record the structural comparison result — this feeds into the final RPT and HTML timing/LOL section.

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
| `change_type=new_logic_dff` | DFF insertion path (4c-DFF) |
| `change_type=new_logic_gate` | Gate insertion path (4c-GATE) |
| `change_type=rewire` with `new_logic_dependency` | Must be processed in Pass 2 — after Pass 1 new_logic insertions create the `new_net` |
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
      "cell_name": "<cell_name>",
      "cell_type": "<CellType>",
      "pin": "<pin>",
      "old_net": "<old_signal>",
      "new_net": "<inv_out>",
      "change_type": "new_logic",
      "status": "INSERTED",
      "instance_name": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<INST_A>/<INST_B>/eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "source_net": "<source_net>",
      "cell_type": "<CellType>",
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
      "port_connections": {"CK": "<clk>", "D": "<data>", "RN": "<reset>", "Q": "n_eco_<jira>_<seq>"},
      "status": "INSERTED",
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_gate",
      "target_register": "<output_signal>",
      "instance_scope": "<INST_A>/<INST_B>",
      "cell_type": "<gate_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<INST_A>/<INST_B>/eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "gate_function": "<NAND2|NOR2|AND2|...>",
      "port_connections": {"A": "<input1>", "B": "<input2>", "ZN": "n_eco_<jira>_<seq>"},
      "status": "INSERTED",
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "cell_name": "<cell_name>",
      "pin": "<pin>",
      "old_net": "<old_signal>",
      "new_net": "<new_signal>",
      "change_type": "rewire",
      "status": "SKIPPED",
      "reason": "AMBIGUOUS — 2 occurrences of .<pin>(<old_signal>) found"
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

---

## Critical Safety Rules

## Output RPT

After writing the JSON, write `<BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt` then copy to `AI_ECO_FLOW_DIR`:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
```

**Key requirement:** For every cell entry, the RPT must clearly state:
1. **Which RTL block this ECO targets** — the declaring module and instance hierarchy
2. **Why this ECO fix is being applied** — what RTL change drove it, what the functional intent is
3. **What was done exactly** — the specific net substitution or cell insertion, with the actual Verilog evidence
4. **What was decided for SKIPPED entries** — the exact reason, so engineers know what needs manual attention

```
================================================================================
STEP 4 — ECO APPLIED  (Round <ROUND>)
Tag: <TAG>  |  JIRA: DEUMCIPRTL-<JIRA>
================================================================================

ECO Intent (from Step 1 RTL diff):
  RTL Change : <change_type> in <module_name> (<file>)
  Signal Swap: <old_token>  →  <new_token>
  Functional : The RTL engineer changed which signal drives <expression_context>.
               Gate-level netlists must be updated to reflect this — wherever
               <old_token> feeds the relevant logic in module <module_name>,
               it must be rewired to <new_token> instead.

Summary: <applied> applied / <inserted> inserted / <skipped> skipped / <verify_failed> verify failed
Backup : <Stage>.v.gz.bak_<TAG>_round<ROUND>  (created before any edit)

<For each stage (Synthesize, PrePlace, Route):>
────────────────────────────────────────────────────────────────────────────────
[<Stage>]
  RTL Block : <module_name>  (instance path: <TILE>/<INST_A>/.../<INST_B>)
────────────────────────────────────────────────────────────────────────────────

  Cell [<n>/<total>]  —  <APPLIED / INSERTED / SKIPPED / VERIFY_FAILED>
  ──────────────────────────────────────────────────────────────────────
  Cell Name : <cell_name>  (<cell_type>)
  Block     : <TILE>/<INST_A>/.../<INST_B>/<cell_name>
  Pin       : <pin>

  Why This Fix:
    This cell is the gate-level implementation of the <old_token> →
    <new_token> RTL change in <module_name>. PreEco study (Step 3)
    confirmed <old_net> on pin <pin>. The ECO rewires this pin so the
    downstream logic (<output_net> → ...) now receives <new_net> instead,
    matching the updated RTL intent.

  <If rewire (APPLIED):>
  Action    : Rewire  .<pin>(<old_net>)  →  .<pin>(<new_net>)
  Scope     : Changed only within the <cell_name> instance block (line-range
              scoped — no global substitution performed)
  Occurrence: <N> occurrence(s) of .<pin>(<old_net>) in PostEco — must be 1
  Verified  : YES — post-edit grep confirms .<pin>(<old_net>) no longer present
              in the <cell_name> instance block
  Backup    : <backup_path>

  <If new_logic (INSERTED):>
  Action    : Insert new inverter — <old_net> must be inverted to produce
              <new_net>. Since <new_net> does not exist in this stage's
              PostEco netlist, a new inverter cell is auto-inserted.
  Inverter  : <inv_cell_type>  <inv_inst>
              Input  : <source_net>  (the non-inverted form of <new_net>)
              Output : <inv_out>  (used as new_net substitute)
  Full Path : <inv_inst_full_path>
  Then      : Rewired  .<pin>(<old_net>)  →  .<pin>(<inv_out>)
  Placement : Inserted inside <module_name> scope (before its endmodule),
              not at file-level endmodule
  Verified  : YES — grep confirms <inv_inst> present in recompressed PostEco

  <If SKIPPED:>
  Action    : No change made to this cell in <Stage>
  Reason    : <specific reason — e.g.:
               "AMBIGUOUS — 3 occurrences of .<pin>(<old_net>) found in PostEco;
                cannot safely scope the replacement without risk of modifying
                unrelated logic. Manual review required."
               OR
               "cell not found in PostEco — may have been optimized away
                during P&R. No ECO needed for this instance."
               OR
               "new_net <new_net> absent from PostEco and source_net
                <source_net> also not found — cannot insert inverter.
                Manual fix required.">
  Engineer  : <what the engineer should do about this, if anything>

  ···  (repeat Cell block for each cell)

<If stage had no confirmed cells from Step 3:>
  [<Stage>] — SKIPPED (no confirmed cells from PreEco study)
  Reason    : Step 3 found no qualifying cells for this stage. No PostEco
              edits were made. Backup was not created.

--------------------------------------------------------------------------------
TIMING & LOL ESTIMATION  (Synthesize stage structural analysis)
--------------------------------------------------------------------------------

  Old Net Driver : <driver_cell_name>  (<driver_cell_type>)  pin=<Z/ZN/Q>
                   Structure: <e.g. "combinational NAND2, inputs from FF outputs"
                               OR "FF Q output — direct register launch point">
  Old Net Fanout : <N> connections in Synthesize PreEco netlist

  New Net Driver : <driver_cell_name>  (<driver_cell_type>)  pin=<Z/ZN/Q>
                   Structure: <e.g. "FF Q output — direct register launch point"
                               OR "combinational INV, input from FF Q">
  New Net Fanout : <N> connections in Synthesize PreEco netlist

  LOL Impact     : <e.g. "new_net is driven directly by FF Q — shallower than
                           old_net which passes through a combinational chain.
                           Replacing old_net reduces logic depth at this pin.">
  Timing Estimate: <BETTER / LIKELY_BETTER / NEUTRAL / RISK / LOAD_RISK / UNCERTAIN>
  Reasoning      : <1-2 sentences — plain English explanation why timing will
                    improve, worsen, or stay the same based on the structure>

  PostEco Confirm: <"Structural match confirmed — estimate stands" /
                    "Revised — PostEco reveals <difference>, estimate updated to <new>">

================================================================================
```

---

## Critical Safety Rules

1. **NEVER edit if occurrence count > 1** — ambiguity means you cannot be sure which instance to change
2. **NEVER do global search-replace** — scope all changes to the specific cell instance block
3. **ALWAYS backup before decompressing** — one backup per stage, before any edits
4. **For new_logic: use same instance name across all stages** — consistent naming is required for FM stage-to-stage matching
5. **ALWAYS verify after recompressing** — confirm old_net count drops to 0 and new cell is present
6. **ONE decompress per stage** — decompress once, apply ALL confirmed cells, then recompress once
7. **Keep processing remaining cells if one is SKIPPED** — a SKIPPED cell does not abort the stage
8. **Polarity rule** — only use Step 4c (inverter) when new_net is an inverted signal (`~source_net`); for DFF or gate new_logic, use 4c-DFF or 4c-GATE respectively — never SKIPPED simply because it is not a simple inversion
9. **Dependency order** — always insert new_logic cells (Pass 1) before attempting rewires that depend on their output nets (Pass 2); never attempt rewire when new_net is a `n_eco_<jira>_<seq>` that hasn't been inserted yet
10. **Consistent instance naming across stages** — `eco_<jira>_<seq>` must be the same name in Synthesize, PrePlace, and Route for the same logical change — FM stage-to-stage matching requires identical instance names
11. **D-input chain naming convention** — DFF D-input intermediate gates use `eco_<jira>_d<seq>` (with `d` prefix) and nets `n_eco_<jira>_d<seq>`. These are distinct from the main ECO cells (`eco_<jira>_001`, `eco_<jira>_002`, etc.) and are always processed in Pass 1 before the DFF itself. The dependency ordering within the chain is guaranteed by `input_from_change` fields set by eco_netlist_studier.
