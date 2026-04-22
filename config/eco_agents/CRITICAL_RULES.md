# ECO Flow — CRITICAL RULES

**Every orchestrator and sub-agent in the ECO flow MUST read this file first before doing any work.**
These rules exist because each one maps to a confirmed bug that caused a real run to fail or produce wrong output.

---

## RULE 0 — Scope Restriction

Only read guidance files from `config/eco_agents/`. Do NOT read from `config/analyze_agents/` — those files govern static check analysis (CDC/RDC, Lint, SpgDFT) and contain rules that are wrong for ECO gate-level netlist editing. `config/analyze_agents/shared/CRITICAL_RULES.md` does NOT apply to this flow.

---

## RULE 1 — Every Run is From Scratch

**Every TAG is an independent, fresh run. Never reuse files from a previous TAG.**

- Do NOT copy, read, or import any file from a previous `AI_ECO_FLOW_<OLDER_TAG>/` directory in REF_DIR.
- Do NOT reuse fenets RPTs, netlist study JSONs, eco_applied JSONs, or any other output from a previous TAG.
- REF_DIR may contain multiple older `AI_ECO_FLOW_*` directories from previous runs — treat them as **read-only historical artifacts that do not affect this run**.
- Step 2 (find_equivalent_nets) **MUST always be submitted fresh** for a new TAG. It may never be skipped by copying from an older AI_ECO_FLOW directory.

> **Confirmed bug pattern:** Agent found multiple older `AI_ECO_FLOW_*` directories in REF_DIR, copied fenets RPTs from a previous TAG's directory, and skipped Step 2 entirely.

---

## RULE 2 — Spawn Then Hard Stop (ORCHESTRATOR and ROUND_ORCHESTRATOR)

**After Step 5, your ONLY remaining work is: (A) write `round_handoff.json`, (B) spawn the next agent, (C) stop.**

You MUST NOT:
- Run Steps 7 or 8 yourself
- Write `eco_summary.rpt` or `eco_report.html`
- Send any final email
- Run any bash commands after the spawn
- "Help" the next agent by doing its work early

Those files and actions belong to FINAL_ORCHESTRATOR. If you produce them yourself, you are violating the spawn-then-exit contract.

**The presence of `eco_report.html` or `eco_summary.rpt` written by ORCHESTRATOR or ROUND_ORCHESTRATOR is a bug, not a success.**

> **Root cause of confirmed bug:** ORCHESTRATOR ran Steps 7-8 itself after FM PASSED, never wrote `round_handoff.json`, and never spawned FINAL_ORCHESTRATOR.

---

## RULE 3 — Write round_handoff.json FIRST, Verify on Disk

`round_handoff.json` MUST be written and verified on disk **before** any spawn decision is made.

```bash
# Always verify after writing:
ls -la <BASE_DIR>/data/<TAG>_round_handoff.json
```

If the file does not exist or is empty after writing — write it again. Do NOT spawn any agent until this file is confirmed on disk.

> **Root cause of confirmed bug:** ORCHESTRATOR skipped writing `round_handoff.json` entirely, which also broke any retry recovery path.

---

## RULE 4 — Never Skip a Step

**Context pressure, token budget, and time constraints are NOT valid reasons to skip any step or checkpoint.**

Every step must:
1. Fully execute
2. Write its output file(s) to disk
3. Pass its checkpoint (verify output file exists and is non-empty)

Only then may the next step begin.

---

## RULE 5 — Read All Inputs From Disk

**Never assume state from previous context, memory, or another agent's summary.**

- ORCHESTRATOR: read `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR` from the prompt inputs
- ROUND_ORCHESTRATOR: read all state from `ROUND_HANDOFF_PATH` and `_eco_fixer_state` on disk
- FINAL_ORCHESTRATOR: read all state from `ROUND_HANDOFF_PATH` on disk; read all round JSONs from disk

If a file you expect to read does not exist — stop and report the missing file. Do not guess its contents.

---

## RULE 6 — Backup Before Every PostEco Edit

Before modifying any `PostEco/<Stage>.v.gz` file:

```bash
cp <REF_DIR>/data/PostEco/<Stage>.v.gz \
   <REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>
```

Backup names are TAG- and ROUND-specific so each round can be independently reverted. Never overwrite a backup from a previous round.

---

## RULE 7 — Instance Names, Not Module Names

All hierarchy paths in ECO changes use **instance names** (the name given at instantiation), not module names (the `module` definition name). Confusing the two will cause the applier to fail to locate cells in the netlist.

---

## RULE 8 — Email is Mandatory at Every Stage

- ROUND_ORCHESTRATOR: per-round email (Step 6a) is mandatory BEFORE revert (Step 6b). Never skip.
- FINAL_ORCHESTRATOR: final email (Step 8) is mandatory. Verify `Email sent successfully` before cleanup.
- Retry once on failure. Never silently skip.

---

## RULE 9 — Single-Occurrence Rule for PostEco Edits

If `old_net` appears more than once on a given pin in the PostEco netlist, **skip and report AMBIGUOUS**. Do not apply a partial or guessed rewire.

---

## RULE 10 — Step 2 Retries Are Mandatory; Strategy Depends on Failure Type

When FM returns **No Equivalent Nets** or **FM-036** in Step 2, retries MUST be attempted before falling back to grep/stage fallback. The strategies differ:

**No Equivalent Nets:** Retry direction is always **deeper** (add sub-instance level) — never shallower. Max 2 retries.

**FM-036:** First classify the root cause:
- **Port-level signal** (net exists as a module port at some hierarchy level): retry by stripping one level at a time (going shallower). Max 3 retries.
- **Internal wire** (net is inside a submodule, not exposed as a port at any level): DO NOT strip levels — FM-036 will fire at all depths. **Pivot immediately to query the target register's output signal.** The eco_netlist_studier backward-cone trace will identify the actual cell and pin from there.

> **Confirmed bug pattern:** FM-036 retries went shallower on an internal wire net. The net was invisible to FM at every hierarchy level. Two retries were wasted before accidentally pivoting to the target register — which worked immediately. Classify first, then choose the correct strategy.

The retry strategies in Step 2 of ORCHESTRATOR.md are NOT optional. Only after the correct retries are exhausted may fallback be applied.

---

## RULE 11 — SVF: No Cell-Insertion Entries for ECO-Inserted Cells

**NEVER write `guide_eco_change -type insert_cell -instance {...} -reference {...}` to EcoChange.svf.** This command does not exist in Formality SVF and causes CMD-010 abort on all 3 FM targets before any comparison occurs.

The correct SVF behavior for ECO-inserted cells:
- **FM auto-matches inserted cells by instance path name** — no SVF guidance entry is needed or valid.
- `EcoChange.svf` is appended after the `setup` keyword; the appended entries land in the `setup` partition.
- `guide_eco_change` belongs in the `guide` partition (generated by `fm_eco_to_svf.pl` from RTL file diffs) — it is rejected in the `setup` partition with CMD-010.

The **only valid SVF entries** to append (in the `setup` partition) are:
- `set_dont_verify -type { register } /path` — suppress pre-existing FM failures (use curly braces: `-type { register }`, NOT `-type register`)
- `set_user_match /rtl/path /impl/path` — force-match a specific point when FM cannot auto-match

For pure new_logic cell insertions with no pre-existing failures: `svf_update_needed=false` — write no TCL file and skip Step 4b file creation (RPT still written noting "not applicable").

> **Confirmed bug pattern:** eco_svf_updater wrote `guide_eco_change -type insert_cell -instance -reference` entries. This command is invalid SVF. FM rejected all entries with CMD-010, aborting all 3 targets before any comparison — multiple rounds wasted.

> **Secondary confirmed bug pattern:** An earlier version used `eco_change` (not `guide_eco_change`) — also invalid for FM X-2025.06-SP3-VAL-20251201, causing CMD-005 elaboration failure.

---

## RULE 12 — All 3 Stages Must Be Modified (ECO Applier)

ECO changes MUST be applied to all 3 stages: **Synthesize, PrePlace, and Route**. Applying only to Synthesize and leaving PrePlace and Route unchanged is a partial ECO that FM will fail.

After eco_applier completes, verify:
```bash
# Each modified stage must differ from its backup:
md5sum <REF_DIR>/data/PostEco/Synthesize.v.gz
md5sum <REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>
# (hashes must differ)
```

If any stage's md5 matches its backup — the ECO was not applied to that stage. Do NOT proceed to Step 5.

> **Confirmed bug pattern:** eco_applier only modified Synthesize; PrePlace and Route were left unchanged. FM stage-to-stage comparison then failed because the PostEco netlists diverged from each other.

---

## RULE 13 — Poll with 5-Minute Bash Tool Calls for Long Waits

**Use individual Bash tool calls every 5 minutes** for fenets and FM polling. Each tool call = one "Running..." update visible in the main session — this keeps the session responsive and showing progress instead of showing "Sublimating..." for hours.

```bash
# CORRECT — one tool call per poll interval (every 5 min)
grep -c "SENTINEL" <file> 2>/dev/null || echo 0
# If not complete: sleep 300 (one Bash call), then poll again
```

```bash
# WRONG — single blocking bash call that runs for 2+ hours
timeout 7200 bash -c 'while true; do check && break; sleep 300; done'
# This makes the session show "Sublimating... (2h 7m)" with no visible progress
```

**Maximum poll counts:** fenets = 12 polls × 5 min = 60 min max; FM = 72 polls × 5 min = 6 hours max.

---

## RULE 14 — Orchestrator Generates RPTs, Sub-Agents Write JSON Only

**Sub-agents (eco_netlist_studier, eco_applier) write their JSON output only and exit. The ORCHESTRATOR or ROUND_ORCHESTRATOR generates all RPT files from the JSON.**

This prevents context pressure from causing sub-agents to exit before completing the RPT. The orchestrator reads the JSON (which it must do anyway for checkpointing) and generates the RPT immediately after the checkpoint passes.

**After each sub-agent completes:**
1. Checkpoint: verify JSON exists and is valid
2. Generate RPT from JSON (you, the orchestrator, do this — not the sub-agent)
3. Copy RPT to `AI_ECO_FLOW_DIR/`
4. Verify copy succeeded
5. Only then proceed to the next step

> **Confirmed bug pattern:** eco_applier sub-agent exited after writing JSON due to context pressure. RPT was never written. ORCHESTRATOR checkpoint only verified the JSON existed — it missed the missing RPT. The step RPT never appeared in AI_ECO_FLOW_DIR.

---

## Quick Checklist — Before Each Step Transition

| Before entering... | Verify on disk (JSON + RPT) |
|--------------------|---------------------------|
| Step 2 | `data/<TAG>_eco_rtl_diff.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step1_rtl_diff.rpt` ✓ |
| Step 3 | `data/<TAG>_eco_step2_fenets.rpt` ✓ + all fenets raw RPTs in AI_ECO_FLOW_DIR ✓ |
| Step 4 | `data/<TAG>_eco_preeco_study.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step3_netlist_study.rpt` ✓ |
| Step 4b | `data/<TAG>_eco_applied_round<N>.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step4_eco_applied_round<N>.rpt` ✓ + all 3 stages md5-differ from backup ✓ |
| Step 5 | `data/<TAG>_eco_svf_entries.tcl` ✓ only if pre-existing FM failures exist — otherwise `svf_update_needed=false`, no TCL file |
| After Step 5 | `data/<TAG>_round_handoff.json` ✓ — then spawn — then STOP |
| Step 7b | `data/<TAG>_eco_summary.rpt` ✓ |
| Step 8 | `data/<TAG>_eco_report.html` ✓ |

---

## RULE 15 — Detect Netlist Type Before Applying Port Entries

**Always detect hierarchical vs flat PostEco netlist before processing any stage:**

```bash
grep -c "^module " /tmp/eco_apply_<TAG>_<Stage>.v
```

- **Count > 1 → hierarchical.** `port_declaration` and `port_connection` entries MUST be applied — never skipped. The flags `flat_net_confirmed: true` and `no_gate_needed: true` are only valid for flat netlists and must be ignored in hierarchical context.
- **Count = 1 → flat.** `port_promotion` path applies — the net exists as a wire in the single module; explicit port declarations in submodules are not needed.

> **Confirmed bug pattern:** eco_netlist_studier ran `port_promotion` on Synthesize, found the new signal as a flat wire, and set `no_gate_needed: true`. eco_applier skipped all `port_declaration` and `port_connection` entries. The PostEco netlist was hierarchical (many modules). The new signal was left unconnected through the module port boundary. FM reported it as "globally unmatched" — a DFF receiving the signal was a failing point.

---

## RULE 16 — Use Per-Stage Port Connections for DFF Insertions

**Never use Synthesize-derived port_connections for all stages.** P&R tools rename clock and reset nets in PrePlace and Route stages. eco_netlist_studier must verify each signal name per stage and record `port_connections_per_stage` in the study JSON. eco_applier must read `port_connections_per_stage[<Stage>]` for each stage, falling back to flat `port_connections` only when the per-stage map is absent.

If a signal from `port_connections_per_stage` is not found in the current stage's PostEco netlist — search for a P&R alias before skipping. Never insert a DFF with a net name that does not exist in that stage's netlist.

> **Confirmed bug pattern:** eco_applier used Synthesize-derived `port_connections` for all 3 stages. PrePlace/Route had renamed clock and reset nets via P&R buffering. The inserted DFF had wrong pin-to-net connections → FM stage-to-stage mismatch → the newly inserted DFF appeared as a regression failing point in the PrePlace-vs-Synthesize comparison.

---

## RULE 17 — Include All DFF Pins; Derive Auxiliary Pin Values from a Neighbour DFF

**A DFF inserted by the ECO must have every pin connected — not just functional pins (clock, data, output).** Auxiliary pins (scan input, scan enable, and any others) must also be connected with the correct stage-specific nets.

**How to find auxiliary pin values:**
1. Read the full port list of the chosen DFF cell type from an existing instance in the same module scope in the PreEco netlist for that stage.
2. Copy the auxiliary pin net values from that neighbour DFF — this wires the ECO DFF into the existing scan chain consistently.
3. Do NOT assume auxiliary pins are constants (`1'b0`) in PrePlace and Route — they are typically connected to real scan chain nets in P&R stages.
4. Only in Synthesize (before scan insertion) are auxiliary pins commonly tied to constants — confirm by reading the neighbour DFF.

**eco_netlist_studier** records all pins (functional + auxiliary) in `port_connections_per_stage` per stage.
**eco_applier** uses the full `port_connections_per_stage[<Stage>]` map when building the DFF instantiation string — every pin, every stage.

> **Confirmed pattern from a passing ECO:** In Synthesize, auxiliary scan pins were `1'b0`. In PrePlace and Route, they were connected to module-local scan chain nets taken from a neighbour DFF in the same scope. The ECO DFF used neighbour values — not constants — in P&R stages.

---

## RULE 18 — MUX Select Pin Polarity Must Be Derived From the Netlist, Not the RTL Condition

**When a `wire_swap` change targets a MUX select pin, the gate function for the new select logic must match the MUX's actual input-to-output truth table — NOT the RTL condition as written.**

The RTL condition (e.g., `condition ? val_A : val_B`) describes the select semantics. But whether the new gate should be inverting or non-inverting depends on which MUX input (`I0` or `I1`) carries `val_A`:
- If `val_A` (the true-branch value) is on `I1` → `.S=1` selects it → gate must be **non-inverting** (e.g., AND2)
- If `val_A` (the true-branch value) is on `I0` → `.S=0` selects it → gate must be **inverting** (e.g., NAND2)

**MANDATORY steps (Step 4c-POLARITY in eco_netlist_studier.md):**
1. Read the MUX cell's I0 and I1 connections from the PreEco netlist
2. Trace which PreEco net drives the RTL true-branch and which drives the false-branch
3. Determine from the MUX truth table what polarity of `.S` selects each branch
4. Choose the gate function accordingly — record in `mux_select_polarity` in the study JSON
5. Update the associated `new_logic_gate` entry's `gate_function` to match

**Never assume the gate function from the RTL condition alone.** The same RTL ternary expression may require an inverting or non-inverting gate depending on which MUX input carries the true-branch value — this can only be determined by reading the PreEco netlist.

> **Confirmed bug pattern:** A wire_swap targeting a MUX select pin used an inverting gate when a non-inverting gate was required. The gate function was derived from the RTL condition text without checking whether the true-branch mapped to I0 or I1 in the netlist. The inverted select caused the MUX to pick the wrong input every cycle. FM failed on the target register across all rounds even after all other issues were resolved.

---

*Last updated: 2026-04-21*
