# 9899 Engineer Solution Reference
**Path:** `/proj/cip_feint2_konark/konark/MECO/regr_0306/main/pd/tiles/ddrss_umccmd_t_DEUMCIPRTL-9899`
**Purpose:** Reference document for comparing AI flow output vs engineer solution step by step

---

## Summary of ECO

JIRA 9899 | Tile: umccmd | Files changed: rtl_umcdcqarb.v, rtl_umccmdarb.v, rtl_umcarb.v

**RTL changes:**
1. `rtl_umcdcqarb.v` — and_term: `QualPmArbWinVld_d1 += ~SplitActInProgOthDcq`; port_promotion: PhArbFineGater → output; new_port: SplitActInProgOthDcq input
2. `rtl_umccmdarb.v` — new_port: DcqArb0/1_PhArbFineGater inputs; new_logic: ToggleChn priority conditions
3. `rtl_umcarb.v` — port_connections: DCQARB/DCQARB1/CMDARB new port hookups

---

## Engineer Gate-Level Solution (PostEco Synthesize)

### Change 1 — DCQARB0 and_term (module `ddrss_umccmd_t_umcdcqarb_0`)

**What engineer did:**
```verilog
// Original driver renamed (implicit wire — no explicit declaration)
AOI12D8AMDBWP136P5M273H3P48CPDLVT A2234246 (
    .A2 ( N2409689 ) , .B ( N2409691 ) , .ZN ( ECO_9899_net1 ) ) ;  // was QualPmArbWinVld_d1

// New explicit wires for intermediate nets
wire ECO_9899_net0;
wire ECO_9899_net1;

// INV gate for ~SplitActInProgOthDcq
INVD4BWP136P5M156H3P48CPDLVT ECO_9899_cell10 ( .I ( SplitActInProgOthDcq ) ,
    .ZN ( ECO_9899_net0 ) ) ;

// AND2 drives QualPmArbWinVld_d1 PORT directly — all consumers see gated value
AN2D3BWP136P5M156H3P48CPDLVT ECO_9899_cell11 ( .A1 ( ECO_9899_net0 ) ,
    .A2 ( ECO_9899_net1 ) , .Z ( QualPmArbWinVld_d1 ) ) ;
```

**Key principle:** Gate output = `QualPmArbWinVld_d1` (existing port name). No individual consumer rewires.

---

### Change 2 — DCQARB1 and_term (module `ddrss_umccmd_t_umcdcqarb_1`)

```verilog
// Original driver renamed (implicit wire from cell output)
INR3D8BWP136P5M156H3P48CPDLVT A2387450 (
    .A1 ( PmArbWinVld_d1 ) , .B1 ( N2454200 ) , .B2 ( ctmn_1892668 ) , .ZN ( ECO_9899_net21 ) ) ;

// New explicit wires
wire ECO_9899_net20;
wire ECO_9899_net21;

INVD4BWP136P5M156H3P48CPDLVT ECO_9899_cell20 ( .I ( SplitActInProgOthDcq ) ,
    .ZN ( ECO_9899_net20 ) ) ;

// AND2 drives QualPmArbWinVld_d1 PORT directly
AN2D3BWP136P5M156H3P48CPDLVT ECO_9899_cell21 ( .A1 ( ECO_9899_net20 ) ,
    .A2 ( ECO_9899_net21 ) , .Z ( QualPmArbWinVld_d1 ) ) ;
```

---

### Change 3 — umcarb port connections + new wires

```verilog
// Explicit wire declarations for new cross-module signals
wire DcqArb0_PhArbFineGater;
wire DcqArb1_PhArbFineGater;

// Port connections added to DCQARB/DCQARB1/CMDARB instances (//ECO 9899 comments)
// .SplitActInProgOthDcq(SplitActInProgCmd1) on DCQARB
// .SplitActInProgOthDcq(SplitActInProgCmd0) on DCQARB1
// .PhArbFineGater(DcqArb0_PhArbFineGater) on DCQARB
// .PhArbFineGater(DcqArb1_PhArbFineGater) on DCQARB1
// .DcqArb0_PhArbFineGater(DcqArb0_PhArbFineGater) on CMDARB
// .DcqArb1_PhArbFineGater(DcqArb1_PhArbFineGater) on CMDARB
```

---

### Change 4 — CMDARB ToggleChn cascade (module `ddrss_umccmd_t_umccmdarb`)

**Explicit wire declarations:**
```verilog
wire ECO_9899_net30;  wire ECO_9899_net31;  wire ECO_9899_net32;
wire ECO_9899_net33;  wire ECO_9899_net34;  wire ECO_9899_net35;
wire ECO_9899_net36;  wire ECO_9899_net38;  wire ECO_9899_net39;
wire ECO_9899_net40;  // (net38-40 are commented out — engineer revised approach)
wire ECO_9899_SplitActCtr1_inv;
wire ECO_9899_PhArbFineGater0_inv;
wire ECO_9899_PhArbFineGater1_inv;
```

**Gate chain (active — not commented out):**
```verilog
// net30: existing gate OA12 output used directly (ctmn_2084152 area)
// Using actual net names phfnn_2405075=DcqArb0_QualPhArbReqVld, N2408127=DcqArb1_QualPhArbReqVld

OA12D1... ECO_9899_cell30  ( .A1(N2408127), .A2(phfnn_2405075), .B(SplitActInProgCmd1) → ECO_9899_net31 )
OAI21D1  ECO_9899_cell31  ( .A1(N2408127), .A2(phfnn_2405075), .B(SplitActInProgCmd0) → ECO_9899_net32 )  [ZN=inverted]
OA12D1   ECO_9899_cell32  ( .A1(ECO_9899_net30), .A2(ECO_9899_net31), .B(ECO_9899_net32) → ECO_9899_net33 )
INVD1    ECO_9899_cell_SplitActCtr1inv ( .I(SplitActCtr[1]) → ECO_9899_SplitActCtr1_inv )
ND2      ECO_9899_cell_33 ( .A1(ECO_9899_SplitActCtr1_inv), .A2(SplitActCtr[0]) → ECO_9899_net34 )
                            [= ~(SplitActCtr==2'b01) = NAND(~Ctr[1], Ctr[0])]
INVD1    ECO_9899_cell_34 ( .I(DcqArb0_PhArbFineGater) → ECO_9899_PhArbFineGater0_inv )
INVD1    ECO_9899_cell_35 ( .I(DcqArb1_PhArbFineGater) → ECO_9899_PhArbFineGater1_inv )
AN3D3    ECO_9899_cell_36 ( .A1(ECO_9899_PhArbFineGater0_inv), .A2(DcqArb1_PhArbFineGater), .A3(ECO_9899_net34) → ECO_9899_net35 )
                            [= ~DcqArb0 & DcqArb1 & ~(SplitActCtr==01) — cond for DcqArb1 winning]
ND3D1    ECO_9899_cell_37 ( .A1(ECO_9899_PhArbFineGater1_inv), .A2(DcqArb0_PhArbFineGater), .A3(ECO_9899_net34) → ECO_9899_net36 )
                            [= NAND(~DcqArb1 & DcqArb0 & ~(SplitActCtr==01)) — inverted cond for DcqArb0 winning]
OA12D1   ECO_9899_cell38  ( .A1(ECO_9899_net35), .A2(ECO_9899_net33), .B(ECO_9899_net36) → ctmn_2084955 )
                            [feeds existing ToggleChn MUX chain]
```

**Commented-out cells (engineer revised approach):**
```verilog
//ECO_9899_cell_38/39/40 — originally tried different gate structure for SplitActInProgCmd conditions
//                          revised to OA12/OAI21 approach above (net30-net33)
```

---

### Change 5 — DCQARB1 PhArbFineGater (port promotion)

```verilog
// In ddrss_umccmd_t_umcdcqarb_1:
// wire ECO_9899_net220 — buffer chain for PhArbFineGater output
INVD1 ECO_9899_cell220 ( .I(ctmn_1889436) → ECO_9899_net220 )
INVD1 ECO_9899_cell221 ( .I(ECO_9899_net220) → PhArbFineGater )  // drives output port
```

---

## Engineer Key Design Decisions (vs AI Flow)

| Decision | Engineer | AI Flow (5 rounds) |
|----------|----------|-------------------|
| and_term gate output | Drives `QualPmArbWinVld_d1` (module port) directly | Drove `n_eco_9899_1_DCQARB1` (new net) |
| Individual consumer rewires | **None** — port handles all | Rewired 2-4 consumers → 3000 failures |
| Intermediate net wires | Explicit `wire ECO_9899_net20;` etc. | Explicit `wire QualPmArbWinVld_d1_orig;` (wrong signal) |
| Net names for ToggleChn inputs | `phfnn_2405075`, `N2408127` (correct actual wires) | `A2336162/ZN`, `A2230141/ZN` (INVALID — `/` in name) |
| Gate complexity for ToggleChn | OA12/OAI21/AN3/ND3 (complex RTL translation) | INV/OR2/AND3/MUX2 (simplified decomposition) |
| Revised cells | Commented out ECO_9899_cell_38/39/40 (iteration) | No revision mechanism |

---

## What This Tells the AI Flow

1. **GAP-15**: For `and_term` where `old_token` is a module port → gate drives port directly. No rewires needed.
2. **GAP-14**: Explicit `wire N;` IS needed for new intermediate nets between ECO gates.
3. **GAP-1**: Condition gate inputs must be resolved to actual wire names from PreEco netlist, not cell/pin path notation.
4. **Observation**: Engineer used explicit `wire DcqArb0/1_PhArbFineGater;` in umcarb — these ARE new cross-module wires needing explicit declaration (not implicit from port connection alone since they appear as both output of DCQARB and input of CMDARB through umcarb parent).

---

*Document created: 2026-04-26 | Reference only — do not commit*
