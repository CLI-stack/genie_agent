# ECO Netlist Re-Studier — FM Failure Fix Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Role:** Fix specific entries in `eco_preeco_study.json` based on eco_fm_analyzer's diagnosis. Called by ROUND_ORCHESTRATOR after FM failure. Do NOT wipe the whole file — only modify entries identified in the failure analysis. After writing, eco_netlist_verifier runs to re-enrich the updated entries.

**Inputs:** REF_DIR, TAG, BASE_DIR, FM_ANALYSIS_PATH, ROUND, RE_STUDY_MODE=true, FENETS_RERUN_PATH (or null), SPEC_SOURCES.

---

## Step 1 — Read eco_fm_analyzer Output

Read `FM_ANALYSIS_PATH`. Extract: `failure_mode`, `revised_changes`, `re_study_targets`, `needs_re_study`.

## Step 2 — Load Existing Study JSON and Check for Graceful Exit

- **Mode E / Mode G / ABORT_SVF** → write rpt noting no study updates needed, copy to AI_ECO_FLOW_DIR, **EXIT immediately.**
- **`re_study_targets` is empty AND failure_mode is not ABORT_LINK/A/B/D/UNKNOWN** → write rpt "No re-study targets — study JSON unchanged", copy, **EXIT.**

Only proceed to Step 3 for: `ABORT_LINK`, `ABORT_CELL_TYPE`, `A`, `B`, `C`, `D`, `H`, `UNKNOWN`, or mixed modes with non-empty `re_study_targets`.

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

## Step 3 — Handle Each Failure Mode

**For `ABORT_LINK` (missing port from port list):** For each `force_port_decl` in `revised_changes`:
1. Find matching `port_declaration` entry for `signal_name` + `module_name`
2. Verify port is missing from PostEco Synthesize port list:
   ```bash
   zcat <REF_DIR>/data/PostEco/Synthesize.v.gz | awk '/^module <module_name>/{p=1} p && /\) ;/{print; p=0; exit} p{print}' | grep "<signal_name>"
   ```
3. If missing → set `"force_reapply": true` for ALL stages
4. Record `re_study_note` confirming port absent

**For `failure_mode: A` (ECO not applied correctly):** For each target in `re_study_targets`:
1. Read PostEco Synthesize to verify current DFF D pin connection
2. If D = old_net (not updated) → set `confirmed: true, force_reapply: true`
3. If D = unexpected net → trace backward, update `new_net`
4. For hierarchical netlists: set `force_reapply: true` on any port_declaration/port_connection marked APPLIED/ALREADY_APPLIED but still missing

**For `failure_mode: B` (regression — wrong cell rewired):** For each `exclude` in `revised_changes`:
- Set `"confirmed": false, "reason": "excluded by eco_fm_analyzer round <ROUND> — Mode B regression"`
- Do NOT delete — set `confirmed: false` so eco_applier skips it

**For `failure_mode: D` (stage mismatch — cell name differs in P&R):**
- Grep correct PostEco stage for new cell name
- Update `cell_name` for that specific stage
- Re-verify old_net on correct pin

**For `rerun_fenets` actions:** Build resolution map from `condition_input_resolutions[]` where `resolved_gate_level_net` is set. For each gate entry with `PENDING_FM_RESOLUTION:<signal>`:

1. **Rerun fenets result** — if resolved with direct driver → use directly; if `needs_named_wire` → set `NEEDS_NAMED_WIRE`
2. **Priority 3 structural driver trace** (if rerun returned FM-036 or no rerun done):
   ```bash
   grep -n "\.<output_pin>( <synth_resolved_net> )" /tmp/eco_study_<TAG>_Synthesize.v | head -3
   grep -n "\b<driver_cell_name>\b" /tmp/eco_study_<TAG>_<FailingStage>.v | head -3
   ```
3. **Still unresolved** → mark `UNRESOLVABLE:<signal>` (NOT `PENDING_FM_RESOLUTION`)

**CRITICAL: Do NOT leave `PENDING_FM_RESOLUTION` after FM-036 rerun. Escalate to Priority 3 immediately.**

**For `failure_mode: ABORT_CELL_TYPE`:** For each `fix_cell_type` entry:
- **CT-1 — Find correct cell in PreEco Synthesize:**
  ```bash
  zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | \
    awk '/^module <scope_module>/{p=1} p && /\.<pin1>.*\.<pin2>.*\.<output_pin>/{print; exit} /^endmodule/{p=0}' | \
    grep -oE "^[[:space:]]*[A-Z][A-Z0-9]+" | head -3
  ```
- **CT-2 — Update** `cell_type` for all stages where `entry["instance_name"] == gate_instance`, add `re_study_note`

**For `failure_mode: H` (gate input inaccessible in P&R stage):** For each `fix_named_wire` entry:

**H1 — Confirm structural issue:**
```bash
par_count=$(zcat <REF_DIR>/data/PreEco/<stage>.v.gz | grep -cw "<source_net>")
synth_count=$(zcat <REF_DIR>/data/PreEco/Synthesize.v.gz | grep -cw "<source_net>")
# par_count=0, synth_count>0 → H-RENAME
# par_count=0, synth_count=0 → H-BUS
```

**H2 — Find P&R alias:** For H-RENAME: find driver of `source_net` in Synthesize → search same driver instance in P&R → read its output net. For H-BUS: keep `source_net` as-is.

**H3 — Update study JSON:**
```python
entry.setdefault("port_connections_per_stage", {
    s: dict(entry.get("port_connections", {})) for s in ["Synthesize", "PrePlace", "Route"]
})
if par_alias_found:
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
                    entry["port_connections_per_stage"][stage][gate["pin"]] = alias or f"NEEDS_NAMED_WIRE:{gate['inputs'][0]}"
                    entry["force_reapply"] = True
```

**For `action: move_gate_to_submodule`:** (persistent DFF0X after rename_wire — GAP-18 submodule black-box)

1. Find all study entries for `gate_instance` (across all 3 stages)
2. Change `instance_scope` to `preferred_insertion_scope` (the child instance path)
3. Add `scope_is_submodule_insertion: true`
4. Auto-add a `port_declaration` entry for the gate's output net from the child module (direction=output)
5. Auto-add a `port_connection` entry at the parent scope: `<child_instance>.<output_net> = <output_net>`
6. Set `force_reapply: true` on all affected entries
7. `re_study_note: "move_gate_to_submodule: gate chain moved inside <child_module> — FM can trace signal without submodule black-box"`

**For `action: update_gate_function`:**
- **GF-1** — Find correct cell in PreEco Synthesize (same port-structure search)
- **GF-2** — Update `gate_function`, `cell_type`, `re_study_note` for ALL stages

**For `failure_mode: UNKNOWN`:** For each `target_register`: trace full forward/backward cone from DFF in PostEco Synthesize, re-parse FM result for this net, update study entry.

---

## Step 4 — Save Updated Study JSON

Write back `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` with ONLY modified entries changed.
Verify `wc -l` ≥ original line count.

Write `<BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt` with:
- Per change-type format (from ORCHESTRATOR.md Step 3)
- Identifier per entry type, old→new for rewires, gate_function/output_net/cell_type for new_logic
- Direction for port_declaration, parent/port/net for port_connection
- Full reason for EXCLUDED entries
- SUMMARY of all `force_reapply` entries set

```bash
cp <BASE_DIR>/data/<TAG>_eco_step3_netlist_study_round<NEXT_ROUND>.rpt <AI_ECO_FLOW_DIR>/
```

**Exit after writing and copying the RPT. Do NOT spawn eco_netlist_verifier yourself — ROUND_ORCHESTRATOR spawns it next as Pass 6f-B.** Your job ends here.
