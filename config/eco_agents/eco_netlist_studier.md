# ECO Netlist Studier — Collect Pass

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before doing anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract section in the parent orchestrator: `config/eco_agents/STUDY_ORCHESTRATOR.md` **§STEP 3 — Study PreEco Gate-Level Netlist** (initial Round 1 only). For per-round re-study fixes (Round 2+), use `eco_netlist_re_studier.md` instead. Do NOT read other STEP sections; they belong to other agents.

**Role:** For each ECO change, classify the change type, find the correct cell type from PreEco, assign instance names, confirm old_net presence, and write initial skeleton entries to `eco_preeco_study.json`. Per-stage net resolution, gap checks, port boundary analysis, and cone verification are handled by `eco_netlist_verifier` (spawned after this agent exits).

**Inputs:** REF_DIR, TAG, BASE_DIR, path to `<TAG>_eco_rtl_diff.json`, GAP15_CHECK_PATH, and a **per-stage spec source map**:
```
SPEC_SOURCES:
  Synthesize: <path>   ← initial or noequiv_retry spec
  PrePlace:   <path>   ← initial, noequiv_retry spec, or FALLBACK
  Route:      <path>   ← initial or fm036_retry spec
```
**CRITICAL: Use the spec file specified for each stage — do NOT use the same spec file for all stages.**

---

## OWNED OUTPUTS — single source of truth

| Path | When I write it | Format |
|---|---|---|
| `data/<TAG>_eco_preeco_study.json` | end of Phase 1 | JSON (skeleton — verifier enriches) |
| `data/<TAG>_eco_step3_collect.rpt`  | last action before exit | RPT (summary) |
| Copy of both → `AI_ECO_FLOW_DIR/`   | last action before exit | mirror |

Verifier (next agent) re-writes `eco_preeco_study.json` in-place with `port_connections_per_stage`, GAP checks, auto-added entries, and cone verification.

## INPUTS — what the orchestrator gives me

- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `AI_ECO_FLOW_DIR`
- `SPEC_SOURCES` — per-stage map: Synthesize / PrePlace / Route → fenets spec path
- `data/<TAG>_eco_rtl_diff.json` — Step 1 RTL diff classification
- `data/<TAG>_eco_fenets_rename_map.json` — Step 2 FM-resolved per-stage map (SOURCE OF TRUTH for Rule 32)
- `GAP15_CHECK_PATH` — pre-computed module-port-direct-gating decisions

## EXECUTION ORDER — flat checklist (process in this order)

```
Phase 0 — new_logic / port changes (complete ALL before Phase 1):
  □ 0.1   Classify cell type per change                  (was 0a)
  □ 0.2   Identify input signals (basic)                 (was 0b)
  □ 0.3   P&R alias detection per input                  (was 0b-ALIAS)
  □ 0.4   UNCONNECTED rename       [SKIP IF no UNCONNECTED_* in any input]
          (was 0b-UNCONNECTED)
  □ 0.5   Bus DFF expansion        [SKIP IF no change has is_bus_dff: true]
          (was 0b-BUS-DFF)
  □ 0.6   Bus gate expansion       [SKIP IF no change has is_bus_gate: true]
          (was 0b-BUS-GATE)
  □ 0.7   DFF entry via wrapper    [SKIP IF no new_logic_dff change]
          (was 0b-DFF)
  □ 0.8   Find cell type from PreEco                     (was 0c)
  □ 0.9   Use preferred_insertion_scope                  [SKIP IF unset]
          (was 0c-SCOPE)
  □ 0.10  Assign instance + output net names             (was 0d)
  □ 0.11  Record skeleton entry                          (was 0e)
  □ 0.12  Mark wire_swap dependencies                    (was 0f)
  □ 0.13  Process new_port → port_declaration            (was 0g)
  □ 0.14  Process port_connection                        (was 0h)
  □ 0.15  Process port_promotion   [SKIP IF hierarchical netlist (>1 module)]
          (was 0i)
  □ 0.16  Process enable_swap      [SKIP IF no enable_swap changes]
          (was Phase 0e)

Phase 1 — wire_swap (FM-driven, per stage):
  □ 1.0   PRE-PHASE — driver_substitution / intermediate_net_insertion
  □ 1.1   Read PreEco netlist per stage                  (was 1)
  □ 1.2   Find + extract cell instantiation block        (was 2–3)
  □ 1.3   Confirm old_net is present                     (was 4)
  □ 1.4   Basic new_net reachability                     (was 4b)
  □ 1.5   Verify output count per stage                  (was 5)
  □ 1.6   Cleanup temp files                             (was 6)

Exit:
  □ Sort JSON by PASS_ORDER, write to disk
  □ Write collect RPT
  □ Copy both files to AI_ECO_FLOW_DIR
  □ Exit — verifier is spawned next by orchestrator
```

## HARD RULES — break these = fail the round (top 5)

1. **SE/SI on new ECO DFFs = `1'b0` in ALL 3 stages.** Scan stitching is out of scope; DFT team handles it.
2. **Rule 32 polarity check.** Never use a bare RTL name if inverter-parity differs across stages. Use `actual_wire_<stage>` from rename_map, or FM-resolved `<cell>/<pin>` wire. Step 3 validator Check 38 hard-fails violations.
3. **Always use `eco_emit_dff_entry.py` wrapper for DFFs** — never call `eco_synth_chain.py` directly. Wrapper handles per-DFF prefix, chain decomposition, Mode-I detection, and validator-invariants.
4. **Phase 0 fully complete BEFORE Phase 1 starts.** wire_swap (Phase 1) depends on new_logic outputs (Phase 0) being in the study JSON first.
5. **`needs_explicit_wire_decl: true` ONLY on output pins (ZN/Z/Q).** Setting it on input pins causes SVR-9 duplicate wire declaration ABORT.

## I HAND OFF TO

- `eco_netlist_verifier` — spawned by orchestrator after I exit. Reads my JSON, enriches every entry with `port_connections_per_stage`, GAP checks (GAP-15/14/5/18/CTS-1/CTS-2), auto-adds port_declarations / consumer rewires / UNCONNECTED renames, and runs cone verification.

---

## How to Read the fenets_spec File

The `<fenets_tag>_spec` file uses `#text#` / `#table#` block markers. FM find_equivalent_nets output appears in `#text#` blocks. **Polarity rule:** Only use `(+)` impl lines. Lines marked `(-)` are inverted nets — never use them. If a net only returns `(-)` results, treat it as `fm_failed`.

Results are grouped by target — parse each block separately:
```
TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl
TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize
TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace
```

---

## How to Collect ALL Qualifying Impl Cells Per Net

Apply ALL four filters to every FM impl line:

| Filter | Keep | Skip |
|--------|------|------|
| **F1 — Polarity** | `(+)` | `(-)` |
| **F2 — Hierarchy scope** | Path contains `/<TILE>/<INST_A>/<INST_B>/` | Sibling module or parent level |
| **F3 — Cell/pin pair** | Last path component matches `^[A-Z][A-Z0-9]{0,4}$` | Long signal name (bare net alias) |
| **F4 — Input pins only** | A, A1, A2, B, B1, I, D, CK, etc. | Z, ZN, Q, QN, CO, S (output pins) |

**After filtering: write the complete qualifying list before studying any cell. JSON must contain exactly this many entries.**

### Extracting cell name and pin from impl line:
```
i:/FMWORK_IMPL_<TILE>/<TILE>/<INST_A>/<INST_B>/<cell_name>/<pin> (+)
```

### GAP-1 — MANDATORY: Convert FM cell/pin path to actual wire name

FM returns `i:/FMWORK.../<cell_name>/<pin_name>` — this is a LOCATION address, NOT a valid Verilog net name.
1. Extract `<cell_name>` from the path
2. `grep -m1 "<cell_name>" /tmp/eco_study_<TAG>_<Stage>.v`
3. Read `.<pin_name>(<actual_wire>)` from that block
4. Use `<actual_wire>` as the net name — never use `<cell_name>/<pin_name>`

If `<actual_wire>` not found in PreEco → try other PreEco stages → if still not found → use RTL signal name from `old_token` or `new_token` as fallback.

---

## Phase 0 — Process new_logic and new_port Changes FIRST

**MANDATORY ORDER: complete ALL Phase 0 entries before starting Phase 1.**

Process ALL entries in `changes[]` in this exact order:
1. `"new_logic"` / `"and_term"` → gate/DFF insertion (steps 0a–0i)
2. `"new_port"` → `port_declaration` study entry (step 0g)
3. `"port_connection"` → `port_connection` study entry (step 0h)
4. `"port_promotion"` → `port_promotion` study entry (step 0i — flat netlist only)
5. `"wire_swap"` → **skip here** (handled by FM find_equivalent_nets in Phase 1)

**MANDATORY — `and_term` gate selection from FM polarity:**

The gate type (NOR2 vs INR2) is determined by the FM `(+)/(-)` polarity of the old driver's qualifying impl line from the Step 2 fenets rpt — NOT from `old_driver_inverting` in rtl_diff (that is a cell-type-prefix estimate only):
- FM `(-)` polarity → renamed output = `~old_expression` → use `NOR2(renamed, new_term)`
- FM `(+)` polarity → renamed output = `+old_expression` → use `INR2(renamed, new_term)`

Update `old_driver_inverting` in the study entry to match the FM polarity (true for `-`, false for `+`).

**MANDATORY — `and_term` companion rewire:**

For every `and_term` NOR2/INR2 gate whose A1 input is a renamed intermediate net (e.g. `eco_<jira>_andterm<N>_orig`), emit a companion `rewire` entry that renames the original driver output: `old_token → eco_<jira>_andterm<N>_orig`, per stage using the rename_map. Without this rewire the intermediate net is undriven → A1 floats → FM sees globally unmatched cone inputs → thousands of failures.

Do NOT interleave Phase 1 (wire_swap/FM) processing with Phase 0. Phase 1 depends on Phase 0 outputs being complete (new_logic output nets must exist before wire_swap FM queries are interpreted).

**CRITICAL: For hierarchical PostEco netlists, `new_port` and `port_connection` changes require explicit port list updates and instance connection additions.**

**`port_promotion` — FLAT NETLIST ONLY:** Only when `grep -c "^module " Synthesize.v` = 1. If hierarchical use `port_declaration` + `port_connection` instead.

---

### Phase 0.1 — Classify the new cell type  (was 0a)
> **DONE WHEN** every change has `change_type` set to `new_logic_dff` / `new_logic_gate` / `port_declaration` / `port_connection` / `port_promotion` / `enable_swap`, AND DFF entries have `reset_polarity` recorded.

From RTL diff `context_line`:
- `always @(posedge <clk>)` with reset/data pattern → **DFF** (sequential)
- `wire/assign <signal> = <expr>` → **combinational gate**
- Bare `reg <signal>` with no always block → skip

**For DFF: extract reset polarity (MANDATORY):**
- `if (~<rst>)` or `if (!<rst>)` → active-low reset → `reset_polarity: "active_low"` (DFF uses RN pin)
- `if (<rst>)` → active-high reset → `reset_polarity: "active_high"` (DFF uses R pin)
- No reset clause → `has_sync_reset: false`

Record `reset_signal: <rst_name>`, `reset_polarity` in the DFF entry. Used in 0c to match the correct DFF reset pin type.

### Phase 0.2 — Identify input signals (basic)  (was 0b)
> **DONE WHEN** every gate input net is identified and module-scope-verified, and `input_from_new_port` flag is set wherever applicable.

Parse `context_line` for clock/reset/data (DFF) or input signals (combinational).

**MODULE-SCOPE net verification (NOT whole-file grep).** Scope every net check to the declaring module (`entry["module_name"]`); a net only declared inside a child module is inaccessible at the parent — using it causes SVR-14 / FM-599 ABORT on all 3 stages.

```bash
# WRONG (global): grep -cw "<net>" /tmp/eco_study_<TAG>_Synthesize.v
# CORRECT (scoped):
awk '/^module <module_name>\b/,/^endmodule/' /tmp/eco_study_<TAG>_Synthesize.v | grep -cw "<net>"
```

Use `<module_name>` from `change.declaring_module` (or derived from `instance_scope`).

**Bus indexing scope check** — for any net `name[N]`, verify the base is declared as multi-bit within module scope. If not, `[N]` causes SVR-14. Find the scalar wire at bit[N] in the port bus (`.<port>({ a, b, c })` is MSB→LSB, so bit[0] = last element):

```bash
awk '/^module <module_name>\b/,/^endmodule/' /tmp/eco_study_<TAG>_Synthesize.v \
  | awk "/<base_name>/,/\)/" | grep -oP '\{\K[^}]+' | tr ',' '\n' | sed 's/\s//g' \
  | awk "NR==(total_bits - N)"
```

If base not bus-declared → record `input_from_change: <N>`. Full per-stage resolution + bus validation lives in eco_netlist_verifier; record what Synthesize allows here.

**New-port dependency flag** — when a chain input matches a `signal_name` from any `new_port`/`port_declaration`/`port_promotion` in the same change set, set `input_from_new_port: "<signal_name>"` so eco_perl_spec.py skips the PostEco existence check on that pin (port doesn't exist until Pass 2):

```python
new_port_signals = {c.get('new_token') or c.get('signal_name','')
                    for c in rtl_diff['changes']
                    if c.get('change_type') in ('new_port','port_declaration','port_promotion')}
for pin, net in port_connections.items():
    if net in new_port_signals:
        entry['input_from_new_port'] = net
```

### Phase 0.3 — P&R Driver Alias Detection  (was 0b-ALIAS)
> **DONE WHEN** every non-ECO input net has its per-stage value resolved from rename_map OR neighbor-DFF OR module-body grep, and Rule 32 polarity pre-check has run on every bare-RTL-name pick.

P&R renames DFF outputs (CTS/optimization in Route). A wire may exist in scope but be undriven → FM `X` → DFF0X. For every non-ECO input net, verify it is driven in each stage and record per-stage aliases.

**Rule** — for each input net (skip `n_eco_*` and `new_port_signals`):
0. **Rule 32 pre-check (MANDATORY, polarity-aware — see CRITICAL_RULES.md Rule 32).** Two sub-cases:
   - (a) **Bare RTL name missing from current module scope but exists in file:** emit `port_declaration` adding `<net>` as `input` + matching `port_connection` entries up to the visible scope. Use the bare name.
   - (b) **Bare RTL name exists in current module scope:** check fenets rename_map for `actual_wire_<stage>`. If present, USE IT VERBATIM (polarity-correct by construction). If absent, the bare RTL name is OK ONLY when inverter-parity from the bare wire to the nearest DFF.Q matches across all 3 stages. If parity differs in any stage, use FM's resolved `<cell>/<pin>` wire instead of the bare name (Step 3 Check 38 catches violations). Hazard pattern: P&R drive-strength buffer chains (odd INV count between MB-merged DFF.Q and port wire) flip polarity of the port-named wire — same name across stages, opposite logical value in the buffered stage → FM cone divergence.
1. Check driver in stage scope: `grep -P '\.(Q|Z|ZN|ZN1|CO|S)\s*\(<net>\s*\)'`. Driven → use as-is.
2. Not driven → find the Synthesize driver instance, locate same instance in P&R stage, read its output pin → that's the alias.
3. Driver absent in P&R → one hop upstream (grep driver's inputs in Synth, find those in P&R, read output).
4. Still absent → **CTS buffer search**: any cell in the module whose output is sole driver of a net feeding the same downstream consumers as `<net>` in Synthesize. CTS makes buffer chains with tool-generated names — accept the first driven net reaching the same fanout.
5. Aliases differ across stages → set `entry["net_per_stage"][pin] = {Syn, PP, Route}`.

**CRITICAL — this rule applies to EVERY non-output pin of EVERY chain gate, not just the primary data input (A1).** Scalar inputs to chain gates — including reset signals (B1), MUX selects (S), and any other non-ECO net — are equally subject to CTS renaming across stages. Using a bare RTL name (e.g. `IReset`) for all stages when the rename map has different per-stage values causes DFF0X or cone mismatch in PP/Route. The `eco_emit_dff_entry.py` wrapper performs this resolution automatically after building the chain entries; sub-agents must NOT override the per-stage values with bare names.

**Per-stage resolution priority** (all ECO input pins, anything except `{Z, ZN, ZN1, Q, QN, CO}`):

1. **`<BASE_DIR>/data/<TAG>_eco_fenets_rename_map.json`** — Step 2 (eco_fenets_runner) builds the authoritative per-stage map for every queried signal. If the pin's logical signal is in the map, USE ITS VALUES VERBATIM. Single source of truth.
2. **Neighbor-DFF inference** (only when signal absent from map): find a pre-existing DFF in same module scope whose Synth value of the same pin matches the ECO logical signal; copy its per-stage net verbatim, including CTS-renamed names.
3. **Module-body grep for internal wire**: when a chain leaf is a local internal wire driven by a sync-flop inside the host module, grep each stage's PostEco for `.Q(<net>)` on the source DFF instance:

```python
def find_driver_in_module(host_mod_text, original_signal, source_dff_inst):
    m = re.search(rf'\b{re.escape(source_dff_inst)}\b\s*\([^)]*?\.Q\s*\(\s*(\w+)\s*\)', host_mod_text, re.DOTALL)
    return m.group(1) if m else original_signal
```

NEVER force the Synth name across all stages — each path produces FM-correct per-stage values. **SE/SI on new ECO DFFs: hardwire `1'b0` in ALL stages (Synth/PP/Route).** Scan stitching is out of scope; DFT team handles it.

**Path 1 vs Path 3:** rename_map is FM-anchored to a combinational path through CTS inverters; module-body grep may resolve a topologically-equivalent net (e.g., a `.Qn` of a registered replica). Both can be FM-equivalent — choose based on consuming gate; FM equivalence is the arbiter.

Log: `PR_ALIAS: <gate>.<pin> Syn=<net> PP=<alias> Route=<alias>` or `PR_ALIAS_SAME`.

**Mode H Route fallback — condition gate chain inputs unavailable in Route:**

When Path 1 returns a Route value that's actually Synth-only (`zgrep -c "<route_value>" PreEco/Route.v.gz` = 0), the signal doesn't exist in Route. Do NOT use the Synth fallback — it will FAIL FM. Instead:

1. Search same run's `changes[]` for `new_port` / `port_promotion` whose signal is logically related (same module scope, same domain).
2. If substitute ECO port exists in `PreEco/Route.v.gz`, use it as Route value. Record `route_substituted_with_eco_port: true` + `original_signal: <unresolvable>`.
3. No substitute → set `confirmed: false` for Route entries only. Applier skips Route chain; FM will FAIL on Route; Round 2 handles.

Apply only to Route (Synth/PP already resolved via fenets fix1 ZBUF retry).

---

### Phase 0.4 — UNCONNECTED rename  (was 0b-UNCONNECTED)
> **SKIP IF** no gate input matches `^(SYNOPSYS_)?UNCONNECTED_\d+$` AND no chain leaf landed on UNCONNECTED at a parent's child-port-bus connection.
> **DONE WHEN** every UNCONNECTED_* in port_connections is replaced by an `n_eco_*` named wire AND `unconnected_rewires[]` has one entry per `(module, instance, port, bit)` tuple.

FM cannot trace `UNCONNECTED_*` / `SYNOPSYS_UNCONNECTED_*` across hierarchy → DFF non-equivalent. Any gate input matching `^(SYNOPSYS_)?UNCONNECTED_\d+$` must be renamed.

**Trigger ALSO fires on chain leaf inputs (MANDATORY) — auto-detected by `eco_modei_chain_input_check.py`** (invoked automatically by the §0b-DFF wrapper per chain leaf). Detection logic: for each chain leaf in `<bus>[<N>]` (or flat `<bus>_<N>_`) form, scan host module's gate-level body for child instance port-bus connections; if leaf's bus[bit] position lands on `UNCONNECTED_*` in the parent's `.<bus>({...})` concat, walk into the child module body to locate the sub-instance whose port-bus bit is also UNCONNECTED **and whose concat self-references the parent bus** (this discriminator distinguishes the actual driver from unrelated UNCONNECTED-at-bit-N hits in other ports). Helper emits per-stage UNCONNECTED literals + suggested study snippets that the wrapper splices verbatim. Without this trigger, chain inputs that look "resolved" via FM rename map (e.g. a deep gate-level driver name) are actually invisible at host scope because the parent DECLINED to wire them — Step 5 catches as `INPUT_UNDRIVEN`. **The studier no longer needs to grep/count/walk per leaf** — the wrapper does it deterministically.

**`named_net` format:** flat Verilog identifier `^[A-Za-z_]\w*$` only. For bus-bit semantics use flat-net escape `X_N_` (NEVER `X[N]` — bracket form is illegal in wire decls; valid only inside port_connections/concats). The applier auto-sanitizes brackets via `_sanitize_named_net()` (logs `AUTO_SANITIZED`), but emit the correct form directly — repeated AUTO_SANITIZED entries indicate violation.

**Scope:** each `unconnected_rewires` entry targets exactly ONE `(module, instance, port_name, bus_bit)` tuple. Do not emit N entries sharing the same `original`+`named_net` across N modules — that's a scope-leak symptom. Emit only what the ECO needs.

**MANDATORY — paired `port_connection` entry for every `unconnected_rewires` / `a1_unconnected_rename`:** Whenever you emit `unconnected_rewires` on a DFF entry OR `a1_unconnected_rename` on a gate entry, you MUST ALSO emit a SEPARATE `port_connection` study entry (per stage) that renames the parent's child-instance bus bit. The applier (`eco_perl_spec.py` + `eco_netlist_port_rewire.py`) does NOT introspect embedded fields — it only acts on `port_connection` change_type entries. Without the paired entry, the bus rename never lands in the netlist → gate input net stays UNCONNECTED → undriven → FM globally unmatched.

```json
{
  "change_type":       "port_connection",
  "parent_module":     "<host_module>",
  "instance_name":     "<child_instance_name>",         // e.g. REGCMD
  "child_module_name": "<child_module_type>",           // e.g. ddrss_umccmd_t_umcregcmd
  "port_name":         "<child_port_bus_name>",         // e.g. oQ_UmcCfgEco_UmcCfgEco
  "bus_bit_index":     <bit>,                           // e.g. 12
  "net_name":          "<named_net from rewires>",      // matches unconnected_rewires.named_net
  "net_name_before":   {<per-stage UNCONNECTED_*>}      // from original_per_stage
}
```

**Rule** — for each such net:
1. `named_net` — choose based on context:
   - **Default:** `"n_eco_<jira>_<rtl_hint>"` (sanitized from `new_token`/port/RTL). Same name across all stages, flat-net form.
   - **Exception — CSR/register output bus slots:** when the UNCONNECTED being renamed is on an output port of a register-file wrapper (e.g. `REGCMD.<port>[N]` or `REG.<port>[N]`) AND that renamed wire is the direct input to a new ECO gate, use the **flat form of the port name + bit**: `<port_name>_<N>_`. This preserves FM's compare-point traceability across Synth→PrePlace stages — the flat port form is recognized by FM as the sanitized form of the register output compare point itself. A generic `eco_{jira}_` prefix creates an extra indirection level that FM cannot resolve across stages, causing non-equivalent compare points on the ECO-modified DFF D-pins.
2. Find bus position **per stage independently** by scanning `.<port>( { ..., <UNCONNECTED_N>, ... } )`. Each stage assigns fresh UNCONNECTED names — locate by MSB-first bit index, not by name match.
3. Record `original_per_stage: {Synthesize, PrePlace, Route}` and `port_bus_instance_per_stage` (Route may add `_0` uniquification suffix). Do NOT hardcode instance — read from port_connection or grep PostEco scope.
4. Emit: `unconnected_rewires: [{original, original_per_stage, named_net, needs_explicit_wire_decl:true, port_bus_instance, port_bus_instance_per_stage, port_bus_name, port_bus_bit}]`. Use `named_net` in port_connections for all stages.

eco_perl_spec declares `wire <named_net>;` once, applies per-stage replacement in port bus `{ }`.

**PARENT SCOPE (default):** rename at the module scope where the ECO gate is inserted. Inventing fresh names inside the child breaks FM's clock/cone analysis.

**EXCEPTION — child output port internally undriven (auto-detect, MANDATORY in studier):** if the renamed bus is `output` of the child AND a child sub-instance has `UNCONNECTED_*` at the same bit, the parent rename leaves the port undriven → FM `X` → DFF0X.

Algorithm: walk the child module body, find any sub-instance whose output bus has `UNCONNECTED_<N>` at the same `bus_bit_index` (MSB-first `{}` parse). Emit a SECOND `port_connection` inside the child module:
- `module_name`: child module name
- `instance_name`: the sub-instance whose bus output is undriven
- `port_name`/`bus_bit_index`: same bit position
- `net_name`: `<port_name>[<bit>]` (self-loop to OWN output port — legal in port_connections only). Pair with the matching `<port_name>_<bit>_` flat-net form in `unconnected_rewires.named_net`.
- `net_name_before`: per-stage map of internal UNCONNECTED placeholders

This is wire-up (real driver), not invention. Engineers do this manually when a register output bit is spare.

Log: `UNCONNECTED_RENAME: <N_syn>/<N_pp>/<N_rt> → n_eco_<jira>_<hint> | bus=<inst>.<port>[<bit>]`

**MANDATORY port_connection schema for bus-position renames** — eco_netlist_port_rewire dispatches to `_apply_bus_rename` on these exact fields:

```json
{
  "change_type": "port_connection",
  "instance_name": "<submodule_instance>",
  "child_module_name": "<full submodule type name>",
  "port_name": "<bus_port_name>",
  "bus_bit_index": <int — MSB-first>,
  "net_name": "<n_eco_jira_named>",
  "net_name_before": {"Synthesize": "<orig_syn>", "PrePlace": "<orig_pp>", "Route": "<orig_rt>"},
  "net_name_after": "<n_eco_jira_named>",
  "force_reapply": true
}
```

**`child_module_name` MANDATORY on EVERY `port_connection`** (not only bus renames) — Step 3 Check 3e cross-checks `port_name` against the child's port list. Missing child_module_name skips the check; missing port slips to FM as FE-LINK-7 ABORT. Whenever you introduce a new port on a child, also emit a `port_declaration` for it.

**`net_name_before` per-stage map REQUIRED** — eco_netlist_port_rewire prefers scope-search by exact old name (mode a). Bit-index parsing (mode b) is fallback only. Omitting `net_name_before` causes wrong-instance edits when multiple instances share the same port name.

**MANDATORY: read the actual PreEco netlist to obtain each `net_name_before` value.** Never infer or guess UNCONNECTED numbers — locate the `.<port>( { ... } )` concat in the PreEco netlist for the exact stage, parse it MSB-first, and record the literal string at that bit position. A wrong UNCONNECTED number causes the applier to SKIP the entry silently (validator Check 46 catches this).

**MANDATORY: all `net_name` values for bits of the same `(instance_name, port_name)` must use the same form** — either all flat (`_N_`) or all bracket (`[N]`). Use flat form when the wire is declared as a scalar in the parent module; use bracket only when the wire is an array. Mixed forms cause SVR-14 in FM (validator Check 47 catches this).

---

### Phase 0.5 — Bus DFF expansion  (was 0b-BUS-DFF)
> **SKIP IF** no change has `is_bus_dff: true`.
> **DONE WHEN** N per-bit DFF entries are spliced into study[stage] for all 3 stages, where N = resolved bus width.

When `is_bus_dff: true` on a `new_logic` change, the register is a vector type.
Gate-level synthesis produces N individual DFF cells (one per bit).

**Step 1 — Resolve bus width:**
```bash
python3 script/eco_scripts/eco_resolve_bus_width.py \
    --macro         <bus_width_expr>                    \
    --signal        <target_register>                   \
    --rtl-dir       <REF_DIR>/data/SynRtl               \
    --preeco-synth  <REF_DIR>/data/PreEco/Synthesize.v.gz \
    --output        data/<TAG>_eco_bus_width_<target>.json
```
Read `width` (integer N) from output. If `resolved: false` → log `BUS_WIDTH_UNRESOLVABLE` and emit a CRITICAL issue for the orchestrator. Record `bus_width_resolved: N` on the change entry in the study JSON.

**Step 2 — Emit N DFF entries via eco_emit_dff_entry.py:**
```bash
python3 script/eco_scripts/eco_emit_dff_entry.py \
    --rtl-change <change_json> --ref-dir <REF_DIR>      \
    --rename-map data/<TAG>_eco_fenets_rename_map.json  \
    --tag <TAG> --jira <JIRA> --tile-module ddrss_<TILE>_t \
    --base-dir <BASE_DIR>                               \
    --bus-width N                                       \
    --output data/<TAG>_eco_dff_entry_<target>.json
```
The wrapper emits N entries (`<target>_reg_<bit>_`) with per-bit D (`<d_src>[bit]`) and Q (`<target>[bit]`) nets, plus shared CP/SI/SE derived from a sibling DFF in the same clock domain.

**D-input gate chain — must also be per-bit (MANDATORY when chain is non-empty):**
If the bus DFF has a D-input gate chain (e.g., reset-baked `INR2(data, reset)` → D), that chain gate must be replicated N times with bit-indexed data inputs — one gate per DFF bit. Never emit a single scalar chain gate shared across all N bits: each bit's DFF connects to its own gate output, and each gate's data input is the bit-indexed form of the source bus signal (e.g., `data[bit]`). Scalar inputs such as reset signals are shared unchanged across all N gate entries.

**Per-stage net form for bus bit-indexed gate inputs (MANDATORY):**
Bus signal bits appear in different forms across stages — use the correct form per stage:
- **Synthesize:** bracket form is valid inside `port_connections` — use `<signal>[<bit>]`
- **PrePlace / Route:** bit-indexed wires use flat underscore-escaped form — use `<signal>_<bit>_`

Verify each per-stage form exists in the corresponding PreEco netlist: `zgrep -c "<signal>_<bit>_" PreEco/<stage>.v.gz`. If 0 occurrences in Route (P&R may have merged or renamed that bit), mark the gate entry with `input_from_new_port: "<signal>_<bit>_"` so the verifier skips the existence check and let eco_applier resolve at apply time.

**Step 3 — Splice all N entries per stage:**
```python
out = json.load(open(f'data/{TAG}_eco_dff_entry_{target}.json'))
for stage in ('Synthesize', 'PrePlace', 'Route'):
    study[stage].extend(out[stage])   # N entries per stage, no chain gates
```

Do NOT call `eco_expand_chains.py` for bus DFF changes — it skips them automatically.

### Phase 0.6 — Bus gate expansion  (was 0b-BUS-GATE)
> **SKIP IF** no change has `is_bus_gate: true`.
> **DONE WHEN** N per-bit gate entries are spliced into study[stage] for all 3 stages with `is_bus_gate_bit: true` set per entry.

When a `new_logic_gate` change has `is_bus_gate: true` (e.g. `wire [N:0] X = cond ? A : B`), synthesis produces N individual gate cells — one per bit.

**Step 1 — Resolve bus width** (same script as bus DFF):
```bash
python3 script/eco_scripts/eco_resolve_bus_width.py \
    --macro <bus_width_expr> --signal <output_net_base> \
    --rtl-dir <REF_DIR>/data/SynRtl \
    --preeco-synth <REF_DIR>/data/PreEco/Synthesize.v.gz \
    --output data/<TAG>_eco_bus_width_<output_net>.json
```

**Step 2 — Emit N gate entries.** For each bit 0..N-1:
- `instance_name`: `eco_<jira>_<gate_seq>_bit<bit>_`
- `is_bus_gate_bit: true`, `bus_bit_index: <bit>`
- `output_net`: `<signal_base>[<bit>]`
- Bus-width inputs: add `[<bit>]` suffix per entry
- Scalar inputs (1-bit signals): **shared unchanged** across all N entries
- **`module_name` and `instance_scope` MUST come from the PARENT `new_logic_gate` change** (the `is_bus_gate: true` entry), NOT from surrounding `and_term` or `wire_swap` entries which may be in a different module scope. The parent change's declaring module is where the bus signal's inputs are accessible. Using a different module (e.g. a child module where the `and_term` gates live) causes SVR-14 (signal not in scope) at FM elaboration time. Step 3 validator Check 40 enforces this.

**Step 3 — Splice N entries per stage** (same pattern as bus DFF):
```python
for stage in ('Synthesize', 'PrePlace', 'Route'):
    study[stage].extend(bit_entries_for_stage)  # N entries per stage
```

eco_perl_spec.py automatically emits `wire [N-1:0] <signal_base> ;` after detecting the N `is_bus_gate_bit` entries — no extra action needed.

**Step 4 — UNCONNECTED inputs on per-bit entries (MANDATORY):**

After expansion, check each bit entry's input pins. If any input matches `UNCONNECTED_*` or `SYNOPSYS_UNCONNECTED_*`, add `unconnected_rewires` to that entry — same fields as Phase 0.4. Phase 0.4 ran before Phase 0.6 and never saw these expanded inputs; without this step, the gate reads from an undriven net → `INPUT_UNDRIVEN`. Validator Check 43 enforces this.

### Phase 0.7 — DFF entry assembly via `eco_emit_dff_entry.py` wrapper  (was 0b-DFF)
> **SKIP IF** no `new_logic_dff` change (i.e., only gates / ports — no sequential insertions).
> **DONE WHEN** wrapper output JSON exists per target_register AND its per-stage entries are spliced into study[stage], AND `diagnostics.modei_check[]` shows no unhandled leaves.

For EVERY `new_logic` DFF change, invoke `eco_emit_dff_entry.py` ONCE and splice its per-stage output verbatim into `eco_preeco_study.json`. Do NOT call `eco_synth_chain.py` directly — the wrapper invokes it with the correct per-DFF prefix.

```bash
python3 -c "import json; d=json.load(open('data/<TAG>_eco_rtl_diff.json')); \
    print(json.dumps([c for c in d['changes'] if c.get('target_register')=='<TARGET_REG>'][0]))" \
    > /tmp/<TARGET_REG>_change.json

python3 script/eco_scripts/eco_emit_dff_entry.py \
    --rtl-change /tmp/<TARGET_REG>_change.json --ref-dir <REF_DIR> \
    --rename-map data/<TAG>_eco_fenets_rename_map.json \
    --tag <TAG> --jira <JIRA> --tile-module ddrss_<tile>_t \
    --base-dir <BASE_DIR> --output data/<TAG>_eco_dff_entry_<TARGET_REG>.json
```

Wrapper handles: D-input chain via `eco_synth_chain.py` from `d_input_expected_function` (engineer-style topology + per-DFF prefix); per-stage CP from rename map; DFF entry with `SE=SI=1'b0` in all 3 stages; **per-chain-leaf Mode-I detection via `eco_modei_chain_input_check.py`** (auto-emits `unconnected_rewires` + child-scope `port_connection` when a chain leaf bus-bit lands on UNCONNECTED at the parent's child-instance port-bus connection — no manual grep/walk needed); self-validation against Step 3 invariants. Diagnostics in output JSON: `diagnostics.modei_check[]` lists per-leaf status, `diagnostics.modei_entries_added` counts spliced child port_connections.

Splice per stage:
```python
out = json.load(open(f'data/{TAG}_eco_dff_entry_{target}.json'))
for stage in ('Synthesize', 'PrePlace', 'Route'):
    study[stage].extend(out[stage])
```

**Scan stitching is OUT OF SCOPE.** New ECO DFFs get `SE=SI=1'b0` in all 3 stages. DFT team handles scan integration. The wrapper does NOT pick siblings, build bridges, or emit scan plumbing. FM may flag new DFFs as scan-cone divergent in PP/Route — expected; AI flow is responsible only for FUNCTIONAL ECO correctness.

**Validator invariants the wrapper guarantees** (`eco_validate_step3.py`): 19 per-stage SI/SE wire exists (skips bridge / SE=1'b0); 27 per-stage CP same clock-root token (decorator-strip aware); 31 chain topology matches `eco_synth_chain.synthesize()` multiset; 33 DFF.D is a valid Verilog identifier.

**Combinational gates (non-DFF) — chains still use `eco_synth_chain.py`.** For a standalone `new_logic_gate` (rare — usually `wire_swap` and-term), call directly. Hand-decomposition FORBIDDEN — Check 31 hard-fails any cell-type multiset mismatch.

```bash
python3 script/eco_scripts/eco_synth_chain.py synthesize \
    --boolean "<RTL_BOOLEAN>" --inputs "<comma-separated names>" --jira <JIRA>
```

---

### Phase 0.8 — Find suitable cell type from PreEco  (was 0c)
> **DONE WHEN** every new_logic entry has `cell_type` populated from a matching PreEco neighbor, AND DFF entries have `reset_pin_used` decision + actual pin name OR have the reset baked into the D-input chain.

**Generic discovery — no hardcoded cell names or pin names.** Read module scope from PreEco Synthesize:
```bash
awk '/^module <declaring_module>/,/^endmodule/' /tmp/eco_study_<TAG>_Synthesize.v > /tmp/eco_module_scope.v
```

#### DFF with `has_sync_reset: true` — try reset-pin cell FIRST (preferred)

**`find_reset_capable_dff(scope_lines, reset_signal)`:**
1. Find a line `\.<pin>\(\s*<reset_signal>\s*\)` in scope.
2. Walk back to instance start (prev line ends `;` or blank).
3. Block contains `\.Q\(` → it's a DFF. Extract `cell_type` (first uppercase token on decl line) + `reset_pin_name` (pin from step 1). Return `(cell_type, pin)` or None.

**Found:** use it as DFF; set `reset_pin_used: true`, `reset_pin_name`, connect `reset_signal` to that pin, **remove the reset term from `d_input_gate_chain`** (DFF `.D` = last functional gate output).

```json
{"dff_cell_type": "<discovered>", "reset_pin_used": true,
 "reset_pin_name": "<discovered>", "reset_signal": "<rst>",
 "port_connections": {"<data>": "n_eco_<jira>_d<last>", "<clk>": "<clk_net>",
                      "<reset>": "<rst>", "<q>": "<target_register>"}}
```

**Not found — bake reset into D-input chain.** Set `reset_pin_used: false`; log `RESET_PIN_FALLBACK: no DFF in scope <mod> uses <reset> — baking (GAP-CTS-2 risk in Route)`.

**MANDATORY chain extension when `reset_pin_used: false`** (rtl_diff_analyzer Step E strips the reset term so it can be baked here):
- Chain non-empty → append reset-gating tail.
- Chain empty (`d_input_resolved_net` set, e.g. direct-wire `REG_X[i]`) → BUILD chain from `d_input_resolved_net` (and per-stage UNCONNECTED variants) as AND2 source. NEVER invent undriven `n_eco_*`.

**INR2 in rtl_diff d_input_gate_chain — replace with shared INV + AND2:**
If the rtl_diff `d_input_gate_chain` contains an `INR2` gate with the reset signal as B1 input, do NOT emit the INR2 directly. Instead:
1. Check if a shared INV for this reset signal already exists in the study JSON for this module (e.g. `eco_<jira>_<module>_rst_inv`). If not, emit ONE shared INV: `INV(<reset>) → eco_<jira>_<module>_rst_inv`.
2. Replace the INR2 with `AND2(data_input, eco_<jira>_<module>_rst_inv) → output_net`.
This ensures all DFF chains in the same module share one INV output (stage-stable ECO net) rather than each referencing the bare reset signal directly.

Tail: `INV(<reset>) → eco_<jira>_<reg>_rst_inv`; combiner `AND2(eco_<jira>_<reg>_rst_inv, chain_tail) → n_eco_<jira>_d<N+1>`. **The INV output `eco_<jira>_<reg>_rst_inv` is a new ECO net — stage-stable, no CTS renaming needed. Use it as the inverted-reset input for ALL gates in this DFF's chain that need `~<reset>`.** Never reference `<reset>` directly as a negated input; always invert it once into a shared ECO net first. For bus DFFs with N bits: emit ONE shared INV for `<reset>`, then N AND2 gates each consuming `eco_<jira>_<reg>_rst_inv` as A1. Update `d_input_net` to the AND2 output → DFF `.D`. Per-stage resolution: the INV input `<reset>` is the sole reference to the bare reset name — resolve it per-stage via rename map (Rule 32). The AND2 inputs downstream only reference the ECO-internal net, so no per-stage resolution is needed for them.

**Self-check:** `has_sync_reset && !reset_pin_used && no chain references <reset>` → bake-in skipped → fix before writing JSON. DFF must never lack a reset path.

**Why prefer reset-pin:** CTS heavily replicates reset in Route; baked into the D-cone, FM can't trace through CTS-merged drivers → non-equivalent (GAP-CTS-2). The reset pin bypasses the combinational cone entirely.

#### DFF without sync reset (or fallback) — find any DFF in scope

`find_neighbour_dff(scope_lines)`: scan for a line containing `\.Q\(`, walk back to instance start (prev line ends with `;` or blank), return first uppercase token from the declaration line as `cell_type` (e.g. `SDFQD4AMDBWP...`).

#### Combinational gate

Determine function from RTL expression (`A & B` → AND2, `~A` → INV, …), then search PreEco for matching cell pattern.

**MANDATORY — extract actual pin names from PreEco instance (ALL pins):** `grep -m1 "<cell_type>" /tmp/eco_study_<TAG>_<Stage>.v`. Parse every `.<PIN>(` — these are the ONLY valid pin names. Never assume pin names from the gate function name.

### CELL OUTPUT PIN TABLE — MANDATORY REFERENCE

| Gate Function | Output Pin | Notes |
|--------------|-----------|-------|
| AND2, AND3, AND4 | `Z` | Non-inverting |
| OR2, OR3, OR4 | `Z` | Non-inverting |
| MUX2, MUX4 | `Z` | NOT `ZN` |
| XOR2 | `Z` | Non-inverting |
| INV | `ZN` | Inverting |
| NAND2, NAND3, NAND4 | `ZN` | Inverting |
| NOR2, NOR3, NOR4 | `ZN` | Inverting |
| XNOR2 | `ZN` | Inverting |
| IND2, IND3 | `ZN` | AND-NOT (inverting) |
| DFF, SDFF | `Q` | Sequential |

Verify output pin by examining an actual instance from PreEco — always authoritative over this table.

**GATE POLARITY VALIDATION (MANDATORY after 0c):** verify chosen gate_function polarity matches the RTL expression — `~(A & B)` → NAND2 (`ZN`); `~(A | B)` → NOR2 (`ZN`); `A & B` → AND2 (`Z`); `~(A[1] & ~A[0])` → NAND2(A[1], ~A[0]). On mismatch log `POLARITY_MISMATCH: chosen {x} but RTL requires {y}` and correct before writing study JSON.

**BUS CONSTANT EQUALITY DECODE (MANDATORY for `~(bus[N:0] == K)` conditions):** when a condition sub-expression is a negated equality against a fixed bit-pattern constant, the gate inputs MUST reflect each bit's value in K individually. For each bit position i: if K[i]==0 → the input to the NAND-N is `~bus[i]` (requires an INV cell for that bit before the NAND); if K[i]==1 → the input is `bus[i]` directly. **Never pass raw bus bits unconditionally into IND2/IND3 when K is not all-ones** — that computes `~(all bits AND)` = `~(bus==2'b11...1)`, not the intended constant. Step 3 validator Check BUS-CONST-DECODE catches this. Example: `~(bus[1:0]==2'b01)` → emit `INV(bus[1])` first, then `ND2(~bus[1], bus[0])`.

**CHAIN-LEVEL POLARITY (MANDATORY for chains ≥2 cells):** correct per-cell polarity is necessary but not sufficient — the COMPOSED Boolean must equal `d_input_expected_function`. Two traps per-cell checks miss: (1) a downstream NR/NAND flips an upstream input's effective polarity; (2) RTL has `~SIG`, picking `SIG` into a non-inverting cell silently drops the inversion.

**Rule:** do NOT hand-decompose multi-cell chains. The DFF wrapper invokes `eco_synth_chain.py`; for standalone chains call it directly (see §0b-DFF Combinational subsection). The synthesizer derives cell types AND input polarities from `d_input_expected_function` (correct by construction). Step 3 Check 31 hard-fails topology mismatch.

When an input must enter a non-inverting cell as `~SIG`, reuse an existing INV in the host module whose output is `~SIG` — do NOT add a redundant INV.

### Phase 0.9 — Use preferred_insertion_scope when set  (was 0c-SCOPE)
> **SKIP IF** no change has `preferred_insertion_scope` set in the RTL diff.
> **DONE WHEN** chain instance_scope set to the preferred child submodule AND last-gate output is registered as a new output_port on the child via port_declaration + port_connection at the parent.

Before assigning `instance_scope` for a gate chain, check `preferred_insertion_scope` from the RTL diff change JSON:

- Set → place chain INSIDE the child submodule. Last gate's output becomes a NEW OUTPUT PORT on the child (emit `port_declaration` on the child + `port_connection` at the parent level). DFF stays at parent; its `.D` = the new port. Log `PREFERRED_SCOPE: <scope>`.
- Unset → default to `change.instance_scope` (declaring module).

**Why:** when `input_from_submodule: true`, chain inputs only exist inside the child. FM black-boxes the child in P&R → inputs appear undriven (DFF0X) if gates sit at parent.

### Phase 0.10 — Assign instance and output net names  (was 0d)
> **DONE WHEN** every new entry has `instance_name` and `output_net` populated (DFF: `<target>_reg` / `<target>`; gate: `eco_<jira>_<seq>` / `n_eco_<jira>_<seq>`).

**For `new_logic_dff`:**
```
instance_name = <target_register>_reg
output_net    = <target_register>
```

**For `new_logic_gate` (including D-input chain gates):**
```
instance_name = eco_<jira>_<seq>   (e.g., eco_<jira>_d001)
output_net    = n_eco_<jira>_<seq>
```
Same seq across all 3 stages. Seq counter is global across all chains.

### Phase 0.11 — Record skeleton entry  (was 0e)
> **DONE WHEN** every entry has `instance_scope`, `scope_is_tile_root`, `cell_type`, `instance_name`, `output_net`, `port_connections` (Synth only), `confirmed`, AND the context fields `reason` / `notes` / `source` are non-empty.

**`instance_scope` rules — MANDATORY:**
- Submodule: `instance_scope = "<INST_A>/<INST_B>"`
- Tile root: `instance_scope = ""` (empty string) AND `"scope_is_tile_root": true`
- NEVER leave `instance_scope` as null — use `""` explicitly for tile-root scope

**`instance_scope` for tile-root detection:**
```bash
# Match tile-root module — no hardcoded prefix; pattern: any module containing the tile name as a word
grep -m1 "^module [a-z0-9_]*<tile>[a-z0-9_]* " /tmp/eco_study_<TAG>_Synthesize.v
```
The tile-root module name is also available directly from `TILE_ROOT_MODULE` (provided in agent prompt or from `resolve_module_name()` fallback).

Record skeleton entry with: `change_type`, `instance_scope`, `scope_is_tile_root`, `cell_type`, `instance_name`, `output_net`, `port_connections` (Synthesize only), `confirmed: true/false`.

**MANDATORY context fields on every entry** (consumed by eco_rpt_generator.py; empty = Step 3 validate failure):
- `reason` — one short line: WHY this change exists (its role in the ECO). E.g. `new_logic_gate`: `"<role>: <boolean expression or position>"`; `new_logic_dff`: `"<reg> with <reset/clk summary>"`; `rewire`: `"<old> → <new> on <pin>: <upstream context>"`; `port_declaration`/`port_connection`: `"<signal> as <dir> of <module> for <ECO purpose>"`.
- `notes` — 2–8 lines: chain trace `<driver>/<pin> → <wire> → ... → <DFF>.D`; RULE refs that justified the choice (e.g. `RULE 32: real RTL net over P&R alias`); lookup evidence (`Found in PreEco Synth line N`, `cell_function_matches OK`).
- `source` — stable label: `"initial_run_<TAG>"`, `"retry<N>_<TAG>"`, or `"FALLBACK_from_<stage>"`.

These are the audit trail for engineer review and round-N re-studier (Mode A/H/I/T fixes), not cosmetic.

eco_netlist_verifier will add `port_connections_per_stage`, GAP-15 correction, port boundary entries, and consumer cascade entries.

### Phase 0.12 — Mark wire_swap entries that depend on new_logic outputs  (was 0f)
> **SKIP IF** no wire_swap changes in rtl_diff.
> **DONE WHEN** every wire_swap whose `new_token` matches a `new_logic` output has `new_logic_dependency: [<seq>]` set, AND `mux_select_gate_function` / `mux_select_*` fields are propagated from Step 1 verbatim (no De Morgan substitutions).

For each `wire_swap` whose `new_token` matches a `new_logic` output net, add `"new_logic_dependency": [<seq>]`.

**MUX select polarity** — when `mux_select_gate_function` is non-null in the RTL diff, create the `new_logic_gate` from it directly. If null → record `mux_select_i0_net`/`i1_net` for eco_netlist_verifier Check 4c.

**WIRE_SWAP GATE DIRECTION (MANDATORY):** use `mux_select_gate_function` EXACTLY as given — no De Morgan substitutions. AND2 → `Z` output, NAND2 → `ZN` output. RTL diff analyzer already picked the correct function from MUX polarity; any equivalent rewrite changes LatCG cone structure → FM mismatch.

**WIRE_SWAP OUTPUT NET — GAP-22 (MANDATORY):** before reusing an existing net as a gate output, check fanout in the declaring module:
```bash
fanout=$(awk '/^module <module>/,/^endmodule/' PreEco/Synthesize.v.gz | grep -c "\b<net_name>\b")
```
`fanout > 10` → NEVER reuse — driving a high-fanout net with a new gate cascades FM mismatches across hundreds of DFFs. Use a NEW intermediate wire as the gate output, then rewire the old driver to it. Log `FANOUT_BLOCK: <net> has <N> consumers → using n_eco_<jira>_<seq>`.

**WIRE_SWAP PER-STAGE CELL RESOLUTION (MANDATORY for every rewire entry):** rtl_diff identifies the MUX cell from PreEco Synthesize only — `ctmi_*`/`phs_*`/tool-generated instance prefixes get renamed by CTS in PP/Route. Emit `cell_name_per_stage: {Synthesize, PrePlace, Route}` on every rewire so the applier targets the correct instance per stage. Two-step resolver (use both, prefer A):
- (A) **Grep PreEco/<stage>** within the declaring module for a cell of the same `cell_type` whose pin (`<pin>`) connects to the per-stage form of `<old_net>` (use `net_per_stage` map if available, else bare `<old_net>`).
- (B) **Backward-trace** from `<target_register>_reg.D` per stage: locate the DFF instance, follow `.D(<wire>)` upstream until you hit the cell whose `<pin>` drives the chain — that cell's instance name is the per-stage rewire target.
On (A)+(B) miss, emit `cell_name_per_stage[stage]: null` and `confirmed_per_stage[stage]: false` with a reason — the applier will hard-fail rather than silently SKIP.

### Phase 0.13 — Process `new_port` → `port_declaration` entries  (was 0g)
> **SKIP IF** no `new_port` changes in rtl_diff.
> **DONE WHEN** every new_port has a port_declaration study entry with `declaration_type`, `module_name`, `signal_name` populated, AND for `declaration_type=output` an INV+INV buffer chain has been emitted when no PreEco driver exists.

1. Identify `module_name`, `signal_name` (`new_token`), `declaration_type`, `instance_scope`
2. Detect netlist type: `grep -c "^module " /tmp/eco_study_<TAG>_Synthesize.v` — count > 1 = hierarchical
3. **Implicit wire check:** if `context_line` has only `wire` AND ≥ 2 `port_connection` changes reference it → skip port_declaration, set `no_wire_decl_needed: true` on those port_connection entries, note in entry.
4. If hierarchical: validate module name — `grep -c "^module <module_name>\b"`. If 0 → try `<module_name>_0`. Not found → `confirmed: false`.
5. **Output port driver check (MANDATORY when `declaration_type=output`):** first check if a companion `port_connection` change in the same ECO wires a child instance output to this signal — if so, the driver is the child port connection and **no buffer chain is needed**; emit only the `port_declaration`. Only if no such companion port_connection exists AND `grep -cE "\.(ZN|Z|Q)\s*\(\s*<signal_name>\s*\)" /tmp/eco_study_<TAG>_Synthesize.v` returns 0 (no primitive driver) → emit INV+INV buffer chain entries with `needs_buffer_chain: true`. Emitting a buffer chain when the driver is already provided by a child port_connection creates a combinational loop (gate output feeds its own input) → FM ABORT.

### Phase 0.14 — Process `port_connection` entries  (was 0h)
> **SKIP IF** no `port_connection` changes in rtl_diff.
> **DONE WHEN** every port_connection has `module_name` (the PARENT module where the child instance lives), `instance_name`, `port_name`, `net_name`, `child_module_name`, AND multi-instance entries are expanded via `flat_net_name_per_instance` into one entry per instance.
> **`module_name` is MANDATORY on every port_connection — including Mode I bus-slot renames auto-generated in Phase 0.6 Step 4.** Without it the applier searches the whole netlist and may edit the wrong instance when multiple instantiations exist. Resolve: grep PreEco for `<child_module_type> <instance_name> (` and walk upward to the enclosing `^module` line — that is the `module_name`. Validator Check 3e will WARN if `module_name` is missing.

1. Identify `parent_module`, `instance_name`, `port_name`, `net_name`, `submodule_type`
2. **MANDATORY — Validate `submodule_pattern`:** `grep -c "<submodule_type> <instance_name>" /tmp/eco_study_<TAG>_Synthesize.v`. If 0 → check PrePlace and Route; record per-stage `instance_confirmed` flags.

**Per-instance expansion:** When the rtl_diff entry has `flat_net_name_per_instance`, emit one separate `port_connection` study entry per instance, each with its own `instance_name` and `net_name` from the dict. When absent, emit a single entry using `flat_net_name` as normal. This is backward-compatible — single-instance ECOs produce one entry, multi-instance cross-channel ECOs produce one entry per instance with the correct hookup net.

### Phase 0.15 — Process `port_promotion` entries  (was 0i)
> **SKIP IF** netlist is hierarchical (`grep -c '^module ' Synthesize.v` > 1) — use port_declaration + port_connection instead.
> **DONE WHEN** every port_promotion has `flat_net_confirmed` decided, AND missing-driver cases have emitted INV+INV buffer chain entries (do NOT rely on verifier for this).

1. Check Synthesize: `grep -cw "<signal_name>" /tmp/eco_study_<TAG>_Synthesize.v`
   - If ≥ 1 → signal exists in file. **Also verify it has a driver cell:** `grep -c "\.\(ZN\|Z\|Q\)( <signal_name> )" /tmp/eco_study_<TAG>_Synthesize.v`. If driver found → `flat_net_confirmed: true`, no buffer chain needed. If signal exists but has NO driver → treat as undriven → proceed to step 2 (emit buffer chain).
2. If 0 (net absent — synthesis merged it into cone): find `<signal_name>_d1_reg` D-input wire → that is `D_INPUT`. **Do NOT use `D_INPUT` directly.** Find the cell driving `D_INPUT` and check its output pin type:
   - **Driver output is `.ZN` (inverting: ND/NR/NAND/NOR/INV prefix)** → `D_INPUT` is an active-LOW pure combinational net (no extra gating). Use it directly: `driver_net = D_INPUT`. The INV+INV double-buffer corrects polarity.
   - **Driver output is `.Z` (non-inverting: AN/OR/BUF/AN2 prefix)** → `D_INPUT` has extra AND/OR gating mixed in — do NOT use it. Trace to the driver cell's INPUTS and pick the one whose name matches the signal's purpose (e.g. the ND4/NR4 output, not the enable/clock gating input). Use Step 2 fenets result for `<signal_name>_d1` as authoritative per-instance source. Record `driver_net: <pure_input_of_AND_cell>`.
   - Record `driver_net: <pure_source>`, `needs_buffer_chain: true`, `driver_cell_inverting: true/false`.
   - Emit: `INV(<pure_source>) → n_eco_<jira>_<signal>_inv1`, `INV(n_eco_<jira>_<signal>_inv1) → <signal_name>`. Step 3 validator `PORT-DECL-WRONG-SOURCE` flags if `driver_net` is a non-inverting (`.Z`) cell output — those always have extra gating.
3. If `<signal_name>_reg` also absent → `flat_net_confirmed: false`, `reason: "net and reg both absent — port_promotion cannot be auto-applied"`. Log for engineer review.

### Phase 0.16 — Process `enable_swap` changes  (was Phase 0e — was out-of-order at end of Phase 0)
> **SKIP IF** no `enable_swap` changes in rtl_diff.
> **DONE WHEN** for every enable_swap: either (A) the clock gate E pin is rewired (preferred) or (B) the DFF CE/WE pin is rewired (fallback), AND new enable condition gate entries are emitted from `new_enable_gate_chain[]`.

For each `enable_swap` change (clock-enable / write-enable pin rewire on an existing DFF):

**Step 0 — Detect clock gate (MANDATORY — set `enable_via_clock_gate` in JSON before Step 1):**

`enable_via_clock_gate` MUST be explicitly set on the enable_swap change entry (`true` or `false`) — eco_validate_step1.py FAILs if absent. Check if the target DFF's CP (clock) is driven by a clock gate cell:
```bash
grep -E "\.(CP|CK)\s*\(\s*<clk_net>\s*\)" /tmp/eco_study_<TAG>_Synthesize.v | head -3
# Then find what drives <clk_net>:
grep -E "\.(Z|Q)\s*\(\s*<clk_net>\s*\)" /tmp/eco_study_<TAG>_Synthesize.v | head -3
```
If the driver cell is a clock gate type (`ICG*`, `CKOR*`, `CTG*`, `CKLNQ*`, `CKGT*`):
- **Use Path A (clock gate E-pin rewire)** — engineers strongly prefer this
- Record `enable_via_clock_gate: true`, `clock_gate_instance: <inst>`, `clock_gate_E_pin: <E|EN|TE>`
- Emit a `rewire` on the clock gate's E pin: `old_enable_net → new_enable_net`
- NO per-bit iteration — one clock gate serves all bus-width DFF bits
- Immune to wrong-module cell name collisions

If no clock gate (DFF's CP comes directly from a clock net):
- **Use Path B (CE/WE pin rewire)** — as before

**Step 1 — Locate enable target (Path A: clock gate / Path B: DFF CE pin):**
- Get the FM fenets results for `old_enable_net` (queried in Step 2 as Cat 8).
- **Path A:** from the clock gate instance found in Step 0, emit the E-pin rewire directly. No FM fanout walk needed.
- **Path B (fallback):** From the FM `(+)` impl line, extract the cell name. The enable pin (CE/EN/WE/E) is the pin that `old_enable_net` connects to — grep the PreEco Synthesize cell block:
  ```bash
  grep -A 20 "<cell_name>" /tmp/eco_study_<TAG>_Synthesize.v | grep -E "\.(CE|EN|WE|E)\s*\("
  ```
- Use `eco_cell_truth_tables.py` to confirm the enable pin name for that cell type.
- For bus DFFs (is_bus_dff: true on the companion new_logic change): repeat for all N per-bit DFF cells; the enable net is shared across all bits.

**Step 2 — Emit rewire entries:**

For each stage, emit a `rewire` entry for the enable pin:
```json
{ "change_type": "rewire",
  "cell_name": "<cell_name_per_stage>",
  "pin": "<CE|EN|WE|E>",
  "old_net": "<old_enable_net>",
  "new_net": "<new_enable_net>",
  "confirmed": true,
  "reason": "enable_swap: CE pin rewired from old condition to new condition",
  "cell_name_per_stage": {"Synthesize": "...", "PrePlace": "...", "Route": "..."},
  "module_name": "<gate-level module name containing the cell>" }
```

**`module_name` is MANDATORY on every `rewire` entry** — including enable_swap rewires. Tool-generated cell names (`ctmi_*`, `phs_*`, `copt_*`) repeat across many modules in the hierarchical netlist. Without `module_name`, eco_applier matches the first cell of that name in the entire file — which may be in a completely unrelated module, silently corrupting unrelated logic while leaving the intended target unchanged. Extract `module_name` from the FM impl path: `i:/FMWORK_IMPL_<TILE>/<TILE>/<INST_A>/<INST_B>/<cell>/<pin>` — the declaring module is derived from `<INST_A>/<INST_B>` hierarchy using `resolve_module_name()` against the PostEco netlist.

For bus DFFs: emit N rewire entries (one per bit cell), all sharing the same enable pin name and old/new net names.

**Step 3 — Emit new_logic_gate entries for the new enable condition gates:**

From `new_enable_gate_chain[]` in the RTL diff, emit one `new_logic_gate` entry per gate — same as wire_swap condition gate chain handling. These gates produce `new_enable_net` from its sub-expressions.

Log: `ENABLE_SWAP: <target_register> CE pin rewired from <old_enable_net> → <new_enable_net> | <N> gate(s) inserted`

---

## Phase 1 — Process Per Stage (wire_swap FM Results)

For each `wire_swap` change, process FM fenets results per stage.

**MANDATORY PRE-PHASE 1A — `wire_swap + fallback_strategy: "driver_substitution"`** (check BEFORE intermediate_net_insertion):
1. Emit a `rewire`: rename driver of `driver_sub_target_net` from `<target_net>` to `ECO_<jira>_net_orig` per stage (rename_map for per-stage cell name).
2. Emit `new_logic_gate` entries for each gate in `new_condition_gate_chain` — only stage-stable inputs (verified via rename_map; must exist in all 3 PreEco stages).
3. Last gate's `output_net` MUST equal `driver_sub_target_net` (original name) — keeps downstream untouched, FM traces trivially.
4. Do NOT rewire the pivot net (SEQMAP_NET_*) — never touched.

**MANDATORY PRE-PHASE 1 — `wire_swap + fallback_strategy: "intermediate_net_insertion"` with non-empty `new_condition_gate_chain`** (run BEFORE the rename_map lookup that produces the rewire entry, so gate entries appear alongside it):

**CRITICAL — cell type selection for condition gates:**
For each gate in `new_condition_gate_chain`, use the cell type from the rtl_diff's E4c compound gate discovery (which searched the PreEco Synthesize netlist). Do NOT invent alternate gate decompositions. The PreEco netlist is the ground truth — synthesis chose specific compound types (OA12, OAI21, AN3, ND3, ND2LLK, etc.) for these RTL sub-expressions. Using different-but-logically-equivalent types (e.g. NR2+OR3+AN2 instead of OA12+OAI21) causes scan-enable path structural divergence between Synth ECO and PP ECO → thousands of FM failures even when logic is correct. If E4c found no matching compound gate for a sub-expression, use the simplest matching primitive from the PreEco scope (grep for the function near the pivot).

1. **If `driver_sub_renamed_to` is set**: emit a `rewire` renaming `driver_sub_target_net` → `driver_sub_renamed_to` (e.g., `ctmn_2084955` → `ECO_<jira>_net_orig`) per stage using rename_map. Any gate in `new_condition_gate_chain` whose input equals `driver_sub_target_net` MUST use `driver_sub_renamed_to` instead — otherwise the final gate outputs to the same net it reads as input, creating a combinational loop.
2. Emit `new_logic_gate` per chain gate (instance_name, gate_function, per-stage inputs, output_net, instance_scope = declaring module).
3. Resolve PENDING_FM_RESOLUTION inputs via rename map (Step 2 condition_inputs_to_query). If a signal resolves to **different nets per stage** (e.g., Synth net differs from PP/Route net), emit `port_connections_per_stage` for that gate instead of a single `port_connections`. Each stage entry maps the PENDING_FM_RESOLUTION input to its stage-specific resolved net.
4. Apply Mode H Route fallback for unresolvable Route inputs.
5. Last gate (`c_mux_final` etc.) MUST output to `<pivot_net>` — NOT a new `n_eco_*`.

Without this, `<pivot_net>` is renamed `<pivot_net>_orig` with nothing driving the original → undriven DFF.D → thousands of FM cascading failures.

Log: `CONDITION_GATE_CHAIN: emitting <N> new_logic_gate entries for wire_swap <old_token>`

**Multi-instance:** when `instances` is non-null, process each instance's FM results independently.

### Phase 1.1 — Read the PreEco netlist (once per stage, reuse across all cells)  (was 1)
> **DONE WHEN** `/tmp/eco_study_<TAG>_<Stage>.v` exists for all 3 stages.
```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz > /tmp/eco_study_<TAG>_<Stage>.v
```

### Phase 1.2 — Find and extract cell instantiation block  (was 2–3)
> **DONE WHEN** each FM-qualified cell's instantiation block has been parsed and its `.portname(netname)` map extracted.

Read from the line with the cell name through the closing `);`. Extract all `.portname(netname)` entries.

### Phase 1.3 — Confirm old_net is present  (was 4)
> **DONE WHEN** each rewire entry has `old_net` populated AND `confirmed: true/false` decided. HFS-alias case has `old_net_alias: true` + reason set when applicable.

**Step 1 — Try direct old_net name:** `grep -c "\.<pin>(<old_token>)" /tmp/eco_study_<TAG>_<Stage>.v`
- If ≥ 1 → `"old_net": "<old_token>"`, `"confirmed": true`

**Step 2 — If not found, check for HFS alias on that pin.** Read actual net on `<pin>`, verify alias via parent module port connection. If confirmed: set `"old_net_alias": true`, `"old_net_alias_reason"`.

If neither found: `"confirmed": false`. eco_netlist_verifier will run stage fallback (GAP-5).

### Phase 1.4 — Basic new_net reachability  (was 4b)
> **DONE WHEN** each rewire entry has `new_net` populated AND `new_net_reachable: true/false` decided. (Full cone verification deferred to verifier Check 10.)

**Priority 1 — Direct name:** `grep -cw "<new_token>" /tmp/eco_study_<TAG>_<Stage>.v`. If ≥ 1 → `"new_net": "<new_token>"`.

**Priority 2 — HFS alias (only if direct absent):** Set `"new_net_alias": "<alias>"`, `"new_net_reachable": true`. If not found: `"new_net_reachable": false`.

Backward cone and forward trace verification are handled by eco_netlist_verifier Check 10.

### Phase 1.5 — Verify output count per stage  (was 5)
> **DONE WHEN** `N qualifying cells` from the FM rpt == `N entries` in study[stage] for the current stage. Mismatch → re-check Phase 1.2–1.4.
```
Qualifying list had: N cells
Output JSON has:     N entries  ← must match
```

### Phase 1.6 — Cleanup temp files  (was 6)
> **DONE WHEN** `/tmp/eco_study_<TAG>_*.v` files are removed.
```bash
rm -f /tmp/eco_study_<TAG>_Synthesize.v /tmp/eco_study_<TAG>_PrePlace.v /tmp/eco_study_<TAG>_Route.v
```

---

## Output JSON

Write `<BASE_DIR>/data/<TAG>_eco_preeco_study.json`.

**`change_type` translation:** `wire_swap` → `rewire`; `new_logic` → `new_logic_dff` or `new_logic_gate`.

**Sort each stage array by PASS_ORDER before writing:**
```python
PASS_ORDER = {
    "new_logic": 1, "new_logic_dff": 1, "new_logic_gate": 1,
    "port_declaration": 2, "port_promotion": 2,
    "port_connection": 3,
    "rewire": 4,
}
for stage in ["Synthesize", "PrePlace", "Route"]:
    study[stage].sort(key=lambda e: PASS_ORDER.get(e.get("change_type", "rewire"), 4))
```

Verify output is non-empty with at least one confirmed entry.

**Write collect RPT** to `<BASE_DIR>/data/<TAG>_eco_step3_collect.rpt`:
```
ECO NETLIST STUDIER — COLLECT PASS
TAG=<TAG>  |  JIRA=<JIRA>  |  TILE=<TILE>
================================================================================
PHASE 0 — new_logic / port entries:
  new_logic_gate / new_logic_dff / port_declaration / port_connection:
      <N>  (confirmed: <N>  excluded: <N>)   — one line per change_type
  d_input_chains: <N> chains  <N> gates total  (<N> decompose_failed)

SYNC RESET HANDLING (per DFF with has_sync_reset=true):
  <target_register>:
    reset_signal/polarity/reset_pin_used: <rst> / active_high|active_low / YES|NO
    [YES] cell_type/reset_pin/d_input_gates (reset removed)  → GAP-CTS-2 AVOIDED
    [NO ] no DFF in <module> uses <rst> — reset baked into D cone (GAP-CTS-2 risk)

PHASE 1 — wire_swap rewire entries:
  [Synthesize|PrePlace|Route]  <N> qualifying  confirmed: <N>  excluded: <N>

EXCLUDED entries (need verifier or manual fix):  <cell/signal>: <reason>
NOTE: port_connections_per_stage resolved by eco_netlist_verifier.
================================================================================
```
Copy RPT to `AI_ECO_FLOW_DIR/`.

**After writing, exit immediately.** eco_netlist_verifier is spawned by ORCHESTRATOR next.

---

## Confirmed-false Notes

- Cell not found in PreEco: `"confirmed": false, "reason": "cell not found in PreEco netlist"`
- Old net not on expected pin: `"confirmed": false, "reason": "pin <pin> has net <actual_net> not expected <old_net>"`
- Multiple instances: `"confirmed": false, "reason": "AMBIGUOUS — multiple occurrences"`
- Name mangling: retry with `"<cell_name>_reg"` before marking confirmed: false
- All stages have no FM results: mark all confirmed: false for manual review
