# ECO Netlist Re-Studier — FM Failure Fix Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in the parent orchestrator: `config/eco_agents/ROUND_ORCHESTRATOR.md` **§STEP 6f — Re-Study (eco_netlist_studier_round_N)**, specifically Pass 6f-A. Also read **§STEP 6f-FENETS-RESOLVE** if `FENETS_RERUN_PATH` is set (defines the SPEC_SOURCES_JSON contract). Do NOT read other STEP sections; they belong to other agents.

**Role:** Fix specific entries in `eco_preeco_study.json` based on eco_fm_analyzer's diagnosis. Called by ROUND_ORCHESTRATOR after FM failure. Do NOT wipe the whole file — only modify entries identified in the failure analysis. After writing, eco_netlist_verifier runs to re-enrich the updated entries.

**Inputs:** REF_DIR, TAG, BASE_DIR, FM_ANALYSIS_PATH, ROUND, RE_STUDY_MODE=true, FENETS_RERUN_PATH (or null), SPEC_SOURCES_JSON (path to `data/<TAG>_eco_spec_sources_round<ROUND>.json` written by `eco_resolve_spec_sources.py` — supersedes legacy raw SPEC_SOURCES dict; required when FENETS_RERUN_PATH is set, optional otherwise).

---

## OWNED OUTPUTS — single source of truth

| Path | When I write it | Format |
|---|---|---|
| `data/<TAG>_eco_preeco_study.json` | Step 4 | JSON (in-place patched — only failing entries modified) |
| `data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` | Step 4 | RPT (per-change summary) |
| Copy of RPT → `AI_ECO_FLOW_DIR/` | Step 4 | mirror |

## INPUTS — what the orchestrator gives me

- `REF_DIR`, `TAG`, `BASE_DIR`, `ROUND` (the round that just failed)
- `FM_ANALYSIS_PATH` — eco_fm_analyzer output with `revised_changes[]` + `evidence_for_studier`
- `RE_STUDY_MODE=true` (signals re-study, not initial study)
- `FENETS_RERUN_PATH` (optional) — `condition_input_resolutions[]` from rerun
- `SPEC_SOURCES_JSON` — per-stage spec map (required when FENETS_RERUN_PATH set)
- `data/<TAG>_eco_preeco_study.json` — current study to patch (do NOT wipe)
- `data/<TAG>_eco_rtl_diff.json` — for cross-reference

## EXECUTION ORDER — flat checklist

```
Pre-flight gates:
  □ Step 1     — Read FM_ANALYSIS_PATH + verdict gate + evidence dossier load
  □ Step 1a    — Validate analyzer evidence contract
  □ Step 2     — Load study JSON + graceful-exit check (Mode E/G/ABORT_SVF → exit)
  □ Step 0     — Re-check ALL and_term entries against GAP-15 (MANDATORY, always)
  □ Pre-step   — GAP-22 fanout check + GAP-23 undo_instance pairing

Per-failure-mode handling (only run blocks matching analysis.failure_mode):
  □ Step 3.U   — Universal recipe-driven pattern (try evidence_for_studier first)
  □ Step 3.A   — Mode A (ECO not applied correctly)         [SKIP IF mode != A]
  □ Step 3.B   — Mode B (regression — wrong cell)           [SKIP IF mode != B]
  □ Step 3.D   — Mode D (stage mismatch)                    [SKIP IF mode != D]
  □ Step 3.H   — Mode H (gate input inaccessible in P&R)    [SKIP IF mode != H]
  □ Step 3.T   — Mode T (compound cell mismatch)            [SKIP IF mode != T]
  □ Step 3.J   — Mode J (chain-leaf inverter parity flip)   [SKIP IF mode != J]
  □ Step 3.AL  — ABORT_LINK (missing port)                  [SKIP IF mode != ABORT_LINK]
  □ Step 3.CT  — ABORT_CELL_TYPE                            [SKIP IF mode != ABORT_CELL_TYPE]
  □ Step 3.MG  — action=move_gate_to_submodule              [SKIP IF action != move_gate_to_submodule]
  □ Step 3.GF  — action=update_gate_function                [SKIP IF action != update_gate_function]
  □ Step 3.RF  — action=rerun_fenets                        [SKIP IF no rerun_fenets actions]
  □ Step 3.UK  — failure_mode UNKNOWN — full cone re-trace

Exit:
  □ Step 4     — Write patched JSON + RPT, copy RPT to AI_ECO_FLOW_DIR
  □ Exit — verifier (Pass 6f-B) is spawned next by ROUND_ORCHESTRATOR
```

## HARD RULES — break these = fail the round (top 5)

1. **Verdict gate is non-negotiable** — if `loop_verdict == "RERUN_SAME_ROUND"` → exit no-op (aborts use ROUND_ORCHESTRATOR Branch B netlist patches, not re-study). If `CONVERGED` → exit no-op.
2. **Patch in place — never wipe.** Only modify entries referenced in `re_study_targets` / `revised_changes`. All other entries stay byte-for-byte unchanged.
3. **GAP-22 fanout check** — never rename a driver whose `old_net` has `fanout > 10` in the declaring module scope. Use FM tuning (`set_constant`) instead.
4. **GAP-23 pair `force_reapply` with `undo_instance`** — when re-inserting a gate already in PostEco, always insert an `undo_instance` entry BEFORE the gate entry. Without undo: SVR-9 duplicate gate OR silent pin-change miss.
5. **UNCONNECTED parent-scope rule** — only update `original_per_stage` at the declaring module scope. NEVER edit child module internals — FM traces hierarchically; touching children breaks clock/cone analysis.

## I HAND OFF TO

- Returns to **ROUND_ORCHESTRATOR**, which then spawns:
  - `eco_netlist_verifier` (Pass 6f-B) — re-enrich the patched entries
  - `eco_expand_chains.py` — re-splice any chain that was modified
  - `eco_validate_step3.py` — re-validate the patched study (HARD GATE before re-apply)

---

## Step 1 — Read eco_fm_analyzer Output (with Evidence Contract)

Read `FM_ANALYSIS_PATH`. Extract:
- **Verdict + control fields:** `loop_verdict`, `next_round`, `failure_mode`
- **Action set:** `revised_changes`, `re_study_targets`, `needs_re_study`
- **Evidence summary:** `evidence_summary.evidence_walk_json`, `evidence_summary.xstage_compare_json`

> **MANDATORY — Verdict gate (added with the new investigative analyzer):**
> ```python
> if loop_verdict == "RERUN_SAME_ROUND":
>     # Re-studier MUST NOT run on aborts. ROUND_ORCHESTRATOR Branch B handles
>     # via netlist patches only. If we got called here, exit early with no-op rpt.
>     write_rpt("RERUN_SAME_ROUND verdict — re-studier skipped per evidence contract")
>     copy_to_ai_eco_flow_dir(); EXIT
> if loop_verdict == "CONVERGED":
>     # All targets pass; no work for re-studier.
>     write_rpt("CONVERGED — no study updates needed"); EXIT
> # else: loop_verdict == "ADVANCE_NEXT_ROUND" — proceed
> ```

> **MANDATORY — Load analyzer's evidence dossier (NEW):**
> ```python
> evidence = json.load(open(analysis["evidence_summary"]["evidence_walk_json"]))
> xstage   = json.load(open(analysis["evidence_summary"]["xstage_compare_json"]))
> ```
> These dossiers contain per-DFF failing_diagnostics, cone_analysis, undriven_nets, tune_directives_status, and 3-way Synth/PrePlace/Route netlist deltas. The analyzer pre-walked them; consume directly via `evidence_path_refs` instead of re-greping reports.

> **MANDATORY — Validate analyzer evidence contract:**
> ```bash
> python3 script/eco_scripts/eco_validate_analyzer_evidence_contract.py \
>     --analysis-json $FM_ANALYSIS_PATH
> # Exit code 0 → all revised_changes have valid evidence_for_studier blocks
> # Exit code 1 → contract violated; ABORT this re-study round, force re-spawn analyzer
> ```
> The contract (`config/eco_agents/eco_re_studier_evidence_contract.md`) requires every actionable `revised_change` to carry `evidence_for_studier` with universal + per-action structured fields. If the analyzer didn't comply, the re-studier cannot fulfill its role — fail loudly rather than guess.

## Step 2 — Load Existing Study JSON and Check for Graceful Exit

- **Mode E / Mode G / ABORT_SVF** → write rpt noting no study updates needed, copy to AI_ECO_FLOW_DIR, **EXIT immediately.**
- **`re_study_targets` is empty AND failure_mode is not ABORT_LINK/A/B/D/UNKNOWN** → write rpt "No re-study targets — study JSON unchanged", copy, **EXIT.**

Only proceed to Step 3 for: `ABORT_LINK`, `ABORT_CELL_TYPE`, `A`, `B`, `C`, `D`, `H`, `I`, `S`, `T`, `INCOMPLETE_AND_TERM`, `WRONG_GATE_STRUCTURE`, `CTS_CLOCK_RENAMED`, `CTS_BBNET_INPUT`, `SCAN_CHAIN_MISMATCH`, `UNKNOWN`, or mixed modes with non-empty `re_study_targets`.

---

## MANDATORY STEP 0 — Re-check All and_term Entries

Before processing any failure mode, scan `eco_preeco_study.json` for `and_term` entries where `and_term_strategy != "module_port_direct_gating"`. For each:

```bash
old_token="<old_token_from_study_json>"
rtl_check=$(grep -c "output.*\b${old_token}\b" <REF_DIR>/data/SynRtl/<rtl_file>.v 2>/dev/null || echo 0)
gatelvl_check=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
  awk "/^module <posteco_module_name>/,/\) ;/" | \
  grep -c "output.*\b${old_token}\b" || echo 0)
echo "STEP0: ${old_token} rtl=${rtl_check} gatelvl=${gatelvl_check}"
```

If `rtl_check >= 1` OR `gatelvl_check >= 1` → entry was WRONG — correct it:
1. Set `and_term_strategy = "module_port_direct_gating"`
2. Change `output_net` → `<old_token>` (the port name itself)
3. Add driver rename rewire: original driver cell `.ZN → eco_<jira>_<seq>_orig`
4. Remove ALL individual consumer rewires for `<old_token>` in this module
5. Set `force_reapply: true`
6. Record `re_study_note: "STEP0 correction round N: strategy corrected to module_port_direct_gating. output_net corrected to <old_token>."`

---

## MANDATORY PRE-STEP — Two Safety Checks Before Any Fix

### GAP-22: Fanout Check Before Driver Rename

Before prescribing ANY rewire that renames a driver's output net (e.g., `old_net → new_orig`), count how many cells consume `old_net` in the declaring module scope:

```bash
fanout=$(zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | \
  awk '/^module <module>/,/^endmodule/' | \
  grep -c "\b<old_net>\b")
```

If `fanout > 10` → **DO NOT rename the driver**. High-fanout nets feed many downstream DFFs — renaming severs all connections. Use FM tuning (set_dont_reverse, set_constant) instead. Log: `FANOUT_BLOCK: <old_net> has <N> consumers — driver rename skipped`.

### GAP-23: Paired undo_instance for force_reapply Gates

When setting `force_reapply: true` on a `new_logic_gate` or `new_logic_dff` entry that is ALREADY in PostEco, **always add a paired `undo_instance` entry** before the re-insert entry. Without undo, eco_perl_spec either creates a duplicate gate (SVR-9) or skips silently (pin change not applied).

```python
# If gate already in PostEco AND force_reapply=True:
undo = {
    "change_type": "undo_instance",
    "instance_name": entry["instance_name"],
    "output_net":    entry.get("output_net", ""),
    "module_name":   entry["module_name"],
    "confirmed":     True
}
# Insert undo entry BEFORE the gate entry in the same stage list
```

Log: `FORCE_REAPPLY_UNDO: added undo_instance for <inst> before re-insert`.

---

## Step 3 — Handle Each Failure Mode

> **MANDATORY UNIVERSAL PATTERN — `evidence_for_studier` consumption (NEW):**
>
> Per the evidence contract, every actionable `revised_change` carries `evidence_for_studier.candidate_fix_recipes[]` — the analyzer's pre-vetted shortlist of recipes ranked by `applicability_score`. Use this pattern BEFORE falling back to mode-specific manual investigation:
>
> ```python
> for change in analysis["revised_changes"]:
>     action = change.get("action")
>     if action in ("cascade_verified_skip", "manual_only"):
>         continue
>     e4s = change.get("evidence_for_studier")
>     if not e4s:
>         # Contract violation — should have been caught by validator in Step 1.
>         # If we got here, the analyzer somehow shipped non-compliant output.
>         log_error(f"Missing evidence_for_studier for {action} on {change.get('cell_name')}")
>         continue
>
>     # 1. Honor scope constraints from the contract — abort if studier action
>     #    would touch forbidden modules/signals
>     scope_module = e4s["constraints"].get("scope_module")
>     forbidden_mods = set(e4s["constraints"].get("do_not_modify_modules", []))
>     forbidden_sigs = set(e4s["constraints"].get("do_not_touch_signals", []))
>
>     # 2. Pick the highest-applicability_score recipe whose `applicable_only_if`
>     #    holds (evaluated against analysis context + previous_round_attempts)
>     recipes = sorted(e4s["candidate_fix_recipes"],
>                      key=lambda r: -r["applicability_score"])
>     picked_recipe = None
>     for recipe in recipes:
>         cond = recipe.get("applicable_only_if")
>         if cond and not eval_recipe_condition(cond, e4s, fixer_state):
>             continue
>         picked_recipe = recipe
>         break
>     if not picked_recipe:
>         log_warn(f"No applicable recipe for {action} — falling back to mode-specific block below")
>         # fall through to mode-specific handling
>         continue
>
>     # 3. Apply the recipe's required_inputs_for_studier to study JSON.
>     #    THIS is the recipe-driven path — no re-discovery needed; values already vetted.
>     apply_recipe_to_study(picked_recipe["kind"],
>                           picked_recipe["required_inputs_for_studier"],
>                           study_json,
>                           scope_module=scope_module,
>                           forbidden_modules=forbidden_mods,
>                           forbidden_signals=forbidden_sigs)
>
>     # 4. Verify per recipe's `verification_after_fix` BEFORE moving on.
>     #    If verification fails, try next recipe in the sorted list.
>     if not run_verification(picked_recipe["verification_after_fix"]):
>         log_warn(f"Recipe {picked_recipe['kind']} verification failed; trying next candidate")
>         # loop back to next recipe in sorted list
> ```
>
> The mode-specific blocks below are **fallback** for when `candidate_fix_recipes` is empty or all recipes' `applicable_only_if` failed. Prefer the recipe-driven path.

---

### Step 3.AL — `ABORT_LINK` (missing port from port list)
> **SKIP IF** `failure_mode != "ABORT_LINK"` AND no `force_port_decl` in revised_changes.
> **DONE WHEN** every `force_port_decl` action has its matching `port_declaration` entry set to `force_reapply: true` for all 3 stages with `re_study_note` confirming the port is absent in PostEco.

For each `force_port_decl` in `revised_changes`: For each `force_port_decl` in `revised_changes`:
1. Find matching `port_declaration` entry for `signal_name` + `module_name`
2. Verify port is missing from PostEco Synthesize port list:
   ```bash
   zcat <REF_DIR>/data/PostEco/Synthesize.v.gz | awk '/^module <module_name>/{p=1} p && /\) ;/{print; p=0; exit} p{print}' | grep "<signal_name>"
   ```
3. If missing → set `"force_reapply": true` for ALL stages
4. Record `re_study_note` confirming port absent

### Step 3.A — `failure_mode: A` (ECO not applied correctly)
> **SKIP IF** `failure_mode != "A"`.
> **DONE WHEN** every Mode A target has either `force_reapply: true` set (D-pin not updated case) OR `new_net` corrected (D-pin points to unexpected net) with `re_study_note`.

 For each target in `re_study_targets`:
1. Read PostEco Synthesize to verify current DFF D pin connection
2. If D = old_net (not updated) → set `confirmed: true, force_reapply: true`
3. If D = unexpected net → trace backward, update `new_net`
4. For hierarchical netlists: set `force_reapply: true` on any port_declaration/port_connection marked APPLIED/ALREADY_APPLIED but still missing

**UNCONNECTED PARENT SCOPE RULE (MANDATORY for all Mode A UNCONNECTED fixes):** When fixing UNCONNECTED_* bus bit issues, only update `original_per_stage` at the declaring module scope (the parent that instantiates the submodule). NEVER search inside child modules to rename their internal UNCONNECTEDs. FM traces hierarchically from parent → child → internal DFF on its own. Editing child module internals breaks FM's clock/cone analysis.

**Mode A sub-case — UNCONNECTED bus bit name wrong in a specific stage:**

When Mode A sub-cause 2 (missing wire for UNCONNECTED rename) is diagnosed AND the wire exists in some stages but the bus_element rewire silently failed in another stage (DFF0X in Route vs PP but PP passes), the issue is that `original_per_stage[<stage>]` has the wrong UNCONNECTED name for that stage — studier fell back to Synthesize name.

Fix: Re-search the failing stage's PreEco netlist for the actual UNCONNECTED_* name at the recorded `port_bus_bit` position:
```bash
# Find actual UNCONNECTED name at bit position in failing stage
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | \
  awk '/^module <port_bus_module>/,/^endmodule/' | \
  grep -A5 ".<port_bus_name>\s*(" | \
  grep -oP '\{[^}]+\}' | tr ',' '\n' | \
  sed 's/[{} ]//g' | \
  awk "NR==<total_elements - port_bus_bit>"
# Use the result as the correct original_per_stage[<stage>]
```

Then update study JSON:
```python
for e in study["<Stage>"]:
    for ur in e.get("unconnected_rewires", []):
        if ur.get("port_bus_bit") is not None:
            ur["original_per_stage"]["<Stage>"] = "<actual_unconnected_name>"
            e["force_reapply"] = True
            e["re_study_note"] = f"UNCONNECTED_BIT_FIX: Route original_per_stage corrected from <wrong> to <actual>"
```

### Step 3.B — `failure_mode: B` (regression — wrong cell rewired)
> **SKIP IF** `failure_mode != "B"`.
> **DONE WHEN** every `exclude` action target has `confirmed: false` set (do NOT delete the entry).

 For each `exclude` in `revised_changes`:
- Set `"confirmed": false, "reason": "excluded by eco_fm_analyzer round <ROUND> — Mode B regression"`
- Do NOT delete — set `confirmed: false` so eco_applier skips it

### Step 3.D — `failure_mode: D` (stage mismatch — cell name differs in P&R)
> **SKIP IF** `failure_mode != "D"`.
> **DONE WHEN** every affected entry has `cell_name` updated for the correct P&R stage AND old_net re-verified on correct pin; HFS-alias / cone-check fallback applied for 0 / 2+ grep hits.


- Grep correct PostEco stage for new cell name
- Update `cell_name` for that specific stage
- Re-verify old_net on correct pin
- **If grep returns 0 hits or 2+ hits:** apply `eco_netlist_verifier.md` GAP-5 Steps 2-3 — HFS alias search (0 hits) or cone check (2+ hits) — before accepting any candidate. The same wrong-cell risk applies here: picking the first grep hit without cone verification causes FM failures on unrelated DFFs.

### Step 3.RF — action `rerun_fenets`
> **SKIP IF** no `rerun_fenets` actions in revised_changes AND no entry has `PENDING_FM_RESOLUTION:*` inputs.
> **DONE WHEN** every PENDING signal is resolved via condition_input_resolutions OR Priority-3 structural trace OR marked `UNRESOLVABLE:<signal>`. NEVER leave `PENDING_FM_RESOLUTION` after FM-036 rerun.

 Build resolution map from `condition_input_resolutions[]` where `resolved_gate_level_net` is set. For each gate entry with `PENDING_FM_RESOLUTION:<signal>`:

1. **Rerun fenets result** — if resolved with direct driver → use directly; if `needs_named_wire` → set `NEEDS_NAMED_WIRE`
2. **Priority 3 structural driver trace** (if rerun returned FM-036 or no rerun done):
   ```bash
   grep -n "\.<output_pin>( <synth_resolved_net> )" /tmp/eco_study_<TAG>_Synthesize.v | head -3
   grep -n "\b<driver_cell_name>\b" /tmp/eco_study_<TAG>_<FailingStage>.v | head -3
   ```
3. **Still unresolved** → mark `UNRESOLVABLE:<signal>` (NOT `PENDING_FM_RESOLUTION`)

**CRITICAL: Do NOT leave `PENDING_FM_RESOLUTION` after FM-036 rerun. Escalate to Priority 3 immediately.**

### Step 3.CT — `failure_mode: ABORT_CELL_TYPE`
> **SKIP IF** `failure_mode != "ABORT_CELL_TYPE"`.
> **DONE WHEN** every `fix_cell_type` action has `cell_type` updated for all 3 stages where `instance_name == gate_instance` AND `re_study_note` recorded.

 For each `fix_cell_type` entry:
- **CT-1 — Find correct cell in PreEco Synthesize:**
  ```bash
  zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
    awk '/^module <scope_module>/{p=1} p && /\.<pin1>.*\.<pin2>.*\.<output_pin>/{print; exit} /^endmodule/{p=0}' | \
    grep -oE "^[[:space:]]*[A-Z][A-Z0-9]+" | head -3
  ```
- **CT-2 — Update** `cell_type` for all stages where `entry["instance_name"] == gate_instance`, add `re_study_note`

### Step 3.T — `failure_mode: T` (compound-cell truth-table mismatch)
> **SKIP IF** `failure_mode != "T"`.
> **DONE WHEN** every `swap_compound_cell` action has `cell_type` overridden to `correct_cell_type` for all 3 stages AND `port_remap` applied atomically (when present) AND `re_study_note` recorded.

 For each `swap_compound_cell` entry — fm_analyzer Check T already picked `correct_cell_type`; no PreEco re-search needed:
- **T-1 — Override `cell_type`** to `correct_cell_type` for all 3 stages where `entry["instance_name"] == gate_instance`.
- **T-2 — If `port_remap` present**, rebuild `port_connections` (and `port_connections_per_stage[*]`) by remapping pin names: for each `(old_pin, new_pin)` in `port_remap`, the value previously at `old_pin` moves to `new_pin`. Apply atomically to avoid clobbering when remap is a permutation.
- **T-3 — Add `re_study_note: "swap_compound_cell <wrong>→<correct>"`.** Do NOT touch `gate_function` text, output_net, or scope.

### Step 3.H — `failure_mode: H` (gate input inaccessible in P&R stage)
> **SKIP IF** `failure_mode != "H"`.
> **DONE WHEN** every `fix_named_wire` action has H1/H2/H3 applied (structural confirm + P&R alias resolve + study JSON updated with `port_connections_per_stage` per-stage values, falling back to `NEEDS_NAMED_WIRE` only when no alias found), AND `mode_H_risk` flags re-read from rtl_diff are propagated.

 For each `fix_named_wire` entry:

**H1 — Confirm structural issue:**
```bash
par_count=$(zcat <REF_DIR>/data/PreEco/<stage>.v.gz | grep -cw "<source_net>")
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<source_net>")
# par_count=0, synth_count>0 → H-RENAME
# par_count=0, synth_count=0 → H-BUS
```

**H2 — Find P&R alias:** For H-RENAME: find driver of `source_net` in Synthesize → search same driver instance in P&R → read its output net. For H-BUS: keep `source_net` as-is.

**P&R PER-STAGE ALIAS RULE (MANDATORY in H2 — all input pins):** Copy per-stage values from a pre-existing DFF in the same module scope (find one whose Synth pin matches the ECO entry's logical signal; use its per-stage net names verbatim, including scan/DFT/CTS renames). For SE/SI on new ECO DFFs: Synth=`1'b0`, PP/Route=neighbor DFF's per-stage SE/SI (NOT `1'b0` — see eco_netlist_studier.md `0b-STAGE-NETS`).

**H3 — Update study JSON:**
```python
entry.setdefault("port_connections_per_stage", {
    s: dict(entry.get("port_connections", {})) for s in ["Synthesize", "PrePlace", "Route"]
})
# SE/SI on new ECO DFFs: Synth='1'b0', PP/Route=neighbor DFF's per-stage SE/SI
# (real scan-bridge wires — NOT '1'b0' in P&R, which would isolate from scan chain).
# All other input pins: copy per-stage value from a neighbor DFF whose Synth value
# matches the entry's logical signal — including scan/DFT/CTS-renamed names.
if input_pin in ('SE', 'SI') and stage == 'Synthesize':
    entry["port_connections_per_stage"][stage][input_pin] = "1'b0"
elif par_alias_found:
    entry["port_connections_per_stage"][stage][input_pin] = par_alias
else:
    entry["port_connections_per_stage"][stage][input_pin] = f"NEEDS_NAMED_WIRE:{source_net}"
    entry["needs_named_wire"] = True
    entry["port_bus_source_net"] = source_net
entry["force_reapply"] = True
entry["re_study_note"] = f"Mode H fix on pin {input_pin}: {source_net} inaccessible in {stage}"
```

**H4 — Do NOT set `force_reapply` for Synthesize** unless diagnosed with the same issue.

**GAP-19 — Original register preference in Mode H:**
- Skip cells with `_dup<N>` suffix (scan-chain duplicates)
- For `_MB_` merged cells: the LAST `_MB_<reg_name>` segment identifies the original register

**H5 — Re-read `mode_H_risk` flags (MANDATORY at re-study start):**
```python
for change in rtl_diff.get("changes", []):
    for gate in change.get("d_input_gate_chain", []):
        if gate.get("mode_H_risk") and gate.get("missing_in_stages"):
            for stage in gate["missing_in_stages"]:
                entry = find_entry_by_instance(gate["instance_name"])
                if entry and not already_updated(entry, stage, gate["inputs"]):
                    alias = priority3_structural_trace(gate["inputs"][0], stage)
                    # P&R PER-STAGE ALIAS RULE: copy from neighbor's per-stage value
                    # (scan/DFT/CTS-renamed names ARE the right answer in P&R).
                    # Only Synth-stage SE/SI uses the constant '1'b0'.
                    if gate["pin"] in ('SE', 'SI') and stage == 'Synthesize':
                        entry["port_connections_per_stage"][stage][gate["pin"]] = "1'b0"
                    else:
                        # Mode H Route fallback — if alias still not found in Route,
                        # check for ECO port substitute before falling back to NEEDS_NAMED_WIRE.
                        # When `route_substituted_with_eco_port: true` is in the gate entry,
                        # a prior studier pass already chose a substitute — reuse it.
                        if stage == 'Route' and not alias:
                            eco_port_sub = gate.get('route_eco_port_substitute')
                            if eco_port_sub and zgrep_count(eco_port_sub, preeco_route_gz) > 0:
                                alias = eco_port_sub
                                entry["route_substituted_with_eco_port"] = True
                        entry["port_connections_per_stage"][stage][gate["pin"]] = alias or f"NEEDS_NAMED_WIRE:{gate['inputs'][0]}"
                    entry["force_reapply"] = True
```

### Step 3.MG — action `move_gate_to_submodule`
> **SKIP IF** no revised_change has `action == "move_gate_to_submodule"`.
> **DONE WHEN** affected gate's entries have `instance_scope` changed to the child path, `scope_is_submodule_insertion: true` set, AND companion port_declaration + port_connection entries auto-added; all marked `force_reapply: true`.

 (persistent DFF0X after rename_wire — GAP-18 submodule black-box)

1. Find all study entries for `gate_instance` (across all 3 stages)
2. Change `instance_scope` to `preferred_insertion_scope` (the child instance path)
3. Add `scope_is_submodule_insertion: true`
4. Auto-add a `port_declaration` entry for the gate's output net from the child module (direction=output)
5. Auto-add a `port_connection` entry at the parent scope: `<child_instance>.<output_net> = <output_net>`
6. Set `force_reapply: true` on all affected entries
7. `re_study_note: "move_gate_to_submodule: gate chain moved inside <child_module> — FM can trace signal without submodule black-box"`

### Step 3.GF — action `update_gate_function`
> **SKIP IF** no revised_change has `action == "update_gate_function"`.
> **DONE WHEN** every affected entry has `gate_function` + `cell_type` updated for all 3 stages AND WIRE_SWAP gate direction check passed (no De Morgan substitutions — match `mux_select_gate_function` exactly).


- **GF-1** — Find correct cell in PreEco Synthesize (same port-structure search)
- **GF-2** — Update `gate_function`, `cell_type`, `re_study_note` for ALL stages

**WIRE_SWAP GATE DIRECTION CHECK (MANDATORY in GF-2):** Before updating gate_function, verify the gate type matches the RTL operator direction. AND expression in RTL → must use AND2 (output pin `Z`). NEVER substitute De Morgan equivalents (NAND2, OR2) even if logically identical — they create different LatCG cone structures that cause FM gate-level equivalence failures. Verify by reading `mux_select_gate_function` from `eco_rtl_diff.json` and confirming gate polarity matches.

### Step 3.J — `failure_mode: J` (chain-leaf inverter parity flip — Mode J)
> **SKIP IF** `failure_mode != "J"`.
> **DONE WHEN** every affected gate's failing-stage input pin has been rewired (via `port_connections_per_stage[<stage>][<pin>]`) to a polarity-correct wire: priority 1 = MB DFF Q-pin direct (`aps_rename_*`); priority 2 = `actual_wire_<stage>` from rename_map; NEVER mid-buffer-chain nets. Do NOT call `update_gate_function` — gate function is correct; the input wire is the bug. See `eco_fm_pattern_library.md §B-FAIL-J` for the recipe.

### Step 3.UK — `failure_mode: UNKNOWN`
> **SKIP IF** `failure_mode != "UNKNOWN"`.
> **DONE WHEN** for each `target_register`: full forward/backward cone traced from DFF in PostEco Synthesize, FM result re-parsed for the net, study entry updated with the resolved fix.

For each `target_register`: trace full forward/backward cone from DFF in PostEco Synthesize, re-parse FM result for this net, update study entry.

---

## Step 4 — Save Updated Study JSON

Write back `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` with ONLY modified entries changed.
Verify `wc -l` ≥ original line count.

Write `<BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` with:
- Per change-type format (from STUDY_ORCHESTRATOR.md Step 3)
- Identifier per entry type, old→new for rewires, gate_function/output_net/cell_type for new_logic
- Direction for port_declaration, parent/port/net for port_connection
- Full reason for EXCLUDED entries
- SUMMARY of all `force_reapply` entries set

```bash
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt <AI_ECO_FLOW_DIR>/
```

**When making DIRECT PostEco netlist edits** (removing lines from PostEco stages), always check and fix trailing comma:
```python
def remove_line_and_fix_trailing_comma(lines, line_idx):
    """Remove line at line_idx. If preceding non-empty line ends with comma
    and next non-empty line is ') ;', strip the trailing comma."""
    lines.pop(line_idx)
    # Find preceding non-empty line
    for prev in range(line_idx-1, -1, -1):
        if lines[prev].strip():
            if lines[prev].rstrip().endswith(','):
                # Check if next non-empty line is ') ;'
                for nxt in range(line_idx, min(line_idx+5, len(lines))):
                    if lines[nxt].strip():
                        if re.match(r'^\)\s*;', lines[nxt].strip()):
                            lines[prev] = lines[prev].rstrip().rstrip(',') + '\n'
                        break
            break
    return lines
```
This prevents SVR-4 "mixed ordered/named" errors from dangling trailing commas.

**Exit after writing and copying the RPT. Do NOT spawn eco_netlist_verifier yourself — ROUND_ORCHESTRATOR spawns it next as Pass 6f-B.** Your job ends here.
