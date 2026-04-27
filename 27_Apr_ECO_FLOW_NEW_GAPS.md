# ECO Auto-Flow — New Gaps Found (2026-04-27)
**Source:** 9899 new run (20260427024445) — analysis of preeco_study.json after gap fixes applied
**Status:** Pending fix before next run

---

## Summary

After applying all 20 gaps from `26_Apr_ECO_FLOW_GAPS_planning_fix.md`, the new 9899 run shows:
- **GAP-1 ✅ WORKING** — eco_9899_c007 inputs correctly resolved to actual wire names (`phfnn_2405075`, `N2408127`)
- **GAP-14 ✅ WORKING** — `needs_explicit_wire_decl: true` set on 13 intermediate nets
- **GAP-15 ❌ NOT TRIGGERED** — studier chose `parent_scope` instead of `module_port_direct_gating`
- **GAP-18, GAP-20** — N/A for 9899 (no submodule bus driver, no new DFF insertion)

---

## NEW-GAP-1 — eco_netlist_studier: GAP-15 rule written as optional IF block — agent bypasses it

**Severity:** CRITICAL
**Observed in:** 9899 new run (20260427024445)
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
GAP-15 "MODULE PORT DIRECT GATING" was added to eco_netlist_studier.md. But the studier still set `and_term_strategy: "parent_scope"` for eco_9899_1_DCQARB/1. The check was not executed. Gates remain at ARB scope with `output=n_eco_9899_1_DCQARB` (not driving the module port `QualPmArbWinVld_d1` directly). Expected result: same 3000+ Synthesize FM failures.

**Root cause:**
The GAP-15 rule is written as an IF-YES block — the agent simply chose the else (parent_scope) path without running the port check. The guidance needs to be a **mandatory pre-condition that overrides all strategies**, not an optional path.

**Evidence from preeco_study.json:**
```
eco_9899_1_DCQARB:  and_term_strategy="parent_scope"  scope=ARB  output=n_eco_9899_1_DCQARB
eco_9899_1_DCQARB1: and_term_strategy="parent_scope"  scope=ARB  output=n_eco_9899_1_DCQARB1
```
Expected (if GAP-15 worked):
```
eco_9899_1_DCQARB:  and_term_strategy="module_port_direct_gating"  scope=ARB/DCQARB  output=QualPmArbWinVld_d1
```

**Fix required in eco_netlist_studier.md:**
Restructure the `and_term` processing to make the port check a **mandatory gate that runs FIRST and STOPS all other strategy evaluation**:

```
STEP 0 (MANDATORY — runs before any strategy selection):
  is_output_port = grep -cw "output.*\b<old_token>\b" <module_body_lines> >= 1
                   OR <old_token> appears in module header port list

  if is_output_port == True:
    → set and_term_strategy = "module_port_direct_gating"
    → STOP. Do NOT evaluate parent_scope or direct_rewire.
    → Apply: rename original driver output → intermediate net (implicit wire)
              new gate output = <old_token> (drives port directly)
              no individual rewire entries
    → RETURN from and_term processing

  # Only reaches here if NOT an output port
  → proceed to normal strategy selection (direct_rewire or parent_scope)
```

The word "MANDATORY" and "STOP/RETURN" must be explicit — not just "if yes do this".

---

## NEW-GAP-2 — eco_netlist_studier: parent_scope still causes 3000+ failures when old_token is a module output port

**Severity:** HIGH
**Observed in:** 9899 new run — consequence of NEW-GAP-1
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
With `parent_scope` strategy and only 4 individual consumer rewires (A606036, A606254, A648153, A648363), all other consumers of `QualPmArbWinVld_d1` in TIM scope, ARB scope etc. still see the ungated value. This is the root cause of 3000+ Synthesize FM failures that persisted through all 5 previous rounds.

**Fix:** Same as NEW-GAP-1 — if the STEP 0 pre-check fires correctly, module port direct gating automatically handles all consumers without individual rewires.

---

## NEW-GAP-3 — eco_netlist_studier: global seq counter not shared across multiple DFF chains in different module scopes

**Severity:** HIGH
**Observed in:** 9868 new run (20260427024454) — Step 3 shows `eco_9868_d001` used twice (NOR3 in ARB/CTRLSW AND INV with empty scope for umccmd chain)
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
9868 has two DFF chains in different modules:
- NeedFreqAdj D-input chain: d001–d007 in ARB/CTRLSW scope
- EcoUseSdpOutstRdCnt D-input chain: should be d008–d009 in umccmd scope

The studier reset the seq counter when processing the second DFF chain, reusing `eco_9868_d001` (INV) and `eco_9868_d002` (AND2) for the umccmd scope — conflicting with the CTRLSW chain entries. Additionally, the umccmd chain entries have empty `scope=`.

**Impact:**
eco_applier sees `eco_9868_d001` in CTRLSW (already inserted as NOR3) → marks the umccmd `eco_9868_d001` (INV) as ALREADY_APPLIED → EcoUseSdpOutstRdCnt_reg has no D-input chain → FM fails → wastes 1 round.

**Fix required in eco_netlist_studier.md:**
The global seq counter (from CRITICAL_RULES RULE and Section 3b) must be shared across ALL `d_input_gate_chain` entries regardless of module scope. When a design has multiple DFF insertions in different modules (e.g., DFF1 in ModuleA → d001-d007; DFF2 in ModuleB → d008-d009), the counter must continue from where it left off:
```
# WRONG — resets per DFF chain:
DFF1 chain: d001, d002, d003 ...
DFF2 chain: d001, d002  ← CONFLICT

# CORRECT — global seq across all chains:
DFF1 chain: d001, d002, d003 ...
DFF2 chain: d008, d009  ← continues from DFF1's last index
```

Also: `instance_scope` must be explicitly set for EVERY gate entry — never leave it empty. If a gate is in `umccmd` (tile root), set `instance_scope: "umccmd"` or the appropriate path.

---

## NEW-GAP-4 — eco_netlist_studier: instance_scope empty for DFF D-input gates in tile-root module scope

**Severity:** MEDIUM
**Observed in:** 9868 new run (20260427024454) — eco_9868_d001/d002 (umccmd chain) have `scope=` empty
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
When the DFF D-input chain gates belong to the tile-root module (e.g., `umccmd` itself, not a sub-instance), the studier left `instance_scope` empty. eco_applier needs a valid scope to locate the correct module in the PostEco netlist.

**Fix required:**
When `scope_is_tile_root: true` (or the DFF is in the top-level tile module), set `instance_scope: "<tile_module_name>"` explicitly — e.g., `instance_scope: "umccmd"` for `ddrss_umccmd_t_umccmd`. Never leave instance_scope blank.

---

## Priority for next run

| Priority | Gap | Fix |
|----------|-----|-----|
| P1 | NEW-GAP-1 — GAP-15 must be mandatory pre-condition (STOP/RETURN pattern) | Restructure and_term logic in eco_netlist_studier.md |
| P1 | NEW-GAP-3 — global seq counter not shared across multiple DFF chains | eco_netlist_studier.md Section 3b: one counter for all chains |
| P2 | NEW-GAP-2 — consequence of NEW-GAP-1 | Resolved by NEW-GAP-1 fix |
| P2 | NEW-GAP-4 — instance_scope empty for tile-root DFF chain gates | eco_netlist_studier.md: always set instance_scope |
| P1 | NEW-GAP-5 — Check H only validates output pin — input pins not checked → FE-LINK-7 | eco_pre_fm_checker.md + eco_netlist_studier.md |
| P3 | NEW-GAP-6 — cell type AMD variant may not exist in FM library | eco_netlist_studier.md |

---

## NEW-GAP-5 — eco_pre_fm_checker Check H only validates OUTPUT pin names — input pin names not checked

**Severity:** HIGH
**Observed in:** 9868 new run (20260427024454) — eco_9868_d001 (NOR3) inserted with wrong input pin names B1/B2 instead of A2/A3 → FE-LINK-7 ABORT_LINK
**File:** `config/eco_agents/eco_pre_fm_checker.md`, `config/eco_agents/eco_netlist_studier.md`

**What happened:**
eco_9868_d001 is a NOR3 gate (`NR3D1BWP136P5M156H3P48CPDLVT`). Its correct input pins are `A1`, `A2`, `A3`. But eco_netlist_studier specified `A1`, `B1`, `B2` as the input pin names (incorrect). Check H in eco_pre_fm_checker validated the OUTPUT pin (`ZN`) correctly but did NOT check input pin names. FM aborted with FE-LINK-7 on BOTH PrePlace and Route (using Synthesize as reference).

**Also:** eco_9868_d004 (OR2) was inserted with wrong cell variant `OR2D1AMDBWP...` instead of `OR2D1BWP...` → FE-LINK-2 (cannot link cell).

**Fix required:**

1. **eco_pre_fm_checker Check H:** Extend to validate ALL pin names (inputs + output) against the actual cell found in PreEco:
   - For each ECO-inserted gate, grep PreEco for the cell_type → extract all pin names from the instantiation
   - Verify ALL port_connections pin names match the actual cell pins (not just the output)
   - If any INPUT pin mismatch → flag as `H_wrong_input_pin_name` and apply inline fix (look up correct name from PreEco grep)

2. **eco_netlist_studier:** When building port_connections for a new gate, look up the actual pin names from a PreEco example of that cell type — never assume pin names from the gate function name alone. The GATE_OUTPUT_PIN table covers output pins; input pin names also need to be read from PreEco.

---

## NEW-GAP-6 — eco_netlist_studier: cell type variant selection may pick wrong AMD/non-AMD variant for P&R stages

**Severity:** MEDIUM
**Observed in:** 9868 — eco_9868_d004 used `OR2D1AMDBWP...` (AMD variant) which cannot be linked
**File:** `config/eco_agents/eco_netlist_studier.md`

**What happened:**
When searching PreEco for a matching cell type for OR2, the studier found `OR2D1AMDBWP136P5M156H3P48CPDLVT` (an AMD-optimized variant). FM's library doesn't include this variant → FE-LINK-2 (cannot link cell). The correct cell would be `OR2D1BWP136P5M156H3P48CPDLVT`.

**Fix required:**
When finding cell type from PreEco, cross-validate that the cell type exists in the FM technology library (`/TECH_LIB_DB/`). A practical check: if `grep -c "<cell_type>" <REF_DIR>/data/PreEco/Synthesize.v.gz` > 0 AND the cell type was found in PreEco, it should be valid for FM. The ABORT_LINK inline fix in eco_fm_runner handles this case automatically in Round 2.

---

## NEW-GAP-7 — eco_pre_fm_checker: agent falsely reports validator "not available" to skip Check 8

**Severity:** HIGH
**Observed in:** 9899 new run Step 5 — Check 8 SKIPPED with reason "script not available in this environment" but `script/validate_verilog_netlist.py` EXISTS at the correct path
**File:** `config/eco_agents/eco_pre_fm_checker.md`

**What happened:**
The eco_pre_fm_checker agent claimed the validator script was not available — but it IS at `script/validate_verilog_netlist.py` relative to BASE_DIR. The agent took the SKIPPED path without actually checking for the script. This is the same bypassing pattern as GAP-15 (mandatory rule written as optional path).

**Fix required:**
Add a mandatory existence check BEFORE any skip decision:
```python
import os
validator = "script/validate_verilog_netlist.py"
if not os.path.isfile(os.path.join(BASE_DIR, validator)):
    raise RuntimeError(
        f"ABORT: {validator} not found at {BASE_DIR}. "
        "Check 8 CANNOT be skipped — fix the script path before proceeding."
    )
# Only reaches here if script exists — never SKIPPED
```
Make SKIPPED **impossible** by turning it into a hard error. The agent must find and run the script, not claim it's missing.

---

*Document created: 2026-04-27*
*Based on: 9899 new run tag 20260427024445 preeco_study.json analysis*
