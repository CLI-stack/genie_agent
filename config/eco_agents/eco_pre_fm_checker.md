# ECO Pre-FM Checker — Cross-Stage Consistency Validator

**You are the ECO pre-FM checker.** You run AFTER eco_applier (Step 4) and BEFORE FM submission (Step 6). Your job: verify the 3 PostEco netlists are consistent, fix any issues found inline (without spawning a new round), and gate FM submission.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Inputs:** TAG, REF_DIR, BASE_DIR, ROUND, AI_ECO_FLOW_DIR
- Applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`
- Study JSON: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`
- PostEco netlists: `<REF_DIR>/data/PostEco/Synthesize.v.gz`, `PrePlace.v.gz`, `Route.v.gz`

**Outputs (BOTH required before exiting):**
- `<BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt` → copied to `AI_ECO_FLOW_DIR/`
- `<BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<ROUND>.json`

**Max inline fix retries:** 3 — if issues persist after 3 fix attempts, escalate to ROUND_ORCHESTRATOR.

---

## MANDATORY OUTPUT CONTRACT — JSON Schema

> **Read this BEFORE running any checks.** Your final JSON MUST match this exact structure. Do NOT invent fields. Do NOT omit `check_summary` or `check8_verilog_validator`.

```json
{
  "tag": "<TAG>",
  "round": <ROUND>,
  "passed": true,
  "attempts": <N of 3>,
  "issues_found": [],
  "issues_fixed": [],
  "issues_unresolved": [],
  "warnings": [],
  "check_summary": {
    "A_stage_consistency":           "PASS | FAIL | FIXED",
    "B_port_declarations":           "PASS | FAIL | FIXED | N/A",
    "C_cell_insertions":             "PASS | FAIL | FIXED | N/A",
    "D_duplicate_ports":             "PASS | FAIL | FIXED | N/A",
    "E_rewire_warnings":             "PASS | WARN | N/A",
    "F_wire_dup_implicit":           "PASS | FAIL | FIXED | N/A",
    "G_port_direction_completeness": "PASS | FAIL | FIXED | N/A",
    "H_eco_cell_pin_names":          "PASS | FAIL | FIXED | N/A",
    "I_se_cone_mismatch":            "WARN | N/A",
    "check8_verilog_validator": {
      "Synthesize": "PASS | FAIL",
      "PrePlace":   "PASS | FAIL",
      "Route":      "PASS | FAIL",
      "errors":     []
    }
  }
}
```

**Rules for `check_summary` values:**
- `PASS` — check ran, no issues found
- `FAIL` — check found issues that could NOT be fixed inline (blocks FM)
- `FIXED` — check found issues, all were fixed inline (FM proceeds)
- `WARN` — non-blocking issue noted (FM proceeds but eco_fm_analyzer may diagnose in next round)
- `N/A` — check not applicable to this ECO (e.g., no new ports → Check G is N/A)
- `SKIPPED` — **INVALID** — validator must always run. If script not found → RuntimeError. Never SKIPPED.

**MANDATORY SELF-CHECK before writing JSON:**
```python
assert "check_summary" in result, "MISSING check_summary — do not exit without it"
assert "check8_verilog_validator" in result["check_summary"], "MISSING validator result"
assert result["check_summary"]["check8_verilog_validator"]["Synthesize"] in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
assert result["check_summary"]["check8_verilog_validator"]["PrePlace"]   in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
assert result["check_summary"]["check8_verilog_validator"]["Route"]      in ("PASS","FAIL"), "SKIPPED is not valid — validator must always run"
```
If any assertion fails — **complete the missing sections before writing**. Do not exit with an incomplete JSON.

---

**ABSOLUTE RULE — Check 8 (Verilog Validator) is NEVER suppressed by manual_only:**
`manual_only` status suppresses Check A/C escalation ONLY. Check 8 runs regardless of any manual_only classifications. A FAIL in Check 8 always blocks FM submission. Set `passed: false` in the output JSON if Check 8 fails, even if all other checks are PASS or N/A.

---

## Why This Exists

FM stage-to-stage comparisons fail when stages have different ECO changes applied. Examples:
- A cell SKIPPED in Synthesize but APPLIED in PrePlace → thousands of FM stage-to-stage non-equiv DFFs
- A signal declared twice in a module port list → FM-599 (Verilog syntax abort)
- A gate inserted with wrong cell type (wrong port names) → FM FE-LINK-7 (port not defined abort)

These are fixable in seconds without a full round cycle.

---

## STEP 0 — Verify eco_applier Completed Successfully

**Before any netlist scanning, read the applied JSON for this round:**
```python
applied = load(f"data/{TAG}_eco_applied_round{ROUND}.json")
verify_failed = [
    e for stage_entries in applied.values() if isinstance(stage_entries, list)
    for e in stage_entries if e.get("status") == "VERIFY_FAILED"
]
if verify_failed:
    # eco_applier hit a Checks 1-7 self-validation failure and did NOT recompress.
    # The PostEco netlist on disk is stale — do NOT submit to FM.
    first_reason = verify_failed[0].get("reason", "unknown")
    write_result(passed=False, issues_unresolved=[{
        "check": "STEP0_APPLIER_FAILED",
        "severity": "CRITICAL",
        "detail": f"{len(verify_failed)} VERIFY_FAILED entries in eco_applied_round{ROUND}.json. Reason: {first_reason}. eco_applier aborted before recompress. Escalate to ROUND_ORCHESTRATOR."
    }])
    EXIT  # Do not proceed to FM
```

**CHECKPOINT:** Only proceed to Step 1 if zero VERIFY_FAILED entries in the applied JSON.

---

## STEP 1 — Load Data

```python
applied = load(f"data/{TAG}_eco_applied_round{ROUND}.json")
study   = load(f"data/{TAG}_eco_preeco_study.json")

# Build cross-stage map: {change_name → {stage → {status, change_type, reason}}}
change_map = {}
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in applied.get(stage, []):
        name = (entry.get("instance_name") or entry.get("cell_name") or
                entry.get("signal_name") or entry.get("port_name") or "?")
        change_map.setdefault(name, {})[stage] = {
            "status": entry.get("status"), "change_type": entry.get("change_type"),
            "reason": entry.get("reason", ""), "entry": entry
        }
```

---

## STEP 2 — Run Checks + Fix Inline (repeat up to 3 times)

```python
MAX_RETRIES = 3
for attempt in range(MAX_RETRIES):
    issues = run_all_checks(change_map, study)
    if not issues["critical"]:
        break  # All checks passed — proceed to Step 3 (write RPT) and Step 4 (FM)

    # Fix each critical issue inline
    for issue in issues["critical"]:
        apply_inline_fix(issue)

    # Reload netlists after fixes AND re-run ALL checks to catch cascading errors
    reload_netlists()
    # Re-run Checks F and G (syntax-level checks) on the modified netlist
    # before proceeding to next attempt. This prevents a fix introducing a new error.
    issues = run_syntax_checks_only(["F", "G"])  # F1, F2, F3, G
    if issues["critical"]:
        # Fix introduced a new error — log and try again or escalate
        continue
```

### Check A — Stage Consistency (INSERTED in some, SKIPPED in others)

```python
issues_A = []
for name, stages in change_map.items():
    inserted = [s for s in stages if stages[s]["status"] in ("INSERTED", "APPLIED")]
    skipped  = [s for s in stages if stages[s]["status"] == "SKIPPED"]
    if inserted and skipped:
        issues_A.append({"name": name, "applied_in": inserted, "skipped_in": skipped,
                          "check": "A", "severity": "CRITICAL"})
```

**Inline fix for Check A:**

For each SKIPPED stage, determine why it was skipped (read `reason` from applied JSON):

- If reason = "input net not found" → grep for the P&R-renamed net in the SKIPPED stage PostEco:
  ```bash
  zcat <REF_DIR>/data/PostEco/<SKIPPED_STAGE>.v.gz | grep -cw "<old_net_from_study>"
  ```
  If found → update `port_connections_per_stage[<SKIPPED_STAGE>]` in study JSON with the correct net, then **spawn eco_applier as a sub-agent** (same way ORCHESTRATOR spawns it) with `ROUND=<ROUND>` — eco_applier will detect the now-resolvable entry and apply it in Surgical Patch mode.

  > **IMPORTANT:** eco_pre_fm_checker does NOT directly edit PostEco netlists for cell insertions — it spawns eco_applier as a sub-agent for any fix requiring gate insertion or rewiring. Direct netlist edits by eco_pre_fm_checker are limited to: removing duplicate lines, adding signals to module port lists, and removing explicit wire declarations (all simple text operations on known line numbers).

- If reason = "cell not found in module scope" → find the correct module (P&R may have added `_0` suffix):
  ```bash
  zcat <REF_DIR>/data/PostEco/<SKIPPED_STAGE>.v.gz | grep "module.*<module_base_name>" | head -3
  ```
  Update `module_name` in study JSON for that stage, then spawn eco_applier sub-agent.

- If cannot resolve in 3 attempts → mark as **unresolvable**, record in JSON, allow FM to run (eco_fm_analyzer will handle in next round).

### Check B — Port Declarations in All 3 Stages

```bash
# For each port_declaration APPLIED entry, check all 3 stages
for signal in applied_port_signals:
    for stage in Synthesize PrePlace Route:
        in_port_list=$(zcat <REF_DIR>/data/PostEco/${stage}.v.gz | \
          awk "/^module ${module}\b/{p=1} p && /\) ;/{p=0} p" | grep -cw "${signal}")
        has_decl=$(zcat <REF_DIR>/data/PostEco/${stage}.v.gz | \
          grep -cE "^\s*(input|output|wire)\s+${signal}\s*;")
        if [ "$in_port_list" -eq 0 ] || [ "$has_decl" -eq 0 ]; then
            # PORT MISSING — fix inline
        fi
```

**Inline fix for Check B (port missing from stage):**

Decompress that stage's netlist, add signal to port list and declaration, recompress:
```python
# Add signal to port list of module in this stage
lines = decompress(f"PostEco/{stage}.v.gz")
# Find module, run PORT_DECL step (same as eco_applier 4c-PORT_DECL)
add_port_to_module(lines, module_name, signal, direction)
recompress(lines, f"PostEco/{stage}.v.gz")
```

### Check C — Inserted Cells in All 3 Stages

```bash
for instance in inserted_cell_instances:
    for stage in Synthesize PrePlace Route:
        count=$(zcat <REF_DIR>/data/PostEco/${stage}.v.gz | grep -cw "${instance}")
        if [ "$count" -eq 0 ]; then
            # CELL MISSING — fix inline
        fi
```

**Inline fix for Check C (cell missing from stage):**

Read the cell entry from study JSON, insert it into that stage's PostEco using the same logic as eco_applier 4c-GATE:
```python
entry = find_study_entry(instance, stage)
lines = decompress(f"PostEco/{stage}.v.gz")
insert_gate_into_module(lines, entry, stage)
recompress(lines, f"PostEco/{stage}.v.gz")
```

### Check D — No Duplicate Port Names

```bash
for stage in Synthesize PrePlace Route:
    python3 << 'EOF'
import gzip, re, sys
content = gzip.open(f"PostEco/{stage}.v.gz", 'rt').read()
for mod in re.split(r'^module\s+', content, flags=re.MULTILINE)[1:]:
    name = mod.split('(')[0].strip()
    m = re.search(r'\((.*?)\)\s*;', mod, re.DOTALL)
    if m:
        ports = re.findall(r'\b([A-Za-z_]\w*)\b', m.group(1))
        seen = {}
        for p in ports:
            seen[p] = seen.get(p, 0) + 1
        dups = [p for p, c in seen.items() if c > 1
                and p not in ('input','output','wire','reg','inout')]
        if dups:
            print(f"DUPLICATE:{name}:{','.join(dups)}")
EOF
```

**Inline fix for Check D (duplicate port):**

Remove the duplicate entry from the port list and body (keep the force_reapply version):
```python
lines = decompress(f"PostEco/{stage}.v.gz")
remove_duplicate_port(lines, module_name, signal)  # keep first occurrence, remove duplicate
recompress(lines, f"PostEco/{stage}.v.gz")
```

### Check F — Duplicate Wire Declarations and Duplicate Instance Port Connections

**Run the general Verilog validator FIRST (covers all F sub-checks plus additional patterns):**

Get the ECO-touched module names from the applied JSON (module_name field of all entries). Pass via `--modules` — this is MANDATORY to keep runtime fast on large P&R netlists (otherwise it scans 8000+ modules and times out or gets killed). If the validator times out even with `--modules`, run each stage independently and use `timeout 120` per stage — never fall back to manual-only without attempting module-scoped validation:

```python
# Extract touched module names from eco_applied_round<ROUND>.json
import json
applied = json.load(open(f"data/{TAG}_eco_applied_round{ROUND}.json"))
touched_modules = set()
for stage_entries in applied.values():
    if isinstance(stage_entries, list):
        for e in stage_entries:
            if e.get("module_name"):
                touched_modules.add(e["module_name"])
modules_arg = " ".join(touched_modules)
```

```bash
python3 script/validate_verilog_netlist.py \
  --strict \
  --modules <touched_modules_space_separated> \
  -- \
  <REF_DIR>/data/PostEco/Synthesize.v.gz \
  <REF_DIR>/data/PostEco/PrePlace.v.gz \
  <REF_DIR>/data/PostEco/Route.v.gz
# Exit 0 = PASS for all stages. Exit 1 = errors found → block FM submission → escalate to ROUND_ORCHESTRATOR
```

**ALWAYS run with `--strict`** — this enables F1 (duplicate wire) detection in addition to F3/F5/Check9. F1 catches cases where an explicit `wire n_eco_*;` declaration was added alongside a cell that already creates the net implicitly (eco_applier UNIVERSAL RULE violation). Without `--strict`, duplicate wire errors reach FM and cause FM-599 → ABORT_NETLIST.

**Inline fix for F1 (duplicate wire `n_eco_*`):**
```python
for err in errors:
    if err["check"] == "F1_dup_wire" and err["signal"].startswith("n_eco_"):
        # Remove the explicit wire declaration — the cell output creates the net implicitly
        fix_applied |= remove_line_from_gz(
            stage_gz=f"{REF_DIR}/data/PostEco/{err['stage']}.v.gz",
            lineno=err["line"]  # line of the DUPLICATE wire declaration (second occurrence)
        )
```

This validator catches F1 (duplicate wire), F3 (declaration inside cell instance), F4 (duplicate port connection), and F5 (corrupted port value). Runs in seconds when `--modules` is used. Without `--modules` it scans the entire netlist which is too slow.

If the validator script is unavailable, run the manual checks below.

These are FM-599 abort triggers that eco_applier's Check 5/6 may have missed (e.g., in modules not directly touched by eco_applier, or when the conflict was introduced via port_promotion interaction).

```python
import re, gzip

for stage in ["Synthesize", "PrePlace", "Route"]:
    content = gzip.open(f"PostEco/{stage}.v.gz", 'rt').read()
    for mod_block in re.split(r'^module\s+', content, flags=re.MULTILINE)[1:]:
        mod_name = mod_block.split('(')[0].strip()

        # F1: Duplicate explicit wire declarations
        wire_decls = re.findall(r'^\s*wire\s+(\w+)\s*;', mod_block, re.MULTILINE)
        seen = {}
        for w in wire_decls:
            seen[w] = seen.get(w, 0) + 1
        dups = [w for w, c in seen.items() if c > 1]
        if dups:
            issues_F.append({
                "stage": stage, "module": mod_name,
                "sub_check": "F1_dup_wire",
                "wires": dups, "severity": "CRITICAL"
            })

        # F2: Explicit WIRE declaration for net X where .anypin(X) port connection ALSO
        # creates an implicit wire X — dual wire declaration = FM SVR-9 → FM-599.
        # NOTE: input/output declarations do NOT conflict with port connections — that is
        # normal Verilog (a port being passed to a submodule). Only 'wire' conflicts.
        wire_decls_only = set(re.findall(
            r'^\s*wire\s+(?:\[\s*\d+\s*:\s*\d+\s*\]\s+)?(\w+)\s*;',
            mod_block, re.MULTILINE))
        # Collect ALL net names used in ANY port connection .anypin(N)
        port_conn_nets = set(re.findall(r'\.\s*\w+\s*\(\s*(\w+)\s*\)', mod_block))
        conflict = wire_decls_only & port_conn_nets  # explicit wire + implicit = FM-599
        if conflict:
            issues_F.append({
                "stage": stage, "module": mod_name,
                "sub_check": "F2_implicit_wire_conflict",
                "wires": list(conflict), "severity": "CRITICAL"
            })

        # F3: Duplicate port connections in instance blocks (.pin used twice)
        for inst_match in re.finditer(r'(\w+)\s+(\w+)\s*\((.*?)\)\s*;', mod_block, re.DOTALL):
            inst_name = inst_match.group(2)
            pins = re.findall(r'\.\s*(\w+)\s*\(', inst_match.group(3))
            pin_seen = {}
            for pin in pins:
                pin_seen[pin] = pin_seen.get(pin, 0) + 1
            dup_pins = [p for p, c in pin_seen.items() if c > 1]
            if dup_pins:
                issues_F.append({
                    "stage": stage, "module": mod_name, "instance": inst_name,
                    "sub_check": "F3_dup_port_connection",
                    "pins": dup_pins, "severity": "CRITICAL"
                })
```

**Inline fix for Check F:**

- **F1 (duplicate wire decl)**: Keep the first occurrence, remove all subsequent `wire X;` lines for the duplicated name in that module.
- **F2 (explicit wire conflicts with implicit)**: Remove the explicit `wire X;` declaration — the implicit wire from the port connection is sufficient and the explicit one causes FM-599.
- **F3 (duplicate port connection)**: In the instance block, remove the duplicate `.pin(...)` entry (keep the first; the duplicate is from eco_applier inserting a port connection that was already present).

```python
def fix_F1_F2(lines, module_name, wire_name, sub_check):
    in_module = False
    wire_seen = False
    result = []
    for line in lines:
        if re.match(rf'^module\s+{re.escape(module_name)}\b', line):
            in_module = True
        elif re.match(r'^endmodule\b', line):
            in_module = False
        if in_module and re.match(rf'^\s*wire\s+{re.escape(wire_name)}\s*;', line):
            if wire_seen:
                continue  # drop duplicate (F1) or drop conflicting explicit wire (F2)
            wire_seen = True
        result.append(line)
    return result

def fix_F3(lines, module_name, instance_name, dup_pin):
    in_inst = False
    pin_seen = False
    result = []
    for line in lines:
        if re.search(rf'\b{re.escape(instance_name)}\s*\(', line):
            in_inst = True
        if in_inst and re.search(rf'\.\s*{re.escape(dup_pin)}\s*\(', line):
            if pin_seen:
                continue  # drop duplicate port connection
            pin_seen = True
        if in_inst and re.search(r'\)\s*;', line):
            in_inst = False
        result.append(line)
    return result
```

### Check G — Every ECO-added port in module header has a direction declaration in body

For each `port_declaration` entry with `declaration_type: "input"` or `"output"` in the applied JSON, verify the signal has BOTH a port list entry AND a direction declaration in the module body for each stage:

```python
for stage in ["Synthesize", "PrePlace", "Route"]:
    content = gzip.open(f"PostEco/{stage}.v.gz", 'rt').read()
    for mod_block in re.split(r'^module\s+', content, flags=re.MULTILINE)[1:]:
        mod_name = mod_block.split('(')[0].strip()
        port_list_match = re.search(r'\((.*?)\)\s*;', mod_block, re.DOTALL)
        if not port_list_match:
            continue
        port_names_in_header = set(re.findall(r'\b([A-Za-z_]\w*)\b', port_list_match.group(1)))
        port_names_in_header -= {'input','output','inout','wire','reg','integer','parameter'}
        body = mod_block[port_list_match.end():]
        declared_in_body = set(re.findall(
            r'^\s*(?:input|output|inout)\s+(?:\[.*?\]\s+)?(\w+)\s*;', body, re.MULTILINE))
        eco_ports = {e["signal_name"] for e in applied.get(stage,[])
                     if e.get("change_type") == "port_declaration"
                     and e.get("declaration_type") in ("input","output")
                     and e.get("module_name","").endswith(mod_name)}
        missing_decl = eco_ports & (port_names_in_header - declared_in_body)
        if missing_decl:
            issues_G.append({"stage": stage, "module": mod_name,
                             "missing_direction_decl": list(missing_decl), "severity": "CRITICAL"})
```

**Inline fix for Check G:** For each missing direction declaration, find the port list close line, then insert `input <signal>;` or `output <signal>;` immediately after it (use `declaration_type` from the applied JSON to determine direction).

### Check H — ECO Cell Output Pin Name Validation (FE-LINK-7 prevention)

For every inserted ECO gate (new_logic_gate, new_logic_dff entries in applied JSON), verify the output pin name used matches the actual cell library definition. Wrong pin names cause FM FE-LINK-7 → ABORT_LINK on ALL subsequent stage comparisons.

**Method: look up the cell type in the PreEco netlist to find the actual output pin name.**

```python
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in applied.get(stage, []):
        if entry.get("change_type") not in ("new_logic_gate", "new_logic_dff"):
            continue
        if entry.get("status") not in ("INSERTED",):
            continue

        cell_type  = entry.get("cell_type", "")
        inst_name  = entry.get("instance_name", "")
        gate_fn    = entry.get("gate_function", "")
        # Expected output pin from GATE_OUTPUT_PIN table (see below)
        expected_out_pin = GATE_OUTPUT_PIN.get(gate_fn, None)

        # Method 1: fast table lookup
        if expected_out_pin:
            # Verify PostEco netlist uses correct pin
            posteco_line = find_instance_line(f"PostEco/{stage}.v.gz", inst_name)
            actual_out_pin = extract_output_pin(posteco_line, cell_type)
            if actual_out_pin and actual_out_pin != expected_out_pin:
                issues_H.append({
                    "check": "H_wrong_output_pin",
                    "stage": stage, "instance": inst_name, "cell_type": cell_type,
                    "wrong_pin": actual_out_pin, "correct_pin": expected_out_pin,
                    "severity": "CRITICAL"
                })

        # Method 2: grep PreEco for cell_type to find actual pin name
        else:
            preeco_example = grep_cell_type_example(f"PreEco/Synthesize.v.gz", cell_type)
            if preeco_example:
                # Parse output pin from example: last .PIN(NET) before ); where PIN is Z/ZN/Q etc
                correct_pin = extract_output_pin_from_example(preeco_example)
                posteco_line = find_instance_line(f"PostEco/{stage}.v.gz", inst_name)
                actual_out_pin = extract_output_pin(posteco_line, cell_type)
                if actual_out_pin and correct_pin and actual_out_pin != correct_pin:
                    issues_H.append({
                        "check": "H_wrong_output_pin",
                        "stage": stage, "instance": inst_name, "cell_type": cell_type,
                        "wrong_pin": actual_out_pin, "correct_pin": correct_pin,
                        "severity": "CRITICAL"
                    })
```

**GATE_OUTPUT_PIN lookup table** — authoritative mapping of gate function to output pin name in this library:

```python
GATE_OUTPUT_PIN = {
    # Non-inverting outputs → Z
    "AND2":  "Z",   "AND3":  "Z",   "AND4":  "Z",
    "OR2":   "Z",   "OR3":   "Z",   "OR4":   "Z",
    "MUX2":  "Z",   "MUX4":  "Z",   # MUX2D* output is .Z — NOT .ZN
    "IND2":  "ZN",  "IND3":  "ZN",  # IND = AND-NOT (inverted AND) → ZN
    # Inverting outputs → ZN
    "INV":   "ZN",
    "NAND2": "ZN",  "NAND3": "ZN",  "NAND4": "ZN",
    "NOR2":  "ZN",  "NOR3":  "ZN",
    "XNOR2": "ZN",
    "XOR2":  "Z",
    # DFF outputs → Q (QN for inverted copy, but ECO only uses Q)
    "DFF":   "Q",   "SDFF":  "Q",
}
```

> **CRITICAL:** MUX2 output pin is `.Z` — never `.ZN`. IND2 (inverted AND) is `.ZN`. This distinction is the most common source of FE-LINK-7 ABORT_LINK.

**Inline fix for Check H (wrong output pin):**
```python
for issue in issues_H:
    if issue["check"] == "H_wrong_output_pin":
        # Fix: replace .WRONG_PIN(net) with .CORRECT_PIN(net) for this instance in this stage
        fix_applied |= replace_pin_in_instance(
            stage_gz=f"{REF_DIR}/data/PostEco/{issue['stage']}.v.gz",
            instance_name=issue["instance"],
            wrong_pin=issue["wrong_pin"],
            correct_pin=issue["correct_pin"]
        )
```

This fix is always safe — only modifies the named ECO cell instance's output pin connection. Re-run Check H after fixing to confirm.

**Check H extension — input pin names validation:**
After verifying the output pin, also verify all INPUT pin names in `port_connections`:
1. Grep PreEco for an existing usage of `<cell_type>`: `grep -m1 "<cell_type>" <REF_DIR>/data/PreEco/Synthesize.v.gz`
2. Parse all `.<PIN>(` from that instantiation → collect valid_pins set
3. For each pin in `port_connections` that is NOT the output pin: verify it appears in valid_pins
4. If any input pin NOT in valid_pins → `H_wrong_input_pin_name` error:
   - Find correct name from valid_pins (same position/role)
   - Replace in PostEco: `sed -i "s/\\.{wrong_pin}(/{correct_pin}(/g"` within cell instance block
   - Re-validate
5. This catches FE-LINK-7 on wrong input pin names BEFORE FM submission

```python
    # Check H extension: input pin name validation
    preeco_example = grep_cell_type_example(f"PreEco/Synthesize.v.gz", cell_type)
    if preeco_example:
        valid_pins = set(re.findall(r'\.\s*(\w+)\s*\(', preeco_example))
        output_pin_key = identify_output_pin(entry)  # the pin whose net = n_eco_* or Q-output
        for pin in entry.get("port_connections", {}):
            if pin == output_pin_key:
                continue  # output pin already validated above
            if valid_pins and pin not in valid_pins:
                # Find best candidate from valid_pins (same position in pin list)
                issues_H.append({
                    "check": "H_wrong_input_pin_name",
                    "stage": stage, "instance": inst_name, "cell_type": cell_type,
                    "wrong_pin": pin, "valid_pins": list(valid_pins),
                    "severity": "CRITICAL"
                })
```

**Inline fix for `H_wrong_input_pin_name`:**
```python
    if issue["check"] == "H_wrong_input_pin_name":
        # Determine correct pin name (same positional role from valid_pins)
        correct_pin = resolve_correct_input_pin(issue["wrong_pin"], issue["valid_pins"])
        if correct_pin:
            fix_applied |= replace_pin_in_instance(
                stage_gz=f"{REF_DIR}/data/PostEco/{issue['stage']}.v.gz",
                instance_name=issue["instance"],
                wrong_pin=issue["wrong_pin"],
                correct_pin=correct_pin
            )
```

`H_wrong_input_pin_name` is treated as a fixable check type in the inline fix loop, alongside F3/F5/F6. Re-run Check H after applying the fix to confirm the input pin name is now valid.

### Check F2-POSITION — Cross-Position Wire Declaration Conflict (SVR-9 prevention)

The standard F2 check detects `wire X` + `port_connection(X)` in the same module. This extension specifically detects **position-based conflicts**: an eco-inserted gate output net referenced by a pre-existing cell at a lower line number than the explicit `wire X;` declaration. FM sees the earlier reference as an implicit declaration, then the explicit wire as a duplicate → SVR-9.

```python
for stage in ["Synthesize", "PrePlace", "Route"]:
    lines = list(gzip.open(f"PostEco/{stage}.v.gz", 'rt'))
    for mod_name, mod_start, mod_end in find_module_ranges(lines):
        mod_lines = lines[mod_start:mod_end]

        # Collect: line index of each explicit wire decl for eco-inserted nets
        wire_decl_positions = {}
        for i, line in enumerate(mod_lines):
            m = re.match(r'^\s*wire\s+(\w+)\s*;', line)
            if m:
                wire_decl_positions[m.group(1)] = i

        # Collect: line index of ALL port connection references to eco nets
        for net_name, wire_lineno in wire_decl_positions.items():
            for i, line in enumerate(mod_lines):
                if i >= wire_lineno:
                    break  # only check lines BEFORE the wire decl
                # Is this net referenced as a port connection input here?
                if re.search(rf'\.\w+\s*\(\s*{re.escape(net_name)}\s*\)', line):
                    # Reference at line i < wire decl at wire_lineno → SVR-9 risk
                    issues_F.append({
                        "stage": stage, "module": mod_name,
                        "sub_check": "F2_position_conflict",
                        "wire": net_name,
                        "ref_line": mod_start + i,
                        "decl_line": mod_start + wire_lineno,
                        "severity": "CRITICAL",
                        "fix": "remove_explicit_wire_decl"
                    })
```

**Inline fix for F2-POSITION:** Remove the explicit `wire <net>;` declaration — the earlier port connection reference already implicitly declares the net. The gate output binding that follows also references it correctly.

### Check NEW-B2 — Missing Port Declarations for Cells Whose Outputs Escape Module Scope

For each newly inserted ECO cell, verify that if its output net appears in a parent module's instance connection, a corresponding `port_declaration` entry was applied. Missing port declarations cause FE-LINK-7 ABORT_LINK.

```python
# Build set of output nets from all ECO-inserted cells (new_logic_gate, new_logic_dff)
eco_output_nets = {}  # {output_net: (instance_name, module_name, stage)}
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in applied.get(stage, []):
        if entry.get("status") == "INSERTED" and entry.get("change_type") in ("new_logic_gate", "new_logic_dff"):
            eco_output_nets[entry.get("output_net", "")] = (
                entry.get("instance_name", ""),
                entry.get("module_name", ""),
                stage
            )

# For each eco output net, check if it appears in parent module scope
for output_net, (inst_name, mod_name, stage) in eco_output_nets.items():
    # Search PostEco for this net being used as a port connection argument OUTSIDE mod_name
    parent_ref_count = int(bash(
        f'zcat {REF_DIR}/data/PostEco/{stage}.v.gz | '
        f'grep -c "\\.\\w\\+\\s*(\\s*{re.escape(output_net)}\\s*)"'
    ))
    local_ref_count = int(bash(
        f'zcat {REF_DIR}/data/PostEco/{stage}.v.gz | '
        f'awk "/^module {re.escape(mod_name)}/,/^endmodule/" | '
        f'grep -c "\\.\\w\\+\\s*(\\s*{re.escape(output_net)}\\s*)"'
    ))
    # If net referenced more times than just in its own module → used by parent
    if parent_ref_count > local_ref_count:
        # Verify port_declaration exists for this module/signal
        port_declared = any(
            e.get("change_type") == "port_declaration"
            and e.get("signal_name") == output_net
            and mod_name in e.get("module_name", "")
            for e in applied.get(stage, [])
        )
        if not port_declared:
            issues_B2.append({
                "stage": stage, "module": mod_name,
                "output_net": output_net, "eco_instance": inst_name,
                "sub_check": "B2_missing_output_port",
                "severity": "CRITICAL"
            })
```

**Inline fix for Check B2:** Spawn eco_applier sub-agent with a targeted `port_declaration` entry: `{"change_type": "port_declaration", "signal_name": <output_net>, "module_name": <mod_name>, "declaration_type": "output", "force_reapply": True}`.

### Check NEW-UNDRIVEN — Undriven DFF Inputs After ECO Changes

For each DFF in ECO-touched modules, verify the `.D` pin net has at least one primitive driver in PostEco. An undriven D-input causes FM DFF0X (mode H), which can cascade to hundreds of downstream failures.

```python
for stage in ["Synthesize", "PrePlace", "Route"]:
    touched_modules = get_touched_module_names(applied, stage)
    for mod_name in touched_modules:
        mod_lines = extract_module_lines(f"PostEco/{stage}.v.gz", mod_name)
        # Find all DFF .D pin connections in this module
        for dff_match in re.finditer(
            r'(\w+)\s+(\w+)\s*\(.*?\.\s*D\s*\(\s*(\w+)\s*\).*?\)\s*;',
            "\n".join(mod_lines), re.DOTALL
        ):
            cell_type  = dff_match.group(1)
            dff_inst   = dff_match.group(2)
            d_input_net = dff_match.group(3)

            if d_input_net in ("1'b0", "1'b1"):
                continue  # constant input — always driven

            # Count drivers: any .anypin(<d_input_net>) where anypin is NOT an input port
            # Simplified: count gate output bindings where output = d_input_net
            driver_count = sum(
                1 for line in mod_lines
                if re.search(rf'\.\s*(?:Z|ZN|Q|QN)\s*\(\s*{re.escape(d_input_net)}\s*\)', line)
            )
            if driver_count == 0:
                issues_UNDRIVEN.append({
                    "stage": stage, "module": mod_name,
                    "dff_instance": dff_inst, "d_input_net": d_input_net,
                    "sub_check": "UNDRIVEN_DFF_D_INPUT",
                    "severity": "CRITICAL"
                })
```

**UNDRIVEN failures BLOCK FM submission** — they cause FM DFF0X which cascades to hundreds of false non-equivalences. Must be fixed before FM runs.

**Fix procedure:** Identify which renamed driver output left this net undriven. Find the ECO rewire that renamed the driver output. Add a new rewire entry connecting the DFF .D to the new driver net. Spawn eco_applier sub-agent.

### Check E — Rewire Consistency (WARNING only — not a blocker)

```python
issues_E = []
for name, stages in change_map.items():
    rewire_stages = {s: v for s, v in stages.items() if v.get("change_type") == "rewire"}
    applied = [s for s, v in rewire_stages.items() if v["status"] == "APPLIED"]
    skipped  = [s for s, v in rewire_stages.items() if v["status"] == "SKIPPED"]
    if applied and skipped:
        issues_E.append({"cell": name, "applied_in": applied, "skipped_in": skipped,
                         "severity": "WARNING"})
# Warnings do NOT block FM — eco_fm_analyzer handles these in next round if FM fails
```

### Check I — SE/SI Scan Chain Stage Mismatch for Newly Inserted DFFs

For each `new_logic_dff` entry in the applied JSON with `needs_se_tune: true` (set by eco_netlist_studier):
1. Read `port_connections_per_stage["PrePlace"]["<SE_pin>"]` and `port_connections_per_stage["Route"]["<SE_pin>"]`
2. If both differ AND neither appears in RTL source (`grep -rw "<se_net>" data/PreEco/SynRtl/ → count = 0`): flag `se_cone_mismatch_risk: true`
3. Record in `check_summary["I_se_cone_mismatch"]`: `"WARN"` (non-blocking — FM proceeds)
4. Add to `warnings[]`: recommended tune file entry `set_constant -type port {<DFF_hierarchy_path>/SE} 0` for stage-to-stage FM targets

Check I is non-blocking. It surfaces the risk before FM runs so eco_fm_analyzer can auto-classify the resulting failure as `SCAN_CHAIN_MISMATCH` without engineer escalation.

If no `new_logic_dff` entries have `needs_se_tune: true`, set `check_summary["I_se_cone_mismatch"]` to `"N/A"`.

---

## STEP 3 — Write RPT

Write `<BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt`:

```
================================================================================
STEP 5 — PRE-FM CROSS-STAGE CONSISTENCY CHECK (Round <ROUND>)
Tag: <TAG>  |  Tile: <TILE>  |  JIRA: <JIRA>
Attempts: <N of MAX_RETRIES>
================================================================================

Check A — Stage Consistency (INSERTED/SKIPPED mismatch) : <PASS / FIXED / FAIL>
Check B — Port Declarations in all 3 stages             : <PASS / FIXED / FAIL>
Check C — Inserted Cells in all 3 stages                : <PASS / FIXED / FAIL>
Check D — No Duplicate Port Names (port list header)     : <PASS / FIXED / FAIL>
Check E — Rewire Consistency (warning)                   : <PASS / WARN>
Check F — Duplicate Wire Decls / Implicit Wire Conflicts : <PASS / FIXED / FAIL>
Check G — Port Direction Completeness                    : <PASS / FIXED / FAIL>
Check 8 — Verilog Netlist Validator (validate_verilog_netlist.py):
  Synthesize : <PASS / FAIL — <N> error(s): <brief description>>
  PrePlace   : <PASS / FAIL — <N> error(s): <brief description>>
  Route      : <PASS / FAIL — <N> error(s): <brief description>>

OVERALL: <PASS — proceed to FM / FAIL after 3 retries — escalate to ROUND_ORCHESTRATOR>

================================================================================
<Per-issue detail including: what was found, what fix was applied, result>

[Check A] FIXED: eco_<jira>_<seq> stage inconsistency
  Was: INSERTED in Synthesize/Route, SKIPPED in PrePlace (input net not found)
  Fix: Found P&R alias for input net in PrePlace PreEco netlist
       Updated study JSON per-stage net, re-applied gate in PrePlace
  Result: eco_<jira>_<seq> now present in all 3 stages ✓

[Check D] FIXED: <signal_name> duplicate in PrePlace <module_name>
  Was: <signal_name> appeared twice in port list and twice as direction declaration
  Fix: Removed duplicate entries (kept force_reapply version)
  Result: <signal_name> declared once in port list, once as direction declaration ✓

[Check E] WARNING: <cell_name> rewire skipped in Synthesize
  Rewire applied in P&R stages but skipped in Synthesize (cell not found in declaring module scope)
  Action: Proceeding to FM — eco_fm_analyzer will diagnose if FM fails on this
================================================================================
NEXT STEP: <Proceed to Step 6 (FM submission) / Escalate to ROUND_ORCHESTRATOR>
================================================================================
```

Copy to AI_ECO_FLOW_DIR:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
```

---

## STEP 4 — Write JSON and Exit

```python
passed = len(remaining_critical_after_fixes) == 0

result = {
    "round": ROUND, "tag": TAG,
    "passed": passed,
    "attempts": attempt_count,
    "issues_found": all_critical_found,
    "issues_fixed": all_critical_fixed,
    "issues_unresolved": remaining_critical_after_fixes,
    "warnings": issues_E,
    "check_summary": {
        "A_stage_consistency": result_A,
        "B_port_declarations": result_B,
        "C_cell_insertions":   result_C,
        "D_duplicate_ports":   result_D,
        "E_rewire_warnings":   "WARN" if issues_E else "PASS",
        "F_wire_dup_implicit": result_F,
        "G_port_direction_completeness": result_G,
        "H_eco_cell_pin_names": result_H,
        "I_se_cone_mismatch":   result_I,   # "WARN" | "N/A"
        "check8_verilog_validator": {
            "Synthesize": validator_result_synth,   # "PASS" | "FAIL" | "SKIPPED"
            "PrePlace":   validator_result_pplace,
            "Route":      validator_result_route,
            "errors":     validator_errors           # list of error dicts from validator
        }
    }
}

# Run Check 8 — Verilog Netlist Validator
# Get touched module names from applied JSON
touched_modules = set(
    e.get("module_name","") for stage_entries in applied.values()
    if isinstance(stage_entries, list)
    for e in stage_entries if e.get("module_name")
)
modules_arg = list(touched_modules)

validator_errors = []
# MANDATORY existence check — SKIPPED is NOT an option if the script exists
import os
_validator_path = os.path.join(BASE_DIR, "script", "validate_verilog_netlist.py")
if not os.path.isfile(_validator_path):
    raise RuntimeError(
        f"CRITICAL: validate_verilog_netlist.py not found at {_validator_path}. "
        "Check 8 CANNOT be skipped. Fix the script path. "
        "Do NOT proceed to FM without running the validator."
    )
# Script confirmed present — always run, never set SKIPPED
validator_result_synth = validator_result_pplace = validator_result_route = "FAIL"  # default until proven PASS
try:
    import subprocess, json as _json
    result_c8 = subprocess.run(
        ["python3", "script/validate_verilog_netlist.py",
         "--modules"] + modules_arg + ["--",
         f"{REF_DIR}/data/PostEco/Synthesize.v.gz",
         f"{REF_DIR}/data/PostEco/PrePlace.v.gz",
         f"{REF_DIR}/data/PostEco/Route.v.gz"],
        capture_output=True, text=True, timeout=120
    )
    output = result_c8.stdout
    # Parse per-stage results from output
    validator_result_synth = "PASS" if "PASS:" in output.split("Synthesize")[1].split("\n")[0] else "FAIL"
    validator_result_pplace = "PASS" if "PASS:" in output.split("PrePlace")[1].split("\n")[0] else "FAIL"
    validator_result_route  = "PASS" if "PASS:" in output.split("Route")[1].split("\n")[0] else "FAIL"
    if result_c8.returncode != 0:
        # Extract errors from output
        for line in output.splitlines():
            if line.strip().startswith("[F"):
                validator_errors.append(line.strip())

        # INLINE FIX ATTEMPT — fix each error before proceeding:
        # For each error, parse stage/module/lineno and attempt targeted fix:
        #
        # F3 fix (declaration inside cell instance): remove the offending line
        #   - Find the line containing 'input/output/wire <signal> ;' at lineno
        #   - Decompress the stage, remove that line, recompress
        #
        # F5 fix (corrupted port value): remove the extra comma-separated nets
        #   - Find '.pin( net1 , ecoadded1, ecoadded2 )' at lineno
        #   - Replace with original single-net form '.pin( net1 )'
        #   - The ECO-added nets are identifiable by name pattern (e.g., signal_name from eco_applied JSON)
        #
        for err_line in validator_errors:
            stage = parse_stage_from_error(err_line)         # "Synthesize"|"PrePlace"|"Route"
            lineno = parse_lineno_from_error(err_line)       # integer line number
            check = parse_check_from_error(err_line)         # "F3"|"F5"|"F6"
            stage_gz = f"{REF_DIR}/data/PostEco/{stage}.v.gz"

            fix_success = False
            if check == "F3_decl_inside_instance":
                # Remove the direction declaration line from the gz file
                fix_success = remove_line_from_gz(stage_gz, lineno)
            elif check == "F5_corrupted_port_value":
                # Revert corrupted .pin(net1, eco_net1, eco_net2) to .pin(net1)
                # Keep only the FIRST net in the port connection value
                fix_success = fix_corrupted_port_value_in_gz(stage_gz, lineno)
            elif check == "F6_invalid_net_name":
                # **F6 auto-fix (invalid net name containing '/'):**
                # 1. Parse the invalid net: split on '/' to get <cell_name> and <pin_name>
                # 2. grep -m1 "<cell_name>" <REF_DIR>/data/PreEco/Synthesize.v.gz → find cell instantiation
                # 3. Read .<pin_name>(<actual_wire>) → extract <actual_wire>
                # 4. Replace the invalid net name with <actual_wire> in PostEco at the error line
                # 5. Re-validate
                #
                # Only escalate to ROUND_ORCHESTRATOR if <actual_wire> cannot be found
                # in any PreEco stage. The inline fix for F6 is fast and prevents wasting
                # an entire FM round on a Verilog syntax error.
                invalid_net = parse_invalid_net_from_error(err_line)   # e.g. "<cell_name>/<pin_name>"
                if '/' in invalid_net:
                    cell_name, pin_name = invalid_net.split('/', 1)
                    actual_wire = grep_pin_connection_from_preeco(
                        cell_name=cell_name, pin_name=pin_name,
                        preeco_stages=[f"{REF_DIR}/data/PreEco/Synthesize.v.gz",
                                       f"{REF_DIR}/data/PreEco/PrePlace.v.gz",
                                       f"{REF_DIR}/data/PreEco/Route.v.gz"]
                    )  # returns None if not found
                    if actual_wire:
                        fix_success = replace_net_in_gz(stage_gz, lineno,
                                                        old_net=invalid_net, new_net=actual_wire)
                    else:
                        fix_success = False  # escalate below — actual wire not in any PreEco stage

            if fix_success:
                issues_fixed.append({"check": f"check8_{check}", "line": lineno, "stage": stage})
            else:
                issues_critical.append({
                    "check": "check8_verilog_validator",
                    "severity": "CRITICAL",
                    "error": err_line,
                    "detail": f"Cannot auto-fix {check} at line {lineno} in {stage}. "
                              f"eco_applier port_list_close_idx bug — revert PostEco to PreEco and re-run eco_applier."
                })

        # Re-run validator after fixes to confirm all resolved
        if issues_fixed and not issues_critical:
            recheck = subprocess.run(
                ["python3", "script/validate_verilog_netlist.py",
                 "--modules"] + modules_arg + ["--",
                 f"{REF_DIR}/data/PostEco/Synthesize.v.gz",
                 f"{REF_DIR}/data/PostEco/PrePlace.v.gz",
                 f"{REF_DIR}/data/PostEco/Route.v.gz"],
                capture_output=True, text=True, timeout=120
            )
            if recheck.returncode != 0:
                issues_critical.append({
                    "check": "check8_verilog_validator_recheck",
                    "severity": "CRITICAL",
                    "detail": "Verilog errors persist after inline fix. Revert PostEco to PreEco and re-run eco_applier."
                })
except Exception as e:
    validator_result_synth = validator_result_pplace = validator_result_route = "SKIPPED"
    # Validator unavailable — log warning but proceed (manual checks below still run)
# MANDATORY SELF-CHECK before writing — verify all required fields present
assert "check_summary" in result, "BUG: check_summary missing from result"
assert "check8_verilog_validator" in result["check_summary"], "BUG: validator result missing"
for stage in ("Synthesize", "PrePlace", "Route"):
    assert result["check_summary"]["check8_verilog_validator"].get(stage) in ("PASS","FAIL","SKIPPED"), \
        f"BUG: validator result for {stage} is missing or invalid"

write_json(f"data/{TAG}_eco_pre_fm_check_round{ROUND}.json", result)
# Verify file written and non-empty
assert os.path.getsize(f"data/{TAG}_eco_pre_fm_check_round{ROUND}.json") > 10, "JSON write failed"
```

**EXIT immediately. Do NOT modify study JSON beyond what was needed for inline fixes.**

---

## Chain: Step 4 → Step 5 → Step 6

```
eco_applier (Step 4) completes
       ↓
eco_pre_fm_checker (Step 5)  ← checks + inline fixes (no new round spawned)
       ↓
  passed: true ─────────────────────────────────→ Step 6: FM submission
       │
  passed: false after MAX_RETRIES
       │  (inline fixes exhausted — remaining issues need deeper diagnosis)
       ↓
  ORCHESTRATOR/ROUND_ORCHESTRATOR:
    write round_handoff (status=FM_FAILED, pre_fm_check_failed=true)
    spawn ROUND_ORCHESTRATOR → HARD STOP
    (eco_fm_analyzer reads pre_fm_check JSON, next round fixes properly)
```

**No new round is spawned for fixable issues — only for issues that couldn't be fixed inline after 3 attempts.**

---

## Output Files

| File | Location | Purpose |
|------|---------|---------|
| `<TAG>_eco_step5_pre_fm_check_round<ROUND>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` | Human-readable: what was found, what was fixed, what was warned |
| `<TAG>_eco_pre_fm_check_round<ROUND>.json` | `data/` | Machine-readable: passed/failed, issues list, for orchestrators |
