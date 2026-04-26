# ECO Auto Flow — Findings and Solutions: 9868 & 9899

**Date:** 2026-04-23
**Scope:** Gate-level ECO automation findings from running the AI ECO flow on two real ECO changes. All findings have been incorporated into the guide files under `config/eco_agents/`.

---

## Overview

The AI ECO flow was run iteratively on two ECOs. Each run surfaced bugs, which were fixed in the guides, and the next run was retried. This document captures all bugs found, root causes, and fixes applied.

---

## Part 1: Common Issues (Both ECOs)

### Issue 1 — SVF: Invalid `guide_eco_change -type insert_cell` command
**Symptom:** FM aborts all 3 targets with CMD-010 before any comparison.
**Root cause:** `eco_svf_updater` was writing `guide_eco_change -type insert_cell -instance -reference` entries — a command that does not exist in Formality SVF.
**Fix:** `eco_svf_updater.md` completely rewritten. FM auto-matches inserted cells by instance name — no SVF entries needed for cell insertions. Only `set_dont_verify` or `set_user_match` belong in the setup partition.
**Rule added:** RULE 11

---

### Issue 2 — PORT_DECL: Hierarchical netlist treated as flat
**Symptom:** All `port_declaration` and `port_connection` entries skipped ("flat netlist").
**Root cause:** `eco_netlist_studier` ran `port_promotion` check on Synthesize, found the net as a flat wire, and set `no_gate_needed: true`. The eco_applier then skipped all port entries. The PostEco netlist was hierarchical (8000+ modules).
**Fix:** Step 0g in studier: detect netlist type first (`grep -c "^module "`). If count > 1 → hierarchical → port entries always applied.
**Rule added:** RULE 15

---

### Issue 3 — PORT_DECL Step 2: Port list close not found for long port lists
**Symptom:** Port declarations SKIPPED with reason "Could not find port list close" in PrePlace/Route. P&R stages add scan/test ports, making port lists 5-10× longer than Synthesize.
**Root cause:** Simple `);` pattern search failed for multi-line port lists. Also, `endmodule` detection didn't strip trailing comments (`endmodule // note`), causing `endmodule_idx` to be found too early.
**Fix:** eco_applier 4c-PORT_DECL Step 2 uses parenthesis depth tracking across full `range(mod_idx, endmodule_idx)`. `endmodule` detection strips comments before comparing. Explicit checkpoint: if `port_list_close_idx` is None after loop → SKIPPED with diagnostic reason (not silent).
**Rule added:** RULE 24

---

### Issue 4 — PORT_DECL: Wire declaration treated as port declaration
**Symptom:** FM-599 (read error) — "Port wire 'ARB_FEI_NeedFreqAdj' was never declared in an input/output/inout" for PrePlace/Route.
**Root cause:** Some `new_port` changes declare a local `wire` inside a module (connecting submodules), not a true port. The eco_applier applied port list modification (Step 2) — which fails for very long P&R port lists — and an `input`/`output` declaration instead of just `wire <signal>;`.
**Fix:** eco_netlist_studier 0g reads `context_line` to determine `declaration_type`: if `wire` keyword → `declaration_type: "wire"`. eco_applier 4c-PORT_DECL Step 4-WIRE: wire declarations only add `wire <signal>;` to module body, NO port list modification.
**Rule added:** Part of RULE 24

---

### Issue 5 — PORT_CONN: Netlist corruption from loose `);` pattern
**Symptom:** FM-599 — netlist syntax error after port connection insertion.
**Root cause:** eco_applier found the instance closing `);` using `lines[i].strip() in (');', ') ;')`. A line like `.last_port( net ) ) ;` has `))` — the outer `)` closes the instance — but the pattern matched mid-block.
**Fix:** eco_applier 4c-PORT_CONN uses parenthesis depth tracking (same as RULE 20 for PORT_DECL). Inserts before the last `)` found by depth tracking.
**Rule added:** RULE 20

---

### Issue 6 — PORT_PROMO: Rename applied to all module variants
**Symptom:** 3000+ FM failing points after port promotion — synthesis-equivalent signal renamed in every module containing that wire name.
**Root cause:** eco_applier applied `replace('wire ', 'output ')` across the full file without stopping at `endmodule`. Modules sharing the same internal wire name (e.g., 550+ variants) were all corrupted.
**Fix:** eco_applier 4c-PORT_PROMO uses exact anchored regex `^module\s+<name>\s*[(\s]` and finds `endmodule_idx`. All search/replace restricted to `lines[mod_idx:endmodule_idx]`.
**Rule added:** RULE 19

---

### Issue 7 — DFF insertion: Synthesize net names used for all stages
**Symptom:** FM stage-to-stage mismatch — inserted DFF appears as regression failing point in PrePlace vs Synthesize comparison.
**Root cause:** eco_applier used Synthesize-derived `port_connections` for all 3 stages. P&R renames clock and reset nets between stages.
**Fix:** eco_netlist_studier Step 0b-STAGE-NETS: verifies each functional pin net per stage (Priority 1 direct name → Priority 2 P&R alias). Writes `port_connections_per_stage`. eco_applier reads per-stage map.
**Rule added:** RULE 16

---

### Issue 8 — DFF insertion: Auxiliary scan pins missing or wrong
**Symptom:** Inserted DFF disconnected from scan chain in P&R stages — DRC and LEC failures.
**Root cause:** Auxiliary pins (scan input, scan enable) not collected per stage. Synthesize constants (`1'b0`) used for P&R stages where real scan nets are required.
**Fix:** eco_netlist_studier Step 0b-STAGE-NETS Step C: reads auxiliary pin values from a neighbour DFF of the same cell type in the same module scope, per stage.
**Rule added:** RULE 17

---

### Issue 9 — Pre-FM integrity check missing
**Symptom:** FM jobs ran 1-2 hours then failed immediately (FM-599 read error or N/A) due to netlist corruption or missing declarations that could have been detected in seconds.
**Fix:** ORCHESTRATOR.md Step 4c added: 4 checks before every FM submission — (1) no SKIPPED port_declaration entries, (2) no Verilog syntax errors (unbalanced parentheses), (3) all 3 stages have ECO cells, (4) declared signals present in all stages.
**Rule added:** RULE 25

---

## Part 2: ECO 9868 Specific Issues

### Issue 10 — MUX select polarity: NAND2 instead of AND2 (persistent, 5+ runs)
**Symptom:** FM fails on `FEI_ARB_OutstRdDat_reg` every round. Gate-level MUX select inverted — selects wrong data path every cycle.
**Root cause:** Multiple layers:
- `eco_svf_updater` wrote invalid SVF (fixed in Issue 1)
- RTL diff analyzer derived NAND2 from condition text `~E|~A` without reading PreEco netlist I0/I1 mapping
- Studier created `new_logic_gate` entry with NAND2 in Phase 0 (before Step 4c-POLARITY could override)
- Phase 0 entry was treated as final — Step 4c-POLARITY never ran or was overridden

**Fixes applied (multiple rounds):**
1. `eco_svf_updater.md`: no SVF entries for cell insertions (RULE 11)
2. `eco_netlist_studier.md` Step 0f: do NOT create `new_logic_gate` in Phase 0 for wire_swap MUX select — gate function must come from Step 4c-POLARITY
3. `rtl_diff_analyzer.md` Step D: set `mux_select_polarity_pending: true` — no gate function hint from RTL condition text
4. `rtl_diff_analyzer.md` Step D-MUX-1→5: read PreEco netlist I0/I1 directly — compute gate function at Step 1 deterministically
5. D-MUX-3→4→5 restructured with enforced STOP at each step — commit to "gate = NOT(condition)" BEFORE reading the condition expression (prevents visual shortcut `~E|~A` → NAND2)
6. `eco_fm_analyzer.md` Check D: re-derives gate function from PreEco netlist (not from RTL diff hint)

**Current status:** Step 1 D-MUX still occasionally produces NAND2 due to agent misapplying MUX truth table direction. Step 3 studier's Step 4c-POLARITY reliably overrides to AND2. AND2 confirmed in preeco_study.json across all runs.

**Rule added:** RULE 18

---

### Issue 11 — FM-036: Internal wire `RegRdbRspCredits` invisible to FM
**Symptom:** FM-036 at all hierarchy depths — `RegRdbRspCredits` is declared as `reg [5:0]` inside `umcsdpintf`, not exposed as a port.
**Fix:** ORCHESTRATOR.md RULE 10 updated: classify FM-036 as "internal wire" → pivot immediately to target register query (`FEI_ARB_OutstRdDat`). Studier traces backward from register D-input to find MUX cell.
**Rule added:** Part of RULE 10

---

## Part 3: ECO 9899 Specific Issues

### Issue 12 — ToggleChn: `d_input_decompose_failed` initially MANUAL_ONLY
**Symptom:** ToggleChn excluded as MANUAL_ONLY — 4 new priority conditions prepended to existing expression.
**Root cause:** New conditions depend on signals that don't exist in PreEco netlist by their RTL names (synthesis renamed them).
**Fix:** Three-phase solution:
1. `rtl_diff_analyzer` E4d: when old expression preserved as default case → `fallback_strategy: intermediate_net_insertion`
2. `eco_netlist_studier` Step 0c: trace backward from register D-input to find "pivot net" — insert new conditions there without touching DFF D-input
3. `eco_netlist_studier` Step 0c-2: driver cell fallback for P&R stages where pivot net is renamed
**Rule added:** RULE 21

---

### Issue 13 — MUX2 generic primitive in condition gate chain
**Symptom:** FM N/A on all targets — "MUX2 is a generic Verilog primitive not in technology library".
**Root cause:** `new_condition_gate_chain` specified `gate_function: "MUX2"`. eco_applier inserted bare `MUX2` primitive instead of resolving to a real library cell.
**Fix:** eco_applier 4c-GATE Step 2: grep PreEco netlist for `MUX2[A-Z0-9]*` pattern to find real library cell name. Never use generic primitive.

---

### Issue 14 — Condition gate chain set to null instead of PENDING
**Symptom:** `new_condition_gate_chain: null` even though some inputs just needed FM resolution.
**Root cause:** When V3 gate-level name resolution failed for an input, E4d set chain = null. But FM can find synthesis-renamed signals — there was no reason to set null.
**Fix:** `rtl_diff_analyzer` E4d Step V4: instead of null, mark input as `"PENDING_FM_RESOLUTION:<signal>"` and add to `condition_inputs_to_query` + `nets_to_query`. Chain structure preserved with PENDING placeholders. Only truly unresolvable inputs (decomposition failure, arithmetic) trigger null.

---

### Issue 15 — FM resolves synthesis-renamed condition inputs
**Symptom:** Signals `DcqArb0_QualPhArbReqVld`, `DcqArb1_QualPhArbReqVld` have 0 occurrences in PreEco gate-level netlist. RTL-level grep fails. Chain truncated.
**Root cause:** Synthesis renamed these signals to internal nets (e.g., `phfnn_2405075`, `N2408127`) with no predictable relationship to RTL names. Standard name variants (`_reg`, `_0_`) don't help.
**Fix:**
- `rtl_diff_analyzer` E4d V3: tries standard name variants, then adds to `condition_inputs_to_query` + `nets_to_query`
- `eco_fenets_runner` Step C2: parses FM results for condition input signals → writes `condition_input_resolutions`
- `eco_netlist_studier` Step 0c-5: substitutes PENDING inputs with FM-resolved gate-level names

**Result:** FM finds `DcqArb0_QualPhArbReqVld` → `phfnn_2405075`, `DcqArb1_QualPhArbReqVld` → `N2408127`. Full 14-gate condition chain applied.

---

### Issue 16 — MUX cascade gates dropped from condition chain
**Symptom:** Chain had 11 gates (conditions only) instead of 14-15 (conditions + MUX cascade). ToggleChn conditions implemented but not connected back to pivot net.
**Root cause:** E4d guide said "cascaded MUX2 **or** priority gate chain" — agent chose "or" and stopped at condition outputs without building the MUX cascade.
**Fix:** E4d guide made MANDATORY: chain MUST include condition gates AND MUX cascade gates. Last MUX gate MUST output to `<pivot_net>` (not a new net). Added explicit JSON example and MANDATORY label.

---

## Part 3B: Issues Found from PostEco FM Failure Analysis (Session 2)

This section documents additional root causes discovered by directly reading PostEco FM logs and rpts rather than waiting for the round loop to diagnose them.

---

### Issue 17 — eco_applier: ALREADY_APPLIED check too broad for PORT_DECL

**Symptom:** FM abort (FE-LINK-7 + FM-156) — "pin 'NeedFreqAdj' has no corresponding port on ddrss_umccmd_t_umcarbctrlsw". 9868 all 3 FM targets fail.

**Root cause:** eco_applier detected `PORT_DECL` as `ALREADY_APPLIED` because `NeedFreqAdj` exists in the file (as a DFF output wire `eco_9868_dff1.Q = NeedFreqAdj`) — but it was NOT in the module port list header. The check used a broad `grep -c "NeedFreqAdj"` ≥ 1 instead of checking the port list specifically.

**Fix applied:**
- `eco_applier.md`: Added explicit ALREADY_APPLIED Detection table — `port_declaration (input|output)` MUST parse port list range `mod_idx..port_list_close_idx` to verify the signal is actually in the port list header, not just anywhere in the file
- `eco_applier.md`: Added `force_reapply: true` flag — eco_netlist_studier_round_N sets this when ABORT_LINK is diagnosed; eco_applier skips ALREADY_APPLIED check unconditionally
- `eco_fm_analyzer.md`: Added Step 0b–0c ABORT_LINK detection — reads FE-LINK-7 errors, extracts missing port name, checks eco_applied JSON for false ALREADY_APPLIED, produces `force_port_decl` revised_changes

---

### Issue 18 — REG_UmcCfgEco[1] gate-level net not FM-queried (9868)

**Symptom:** 9868 eco_9868_dff2 is DFF0X in PrePlace and Route — `EcoUseSdpOutstRdCnt` stuck at 0 → MUX select stuck at 0 → FEI_ARB_OutstRdDat_reg non-equivalent.

**Root cause (two-part):**
1. `rtl_diff_analyzer` Step D-POST was missed — `REG_UmcCfgEco_1_` was in `condition_inputs_to_query` but was never added to `nets_to_query`. FM was never submitted for this signal.
2. eco_netlist_studier fell back to direct netlist lookup and found `UNCONNECTED_3288` at bit[1] of the REGFILE output bus — a valid signal in Synthesize (FM can trace through flat REGFILE) but classified as undriven in PrePlace/Route because FM black-boxes the REGFILE hard macro.

**Fixes applied:**
- `rtl_diff_analyzer.md`: Promoted "condition inputs → nets_to_query" to a mandatory numbered step `Step D-POST` with checkpoint ("verify nets_to_query count increased")
- `eco_netlist_studier.md`: Added `needs_named_wire()` structural check — detects nets with no direct primitive driver (only in hierarchical port bus) and flags `NEEDS_NAMED_WIRE:<net>` instead of using directly
- `eco_applier.md`: Added 4c-GATE Step 0 — `NEEDS_NAMED_WIRE` handling: declare new named wire, replace source net in REGFILE output bus, use named wire as gate input
- General principle: the check is structural (no direct primitive driver + in hierarchical port bus), NOT naming-convention based — applies to any tool/design

---

### Issue 19 — eco_netlist_studier missing PENDING_FM_RESOLUTION → UNCONNECTED substitution

**Symptom:** eco_preeco_study.json has `eco_9868_e002.A1 = UNCONNECTED_6680` (PrePlace), `SYNOPSYS_UNCONNECTED_4826` (Route) instead of the correct REG_UmcCfgEco[1] gate-level net.

**Root cause:** When FM results for `REG_UmcCfgEco_1_` were absent (Issue 18), the studier fell back to direct netlist lookup which found the UNCONNECTED_xxx net at the correct bus position. The UNCONNECTED_xxx net IS driven in Synthesize (FM traces through flat REGFILE) but not traceable in P&R stages (hard macro black-box). This produces `DFF0X` specifically in PrePlace/Route while Synthesize passes.

**Fix applied:** Structural driver check (Issue 18 fix) prevents using port-bus-only nets. The eco_fm_analyzer now detects Mode H (Check E) when ECO-inserted DFF is DFF0X and trace confirms gate input has no direct primitive driver.

---

### Issue 20 — FM "Subsequent rounds" section in ORCHESTRATOR causes loop

**Symptom:** When PostEco FM fails, the ORCHESTRATOR re-runs FM multiple times in the same session instead of handing off to ROUND_ORCHESTRATOR.

**Root cause:** `ORCHESTRATOR.md` Step 5 Notes contained a "Subsequent rounds (only failing targets)" FM config section alongside the initial run. Agents interpreted this as permission to loop FM internally.

**Fix applied:**
- `ORCHESTRATOR.md`: Removed "Subsequent rounds" FM config section — ORCHESTRATOR runs FM **exactly once** (Round 1, all 3 targets). Added HARD RULE: "If FM fails → write round_handoff.json → spawn ROUND_ORCHESTRATOR → HARD STOP. Never re-run FM from ORCHESTRATOR."
- `ROUND_ORCHESTRATOR.md`: Added HARD RULE to Critical Rules #1 and Step 5: "ONE FM run per round. After Step 5, spawn next agent and EXIT."
- Clear contract: ORCHESTRATOR = Round 1 FM once. Each ROUND_ORCHESTRATOR instance = one FM run. No loops within a single agent.

---

### Issue 21 — round_handoff.json missing `ai_eco_flow_dir` in Round 3+

**Symptom:** Round 3+ ROUND_ORCHESTRATOR cannot find AI_ECO_FLOW_DIR — all file copies fail silently.

**Root cause:** ORCHESTRATOR writes handoff with `ai_eco_flow_dir` field. ROUND_ORCHESTRATOR reads it from handoff (Round 2 works). But ROUND_ORCHESTRATOR's own handoff template did NOT include `ai_eco_flow_dir` → Round 3 ROUND_ORCHESTRATOR reads Round 2's handoff → field missing.

**Fix applied:**
- `ROUND_ORCHESTRATOR.md`: Added `ai_eco_flow_dir` to round_handoff.json template with explicit warning: "CRITICAL: must be in every round_handoff.json — value never changes across rounds"
- `ORCHESTRATOR.md`: Added `ai_eco_flow_dir` and `base_dir` to eco_fixer_state as fallback
- `ROUND_ORCHESTRATOR.md`: eco_fm_runner and eco_netlist_studier spawns pass `AI_ECO_FLOW_DIR` explicitly

---

### Issue 22 — eco_svf_updater gets NEXT_ROUND, reads wrong eco_fm_analysis

**Symptom:** eco_svf_updater reads `eco_fm_analysis_round<NEXT_ROUND>.json` which doesn't exist — should read the FAILED round's analysis.

**Root cause:** ROUND_ORCHESTRATOR spawned eco_svf_updater with `ROUND=<NEXT_ROUND>` (the round being set up), but eco_svf_updater reads `eco_fm_analysis_round<ROUND>.json`. This reads the wrong file.

**Fix applied:**
- `ROUND_ORCHESTRATOR.md`: eco_svf_updater now receives `ROUND_FAILED=<ROUND>` (failed round) and `ROUND_NEXT=<NEXT_ROUND>` separately
- `eco_svf_updater.md`: Reads `eco_fm_analysis_round<ROUND_FAILED>.json`, uses `ROUND_NEXT` only for output file naming

---

### Issue 23 — eco_applier: large file sequential edits cause stale line numbers and missed signals

**Symptom:** ECO changes reported as applied but signals missing or wrong in large PostEco netlists (P&R stages 300-500MB decompressed).

**Root cause:** Decompress entire file → sequential edits → each edit shifts line numbers of subsequent lines → grep returns stale positions → changes applied to wrong locations or missed.

**Fix applied:**
- `eco_applier.md`: New module-extraction streaming strategy — stream compressed file, extract only target modules into isolated buffers, apply ALL changes for a module in one pass (stable line numbers), write back. Modules with no changes stream through without loading.
- `eco_applier.md`: Verification done in-memory from already-edited module buffer (no second decompress)
- `eco_applier.md`: Module boundary simplified — isolated buffer always has `mod_idx=0, endmodule_idx=len-1`

---

### Issue 24 — `and_term` scope: rewires parent-scope cells for child-module change (9899)

**Symptom:** 9899 Synthesize: 3000 ARB DFFs non-equivalent. eco_9899_001/002 rewired A606036, A606254, A648153, A648363 from `QualPmArbWinVld_d1` → `n_eco_9899_001/002`. Real engineer does NOT touch these cells.

**Root cause:** The `and_term` RTL change was declared in `umcdcqarb` (DCQARB module). FM query `ARB/DCQARB/QualPmArbWinVld_d1` returned cells using that signal — but A606036 etc. exist at the ARB scope (parent of DCQARB), not inside DCQARB. When PreEco is flat, all cells appear at flat scope and pass the hierarchy filter. In hierarchical PostEco, these parent-scope cells should never be rewired.

**Fix applied:**
- `eco_netlist_studier.md`: Added `and_term` scope validation — for hierarchical PostEco, each FM-returned cell is verified to be INSIDE the declaring module. Cells in parent/sibling scopes are excluded with reason "exists outside module '<name>' in hierarchical PostEco"
- Check is structural (module boundary match), not naming-based — applies to any design

---

### Issue 25 — New_logic_gate inputs not resolved per-stage; floating inputs inserted silently (9899)

**Symptom:** 9899 Route: `eco_9899_c008` (OR2) not inserted. c009 and c010 have floating `n_eco_9899_c008` input. ToggleChn_reg non-equivalent. Step 4 rpt incorrectly shows INSERTED with 0 verify_failed.

**Root cause:**
1. eco_netlist_studier only resolved gate inputs from Synthesize stage. `N2408127` (gate-level name for `DcqArb0_QualPhArbReqVld` in Synthesize) is renamed by P&R in Route — 0 occurrences in PostEco Route.
2. eco_applier used Synthesize-stage `port_connections` for Route stage → `N2408127` not found in module buffer → gate NOT inserted.
3. Verification (`grep -c "eco_9899_c008"`) found 0 occurrences but reported success due to wrong verification scope — actually the net `n_eco_9899_c008` was referenced but cell instance was absent.

**Fixes applied:**
- `eco_netlist_studier.md`: Added `0b-GATE-STAGE-NETS` — mandatory per-stage input net resolution for ALL combinational gates (Priority 1: direct name → Priority 2: driver cell trace → Priority 3: P&R alias). Writes `port_connections_per_stage` per gate.
- `eco_applier.md` 4c-GATE Step 1: Uses `port_connections_per_stage[Stage]` if available. Verifies ALL input nets exist BEFORE insertion. Missing inputs → SKIPPED (not silent insert with floating pin). Never inserts gate with non-existent input.

---

## Part 4: Guide Files Updated

| File | Key Changes (Session 1) | Key Changes (Session 2) |
|------|------------------------|------------------------|
| `CRITICAL_RULES.md` | Rules 11-25 | — |
| `rtl_diff_analyzer.md` | D-MUX polarity; E4d PENDING chain; V3 name resolution | Step D-POST mandatory (condition_inputs → nets_to_query) |
| `eco_netlist_studier.md` | 0b-STAGE-NETS; 0c pivot; 0c-5 PENDING; 0f wire_swap prohibition; 4c-POLARITY STOP; 0g wire vs port | `needs_named_wire()` structural check; 0b-GATE-STAGE-NETS per-stage gate nets; and_term scope validation; RE_STUDY_MODE (all modes A/B/D/H/ABORT_LINK) |
| `eco_applier.md` | Netlist detection; PORT_DECL wire; PORT_PROMO module boundary; PORT_CONN depth tracking; DFF per-stage; GATE library cell | ALREADY_APPLIED per-type rules; force_reapply; GATE Step 0 needs_named_wire; GATE Step 1 all-inputs-exist guard; Step 3 module-extraction streaming; port_connections_per_stage usage |
| `eco_svf_updater.md` | Complete rewrite | ROUND_FAILED/ROUND_NEXT input params |
| `eco_fenets_runner.md` | Step C2 condition input resolution; FM-036 pivot | RERUN_MODE for missing condition inputs |
| `eco_fm_analyzer.md` | Check D polarity; Mode F/G | Check E (DFF0X); Mode H (hierarchical port bus); Check F (unresolved condition inputs); ABORT_LINK; Step 0a–0c abort diagnosis; Step 3b deep netlist investigation; needs_rerun_fenets |
| `eco_fm_runner.md` | — | HARD RULE: one FM run only |
| `ORCHESTRATOR.md` | Step 4c pre-FM checks; FM-036 pivot | HARD RULE one FM run; removed "Subsequent rounds" section; ai_eco_flow_dir in fixer_state |
| `ROUND_ORCHESTRATOR.md` | Mode F/G early exit | ai_eco_flow_dir in handoff; ROUND_FAILED for svf_updater; Step 6f-FENETS; eco_netlist_studier_round_N; eco_apply_fix_round_N; Mode H handling; HARD RULE one FM run |
| `FINAL_ORCHESTRATOR.md` | — | Per-round re-study RPTs in file index |

---

## Part 5: Reference — Real Engineer's Correct ECO Solution

### 9868 — Correct ECO (from passing TileBuilder run)

**MUX select gate:**
- Cell: `AN2D1BWP136P5M117H3P48CPDLVTLL eco9868_an2` — **AND2** (not NAND2)
- Inputs: `EcoUseSdpOutstRdCnt`, `ARB_FEI_NeedFreqAdj`
- Output: `eco9868_new_sel` → MUX `.S` pin

**NeedFreqAdj D-input chain (4 cells — more compact than AI's 8):**
- `INV eco9868_inv_req` (.I=BeqCtrlPeReq, .ZN=eco9868_inv_req_z)
- `XOR2 eco9868_xor_src10` (.A1=BeqCtrlPeSrc[1], .A2=BeqCtrlPeSrc[0], .Z=eco9868_xor_z)
- `OR4 eco9868_or4` (.A1=eco9868_inv_req_z, .A2=ArbCtrlPeRdy, .A3=BeqCtrlPeSrc[2], .A4=eco9868_xor_z, .Z=eco9868_or4_z)
- `NR2 eco9868_nr_needfreqadj` (.A1=eco9868_or4_z, .A2=IReset, .ZN=eco9868_nr_z)
- `SDFQD1 NeedFreqAdj_reg` (.D=eco9868_nr_z, .CP=UCLK01, .Q=NeedFreqAdj)

**EcoUseSdpOutstRdCnt D-input chain (2 cells):**
- `INV eco9868_inv_cfg1` + `NR2 eco9868_nr_cfg1` + `SDFQD1 EcoUseSdpOutstRdCnt_reg`

**SVF:** Empty setup partition — no entries needed. FM auto-matches all cells.

**Total cells inserted:** 9 (Synthesize). AI flow inserted 12 (more gates, functionally equivalent).

---

### 9899 — Correct ECO (from passing TileBuilder run)

**QualPmArbWinVld_d1 gating:** INR2 (AND-NOT) gate inserting `~SplitActInProgOthDcq` term ✅

**ToggleChn pivot net redirect:** Driver of `ctmn_2084958` redirected to `ECO_9899_net30`, new condition gates feed back to `ctmn_2084958`.

**ToggleChn new condition gates (real engineer's gate-level names):**
- `N2408127` and `phfnn_2405075` = gate-level equivalents of `DcqArb0_QualPhArbReqVld` and `DcqArb1_QualPhArbReqVld` (synthesis-renamed)
- Gate types used: `OA12`, `OAI21`, `INVD1`, `ND2LLKGD1`, `AN3D3`, `ND3D1` — complex gate types
- The AI flow correctly resolves `N2408127` / `phfnn_2405075` via FM find_equivalent_nets ✅

**SVF:** Empty setup partition — no entries.

**Port declarations/connections:** All applied. PhArbFineGater promoted, SplitActInProgOthDcq added, DcqArb0/1_PhArbFineGater wired through to CMDARB.

---

## Part 6: AI Flow vs Real Engineer Comparison

| Aspect | Real Engineer | AI Flow (latest) |
|--------|--------------|-----------------|
| 9868 NeedFreqAdj gate count | 4 cells (elegant XOR factorization) | 8 cells (literal decomposition) |
| 9868 MUX select gate | AND2 `AN2D1` | AND2 `AN2D3` (Step 3 confirmed) ✅ |
| 9868 DFF cell type | SDFQD1 (small) | SDFQD1 (same) ✅ |
| 9899 ToggleChn condition inputs | Manual lookup of gate-level names | FM auto-resolves `N2408127`/`phfnn_2405075` ✅ |
| 9899 condition gate count | ~15 cells (OA12, OAI21, AN3, ND3) | 14 gates (different but equivalent) |
| SVF | Empty | Empty ✅ |
| FM Pass | Yes | Pending Step 4/5 |

---

---

## Part 5B: Root Cause Summary — 9868 PostEco FM Failure (Session 2)

| Failing Point | Stage | Root Cause | Fix |
|--------------|-------|-----------|-----|
| `FEI_ARB_OutstRdDat_reg` | Synthesize | ARB_FEI_NeedFreqAdj undriven — NeedFreqAdj port missing from umcarbctrlsw port list (false ALREADY_APPLIED) | force_reapply + ABORT_LINK detection |
| `eco_9868_dff2` | PrePlace, Route | DFF0X — eco_9868_e002.A1 uses UNCONNECTED_xxx net which FM cannot trace through P&R hard macro | Mode H: needs_named_wire, declare named wire, rewire REGFILE bus |
| `FEI_ARB_OutstRdDat_reg_MB_...` | PrePlace, Route | Downstream of eco_9868_dff2 DFF0X — EcoUseSdpOutstRdCnt stuck at 0 | Same as above |
| `REG_UmcCfgEco_1_` unresolved | All | Never submitted to FM (Step D-POST missed) | rtl_diff_analyzer Step D-POST mandatory step |

---

## Part 5C: Root Cause Summary — 9899 PostEco FM Failure (Session 2)

| Failing Point | Stage | Root Cause | Fix |
|--------------|-------|-----------|-----|
| 3000 ARB DFFs | Synthesize | AI wrongly rewired A606036/A606254/A648153/A648363 (and_term scope error — cells in parent ARB scope, not inside DCQARB) | and_term scope validation — exclude parent-scope cells |
| `DCQARB_DebugBusValDcq` | PrePlace | Downstream side-effect of wrong and_term rewires | Excluded by Mode B after scope fix |
| `ToggleChn_reg` | Route | eco_9899_c008 missing (N2408127 not found in Route) + floating input inserted silently | 0b-GATE-STAGE-NETS per-stage resolution; eco_applier Step 1 guards floating inputs |

---

---

## Part 3C: Issues Found from Session 3 (Fresh Run Observation)

### Issue 26 — PORT_DECL sequential edits corrupt netlist when two ports applied to same module

**Symptom:** FM-599 (Verilog read error) — "Port 'EcoUseSdpOutstRdCnt' is not defined in module terminal list". `umcsdpintf` netlist corrupted: `input REG_RegClkGater , EcoUseSdpOutstRdCnt` followed by `    )input REG_RegClkGater ;`.

**Root cause:** Two PORT_DECL changes targeting `ddrss_umccmd_t_umcsdpintf` applied sequentially. After the first PORT_DECL (ARB_FEI_NeedFreqAdj) inserted lines into the port list and module body, line numbers shifted. The second PORT_DECL (EcoUseSdpOutstRdCnt) used a stale `port_list_close_idx` that now pointed to `input REG_RegClkGater ;` (no `)` in that line). `rfind(')')` = -1 in Python → removes last char (`;`) and duplicates the whole line — classic Python index -1 corruption.

**Fix applied:** `eco_applier.md` — added "CRITICAL: BATCH all PORT_DECL changes for the same module in ONE port list modification step" — collect all signal names for the module first, modify the port list close once, then insert all declarations. Also added: `assert ')' in lines[port_list_close_idx]` guard to catch wrong close line before any modification.

---

### Issue 27 — eco_applier cell_type lookup uses name prefix (too specific for library)

**Symptom:** Round 2 eco_applier inserted `eco_9868_e002` as `ND2D1BWP` (NAND2) instead of `AN2D1BWP` (AND2). NAND2 uses output pin `ZN` but study JSON has pin `Z` → FM-599 FE-LINK-7: "pin 'Z' has no corresponding port on ND2D1BWP". All 3 FM targets abort.

**Root cause:** eco_applier Step 2 grepped for `AND2[A-Z0-9]*` to find the AND2 library cell. But TSMC/AMD library uses `AN2D1BWP` (not `AND2...`) — the prefix pattern is technology-specific. With 0 hits, eco_applier fell back and found `ND2D1BWP` (NAND2) — wrong cell type.

**Fix applied:**
- `eco_applier.md` Step 2: Changed from name-prefix grep to **port-structure search** — extract required port names from `port_connections` (e.g., `{A1, A2, Z}`), then search PreEco for any cell in same module scope that has ALL those ports. Technology-library-agnostic, works for TSMC, GF, or any other library.
- `eco_applier.md` Step 1b: Added cell_type verification — look up `cell_type` in PreEco netlist to find its actual ports, verify all `port_connections` keys exist. If not → clear `cell_type` and force re-search.
- `eco_fm_analyzer.md`: Added `ABORT_CELL_TYPE` detection — when FE-LINK-7 names a tech library cell (`/TECH_LIB_DB/...`) rather than a user design module, diagnose as cell_type mismatch → produce `fix_cell_type` revised_changes.
- `eco_netlist_studier.md RE_STUDY_MODE`: Added `ABORT_CELL_TYPE` handling — re-searches PreEco by port structure to find correct cell_type.

---

### Issue 28 — eco_step3_netlist_study RPT naming inconsistent with other round files

**Symptom:** File listing showed:
- `eco_step3_netlist_study.rpt` (no round number — initial)
- `eco_step3_netlist_study_round1.rpt` (re-study for round 1 failure)
- But eco_applied and FM verify ALWAYS use round numbers: `eco_step4_eco_applied_round1.rpt`, `eco_step5_fm_verify_round1.rpt`

**Fix applied:** Renamed consistently across ORCHESTRATOR, ROUND_ORCHESTRATOR, FINAL_ORCHESTRATOR, eco_netlist_studier:
- Initial study (ORCHESTRATOR) → `eco_step3_netlist_study_round1.rpt`
- Round N re-study (ROUND_ORCHESTRATOR) → `eco_step3_netlist_study_round<NEXT_ROUND>.rpt`
All step3, step4, step5 RPTs now follow the same round-N naming pattern.

---

### Issue 30 — PORT_DECL batch inserts same port twice when study JSON has duplicate entries

**Symptom:** FM-599 (Verilog read error) — "You are declaring the direction of a port 'NeedFreqAdj'" in PrePlace and Route. PostEco netlist shows:
```
    dftopt_mbit , NeedFreqAdj
, NeedFreqAdj                   ← inserted twice in port list
) ;
  output  NeedFreqAdj ;
  output  NeedFreqAdj ;          ← declared twice in module body
```

**Root cause:** The PORT_DECL batching code (Issue 26 fix) collects all PORT_DECL entries for a module before applying. But the study JSON may contain the SAME signal twice:
- Once from the initial eco_netlist_studier (initial study entry)
- Once added by eco_netlist_studier_round_N with `force_reapply: True` (round 2 re-study)

Batching collected both entries, saw two NeedFreqAdj entries for `ddrss_umccmd_t_umcarbctrlsw`, and inserted both — creating a duplicate port list addition and duplicate `output` declaration → Verilog syntax error.

**Fix applied:** `eco_applier.md` PORT_DECL batching section — added **deduplication by signal_name** before applying:
```python
seen = {}
for e in entries:
    seen[e["signal_name"]] = e  # later entry (force_reapply) overwrites earlier
entries = list(seen.values())   # each signal appears once
```
Last entry wins — `force_reapply: True` entry overwrites the original, so the forced re-apply behavior is preserved while eliminating duplicates.

---

### Issue 29 — 9899 Step 4: A648153 SKIPPED, eco_9899_c009 Route A2 uses wrong net

**Symptom (fresh 9899 run):** Step 4 shows:
- `A648153 SKIPPED` in Synthesize — "not found in DCQARB1" (exists in PostEco but outside the `ddrss_umccmd_t_umcdcqarb_1` isolated buffer — P&R moved it)
- `eco_9899_c009 Route`: `.A1(FxPrePlace_ZINV_479_26) .A2(FxPrePlace_ZINV_479_26)` — both inputs same because A2 was `PENDING_NETLIST_SEARCH:DcqArb1_QualPhArbReqVld` (N2408127 not in Route)

**Root causes:**
1. A648153 in DCQARB1: in the hierarchical PostEco, the cell may exist at a higher scope than what the isolated module buffer covers → eco_applier can't find it inside `ddrss_umccmd_t_umcdcqarb_1`
2. eco_9899_c009 Route A2: N2408127 (DcqArb1_QualPhArbReqVld gate-level in Synthesize) renamed by P&R in Route → 0b-GATE-STAGE-NETS Priority 2/3 found same alias as A1 for both → OR2 with identical inputs = wrong logic

**Expected FM failures → Round 2 will address via:**
- A648153: Mode A (SKIPPED) → eco_fm_analyzer finds cell in different scope → eco_netlist_studier_round_2 updates rewire entry
- eco_9899_c009: Mode A (wrong gate inputs) → re-identify Route-equivalent of N2408127 → fix c009 A2

---

## Part 4: Guide Files Updated

| File | Key Changes (Session 1) | Key Changes (Session 2) | Key Changes (Session 3) |
|------|------------------------|------------------------|------------------------|
| `CRITICAL_RULES.md` | Rules 11-25 | — | — |
| `rtl_diff_analyzer.md` | D-MUX polarity; E4d PENDING chain; V3 name resolution | Step D-POST mandatory | — |
| `eco_netlist_studier.md` | 0b-STAGE-NETS; 0c pivot; 0c-5 PENDING; 0f; 4c-POLARITY; 0g | needs_named_wire; 0b-GATE-STAGE-NETS; and_term scope; RE_STUDY_MODE | ABORT_CELL_TYPE handling; fix_cell_type action |
| `eco_applier.md` | PORT_DECL wire; PORT_PROMO boundary; PORT_CONN depth; DFF per-stage; GATE library cell | ALREADY_APPLIED rules; force_reapply; needs_named_wire Step 0; Step 1 guard; module-extraction streaming | PORT_DECL batching (Issue 26); PORT_DECL dedup by signal_name (Issue 30); cell_type port-structure lookup (Issue 27); cell_type Step 1b guard |
| `eco_svf_updater.md` | Complete rewrite | ROUND_FAILED/ROUND_NEXT | — |
| `eco_fenets_runner.md` | Step C2; FM-036 pivot | RERUN_MODE | — |
| `eco_fm_analyzer.md` | Check D polarity; Mode F/G | Check E; Mode H; Check F; ABORT_LINK; Step 3b | ABORT_CELL_TYPE; Step 0c-2b cell_type detection |
| `eco_fm_runner.md` | — | HARD RULE: one FM run | — |
| `ORCHESTRATOR.md` | Step 4c pre-FM checks | HARD RULE one FM; ai_eco_flow_dir in fixer_state | eco_step3 renamed to _round1 |
| `ROUND_ORCHESTRATOR.md` | Mode F/G early exit | ai_eco_flow_dir in handoff; ROUND_FAILED; Step 6f-FENETS; Mode H; HARD RULE | ABORT_CELL_TYPE; eco_step3 _round<NEXT_ROUND>; ROUND_ORCHESTRATOR spawn passes AI_ECO_FLOW_DIR |
| `FINAL_ORCHESTRATOR.md` | — | Per-round re-study RPTs | eco_step3 _round1 naming |

---

## Part 6: Key Metrics

| Metric | Value |
|--------|-------|
| Total ECO rounds run | ~30+ across both JIRAs (3 sessions) |
| Guide files updated | 11 |
| New CRITICAL_RULES added | 15 (Rules 11-25) |
| Total guide lines | **~6,400** |
| Root causes identified (Session 1) | 16 distinct issues |
| Root causes identified (Session 2) | 9 additional issues (Issues 17-25) |
| Root causes identified (Session 3) | 5 additional issues (Issues 26-30) |
| Total root causes | **30** |
| Flow bugs fixed (all sessions) | ORCHESTRATOR FM loop; round_handoff missing ai_eco_flow_dir; svf_updater wrong ROUND; eco_applier ALREADY_APPLIED broad check; floating input silent insert; large file stale line numbers; PORT_DECL sequential corruption; cell_type prefix grep mismatched library; step3 RPT naming inconsistency |
| New flow capabilities (all sessions) | Round N re-study; round N fix apply; rerun_fenets; Mode H (hierarchical port bus); ABORT_LINK; ABORT_CELL_TYPE; 0b-GATE-STAGE-NETS; and_term scope check; module-extraction streaming; port-structure cell lookup |

---

## Part 3D: Issues Found from Session 4 (Deep Debugging + Flow Overhaul)

---

### Issue 31 — SVF prohibition: AI flow was applying set_dont_verify and tune files instead of fixing netlist (9868 Rounds 2-6)

**Symptom:** 9868 took 6 rounds via SVF `set_dont_verify`, `set_user_match`, and tune file patches instead of fixing the netlist. `eco_9868_dff1` had wrong SE pin in PrePlace/Route (HFS-renamed net not corrected). `eco_9868_dff2` had wrong UNCONNECTED net in P&R stages. All were suppressed via SVF instead of fixed.

**Root cause:** eco_fm_analyzer was classifying ECO-inserted DFFs as Mode E (pre-existing) and producing `set_dont_verify` actions. eco_netlist_studier was applying these via eco_svf_updater. Tune file changes were being written as fixes.

**Fixes applied:**
- **RULE 27**: SVF updates are permanently prohibited for the AI flow. Engineers apply SVF manually.
- **Step 4b permanently disabled** in ORCHESTRATOR and ROUND_ORCHESTRATOR.
- **eco_fm_analyzer** Mode E Condition 0: ECO-inserted DFFs (`eco_<jira>_` pattern) CANNOT be Mode E — abort classification immediately.
- **eco_fm_analyzer** Mode E Condition 3: HFS-renamed net = Mode H (fix_named_wire), NOT Mode E suppression.
- **eco_svf_updater.md**: Added ⛔ DISABLED notice at top — EXIT IMMEDIATELY if spawned.
- **eco_fm_analyzer RULE 3**: NEVER write `set_dont_verify` or `set_user_match` — outputs `manual_only` instead.
- **RULE 22 updated**: Fix netlist first; `set_dont_verify` only after Priority 3 structural trace confirms net is truly absent.

---

### Issue 32 — DFF instance naming: FM can't auto-match ECO DFFs to RTL registers (9868)

**Symptom:** `FEI_ARB_OutstRdDat_reg` fails in Synthesize across all previous 9868 runs. FM cannot trace the cone because it can't match `eco_9868_dff1` (gate-level) to `NeedFreqAdj_reg` (RTL). Required `set_user_match` which is now prohibited.

**Root cause:** ECO DFFs were named `eco_<jira>_dff<N>` — does not match the name FM synthesizes from the RTL (`<target_register>_reg`). FM's `-r` mode requires name match for auto-matching in `FmEqvEcoSynthesizeVsSynRtl`.

**Fix applied:**
- **RULE 10b**: DFF insertions use `<target_register>_reg` as instance name, `<target_register>` as Q output net. This matches FM's RTL synthesis name → FM auto-matches without any `set_user_match`.
- Updated: CRITICAL_RULES.md, eco_netlist_studier.md Step 0d, eco_applier.md RULE 4, rtl_diff_analyzer.md Step E.

**Result:** `FmEqvEcoSynthesizeVsSynRtl` PASSED in Round 1 of the latest 9868 run (tag 20260425220523).

---

### Issue 33 — eco_applier ALWAYS_APPLIED does not verify port connections match (concurrent agent corruption)

**Symptom:** When PostEco was reset to PreEco and eco_applier ran, it reported ALREADY_APPLIED for all entries. FM then failed because the pre-existing cells had wrong connections (from a concurrent old ROUND_ORCHESTRATOR that had re-applied after the reset).

**Root cause:** ALREADY_APPLIED check only verified instance existence (`grep -c instance_name`), not whether input port connections matched the study JSON. A cell with the correct name but wrong connections was accepted as correctly applied.

**Fixes applied:**
- **eco_applier ALREADY_APPLIED** for `new_logic_gate`/`new_logic_dff`: Step 2 mandatory — verify ALL input pin connections match the expected nets from study JSON. If any mismatch → NOT ALREADY_APPLIED → UNDO + REAPPLY.
- **PRE-FLIGHT MD5 check**: Before ANY edits, compare PostEco vs PreEco (Round 1) or vs ROUND_ORCHESTRATOR Step 6b backup (Round 2+). If mismatch → restore automatically.
- **UNIVERSAL RULE**: eco_applier NEVER adds explicit `wire N;` declarations — all ECO nets are connected via port connections (implicit wires). Explicit wire + implicit wire = FM SVR-9 → FM-599.

---

### Issue 34 — PORT_DECL: port name added to body but NOT to module port list header (9868 latest run)

**Symptom:** `FmEqvEcoPrePlaceVsEcoSynthesize` and `FmEqvEcoRouteVsEcoPrePlace` both abort with FM-599: `Port 'NeedFreqAdj' is not defined in module terminal list` and `Port 'ARB_FEI_NeedFreqAdj' is not defined in module terminal list`. `FmEqvEcoSynthesizeVsSynRtl` PASSES because `-i` (IMPL) mode is lenient; `-r` (REF) mode is strict.

**Root cause:** In the BATCH PORT_DECL approach, **Step 2 (insert signal name into port list header) was written as a COMMENT, not actual executable code**. The AI agent read the code, implemented Step 3 (direction declarations in body) but skipped Step 2 (port list modification). Result: `output NeedFreqAdj;` in body ✓, `NeedFreqAdj` missing from `module M(...)` ✗.

**Fixes applied:**
- **eco_applier.md BATCH PORT_DECL**: Step 2 made explicit actual code — `find_module_start()`, `find_port_list_close()`, `new_signals_str = ''.join(...)`, `lines[port_list_close_idx] = ...`, with mandatory post-insertion verification that all signal names appear in modified header.
- **Validator Check 9** (`check_declaration_not_in_header`): Scans every `output X;`/`input X;` in module body — verifies `X` appears in module port list header. Catches the exact bug before FM submission.
- **eco_applier post-insertion check**: After Step 2, `re.search(rf'\b{signal_name}\b', modified_header)` — if not found → RuntimeError → VERIFY_FAILED → no recompress.

---

### Issue 35 — eco_fm_runner doesn't fix ABORT_NETLIST inline — wastes a full round

**Symptom:** FM-599 ABORT_NETLIST (Verilog syntax error) triggers a full ROUND_ORCHESTRATOR cycle (~2 hours) when the fix takes seconds.

**Fix applied:**
- **eco_fm_runner.md**: Added ABORT_NETLIST inline fix exception to the EXIT IMMEDIATELY rule. When FM-599 detected: run `validate_verilog_netlist.py` on PostEco stages → identify error (Check 9, F3, F5) → apply targeted fix → re-run FM immediately. Max 1 retry. All other ABORT types still EXIT immediately.

---

### Issue 36 — eco_fm_analyzer: deep D-input chain trace missing — misses real Mode H source

**Symptom:** `eco_9868_dff1` failing in PrePlace/Route → eco_fm_analyzer classified as Mode E → wrote SVF `set_dont_verify`. Real cause: `eco_9868_d006.A1 = IReset` (wrong in P&R — HFS-renamed to `FxPrePlace_HFSNET_168`). Mode H was on the GATE not the DFF.

**Root cause:** eco_fm_analyzer Check E only traced 1-2 hops from failing DFF. Didn't walk the entire D-input gate chain to find which specific gate had a P&R-inaccessible input.

**Fix applied:**
- **eco_fm_analyzer Check E**: Breadth-first walk of ALL ECO-inserted gates in D-input chain. For each gate, checks ALL input pins against P&R PreEco. When `synth_count > 0 AND par_count = 0` → Mode H diagnosed at that specific gate+pin, not at the DFF.
- **eco_netlist_studier RE_STUDY_MODE Mode H**: Sub-case H-RENAME — finds P&R alias via Priority 3 structural trace (driver cell anchor). Sub-case H-BUS — `NEEDS_NAMED_WIRE` as before. Updates `port_connections_per_stage[stage][pin]` for specific gate, sets `force_reapply: true`.
- **Step H5**: At RE_STUDY start, re-reads `mode_H_risk` flags from RTL diff JSON — proactively applies Priority 3 before FM failure.

---

### Issue 37 — eco_applier cascade skip blocks independent gates (9899)

**Symptom:** `eco_9899_c003` missing from PrePlace because `eco_9899_c008` was SKIPPED (unresolvable input). c003 has completely independent inputs (SplitActCtr[0]) with no dependency on c008.

**Fix applied:**
- **eco_applier RULE 6**: Only skip entries whose `input_from_change` DIRECTLY points to the SKIPPED entry. Gates with independent inputs must still be inserted. Conservative placeholder (`1'b0`) used for SKIPPED dependency inputs, not a full cascade skip.
- **eco_applier RULE 8**: Intra-pass dependency resolution — c_mux_final.S resolved after c010 inserted in same Pass 1.

---

### Issue 38 — intermediate_net_insertion pivot cell polarity not validated (9899 ToggleChn)

**Symptom:** 9899 ToggleChn_reg failing across Rounds 2-5 with same 4457 cascade count. Multiple polarity swaps didn't help. A2150230 is NR2D1 (INVERTING) — c_mux cascade constants were built assuming NON-INVERTING pivot.

**Root cause:** eco_netlist_studier Step 0c didn't check whether the pivot driver cell (A2150230) is INVERTING before building the c_mux cascade constant values.

**Fix applied:**
- **eco_netlist_studier Step 0c-3b**: After finding pivot cell, determine if it's INVERTING (NOR/NAND/INV prefixes) or NON-INVERTING. Adjust all `1'b0`/`1'b1` constants in the c_mux cascade accordingly. Record `pivot_driver_cell_type` in study JSON.
- **eco_fm_analyzer Mode F1**: When same failure count across 2+ rounds → try progressive strategies: `invert_cmux_constants`, `try_strategy_A_andterm`, `try_alternative_pivot`. Never declare `manual_only` until max rounds exhausted.
- **eco_fm_analyzer Mode F3**: Pre-existing DFF failing with large cascade (>100) → trace backward to ECO gates in cone → classify Mode A, not Mode E.

---

### Issue 39 — General Python validator missing from flow (no pre-FM Verilog syntax gate)

**Root cause:** All Verilog syntax errors (duplicates, wrong declarations, missing port headers) were discovered by FM after 1-2 hours. No fast pre-submission validator existed.

**Fix applied — new tool** `validate_verilog_netlist.py`:
- **Streaming** architecture (no OOM on large gz files)
- **`--modules` flag** (only scans ECO-touched modules — runs in seconds)
- **7 checks by default**: F1 (dup wire), F2 (implicit wire conflict), F3 (decl inside instance), F4 (dup port conn), F5 (corrupted port value), F6/F7 (unbalanced parens), Check 9 (decl not in header)
- **Integrated** into eco_applier Check 8 (on uncompressed temp file before recompress) and eco_pre_fm_checker Check F (on PostEco gz before FM)
- **Inline fix** in eco_pre_fm_checker and eco_fm_runner for F3, F5, Check 9

---

### Issue 40 — manual_only logic ambiguous: two exit conditions could contradict

**Symptom:** ROUND_ORCHESTRATOR had two exit conditions (after Step 6d and after Step 6f) that could disagree on whether to continue or exit.

**Fix applied:**
- **ROUND_ORCHESTRATOR**: Unified single exit rule — `not reapply_entries AND NEXT_ROUND >= max_rounds` → MANUAL_LIMIT. All other cases (no work but rounds remain, some manual_only, all manual_only but rounds remain) → continue to eco_applier.
- **CORE RULE**: `manual_only` means "try a different strategy next round" — NEVER an early exit unless truly at max rounds.

---

## Part 4 (Session 4 Updates): Guide Files Changed

| File | Session 4 Key Changes |
|------|-----------------------|
| `CRITICAL_RULES.md` | RULE 10b (DFF naming); RULE 22 (netlist fix first); RULE 27 (SVF prohibited); RULE 28-31 (eco_applier 4-pass, Phase 0 first, eco_fm_analyzer Check F, eco_pre_fm_checker checks); eco_svf_updater DISABLED |
| `eco_applier.md` | PRE-FLIGHT MD5 check; UNIVERSAL RULE (no wire decls); ALREADY_APPLIED verifies connections; Check 5/6/7/8 (validator); PORT_DECL Step 2 as actual code; post-insertion Check 9 verification; cascade skip fix; intra-pass dependency; port-structure validation |
| `eco_netlist_studier.md` | RULE 10b Step 0d; implicit wire check; scope_is_tile_root; mode_H_risk propagation; Priority 3 structural trace; RE_STUDY_MODE per-gate fix_named_wire; Step H5 mode_H_risk re-read; PENDING loop prevention; pivot cell polarity Step 0c-3b |
| `eco_fm_analyzer.md` | NEVER set_dont_verify/set_user_match; Mode E Conditions 0+3; Mode F1 progressive strategies; Mode F3 (pre-existing DFF cascade); deep D-input chain trace Check E; RULE 3 SVF prohibition; structural mismatch detection |
| `eco_fm_runner.md` | ABORT_NETLIST inline fix (max 1 retry); updated EXIT IMMEDIATE exception |
| `ORCHESTRATOR.md` | Step 4b DISABLED; SVF prohibited; RESUMPTION CHECK |
| `ROUND_ORCHESTRATOR.md` | Step 4b DISABLED; unified manual_only exit rule; Mode E/G → manual_only; Surgical Patch Mode Step 6b |
| `rtl_diff_analyzer.md` | and_term_gate_input (port name not flat_net); D-IMPLICIT-WIRE (implicit wire detection); D-STAGE-VERIFY (cross-stage gate input check) |
| `eco_pre_fm_checker.md` | Step 0 VERIFY_FAILED gate; Check F2 fix (wire-only); Check G (direction completeness); Check 8 validator; inline fix attempt with re-validate |
| `eco_svf_updater.md` | ⛔ DISABLED notice; EXIT IMMEDIATELY if spawned |
| `validate_verilog_netlist.py` | **NEW** — streaming Verilog validator; Checks F1-F7, Check 9; `--modules` fast mode |

---

## Part 6 (Updated): Key Metrics

| Metric | Value |
|--------|-------|
| Total ECO rounds run | ~50+ across both JIRAs (4 sessions) |
| Guide files updated | 12 (+ new validator script) |
| New CRITICAL_RULES added | 7 more (Rules 10b, 22, 27-31) → **Total 22 rules** |
| Total guide lines | **~8,200** |
| Root causes identified (Session 1) | 16 |
| Root causes identified (Session 2) | 9 |
| Root causes identified (Session 3) | 5 |
| Root causes identified (Session 4) | 10 (Issues 31-40) |
| **Total root causes** | **40** |
| New tool created | `validate_verilog_netlist.py` — 7 Verilog syntax checks, streaming, fast |
| SVF approach | Permanently prohibited for AI flow (RULE 27) |
| DFF naming | Changed to `<target_register>_reg` (RULE 10b) — FM auto-match |
| Best 9868 result | 10/10 Steps 1-4 in latest run (20260425220523); Synthesize FM PASS Round 1 |
| Best 9899 result | 9.5/10 Steps 1-2 in latest run (20260426002406); Step 3 running |
