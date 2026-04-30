# 9868 Engineer Solution Reference
**Path:** `/proj/cip_feint2_konark/konark/MECO/regr_0306/main/pd/tiles/ddrss_umccmd_t_DEUMCIPRTL-9868`
**Author note:** "Edited by Ram DEUMCIPRTL-9868 Eco Fix 03/25"
**Purpose:** Reference document for comparing AI flow output vs engineer solution step by step

---

## Summary of ECO

JIRA 9868 | Tile: umccmd | Files changed: rtl_umcarbctrlsw.v, rtl_umcarb.v, rtl_umccmd.v, rtl_umcfei.v, rtl_umcsdpintf.v

**RTL changes:**
1. `rtl_umcarbctrlsw.v` — new_logic DFF: `NeedFreqAdj` (output reg); new_port: NeedFreqAdj output
2. `rtl_umcarb.v` — new_port: ARB_FEI_NeedFreqAdj output wire; port_connection: CTRLSW.NeedFreqAdj
3. `rtl_umccmd.v` — new_logic DFF: `EcoUseSdpOutstRdCnt` (reg); wire declarations; port_connections: FEI.ARB_FEI_NeedFreqAdj + FEI.EcoUseSdpOutstRdCnt
4. `rtl_umcfei.v` — new_ports: ARB_FEI_NeedFreqAdj, EcoUseSdpOutstRdCnt; port_connections to SDPINTF
5. `rtl_umcsdpintf.v` — new_ports: ARB_FEI_NeedFreqAdj, EcoUseSdpOutstRdCnt; wire_swap: MUX select

---

## Engineer Gate-Level Solution (PostEco Synthesize)

### Change 1 — NeedFreqAdj DFF + D-input chain (module `ddrss_umccmd_t_umcarbctrlsw`)

**Gate chain — 4 cells (engineer) vs 7 cells (AI):**

```verilog
// Explicit wire declarations
wire eco9868_inv_req_z ;
wire eco9868_xor_z ;
wire eco9868_or4_z ;
wire eco9868_nr_z ;

// RTL: NeedFreqAdj = BeqCtrlPeReq & ~ArbCtrlPeRdy & (BeqCtrlPeSrc==3'b000 | BeqCtrlPeSrc==3'b011)
// Engineer decomposition (4 cells):
INVD1BWP...      eco9868_inv_req     ( .I(BeqCtrlPeReq), .ZN(eco9868_inv_req_z) )
XOR2D1BWP...     eco9868_xor_src10   ( .A1(BeqCtrlPeSrc[1]), .A2(BeqCtrlPeSrc[0]), .Z(eco9868_xor_z) )
  // XOR=0 when bits equal: covers 3'b000 (SELFREF) and 3'b011 (FADJ)
OR4D1BWP...      eco9868_or4         ( .A1(eco9868_inv_req_z), .A2(ArbCtrlPeRdy), .A3(BeqCtrlPeSrc[2]), .A4(eco9868_xor_z), .Z(eco9868_or4_z) )
  // or4_z=0 only when: BeqCtrlPeReq & ~ArbCtrlPeRdy & ~bit2 & XOR=0 → condition true
NR2D1SPG1AMDBWP... eco9868_nr_needfreqadj ( .A1(eco9868_or4_z), .A2(IReset), .ZN(eco9868_nr_z) )
  // ZN = ~(or4_z | IReset) = condition & ~IReset (sync reset baked in)

// DFF
SDFQD1AMDBWP136P5M117H3P48CPDLVTLL NeedFreqAdj_reg ( .D(eco9868_nr_z), .SI(1'b0), .SE(1'b0), .CP(UCLK01), .Q(NeedFreqAdj) )
```

**New port added to module header:**
```verilog
output NeedFreqAdj ;  // ECO DEUMCIPRTL-9868
```

**AI approach (7 cells):** NOR3 + INV + AND3 + OR2 + INV(ArbCtrlPeRdy) + INV(IReset) + AND4
- Same logic, different decomposition. Both are functionally correct.
- Engineer used XOR2 + OR4 approach: more compact.

---

### Change 2 — EcoUseSdpOutstRdCnt DFF (module `ddrss_umccmd_t_umccmd`)

```verilog
// Explicit wire declarations
wire eco9868_UmcCfgEco_1 ;  // renamed alias for UNCONNECTED_3288 = REG_UmcCfgEco[1]
wire eco9868_nr_cfg1_z ;
wire eco9868_inv_cfg1_z ;

// REGCMD port change: UNCONNECTED_3288 → eco9868_UmcCfgEco_1 (explicit name)
// In REGCMD instance port list:
// OLD: { ..., UNCONNECTED_3287, UNCONNECTED_3288, UNCONNECTED_3289, ... }
// NEW: { ..., UNCONNECTED_3287, eco9868_UmcCfgEco_1, SplitActCtrPhaseDis, ... }

// D-input chain (2 cells):
INVD1BWP...        eco9868_inv_cfg1    ( .I(eco9868_UmcCfgEco_1), .ZN(eco9868_inv_cfg1_z) )
NR2D1SPG1AMDBWP... eco9868_nr_cfg1     ( .A1(eco9868_inv_cfg1_z), .A2(IReset), .ZN(eco9868_nr_cfg1_z) )
  // ZN = ~(~REG_UmcCfgEco[1] | IReset) = REG_UmcCfgEco[1] & ~IReset

// DFF
SDFQD1AMDBWP136P5M117H3P48CPDLVTLL EcoUseSdpOutstRdCnt_reg ( .D(eco9868_nr_cfg1_z), .SI(1'b0), .SE(1'b0), .CP(UCLK01), .Q(EcoUseSdpOutstRdCnt) )
```

**AI approach (2 cells):** INV(IReset) → AND2(INV_out, UNCONNECTED_3288)
- Same gate count, different cell types (engineer uses NOR2 with inverted INV input, AI uses AND2)
- Engineer explicitly renamed UNCONNECTED_3288 → `eco9868_UmcCfgEco_1` for clarity
- AI kept UNCONNECTED_3288 directly as wire name

---

### Change 3 — Wire_swap: MUX select (module `ddrss_umccmd_t_umcsdpintf`)

```verilog
// New wire for MUX select
wire eco9868_new_sel ;

// AND2 gate: new MUX select logic
AN2D1BWP136P5M117H3P48CPDLVTLL eco9868_an2 ( .A1(EcoUseSdpOutstRdCnt), .A2(ARB_FEI_NeedFreqAdj), .Z(eco9868_new_sel) )

// MUX rewire: S pin changed from ctmn_2007473 to eco9868_new_sel
MUX2D2BWP136P5M156H3P48CPDLVT ctmi_523004 ( .I0(ctmn_517750), .I1(FEI0hi_debug_crashdump_comb_12_),
    .S(eco9868_new_sel),   // ECO DEUMCIPRTL-9868: was ctmn_2007473
    .Z(N6974) )
```

**AI approach:** Same — AND2 gate + MUX rewire. AI used `eco_9868_1` / `n_eco_9868_1`.
Both functionally identical. Only naming convention differs.

---

### Change 4 — Port declarations + port connections (umcarb, umcfei, umcsdpintf)

Engineer added ports and connections with `//ECO DEUMCIPRTL-9868` comments:

```verilog
// umcarb: NeedFreqAdj output port + port connection to CTRLSW + ARB_FEI_NeedFreqAdj wire
output ARB_FEI_NeedFreqAdj ;
.NeedFreqAdj( ARB_FEI_NeedFreqAdj ) ,  // CTRLSW instance

// umccmd: wire declarations + FEI port connections
wire ARB_FEI_NeedFreqAdj ;
.ARB_FEI_NeedFreqAdj(ARB_FEI_NeedFreqAdj) ,  // FEI instance
.EcoUseSdpOutstRdCnt(EcoUseSdpOutstRdCnt) ,  // FEI instance
.ARB_FEI_NeedFreqAdj(ARB_FEI_NeedFreqAdj) ,  // ARB instance

// umcfei: input ports + SDPINTF connections
input ARB_FEI_NeedFreqAdj ;
input EcoUseSdpOutstRdCnt ;
.ARB_FEI_NeedFreqAdj(ARB_FEI_NeedFreqAdj) ,  // SDPINTF instance
.EcoUseSdpOutstRdCnt(EcoUseSdpOutstRdCnt) ,  // SDPINTF instance

// umcsdpintf: input ports
input ARB_FEI_NeedFreqAdj ;
input EcoUseSdpOutstRdCnt ;
```

---

## Engineer Key Design Decisions (vs AI Flow)

| Decision | Engineer | AI Flow |
|----------|----------|---------|
| **NeedFreqAdj D-input cells** | 4 cells: INV+XOR2+OR4+NOR2 | 7 cells: NOR3+INV+AND3+OR2+INV+INV+AND4 |
| **EcoUseSdpOutstRdCnt D-input** | 2 cells: INV+NOR2 | 2 cells: INV+AND2 (same count) |
| **UNCONNECTED_3288 handling** | Renamed to `eco9868_UmcCfgEco_1` (explicit, readable) | Used `UNCONNECTED_3288` directly |
| **Wire_swap AND2 gate** | `eco9868_an2` → `eco9868_new_sel` | `eco_9868_1` → `n_eco_9868_1` |
| **MUX rewire** | Change `.S(ctmn_2007473)` → `.S(eco9868_new_sel)` | Same (correct) |
| **Explicit wire declarations** | `wire eco9868_*;` for all new nets | Mixed — some explicit, some not |
| **Cell naming convention** | `eco9868_<function>` (descriptive) | `eco_9868_d001` (sequential) |

**Key observation:** Both solutions are architecturally correct for 9868. The wire_swap MUX rewire approach is identical. The DFF D-input chain uses different gate decomposition but same logic. The main difference is naming convention and cell count optimization.

**No fundamental architectural gap for 9868** — unlike 9899 where the and_term needed to drive the module port directly, 9868's wire_swap is handled the same way by both engineer and AI.

---

## What This Tells the AI Flow

1. **GAP-1 (not applicable here):** 9868 didn't have the cell/pin path notation issue — AI used UNCONNECTED_3288 correctly as a wire name.
2. **Wire declarations:** Engineer uses explicit `wire eco9868_*;` — consistent with our refined GAP-14 rule (explicit wire for new intermediate nets).
3. **Gate count:** Engineer prefers more compact gate chains (4 vs 7 for NeedFreqAdj). The AI decomposition (NOR3+INV+AND3+OR2+INV+INV+AND4) is verbose but functionally correct.
4. **Naming:** Engineer uses descriptive names (`eco9868_inv_req`, `eco9868_or4`). AI uses sequential (`eco_9868_d001`). Both are valid.

---

*Document created: 2026-04-26 | Reference only — do not commit*
