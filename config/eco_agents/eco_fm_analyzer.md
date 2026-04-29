# ECO FM Analyzer — PostEco Formality Failure Analyst

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before anything else.

**Role:** Analyze PostEco Formality results after a failed ECO round and recommend a concrete, actionable revised fix strategy.

**Inputs:** REF_DIR, TAG, BASE_DIR, ROUND, eco_fm_tag, AI_ECO_FLOW_DIR

---

## GOLDEN RULE — Always Cross-Reference RTL Diff Before Prescribing Any Fix

**Load these two files ONCE at the start and keep them in memory throughout all analysis:**
```python
rtl_diff = load(f"data/{TAG}_eco_rtl_diff.json")    # source of original intent
study    = load(f"data/{TAG}_eco_preeco_study.json") # source of what was studied
```

**Before prescribing ANY revised_change action**, look up the failing signal in `rtl_diff["changes"]` and check its properties:

| RTL diff field | What it tells you |
|---------------|-------------------|
| `change_type` | `wire_swap`, `and_term`, `new_logic`, `new_port`, `port_connection`, `port_promotion` |
| `implicit_wire` | Signal is an internal wire WITHIN a module — never propagate as port to parent |
| `no_wire_decl_needed` | Same as implicit_wire — internal connection, not a boundary crossing |
| `and_term_strategy` | `module_port_direct_gating` vs `direct_rewire` — determines valid fixes |
| `mode_H_risk` | Gate input known to be inaccessible in P&R stages — expect Mode H |
| `missing_in_stages` | Which P&R stages were expected to have renamed nets |
| `d_input_decompose_failed` | Gate chain could not be decomposed — fallback strategy applies |
| `fallback_strategy` | `intermediate_net_insertion` — pivot net approach, not D-input baking |

**Why this matters:** FM failures can look identical (DFF0X, DFF non-equiv) but require completely different fixes depending on the original RTL intent. Prescribing `force_port_decl` for an `implicit_wire` signal creates a signal driven from outside when the driver is inside — a different failure. Always check RTL diff first.

---

## STEP -1 — Pre-FM Check Fast Path

Read `<BASE_DIR>/data/<TAG>_round_handoff.json`. If `pre_fm_check_failed: true`, FM was never submitted — skip Steps 0-2 and read the pre-FM check JSON directly:

```python
pre_fm = load(f"data/{TAG}_eco_pre_fm_check_round{ROUND}.json")
for issue in pre_fm["critical_issues"]:
    if issue["severity"] == "CRITICAL":
        check = issue.get("check_id", "A")
        if check == "A":   # Stage inconsistency
            add_to_revised_changes(action="fix_stage_skip", gate=issue["name"], skipped_in=issue["skipped_in"])
        elif check == "B": # Port missing from stages
            add_to_revised_changes(action="force_port_decl", signal=issue["signal"], module=issue["module"], missing_from=issue["stage"])
        elif check == "C": # Cell missing from stage
            add_to_revised_changes(action="force_cell_insert", instance=issue["instance"], missing_from=issue["missing_from"])
        elif check == "D": # Duplicate port
            add_to_revised_changes(action="force_reapply", signal=issue["duplicates"][0], note="dedup required")

set failure_mode = "PRE_FM_CHECK"; set needs_re_study = True
Write eco_fm_analysis_round<ROUND>.json and EXIT
```

---

## STEP 0 — FM Abort Detection (MANDATORY FIRST)

Read `<BASE_DIR>/data/<TAG>_eco_fm_verify.json`. Each target result:

| Status | Meaning |
|--------|---------|
| `{"status": "PASS"}` | FM ran and passed |
| `{"status": "FAIL", "failing_count": N}` | FM ran, N non-equivalent points |
| `{"status": "ABORT", "abort_type": "ABORT_LINK\|ABORT_NETLIST\|ABORT_SVF\|ABORT_OTHER"}` | FM aborted |
| `{"status": "NOT_RUN"}` | Not run this round |

Old format: each target is a string `"PASS"`, `"FAIL"`, or `"NOT_RUN"` — ABORT appears as `"FAIL"` with empty failing_points; check log to confirm.

**If ANY target is ABORT (or FAIL with 0/N/A failing_points in old format) — complete abort diagnosis before anything else.**

### Step 0a — Read FM log for error codes

```bash
for target in FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace; do
    log=<REF_DIR>/logs/${target}.log.gz
    [ -f "$log" ] && zcat "$log" | grep -E "^Error|FE-LINK|FM-[0-9]+|CMD-[0-9]+|Unresolved|cannot|no corresponding port" | head -30
done
```

### Step 0b — Classify abort type

| Error pattern | Abort Type | Fix |
|---------------|-----------|-----|
| `CMD-010` on `guide_eco_change` | `ABORT_SVF` | Remove eco_svf_entries.tcl; set `svf_update_needed: false` |
| `CMD-005` | `ABORT_SVF` | SVF elaboration error — same fix |
| `FE-LINK-7` + `no corresponding port` | `ABORT_LINK` | Go to Step 0c immediately |
| `FM-234` (Unresolved references) | `ABORT_LINK` | Port missing from module |
| `FM-156` (Failed to set top design) | `ABORT_LINK` | Cascades from FM-234 |
| `FM-001` design read error | `ABORT_NETLIST` | PostEco netlist not readable |
| Syntax error | `ABORT_NETLIST` | eco_applier wrote malformed Verilog |

### Step 0c — ABORT_LINK: diagnose missing ports

Extract missing ports from FE-LINK-7 errors:
```bash
zcat <REF_DIR>/logs/<target>.log.gz | grep "FE-LINK-7" | head -10
# Error: The pin '<missing_port>' of '.../<parent>/<instance>' has no corresponding port on '<module_name>'
```

**Step 0c-1:** Check if port is in the PostEco netlist module port list header:
```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | grep -n "module.*<module_name>" | head -3
# Read ~30 lines from that position to see the port list
```

**Step 0c-2:** Check if port was incorrectly marked ALREADY_APPLIED:
```python
import json
data = json.load(open('<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json'))
for stage, entries in data.items():
    if stage == 'summary': continue
    for e in entries:
        if (e.get('change_type') in ('port_declaration','port_connection')
            and e.get('status') == 'ALREADY_APPLIED'
            and '<missing_port>' in str(e)):
            print(f'{stage}: ALREADY_APPLIED — {e}')
            print(f'  already_applied_reason: {e.get("already_applied_reason", "NO REASON RECORDED")}')
```

**Step 0c-2b: Detect cell_type/port mismatch (FE-LINK-7 on technology library cell)**

If the FE-LINK-7 module path contains `/TECH_LIB_DB/` (or similar) rather than a user design module, this is a **cell_type/port mismatch** — the inserted ECO cell uses a pin name that doesn't exist on the technology library cell.

Pattern: `Error: The pin '<pin>' of '.../eco_<jira>_<seq>' has no corresponding port on '/TECH_LIB_DB/<WRONG_CELL_TYPE>'. (FE-LINK-7)`

**Fix:** Re-search the PreEco netlist for a cell that (a) implements `gate_function` AND (b) has the port names in `port_connections`. Set `action: fix_cell_type` and `failure_mode: ABORT_CELL_TYPE`:
```json
{
  "stage": "ALL",
  "action": "fix_cell_type",
  "gate_instance": "<eco_jira_seq>",
  "gate_function": "<gate_function from study JSON>",
  "wrong_cell_type": "<cell that was used but failed>",
  "missing_pin": "<the pin that didn't exist on wrong_cell_type>",
  "rationale": "FE-LINK-7: pin not a port of wrong_cell_type. Re-search PreEco for cell implementing gate_function with correct port.",
  "eco_preeco_study_update": {"action": "fix_cell_type", "gate_instance": "<eco_jira_seq>"}
}
```

**Step 0c-3:** If `already_applied_reason` is absent or says "found in file" (not "found in port list") → **false ALREADY_APPLIED** — eco_applier found the signal name as a wire but did not verify it was in the module port list.

**Step 0c-4:** If Step 0c-1 confirms port is missing AND Step 0c-2 confirms ALREADY_APPLIED was applied to this port_declaration → root cause confirmed: `failure_mode: ABORT_LINK`.

**Step 0c-5:** For each missing port, add to `revised_changes`:
```json
{
  "stage": "ALL",
  "action": "force_port_decl",
  "signal_name": "<missing_port>",
  "module_name": "<module_name>",
  "declaration_type": "input|output",
  "rationale": "FE-LINK-7: port missing from port list. eco_applier marked ALREADY_APPLIED incorrectly — signal exists as wire/DFF output but NOT in module port list header.",
  "eco_preeco_study_update": {
    "action": "force_reapply_port_decl",
    "signal_name": "<missing_port>",
    "module_name": "<module_name>"
  }
}
```

ROUND_ORCHESTRATOR adds `"force_reapply": true` to the port_declaration entry so eco_applier skips the ALREADY_APPLIED check.

**Only proceed to Step 1 if FM ran comparison (all targets show PASS or FAIL with actual failing counts).**

---

## STEP 1 — Read Structured FM Results

Read `<BASE_DIR>/data/<TAG>_eco_fm_verify.json` for failing counts and points per target:
- `FmEqvEcoSynthesizeVsSynRtl`
- `FmEqvEcoPrePlaceVsEcoSynthesize`
- `FmEqvEcoRouteVsEcoPrePlace`

Also read `eco_fixer_state` → `fm_results_per_round` for trend across all previous rounds (converging vs. diverging).

---

## STEP 2 — Quick Health Checks (run IN ORDER before mode classification)

### Check F — Unresolved condition inputs (MANDATORY FIRST — before Checks A–E)

Run this before all other checks. Unresolved condition inputs contaminate the gate chain and produce misleading DFF0X / non-equivalent failures that appear as Mode A or H.

```python
rtl_diff  = json.load(open('<BASE_DIR>/data/<TAG>_eco_rtl_diff.json'))
study     = json.load(open('<BASE_DIR>/data/<TAG>_eco_preeco_study.json'))
fenets_rpt = open('<BASE_DIR>/data/<TAG>_eco_step2_fenets.rpt').read()

unresolved = []
for change in rtl_diff.get('changes', []):
    for ci in change.get('condition_inputs_to_query', []):
        if ci['signal'] not in fenets_rpt:
            unresolved.append({'signal': ci['signal'], 'scope': ci['scope'],
                               'change_type': change.get('change_type')})

for stage, entries in study.items():
    if stage == 'summary': continue
    for e in entries:
        for pcs in [e.get('port_connections', {}), *e.get('port_connections_per_stage', {}).values()]:
            for pin, net in pcs.items():
                if isinstance(net, str) and net.startswith('PENDING_FM_RESOLUTION:'):
                    sig = net.split(':', 1)[1]
                    if not any(u['signal'] == sig for u in unresolved):
                        unresolved.append({'signal': sig, 'scope': '?', 'source': 'study_json_pending'})
```

**If any unresolved condition inputs found:**
- Check if a prior round already returned FM-036 for this signal:
  ```python
  prior_rerun = load(f"data/{TAG}_eco_fenets_rerun_round{ROUND-1}.json")
  signal_result = next((r for r in prior_rerun.get("condition_input_resolutions", [])
                        if r["signal"] == signal), None)
  action = "structural_trace" if (signal_result and not signal_result.get("resolved_gate_level_net")) else "rerun_fenets"
  ```
- Add `action: rerun_fenets` or `structural_trace` per signal; set `needs_re_study: true`.
- After first FM-036, switch to `structural_trace` — never rerun FM for the same signal twice (infinite loop).
- Do NOT skip this check. Without it, the flow loops on the same unresolvable signal burning all remaining rounds.

### Check A — eco_applied SKIPPED entries

```bash
python3 -c "
import json
data = json.load(open('<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json'))
for stage, entries in data.items():
    if stage == 'summary': continue
    for e in entries:
        if e.get('status') == 'SKIPPED':
            print(f'{stage}: SKIPPED — {e.get(\"cell_name\",\"?\")} reason={e.get(\"reason\",\"?\")}')
"
```

Any SKIPPED confirmed change is almost certainly the FM failure cause → Mode A. No netlist tracing needed.

### Check B — VERIFY_FAILED entries

```bash
python3 -c "
import json
data = json.load(open('<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json'))
for stage, entries in data.items():
    if stage == 'summary': continue
    for e in entries:
        if e.get('verify_failed') or e.get('status') == 'VERIFY_FAILED':
            print(f'{stage}: VERIFY_FAILED — {e.get(\"cell_name\",\"?\")}')
"
```

VERIFY_FAILED means the cell was edited but the change may have been only partially applied.

### Check C — Cross-reference failing DFFs against RTL diff target registers

```python
targets = [c.get('target_register','') for c in rtl_diff.get('changes',[]) if c.get('target_register')]
for target_name, result in fm.items():
    for pt in result.get('failing_points', [])[:10]:
        matched = any(t and t in pt for t in targets)
        print(f'{pt} -> matches RTL target: {matched}')
```

- Matches RTL diff `target_register` → ECO for that change did not work → Mode A or C
- Does NOT match any RTL target → downstream consumer or unrelated → Mode B or E

This single check classifies 90% of cases before any netlist tracing.

### Check E — DFF0X classification on ECO-inserted DFFs

**Trigger:** Failing DFF is classified `DFF0X`/`DFF0` AND matches `eco_<jira>_xxx` pattern.

**Step E1 — Read the DFF D-input net:**
```bash
zcat <REF_DIR>/data/PostEco/<FailingStage>.v.gz | grep -A6 "\b<dff_instance>\b" | grep "\.D("
```

**Step E2 — Walk the FULL D-input gate chain (do NOT stop at the first gate):**

```python
queue = [d_net]; visited_gates = set(); chain_depth = 0
while queue and chain_depth < 10:
    net = queue.pop(0); chain_depth += 1
    driver = find_driver_of_net(net, failing_stage_posteco)
    if driver is None:
        # GAP-18: Check submodule bus output FIRST
        bus_match = re.search(r'\.\s*\w+\s*\(\s*\{[^}]*\b' + re.escape(net) + r'\b', failing_stage_module_lines)
        if bus_match:
            # driven_by_submodule: true → Mode_H_submodule_rename; action: fix_named_wire with rename_wire=True
            break
        synth_driver = find_driver_of_net(net, synthesize_posteco)
        if synth_driver is not None:
            report_mode_H(gate=current_gate, pin=current_pin, net=net, stage=failing_stage)
        else:
            report_mode_A(net=net)
        break
    if driver["instance"] in eco_preeco_study:
        for pin, input_net in driver["inputs"].items():
            par_count   = grep_count_in_preeco(input_net, failing_stage)
            synth_count = grep_count_in_preeco(input_net, "Synthesize")
            if synth_count > 0 and par_count == 0:
                report_mode_H(gate=driver["instance"], pin=pin, net=input_net, stage=failing_stage)
                return  # Root cause found — stop walking
            queue.append(input_net)
        visited_gates.add(driver["instance"])
    else:
        break  # Non-ECO cell — stop tracing
```

Mode H is diagnosed on the **specific gate+pin** where the input net is inaccessible — NOT on the top-level DFF.

**Step E3 — Confirm with PreEco grep:**
```bash
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<net>")
par_count=$(zcat <REF_DIR>/data/PreEco/<FailingStage>.v.gz | grep -cw "<net>")
# synth_count > 0 AND par_count = 0 → confirmed Mode H
```

### Check D — Polarity verification for inserted gate cells

For `new_logic_gate` entries in eco_applied where the change is a `wire_swap` targeting a MUX select pin:

**Step D1:** Read the inserted gate type from PostEco.

**Step D2:** Re-derive the correct gate function from PreEco netlist (do NOT use RTL diff hint — it may be wrong). Run the Step 4c-POLARITY algorithm from eco_netlist_studier.md using actual PreEco MUX I0/I1 connections.

**Step D3:** If inserted gate type ≠ correct_gate_function → **Mode A (wrong gate function)**:
- Set `eco_preeco_study_update: {action: "update_gate_function", instance_name: "<inst>", gate_function: "<correct>"}`

---

## STEP 3 — Mode Classification

Use Step 2 results to classify:

| Step 2 result | Mode | Action |
|---------------|------|--------|
| FM aborted (Step 0) | ABORT_SVF / ABORT_LINK / ABORT_NETLIST | Fix tool/structure error; do NOT propose ECO rewires |
| SKIPPED entries (Check A) | A | Re-apply skipped change with corrected approach |
| VERIFY_FAILED entries (Check B) | A | Debug verify failure; re-apply |
| Failing DFF = RTL target register (Check C) | A or C | ECO for that register didn't work |
| Failing DFF ≠ any RTL target (Check C) | B or E | Wrong cell rewired OR pre-existing |
| Gate polarity wrong (Check D) | A | Replace gate with correct type |
| ECO-inserted DFF is DFF0X AND gate input has no direct primitive driver (Check E) | H | Gate input driven only through hierarchical port bus |
| `d_input_decompose_failed` in RTL diff | F | See Mode F below |
| `FmEqvEcoRouteVsEcoPrePlace` PASS, `FmEqvEcoPrePlaceVsEcoSynthesize` FAIL ≥ 10, no failing DFF matches target_register | G | Structural HFS mismatch — attempt fix_named_wire; if Priority 3 trace confirms no fixable net → manual_only |
| 3000+ cascade failures from one module scope where `<old_token>` was a module output port | `Mode_A_module_port_direct_gating` | Set `and_term_strategy: "module_port_direct_gating"` |
| Pre-existing DFF downstream of `and_term` old_token, and_term gate drove new net NOT the port | `INCOMPLETE_AND_TERM` | Re-study with `module_port_direct_gating`; new gate output = old_token; rename original driver; remove individual rewires |
| Newly inserted DFF fails with globally unmatched SE/SI cone nets (HFS aliases differ between stages) | `SCAN_CHAIN_MISMATCH` | Auto-fixable via tune file entries (GAP-20) |

**Multiple modes can coexist** — classify each failing point independently and combine all into a single `revised_changes` list.

### Mode A — ECO change not correctly applied

**Sub-causes (check in order):**
1. **SKIPPED** — status=SKIPPED in eco_applied → find reason and fix
2. **Wrong gate polarity** — inserted gate implements inverse of required logic → replace gate
3. **Wrong net name** — new_net connected to cell is wrong → grep PostEco for correct net
4. **Port missing** — port declaration/connection not applied. Before prescribing fix, check `eco_rtl_diff.json` for this signal:

   - If `implicit_wire: true` or `no_wire_decl_needed: true` → signal is an **internal wire within the declaring module** (connects two child instances inside, not a cross-boundary port). **Fix: force_reapply the existing port_connection entries within that module + add explicit `wire <signal>;` decl. NEVER add as input port to parent or connect from grandparent** — the driver is inside the module, not outside.
   - If neither flag → signal is a genuine missing port. Fix: `force_port_decl` + `force_reapply`.

5. **Module output port cascade (GAP-16):** When 3000+ failures cascade from one module scope, check if `<old_token>` is a module output port in `port_promotion` or `and_term` RTL diff changes. If yes, the `and_term` gate must drive the port name directly ("Module Port Direct Gating"). Do NOT propose an internal `<old_token>_orig` intermediate wire — this creates P&R cell type mismatches. Set `and_term_strategy: "module_port_direct_gating"` in revised_changes.

### Mode B — Regression: new failing points not in RTL diff

1. Read the failing DFF from PostEco and find its D-input net.
2. Find the driver cell and check if it appears in `eco_applied_round<ROUND>.json`.
3. If driver IS in eco_applied → ECO rewired this cell but it also drives unrelated DFFs → set `action: exclude` and `eco_preeco_study_update: {action: "mark_excluded", entry_key: "<cell_name>"}`.
4. If driver NOT in eco_applied → trace one more level (max 5 hops); if still not found → Mode E candidate — apply two-condition proof first.

### Mode C — Partial progress

Check for confirmed study entries absent from eco_applied:
```python
for stage in ["Synthesize", "PrePlace", "Route"]:
    study_confirmed = {e.get("cell_name","") for e in study.get(stage,[]) if e.get("confirmed")}
    applied_cells   = {e.get("cell_name","") for e in applied.get(stage,[])}
    missing = study_confirmed - applied_cells
    if missing: print(f"{stage}: confirmed in study but absent from eco_applied: {missing}")
```
Add each missing cell as a `rewire` or `insert_cell` action in revised_changes.

### Mode D — FM stage mismatch

The cell or net name differs between stages. Grep PostEco for the failing stage to find the actual cell name, then update eco_preeco_study accordingly.

**DFF0X sub-case (ECO-inserted DFF):** If the failing point is an ECO DFF (`eco_<jira>_` pattern) classified DFF0X, check in order:
1. Clock net absent in this stage → SKIPPED by eco_applier → Mode A (re-apply with correct stage clock)
2. Gate input undriven (submodule bus) → Mode H (fix_named_wire)
3. SE/SI scan nets differ across stages → Mode D (update `port_connections_per_stage` with correct stage nets)
Grep `<eco_instance>` in PostEco for this stage; read actual pin connections; update study JSON `port_connections_per_stage[<stage>]`.

### Mode E — Pre-existing failure (unrelated to ECO)

> **HARD RULE — ECO-inserted DFFs (`eco_<jira>_` pattern) are NEVER Mode E.** Do NOT write `set_dont_verify` or `set_user_match` for them. Re-examine as Mode A or Mode H immediately.
>
> **HARD RULE — `set_user_match` is NEVER written for ECO-inserted cells.** An equivalence failure on an ECO-inserted DFF is Mode A, H, or D — not an unmatched point.
>
> **HARD RULE — `set_dont_verify` is NEVER a substitute for `fix_named_wire`.** When an ECO DFF fails in P&R only due to HFS-renamed nets, the correct action is `fix_named_wire` (Mode H).

**Condition -1 — INTENTIONAL_CASCADE check (runs BEFORE Mode E proof):**

Read `eco_preeco_study.json`. For each `and_term` change entry that has `expected_cascade_dffs`:
```python
for change in study_changes:
    if change.get("and_term_strategy") == "module_port_direct_gating":
        cascade_dffs = change.get("expected_cascade_dffs", [])
        cascade_net  = change.get("expected_cascade_net", "")
        if failing_dff_instance in cascade_dffs:
            classify_as("INTENTIONAL_CASCADE",
                reason=f"{failing_dff_instance} is a downstream DFF whose D-input "
                       f"cone reaches {cascade_net}. The ECO intentionally changed "
                       f"the gating of {cascade_net} — this DFF's behavior change "
                       f"is a correct ECO consequence, not a bug.",
                action="manual_only",
                svf_guidance="set_dont_verify for this DFF (expected value change from ECO)"
            )
            return  # STOP — do NOT evaluate Mode E or other modes
```

**INTENTIONAL_CASCADE vs Mode E distinction:**
- Mode E: DFF was NEVER affected by the ECO — pre-existing structural divergence
- INTENTIONAL_CASCADE: DFF IS affected by the ECO — its behavior intentionally changed because it depends on a gated port. FM vs OLD SynRtl correctly detects this. No netlist fix possible. Engineer applies `set_dont_verify`.

Both result in `action: manual_only`, but INTENTIONAL_CASCADE should be reached in Round 1 (no wasted rounds trying to fix it), while Mode E requires the 5-condition proof.

**PROOF required — ALL five conditions must be satisfied. One failure → not Mode E:**

**Condition 0:** If `<failing_dff_instance>` matches `eco_<jira>_` → STOP. Cannot be Mode E.

**Condition 1 — No ECO contact:** Trace D-input chain backward max 5 hops; check every net against `old_net`, `new_net`, `old_token`, `new_token` from eco_rtl_diff.json. Any match → Mode A or B.

**Condition 2 — Existed in PreEco:**
```bash
zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -c "<failing_dff_instance_name>"
# count >= 1 → pre-existing; count = 0 → ECO-inserted → re-examine Mode A or H
```

**Condition 3 — Not a HFS net rename:** If DFF fails in P&R only, check each D-input gate's input nets:
```bash
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<input_net>")
pplace_count=$(zcat <REF_DIR>/data/PreEco/PrePlace.v.gz   | grep -cw "<input_net>")
# synth_count > 0 AND pplace_count = 0 → Mode H (fix_named_wire), NOT Mode E
```

**Condition 4 — Not downstream of an and_term target (CRITICAL — prevents misclassifying GAP-15 failures as Mode E):**
Check whether any `and_term` change in `eco_rtl_diff.json` has `old_token` whose gate-level equivalent net appears in this DFF's D-input cone:
```python
and_term_tokens = [c["old_token"] for c in rtl_diff["changes"] if c["change_type"] == "and_term"]
d_input_cone_nets = trace_D_input_chain(failing_dff, depth=10, stage_lines=posteco_lines)
for token in and_term_tokens:
    token_aliases = find_all_stage_aliases(token, preeco_lines)  # RTL net + all P&R aliases
    if any(alias in d_input_cone_nets for alias in token_aliases):
        # DFF is downstream of an and_term target → NOT Mode E
        # The ECO did not gate ALL consumers of old_token (GAP-15 not applied correctly)
        classify_as("INCOMPLETE_AND_TERM", old_token=token, failing_dff=failing_dff)
        return  # STOP — do NOT continue to Mode E
```

**Condition 5 — Cascade count is not suspiciously large:**
If `cascade_count >= 100` and all failing DFFs share a common module scope related to an `and_term` change → the cascade strongly suggests GAP-15 was not applied (not a pre-existing divergence). Do NOT classify as Mode E; classify as `INCOMPLETE_AND_TERM` and prescribe GAP-15 fix.

Only after ALL five conditions confirmed → classify Mode E → `action: manual_only`.

### Mode F — d_input_decompose_failed

Check `fallback_strategy` in eco_rtl_diff.json:

- **`intermediate_net_insertion`** → Mode F1: check if eco_preeco_study has `source: "intermediate_net_fallback"`. If present → classify Mode A (re-apply). If absent → studier did NOT run Step 0c → `action: manual_only`.

  When `intermediate_net_insertion` was applied and failing count is identical across 2+ consecutive rounds, try progressive fixes in order:
  ```python
  if "invert_cmux_constants" not in strategies_tried:
      action = "invert_cmux_constants"  # Flip all 1'b0/1'b1 constants in c_mux chain
  elif "try_structural_insertion" not in strategies_tried:
      action = "try_structural_insertion"  # Use Strategy A: find existing gate for structural insertion
      # eco_netlist_studier re-study with intermediate_net_strategy="structural_insertion"
      # Uses OA12/OAI21/AND3/ND3 compound gates feeding into existing priority chain gate
      # This produces FM-verifiable structure without set_dont_verify
  elif "try_strategy_A_andterm" not in strategies_tried:
      action = "try_strategy_A_andterm"  # Abandon intermediate_net_insertion, try and_term Strategy B
  elif "try_alternative_pivot" not in strategies_tried:
      action = "try_alternative_pivot"   # Find alternative pivot net
  else:
      action = "manual_only" if current_round >= max_rounds - 1 else "try_alternative_pivot"
  ```
  Also check `pivot_driver_cell_type` — if INVERTING (NOR, NAND, INV) and constants not yet flipped → `invert_cmux_constants` first. Run proactive Check D on ALL c_mux gates.

- **`null`** → Mode F2: set all revised_changes to `action: manual_only`. ROUND_ORCHESTRATOR exits loop early.

### Mode INCOMPLETE_AND_TERM — and_term gate did not drive port directly (GAP-15 violation)

**Trigger:** Pre-existing DFF (NOT `eco_<jira>_`) fails FM AND the D-input cone traces back to an `and_term` target signal (`old_token`) from `eco_rtl_diff.json` AND cascade count ≥ 50. This is NOT Mode E — it is a structural incompleteness in the ECO application: the `and_term` gate drove a new intermediate net (`n_eco_<jira>_<seq>`) and rewired only 2–4 individual consumer cells, leaving all other consumers (including the failing DFFs) still seeing the ungated value.

**Diagnosis steps:**
```python
# 1. Identify which and_term old_token's downstream this is
for change in rtl_diff["changes"]:
    if change["change_type"] != "and_term":
        continue
    old_token = change["old_token"]
    # 2. Find what the ECO gate output net was (from eco_preeco_study)
    eco_gate = find_eco_gate_for_and_term(old_token, eco_preeco_study)
    if eco_gate:
        current_output = eco_gate["output_net"]  # what the gate actually drives
        # 3. If current_output != old_token → GAP-15 not applied (drives new net, not port)
        if current_output != old_token:
            return {
                "failure_mode": "INCOMPLETE_AND_TERM",
                "old_token": old_token,
                "eco_gate": eco_gate["instance_name"],
                "current_output_net": current_output,
                "required_output_net": old_token,
                "cascade_count": cascade_count,
                "fix": "apply_module_port_direct_gating"
            }
```

**Fix — prescribe module_port_direct_gating re-study:**
```json
{
  "stage": "ALL",
  "action": "re_study_and_term",
  "change_type": "and_term",
  "old_token": "<old_token>",
  "required_strategy": "module_port_direct_gating",
  "current_incorrect_output": "<n_eco_jira_seq>",
  "required_output": "<old_token>",
  "rationale": "and_term gate drives new intermediate net instead of module port directly. All <N> consumer DFFs still see ungated value. Fix: new gate output = old_token (port name). Rename original driver output to old_token_orig. No individual consumer rewires needed.",
  "eco_preeco_study_update": {
    "action": "update_and_term_strategy",
    "old_token": "<old_token>",
    "new_output_net": "<old_token>",
    "rename_original_driver_output_to": "eco_<jira>_<old_token>_orig",
    "remove_individual_rewire_entries": true
  }
}
```

**HARD RULE: Do NOT classify INCOMPLETE_AND_TERM as Mode E.** The 3000+ / 159 / 4616 cascade pattern is the signature of an incomplete and_term application, not pre-existing P&R structural divergence.

### Mode F3 — Pre-existing DFF failing due to wrong ECO gate chain

**Trigger:** Pre-existing DFF (NOT `eco_<jira>_`) fails FM AND cascade count ≥ 100 in one stage AND Condition 4 of Mode E check confirmed no `and_term` connection.

The failing DFF is a DOWNSTREAM register driven by a wrong ECO c_mux cascade. Do NOT classify as Mode E.

```python
if not is_eco_inserted(failing_dff) and cascade_count > 100:
    # First check INCOMPLETE_AND_TERM (higher priority)
    if and_term_downstream_check(failing_dff, rtl_diff, posteco_lines):
        classify_as("INCOMPLETE_AND_TERM")
        return
    # Then check c_mux cascade
    eco_gates_in_cone = trace_D_input_chain_for_eco_gates(failing_dff, eco_preeco_study)
    if eco_gates_in_cone:
        classify_as_mode_A_with_eco_chain_diagnosis(eco_gates_in_cone)
```

Classify as Mode A targeting the specific ECO gate that is wrong. Run Check D (polarity) on ALL c_mux gates in the chain. Try progressive fixes (update_gate_function → invert_cmux_constants → try_structural_insertion → try_alternative_pivot) before declaring manual_only.

### Mode WRONG_GATE_STRUCTURE — MUX2 cascade creates FM-unverifiable structure

**Trigger:** `FmEqvEcoPrePlaceVsEcoSynthesize` fails with N > 50 points AND:
1. `d_input_decompose_failed: true` in `eco_rtl_diff.json` for a `new_logic` change
2. Failing DFFs are all in the same module scope as the `intermediate_net_insertion` change
3. At least one `MUX2` gate appears in `eco_preeco_study.json` for this change (as `fn=MUX2`)

**Root cause:** MUX2 cascade does not match the structural form RTL synthesis would produce for priority expressions. PrePlace has a different synthesis of the same RTL (different gate topology), so FM structural comparison fails. The correct approach uses compound gates discovered from PreEco that directly implement the RTL boolean, matching the synthesis output structure — FM can then auto-verify without SVF.

**Diagnosis:**
```python
# Check if failing DFFs are in same module as intermediate_net_insertion
mux2_gates = [e for e in eco_preeco_study.get("Synthesize",[])
              if e.get("gate_function") == "MUX2" and e.get("source") == "intermediate_net_fallback"]
if mux2_gates and ppvssynth_failing_count > 50:
    classify_as("WRONG_GATE_STRUCTURE", mux2_gates=mux2_gates)
```

**Fix — prescribe structural re-study:**
```json
{
  "stage": "ALL",
  "action": "try_structural_insertion",
  "failure_mode": "WRONG_GATE_STRUCTURE",
  "rationale": "MUX2 cascade produces structural non-equivalence in PPvsSynth. Re-study with Strategy A: search PreEco for an existing compound gate in the priority chain whose input can accept the new condition. Discover appropriate gate types from PreEco library — do NOT prescribe gate names. Never use MUX2 for priority encoding.",
  "eco_preeco_study_update": {
    "action": "re_study_intermediate_net_insertion",
    "preferred_strategy": "structural_insertion",
    "preferred_gate_discovery": "Search PreEco for compound gates (gates with 3+ inputs implementing AND-OR or OR-AND boolean combinations) that exist in the priority chain cone. Use discovered gate types — they are library-correct and FM-verifiable.",
    "forbidden_gate_types": ["MUX2"],
    "note": "Feed new conditions into an existing compound gate in the priority chain rather than building a new parallel MUX cascade alongside the existing logic"
  }
}
```

**HARD RULE: Do NOT classify WRONG_GATE_STRUCTURE as Mode E.** The engineer's pure netlist solution (no SVF) proves these failures are fixable. The correct fix is changing the gate structure, not adding set_dont_verify.

### Mode CTS_CLOCK_RENAMED — ECO DFF clock pin renamed by CTS in Route

**Trigger:** ECO-inserted DFF fails only in `FmEqvEcoRouteVsEcoPrePlace` AND `cts_clock_renamed: true` in study JSON AND DFF type is DFF (not DFF0X).

**Diagnosis:** Clock tree synthesis renamed the DFF's CP clock net in Route. FM sees mismatched clock connections between PrePlace and Route → non-equivalent.

**Fix:**
```json
{ "stage": "Route", "action": "rewire_cp",
  "instance": "<eco_dff_instance>", "pin": "CP",
  "old_net": "<preplace_cp_net>",
  "new_net": "<cts_clock_net_from_neighbour_dff>",
  "rationale": "CTS renamed clock net in Route. Rewire CP to CTS-assigned equivalent found in neighbour DFF of same clock domain." }
```
Find `<cts_clock_net>` by: read CP pin of a neighbour DFF in the same module and clock domain in Route PostEco.

### Mode CTS_BBNET_INPUT — ECO gate input passes through CTS multi-driver merged cell

**Trigger:** ECO-inserted DFF is DFF0X in Route (passes Synthesize and PrePlace) AND the gate feeding its D-input uses a net whose Route driver is a CTS-created merged cell (absent from Synthesize/PrePlace PreEco).

**Diagnosis:** CTS creates multi-driver merged cells for test/reset signals in Route. FM treats these as black-box → gate input appears undriven → DFF0X. The underlying signal has a primary input port declaration in the module header that FM can trace directly without going through the merged cell.

**Fix:**
```json
{ "stage": "Route", "action": "rewire_gate_input",
  "gate_instance": "<eco_gate>", "pin": "<input_pin>",
  "old_net": "<cts_merged_cell_driven_net>",
  "new_net": "<primary_input_port_for_same_signal>",
  "rationale": "CTS merged cell black-boxed by FM. Use primary input port (single driver, FM-traceable) instead." }
```
Find primary input port by: `grep "input.*\b<base_signal_name>\b" <Route_module_header>` — the declared input port for the same functional signal.

### Mode G — Structural stage mismatch

Apply `set_dont_verify` scoped to common hierarchy prefix only when Priority 3 structural trace confirms no fixable net exists.

### Mode H — Gate input driven only through hierarchical submodule output port bus

**Diagnosis:** ECO-inserted DFF is `DFF0X` in P&R stages (passes Synthesize). Check E confirms a gate input net has no direct primitive driver — only connected through a submodule's output port bus. FM black-boxes that submodule in P&R → net appears undriven → DFF0X.

**Fix:** Declare a named wire, replace the source net in the hierarchical port bus with the named wire, and use the named wire as the gate input.

```json
{
  "stage": "PrePlace|Route|ALL",
  "action": "fix_named_wire",
  "gate_instance": "<eco_jira_seq>",
  "input_pin": "<A1|A2|I|...>",
  "source_net": "<the_net_currently_in_port_bus>",
  "rationale": "Gate input has no direct primitive driver in <Stage> — only through hierarchical port bus. FM black-boxes submodule in P&R → DFF0X. Named wire needed.",
  "eco_preeco_study_update": {
    "action": "set_needs_named_wire",
    "gate_instance": "<eco_jira_seq>",
    "input_pin": "<A1|A2|I|...>",
    "source_net": "<the_net_currently_in_port_bus>"
  }
}
```

Apply only to stages where DFF is DFF0X. Do NOT flag Synthesize if it passes.

### Mode H sub-type — Submodule bus output (GAP-18): `Mode_H_submodule_rename`

**Trigger:** Check E Step E2 driver-not-found branch matches the net via submodule bus regex:
```python
re.search(r'\.\s*\w+\s*\(\s*\{[^}]*\b<signal>\b', <failing_stage_module_lines>)
```

**PERSISTENT DFF0X check — MANDATORY before prescribing fix_named_wire:**

Check if a `rename_wire: true` fix was already applied for this gate+pin in a previous round:
```python
prev_analysis = load(f"data/{TAG}_eco_fm_analysis_round{ROUND-1}.json") if ROUND > 1 else None
rename_already_tried = (
    prev_analysis and
    any(c.get("action") == "fix_named_wire" and c.get("rename_wire") and
        c.get("gate_instance") == gate_instance
        for c in prev_analysis.get("revised_changes", []))
)
```

**If `rename_already_tried = True` AND DFF0X still fails this round:**
The submodule is black-boxed by FM in P&R — renaming the wire doesn't help because FM cannot trace through the submodule boundary regardless of wire name. Prescribe **submodule insertion** instead:
```python
# Find which child submodule drives the signal (from submodule bus match)
child_module_type = extract_module_type_from_bus_driver(bus_match, failing_stage_lines)
child_instance    = find_instance_of_module(child_module_type, declaring_module_lines)

add_to_revised_changes({
    "action": "move_gate_to_submodule",
    "gate_instance": gate_instance,
    "preferred_insertion_scope": child_instance,
    "submodule_type": child_module_type,
    "rationale": (f"rename_wire already applied in Round {ROUND-1} but DFF0X persists. "
                  f"Child module {child_module_type} is black-boxed by FM in P&R — "
                  f"wire renaming cannot fix submodule black-box boundary. "
                  f"Move gate chain inside {child_instance} where signal is directly accessible. "
                  f"Gate chain output becomes new output port of {child_module_type}; "
                  f"DFF at parent uses this port as D-input.")
})
```
eco_netlist_re_studier reads `action: move_gate_to_submodule` and uses `preferred_insertion_scope` when updating the study JSON.

**If `rename_already_tried = False` (first time seeing this failure):** prescribe `fix_named_wire` as before.
When matched → `driven_by_submodule: true`. The net is NOT absent — it has no direct primitive driver. Never declare it absent or undriven.

**Fix:** Rename the net to a meaningful eco name; keep the existing port bus connection.
```json
{
  "stage": "<FailingStage>",
  "action": "fix_named_wire",
  "rename_wire": true,
  "source_net": "<original_net>",
  "rationale": "Net driven through submodule output port bus — no direct primitive driver. Rename to eco wire; keep port bus connection.",
  "eco_preeco_study_update": {
    "action": "set_needs_named_wire",
    "rename_wire": true,
    "source_net": "<original_net>"
  }
}
```

### SCAN_CHAIN_MISMATCH (GAP-20)

**Trigger:** Newly inserted DFF fails with globally unmatched SE/SI cone nets where SE net differs between PrePlace and Route (both are P&R HFS aliases), or study JSON has `needs_se_tune: true`.

**Fix:** Auto-generate tune file entries for each failing stage-to-stage target:
```
set_constant -type port {<DFF_full_hierarchy_path>/SE} 0
```
Apply for both ref and impl sides. eco_fm_runner adds these to the tune file before the next FM submission. Do NOT set `manual_only`. Do NOT escalate to engineer.

---

## STEP 3b — Deep Netlist Investigation (when cause is unclear after Steps 1–3)

Run if failure cannot be classified after Checks A–D. Reading PostEco netlists is mandatory before giving up.

### 3b-1 — Read failing points from FM rpt
```bash
zcat <REF_DIR>/rpts/<target>/<target>__failing_points.rpt.gz 2>/dev/null | head -50
```

### 3b-2 — Trace failing DFF in PostEco
```bash
zcat <REF_DIR>/data/PostEco/${stage}.v.gz | grep -n "<dff_instance_name>" | head -5
```
Check D pin net (expected `n_eco_<jira>_<seq>`), Q pin, and DFF cell type.

### 3b-3 — Verify each ECO gate that should drive this DFF
```bash
zcat <REF_DIR>/data/PostEco/${stage}.v.gz | grep -n "eco_<jira>_<seq>" | head -5
```
Verify gate is present, output net matches DFF D pin.

### 3b-4 — Verify port declarations in hierarchical netlist
```bash
# Port list header
zcat <REF_DIR>/data/PostEco/${stage}.v.gz | \
  awk '/^module <module_name>/{found=1} found && /\) ;/{print NR": "$0; found=0; exit} found{print NR": "$0}' | head -30
# Port declaration
zcat <REF_DIR>/data/PostEco/${stage}.v.gz | grep -n "input\|output" | grep "<signal_name>"
# Port connection
zcat <REF_DIR>/data/PostEco/${stage}.v.gz | grep -n "\.<port_name>( <net_name> )"
```

### 3b-5 — Determine action after investigation

| Root cause found | Action |
|-----------------|--------|
| Gate missing despite INSERTED status | `insert_cell` in revised_changes |
| Port missing from port list despite APPLIED status | `force_port_decl`; set `force_reapply: true` in study |
| Gate present but wrong net on DFF D pin | `rewire` with correct nets |
| Gate/nets correct but FM still fails | Set `needs_re_study: true` |
| FM result inconsistent with netlist | Set `needs_fm_resubmit: true` |

---

## STEP 4 — Build Revised Strategy

**RULE 1: Diagnose ALL failing points — not just one mode.** Run ALL checks (A–H) across ALL failing points before writing revised_changes. NEVER stop at the first issue.

```python
all_revised_changes = []
for target, failing_points in all_failing_points.items():
    for dff_path in failing_points:
        issue = classify_failing_point(dff_path, target)
        if issue not in all_revised_changes:
            all_revised_changes.append(issue)

# Proactive Check D on ALL new_logic_gate entries
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in eco_applied.get(stage, []):
        if entry.get("change_type") == "new_logic_gate":
            check_gate_polarity(entry)
# Proactive port_connection false-applied check
for conn_entry in eco_applied.get(stage, []):
    if conn_entry.get("change_type") == "port_connection" and conn_entry.get("status") == "APPLIED":
        check_port_conn_in_instance_block(conn_entry)
```

**RULE 2: Check D runs proactively whenever Mode H is diagnosed.** When a gate input is undriven (Mode H), gate output is stuck constant regardless of polarity — the polarity error is masked. After fixing Mode H, wrong polarity causes a new FM failure next round. Fix gate polarity and named wire in the same round.

```python
checked_gates = set()
for stage in ["Synthesize", "PrePlace", "Route"]:
    for entry in eco_applied.get(stage, []):
        if entry.get("change_type") != "new_logic_gate": continue
        gate_instance = entry.get("instance_name")
        if not gate_instance or gate_instance in checked_gates: continue
        checked_gates.add(gate_instance)
        correct_fn = re_derive_gate_function_from_preeco(gate_instance, entry.get("gate_function"), entry.get("port_connections", {}))
        if correct_fn and correct_fn != entry.get("gate_function"):
            all_revised_changes.append({
                "action": "update_gate_function", "stage": "ALL",
                "gate_instance": gate_instance,
                "wrong_gate_function": entry.get("gate_function"),
                "correct_gate_function": correct_fn,
                "rationale": f"Gate has wrong gate_function in study JSON. Correct function derived from PreEco netlist.",
                "eco_preeco_study_update": {"action": "update_gate_function", "gate_instance": gate_instance, "gate_function": correct_fn}
            })
```

**RULE 3: revised_changes must be ACTIONABLE and HONEST.**
- Every entry must name a specific cell or scope — never "apply the same fix again" without naming it.
- If root cause cannot be determined after Step 3b → describe every check done. Do NOT invent a fix.
- **NEVER write `set_dont_verify` or `set_user_match`** — SVF updates are prohibited for the AI flow. Use `manual_only` for cases requiring engineer SVF.
- `set_dont_verify` is only valid for proven Mode E or Mode G-P&R (Priority 3 structural trace confirmed signal truly absent in that stage, ECO architecturally correct, failure is stage-to-stage only).

**`UNRESOLVABLE` input handling — when `set_dont_verify` IS valid:**
Confirm: (1) driver cell AND all ancestors of the signal have 0 occurrences in failing stage's PreEco; (2) ECO logic is architecturally correct from RTL; (3) failure is in a stage-to-stage comparison only. Record reason: `"Signal <signal> P&R-optimized away in <stage>. ECO logic verified correct from RTL. Stage-to-stage comparison only."` This is Mode G-P&R, NOT Mode E.

**Output JSON structure:**
```json
{
  "round": "<ROUND>",
  "failure_mode": "ABORT_SVF|ABORT_LINK|ABORT_NETLIST|ABORT_CELL_TYPE|A|B|C|D|E|F|G|H|UNKNOWN",
  "diagnosis": "<specific — which DFF, port, net, which check found it, what was checked in 3b>",
  "failing_points_count": {
    "FmEqvEcoSynthesizeVsSynRtl": "<N>",
    "FmEqvEcoPrePlaceVsEcoSynthesize": "<N>",
    "FmEqvEcoRouteVsEcoPrePlace": "<N>"
  },
  "wrong_cells": ["<cell_name_if_mode_B>"],
  "needs_re_study": false,
  "re_study_targets": [],
  "needs_rerun_fenets": false,
  "rerun_fenets_signals": [],
  "revised_changes": [
    {
      "stage": "Synthesize|PrePlace|Route|ALL",
      "action": "rewire|insert_cell|new_logic_dff|new_logic_gate|revert_and_rewire|exclude|force_port_decl|fix_named_wire|structural_trace|manual_only|update_gate_function|rerun_fenets|scan_chain_tune|fix_cell_type",
      "cell_name": "<cell>",
      "pin": "<pin>",
      "old_net": "<old>",
      "new_net": "<new>",
      "signal_name": "<signal_for_port_decl_actions>",
      "module_name": "<module_for_port_decl_actions>",
      "declaration_type": "input|output",
      "rationale": "<which DFF/port, why this change fixes it, what evidence was found>",
      "eco_preeco_study_update": {
        "action": "mark_excluded|update_net|add_entry|mark_confirmed|force_reapply_port_decl|set_needs_named_wire|update_gate_function|fix_cell_type",
        "entry_key": "<cell_name_or_change_type>",
        "field": "<field_to_update>",
        "value": "<new_value>"
      }
    }
  ],
  "svf_update_needed": "true|false",
  "svf_commands": []
}
```

**`action` values:**
- `rewire` — net substitution on existing cell
- `insert_cell` — insert new inverter
- `new_logic_dff` — insert new flip-flop
- `new_logic_gate` — insert new combinational gate (include `gate_function`)
- `revert_and_rewire` — previous rewire was wrong; apply corrected version
- `exclude` — do NOT touch this cell again (Mode B wrong cell)
- `force_port_decl` — ABORT_LINK or false-APPLIED; force re-apply port declaration/connection
- `fix_named_wire` — Mode H; gate input driven only through hierarchical port bus
- `update_gate_function` — Mode A (Check D); wrong gate polarity; eco_netlist_studier updates gate_function and cell_type
- `rerun_fenets` — Check F; condition input never FM-queried
- `structural_trace` — Check F; after first FM-036; search P&R netlist for driver cell anchor
- `scan_chain_tune` — SCAN_CHAIN_MISMATCH (GAP-20); auto-fixable via tune file entries
- `fix_cell_type` — ABORT_CELL_TYPE; re-search PreEco for cell with correct gate_function and pin names
- `manual_only` — Mode E (proven pre-existing), Mode F2, Mode G after Priority 3 exhausted

**`eco_preeco_study_update`** is MANDATORY for Modes B, D, A, and ABORT_LINK — without it the next round re-applies the same wrong change.

---

## STEP 5 — Write Output

Write `<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`.

Every `revised_changes` entry must name a specific cell or scope. Never write "apply the same fix again" without naming the specific target.

---

## Critical Rules

1. **FM abort first** — classify abort type (Step 0a–0c) before proposing any ECO rewires.
2. **ABORT_LINK = missing port** — FE-LINK-7 + FM-234 means port declaration not applied. Check ALREADY_APPLIED entries; set `force_port_decl`.
3. **ALREADY_APPLIED may be wrong** — always check `already_applied_reason`. "Found in file" without "in port list" is suspect.
4. **SKIPPED entries are the first clue** — always check eco_applied for SKIPPED before any cone tracing.
5. **Cross-reference failing DFF against RTL diff `target_register` immediately** — classifies 90% of cases without netlist tracing.
6. **Polarity check re-derives from PreEco netlist** — do NOT trust RTL diff gate function hint.
7. **NEVER use `set_dont_verify` as fallback** — only for proven Mode E or Mode G-P&R (all conditions verified).
8. **eco_preeco_study_update is mandatory for Modes B, D, A, ABORT_LINK.**
9. **Stage-specific analysis** — grep the CORRECT stage's PostEco/PreEco netlist, not always Synthesize.
10. **Pre-existing requires cone trace proof** — trace ≥ 5 hops and confirm no ECO net contact before Mode E.
11. **Deep investigation before UNKNOWN** — never return `failure_mode: UNKNOWN` without completing Step 3b.
12. **Set `needs_re_study: true` when upstream data is wrong** — if eco_preeco_study.json has wrong gate chains or net names.
13. **Honest output over forced output** — describe every check done if root cause cannot be determined. Do NOT invent a fix.
14. **Mode F exits the loop** — if all revised_changes are `manual_only`, ROUND_ORCHESTRATOR spawns FINAL_ORCHESTRATOR immediately.

**PRIORITY RULE — NETLIST FIX FIRST, SVF/TUNE NEVER:**
1. Fix the netlist — find the wrong/missing connection and correct it (fix_named_wire, rewire, re-insert)
2. Only if Priority 3 structural trace confirms signal truly absent AND ECO is architecturally correct AND failure is stage-to-stage only → `set_dont_verify` on that specific DFF
3. NEVER update tune files to work around FM failures — tune file changes mask netlist problems
4. NEVER use `set_user_match` for ECO-inserted cells — an equivalence failure means the netlist is wrong

**HFS net rename is a NETLIST fix:** When an ECO gate uses a net that P&R renames (HFS distribution), fix with `fix_named_wire` — do NOT suppress with `set_dont_verify`.
