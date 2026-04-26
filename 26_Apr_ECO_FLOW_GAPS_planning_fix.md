# ECO Auto-Flow — Gaps Planning & Fix Document
**Date:** 2026-04-26
**Session:** 9874 / 9868 / 9899 new runs with updated flow
**Status:** Waiting for all FM rounds to finish before applying fixes

---

## Summary

Three new ECO runs started today to validate the updated flow on both simple (9874 wire_swap) and complex (9868, 9899 multi-module DFF+gate) ECOs. Multiple gaps observed. This document records each gap for systematic fixing after all FM runs complete.

---

## GAP-1 — eco_netlist_studier: FM cell/pin path stored as wire name

**Severity:** CRITICAL
**Observed in:** 9899 Round 1 → caused ABORT_NETLIST in Round 2 FM
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
DcqArb0_QualPhArbReqVld and DcqArb1_QualPhArbReqVld were resolved via FM fenets as `A2336162/ZN` and `A2230141/ZN`. The studier stored these FM cell/pin path notations directly as wire names in `port_connections_per_stage`. Verilog SVR-4 error: `/` is not valid in net identifiers → FM-599 ABORT_NETLIST.

**Root cause:**
The studier reads FM results in the format `i:/FMWORK.../cell_name/pin_name` and should extract just the net driven by that pin. Instead it stored the path notation verbatim.

**Fix required:**
After resolving any signal via FM equivalent nets, always look up the actual wire name by grepping the PreEco netlist:
```bash
grep -m1 "<cell_name>" <REF_DIR>/data/PreEco/Synthesize.v.gz
# Read the .<output_pin>(<wire_name>) from the instance block
# Use <wire_name> — never use <cell_name>/<pin_name> notation
```
Add this as a mandatory step in eco_netlist_studier.md UNIVERSAL REAL-NET PREFERENCE RULE and PENDING_FM_RESOLUTION resolution section.

---

## GAP-2 — validate_verilog_netlist.py: No check for invalid net name characters

**Severity:** HIGH
**Observed in:** 9899 Round 2 — validator ran but PASSED despite `A2336162/ZN` in netlist
**File:** `script/validate_verilog_netlist.py`

**What happened:**
The validator checks F1/F2/F3/F4/F5/Check9 but has no check for invalid characters (like `/`, `\`, spaces) in net names. `A2336162/ZN` slipped through.

**Fix required:**
Add Check F6 — Net name validity:
```python
def check_invalid_net_names(mod_lines, mod_name, start_lineno):
    """F6: Detect net names containing '/' or other invalid Verilog identifiers."""
    for i, line in enumerate(mod_lines):
        for m in re.finditer(r'\.\s*\w+\s*\(\s*(\S+?)\s*\)', line):
            net = m.group(1)
            if '/' in net or '\\' in net:
                errors.append({
                    'check': 'F6_invalid_net_name',
                    'module': mod_name,
                    'msg': f"Net name '{net}' contains invalid character — FM SVR-4",
                    'line': start_lineno + i
                })
```
Always include F6 in default mode (not --strict only) — `/` in a net name is always a bug.

---

## GAP-3 — eco_pre_fm_checker: Check 8 SKIPPED in Round 1 — missed inline fix opportunity, wasted a full round

**Severity:** HIGH (CRITICAL impact on round efficiency)
**Observed in:** 9899 Round 1 Step 5
**File:** `config/eco_agents/eco_pre_fm_checker.md`

**What happened:**
Round 1 Step 5 ran but the validator (Check 8) was SKIPPED. `A2336162/ZN` invalid net names were already in Synthesize PostEco but went undetected.

**Impact — a full round was wasted:**
If the validator had run with F6 (invalid net name check), the flow would have been:
```
Step 5 Round 1:
  Check A/C: Route SKIPPED → 3 inline fix attempts → UNRESOLVABLE (correct)
  Check 8:   DETECTS A2336162/ZN (F6 invalid net name)
             INLINE FIX: grep PreEco → A2336162.ZN drives phfnn_2405075 → replace
             Re-validate: PASS
  Result: Escalate ONLY for Route issue → Round 2 FM runs with clean Verilog
```

Instead, without validator:
- Round 1: escalated without fixing the Verilog bug
- Round 2: eco_fm_analyzer classified everything as manual_only without seeing the Verilog issue
- Round 2: FM submitted with corrupt Verilog → ABORT_NETLIST → wasted FM slot (1-2 hours)
- Round 3: finally fixed

**The validator being SKIPPED cost 1 extra round + 1 wasted FM run.**

**Fix required:**
1. The validator MUST always run (not SKIPPED unless script genuinely absent)
2. F6 invalid net name check must be in default mode (GAP-2)
3. When F6 is detected: eco_pre_fm_checker must apply inline fix:
   - Parse the invalid net name (e.g., `A2336162/ZN`)
   - Grep PreEco for the cell: `grep "A2336162" <PreEco/Synthesize.v.gz>` → find `.ZN(<actual_wire>)`
   - Replace `A2336162/ZN` with `<actual_wire>` in PostEco
   - Re-validate → if pass, rerun Step 5 (do NOT escalate to ROUND_ORCHESTRATOR just for this)
4. Only escalate to ROUND_ORCHESTRATOR when Route-level issues (UNRESOLVABLE) remain after all inline fixes

---

## GAP-4 — eco_pre_fm_checker: manual_only entries should not suppress Verilog error check

**Severity:** HIGH
**Observed in:** 9899 Round 2 Step 5
**File:** `config/eco_agents/eco_pre_fm_checker.md`

**What happened:**
In Round 2, eco_fm_analyzer classified Route c007/c008/c009 as `manual_only`. eco_pre_fm_checker saw Check A/C as "unresolvable → allow FM to run." BUT — Synthesize still had corrupt Verilog (`A2336162/ZN`) from Round 1. The manual_only classification for Route should NOT suppress the Verilog syntax check for Synthesize. FM ran with corrupt Synthesize → ABORT_NETLIST.

**Fix required:**
Add explicit rule in eco_pre_fm_checker.md:
> "Check 8 (Verilog validator) ALWAYS runs regardless of manual_only status. manual_only classification (from eco_fm_analyzer) only suppresses Check A/C escalation — it NEVER suppresses Verilog syntax validation. A FAIL in Check 8 blocks FM submission even when all Check A/C issues are classified manual_only."

---

## GAP-5 — eco_netlist_studier: A648153 (DCQARB1 A2 pin) missing from PrePlace

**Severity:** HIGH
**Observed in:** 9899 Round 3 FM — PrePlace FAIL on DCQARB1/DebugBusValDcq_reg
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
Step 3 confirmed A648153 (DCQARB1 A2 pin, old_net=QualPmArbWinVld_d1) in Synthesize only. PrePlace showed A648363 (A1 pin) but not A648153 (A2 pin). The studier reported "No Equivalent Nets" for A648153 in PrePlace and used Stage Fallback — but did not find the correct PrePlace cell for A648153.

**Root cause:**
The PrePlace equivalent of A648153 was likely `FxPrePlace_ctmTdsLR_1_5274047` (from the old passing run analysis), but the new studier's structural trace failed to find it.

**Fix required:**
In eco_netlist_studier.md, strengthen the Stage Fallback for rewire entries:
- When a cell cannot be confirmed in PrePlace via FM, do a secondary structural trace: find the Synthesize cell's driver net → grep PrePlace netlist for cells consuming that net → find the A2 pin equivalent.
- The old passing run (20260421195535) found this cell — its step3 analysis should be referenced as a validation pattern.

---

## GAP-6 — eco_applier: ALREADY_APPLIED for manual_only SKIPPED entries in Route

**Severity:** MEDIUM
**Observed in:** 9899 Round 3 Step 4 — eco_9899_c007/c008/c009/c_mux3/c_mux_final showed ALREADY_APPLIED in Route when they were SKIPPED since Round 1
**File:** `config/eco_agents/eco_applier.md`

**What happened:**
In Surgical Patch mode (Round 3), eco_applier's ALREADY_APPLIED check for eco_9899_c007 in Route returned ALREADY_APPLIED because the entry had `confirmed: true` and `force_reapply: false` (no force_reapply for Route since it was manual_only). The eco_applier treated "not in force_reapply list" as ALREADY_APPLIED without verifying the instance actually exists in the PostEco netlist.

**Fix required:**
In eco_applier.md ALREADY_APPLIED detection (Section 11), add special handling for manual_only entries:
```python
# For Surgical Patch mode: if entry is not in force_reapply list AND
# was SKIPPED in the previous round (check prior eco_applied_round<ROUND-1>.json):
prior_status = get_prior_status(entry, ROUND-1)
if prior_status == "SKIPPED":
    # Don't mark as ALREADY_APPLIED — it was never inserted
    # Mark as SKIPPED with same reason as prior round
    record(status="SKIPPED", reason=f"Carried from Round {ROUND-1}: {prior_reason}")
```

---

## GAP-7 — 9868 Step 1: incorrect wire declaration note

**Severity:** MEDIUM
**Observed in:** 9868 Step 1 RTL diff RPT
**File:** `config/eco_agents/rtl_diff_analyzer.md`

**What happened:**
Step 1 for 9868 noted `FEI.ARB_FEI_NeedFreqAdj` port_connection in umccmd as "new wire, must add wire decl." ARB_FEI_NeedFreqAdj appears in 2 port_connections (ARB output + FEI input) → Verilog implicit wire. UNIVERSAL RULE: never add explicit `wire N;`.

**Fix required:**
In rtl_diff_analyzer.md, strengthen the implicit wire detection:
- When a signal appears in ≥2 port_connection entries in the same module → set `no_wire_decl_needed: true` and add note "implicit wire from multiple port connections — eco_applier MUST NOT add explicit wire declaration"
- The "must add wire decl" note should never appear — remove it from any existing examples or templates

---

## GAP-8 — 9868 Step 2: EcoUseSdpOutstRdCnt DFF not mentioned in RPT

**Severity:** MEDIUM
**Observed in:** 9868 Step 2 RPT
**File:** `config/eco_agents/eco_fenets_runner.md`

**What happened:**
Step 1 identified two new DFFs: NeedFreqAdj (in ARB/CTRLSW) and EcoUseSdpOutstRdCnt (in umccmd). Step 2 only mentioned NeedFreqAdj and RegRdbRspCredits. EcoUseSdpOutstRdCnt DFF was not discussed at all in Step 2.

**Fix required:**
eco_fenets_runner.md should explicitly note when new_logic_dff entries do not require FM queries (new signals not in PreEco) — include a section:
```
No FM query needed for new DFF signals (not in PreEco reference):
  - <signal_name>: new_logic_dff — eco_netlist_studier Phase 0 handles insertion
  - <signal_name2>: new_logic_dff — same
```
This ensures eco_netlist_studier knows all DFFs to process, not just the ones appearing in FM results.

---

## GAP-9 — 9868 Step 2: "wire_swap" classification misleading for AND2 gate insertion

**Severity:** LOW
**Observed in:** 9868 Step 2 RPT
**File:** `config/eco_agents/eco_fenets_runner.md`

**What happened:**
The FEI_ARB_OutstRdDat MUX select change was described as "wire_swap in umcsdpintf" but actually requires inserting a new AND2 gate (eco_9868_c001) and then rewiring the MUX S pin to the gate output. This is NOT a simple wire swap.

**Fix required:**
In eco_fenets_runner.md, when the change involves both gate insertion AND rewire, classify it as "gate_insertion_with_rewire" or "new_logic_gate + rewire" in the RPT to clearly communicate to eco_netlist_studier that a new cell must be inserted, not just a net substituted.

---

## GAP-10 — 9899 MUX cascade priority inversion concern

**Severity:** LOW (pending FM validation)
**Observed in:** 9899 Step 1 gate chain analysis
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
The MUX cascade (c_mux1→c_mux_final) has cond4 as outermost (highest priority in gate output) but RTL has cond1 as highest priority. If conditions cond1-cond4 are NOT mutually exclusive, the priority inversion would cause incorrect behavior. FM will validate equivalence.

**Fix required (if FM confirms FAIL on Synthesize due to this):**
Reverse the MUX cascade order in eco_netlist_studier so the outermost MUX checks the highest-priority RTL condition (cond1), not the lowest.

If FM passes → conditions are mutually exclusive and no fix needed. Document the finding either way after FM completes.

---

## GAP-11 — eco_pre_fm_checker JSON schema not followed by agent

**Severity:** MEDIUM
**Observed in:** 9874 Step 5 — agent wrote simplified JSON missing check_summary, validator result
**File:** `config/eco_agents/eco_pre_fm_checker.md`

**What happened:**
The eco_pre_fm_checker agent ignored the JSON schema defined in STEP 4 and wrote its own simplified version (missing `check_summary`, `check8_verilog_validator`, `tag`, `attempts`, `issues_fixed`).

**Fix applied today:** Schema now at the TOP of the file as an output contract + mandatory self-check assertions before write + orchestrator validates schema on read.

**Status: FIXED today** — monitoring next runs to confirm.

---

## GAP-12 — Step 3 RPT showing `?` for signal names and scope

**Severity:** MEDIUM
**Observed in:** 9868 and 9899 Step 3 RPTs — showed `?` for all port_declaration signal names and `scope=?` for port_connections
**File:** `config/eco_agents/ORCHESTRATOR.md` (RPT generator)

**What happened:**
The ORCHESTRATOR's Step 3 RPT generator used `e.get('cell_name','?')` generically. For port_declaration entries, the key is `signal_name`. For port_connection, it's `instance_name`. Wrong keys → `?` everywhere in the RPT.

**Fix applied today:** RPT generator updated with per-change-type helper functions that use the correct field names.

**Status: FIXED today** — RPTs regenerated for 9868 and 9899 to confirm.

---

## GAP-13 — eco_netlist_studier: manual_only declared too early — engineers can fix Route signal restructuring

**Severity:** HIGH
**Observed in:** 9899 — Route eco_9899_c007/c008/c009/c_mux3/c_mux_final declared manual_only
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
DcqArb1_QualPhArbReqVld was absent from Route after P&R optimization. Priority 1 (net name grep), Priority 2 (alias search), and Priority 3 (structural driver trace via A2230141 cell) all returned 0 matches → classified as `manual_only`. Engineers however CAN find and fix this.

**What engineers do that the flow doesn't:**

1. **Trace backward from ToggleChn_reg.D in Route** — instead of tracing FROM the absent signal, trace BACKWARD from the DFF D-input in Route. The gate feeding ToggleChn_reg.D is the pivot net anchor. From there, the backward cone will reveal what signals ARE present in Route for the condition logic.

2. **Search from DCQARB1 module output boundary** — DcqArb1_QualPhArbReqVld is internal to DCQARB1. In Route, even if the internal net was restructured, find the cell in DCQARB1 scope that was previously driven by DcqArb1_QualPhArbReqVld and trace its Route P&R equivalent via fanout from DCQARB1's output ports.

3. **Cross-reference against Synthesize fanout** — find all cells in Synthesize that consume `N2408127` (the resolved gate-level name for DcqArb1_QualPhArbReqVld), then search for those same cell instances in Route PostEco to find their input nets.

4. **Use 1'b0 constant as partial fix** — if DcqArb1 truly cannot be found, eco_9899_c007 can be built as `OR2(DcqArb0_QualPhArbReqVld, 1'b0)` = effectively using only DcqArb0's signal. This changes behavior (only DCQ0's phase arb request is checked) but allows the cascade to be inserted. Engineers must decide if this is functionally acceptable.

**Fix required:**
In eco_netlist_studier.md, add Priority 4 before declaring `manual_only`:

```
Priority 4 — Backward cone trace from the DFF D-input in the failing stage:
  1. Find the target DFF (e.g., ToggleChn_reg) in the failing stage PostEco
  2. Read its .D(<net>) pin value
  3. Trace backward through the gate chain (max 10 hops)
  4. At each gate, check if the gate's inputs contain any net resolvable to
     the absent signal's Synthesize equivalent (check gate instance names
     — same cell names often survive P&R renaming)
  5. If found: use that net for the gate input in this stage

Only after Priority 4 fails → declare UNRESOLVABLE (not manual_only unless
RULE 27 SVF prohibition applies to the specific fix type).
```

**Distinction: UNRESOLVABLE vs manual_only:**
- `UNRESOLVABLE` = signal genuinely absent and no structural equivalent found after all 4 priorities → eco_applier uses `1'b0` fallback if allowed
- `manual_only` = fix COULD be done but requires SVF (prohibited for AI flow per RULE 27)
- Current flow incorrectly uses `manual_only` when the issue is actually `UNRESOLVABLE` or fixable with Priority 4

---

## GAP-14 — UNIVERSAL RULE refinement: explicit wire declarations ARE needed for new intermediate nets

**Severity:** HIGH
**Observed in:** 9899 — engineer comparison revealed explicit wire declarations are correct for ECO intermediate nets
**Files:** `config/eco_agents/eco_applier.md`, `CRITICAL_RULES.md`

**What happened:**
The UNIVERSAL RULE "eco_applier NEVER adds explicit wire declarations" was applied too broadly. Comparison with engineer solution for 9899 shows:
- Engineer `wire ECO_9899_net20;` and `wire ECO_9899_net21;` — **CORRECT**, required for new intermediate nets
- AI Round 4 `wire QualPmArbWinVld_d1_orig;` — **INCORRECT** because `QualPmArbWinVld_d1_orig` is created implicitly by `A2387450.ZN(QualPmArbWinVld_d1_orig)` — the driver cell output creates the net

**Refined UNIVERSAL RULE:**
- ❌ **NEVER** add explicit `wire N;` for nets created by **port connections** (e.g., `DcqArb0_PhArbFineGater` created by `.PhArbFineGater(DcqArb0_PhArbFineGater)` port connection in parent module) — implicit wire is sufficient
- ❌ **NEVER** add explicit `wire N;` for renamed original driver nets (e.g., `QualPmArbWinVld_d1_orig`) — the renamed driver cell output `.ZN(QualPmArbWinVld_d1_orig)` creates the implicit wire
- ✅ **MUST** add explicit `wire N;` for **new intermediate nets** between ECO-inserted gates when those nets don't exist anywhere in the netlist yet (e.g., `ECO_9899_net20` between INV and AND2 — this net exists nowhere, must be declared)

**Fix required:**
- Update CRITICAL_RULES.md RULE 10 and eco_applier.md to reflect the refined rule
- eco_netlist_studier.md: When creating intermediate nets between ECO gates, mark them as `needs_explicit_wire_decl: true`. eco_applier adds `wire N;` for these only.

---

## GAP-15 — eco_netlist_studier: and_term gate must drive module output port directly

**Severity:** CRITICAL
**Observed in:** 9899 Rounds 1-5 — 3000+ Synthesize failures persisted across all 5 rounds
**File:** `config/eco_agents/eco_netlist_studier.md`

**Note:** The AI does NOT need to match the engineer's exact implementation (any functionally correct gate combination is acceptable). The gap is that the **guidance is wrong on WHERE the gate output should go** — not HOW the gate is built.

**The principle (implementation-agnostic):**
For `and_term` changes where `<old_token>` is a module output port:
1. Find the cell currently driving `<old_token>` (the original driver)
2. Rename that cell's output from `<old_token>` to any valid intermediate net name (implicit wire — no explicit declaration needed)
3. Insert the new gating gate (any implementation: IND2, INV+AND2, NAND+INV, etc.) with:
   - Inputs: the intermediate net (pre-gate value) + the new AND term signal
   - **Output: `<old_token>` — the original port name**
4. All downstream consumers automatically see the gated value through the port
5. **No individual consumer rewires needed**

**Wrong approach (what the AI did — 3000 failures):**
New gate output → **new intermediate net** (`n_eco_..._DCQARB1`) → manually rewire 2-4 consumers → all other consumers still see ungated `<old_token>` value → thousands of FM failures.

**Wrong approach (Round 4 pivot fix):**
Correct concept but wrong scope: internal wire `<old_token>_orig` is driven by DIFFERENT P&R cell types per stage → FM cannot prove stage equivalence → PrePlace regression 2→4458.

**How to detect this situation:**
In eco_netlist_studier.md, before creating `port_connections_per_stage` for an `and_term` new gate:
```python
# Check if old_token is a module output port
is_module_port = grep_count(f"output.*{old_token}", module_body) > 0
           OR grep_count(f"{old_token}", module_header_port_list) > 0
if is_module_port:
    # GATE MUST DRIVE PORT DIRECTLY
    # output net = old_token (not n_eco_..._new)
    # rename original driver output to an intermediate net name
    # NO individual consumer rewires needed
else:
    # Normal and_term: gate drives new net, rewire specific consumers
```

**Fix required in eco_netlist_studier.md:**
Add a "Module Port Direct Gating" rule for and_term changes:
> "**CRITICAL:** Before choosing the gate output net, check if `<old_token>` is a module output port. If yes, the new gate output MUST be `<old_token>` (the port name itself). The original port driver is renamed to an intermediate net (any valid name — the cell output creates an implicit wire). This ensures ALL module port consumers automatically see the gated value without any individual rewires."

---

## GAP-16 — eco_fm_analyzer: wrong Mode B diagnosis led to counterproductive pivot fix strategy

**Severity:** HIGH
**Observed in:** 9899 Round 3→4 — eco_fm_analyzer proposed pivot fix that made PrePlace worse (2→4458 failures)
**File:** `config/eco_agents/eco_fm_analyzer.md`

**What happened:**
Round 3 had 3000 Synthesize failures. eco_fm_analyzer diagnosed Mode B (incomplete rewiring) and proposed the "pivot fix" — rename the original driver output to `_orig`, make the eco gate drive `QualPmArbWinVld_d1` directly. This is architecturally correct but was implemented INSIDE the DCQARB1/DCQARB modules, creating an internal wire `QualPmArbWinVld_d1_orig` driven by DIFFERENT cell types per stage:
- Synthesize: INR3D8 cell
- PrePlace: NR2D6 cell (P&R renamed)
- Route: NR2SKRD6 cell (P&R renamed)

FM stage-to-stage comparison failed to prove equivalence of the internal wire across stages → PrePlace regression 2→4458 failures.

**Root cause:**
eco_fm_analyzer proposed the correct CONCEPT (gate the port output) but in the WRONG LOCATION (inside module with different P&R cell types per stage). The intermediate `_orig` internal wire is invisible to FM at stage boundaries.

**Fix required:**
eco_fm_analyzer.md: When diagnosing "cascade from ungated module port" (3000+ failures in module scope), the recommended fix should be:
1. Check if `old_token` is a module output port
2. If yes → propose "module port direct gating" strategy (GAP-15 approach) instead of internal wire pivot
3. NEVER use intermediate internal wire `_orig` approach for signals that are module output ports — FM cannot match internal wires across P&R stages

---

## GAP-17 — 9899: 3000+ Synthesize failures persisted through 5 rounds — unresolved

**Severity:** HIGH
**Observed in:** 9899 Rounds 3, 4, 5 — Synthesize consistently fails at 3000-3071 points
**Root cause:** eco gate architecture never correctly resolved (GAP-15)

**What was tried:**
- Round 3: IND2 inside DCQARB1/DCQARB, rewire 4 consumers → 3000 Synth failures (other consumers ungated)
- Round 4: Pivot fix inside module (_orig intermediate wire) → 3071 Synth + 4458 PrePlace
- Round 5: Move eco gate to ARB parent module → 3071 Synth + 4460 PrePlace (no improvement)

**What should have been done from Round 1:**
Module port direct gating (GAP-15):
- IND2 inside DCQARB1: `A1=QualPmArbWinVld_d1_orig, ZN=QualPmArbWinVld_d1`
- All consumers see gated value through port → 0 failures expected

**Remaining gap:**
The flow hit max rounds (5) without resolving. FINAL_ORCHESTRATOR should be spawned with `MANUAL_LIMIT` status. Engineer can apply GAP-15 fix manually.

---

## GAP-18 — eco_fm_analyzer: driver search misses submodule bus output ports

**Severity:** HIGH
**Observed in:** 9868 Round 1 — `EcoUseSdpOutstRdCnt_reg` classified `manual_only` incorrectly
**File:** `config/eco_agents/eco_fm_analyzer.md`

**What happened:**
eco_fm_analyzer diagnosed `UNCONNECTED_3288` (= REG_UmcCfgEco[1]) as "undriven — no gate-level driver in umccmd scope" and classified the fix as `manual_only`. But `UNCONNECTED_3288` IS driven — it appears as an output element of the REGCMD submodule output bus:
```
UNCONNECTED_3287 , UNCONNECTED_3288 , SplitActCtrPhaseDis } ) ,  ← REGCMD .REG_UmcCfgEco bus output
```
REGCMD drives `UNCONNECTED_3288` as bit[1] of its `REG_UmcCfgEco[31:0]` output bus. The engineer simply renamed it `eco9868_UmcCfgEco_1` — no new gate needed.

**Root cause:**
eco_fm_analyzer's driver search pattern: `grep ".<pin>(<signal>)" module_body` — finds cells where the signal is on a single output pin (`.ZN(UNCONNECTED_3288)`). It does NOT find drivers where the signal appears as part of a multi-bit bus port connection: `{ ..., UNCONNECTED_3288, ... }`.

**Fix required:**
In eco_fm_analyzer.md, when checking if a signal is "undriven," also search for it as a submodule output bus element:
```bash
# Check 1: direct cell output pin
grep -c "\.<output_pin>( <signal> )" module_body

# Check 2: submodule output bus element (NEW)
grep -c "^\s*\.[A-Za-z_]\+\s*(\s*{[^}]*\b<signal>\b[^}]*}" module_body
# Or more simply:
grep -c "<signal>" submodule_port_connections | grep -v "input\|wire\|assign"
```
If signal appears as a submodule output bus element → it IS driven (by the submodule) → classify as **Mode H (net rename)** not `manual_only`. The fix is to rename the unconnected wire to a meaningful name and keep the existing connection.

---

## Pending After FM Completion

Once all FM runs finish, prioritize fixes in this order:

| Priority | Gap | File(s) |
|----------|-----|---------|
| P1 | GAP-1 — cell/pin path stored as wire name | eco_netlist_studier.md |
| P1 | GAP-2 — validator no F6 invalid net name check | validate_verilog_netlist.py |
| P1 | GAP-4 — manual_only should not suppress Verilog check | eco_pre_fm_checker.md |
| P2 | GAP-5 — A648153 PrePlace missing (Stage Fallback gap) | eco_netlist_studier.md |
| P2 | GAP-6 — ALREADY_APPLIED for manual_only SKIPPED entries | eco_applier.md |
| P1 | GAP-3 — validator SKIPPED: missed inline fix, wasted full round | eco_pre_fm_checker.md |
| P3 | GAP-7 — wire decl note in rtl_diff_analyzer | rtl_diff_analyzer.md |
| P3 | GAP-8 — EcoUseSdpOutstRdCnt not in Step 2 RPT | eco_fenets_runner.md |
| P3 | GAP-9 — wire_swap classification misleading | eco_fenets_runner.md |
| P1 | GAP-14 — eco_applier: wire_declaration type entries violate UNIVERSAL RULE | eco_applier.md |
| P1 | GAP-15 — eco_netlist_studier: and_term IND2 must drive module OUTPUT PORT directly | eco_netlist_studier.md |
| P2 | GAP-16 — eco_fm_analyzer: wrong Mode B diagnosis → counterproductive pivot fix | eco_fm_analyzer.md |
| P2 | GAP-17 — 9899: 3000+ Synth failures unresolved through 5 rounds | eco_netlist_studier.md |
| P2 | GAP-13 — manual_only too early: add Priority 4 backward cone trace before giving up | eco_netlist_studier.md |
| P4 | GAP-10 — MUX cascade priority (pending FM result) | eco_netlist_studier.md |

---

*Document created: 2026-04-26*
*Author: ECO Auto-Flow Session Analysis*
