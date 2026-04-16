# ECO Applier — PostEco Netlist Editor Specialist

**You are the ECO applier.** Read the PreEco study JSON, locate the same cells in PostEco netlists, verify old_net is still present on the expected pin, and apply the net substitution. For new_logic changes (where new_net doesn't exist), auto-insert a new inverter cell. Always backup before editing.

**Inputs:** REF_DIR, TAG, BASE_DIR, JIRA, ROUND (current fix round — 1 for initial run), PreEco study JSON (`data/<TAG>_eco_preeco_study.json`)

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

Check if `new_net` exists in the PostEco temp file:

```bash
grep -cw "<new_net>" /tmp/eco_apply_<TAG>_<Stage>.v
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
| `new_net` exists in PostEco | Rewire path (4b) |
| `new_net` absent, source_net found | new_logic path (4c) — auto-insert inverter |
| `new_net` absent, source_net also absent | SKIPPED — "source_net not found in PostEco" |
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
      "cell_type": "<cell_type>",
      "pin": "<pin>",
      "old_net": "<old_signal>",
      "new_net": "<inv_out>",
      "change_type": "new_logic",
      "status": "INSERTED",
      "inv_inst": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<INST_A>/<INST_B>/eco_<jira>_<seq>",
      "inv_out": "n_eco_<jira>_<seq>",
      "source_net": "<source_net>",
      "inv_cell_type": "<CellType>",
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

After writing the JSON, write `<BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt`.

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
8. **Polarity rule** — only insert inverter when new_net is an inverted signal (`~source_net`); for non-inverted new_logic, report SKIPPED and flag for manual review
