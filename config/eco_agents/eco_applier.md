# ECO Applier — PostEco Netlist Editor Specialist

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else.

---

## 1. Overview

**Role:** Read the PreEco study JSON, locate cells in PostEco netlists, verify old nets on expected pins, apply net substitutions, and auto-insert new cells for `new_logic` changes.

**Inputs:** `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (1 = initial, 2+ = surgical patch)

**Outputs:**
- Edited `<REF_DIR>/data/PostEco/{Synthesize,PrePlace,Route}.v.gz`
- `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`

**Working directory:** `<BASE_DIR>` (parent of `runs/`). No hardcoded signal/port/module names anywhere — all `<placeholder>` style.

---

## 2. Pre-Flight Checks

Run ONCE before decompressing any stage. Defends against concurrent agents corrupting PostEco between rounds.

### Round 1 — PostEco must match PreEco

```bash
for stage in Synthesize PrePlace Route; do
    preeco_md5=$(md5sum <REF_DIR>/data/PreEco/${stage}.v.gz | awk '{print $1}')
    posteco_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
    if [ "$preeco_md5" != "$posteco_md5" ]; then
        cp <REF_DIR>/data/PreEco/${stage}.v.gz <REF_DIR>/data/PostEco/${stage}.v.gz
        restored_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
        [ "$restored_md5" != "$preeco_md5" ] && echo "ERROR: Restore failed for ${stage}. ABORT." && exit 1
        PREFLIGHT_RESTORED_STAGES+=("$stage")
    fi
done
```

### Round 2+ — PostEco must match ROUND_ORCHESTRATOR backup

```bash
for stage in Synthesize PrePlace Route; do
    bak=<REF_DIR>/data/PostEco/${stage}.v.gz.bak_<TAG>_round<ROUND>
    posteco_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
    backup_md5=$(md5sum ${bak} | awk '{print $1}')
    if [ "$posteco_md5" != "$backup_md5" ]; then
        cp ${bak} <REF_DIR>/data/PostEco/${stage}.v.gz
        restored_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
        [ "$restored_md5" != "$backup_md5" ] && echo "ERROR: Restore failed for ${stage}. ABORT." && exit 1
        PREFLIGHT_RESTORED_STAGES+=("$stage")
    fi
done
```

After either loop: set `pre_flight_restore: true` and `pre_flight_restored_stages: [...]` in the applied JSON if any stage was restored. MD5 is used (not grep) because it catches ALL changes from any source — a concurrent agent can corrupt a port list without touching any eco instance.

---

## 3. Global Setup

### 3a — Mode Determination

**Round 1 (Full Apply):** All study JSON changes processed from scratch. PostEco = copy of PreEco (verified by pre-flight). Create backup before editing: `<Stage>.v.gz.bak_<TAG>_round1`.

**Round 2+ (Surgical Patch):** PostEco contains previous rounds' correct changes — do NOT restore from any backup. ROUND_ORCHESTRATOR already backed up as `bak_<TAG>_round<ROUND>` — skip eco_applier's backup step. Read `eco_fm_analysis_round<ROUND-1>.json` → `revised_changes` list. For each study JSON entry:
- NOT in `revised_changes` AND `force_reapply: false` → mark ALREADY_APPLIED (skip)
- In `revised_changes` OR `force_reapply: true` → UNDO then RE-APPLY

### 3b — Global Seq Counter (build ONCE, shared across all 3 stages)

```python
seq_table = {}   # {change_id: eco_instance_name}
seq_counter = 1
for entry in all_confirmed_new_logic_entries:
    change_id = entry["change_id"]
    if change_id not in seq_table:
        seq_table[change_id] = f"eco_{JIRA}_{seq_counter:03d}"
        seq_counter += 1
# NEVER re-derive seq per stage — breaks FM's stage-to-stage matching
```

Instance naming: DFF → use `<target_register>_reg` (instance) / `<target_register>` (Q net) so FM auto-matches without `set_user_match`. Gates → `eco_<jira>_<seq>` (instance) / `n_eco_<jira>_<seq>` (output). D-input chain gates → `eco_<jira>_d<seq>`; condition gates → `eco_<jira>_c<seq>`.

### 3c — UNDO Logic (Surgical Patch Mode Only)

Before re-applying a `force_reapply: true` entry: check prior status in `data/<TAG>_eco_applied_round<ROUND-1>.json`. If prior status = `SKIPPED` → skip UNDO entirely, go straight to RE-APPLY. If prior status = `APPLIED`/`INSERTED` → verify element exists before removing; if not found → log and skip UNDO, proceed to RE-APPLY.

| change_type | Undo action |
|-------------|-------------|
| `rewire` | Find `.<pin>(<new_net>)` in cell block → replace with `.<pin>(<old_net>)` |
| `new_logic_gate` / `new_logic_dff` / `new_logic` | Find `<cell_type> <instance_name> (...)` block → remove including trailing `;` |
| `port_declaration` / `port_promotion` | Remove duplicate port or incorrect declaration line |
| `port_connection` | Revert `.<port>(<new_net>)` back to prior form |
| `wire_declaration` | Remove the explicit `wire <net_name>;` that caused FM-599 |
| `port_connection_duplicate` | Remove the duplicate `.<pin>(<net>)` line from instance block |

After undo, verify removed element is gone: `grep -c "<instance_name>"` must = 0.

---

## 4. Pass Order

Process ALL changes for a stage in 4 passes within each module's isolated buffer. Never mix order.

| Pass | change_type(s) | Why |
|------|----------------|-----|
| 1 | `new_logic_gate`, `new_logic_dff`, `new_logic` | Insert cells so output nets exist before rewires reference them |
| 2 | `port_declaration`, `port_promotion` | Add ports/directions before connections reference them |
| 3 | `port_connection` | Add `.port(net)` to submodule instances after ports exist |
| 4 | `rewire` | Change pin connections last — may depend on new cells AND new ports |

**ONE decompress per stage.** Decompress once → apply ALL passes to the same temp file → recompress once.

**Per-stage setup (S0–S4):**
- **S0 — Netlist type:** `grep -c "^module "` — count > 1 = hierarchical (port_declaration and port_connection mandatory; flat_net_confirmed/no_gate_needed ignored); count = 1 = flat.
- **S1 — Confirmed entries:** If none with `"confirmed": true` → write all SKIPPED, skip to next stage.
- **S2 — Backup:** Round 1: `cp <Stage>.v.gz <Stage>.v.gz.bak_<TAG>_round1`. Round 2+: skip.
- **S3 — Decompress strategy:** Group confirmed changes by module. Stream through compressed file — extract target modules into memory buffer, pass others through unchanged unchanged.
- **S4 — ALREADY_APPLIED pre-snapshot:** Snapshot original module buffer BEFORE any changes. Run ALREADY_APPLIED checks against this snapshot, not the modified buffer. For Round 1 `new_logic` entries flagged ALREADY_APPLIED → add `"warning": "UNEXPECTED in Round 1 — concurrent agent suspected"`.

---

## 5. Pass 1 — new_logic Insertions

### Pass 1a — rewire (new_net exists in PostEco)

When `change_type == "rewire"` and effective `new_net` found in temp file (`new_net_alias` if non-null, else `new_net`):
1. Find cell: `grep -n "<cell_name>"` — not found → SKIPPED.
2. Verify `old_net` on expected pin (count = 1); count > 1 → SKIPPED with reason AMBIGUOUS.
3. Replace within cell instance block scope only: `.<pin_name>(<old_net>)` → `.<pin_name>(<new_net>)`.
4. Record status=APPLIED, change_type=rewire.

### Pass 1b — Inverter insertion (new_net absent)

When `change_type == "rewire"` and effective `new_net` absent in PostEco:
1. Verify `old_net` (source_net) exists; if not → SKIPPED.
2. Find inverter cell type from this stage's PreEco: `grep -E "^[[:space:]]*INV[A-Z0-9]+ [a-z]"`. Try Synthesize if not in this stage. **Cross-stage cell type validation:** after finding cell_type in Synthesize PreEco, verify it also appears in PrePlace and Route PreEco (`grep -c "<cell_type>"` >= 1 in each). If missing in any stage → find an alternative for that stage.
3. Derive from seq_table: `inv_inst = eco_<jira>_<seq>`, `inv_out = n_eco_<jira>_<seq>`.
4. Insert before the `endmodule` enclosing the target cell:
   ```verilog
   // ECO inverter insert — TAG=<TAG> JIRA=<JIRA>
   <cell_type> <inv_inst> (.I(<source_net>), .ZN(<inv_out>));
   ```
5. Rewire target pin from `old_net` → `inv_out` (scoped to cell block).
6. Compute `inv_inst_full_path = f"{TILE}/{instance_scope}/{inv_inst}"`.
7. Record status=INSERTED, change_type=new_logic, inv_inst, inv_out, source_net, cell_type, inv_inst_full_path.

### Pass 1c — DFF insertion (new_logic_dff)

1. **Per-stage port connections:** Use `port_connections_per_stage[stage]`; fall back to `port_connections` only if absent. Validate all 3 stages have entries in `port_connections_per_stage`; if missing for a stage and clock/reset nets differ from Synthesize, log a WARNING about possible net mismatch.
2. **Functional pins** (clock, data, D-input chain): `grep -cw "<net>"` >= 1 required.
3. **Auxiliary pins** (scan, enable) — fallback hierarchy: (1) neighbour DFF in same module scope; (2) any DFF of same cell type anywhere in PostEco file; (3) Synthesize-derived value only for Synthesize stage. For PrePlace/Route with no DFF found → SKIPPED with reason "no DFF cell instance found in PostEco". Use flexible port extraction: `re.findall(r'\.\s*(\w+)\s*\(', cell_block_text)`.
4. **Cell type in this stage:** `zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -m1 "<dff_cell_type>"`. If absent → find variant used in this stage.
5. **Instance scope validation:** Verify module matching `instance_scope` exists in current stage PostEco. If not found → try with `_0` suffix (P&R rename). Still not found → VERIFY_FAILED.
6. Insert: `<cell_type> <instance_name> (.<pin1>(<net1>), ...);` before enclosing `endmodule`.
7. Compute `inv_inst_full_path = f"{TILE}/{instance_scope}/{instance_name}"`.
8. Verify: `grep -c "<instance_name>"` in module buffer >= 1.
9. Record status=INSERTED, change_type=new_logic_dff, instance_name, inv_inst_full_path, output_net, cell_type.

### Pass 1d — Combinational gate insertion (new_logic_gate)

**Step 0 — NEEDS_NAMED_WIRE inputs (handle BEFORE other steps):**

If any input in `port_connections` starts with `NEEDS_NAMED_WIRE:<source_net>`:
- Derive `named_wire = f"eco_{JIRA}_{<signal_alias>}"`.
- Do NOT insert explicit `wire <named_wire>;` — the port connection creates an implicit wire; explicit causes FM-599.
- Find the port bus line and replace `source_net` → `named_wire` (scoped to instance block, exactly once).
- Verify `source_net` count = 0 in that line after replacement; verify `named_wire` appears >= 1 in module.
- Update `port_connections[input_pin] = named_wire`.
- Record: named_wire_inserted=true, named_wire, source_net_rewired.

**Step 1 — Verify ALL input signals exist:**

For each pin in `port_connections_per_stage[Stage]` (or `port_connections` if absent):
- Constants (`1'b0`, `1'b1`): valid — write directly in port connection, no tie cells needed.
- `NEEDS_NAMED_WIRE:*`: skip (resolved in Step 0).
- `UNRESOLVABLE_IN_<signal>`: grep the signal in current stage PostEco temp — found → use it; not found → SKIPPED.
- All other nets: `grep -cw "<net>"` in module buffer >= 1 required.

If any input missing → SKIPPED. NEVER insert a gate with missing input — floating input causes FM to misclassify downstream DFFs.

**Step 1b — Cell type port compatibility:** If `cell_type` provided, verify all required pins are in that cell's port list (flexible regex: `re.findall(r'\.\s*(\w+)\s*\(', block)`). If any pin missing → clear `cell_type` and re-search.

**Step 2 — Find gate cell type by port structure (technology-agnostic):**
```python
required_ports = set(port_connections.keys())
for line in module_scope_lines:
    m = re.match(r'^\s*([A-Z][A-Z0-9]+)\s+\w+\s*\(', line)
    if m:
        block = read_instance_block(line_idx, module_scope_lines)
        cell_ports = set(re.findall(r'\.\s*(\w+)\s*\(', block))
        if required_ports.issubset(cell_ports):
            cell_type = m.group(1); break
```
If no match in this stage → try other stages. Still none → SKIPPED. NEVER use bare generic primitives (`MUX2`, `AND2`, `OR2`) — FM cannot elaborate behavioral constructs.

**Step 3 — Insert** (same pattern as Pass 1c Step 6). **Step 4 — Compute `inv_inst_full_path`** and verify instance in module buffer.

Record status=INSERTED, change_type=new_logic_gate, instance_name, inv_inst_full_path, output_net, cell_type.

---

## 6. Pass 2 — port_declaration and port_promotion

### Pass 2a — port_declaration

**MANDATORY pre-check:** Hierarchical netlist → always apply regardless of `flat_net_confirmed` flag.

Read `declaration_type`:
- `"input"` or `"output"` → TRUE PORT DECLARATION — apply steps below.
- `"wire"` → SKIP (corresponding `port_connection` implicitly declares the wire; explicit `wire N;` causes FM-599). Record SKIPPED with reason "wire implicitly declared via port connections".

**BATCH all PORT_DECL changes for the same module in ONE modification** to avoid stale line numbers. Deduplicate by `signal_name` — last entry (force_reapply) wins; log which duplicate was discarded.

**Find port list close using parenthesis depth tracking:**
```python
depth = 0
for i in range(mod_idx, endmodule_idx):
    for ch in lines[i]:
        if ch == '(': depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0: port_list_close_idx = i; break
    if port_list_close_idx: break
```

**Validate found close line:** (1) `port_list_close_idx` must not be None; (2) line must NOT contain `.pin(` patterns — if it does, depth tracking hit a cell port connection, not the module port list → SKIPPED; (3) line must contain `)` (`rfind` = -1 would corrupt); (4) line should match `\)\s*;` — if not, advance to find the actual `) ;` on its own line.

**Insert signals before last `)` on close line:**
```python
new_sigs = ''.join(f' , {s}' for s in signal_names)
lines[port_list_close_idx] = close_line[:last_paren] + new_sigs + '\n)' + close_line[last_paren+1:]
```
Then verify port list depth = 0 after insertion.

**Insert direction declarations** after port list close (one line per signal). Each insert shifts subsequent indices — update `port_list_close_idx` accordingly.

**P&R note:** P&R port lists are much longer than Synthesize (scan, clock distribution, test ports). Never limit the depth-tracking search range — always scan `mod_idx` to `endmodule_idx`.

### Pass 2b — port_promotion

Signal already in module port list — do NOT add it again. Only change declaration keyword:
```python
re.sub(rf'\b(wire|reg)\b', 'output', lines[i], count=1)
```
Use `re.sub` with `\b` — plain `str.replace` matches partial occurrences within net names.

---

## 7. Pass 3 — port_connection

Find instance: `re.search(rf'\b{re.escape(submodule_pattern)}\s+{re.escape(instance_name)}\b', lines[i])`.

Find instance close using depth tracking. Validate close line: must NOT contain `.pin(` (would be an inner cell port connection); if it does, advance to find the actual `) ;` line.

**ALREADY_APPLIED:** `re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', instance_block)` — found → ALREADY_APPLIED. If still on `old_net` → set `force_reapply: true`.

**Insert:** `', .<port_name>( <net_name> )'` before last `)` on close line.

**Verify (instance-scoped, flexible whitespace):** `re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', instance_block)` — not found → VERIFY_FAILED.

---

## 8. Pass 4 — rewire

For existing cells where `new_net` already exists in PostEco.

**ALREADY_APPLIED:** `re.search(rf'\.\s*{re.escape(pin_name)}\s*\(\s*{re.escape(new_net)}\s*\)', cell_block)` — found → ALREADY_APPLIED. If still on `old_net` in Round 2+ → set `force_reapply: true`. If old_net not found either → SKIPPED (PostEco differs structurally).

Apply scoped replacement within cell instance block only. Never global replace.

---

## 9. Post-Apply Validation (Checks 1–8)

Run on the UNCOMPRESSED temp file BEFORE `gzip`. If ANY check fails: discard temp file, restore from backup, mark ALL affected entries VERIFY_FAILED, do NOT recompress.

**Check 1 — No duplicate ports in any module header.** Parse each module's port list `(...)`, collect port names, flag any appearing > 1 time.

**Check 2 — Port list correctly closed.** For each `module`, depth-track through up to 50000 chars — depth must return to 0 exactly once. Also check: any net declared as both explicit `wire N;` AND as `input`/`output N;` → FM-599 conflict → FAIL.

**Check 3 — No signal declared as both input and output.** Grep for `input`/`output` declarations, collect signal names, find duplicates across both directions.

**Check 4 — Module count unchanged (ERROR + hard exit, not warning).**
```bash
preeco_count=$(zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -c "^module ")
posteco_count=$(grep -c "^module " /tmp/eco_apply_<TAG>_<Stage>.v)
[ "$preeco_count" != "$posteco_count" ] && set summary.module_count_mismatch=true && exit 1
```
Mark ALL entries VERIFY_FAILED. Never proceed to recompress with wrong module count.

**Check 5 — No explicit wire conflicts with implicit port-connection wires.** For each module: collect `wire N;` explicit declarations and all nets appearing in `.anypin(N)` connections. Any overlap → FAIL. eco_applier NEVER adds explicit `wire N;` — every net is implicitly declared via port connections.

**Check 6 — No duplicate port connections in any instance block.** For each `<type> <inst> (...)` block, collect `.pin(` names, flag any appearing > 1 time.

**Check 7 — Every port in module header has a direction declaration in the body.** Parse port list names (excluding Verilog keywords), verify each has an `input`/`output`/`inout` declaration in the module body.

**Check 8 — Verilog netlist validator (strict mode).**
```bash
python3 script/validate_verilog_netlist.py --strict \
  --modules <touched_modules_space_separated> \
  -- /tmp/eco_apply_<TAG>_<Stage>.v
```
Touched modules = all entries with status INSERTED or APPLIED. Errors → mark ALL entries for affected modules as VERIFY_FAILED (not just the failing entry); record in `summary.verilog_validator.errors`; do NOT recompress.

**Cross-stage consistency check (after all 3 stages):**
- For each `change_id`, verify status is the same across Synthesize/PrePlace/Route. Asymmetric status for UNRESOLVABLE entries is expected — log as INFO, not WARNING.
- For each `new_logic` entry, verify the `eco_instance_name` from seq_table appears in all 3 stage PostEco netlists. Missing in any → WARNING.

---

## 10. Recompress and Output

**NEVER recompress if ANY entry has VERIFY_FAILED or `module_count_mismatch = true`.** Restore backup to PostEco, delete temp file.

**When all checks pass:**
```bash
gzip -c /tmp/eco_apply_<TAG>_<Stage>.v > <REF_DIR>/data/PostEco/<Stage>.v.gz
pre_lines=$(wc -l < /tmp/eco_apply_<TAG>_<Stage>.v)
post_lines=$(zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | wc -l)
diff=$(( post_lines - pre_lines ))
[ ${diff#-} -gt 5 ] && echo "ERROR: Recompress line count mismatch" && exit 1
rm -f /tmp/eco_apply_<TAG>_<Stage>.v
```

**In-memory verification (before recompress, from module buffers already in memory):**
- rewire: `old_net` must no longer appear on target pin in cell block.
- new_logic: `instance_name` must appear in module buffer.
- port_decl: `signal_name` must appear in port list range.

If any in-memory check fails → do NOT recompress; retry the change on the module buffer first.

---

## 11. ALREADY_APPLIED Detection Rules

Run ALL checks against the ORIGINAL module buffer (pre-snapshot from S4), never against the modified buffer.

| change_type | ALREADY_APPLIED condition |
|-------------|--------------------------|
| `new_logic_dff` / `new_logic_gate` / `new_logic` | **Step 1:** instance exists: `grep -c "^\s*<cell_type>\s*<instance_name>\s*("` >= 1. **Step 2 (MANDATORY):** for each input pin in `port_connections_per_stage[stage]`, verify expected net is on that pin using `\.<pin>\s*\(\s*<expected_net>\s*\)`. Step 1 passes but Step 2 fails for ANY pin → NOT ALREADY_APPLIED; set `force_reapply: true`. |
| `rewire` | `re.search(r'\.<pin>\s*\(\s*<new_net>\s*\)', cell_block)` — found = ALREADY_APPLIED. Still on old_net → `force_reapply: true`. |
| `port_declaration` (`input`/`output`) | Signal in MODULE PORT LIST (not just body). Parse from `mod_idx` to `port_list_close_idx`. Signal only in body as wire/DFF output does NOT count. |
| `port_declaration` (`wire`) | `grep -c "^\s*wire\s+<signal_name>\s*;"` >= 1 in module body. |
| `port_promotion` | `grep -c "output\s+<signal_name>\s*;"` >= 1 in module scope. |
| `port_connection` | `re.search(r'\.<port_name>\s*\(\s*<net_name>\s*\)', instance_block)` — found = ALREADY_APPLIED. Still on old_net → `force_reapply: true`. |

Always record `already_applied_reason` in JSON with exactly what was checked and what was found.

---

## 12. Applied JSON Schema

Write `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. Every entry MUST include `reason` or `already_applied_reason` (used by ORCHESTRATOR to generate RPT).

```json
{
  "Synthesize": [
    {
      "cell_name": "<cell_name>", "cell_type": "<cell_type>",
      "pin": "<pin_name>", "old_net": "<old_net>", "new_net": "<new_net>",
      "change_type": "rewire", "status": "APPLIED",
      "reason": "pin .<pin>(<old_net>) found at line <N>, replaced with .<pin>(<new_net>)",
      "occurrence_count": 1,
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff", "target_register": "<register_signal>",
      "instance_scope": "<inst_path>/<sub_inst>", "cell_type": "<dff_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<inst_path>/<sub_inst>/eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "port_connections": {"<clk_pin>": "<clk_net>", "<data_pin>": "<data_net>", "<q_pin>": "n_eco_<jira>_<seq>"},
      "status": "INSERTED",
      "reason": "DFF <cell_type> eco_<jira>_<seq> inserted before endmodule at line <N>",
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff", "instance_name": "eco_<jira>_<seq>",
      "status": "ALREADY_APPLIED",
      "already_applied_reason": "instance 'eco_<jira>_<seq>' present (grep count=1) AND all input pins verified in instance block"
    },
    {
      "change_type": "port_declaration", "signal_name": "<port_signal>",
      "module_name": "<module>", "declaration_type": "output",
      "status": "APPLIED",
      "reason": "added '<port_signal>' to port list at line <N>; added 'output <port_signal> ;' at line <M>"
    },
    {
      "change_type": "port_connection", "port_name": "<port>", "net_name": "<net>",
      "instance_name": "<submodule_instance>", "status": "SKIPPED",
      "reason": "instance '<submodule_instance>' not found in module '<parent_module>' scope"
    }
  ],
  "PrePlace": [],
  "Route": [],
  "summary": {
    "total": "<count>", "applied": "<count>", "inserted": "<count>",
    "already_applied": "<count>", "skipped": "<count>", "verify_failed": "<count>",
    "module_count_mismatch": false,
    "pre_flight_restore": false,
    "pre_flight_restored_stages": [],
    "verilog_validator": {
      "Synthesize": "<PASS|FAIL|SKIPPED>",
      "PrePlace": "<PASS|FAIL|SKIPPED>",
      "Route": "<PASS|FAIL|SKIPPED>",
      "errors": []
    }
  }
}
```

`verilog_validator` values: `PASS` = 0 errors; `FAIL` = errors found, stage not recompressed, all affected modules → VERIFY_FAILED; `SKIPPED` = validator script unavailable (Checks 1–7 only).

---

## 13. Critical Safety Rules

1. **NEVER edit if occurrence count > 1** — ambiguity; mark SKIPPED + AMBIGUOUS.
2. **NEVER do global search-replace** — scope all changes to the specific cell instance block.
3. **ALWAYS backup before decompressing** — one backup per stage per round with round number in name.
4. **Consistent instance naming across stages** — same seq_table for all 3 stages (never re-assign).
5. **ALWAYS verify from in-memory buffers** — no second decompress; check before recompress.
6. **NEVER recompress with VERIFY_FAILED or module count mismatch** — restore backup.
7. **Keep processing remaining cells if one is SKIPPED** — only skip entries whose `input_from_change` directly points to the SKIPPED entry. If a dependency gate was SKIPPED, substitute `1'b0` as a conservative placeholder rather than skipping the dependent gate.
8. **Use per-stage port_connections for DFF** — always read `port_connections_per_stage[<Stage>]`; fall back to flat `port_connections` only if absent.
9. **Detect netlist type before every stage** — `grep -c "^module "` before processing.
10. **eco_applier NEVER adds `wire N;` declarations** — every net is implicitly declared via port connections; explicit `wire N;` always causes FM-599.
11. **ALREADY_APPLIED for new_logic requires pin verification** — instance existence alone is insufficient; verify each input pin connection matches study JSON; if any pin differs → `force_reapply: true`.

**Final output:** `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. After writing, verify it is non-empty and contains a `summary` field, then exit. Do NOT write the RPT — the calling orchestrator reads the JSON and generates the RPT.
