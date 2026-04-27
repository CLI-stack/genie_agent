# ECO Auto-Flow Step Ratings — 2026-04-27
**Runs:** 9868 (20260427040902) and 9899 (20260427041359)

---

## Latest Runs Rating Table

| JIRA | Tag | Step 1 RTL Diff | Step 2 Fenets | Step 3 Netlist Study | Step 4 ECO Applier | Step 5 Pre-FM Check | Step 6 FM |
|------|-----|-----------------|---------------|----------------------|--------------------|---------------------|-----------|
| 9868 | 20260427040902 | 9/10 | 10/10 | 9/10 | pending | — | — |
| 9899 | 20260427041359 | 9.5/10 | 9.5/10 | 9/10 | pending | — | — |

---

## Step Notes

### 9868 (20260427040902)

**Step 1 — 9/10:**
- Gate chain naming fixed (NEW-GAP-3): EcoUseSdpOutstRdCnt chain uses `eco_9868_d001_EcoUse`, `eco_9868_d002_EcoUse` — no conflict
- NAND2 correctly identified for wire_swap gate
- Implicit wire warnings detailed
- 6 FM queries (more thorough)
- **Deduction:** EcoUseSdpOutstRdCnt chain scope still empty (NEW-GAP-4 persists)

**Step 2 — 10/10:**
- GAP-8 ✅: "NEW LOGIC DFF ENTRIES — NO FM QUERY REQUIRED" section present for both DFFs
- GAP-9 ✅: Correctly classifies as "wire_swap + new_logic_gate (NAND2 gate insertion)"
- FM-036 pivot on FEI_ARB_OutstRdDat resolved all stages
- Clear 5-step action for eco_netlist_studier

**Step 3 — 9/10:**
- 27/27 confirmed all 3 stages — perfect consistency
- NEW-GAP-3 ✅: `eco_9868_d001_EcoUse` / `eco_9868_d002_EcoUse` — no naming conflict
- NAND2 gate for wire_swap correct
- MUX rewire per stage (ctmi_523004 Synth, FxPrePlace_ctmTdsLR_2_4913443 PrePlace/Route)
- **Deduction:** EcoUseSdpOutstRdCnt chain `scope=` still empty (NEW-GAP-4 not resolved)

---

### 9899 (20260427041359)

**Step 1 — 9.5/10:**
- 12 changes all correctly identified
- and_term ECO plan correctly states "Insert gate in series with existing QualPmArbWinVld_d1 output" (GAP-15 language)
- Detailed pivot analysis (SEQMAP_NET_2948 ← A2150230 ← A2150336)
- Implicit wire warnings for DcqArb0/1_PhArbFineGater
- no_wire_decl_needed:true on all implicit wire port_connections

**Step 2 — 9.5/10:**
- GAP-1 ✅: Condition inputs resolved to REAL wire names: phfnn_2405075 and N2408127 (not A2336162/ZN)
- Primary drivers identified: A2234246 (DCQARB), A2387450 (DCQARB1)
- RULE 32: QualPmArbWinVld_d1 (real RTL net) used over HFS aliases
- FM-036 internal wire correctly handled with no retries
- CONDITION_INPUT_RESOLUTIONS section clean
- Minor: phfnn_2405075 is synthesis-generated (not P&R), could be noted explicitly

**Step 3 — 9/10:**
- 32/26/26 (Synth/PrePlace/Route) with correct cascade exclusion
- Duplicate CMDARB port connection GONE ✅ — FM-599 duplicate port issue fixed
- eco_9899_1_DCQARB now in ARB/DCQARB scope (closer to correct)
- Cascade exclusion labels: "EXCLUDED_PrePlace", "EXCLUDED_Route" — clear
- c007→c008/c009→c_mux3/c_mux_final cascade skip correct
- A648153 correctly excluded PrePlace/Route
- Per-stage rewire nets correct (HFS aliases per stage)
- Route _0 suffix handled correctly
- **Deduction:** GAP-15 still not fully applied — IND2 output = n_eco_9899_1_DCQARB not QualPmArbWinVld_d1 port; only 2 individual consumer rewires → likely 3000+ Synthesize failures

---

## Previous Runs (26 Apr) Reference

### 9874 (20260426041844) — Simple wire_swap — PASS Round 1
| Step | Rating | Notes |
|------|--------|-------|
| Step 1 | 10/10 | Single wire_swap, perfect |
| Step 2 | 9/10 | No-Equiv-Nets retry correct; PrePlace fallback |
| Step 3 | 10/10 | Exact match to previous passing run |
| Step 4 | 10/10 | 3 applied, 0 skipped, 0 verify_failed |
| Step 5 | 7/10 | JSON schema incomplete; validator SKIPPED |
| Step 6 | **PASS** | All 3 targets PASS Round 1 |

### 9868 (20260426042232) — 3 rounds
| Step/Round | Rating | Notes |
|------------|--------|-------|
| Step 1 | 8.5/10 | Wire decl note wrong |
| Step 2 | 8.5/10 | EcoUseSdpOutstRdCnt missing, wire_swap misleading |
| Step 3 R1 | 9.5/10 | RPT `?` was ORCHESTRATOR bug (JSON correct) |
| Step 4 R1 | 7.5/10 | PRE-FLIGHT MD5 not enforced (50 ALREADY_APPLIED) |
| Step 5 R1 | 9.5/10 | Most detailed; Check H verified |
| Step 6 R1 | FAIL | Mode H (IReset) + UNCONNECTED_3288 |
| Step 3 R2 | 8.5/10 | GAP-18: eco_9868_d009 wrong manual_only |
| Step 4 R2 | 9.5/10 | Clean Mode H fix |
| Step 5 R2 | 9/10 | GAP-18 miss on UNCONNECTED warning |
| Step 6 R2 | FAIL | Mode H NeedFreqAdj Route (aps_rename_12113_ wrong register) |
| Step 3 R3 | 9/10 | Correct IReset register (dftopt3065) |
| Step 4 R3 | 9.5/10 | Clean surgical patch |
| Step 5 R3 | 9/10 | Good validation |
| Step 6 R3 | FAIL | SE scan chain mismatch (tune file needed) |

### 9899 (20260426041316) — 5 rounds
| Step/Round | Rating | Notes |
|------------|--------|-------|
| Step 1 | 9/10 | 13-gate chain optimized |
| Step 2 | 9.5/10 | RULE 32 applied, condition inputs resolved |
| Step 3 R1 | 9/10 | A648153 missing PrePlace; cascade skip correct |
| Step 4 R1 | 7/10 | A2336162/ZN invalid net names in c007 |
| Step 5 R1 | 6/10 | Validator SKIPPED; schema wrong; pre_fm_check_failed |
| Step 6 R1 | N/A | FM blocked (pre_fm_check_failed) |
| Step 3 R2 | 5/10 | Missed corrupt c007; wrong manual_only |
| Step 4 R2 | 9/10 | No-op correct |
| Step 5 R2 | 6/10 | No F6 check; schema incomplete |
| Step 6 R2 | ABORT | A2336162/ZN SVR-4 |
| Step 3 R3 | 9.5/10 | Fixed A2336162/ZN → phfnn_2405075 |
| Step 4 R3 | 9/10 | 1 surgical fix; Route ALREADY_APPLIED gap |
| Step 5 R3 | 9/10 | Validator PASS with grep evidence |
| Step 6 R3 | FAIL | Route PASS; Synth FAIL 3000; PrePlace FAIL 2 |
| Step 3 R4 | 5/10 | Wrong pivot fix → PrePlace 2→4458 |
| Step 4 R4 | 6/10 | Explicit wire_declaration UNIVERSAL RULE violation |
| Step 5 R4 | 7/10 | All Verilog checks PASS; semantic issue not caught |
| Step 6 R4 | FAIL | Synth 3071; PrePlace 4458 |
| Step 3 R5 | 7/10 | Relocated eco gates to ARB scope (better direction) |
| Step 4 R5 | 9/10 | Clean surgical patch |
| Step 5 R5 | 9/10 | Detailed verification |
| Step 6 R5 | FAIL | Route PASS; Synth 3071; PrePlace 4460 — MAX_ROUNDS |

---

*Document created: 2026-04-27*
*Purpose: Preserve rating table before context compaction*
