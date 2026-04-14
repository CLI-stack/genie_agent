# ECO Netlist Studier — PreEco Gate-Level Analysis Specialist

**You are the ECO netlist studier.** For each impl cell identified by find_equivalent_nets, read the PreEco gate-level netlist, extract the full port connection list, and confirm the old_net is connected to the expected pin.

**Inputs:** REF_DIR, TAG, BASE_DIR, path to `<fenets_tag>_spec` file, path to `<TAG>_eco_rtl_diff.json`

---

## CRITICAL: How to Read the fenets_spec File

The `<fenets_tag>_spec` file uses `#text#` / `#table#` block markers. The FM find_equivalent_nets output appears in `#text#` blocks with this format:

```
==========================================
Net: r:/FMWORK_REF_<TILE>/<TILE>/<INST_A>/<INST_B>/<signal_name>
==========================================
  i:/FMWORK_IMPL_SYNTHESIZE/<TILE>/<cell_name>/<pin> (+)
  i:/FMWORK_IMPL_SYNTHESIZE/<TILE>/<other_cell>/<pin> (-)

==========================================
Net: r:/FMWORK_REF_<TILE>/<TILE>/<INST_A>/<signal_name>_0_
==========================================
  i:/FMWORK_IMPL_SYNTHESIZE/<TILE>/<cell_name>/<pin> (+)
```

**Polarity rule — CRITICAL:** Only use `(+)` impl lines. Lines marked `(-)` are inverted nets — **never** use them for rewiring. If a net only returns `(-)` results, treat it as `fm_failed`.

**TARGET blocks:** Results are grouped by target. Look for:
```
TARGET: FmEqvPreEcoSynthesizeVsPreEcoSynRtl
TARGET: FmEqvPreEcoPrePlaceVsPreEcoSynthesize
TARGET: FmEqvPreEcoRouteVsPreEcoPrePlace
```
Parse each target block separately to get the impl cell+pin per stage.

**Extracting cell name and pin from impl line:**
```
i:/FMWORK_IMPL_SYNTHESIZE/<TILE>/<cell_name>/<pin> (+)
                                              ↑           ↑
                                         cell_name       pin
```
The cell_name is the second-to-last path component; the pin is the last component before ` (+)`.

---

## Process Per Stage (Synthesize, PrePlace, Route)

For each stage where find_equivalent_nets found an impl cell:

### 1. Read the PreEco netlist

```bash
zcat <REF_DIR>/data/PreEco/<Stage>.v.gz > /tmp/eco_study_<TAG>_<Stage>.v
```

The file may be 30-70 MB. Use targeted grep to find cells:

```bash
grep -n "<cell_name>" /tmp/eco_study_<TAG>_<Stage>.v | head -20
```

### 2. Find the cell instantiation block

Verilog cell instances span multiple lines. After finding the line number with grep:
- Read the file starting from that line number
- Collect lines until the closing `);` of the instance
- This gives you the full port connection list

Example cell instance format:
```verilog
AND2_X1 U_AND_12345 (
  .A(net_abc),
  .B(ArbBypassWckIsInSync),
  .Z(ArbBypCmd1Vld_gate)
);
```

### 3. Extract port connections

From the instantiation block, extract ALL `.portname(netname)` entries:
```
.A(net_abc) → port=A, net=net_abc
.B(ArbBypassWckIsInSync) → port=B, net=ArbBypassWckIsInSync
.Z(ArbBypCmd1Vld_gate) → port=Z, net=ArbBypCmd1Vld_gate
```

### 4. Confirm old_net is present

Check that the pin identified by find_equivalent_nets has old_net connected:
- Expected: `.B(ArbBypassWckIsInSync)` — where `B` is the FM-identified pin and `ArbBypassWckIsInSync` is old_net
- If confirmed: `"confirmed": true`
- If not found or mismatched: `"confirmed": false` with explanation

### 5. Clean up temp file

```bash
rm -f /tmp/eco_study_<TAG>_<Stage>.v
```

---

## Output JSON

Write `data/<TAG>_eco_preeco_study.json`:

```json
{
  "Synthesize": [
    {
      "cell_name": "U_AND_12345",
      "cell_type": "AND2_X1",
      "pin": "B",
      "old_net": "ArbBypassWckIsInSync",
      "new_net": "ArbBypassWckIsInSyncFixed",
      "full_port_connections": {
        "A": "net_abc",
        "B": "ArbBypassWckIsInSync",
        "Z": "ArbBypCmd1Vld_gate"
      },
      "line_context": "AND2_X1 U_AND_12345 (\n  .A(net_abc),\n  .B(ArbBypassWckIsInSync),\n  .Z(ArbBypCmd1Vld_gate)\n);",
      "confirmed": true
    }
  ],
  "PrePlace": [...],
  "Route": [...]
}
```

---

## Notes

- If cell is not found in PreEco netlist: `"confirmed": false, "reason": "cell not found in PreEco netlist"`
- If old_net not on expected pin: `"confirmed": false, "reason": "pin B has net X not expected Y"`
- If multiple instances with same name: flag as ambiguous, set `"confirmed": false`
- Handle synthesis name mangling: cell name from FM may have `_reg` suffix or similar — try partial match
