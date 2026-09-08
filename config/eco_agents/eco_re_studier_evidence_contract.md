# ECO Analyzer ↔ Re-Studier Evidence Contract

**Purpose:** Define the structured `evidence_for_studier` block that `eco_fm_analyzer` MUST emit per `revised_change`, and that `eco_netlist_re_studier` MUST consume. Closes the broken-link gap where the analyzer produces rich evidence (evidence_walk_json + xstage_compare_json) but the re-studier today only reads 4 high-level fields.

**Producer:** `config/eco_agents/eco_fm_analyzer.md` §6 (output JSON)
**Consumer:** `config/eco_agents/eco_netlist_re_studier.md` Step 1 + Step 3 mode handlers
**Validator:** `script/eco_scripts/eco_validate_analyzer_evidence_contract.py`
**Enforced as:** mandatory pre-FM gate `[ANALYZER_EVIDENCE_CONTRACT]` — non-compliant analyzer output fails the round, no FM submission

---

## §1 — Universal `evidence_for_studier` Block (every revised_change)

Every `revised_change` entry must carry this block, regardless of action:

```json
{
  "evidence_for_studier": {
    "failing_pin": "D|CP|SE|SI|<other>",
    "failing_pin_load_bearing": true|false,
    "load_bearing_reason": "shadowed_by_set_constant|shadowed_by_set_dont_verify|none",
    "first_divergent_point": {
      "kind": "undriven_cut|cts_rename|blackbox|wrong_gate|missing_port|wrong_polarity|cell_not_in_lib|other",
      "what": "<specific_net_or_cell_or_wire>",
      "exists_in_ref_cone": true|false,
      "exists_in_impl_cone": true|false,
      "evidence_path_refs": ["evidence.<jsonpath>", "xstage.<jsonpath>", ...]
    },
    "stage_compare": {                 ← optional per action; from xstage JSON
      "synth": {...},
      "preplace": {...},
      "route": {...}
    },
    "candidate_fix_recipes": [         ← REQUIRED — analyzer's pre-vetted shortlist
      {
        "kind": "<action-specific recipe identifier>",
        "applicability_score": 0.0-1.0,
        "required_inputs_for_studier": {...},
        "verification_after_fix": "<grep/check the studier should run after applying>"
      },
      ...
    ],
    "constraints": {                   ← what the studier MUST NOT break
      "do_not_modify_modules": ["<list>"],
      "do_not_touch_signals": ["<list>"],
      "scope_module": "<single module name where edits must stay>"
    },
    "previous_round_attempts": [       ← from analysis_round<N-1>.json (if ROUND>1)
      {"round": N-1, "action": "...", "result": "..."}
    ]
  }
}
```

**Required fields**: `failing_pin`, `failing_pin_load_bearing`, `first_divergent_point` (all sub-fields), `candidate_fix_recipes` (at least 1 entry), `constraints.scope_module` (when applicable).

**Optional fields**: `stage_compare` (only meaningful when failure spans stages), `previous_round_attempts` (only when ROUND > 1).

---

## §2 — Per-Action Schemas

Each action verb has additional REQUIRED fields beyond the universal block. The validator enforces these.

### §2.1 — `fix_named_wire` / `move_gate_to_submodule` (Mode H)

```json
"evidence_for_studier": {
  ...universal block...,
  "candidate_fix_recipes": [
    {
      "kind": "rename_to_named_wire",        ← first attempt
      "applicability_score": 0.7,
      "required_inputs_for_studier": {
        "gate_instance": "<eco_jira_seq>",
        "input_pin": "<A1|A2|I|...>",
        "source_net": "<original_net_in_port_bus>",
        "new_named_wire": "n_eco_<jira>_<purpose>",
        "host_module": "<module>",
        "stage_scope": ["PrePlace", "Route"],   ← never Synth (passes there)
        "submodule_blackboxed": "<child_module_name>",
        "port_bus_pattern": ".\\s*<port>\\s*\\(\\s*\\{[^}]*<source_net>"
      },
      "verification_after_fix": "PostEco/<stage>.v.gz contains 'wire <new_named_wire>;' in host_module"
    },
    {
      "kind": "move_gate_to_submodule",      ← if rename_to_named_wire already tried
      "applicability_score": 0.9,
      "applicable_only_if": "previous_round_attempts contains rename_to_named_wire AND DFF still DFF0X",
      "required_inputs_for_studier": {
        "gate_instance": "<eco_jira_seq>",
        "preferred_insertion_scope": "<child_inst_in_host>",
        "submodule_type": "<child_module_type>",
        "new_output_port_name": "ECO_<jira>_<purpose>_out",
        "new_port_declaration_type": "output",
        "gate_chain_to_move": ["<eco_inst1>", "<eco_inst2>", ...],
        "parent_dff_d_input_rewire_to": "<new_output_port_name>"
      },
      "verification_after_fix": "submodule_type now declares output ECO_<jira>_<purpose>_out; parent DFF.D = that port"
    }
  ],
  "constraints": {
    "scope_module": "<host_module_name>",
    "do_not_touch_signals": ["<other ECO signals not in this gate chain>"]
  },
  "previous_round_attempts": [
    {"round": ROUND-1, "action": "fix_named_wire", "rename_wire": true, "result": "DFF0X persists"}
  ]
}
```

**GAP for Mode H persistent (auto-escalation to move_gate_to_submodule)** is encoded in `applicable_only_if` + `previous_round_attempts`.

### §2.2 — `try_alternative_pivot` / `try_structural_insertion` (Mode F1)

```json
"evidence_for_studier": {
  ...universal block...,
  "candidate_fix_recipes": [
    {
      "kind": "invert_cmux_constants",       ← if pivot driver inverting + constants not flipped
      "applicability_score": 0.85,
      "applicable_only_if": "pivot_driver_cell_type matches NOR|NAND|INV AND 'invert_cmux_constants' not in strategies_tried",
      "required_inputs_for_studier": {
        "pivot_net": "<net_name>",
        "c_mux_instances": ["<inst>", ...],
        "constants_to_flip": [
          {"inst": "<i>", "pin": "<A1|A2|...>", "current": "1'b0", "new": "1'b1"},
          ...
        ]
      },
      "verification_after_fix": "all listed constants flipped in PostEco"
    },
    {
      "kind": "try_structural_insertion",    ← Strategy A: feed into existing compound gate
      "applicability_score": 0.7,
      "required_inputs_for_studier": {
        "host_module": "<module>",
        "candidate_anchor_gates": [          ← analyzer pre-searches PreEco for compound gates
          {
            "inst_name": "<existing_inst_in_priority_chain>",
            "cell_type": "<AOI21|OAI22|AND3|ND3|...>",
            "available_pins": ["<pin>", ...],
            "current_inputs": {"<pin>": "<net>"},
            "applicability": "Can accept new condition on <pin>"
          },
          ...
        ],
        "new_condition_signal": "<signal_to_inject>",
        "forbidden_gate_types": ["MUX2"]
      },
      "verification_after_fix": "anchor gate's <pin> now driven by new gate output combining old input with <new_condition_signal>"
    },
    {
      "kind": "try_alternative_pivot",       ← last resort within Mode F1
      "applicability_score": 0.4,
      "required_inputs_for_studier": {
        "current_pivot": "<old_pivot_net>",
        "max_hops_back": 3,
        "candidate_alternative_pivots": [    ← analyzer pre-discovers via PreEco walk
          {
            "net": "<candidate>",
            "hops_from_target": <N>,
            "driver_cell_type": "<type>",
            "passes_gap22_fanout_check": true|false
          },
          ...
        ]
      },
      "verification_after_fix": "study JSON pivot_net field updated; new c_mux chain inserted at new pivot"
    }
  ],
  "constraints": {
    "scope_module": "<host_module>",
    "do_not_touch_signals": ["<other_ECO_changes_unrelated_to_this_pivot>"]
  },
  "previous_round_attempts": [...]
}
```

**Progressive doctrine encoded**: `applicability_score` ranks the recipes; `applicable_only_if` excludes already-tried strategies.

### §2.3 — `force_port_decl` (ABORT_LINK)

```json
"evidence_for_studier": {
  ...universal block (failing_pin not applicable; use "N/A" + load_bearing=N/A)...,
  "first_divergent_point": {
    "kind": "missing_port",
    "what": "<port_name>",
    "evidence_path_refs": [
      "evidence.per_target.<tgt>.abort_diagnostics.missing_ports[<i>]",
      "evidence.per_target.<tgt>.abort_diagnostics.fm_error_codes (FE-LINK-7, FM-234)"
    ]
  },
  "candidate_fix_recipes": [
    {
      "kind": "force_reapply_port_decl",
      "applicability_score": 0.95,
      "required_inputs_for_studier": {
        "signal_name": "<port_name>",
        "module_name": "<module>",
        "declaration_type": "input|output",
        "stage_scope": ["ALL"],
        "force_reapply": true,
        "wire_exists_in_module_body": true|false,    ← from netlist grep
        "port_in_port_list_header": false,           ← FE-LINK-7 confirms
        "previous_already_applied_reason": "<from eco_applied JSON>"
      },
      "verification_after_fix": "PostEco module port list header includes <port_name>; FE-LINK-7 no longer in log"
    },
    {
      "kind": "fix_cell_type",                 ← when sub-B (TECH_LIB_DB error)
      "applicable_only_if": "missing_port.module path contains '/TECH_LIB_DB/'",
      "applicability_score": 0.9,
      "required_inputs_for_studier": {
        "gate_instance": "<eco_jira_seq>",
        "wrong_cell_type": "<cell_used>",
        "missing_pin": "<pin>",
        "gate_function": "<from study JSON>",
        "candidate_correct_cell_types": [    ← analyzer pre-searches PreEco
          {"cell_type": "<candidate>", "verified_via_truth_table": true, "ports": ["..."]},
          ...
        ]
      },
      "verification_after_fix": "ECO instance now uses correct_cell_type with valid port names"
    }
  ],
  "constraints": {
    "scope_module": "<module>",
    "do_not_touch_signals": []
  }
}
```

---

## §3 — Validator Behavior

`script/eco_scripts/eco_validate_analyzer_evidence_contract.py`:

```bash
python3 script/eco_scripts/eco_validate_analyzer_evidence_contract.py \
    --analysis-json <AI_ECO_FLOW_DIR>/data/<TAG>_eco_fm_analysis_round<N>.json \
    --output        <AI_ECO_FLOW_DIR>/data/<TAG>_eco_evidence_contract_check_round<N>.json
```

Exit code:
- `0` — all entries comply
- `1` — at least one entry missing required field; emit error JSON listing violations

Validation rules:
1. Every `revised_changes[i]` (except `cascade_verified_skip` and `manual_only`) MUST have `evidence_for_studier`
2. Universal block fields all present + correctly typed
3. At least 1 `candidate_fix_recipes` entry
4. Per-action required fields per §2 schemas
5. `evidence_path_refs` resolve to actual paths in evidence_walk_json + xstage_compare_json (load + dereference check)

---

## §4 — Re-Studier Consumption Pattern

```python
# eco_netlist_re_studier Step 1 (NEW)
analysis     = json.load(open(FM_ANALYSIS_PATH))
verdict      = analysis["loop_verdict"]
if verdict == "RERUN_SAME_ROUND":
    # Re-studier should NOT run on aborts — ROUND_ORCHESTRATOR Branch B handles via
    # netlist patches only. If we got called, exit early with no-op rpt.
    write_noop_rpt("RERUN_SAME_ROUND verdict — re-studier skipped per contract"); EXIT

evidence = json.load(open(analysis["evidence_summary"]["evidence_walk_json"]))
xstage   = json.load(open(analysis["evidence_summary"]["xstage_compare_json"]))

# Step 3 mode handlers (NEW)
for change in analysis["revised_changes"]:
    if change.get("action") in ("cascade_verified_skip", "manual_only"):
        continue
    e4s = change["evidence_for_studier"]
    # Pick the highest-applicability_score recipe whose applicable_only_if condition holds
    recipes = sorted(e4s["candidate_fix_recipes"], key=lambda r: -r["applicability_score"])
    for recipe in recipes:
        if recipe.get("applicable_only_if") and not eval_condition(recipe["applicable_only_if"], context):
            continue
        # Apply the recipe to the study JSON using its required_inputs_for_studier
        apply_recipe(change["action"], recipe, study_json)
        # Run recipe's verification_after_fix immediately to confirm
        if not verify(recipe["verification_after_fix"], updated_netlist):
            log_warn(f"Recipe verification failed; trying next candidate")
            continue
        break
    else:
        # All recipes exhausted — emit failure entry for ROUND_ORCHESTRATOR
        emit_studier_no_recipe_applicable(change)
```

Key behaviors:
- `applicability_score` picks primary recipe
- `applicable_only_if` filters by prior-round state + context (e.g., "rename already tried → escalate to move")
- `required_inputs_for_studier` IS the recipe's parameter set — no re-discovery needed
- `verification_after_fix` enables in-loop validation — fall through to next recipe on failure

---

## §5 — Backward Compatibility

For one transition round, both old + new schema are accepted. Validator emits WARN (not ERROR) if `evidence_for_studier` missing. After 2 rounds with all entries compliant, validator switches to ERROR mode. Configurable via `--strict` flag.

---

## §6 — Examples

### Example 1: Mode H — submodule-input black-box

```json
{
  "stage": "Route",
  "action": "move_gate_to_submodule",
  "cell_name": "<reg>_reg",
  "rationale": "Chain inputs come from inside child submodule which is black-boxed in P&R; gate must move inside the child so inputs resolve.",
  "fallback_action": "fix_named_wire",
  "evidence_for_studier": {
    "failing_pin": "D",
    "failing_pin_load_bearing": true,
    "load_bearing_reason": "none",
    "first_divergent_point": {
      "kind": "blackbox",
      "what": "<child_submodule_inst>",
      "exists_in_ref_cone": true,
      "exists_in_impl_cone": false,
      "evidence_path_refs": [
        "xstage.per_failing_dff.<reg>_reg.deltas.cell_blackboxed[0]"
      ]
    },
    "candidate_fix_recipes": [
      {
        "kind": "move_gate_to_submodule",
        "applicability_score": 0.9,
        "required_inputs_for_studier": {
          "host_module": "<parent_module>",
          "target_submodule": "<child_module>",
          "gate_chain_to_relocate": ["eco_<jira>_d001", "eco_<jira>_d002"],
          "new_output_port": "n_eco_<jira>_d_last"
        },
        "verification_after_fix": "child module port list contains n_eco_<jira>_d_last; parent .D wires through new port"
      }
    ],
    "constraints": {
      "scope_module": "<child_module>",
      "do_not_touch_signals": []
    },
    "previous_round_attempts": []
  }
}
```

### Example 2: ABORT_LINK missing port

```json
{
  "stage": "ALL",
  "action": "force_port_decl",
  "signal_name": "<eco_port_name>",
  "module_name": "<child_module>",
  "declaration_type": "input",
  "rationale": "...",
  "evidence_for_studier": {
    "failing_pin": "N/A",
    "failing_pin_load_bearing": false,
    "load_bearing_reason": "none (abort, no compare ran)",
    "first_divergent_point": {
      "kind": "missing_port",
      "what": "<eco_port_name> on instance <child_inst>",
      "exists_in_ref_cone": false,
      "exists_in_impl_cone": false,
      "evidence_path_refs": [
        "evidence.per_target.FmEqvEcoRouteVsEcoPrePlace.abort_diagnostics.missing_ports[0]",
        "evidence.per_target.FmEqvEcoRouteVsEcoPrePlace.abort_diagnostics.fm_error_codes"
      ]
    },
    "candidate_fix_recipes": [
      {
        "kind": "force_reapply_port_decl",
        "applicability_score": 0.95,
        "required_inputs_for_studier": {
          "signal_name": "<eco_port_name>",
          "module_name": "<child_module>",
          "declaration_type": "input",
          "stage_scope": ["Route"],
          "force_reapply": true,
          "wire_exists_in_module_body": false,
          "port_in_port_list_header": false,
          "previous_already_applied_reason": "found in file (NOT in port list)"
        },
        "verification_after_fix": "PostEco/Route.v.gz module <child_module> port list contains <eco_port_name>"
      }
    ],
    "constraints": {
      "scope_module": "<child_module>",
      "do_not_modify_modules": []
    }
  }
}
```
