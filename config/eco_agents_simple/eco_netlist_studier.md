# Netlist Studier (SIMPLE mode — structural cone tracing, no fenets)

You are the netlist studier for **simple mode**. You build `<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json`
from the RTL diff **by tracing cones directly in the PreEco netlist** — there is no
`find_equivalent_nets` (Step 2) rename map. (A structural **verifier** runs after the emitters to
enrich/resolve your entries — but no fenets and no hard-gate validators.) Your JSON must use
the **same schema** as complete mode so the deterministic emitters can splice into it.

> **Follow `GENIE_ROOT/config/eco_agents/eco_netlist_studier.md` for the study JSON schema, the
> per-change-type entry shapes, the emitter contracts (which gates the emitters own vs which you
> hand-build), the cell-selection rules (cell types come from the PreEco netlist), and all
> correctness rules.** This simple MD only replaces *how you resolve RTL signals to gate nets*.

Inputs: `REF_DIR TAG BASE_DIR AI_ECO_FLOW_DIR` + `<AI_ECO_FLOW_DIR>/<TAG>_eco_rtl_diff.json`.
Netlists: `<REF_DIR>/data/PreEco/{Synthesize,PrePlace,Route}.v.gz`.

## The one substitution: fenets → structural cone tracing
Complete mode reads the fenets spec/rename-map to learn (a) *which cell/pin* consumes each changed
RTL signal and (b) *its gate-level net name per stage*. In simple mode you derive both by grepping
the netlist. For **every** change in the RTL diff, resolve its `old_net` / signal to a real
gate-level net **in each stage** using this priority ladder (this is the same ladder complete mode
falls back to when fenets is absent — see `eco_netlist_studier.md` Priorities 1–4):

1. **Register Output / Neighbour-DFF (MANDATORY Priority 1 for any register signal)** — If the
   signal is declared as a register (`reg <sig>`) in RTL, find its register instance `<sig>_reg`
   (or merged multi-bit bank slice) and read its direct `.Q`/`.QN` output net per stage. **NEVER**
   use a downstream module output port or repeater net with the bare name `<sig>` — Formality cuts at
   register boundaries, so tapping downstream of the flop's direct `.Q` pin breaks formal equivalence.
2. **Direct name (Combinational / Primary Input only)** — If the signal is a primary input port or
   purely combinational wire, decompress the stage netlist once and grep for the bare RTL/`old_net` name.
   If it exists as a real wire/pin, use it. (Synthesize usually matches RTL names directly.)
3. **Driver trace** — find the cell that *drives* the combinational signal in Synthesize:
   ```bash
   zcat PreEco/Synthesize.v.gz | grep -nE "\.(Q|Z|ZN)\s*\(\s*<signal>\s*\)"   # → driver inst
   ```
   then locate that **same instance name** in PrePlace/Route and read its output-pin net — that is
   the per-stage gate net (survives P&R even when the net was renamed).
4. **`eco_resolve_synth_internal.py`** — for a synthesis-internal net whose driver chain is absent
   in P&R, call the resolver (backward driver / forward consumer trace) and take its per-stage net.

**Prefer `eco_cone_trace.py` for steps 2–4** — it does the driver/consumer trace and register-anchor
resolution deterministically (built on the complete-gate-boundary parser, so it never mis-reads a
buffer/inverter):
```bash
python3 script/eco_scripts/eco_cone_trace.py resolve \
    --netlist <REF_DIR>/data/PreEco/<Stage>.v.gz --module <module_name_per_stage> --signal <signal>
# -> RESOLVED_NET=<net>   (or UNRESOLVED — then leave NET-ABSENT-IN-STAGE, do NOT guess)
```

Record the resolved names into the study entry exactly as complete mode does:
`actual_wire_<stage>`, `cell_name_per_stage`, `pin_per_stage`, `module_name_per_stage`,
`port_connections_per_stage`. **Process only the stages in `STAGES`** — the subset of
`{Synthesize, PrePlace, Route}` whose `<REF_DIR>/data/PreEco/<Stage>.v.gz` exists (Synthesize always;
PrePlace/Route only if provided — a Synthesize-only run is valid). **Do NOT flag `NET-ABSENT-IN-STAGE`
for a stage that was not provided** — it is simply absent by design, not unresolved. Populate every
present stage when you can resolve it; if a *present* P&R stage cannot be resolved after the ladder,
leave a `NET-ABSENT-IN-STAGE` marker — the orchestrator's Step 3c runs `eco_resolve_synth_internal.py`
to clean those up.

## Polarity — MANDATORY (there is no fenets `(+)/(-)` to tell you)
In complete mode Step 2 hands the studier FM-authoritative polarity (`(+)` = same, `(-)` =
complement). Simple mode has none, so **before binding any resolved net as an input** (mux select,
AND-enable, gate input, wire_swap old_net), determine whether it carries the signal or its
**complement** using `eco_cone_trace.py polarity` — inversion-counting back to a known-good reference
(the signal's **source register Q**, which is true-polarity by definition):
```bash
python3 script/eco_scripts/eco_cone_trace.py polarity \
    --netlist <REF_DIR>/data/PreEco/<Stage>.v.gz --module <module> \
    --target <resolved_net> --ref <source_reg_Q_net>[,<other_true_ref>]
# -> POLARITY=TRUE|INVERTED|UNDETERMINED inv=<n>
```
Act on the result:
- **TRUE** → use the net as-is.
- **INVERTED** → the net carries the complement; either bind the un-inverted source, or add one
  `INV` (`n_eco_<jira>_*` output) and bind that — record it in the entry.
- **UNDETERMINED** → the tracer could not prove it through a pure buffer/inverter chain. **STOP and
  flag this change** (`polarity_undetermined` in the entry) — do NOT guess. Without FM to catch a
  wrong-polarity insert, guessing is how simple mode silently corrupts a netlist. Re-derive the
  correct reference net, or hand this change to complete mode.

Do this **per stage** — polarity can differ across Synthesize/PrePlace/Route because P&R inserts
inverter/buffer chains independently. Never carry a Synthesize polarity verdict to Route.

## What you emit vs what the emitters emit
Same division of labour as complete mode: **you** build the base skeleton (locate cells, confirm the
`old_net` sits on the expected pin, set per-stage names, and hand-build only the entries the
emitters do NOT own). The deterministic emitters (run by the orchestrator right after you, WITHOUT a
rename map) own: equality-decode combinators, `priority_force` cones, `comb_net_force` /
`reg_guard_delta` cone rebuilds, uniquified-family replication, and SI/SE + P&R-cell finalize. **Do
NOT hand-build those** — just make sure each such change has its `module_name` + `old_net` +
`target_register`/`term_op`/`branch_assigns` fields so the emitter can ground it structurally.

## New-DFF assembly (`new_logic_dff`) — structural, no fenets
For **every** `new_logic_dff` change, assemble the flop with the same wrapper complete mode uses
(`eco_emit_dff_entry.py`) — the wrapper is what guarantees the chain topology, the `SE=SI=1'b0`
invariant, and the DFF entry shape — but feed it a **structural (empty) rename map** so it resolves
the clock/scan pins from the netlist instead of fenets:
```bash
# 1. write a one-time empty rename map (structural fallback trigger)
echo '{}' > <AI_ECO_FLOW_DIR>/<TAG>_empty_rename_map.json

# 2. slice the single change, then emit the DFF entry
python3 -c "import json; d=json.load(open('<AI_ECO_FLOW_DIR>/<TAG>_eco_rtl_diff.json')); \
    print(json.dumps([c for c in d['changes'] if c.get('target_register')=='<TARGET_REG>'][0]))" \
    > /tmp/<TARGET_REG>_change.json
python3 script/eco_scripts/eco_emit_dff_entry.py \
    --rtl-change /tmp/<TARGET_REG>_change.json --ref-dir <REF_DIR> \
    --rename-map <AI_ECO_FLOW_DIR>/<TAG>_empty_rename_map.json \
    --tag <TAG> --jira <JIRA> --tile-module <tile_module_per_stage> \
    --base-dir <AI_ECO_FLOW_DIR> --output <AI_ECO_FLOW_DIR>/<TAG>_eco_dff_entry_<TARGET_REG>.json
```
`resolve_cp_per_stage` automatically uses the **register-instance neighbour anchor** (finding a same-domain DFF
in the host module) to assign the region-correct clock in PrePlace and Route (e.g. post-CTS clock leaf), falling
back to bare clock only if verified. Scan stitching stays `SE=SI=1'b0`.

## Internal Register / CSR Port Connections (Check 60)
When wiring a previously unconnected output pin on an internal register wrapper (e.g. `.oQ_<name>`):
- Check if the underlying register module has internal instances with `iQ_<name>` (readback mux) and `oQ_<name>` (register array).
- If so, emit companion `port_connection` entries (NEVER `change_type: "rewire"`) for both internal instances to wire them to the active register signal,
  preventing undriven `X` in Formality.
- **Companion entry schema**:
  ```json
  {
    "change_type": "port_connection",
    "module_name": "<parent_wrapper_module>",
    "instance_name": "<child_internal_instance>",
    "child_module_name": "<child_internal_module>",
    "port_name": "<iQ_or_oQ_port_name>",
    "net_name": "<active_signal_net>",
    "net_name_before": {
      "Synthesize": "<placeholder_net_e.g._0>",
      "PrePlace": "<placeholder_net_e.g._0>",
      "Route": "<placeholder_net_e.g._0>"
    }
  }
  ```

## Mandatory Scalar Net Naming (Check 63)
- In gate-level netlists (Synthesize, PrePlace, Route), **ALL generated ECO nets MUST be 1-bit scalar net names** (e.g. `n_eco_<jira>_nxtd_<bit>_`, `n_eco_<jira>_<name>_<bit>_`).
- **NEVER use bracketed vector syntax** like `net_name[0]` in study JSON or rewires. Referencing undeclared vector nets causes Verilog to infer 1-bit scalar nets, making Formality LEC fail on `read_verilog` with `FM-599`.

## Correctness (no FM safety net)
- **Cell types: copy from PreEco.** Grep the PreEco netlist for the needed function/family and copy
  the exact cell name (full VT/pitch suffix). Never invent one.
- **Polarity:** follow CRITICAL_RULES; prefer INV+AN2/OR2 primitives over a compound cell if the
  compound cell's polarity is uncertain.
- **Per-bit distinct gates**, **shared-chain = add parallel gates (never modify in place)**, and
  the scan/reset rules from CRITICAL_RULES all still apply.
- **Script-bug self-fix (simple mode = evaluation, don't hard-stop on a tooling bug).** If a
  deterministic helper you run (`eco_cone_trace.py`, `eco_resolve_synth_internal.py`,
  `eco_emit_dff_entry.py`, …) fails closed but you can confirm in the raw netlist/RTL that the case is
  legitimately resolvable, do NOT stop: copy the script to `/tmp/<name>_<TAG>.py`, make the minimal
  evidence-backed bug fix (never fudge data), re-run the `/tmp` copy, continue, and note
  `SCRIPT-SELF-FIX: <name> — <bug> → <fix>` in the RPT. Only STOP when the input is *genuinely*
  unresolvable/ambiguous (Rules 1–3 in `SIMPLE_ORCHESTRATOR.md`). See its Correctness-posture Rule 4.

Output: `<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json` with entries for ≥1 stage. STOP.
Do NOT run fenets or any hard-gate validator (the orchestrator spawns the simple verifier for you,
right after the emitters, to resolve/enrich your entries).
