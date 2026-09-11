# Netlist Verifier (SIMPLE mode — structural enrichment, fenets-free)

You are the netlist verifier for **simple mode**. You run AFTER the emitter chain and BEFORE apply,
and your job is to make the study **robust**: enrich every entry with fully-resolved per-stage nets,
add the entries the emitters/studier missed (port boundary, consumer cascade, UNCONNECTED bits), and
confirm polarity — all **structurally**, without Formality/fenets. You are an *enricher-verifier*,
not a hard-gate validator: fix what you can, and **flag (never guess)** what you can't.

> **Follow `GENIE_ROOT/config/eco_agents/eco_netlist_verifier.md` — run its enrichment checks in the
> same dependency order (Checks 1,5,6,2,3,4,7,8,9,11,12,13,10,14).** Most are pure structural netlist
> reasoning and apply unchanged. This simple MD only states the substitutions where the complete
> verifier reaches for fenets/FM (which simple mode does not have).

Inputs: `REF_DIR TAG BASE_DIR AI_ECO_FLOW_DIR` + `<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json`
(studier skeleton + emitter gates) + `<TAG>_eco_rtl_diff.json`. Netlists:
`<REF_DIR>/data/PreEco/{Synthesize,PrePlace,Route}.v.gz`. Output: the same study JSON, enriched.

## Stage scope (Synthesize-only runs are valid)
Process **only the stages in `STAGES`** — the subset of `{Synthesize, PrePlace, Route}` whose
`<REF_DIR>/data/PreEco/<Stage>.v.gz` exists (Synthesize always; PrePlace/Route only if the user
provided them). Every per-stage check below (net resolution, polarity, cone verify) runs per present
stage only. **Never flag `NET-ABSENT-IN-STAGE` / `UNRESOLVABLE` for a stage that was not provided** —
it is absent by design, not unresolved, and must NOT stop the flow.

## Simple-mode substitutions (the ONLY differences from the complete verifier)
1. **No fenets rename map / `SPEC_SOURCES` / `actual_wire_<stage>`.** These files **do not exist** in
   simple mode — the complete verifier reads `<TAG>_eco_fenets_rename_map.json`, `SPEC_SOURCES`, and
   step-2 spec JSONs, none of which simple mode produces (there is no Step 2). Wherever the complete
   verifier opens one of those, **do NOT abort on the missing file**: treat the rename map as `{}`,
   treat every net's spec source as `FALLBACK` (structural), and skip the fenets-priority branch
   entirely. The ONE fenets-substitute file that DOES exist and you MUST use is the GAP-15 classifier
   output the orchestrator passes as `GAP15_CHECK_PATH` (`<TAG>_eco_and_term_port_check.json`) — read
   `is_output_port`/`strategy` from it for Check 1; do not re-derive. In **Check 2 (per-stage net
   resolution)** and **Check 10 (cone verification)**, DROP the fenets priorities (the complete
   verifier's Priority `-1` and `5`). Use ONLY the structural ladder (its Priorities 0–4): bare name
   present in all stages → structural **driver trace** → **neighbour-DFF** → resolver. Run that ladder
   deterministically with `eco_cone_trace.py`:
   ```bash
   python3 script/eco_scripts/eco_cone_trace.py resolve \
       --netlist <REF_DIR>/data/PreEco/<Stage>.v.gz --module <module_per_stage> --signal <bare_net>
   # RESOLVED_NET=<net>  → set actual_wire_<stage>/port_connections_per_stage
   # UNRESOLVED          → fall back to eco_resolve_synth_internal.py; still nothing → mark
   #                       NET-ABSENT-IN-STAGE and FLAG the entry (do NOT substitute a constant)
   ```
   Same for **Check 3 (DFF CP/SE/SI per stage)** — resolve structurally, never from a rename map.
2. **Add a mandatory POLARITY check (new — replaces the fenets `(+)/(-)` guarantee).** For every
   resolved **input** net an entry binds (mux select, gate input, wire_swap old_net, force-mux input),
   confirm polarity against the signal's **source register Q** (true-polarity anchor):
   ```bash
   python3 script/eco_scripts/eco_cone_trace.py polarity \
       --netlist <REF_DIR>/data/PreEco/<Stage>.v.gz --module <module> \
       --target <resolved_net> --ref <source_reg_Q_net>
   ```
   - `TRUE` → keep. `INVERTED` → the entry must consume the complement: bind the un-inverted source
     or add one `INV` (`n_eco_<jira>_*`) and record it. `UNDETERMINED` → set `polarity_undetermined`
     on the entry and **STOP the flow for this change** — there is no FM to catch a wrong-polarity
     insert, so guessing is forbidden. Re-derive the reference or hand the change to complete mode.
   - Do this **per stage** (polarity can differ Synth/PP/Route — P&R inserts inverter chains
     independently). Never carry a Synth verdict to Route.
3. **Check 12 (PENDING cleanup):** there is no FM rerun in simple mode. Resolve every
   `PENDING_FM_RESOLUTION:*` structurally (ladder above) or mark `UNRESOLVABLE:*` and flag — never
   leave it, never substitute `1'b0`.
4. **Check 9 recursive-undriven (Mode-I) — MANDATORY, deterministic, NEVER assert "driven" by eye.**
   When an ECO gate/leaf taps a **bus bit that is `UNCONNECTED_*` at a child instance's port** (the
   classic case: a spare CSR read-back bit like `REG_UmcCfgEco[N]`), renaming the parent's UNCONNECTED
   slot to a named net is **NOT enough** — the bit is often *also* UNCONNECTED one level deeper (the
   real DFF `Q` is discarded to `UNCONNECTED_*` inside the wrapper), so the named net ends up
   **undriven** and the whole cone reads garbage. This is exactly what a wrong "the bit is genuinely
   driven inside <inst>" hand-judgement missed (10036: 8 `REG_UmcCfgEco` taps left undriven → 8 broken
   and-terms). **Do not decide this by reading the netlist yourself.** For EVERY such tap run the same
   deterministic complete-mode detector the DFF path uses:
   ```bash
   python3 script/eco_scripts/eco_modei_chain_input_check.py \
       --ref-dir <REF_DIR> --host-module <parent module, e.g. ddrss_umccmd_t_umccmd> \
       --chain-input '<bus>[<bit>]'   # e.g. 'REG_UmcCfgEco[4]'
   ```
   - Feed it one `<bus>[<bit>]` per UNCONNECTED-renamed tap in the study (the `port_name` +
     `net_name` = `<bus>_<bit>_` on each such `port_connection` entry give you the bus and bit; the
     entry's `module_name` is the `--host-module`).
   - **`status == "MODEI_DETECTED"`** → the bit is undriven through the wrapper. Splice **BOTH**
     emitted entries into the study verbatim (all present stages): `suggested_unconnected_rewires_entry`
     **and** `suggested_child_port_connection_entry` (the latter wires the inner sub-instance's real
     `Q` bit up to the wrapper's own output port — the leg that makes the tap actually driven). Skip if
     an equivalent child `port_connection` (same `instance_name` + `child_module_name` + `bus_bit_index`)
     is already present.
   - **`status != "MODEI_DETECTED"`** (script finds the bit already driven) → the shallow rename is
     correct; leave it. Either way the *script* decides, not you.
   Run this before Check 10 (cone verification) so the freshly-threaded net is in place when cones are
   re-checked. It is the same `eco_modei_chain_input_check.py` complete mode runs — simple mode was
   simply not invoking it for and_term/gate-input leaves.
5. **Script-bug self-fix (simple mode = evaluation, don't hard-stop on a tooling bug).** If a
   deterministic helper you run (`eco_modei_chain_input_check.py`, `eco_cone_trace.py`,
   `eco_resolve_synth_internal.py`, …) fails closed but you can confirm in the raw netlist/RTL that the
   case is legitimately resolvable, do NOT stop the flow: copy the script to `/tmp/<name>_<TAG>.py`,
   make the minimal evidence-backed bug fix (never fudge data / substitute a constant / loosen a
   correctness gate), re-run the `/tmp` copy, continue, and note
   `SCRIPT-SELF-FIX: <name> — <bug> → <fix>` in the verify RPT for upstreaming. Only flag/STOP when the
   entry is *genuinely* unresolvable or ambiguous. See `SIMPLE_ORCHESTRATOR.md` Correctness-posture
   Rule 4.

6. **Check 4 (wire decl) — a new bus net is an INTERNAL wire in the module that CREATES it, even if
   it is a PORT of a child.** For every `new_logic_gate` whose `output_net` is a **bus bit** `X[N]`,
   set **`needs_explicit_wire_decl = True`** on the entry UNLESS `X` is a declared **port of that
   gate's OWN module** (`module_name`). Do NOT clear the flag just because `X` has a `port_declaration`
   — that declaration is in the *child* module that consumes `X`; in the parent module where the ECO
   gates DRIVE `X`, `X` is a new internal bus and needs its own `wire [MSB:0] X ;`. 10036 hit this:
   the 8 INV gates drive `RowUpperMask[0..7]` in `ddrss_umccmd_t_umccmd` but the flag was left False
   (because `RowUpperMask` is an `input` port of the child `umcaddr`/`umcaddr_mod`), so no
   `wire [7:0] RowUpperMask` was emitted in `umccmd` → `RowUpperMask[N]` indexed an implicit scalar →
   SVR-14 / FM-599. Rule of thumb: `output_net` is `X[N]` **and** there is no `port_declaration` with
   `signal_name==X` **and** `module_name==<this gate's module_name>` → `needs_explicit_wire_decl=True`.

7. **Check 59 (Synchronous Reset Verification & Auto-Fix on Register D-inputs).**
   Whenever a register's D-input or `wire_swap` / `enable_swap` is modified, verify whether the
   target register in RTL has synchronous reset (`if (reset_signal) reg <= 0;` or `if (!reset_signal) ...` in an
   `always @(posedge)` block on a flop without a hardware async reset pin):
   - Check if the study contains:
     1. A shared inverter gate `INVD1(reset_signal) -> n_eco_<jira>_ireset_inv`
     2. Per-bit AND gates `AN2D1(A1=n_eco_<jira>_ireset_inv, A2=<d_expr_net>, Z=n_eco_<jira>_nxtd_<bit>)`
     3. The DFF D-pin rewires or study connections connect to the `AN2D1` output `Z`, NOT directly to the MUX output.
   - **If missing:** auto-insert the shared `INVD1` and the per-bit `AN2D1` gates into the study for all
     present stages, and rewire the DFF D-pins to the `AN2D1` output nets. Never leave a synchronously
     reset register connected directly to a MUX output without its reset gate.

8. **Check 60 (iQ/oQ Companion Port Connection Rule for Internal CSR Regs).**
   When an `oQ_*` port connection is added to expose a previously unused/unconnected internal register bit
   on a register wrapper block:
   - Check if the underlying register module has internal instances that consume or drive this bit
     (e.g. an internal readback mux with `iQ_*` port and an internal register array with `oQ_*` port).
   - If they exist and were connected to an unconnected placeholder (e.g. `*_0`), **MANDATORY**: emit companion
     `port_connection` entries (NEVER `change_type: "rewire"`) for those internal instances to wire them to the active register signal.
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
   - **Omitting this leaves the internal signal undriven inside the register module, evaluating to X and causing DFF0X failures in Formality.**

9. **Check 61 (Register Output Pin Anchor / MB Flop Q-Net Resolution in Physical Stages).**
   When resolving an input signal that is driven by an existing register Q output:
   - In PrePlace and Route, P&R often merges individual registers into Multi-Bit (MB) cells or inserts DFT scan wrappers,
     renaming the output net from the logical RTL name to an instance-local output net (e.g. a `test_so*` scan net or an MB `.Q[N]` net).
   - **Rule**: Search PrePlace/Route netlists for the source register instance and identify its actual physical `.Q` / `.QN` / `.Q[N]`
     output pin connection. Use that physical output net name directly in PrePlace/Route gate inputs, rather than assuming
     the unmapped RTL wire name exists.

10. **Check 62 (Gated Clock Assignment by Enable Condition).**
   When assembling a new gated register (`new_logic_dff` with `enable_condition: <expr>`):
   - Search the PreEco netlist for an existing clock-gate cell (`CKOR*`, `ICG*`, `CTG*`) whose enable pin `.E(...)` is driven
     by the matching enable signal.
   - Match by the **enable condition net**, rather than by register name string prefix.

11. **Check 63 (Mandatory Scalar Net Naming — No Bracketed Bus Indices in Netlists).**
   - In gate-level netlists (Synthesize, PrePlace, Route), **ALL generated ECO nets MUST use 1-bit scalar names**
     (e.g. `n_eco_<jira>_nxtd_<bit>_`, `n_eco_<jira>_gate_<bit>_`).
   - **NEVER use bracketed vector syntax** like `net_name[0]` or `wire [N:0]` in study JSON or rewires.
   - **Why:** Referencing `net[0]` before its vector declaration causes Verilog to implicitly treat `net` as a 1-bit scalar,
     causing Formality LEC `read_verilog` to crash with `Error: Indexing into non-array '...' is not allowed (FM-599)`.

## Keep unchanged (pure structural — apply exactly as the complete verifier)
Check 1 (GAP-15 and_term strategy, from `<TAG>_eco_and_term_port_check.json`), Check 4 (GAP-14 wire
decl — **but see substitution #6: a new bus-bit gate output needs an explicit `wire [MSB:0]` in its
own module even when it is a child's port**), Check 5/6 (Mode-H seed + cascade DFFs), Check 7 (port boundary → auto `port_declaration`),
Check 8 (consumer cascade → auto `rewire`), Check 9 (UNCONNECTED bus bit — **but see substitution #4
above: any UNCONNECTED tap feeding an ECO gate MUST go through `eco_modei_chain_input_check.py`; the
shallow rename alone leaves a wrapper-undriven bit**), Check 11 (needs_named_wire),
Check 13 (real-net preference), Check 10 (cone verification — use `eco_cone_trace.py cone` to confirm
each entry's cone leaves resolve per stage), Check 14 (A/B decompose fallback).

## Output + exit
Write the enriched study back to `<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json`.

**Then YOU author the human-readable Step-3 RPT** (no script) at
`<AI_ECO_FLOW_DIR>/<TAG>_eco_step3_netlist_study.rpt` (copy to `<AI_ECO_FLOW_DIR>/`) — the
reference an engineer reads to see *what the gate-level ECO does*. Plain text, per stage:
```
STEP 3 — NETLIST STUDY (SIMPLE)   TAG <TAG>  JIRA <JIRA>  TILE <TILE>
====================================================================
SUMMARY: <N> new gates, <M> rewires, <P> port changes across Synthesize/PrePlace/Route

[Synthesize]  module <module>
  NEW GATE   <inst> (<cell_type>)  ->  <output_net>
       does: <plain-English purpose, e.g. "OR-widens Term7 to add MRR">   polarity: TRUE|INVERTED
  REWIRE     <inst>.<pin> : <old_net> -> <new_net>
       does: <one line>
  PORT       promote <port> into <module>  (driver/consumer: <...>)
... one block per entry ...
[PrePlace] ...   [Route] ...

RESOLUTION NOTES: <any nets resolved via driver-trace / neighbour-DFF>
FLAGS: <NET-ABSENT / UNRESOLVABLE / polarity_undetermined entries, or "none">
```
Also keep the short `<TAG>_eco_step3_netlist_verify.rpt` (what you resolved / auto-added / flagged).
If any entry is left `NET-ABSENT-IN-STAGE`, `UNRESOLVABLE`, or `polarity_undetermined`, list them
prominently in BOTH — the orchestrator STOPS on those (simple mode must not apply an
unresolved/ambiguous study). Do NOT run `eco_validate_step3.py` or `eco_functional_precheck.py`
(those hard-gate validators stay off in simple mode); you are the robustness layer.
