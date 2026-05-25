# ECO FM Pattern Library — Reference for `eco_fm_analyzer`

This file is **reference material** consulted by `eco_fm_analyzer.md`. It is NOT a flowchart. The analyzer walks evidence first (Phase 1-3), forms hypotheses, then looks up matching entries here.

**Reading order for the analyzer:**
1. `eco_fm_analyzer.md` Phase 1-3 → builds evidence + hypotheses
2. Look up matching entries in **§B-ABORT** (if `loop_verdict=RERUN_SAME_ROUND`) or **§B-FAIL** (if `loop_verdict=ADVANCE_NEXT_ROUND`)
3. Apply recipe; honor **§D Hard Rules** at all times
4. Emit JSON per **§F Output Schema**

---

## §A0 — Verdict Derivation Rules (canonical)

`loop_verdict` is set by `eco_fm_evidence_walk.py::initial_verdict()` from `eco_fm_verify.json`. Single source of truth for the whole chain (analyzer, ROUND_ORCHESTRATOR Step 6d-VERDICT, re-studier).

| `eco_fm_verify` per-target status | `loop_verdict` |
|---|---|
| File missing / empty | `RERUN_SAME_ROUND` |
| ANY target = `ABORT` | `RERUN_SAME_ROUND` |
| ANY target = `MISSING` or `NOT_RUN` | `RERUN_SAME_ROUND` |
| ALL three targets = `PASS` | `CONVERGED` |
| Mix of `PASS`/`FAIL` (no ABORT) | `ADVANCE_NEXT_ROUND` |

Any consumer that reads `loop_verdict` MUST cross-check it against `eco_fm_verify.json` to detect drift if these rules ever change.

---

## §A — FM Report Reference Table

Every FM-generated artifact, what it tells you, and when to consult it.

### Per-target reports (`<REF_DIR>/rpts/<target>/`)

| File | Tells you | When to consult |
|------|-----------|-----------------|
| `<target>__failing_points.rpt.gz` | Every failing compare point: REF/IMPL hierarchical paths, cell type (DFF/DFF0X/DFF1X/LATCG/Loop/BBPin/etc.) | ALWAYS for FAIL verdict; first failing-point identity check |
| `<target>__analyze_points.rpt.gz` | **Cone divergence detail per failing point**: "Unmatched Cone Inputs" (REF and IMPL sides), "Required Inputs" (with logic value), "Failing Reverse Clock Gating" (LatCG asymmetry), "Rejected Guidance Commands" (SVF Operations rejected) | ALWAYS for FAIL verdict; tells you WHICH PIN's cone diverges and WHY |
| `<target>__before_verify_undriven_nets.rpt.gz` | Every IMPL net that FM treats as `Und` (undriven cut-point) | ALWAYS for FAIL verdict; identifies broken hierarchical connections |
| `<target>__before_verify_constants.rpt.gz` | What FM treats as constant before verification (covers RTL-derived + tune-applied + SVF-applied constants) | When checking whether `set_constant` from tune file actually landed |
| `<target>_user_added_constants.rpt.gz` | Constants explicitly added by the tune file's `set_constant` calls | When verifying tune directives ran (compare vs `before_verify_constants` to detect partial application) |
| `<target>__before_verify_directives.rpt.gz` | Status of every `set_dont_verify`, `set_dont_reverse`, `set_user_match` directive (applied / unmatched / failed) | When tune file uses `set_dont_*` and you need to know if the directive landed |
| `<target>__svf_accept_*.rpt.gz` | SVF Operation IDs accepted by FM (`reg_const`, etc.) | When checking whether SVF guidance for a specific signal landed |
| `<target>__svf_reject_*.rpt.gz` | SVF Operations rejected per category (`change_name`, `const`, `datapath`, `inv_push`, `merge`, `multibit`, `multiplier`, `reg_const`, `reg_duplication`, `reg_merg`, `replace`) | When `analyze_points` reports "Rejected Guidance Commands"; correlate Op IDs to find the rejection that broke verification |
| `<target>__matched_points.rpt.gz` | How each compare point was matched: `Auto`, `Name`, `User(Last)` (set_user_match), `SVF` | When verifying `set_user_match` directives applied + identifying compare points that succeeded |
| `<target>__unmatched_points.rpt.gz` | Compare points FM could not pair (BBPin, Cut, LATCG, Und cuts) | When the failing-point count includes "unmatched" entries |
| `<target>__reg_map.rpt.gz` | REF↔IMPL register pairing including multibit cell decomposition (e.g., `oref reg[1]` → `impl multibit_cell/Q7`) | When the failing DFF is part of a multibit cell or you need to verify name-to-cell mapping |
| `<target>__hdlin_mismatches.rpt.gz` | Module re-elaboration mismatches between REF and IMPL | When ABORT_NETLIST suspected; rare but useful |
| `<target>__bbox_summary.rpt.gz` | Black-box summary | Sanity check that no blackbox was unexpectedly created |
| `<target>.dat` | Compact tile-level overall verification status table | Quick top-level status check |
| `<target>.rpt.gz` | Top-level FM report (passing/failing/aborted counts, summary) | First quick read for overall pass/fail breakdown |
| `<target>__runtime.rpt.gz` | Per-stage timing (constraints, preverify, match, verify, loops, reports) | Identifies which FM phase errored — `error` in `Constraints` means tune file failed; `error` in `Match` means link failure; etc. |
| `<target>.cmd.gz` | The actual TCL commands FM executed | When tune-file behavior is suspect — see what FM actually ran |

### Per-target stdout logs (`<REF_DIR>/logs/<target>.log.gz`)

Search the log for these patterns:

| Pattern | Tells you |
|---------|-----------|
| `^Error:` | Any FM error |
| `FE-LINK-7` | Port mismatch on instance — missing or extra port |
| `FM-001` | Design read error — netlist unreadable |
| `FM-156` | Failed to set top design — typically cascades from FM-234 |
| `FM-234` | Unresolved references — port missing from module |
| `FM-599` | Verilog parse error — syntax issue |
| `FM-036` | Unknown name in tune file `get_pins`/`get_cells` — cell or pin not in elaborated netlist |
| `CMD-005`, `CMD-010` | SVF guidance command syntax error |
| `Duplicate wire/tri/wand/wor declaration` | Wire declared twice (e.g., before and after instance hookup) |
| `AMD-WARN: eco<jira>:` | Tune file `puts` from preverify TCL — directive was attempted but the `get_cells`/`get_pins` returned empty |
| `set_constant` (output line) | Whether `set_constant` actually executed (look for the line, then check `before_verify_constants.rpt.gz`) |
| `set_dont_reverse`, `set_user_match` (output) | Same — was the directive issued? |

### Per-tag JSON artifacts (`<BASE_DIR>/data/`)

| File | Tells you |
|------|-----------|
| `<TAG>_eco_fm_verify.json` | Per-target: `{"status": "PASS\|FAIL\|ABORT\|NOT_RUN", "failing_count": N, "failing_points": [...], "abort_type": "...", "log_excerpt": "..."}` |
| `<TAG>_eco_rtl_diff.json` | Original RTL intent: every change with type, scope, target_register, condition_inputs, gate chain, flags (implicit_wire, mode_H_risk, etc.) |
| `<TAG>_eco_preeco_study.json` | Per-stage cell instances + port_connections that the studier prescribed |
| `<TAG>_eco_applied_round<N>.json` | Per-stage entries from eco_netlist_port_rewire with `status: APPLIED\|SKIPPED\|VERIFY_FAILED\|ALREADY_APPLIED` and reasons |
| `<TAG>_eco_fixer_state` | Round counter, fm_results_per_round trend, strategies_tried list |
| `<TAG>_eco_fm_evidence_round<N>.json` | NEW — output of `eco_fm_evidence_walk.py`; structured per-DFF dossiers |
| `<TAG>_eco_fm_xstage_round<N>.json` | NEW — output of `eco_fm_xstage_compare.py`; 3-way structural compare |

---

## §B-ABORT — Abort Patterns (verdict = `RERUN_SAME_ROUND`)

When FM aborts, it never compared the netlists. The fix is structural (link/parse/SVF error) — patch the netlist or remove the offending SVF entry, then **re-submit FM in the SAME round**. Never advance the round counter on an abort.

**Hard rule for ABORT verdict**: Do NOT prescribe `re_study`, `eco_netlist_port_rewire re-run`, or `revised_changes` that target failing-point logic. Only emit fixes that resolve the elaboration error.

### B-ABORT-1 — `ABORT_LINK` (Port mismatch)

**Symptoms / Evidence:**
- FM log contains `FE-LINK-7` errors: `The pin '<port>' of '.../<parent>/<inst>' has no corresponding port on '<module>'`
- May cascade to `FM-234` (Unresolved references) and `FM-156` (Failed to set top design)
- `<target>.dat` shows `error` under `Match` or `PreVerify`
- `<target>__failing_points.rpt.gz` is empty/missing

**Diagnosis:**
1. Extract every `FE-LINK-7` line from log: `zcat <REF_DIR>/logs/<target>.log.gz | grep "FE-LINK-7"`
2. For each missing port:
   - Check PostEco netlist module port-list header for the named module
   - Check if `<TAG>_eco_applied_round<N>.json` marked the port_declaration entry as `ALREADY_APPLIED` (potentially false-positive)
3. Sub-classify:
   - **Sub-A**: Port missing from user module port list, ALREADY_APPLIED was wrong → eco_applier saw the signal as a wire but did NOT verify it's in the module port list → `force_port_decl` with `force_reapply=true`
   - **Sub-B**: Module path contains `/TECH_LIB_DB/` — the inserted ECO cell uses a pin name that doesn't exist on the technology library cell → `fix_cell_type` (verify via `script/eco_scripts/eco_cell_truth_tables.py`)

**Recipe — Sub-A:**
```json
{
  "stage": "ALL",
  "action": "force_port_decl",
  "signal_name": "<missing_port>",
  "module_name": "<module>",
  "declaration_type": "input|output",
  "rationale": "FE-LINK-7: port missing from module port list. eco_applier marked ALREADY_APPLIED incorrectly — signal exists as wire/DFF output but NOT in port list header.",
  "eco_preeco_study_update": {
    "action": "force_reapply_port_decl",
    "signal_name": "<missing_port>",
    "module_name": "<module>"
  }
}
```

**Recipe — Sub-B:**
```json
{
  "stage": "ALL",
  "action": "fix_cell_type",
  "gate_instance": "<eco_jira_seq>",
  "gate_function": "<from study JSON>",
  "wrong_cell_type": "<cell that was used>",
  "missing_pin": "<pin not on wrong cell>",
  "rationale": "FE-LINK-7 on TECH_LIB_DB: pin not a port of <wrong_cell_type>. Re-search PreEco for cell implementing <gate_function> with correct port. VERIFY via cell_function_matches() before recording.",
  "eco_preeco_study_update": {"action": "fix_cell_type", "gate_instance": "<eco_jira_seq>"}
}
```

### B-ABORT-2 — `ABORT_SVF` (SVF guidance command error)

**Symptoms / Evidence:**
- FM log contains `CMD-005` or `CMD-010` on `guide_eco_change` or other SVF Op
- `<target>__svf_reject_*.rpt.gz` may be empty (SVF wasn't even read)

**Recipe:**
```json
{
  "stage": "ALL",
  "action": "remove_svf_entry",
  "rationale": "CMD-005/010 on guide_eco_change. SVF entry malformed for current netlist. Remove eco_svf_entries.tcl entries that reference this Op.",
  "svf_update_needed": false
}
```

### B-ABORT-3 — `ABORT_NETLIST` (Verilog parse / read error)

**Symptoms / Evidence:**
- FM log: `FM-001`, `FM-599`, or `Duplicate wire/tri/wand/wor declaration`
- `<target>.dat` shows `error` very early (Constraints or PreVerify)
- The error names a specific PostEco file + line

**Diagnosis:**
- Read the named PostEco file at the error line. Common causes:
  - Wire declared twice (e.g., bridge wire decl placed AFTER first usage in instance hookup)
  - Missing semicolon, mismatched braces in port concat
  - Cell instance with port count mismatch

**Recipe:**
```json
{
  "stage": "<stage>",
  "action": "fix_netlist_syntax",
  "file": "data/PostEco/<Stage>.v.gz",
  "error_line": <N>,
  "error_type": "duplicate_wire|missing_semicolon|port_mismatch|...",
  "rationale": "<FM error code>: <quoted error msg>",
  "fix_description": "<exact text edit needed>"
}
```

### B-ABORT-4 — `ABORT_OTHER`

**Symptoms / Evidence:**
- FM log has errors not matching above patterns
- Or `<target>.dat` shows `error` but root cause unclear from log

**Diagnosis:**
- Walk the full log: `zcat <REF_DIR>/logs/<target>.log.gz | grep -E "^Error|FM-|CMD-|FE-" | head -50`
- Check `<target>__runtime.rpt.gz` to identify which phase errored
- Read the surrounding 30 lines of the first `^Error` for context

**Recipe:**
- If still unclear after full log walk: emit verdict-preserving NOOP for this target with `analyzer_blocked: true` and detailed `evidence_summary`. The orchestrator will retry once; second occurrence escalates.

---

## §B-FAIL — Failing-Point Patterns (verdict = `ADVANCE_NEXT_ROUND`)

When FM compared and found unmatched/failing compare points, the analyzer studies the cones, prescribes netlist changes, and advances the round.

Each entry below is indexed by **symptom + cone shape + supporting evidence** so the analyzer can match its hypothesis to the right pattern.

### B-FAIL-A — ECO Change Not Correctly Applied (`Mode A`)

**Symptoms / Evidence:**
- Failing DFF matches an RTL diff `target_register` (Check C of evidence walk)
- OR `eco_applied_round<N>.json` has `status: SKIPPED` or `VERIFY_FAILED` for the relevant change
- OR Check D (polarity) re-derivation from PreEco shows wrong gate function

**Sub-causes (check in order):**
1. **SKIPPED** — status=SKIPPED → find `reason` field, address blocker, re-apply
2. **Missing explicit wire for UNCONNECTED rename** — failing DFF's D-input gate has `unconnected_rewires` in study but PostEco lacks `wire <named_net>;` → `force_wire_decl_reapply`. Critical: NEVER switch to constants or D-pin rewires before confirming the wire exists.
3. **Wrong gate polarity** — gate implements inverse of required logic → `update_gate_function`
4. **Wrong net name on inserted cell** — grep PostEco for the correct net
5. **Port missing or false-APPLIED** — check `eco_rtl_diff.json` flags first:
   - `implicit_wire: true` or `no_wire_decl_needed: true` → internal wire (not a cross-boundary port). Fix: force_reapply existing port_connection entries within that module + add explicit `wire <signal>;` decl. NEVER add as input port to parent.
   - Otherwise → genuine missing port: `force_port_decl` + `force_reapply`

**Recipe (sub-cause #2 — missing wire):**
```json
{
  "stage": "<stage>",
  "action": "force_wire_decl_reapply",
  "signal_name": "<named_net>",
  "module_name": "<module>",
  "rationale": "UNCONNECTED rename target wire missing from PostEco. FM cannot trace across REGCMD hierarchy without explicit wire declaration."
}
```

**Recipe (sub-cause #3 — wrong polarity):**
```json
{
  "stage": "ALL",
  "action": "update_gate_function",
  "gate_instance": "<inst>",
  "wrong_gate_function": "<old>",
  "correct_gate_function": "<derived from PreEco MUX I0/I1>",
  "rationale": "Polarity check: gate implements inverse of required logic. Correct function derived by Step 4c-POLARITY algorithm.",
  "eco_preeco_study_update": {"action": "update_gate_function", "gate_instance": "<inst>", "gate_function": "<correct>"}
}
```

### B-FAIL-B — Regression: New Failing Points Not in RTL Diff (`Mode B`)

**Symptoms / Evidence:**
- Failing DFF does NOT match any RTL diff `target_register`
- D-input cone trace finds the driver in `eco_applied_round<N>.json` (ECO touched it)
- Driver is NOT among the changes RTL diff requested

**Diagnosis:**
1. Trace D-input chain back ≤ 5 hops
2. Find the driver cell, check if it's in eco_applied
3. If driver IS in eco_applied → ECO rewired this cell but it ALSO drives unrelated DFFs → exclude
4. If driver NOT in eco_applied → trace one more level; if still not found → consider Mode E candidate

**Recipe:**
```json
{
  "stage": "<stage>",
  "action": "exclude",
  "cell_name": "<driver_cell>",
  "rationale": "Cell rewired by ECO but it drives DFFs unrelated to the RTL change — collateral damage. Mark excluded so studier skips it next round.",
  "eco_preeco_study_update": {
    "action": "mark_excluded",
    "entry_key": "<cell_name>"
  }
}
```

### B-FAIL-C — Partial Progress (`Mode C`)

**Symptoms / Evidence:**
- Failing DFF matches an RTL `target_register`
- Cell name from `eco_preeco_study.json` (confirmed entry) is absent from `eco_applied_round<N>.json` for that stage

**Diagnosis:**
```python
study_confirmed = {e.cell_name for e in study[stage] if e.confirmed}
applied_cells   = {e.cell_name for e in applied[stage]}
missing = study_confirmed - applied_cells
```

**Recipe:** Add each missing cell as `rewire` or `insert_cell` action.

### B-FAIL-D — FM Stage Mismatch (`Mode D`)

**Symptoms / Evidence:**
- DFF/cell/net name differs between Synth/PrePlace/Route stages
- Failing only in one or two stages, not all
- Cross-stage compare (`xstage_round<N>.json`) shows the failing-stage net name absent

**DFF0X sub-case (ECO-inserted DFF):** check in order:
1. Clock net absent in this stage → `Mode A` (re-apply with correct stage clock)
2. Gate input undriven (submodule bus) → `Mode H` (`fix_named_wire`)
3. SE/SI scan nets differ across stages → `Mode D` (update `port_connections_per_stage`)

**Recipe:**
```json
{
  "stage": "<failing_stage>",
  "action": "rewire",
  "cell_name": "<inst>",
  "pin": "<pin>",
  "old_net": "<wrong_for_this_stage>",
  "new_net": "<correct_for_this_stage_from_grep>",
  "rationale": "Stage <X> uses different net name. Found via grep PostEco/<X>.v.gz."
}
```

### B-FAIL-E — Pre-Existing Failure (`Mode E`) — RARE, requires 5-condition proof

**Symptoms / Evidence (ALL FIVE must hold; one failure → not Mode E):**

> **HARD RULES (Library §D):**
> - ECO-inserted DFFs (`eco_<jira>_*` pattern) are NEVER Mode E
> - `set_user_match` is NEVER for ECO-inserted cells
> - `set_dont_verify` is NEVER a substitute for `fix_named_wire`

**Pre-condition 0**: Failing DFF's D-input gate has `unconnected_rewires` in study → grep PostEco for `wire <named_net>;`. If absent → this is **Mode A sub-cause #2** (missing wire), NOT Mode E. Add `force_wire_decl_reapply`. Do NOT proceed until wire exists and FM still fails.

**Condition -1 (INTENTIONAL_CASCADE check, runs BEFORE Mode E proof):**
For each `and_term` change with `expected_cascade_dffs`, if failing DFF is in the cascade list → classify `INTENTIONAL_CASCADE` with `action: cascade_verified_skip`. STOP.

**Condition 0**: If failing DFF instance matches `eco_<jira>_` → STOP. Cannot be Mode E.

**Condition 1 — No ECO contact:** Trace D-input chain backward max 5 hops. Check every net against `old_net`, `new_net`, `old_token`, `new_token` from rtl_diff. Any match → Mode A or B.

**Condition 2 — Existed in PreEco:** `zcat PreEco/Synthesize.v.gz | grep -c "<failing_dff>"`. Count ≥ 1 → pre-existing; count = 0 → ECO-inserted → re-examine Mode A or H.

**Condition 3 — Not a HFS net rename:** If DFF fails in P&R only, check each D-input gate's input nets:
```bash
synth_count=$(zcat PreEco/Synthesize.v.gz | grep -cw "<input_net>")
pplace_count=$(zcat PreEco/PrePlace.v.gz   | grep -cw "<input_net>")
# synth_count > 0 AND pplace_count = 0 → Mode H (fix_named_wire), NOT Mode E
```

**Condition 4 — Not downstream of an `and_term` target (CRITICAL — prevents misclassifying GAP-15 failures):**
```python
and_term_tokens = [c.old_token for c in rtl_diff.changes if c.change_type == "and_term"]
d_input_cone = trace_D_input_chain(failing_dff, depth=10)
for token in and_term_tokens:
    aliases = find_all_stage_aliases(token, preeco_lines)
    if any(a in d_input_cone for a in aliases):
        classify_as("INCOMPLETE_AND_TERM", old_token=token)  # NOT Mode E
        return
```

**Condition 5 — Cascade count is not suspiciously large:** If `cascade_count >= 100` AND failing DFFs share a common module scope related to an `and_term` change → classify as `INCOMPLETE_AND_TERM`, NOT Mode E.

**Recipe (only after ALL 5 conditions confirmed):**
```json
{
  "stage": "<stage>",
  "action": "try_structural_insertion",
  "cell_name": "<failing_dff>",
  "rationale": "Mode E (5 conditions confirmed): pre-existing structural divergence not caused by ECO. Attempt alternative gate topology that avoids the mismatch.",
  "fallback_action": "conservative_constant"
}
```

### B-FAIL-F — `d_input_decompose_failed` (`Mode F`)

**Sub-modes** based on `fallback_strategy` field in rtl_diff:

**F1 — `intermediate_net_insertion`:**
- If study has `source: "intermediate_net_fallback"` → classify Mode A (re-apply)
- If absent → studier did NOT run Step 0c → `try_alternative_pivot`
- If applied 2+ rounds with same failing count → progressive escalation:
  ```python
  if "invert_cmux_constants" not in strategies_tried:
      action = "invert_cmux_constants"
  elif "try_structural_insertion" not in strategies_tried:
      action = "try_structural_insertion"
  elif "try_strategy_A_andterm" not in strategies_tried:
      action = "try_strategy_A_andterm"
  else:
      action = "try_alternative_pivot"
  ```
- Also: if `pivot_driver_cell_type` is INVERTING (NOR/NAND/INV) and constants not flipped → `invert_cmux_constants` first

**F2 — `null` fallback_strategy:**
- `try_alternative_decomposition` — re-study with different gate approach (compound gates from PreEco, different pivot, or `conservative_constant` for unresolvable inputs)

**F3 — Pre-existing DFF failing due to wrong ECO gate chain:**
Trigger: pre-existing DFF (NOT eco_<jira>_) fails AND cascade_count ≥ 100 AND Condition 4 of Mode E confirmed no `and_term` connection.
```python
if not is_eco_inserted(failing_dff) and cascade_count > 100:
    if and_term_downstream_check(failing_dff, rtl_diff): classify("INCOMPLETE_AND_TERM"); return
    eco_gates_in_cone = trace_D_input_chain_for_eco_gates(failing_dff, study)
    if eco_gates_in_cone: classify_as_mode_A_with_eco_chain_diagnosis(eco_gates_in_cone)
```
Run Check D (polarity) on ALL c_mux gates in chain. Try progressive fixes (update_gate_function → invert_cmux_constants → try_structural_insertion → try_alternative_pivot).

### B-FAIL-G — Structural Stage Mismatch (`Mode G`)

**Symptoms / Evidence:**
- `FmEqvEcoRouteVsEcoPrePlace` PASS, `FmEqvEcoPrePlaceVsEcoSynthesize` FAIL ≥ 10
- No failing DFF matches target_register

**Recipe:**
- First try `fix_named_wire` using `cell_name_per_stage`
- If P&R cell found → use it
- If absent → `move_gate_to_submodule`
- If still stuck → `conservative_constant` (1'b0) as last resort
- `set_dont_verify` only after Priority 3 structural trace confirms no fixable net exists

### B-FAIL-H — Gate Input Driven Only Through Hierarchical Submodule Output Port Bus (`Mode H`)

**Symptoms / Evidence:**
- ECO-inserted DFF is `DFF0X` in P&R stages (passes Synthesize)
- Check E (D-input chain walk) confirms gate input net has no direct primitive driver — only through a submodule's output port bus
- `xstage_round<N>.json` shows the net exists in Synthesize PostEco but not in PrePlace/Route (cone-walk depth)

**Diagnosis:** FM black-boxes the submodule in P&R → net appears undriven → DFF0X.

**Persistent DFF0X check — MANDATORY before prescribing `fix_named_wire`:**
```python
prev_analysis = load(f"data/{TAG}_eco_fm_analysis_round{ROUND-1}.json") if ROUND > 1 else None
rename_already_tried = (prev_analysis and any(
    c.action == "fix_named_wire" and c.rename_wire and c.gate_instance == gate_instance
    for c in prev_analysis.revised_changes))
```

**If `rename_already_tried` AND DFF0X still fails:**
The submodule is BLACK-BOXED — wire renaming cannot fix submodule boundary. Prescribe `move_gate_to_submodule`:
```json
{
  "action": "move_gate_to_submodule",
  "gate_instance": "<eco_jira_seq>",
  "preferred_insertion_scope": "<child_inst>",
  "submodule_type": "<child_module_type>",
  "rationale": "rename_wire applied in Round <N-1> but DFF0X persists. Child module black-boxed by FM in P&R. Move gate chain inside <child_inst>; gate output becomes new output port; DFF at parent uses this port as D-input."
}
```

**If first time:** prescribe `fix_named_wire` with `rename_wire: true`:
```json
{
  "stage": "<failing_stage>",
  "action": "fix_named_wire",
  "rename_wire": true,
  "gate_instance": "<eco_jira_seq>",
  "input_pin": "<A1|A2|I|...>",
  "source_net": "<original_net>",
  "rationale": "Net driven through submodule output port bus — no direct primitive driver in <stage>. Rename to ECO wire; keep port bus connection.",
  "eco_preeco_study_update": {
    "action": "set_needs_named_wire",
    "rename_wire": true,
    "gate_instance": "<eco_jira_seq>",
    "input_pin": "<pin>",
    "source_net": "<original_net>"
  }
}
```

### B-FAIL-I — Child Output Port Internally Undriven (`Mode I`)

**Symptoms / Evidence:**
- Failing DFF is DFF0X
- `analyze_points` reports `<port>[<bit>]` undriven on REF + propagates X on IMPL
- `<port>` is the parent UNCONNECTED rename target (Mode I two-level rename)

**Diagnosis:** Parent renamed `UNCONNECTED_N → n_eco_*` at child instance bus bit, but the child's OWN output port bit is also undriven internally. Scan child module body for sub-instance bus where bit `[bit]` is also `UNCONNECTED_M`.

**Recipe:** Emit second `port_connection` entry:
```json
{
  "stage": "ALL",
  "action": "add_port_connection",
  "module_name": "<child>",
  "bus_bit_index": <bit>,
  "net_name": "<port>[<bit>]",
  "rationale": "Mode I: two-level UNCONNECTED rename. Parent port bit driven, but child's internal sub-instance bus bit also UNCONNECTED. Connect child internal slot to <port>[<bit>]."
}
```

### B-FAIL-J — Chain-Leaf Inverter-Parity Flip (`Mode J`)

**Bug class:** A `new_logic_gate` input pin reads a wire whose **name is identical across all stages** but whose **logical value is inverted in one stage** because P&R inserted an odd number of inverters (drive-strength buffers) between the registered driver and the port wire. Same name, opposite value → FM cone divergence in that stage only.

**Symptoms / Evidence:**
- 1 failing point on a `target_register` (RTL-changed DFF) — typically Route-only or PrePlace+Route
- Cone trace shows the failing DFF's D-input feeds through one of the ECO's new gates
- That gate's input pin reads a bare RTL signal name (e.g. `ArbCtrlPeRdy`, NOT `n_eco_*` or `aps_rename_*`)
- Synth and earlier stages PASS — only the post-physical stage(s) fail
- Mode A `update_gate_function` does NOT converge (gate function is actually correct — the wire is wrong)

**Diagnosis (walk per stage, count inverters):**
For each `new_logic_gate` input pin's net, walk upstream in each PreEco netlist until hitting a DFF output or primary input, counting INV cells (INVD/INVSKR/INVLLKG/INVTX/INVFE/...):

| Stage | Bare wire | INV count | Logical value |
|---|---|---:|---|
| Synthesize | `<wire>` | 0 (even) | `Q` ✓ |
| PrePlace   | `<wire>` | 0 (even) | `Q` ✓ |
| Route      | `<wire>` | **1 or 3 (odd)** | **`~Q`** ❌ |

If parity differs across stages → **Mode J confirmed**. Don't change gate function — change the input wire.

**Cause:** Multi-bit (MB) DFF merge in physical stages packs registers into one cell with `Q1/Q2/Q3/Q4` outputs. Long routes to those outputs get 1-3 drive-strength inverter buffers (e.g. `FxPlace_ZINV_*`, `INVSKR*`). Bare RTL name resolves to the post-buffer net → odd INV count → flipped polarity.

**Recipe:**
```json
{
  "stage": "<failing_stage>",
  "action": "rewire_gate_input",
  "gate_instance": "<eco_new_gate_inst>",
  "pin": "<input_pin>",
  "old_net": "<bare_rtl_name>",
  "new_net": "<polarity_correct_wire>",
  "rationale": "Mode J: bare-name wire <wire> has odd INV-parity (count=<N>) in <stage> vs even parity (0) in Synth/PrePlace → carries ~Q. Replaced with <new_net> (DFF Q output direct, parity=0).",
  "eco_preeco_study_update": {
    "action": "update_port_connection_per_stage",
    "gate_instance": "<inst>",
    "pin": "<pin>",
    "stage": "<failing_stage>",
    "net_name": "<new_net>"
  }
}
```

**Polarity-correct wire selection (in priority order):**
1. **MB DFF Q-pin direct** — walk to the merged cell, decode instance name to find the bit position of the target register, take that Q-pin's net (`aps_rename_*` style). **Permanent** — survives next P&R.
2. **`actual_wire_<stage>` from rename map** — if `eco_fenets_rename_map.json` has `actual_wire_<stage>` field for this signal, use that. **FM-resolved**, polarity-preserving.
3. **NEVER** tap mid-chain (`FxPlace_ZINV_*`, `FxPrePlace_*` intermediate nets) — these are placer scaffolding, renamed/removed by next P&R iteration.

**Worked-example shape (synthetic):**
- Gate: `<new_logic_gate>.<input_pin>` driven by bare RTL net `<sig>`
- Per-stage walk of `<sig>`:
  - Synth: drives directly from `<sig>_reg.Q` → parity 0
  - PrePlace: through 0 or even-count INVs (e.g. via scan-rename intermediate) → parity 0
  - Route: through odd-count INV buffer chain (e.g. `INVD* → INVD* → INVSKR* → <mb_q_net>`) → **parity 1**
- MB cell instance name lists merged registers in pin order (`<reg1>_MB_<reg2>_MB_...`) — decode position of target register to identify its Q-pin net
- Fix: rewire failing stage only `.<pin>(<bare_sig>) → .<pin>(<mb_q_net>)` → FM PASS

**Cross-reference:** This mode is caught at study-time by `eco_validate_step3.py` Check 38 (HIGH/38-CHAIN-LEAF-POLARITY-MISMATCH). If Check 38 fires on a study patch during ROUND, do NOT proceed to applier — Mode J needs a wire change, not a re-apply.

### B-FAIL-S — Scan-Stitching Incomplete on New ECO DFF (`Mode S`) — PrePlace/Route only

**Mode S decision tree (BEFORE classifying Mode I or anything else):**

For every failing `new_logic_dff` compare point in PrePlace/Route, `zcat` the failing-stage netlist and grep the DFF instantiation. Inspect literal `.SE(...)` / `.SI(...)` net names:

1. **Both `1'b0`** → Mode S NOT applied at all → `failure_mode: "S"`
2. **Either pin shows an auto-generated P&R net** (typical prefixes: `FxPrePlace_`, `FxPlace_`, `FxCts_`, `FxOptCts_`, `dftopt`, `test_so`, `HFSNET_`, plus tile-specific equivalents) **that is NOT** the declared bridge port (`ECO_<jira>_SE_in` / `ECO_<jira>_SI_in`, or the `<inst>_SE_in/SI_in` variant) → Mode S applied but DFF rewired to a neighbor-DFF net instead of the bridge port → `failure_mode: "S"`. **Do NOT classify as Mode I / UNDRIVEN here** — the SE/SI fanin will look "globally unmatched" because the bridge wire is the wrong endpoint.
3. **Both pins are exactly the declared bridge ports** AND host module declares those ports AND parent module port_connection wires them up to a real scan chain → only THEN consider Mode I / D-input X / other classifications.

**Recipe:**
```json
{
  "stage": "<failing_stage>",
  "action": "fix_scan_stitching",
  "cell_name": "<inst>",
  "mode_S_hint": "<inst> bridge wire <name> in <stage> not driven by existing scan chain. Re-trace parent-scope scan net via ECO_*_SI_in/SE_in ports up to <scope> and patch port_connections_per_stage.",
  "eco_preeco_study_update": {"action": "rebuild_per_stage_stitching"}
}
```

### B-FAIL-T — Compound-Cell Truth-Table Mismatch (`Mode T`)

**Symptoms / Evidence:**
- `new_logic_gate` entries whose `cell_type` matches `^(I?AOI|I?OAI|I?AO|I?OA)\d+`
- Output net (Z/ZN/ZN1) appears in `__failing_points.rpt`
- Mode A polarity check (Check D) passes (gate function direction is correct)

**Diagnosis steps:**
1. Build `f_expected` from RTL diff `d_input_gate_chain`
2. Look up library truth table for `wrong_cell_type` (AOI21, OAI21, AO21, OA21, with/without I prefix)
3. Enumerate same-family alternatives + input permutations
4. Pick cell whose truth table = `f_expected` under any permutation of existing port_connections

**Recipe:**
```json
{
  "stage": "ALL",
  "action": "swap_compound_cell",
  "instance_name": "<inst>",
  "wrong_cell_type": "<old>",
  "correct_cell_type": "<new>",
  "port_remap": {"A1": "B", "A2": "A1", "B": "A2"},
  "rationale": "RTL expects f=<f_expected>; <wrong> computes <f_wrong>; <correct> with port_remap computes <f_expected>",
  "eco_preeco_study_update": {"action": "swap_compound_cell", "gate_instance": "<inst>", "new_cell_type": "<correct>", "port_remap": {...}}
}
```

**When to give up:** If no same-family alternative + permutation matches, escalate to Mode F2 (`try_structural_decomposition` — studier rebuilds chain with simpler 2/3-input primitives).

### B-FAIL-INTENTIONAL_CASCADE — ECO Correctly Changed This DFF

**Symptoms / Evidence:**
- Failing DFF appears in `expected_cascade_dffs` list of an `and_term` change with `and_term_strategy: "module_port_direct_gating"`
- DFF is downstream of the gated port — its behavior change is a CORRECT ECO consequence, not a bug

**Recipe:**
```json
{
  "stage": "<stage>",
  "action": "cascade_verified_skip",
  "cell_name": "<failing_dff>",
  "rationale": "INTENTIONAL_CASCADE — DFF downstream of <cascade_net>. ECO intentionally changed gating; this DFF's behavior change is correct. Skip in FM scope (engineer applies set_dont_verify; flow uses cascade_verified_skip)."
}
```

### B-FAIL-INCOMPLETE_AND_TERM — `and_term` Did Not Drive Port Directly (GAP-15)

**Symptoms / Evidence:**
- Pre-existing DFF (NOT eco_<jira>_) fails
- D-input cone traces back to an `and_term` target signal (`old_token`)
- Cascade count ≥ 50

**HARD RULE: Do NOT classify as Mode E.** The cascade pattern is the signature of incomplete and_term application, not pre-existing P&R divergence.

**Diagnosis:**
```python
for change in rtl_diff.changes:
    if change.change_type != "and_term": continue
    eco_gate = find_eco_gate_for_and_term(change.old_token, study)
    if eco_gate and eco_gate.output_net != change.old_token:
        # GAP-15 not applied — drives intermediate net, not the port
        return INCOMPLETE_AND_TERM
```

**Recipe:**
```json
{
  "stage": "ALL",
  "action": "re_study_and_term",
  "old_token": "<old_token>",
  "required_strategy": "module_port_direct_gating",
  "current_incorrect_output": "<n_eco_jira_seq>",
  "required_output": "<old_token>",
  "rationale": "and_term gate drives intermediate net instead of module port directly. All <N> consumer DFFs still see ungated value. Fix: gate output = old_token. Rename original driver to old_token_orig.",
  "eco_preeco_study_update": {
    "action": "update_and_term_strategy",
    "old_token": "<old_token>",
    "new_output_net": "<old_token>",
    "rename_original_driver_output_to": "eco_<jira>_<old_token>_orig",
    "remove_individual_rewire_entries": true
  }
}
```

### B-FAIL-WRONG_GATE_STRUCTURE — MUX2 Cascade Creates FM-Unverifiable Structure

**Symptoms / Evidence:**
- `FmEqvEcoPrePlaceVsEcoSynthesize` fails with > 50 points
- `d_input_decompose_failed: true` in rtl_diff for a `new_logic` change
- Failing DFFs in same module scope as `intermediate_net_insertion` change
- At least one MUX2 gate appears in study for this change (`fn=MUX2`)

**HARD RULE: Do NOT classify as Mode E.** Engineer's pure netlist solution (no SVF) proves these are fixable — the issue is the MUX2 cascade structure.

**Recipe:**
```json
{
  "stage": "ALL",
  "action": "try_structural_insertion",
  "rationale": "MUX2 cascade produces structural non-equivalence in PPvsSynth. Re-study with Strategy A: search PreEco for compound gate in priority chain whose input can accept the new condition.",
  "eco_preeco_study_update": {
    "action": "re_study_intermediate_net_insertion",
    "preferred_strategy": "structural_insertion",
    "preferred_gate_discovery": "Search PreEco for compound gates (3+ inputs, AND-OR or OR-AND boolean) in priority chain cone.",
    "forbidden_gate_types": ["MUX2"],
    "note": "Feed new conditions into existing compound gate in priority chain rather than building parallel MUX cascade."
  }
}
```

### B-FAIL-CTS_CLOCK_RENAMED — ECO DFF Clock Pin Renamed by CTS in Route

**Symptoms / Evidence:**
- ECO-inserted DFF fails ONLY in `FmEqvEcoRouteVsEcoPrePlace`
- `cts_clock_renamed: true` in study JSON
- DFF type is DFF (not DFF0X)

**Recipe:**
```json
{
  "stage": "Route",
  "action": "rewire_cp",
  "instance": "<eco_dff>",
  "pin": "CP",
  "old_net": "<preplace_cp_net>",
  "new_net": "<cts_clock_net_from_neighbour_dff>",
  "rationale": "CTS renamed clock net in Route. Find new_net by reading CP pin of neighbour DFF in same module + clock domain in Route PostEco."
}
```

### B-FAIL-CTS_BBNET_INPUT — ECO Gate Input via CTS Multi-Driver Merged Cell

**Symptoms / Evidence:**
- ECO-inserted DFF is DFF0X in Route only (passes Synth + PP)
- Gate feeding D-input uses a net whose Route driver is a CTS-created merged cell (absent from PreEco)

**Recipe:**
```json
{
  "stage": "Route",
  "action": "rewire_gate_input",
  "gate_instance": "<eco_gate>",
  "pin": "<input_pin>",
  "old_net": "<cts_merged_cell_driven_net>",
  "new_net": "<primary_input_port_for_same_signal>",
  "rationale": "CTS merged cell black-boxed by FM. Use primary input port (single driver, FM-traceable) instead. Find via grep input declaration in Route module header."
}
```

### B-FAIL-SCAN_CHAIN_MISMATCH — Newly Inserted DFF Globally Unmatched SE/SI Cone (GAP-20)

**Symptoms / Evidence:**
- Newly inserted DFF fails with globally unmatched SE/SI cone nets
- SE net differs between PrePlace and Route (both are P&R HFS aliases)
- Or study JSON has `needs_se_tune: true`

**Recipe:**
```json
{
  "stage": "<failing_stage>",
  "action": "scan_chain_tune",
  "tune_command": "set_constant -type port {<DFF_full_path>/SE} 0",
  "apply_to": ["ref", "impl"],
  "rationale": "GAP-20: SE cone unmatched, no structural fix exists across stages. Auto-apply set_constant via tune file."
}
```
Do NOT escalate to engineer. Do NOT set `manual_only`.

---

## §C — Fix Action Reference

Every action verb the analyzer can prescribe + when to use it.

| Action | Semantics | When to use |
|--------|-----------|-------------|
| `rewire` | Net substitution on existing cell pin | Mode A (wrong net), Mode D (stage-specific net) |
| `insert_cell` | Insert new buffer/inverter | Polarity fix when re-inserting wrong-polarity gate |
| `new_logic_dff` | Insert new flip-flop | Check C identifies missing confirmed DFF in study |
| `new_logic_gate` | Insert new combinational gate | Same; include `gate_function` |
| `revert_and_rewire` | Previous rewire was wrong; apply corrected | Mode A round 2+ |
| `exclude` | Do NOT touch this cell again | Mode B (collateral damage) |
| `force_port_decl` | Force re-apply of port declaration | ABORT_LINK Sub-A; false-APPLIED port_decl |
| `fix_named_wire` | Gate input via hierarchical port bus | Mode H first occurrence |
| `move_gate_to_submodule` | Relocate gate inside child submodule | Mode H persistent (rename_wire already tried) |
| `add_port_connection` | Wire child internal slot to bus bit | Mode I (two-level UNCONNECTED) |
| `update_gate_function` | Change gate cell type to correct polarity | Check D found wrong polarity |
| `swap_compound_cell` | Swap to truth-table-matching cell + port_remap | Mode T (compound 4-input mismatch) |
| `force_wire_decl_reapply` | Add explicit `wire X;` decl | Mode A sub-cause #2 (UNCONNECTED rename missing wire) |
| `rerun_fenets` | Check F first occurrence | Condition input never FM-queried |
| `structural_trace` | Check F after first FM-036 | Search P&R netlist for driver cell anchor |
| `scan_chain_tune` | Auto-apply tune file `set_constant` | SCAN_CHAIN_MISMATCH (GAP-20) |
| `fix_cell_type` | Re-search PreEco for correct cell+pins | ABORT_LINK Sub-B (cell_type/port mismatch) |
| `fix_netlist_syntax` | Direct text edit of PostEco file | ABORT_NETLIST |
| `remove_svf_entry` | Remove SVF entry causing CMD-005/010 | ABORT_SVF |
| `cascade_verified_skip` | Mark DFF as INTENTIONAL_CASCADE | Mode INTENTIONAL_CASCADE |
| `re_study_and_term` | Re-study `and_term` with module_port_direct_gating | Mode INCOMPLETE_AND_TERM |
| `try_structural_insertion` | Re-study with Strategy A (compound gate from PreEco) | Mode F1 escalation; WRONG_GATE_STRUCTURE |
| `try_alternative_decomposition` | Re-study with different gate approach | Mode F2 |
| `try_alternative_pivot` | Find different pivot net (max 3 hops deeper) | Mode F1 final escalation |
| `invert_cmux_constants` | Flip 1'b0/1'b1 constants in c_mux chain | Mode F1, INVERTING pivot driver |
| `try_strategy_A_andterm` | Abandon intermediate_net_insertion, try Strategy B | Mode F1 mid escalation |
| `conservative_constant` | Tie to 1'b0 as last resort | After all structural fixes exhausted |
| `tune_file_update` | Add tune file directive (last resort, netlist exhausted) | Persistent same-failure-pattern across rounds |
| `rewire_cp` | Rewire ECO DFF clock pin | CTS_CLOCK_RENAMED |
| `rewire_gate_input` | Rewire gate input to bypass CTS merged cell | CTS_BBNET_INPUT |
| `fix_scan_stitching` | Re-emit per-stage stitching chain | Mode S |

---

## §D — Hard Rules (Immutable Constraints)

These rules override all other guidance. Violating any one corrupts the flow.

### Loop Control
1. **ABORT verdict MUST set `loop_verdict: "RERUN_SAME_ROUND"`.** Round counter never increments on abort.
2. **ABORT analysis MUST NOT prescribe `re_study` or `eco_netlist_port_rewire` re-run.** Only netlist patches that fix the elaboration error.
3. **Maximum 3 RERUN_SAME_ROUND emissions per round.** On the 4th attempt, force `ADVANCE_NEXT_ROUND` with synthetic failure mode `abort_unrecoverable`.
4. **CONVERGED only when all 3 targets PASS with 0 failing points.** Any failing point or unmatched target → not converged.

### ECO-Inserted DFFs
5. **ECO-inserted DFFs (`eco_<jira>_*` pattern) are NEVER Mode E.** Re-examine as Mode A, H, or D.
6. **`set_user_match` is NEVER for ECO-inserted cells.** Equivalence failure on ECO DFF is Mode A/H/D, not unmatched point.
7. **`set_dont_verify` is NEVER a substitute for `fix_named_wire`.** When ECO DFF fails P&R only due to HFS-renamed nets, the correct action is `fix_named_wire` (Mode H).

### SVF
8. **NEVER modify `EcoChange.svf` or any SVF file.** SVF is engineer-only.
9. **`set_dont_verify` is only valid for proven Mode E** (5 conditions confirmed) **or Mode G-P&R** (Priority 3 structural trace confirmed signal truly absent in stage, ECO architecturally correct, failure is stage-to-stage only).

### Progressive Action Doctrine
10. **`manual_only` is ABOLISHED.** Always emit progressive action; flow runs all 10 rounds.
11. Replace `manual_only` instinct with the documented progressive fallback per Mode (see §B entries).

### Study JSON Updates
12. **`eco_preeco_study_update` is mandatory for Modes B, D, A, ABORT_LINK, ABORT_CELL_TYPE.** Without it, next round re-applies the same wrong change.

### Netlist Investigation Discipline
13. **Stage-specific analysis** — grep the CORRECT stage's PostEco/PreEco netlist, never default to Synthesize.
14. **Pre-existing requires 5-condition cone trace proof** (see B-FAIL-E). Trace ≥ 5 hops and confirm no ECO net contact before classifying Mode E.
15. **NEVER return `failure_mode: UNKNOWN`** without completing Phase 2 xstage compare.
16. **Honest output over forced output** — if root cause cannot be determined after all phases, describe every check done. Do NOT invent a fix.

### Priority Order — Netlist First
17. **Netlist fix first.** Find wrong/missing connection and correct it (`fix_named_wire`, `rewire`, `re-insert`).
18. **Tune file as last resort.** If same FM failure pattern persists across multiple rounds AND netlist analysis confirms it is structural (not a logical netlist error) → `tune_file_update`. Do NOT specify exact TCL commands; let next round's analyzer read FM log + add appropriate directive.
19. **HFS net rename is a NETLIST fix.** When ECO gate uses a net that P&R renames (HFS distribution), fix with `fix_named_wire` — do NOT suppress with `set_dont_verify`.

---

## §E — Cross-Stage Netlist Compare Recipes

Concrete recipes for the operations Phase 2 (`xstage_compare.py`) performs. Listed here so the agent can also run any of them ad-hoc when needed.

### Pin connection per stage
```bash
for stage in Synthesize PrePlace Route; do
    echo "=== $stage ==="
    zcat <REF_DIR>/data/PostEco/$stage.v.gz | grep -A 2 "<inst_name> (" | head -5
done
```

### Wire decl per stage (port vs local)
```bash
for stage in Synthesize PrePlace Route; do
    echo "=== $stage ==="
    zcat <REF_DIR>/data/PostEco/$stage.v.gz | grep -nE "^(input|output|wire)  *<signal_name> " | head -3
done
```

### Cell instance presence per stage (CTS optimization check)
```bash
for stage in Synthesize PrePlace Route; do
    n=$(zcat <REF_DIR>/data/PostEco/$stage.v.gz | grep -cE "<cell_inst_name> \(")
    echo "$stage: $n instances"
done
```

### Driver chain back-walk (5 hops)
```python
def trace_driver(net, stage_lines, depth=5):
    chain = [net]
    current = net
    for _ in range(depth):
        # Find line where .Z(current) or .ZN(current) appears
        for line in stage_lines:
            m = re.search(r'\.\s*(Z|ZN|ZN1|Q|Q1)\s*\(\s*' + re.escape(current) + r'\s*\)', line)
            if m:
                # Extract input nets from the same instance (lines may span)
                inst_inputs = parse_inst_inputs(line, stage_lines)
                if inst_inputs:
                    chain.append((find_inst_name(line), inst_inputs))
                    current = inst_inputs[0]  # walk first input
                    break
        else:
            break
    return chain
```

### Parent module instance hookup delta
```bash
# For each stage, find the parent's CTRLSW (or other) instance and extract port hookups
for stage in Synthesize PrePlace Route; do
    echo "=== $stage ==="
    zcat <REF_DIR>/data/PostEco/$stage.v.gz | awk '/<inst_name> \(/,/\) ;$/' | grep -E "\.<port_name>\s*\("
done
```

### Identify CTS-renamed wires (present in Synth, missing from P&R)
```bash
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<wire>")
pp_count=$(zcat <REF_DIR>/data/PreEco/PrePlace.v.gz   | grep -cw "<wire>")
rt_count=$(zcat <REF_DIR>/data/PreEco/Route.v.gz      | grep -cw "<wire>")
echo "<wire>  Synth=$synth_count  PP=$pp_count  Route=$rt_count"
# synth_count > 0 AND pp_count = 0 → Mode H candidate
```

### Identify cells black-boxed by FM in P&R only
```bash
# A cell is "black-boxed" by FM if it appears in PreEco Synth but not in PreEco P&R
for cell in $(suspect_cells); do
    s=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -c "$cell ")
    p=$(zcat <REF_DIR>/data/PreEco/PrePlace.v.gz   | grep -c "$cell ")
    [ "$s" -gt 0 -a "$p" -eq 0 ] && echo "BLACK-BOXED in P&R: $cell"
done
```

---

## §F — Output JSON Schema

Path: `<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json`

> **MANDATORY:** Every `revised_changes[i]` (except `cascade_verified_skip` and `manual_only`) MUST carry an `evidence_for_studier` block per **`config/eco_agents/eco_re_studier_evidence_contract.md`**. The block is the structured handoff to `eco_netlist_re_studier` — without it, the studier cannot apply the recipe. Validator `script/eco_scripts/eco_validate_analyzer_evidence_contract.py` enforces compliance as a pre-FM gate.

```json
{
  "round": <ROUND>,
  "loop_verdict": "RERUN_SAME_ROUND" | "ADVANCE_NEXT_ROUND" | "CONVERGED",
  "verdict_reason": "<one-line reason>",
  "next_round": <ROUND or ROUND+1>,

  "evidence_summary": {
    "evidence_walk_json": "data/<TAG>_eco_fm_evidence_round<N>.json",
    "xstage_compare_json": "data/<TAG>_eco_fm_xstage_round<N>.json"
  },

  "failure_mode": "ABORT_SVF|ABORT_LINK|ABORT_LINK_CELL|ABORT_NETLIST|ABORT_OTHER|A|B|C|D|E|F|G|H|I|S|T|INTENTIONAL_CASCADE|INCOMPLETE_AND_TERM|WRONG_GATE_STRUCTURE|CTS_CLOCK_RENAMED|CTS_BBNET_INPUT|SCAN_CHAIN_MISMATCH|UNKNOWN",

  "diagnosis": "<specific: which DFF, port, net, which evidence found it>",

  "failing_points_count": {
    "FmEqvEcoSynthesizeVsSynRtl":   <N or "ABORT" or "PASS">,
    "FmEqvEcoPrePlaceVsEcoSynthesize": <N or "ABORT" or "PASS">,
    "FmEqvEcoRouteVsEcoPrePlace":   <N or "ABORT" or "PASS">
  },

  "wrong_cells": ["<cell_if_mode_B>"],
  "needs_re_study": false,
  "re_study_targets": [],
  "needs_rerun_fenets": false,
  "rerun_fenets_signals": [],

  "root_cause_reasoning": "<natural-language explanation tied to specific evidence in evidence_walk + xstage JSONs>",

  "alternatives_considered": [
    {"hypothesis": "<alt>", "rejected_because": "<evidence contradicting>"}
  ],

  "revised_changes": [
    {
      "stage": "Synthesize|PrePlace|Route|ALL",
      "action": "<one of §C action verbs>",
      "cell_name": "<...>",
      "pin": "<...>",
      "old_net": "<...>",
      "new_net": "<...>",
      "signal_name": "<...>",
      "module_name": "<...>",
      "declaration_type": "input|output",
      "rationale": "<which DFF/port/net, why this fix, what evidence supports it>",
      "fallback_action": "<progressive fallback>",
      "eco_preeco_study_update": {
        "action": "<from §C>",
        "entry_key": "<cell_name_or_change_type>",
        "field": "<field_to_update>",
        "value": "<new_value>"
      },
      "evidence_for_studier": {           // MANDATORY — see eco_re_studier_evidence_contract.md
        "failing_pin": "D|CP|SE|SI|N/A",
        "failing_pin_load_bearing": true|false,
        "load_bearing_reason": "shadowed_by_set_constant|none|...",
        "first_divergent_point": {
          "kind": "undriven_cut|cts_rename|blackbox|wrong_gate|missing_port|bridge_gap|se_not_consolidated|wrong_polarity|other",
          "what": "<specific net/cell/wire>",
          "exists_in_ref_cone": true|false,
          "exists_in_impl_cone": true|false,
          "evidence_path_refs": ["evidence.<jsonpath>", "xstage.<jsonpath>"]
        },
        "candidate_fix_recipes": [        // at least 1; analyzer's pre-vetted shortlist
          {
            "kind": "<action-specific recipe id>",
            "applicability_score": 0.0-1.0,
            "applicable_only_if": "<optional precondition expression>",
            "required_inputs_for_studier": { /* per-action shape; see contract §2 */ },
            "verification_after_fix": "<grep/check command for studier to validate after applying>"
          }
        ],
        "constraints": {
          "scope_module": "<single module where edits must stay>",
          "do_not_modify_modules": ["<other modules to leave alone>"],
          "do_not_touch_signals": ["<signals to preserve>"]
        },
        "previous_round_attempts": [
          {"round": ROUND-1, "action": "...", "result": "..."}
        ]
      }
    }
  ],

  "svf_update_needed": false,
  "svf_commands": [],

  "tune_file_updates": [
    {"target": "<FmTarget>", "directive_summary": "<what was added>", "reason": "<FM log evidence>"}
  ],

  "rerun_count_in_round": <N>,
  "max_rerun_in_round_reached": false
}
```

**Schema notes:**
- `loop_verdict` is the most important field — drives ROUND_ORCHESTRATOR
- `evidence_summary` links to the helper-script outputs so future rounds can audit
- `root_cause_reasoning` and `alternatives_considered` enforce honest investigative reasoning
- `revised_changes[].fallback_action` enforces progressive doctrine
- `rerun_count_in_round` enforces hard rule #3 (max 3 reruns per round)
