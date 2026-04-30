# ECO Flow — CRITICAL RULES

**Every orchestrator and sub-agent in the ECO flow MUST read this file first before doing any work.**
These rules exist because each one addresses a known failure mode that caused a real run to fail or produce wrong output.

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

> **This rule prevents:** copying fenets RPTs from a previous TAG's directory and skipping Step 2 entirely when multiple older `AI_ECO_FLOW_*` directories are present in REF_DIR.

---

## RULE 2 — Spawn Then Hard Stop (ORCHESTRATOR and ROUND_ORCHESTRATOR)

**After Step 6, your ONLY remaining work is: (A) write `round_handoff.json`, (B) spawn the next agent, (C) stop.**

You MUST NOT:
- Run Steps 7 or 8 yourself
- Write `eco_summary.rpt` or `eco_report.html`
- Send any final email
- Run any bash commands after the spawn
- "Help" the next agent by doing its work early

Those files and actions belong to FINAL_ORCHESTRATOR. If you produce them yourself, you are violating the spawn-then-exit contract.

**The presence of `eco_report.html` or `eco_summary.rpt` written by ORCHESTRATOR or ROUND_ORCHESTRATOR is a bug, not a success.**

> **This rule prevents:** the ORCHESTRATOR running Steps 7-8 itself after FM PASSES, bypassing `round_handoff.json` and the FINAL_ORCHESTRATOR spawn entirely.

---

## RULE 3 — Write round_handoff.json FIRST, Verify on Disk

`round_handoff.json` MUST be written and verified on disk **before** any spawn decision is made.

```bash
# Always verify after writing:
ls -la <BASE_DIR>/data/<TAG>_round_handoff.json
```

If the file does not exist or is empty after writing — write it again. Do NOT spawn any agent until this file is confirmed on disk.

> **This rule prevents:** the ORCHESTRATOR skipping `round_handoff.json` entirely, which also breaks any retry recovery path.

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

> **This rule prevents:** wasting FM-036 retries by stripping hierarchy levels on an internal wire net that is invisible to FM at every depth. Classifying the net type first ensures the correct retry strategy is applied immediately.

The retry strategies in Step 2 of ORCHESTRATOR.md are NOT optional. Only after the correct retries are exhausted may fallback be applied.

---

## RULE 10b — ECO Instance Naming Convention

**DFF insertions (`new_logic_dff`):**
- Instance name: `<target_register>_reg` — the RTL register name with `_reg` suffix
- Q output net: `<target_register>` — the actual RTL signal name

**Why:** FM's `FmEqvEcoSynthesizeVsSynRtl` compares PostEco SynRtl (where FM synthesizes the new RTL and names the DFF `<target_register>_reg`) against PostEco gate-level. If the gate-level DFF instance name is `<target_register>_reg`, FM auto-matches them by name — no `set_user_match` needed. If the name is anything else (e.g., `eco_<jira>_dff1`), FM cannot match and the downstream comparison fails.

**Combinational gate insertions (`new_logic_gate`):**
- Instance name: `eco_<jira>_<seq>` (combinational), `eco_<jira>_d<seq>` (D-input chain), `eco_<jira>_c<seq>` (condition gates)
- Output net: `n_eco_<jira>_<seq>`

FM matches combinational gates by structural cone tracing — generic naming is sufficient. Same name must be used in all 3 stages for stage-to-stage matching.

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

> **This rule prevents:** writing `guide_eco_change -type insert_cell -instance -reference` entries to the SVF. This command is invalid SVF — FM rejects all entries with CMD-010, aborting all 3 targets before any comparison.

> **Secondary known failure mode:** An earlier variant used `eco_change` (not `guide_eco_change`) — also invalid, causing CMD-005 elaboration failure.

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

If any stage's md5 matches its backup — the ECO was not applied to that stage. Do NOT proceed to Step 5 (Pre-FM Quality Checker).

> **This rule prevents:** applying the ECO only to Synthesize while leaving PrePlace and Route unchanged, which causes FM stage-to-stage comparison to fail because the PostEco netlists diverge from each other.

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

> **This rule prevents:** a sub-agent exiting after writing JSON due to context pressure, causing the RPT to never be written and the step RPT to never appear in AI_ECO_FLOW_DIR.

---

## Quick Checklist — Before Each Step Transition

| Before entering... | Verify on disk (JSON + RPT) |
|--------------------|---------------------------|
| Step 2 | `data/<TAG>_eco_rtl_diff.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step1_rtl_diff.rpt` ✓ |
| Step 3 | `data/<TAG>_eco_step2_fenets.rpt` ✓ + all fenets raw RPTs in AI_ECO_FLOW_DIR ✓ |
| Step 4 | `data/<TAG>_eco_preeco_study.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step3_netlist_study.rpt` ✓ |
| Step 4b | `data/<TAG>_eco_applied_round<N>.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step4_eco_applied_round<N>.rpt` ✓ + all 3 stages md5-differ from backup ✓ |
| Step 5 | `data/<TAG>_eco_pre_fm_check_round<N>.json` ✓ — pre-FM checks passed |
| Step 6 | `data/<TAG>_eco_svf_entries.tcl` ✓ only if pre-existing FM failures exist — otherwise `svf_update_needed=false`, no TCL file |
| After Step 6 | `data/<TAG>_round_handoff.json` ✓ — then spawn — then STOP |
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

> **This rule prevents:** incorrectly treating a hierarchical PostEco netlist as flat. When `port_promotion` is applied on a flat-netlist assumption and `no_gate_needed: true` is set, eco_applier skips all `port_declaration` and `port_connection` entries, leaving the new signal unconnected through the module port boundary and causing FM "globally unmatched" failures.

---

## RULE 16 — Use Per-Stage Port Connections for DFF Insertions

**Never use Synthesize-derived port_connections for all stages.** P&R tools rename clock and reset nets in PrePlace and Route stages. eco_netlist_studier must verify each signal name per stage and record `port_connections_per_stage` in the study JSON. eco_applier must read `port_connections_per_stage[<Stage>]` for each stage, falling back to flat `port_connections` only when the per-stage map is absent.

If a signal from `port_connections_per_stage` is not found in the current stage's PostEco netlist — search for a P&R alias before skipping. Never insert a DFF with a net name that does not exist in that stage's netlist.

> **This rule prevents:** using Synthesize-derived `port_connections` for all 3 stages. P&R tools rename clock and reset nets in PrePlace and Route; using the wrong net names causes the inserted DFF to have wrong pin-to-net connections, leading to FM stage-to-stage mismatch.

---

## RULE 17 — Include All DFF Pins; Derive Auxiliary Pin Values from a Neighbour DFF

**A DFF inserted by the ECO must have every pin connected — not just functional pins (clock, data, output).** Auxiliary pins (scan input, scan enable, and any others) must also be connected with the correct stage-specific nets.

**How to find auxiliary pin values:**
1. Read the full port list of the chosen DFF cell type from an existing instance in the same module scope in the PreEco netlist for that stage.
2. Copy the auxiliary pin net values from that neighbour DFF — this wires the ECO DFF into the existing scan chain consistently.
3. Do NOT assume auxiliary pins are constants (`1'b0`) in PrePlace and Route. Scan insertion happens between Synthesize and PrePlace — after scan insertion, auxiliary pins are connected to real scan chain nets (SI, SE, etc.), not constants. Using Synthesize constants for PrePlace/Route auxiliary pins produces a DFF that is disconnected from the scan chain, causing DRC and LEC failures.
4. Only in Synthesize (before scan insertion) are auxiliary pins tied to constants — confirm by reading the neighbour DFF. If the neighbour DFF's auxiliary pin connects to a real net (not `1'b0`) even in Synthesize, use that net — do NOT override it with a constant assumption.

**eco_netlist_studier** records all pins (functional + auxiliary) in `port_connections_per_stage` per stage.
**eco_applier** uses the full `port_connections_per_stage[<Stage>]` map when building the DFF instantiation string — every pin, every stage.

> **Principle:** Auxiliary scan pin values change between design stages. In Synthesize, they are typically constants. In P&R stages, they connect to real scan chain nets. Reading a neighbour DFF of the same cell type in the same module scope — per stage — gives the correct values. Never assume constants apply in P&R stages.

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

> **This rule prevents:** deriving the gate function for a MUX select pin from RTL condition text alone, without checking whether the true-branch maps to I0 or I1 in the netlist. Using the wrong polarity (inverting vs non-inverting) causes the MUX to select the wrong input every cycle, and FM fails on the target register across all rounds.

---

## RULE 19 — Port Promotion Must Be Scoped to the Exact Module Boundary

**When applying `port_promotion` (or any wire-to-output rename), always restrict the search to the exact target module — from its `module` line to its `endmodule` line.**

Steps:
1. Find `mod_idx` using an anchored regex: `^module\s+<exact_name>\s*[(\s]` — never substring match
2. Find `endmodule_idx` as the first `endmodule` after `mod_idx`
3. ALL searches and replacements MUST stay within `lines[mod_idx:endmodule_idx]`
4. Use `re.sub` with word-boundary `\b` — do NOT use `str.replace('wire ', 'output ')` which matches any substring

**Why:** A hierarchical netlist contains many module definitions. Multiple modules may share the same internal wire name in completely different functional contexts. Applying the rename beyond `endmodule` corrupts every other module variant with the same wire name — potentially thousands of unintended changes.

> **This rule prevents:** applying a port promotion rename across the entire file without `endmodule` boundary restriction. A hierarchical netlist may contain many module variants sharing the same internal wire name — an unbounded rename corrupts all of them and introduces large numbers of FM failing points in unrelated modules.

---

## RULE 20 — Port Connection Insertion Must Use Parenthesis Depth Tracking

**When inserting a new `.port(net)` connection into a module instance block, find the closing `)` using parenthesis depth tracking — NOT simple string pattern matching.**

The correct algorithm:
1. Start at the instance declaration line (where `(` opens the port list)
2. Count `(` as depth +1, `)` as depth -1; start from depth 0 before the opening `(`
3. When depth returns to 0, that `)` closes the instance — this is where to insert
4. Insert before the final `)` on that line using `close_line[:last_paren] + new_conn + close_line[last_paren:]`

**Never use** `lines[i].strip() in (');', ') ;')` — this pattern fails on lines like `.last_port( net ) ) ;` where the first `)` closes the port value and the second `)` closes the instance. The pattern match may fire on the wrong `)`, inserting the new connection mid-block and corrupting the port list.

> **This rule prevents:** a simple `);` pattern search incorrectly matching a line ending with double `))` (e.g., `.last_port( <net> ) ) ;`) and inserting the new port connection inside an existing port's value expression, corrupting the netlist syntax.

---

## RULE 21 — `d_input_decompose_failed` — Try Intermediate Net Strategy Before MANUAL_ONLY

**When a `new_logic` change has `d_input_decompose_failed: true`, do NOT immediately mark as MANUAL_ONLY. First check `fallback_strategy`.**

**`fallback_strategy: "intermediate_net_insertion"` (Mode F1):**
The new conditions are PREPENDED to an existing expression. The old expression's gate-level output (the "pivot net") is an intermediate combinational net that can be redirected. The ECO inserts the new conditions at the pivot net — the DFF D-input is never touched.

Steps:
1. `rtl_diff_analyzer` sets `fallback_strategy: "intermediate_net_insertion"` and adds `target_register` to `nets_to_query`
2. `eco_netlist_studier` Step 0c traces backward from `target_register.D` to find the pivot net, then builds `rewire + new_logic_gate` entries targeting the pivot net
3. `eco_applier` applies the redirect (driver output → new wire) and inserts the new condition gates
4. FM sees the target register D-input unchanged — matches RTL expectation for the old expression path; new conditions implemented at gate level below

**`fallback_strategy: null` (Mode F2):**
Entirely new logic with no old expression preserved. Cannot use intermediate net approach. This is truly MANUAL_ONLY — an engineer must synthesize the gates from scratch.

**How ROUND_ORCHESTRATOR responds:**
- If ALL revised_changes are `action: manual_only` (Mode F2 only) → spawn FINAL_ORCHESTRATOR with `status: MANUAL_LIMIT`
- If Mode F1 entries exist → continue rounds with the intermediate net strategy applied

> **Principle:** When new conditions are prepended to an existing priority mux chain, the DFF D-input does not need to be modified. Insert the new condition gates at the intermediate combinational net (pivot net) that drives the existing priority logic. This resolves `d_input_decompose_failed` without requiring synthesis of the full D-input expression from scratch.

---

## RULE 22 — Structural Stage-to-Stage Mismatches: Fix Netlist First

**When `FmEqvEcoPrePlaceVsEcoSynthesize` fails with a large count (hundreds+) AND `FmEqvEcoRouteVsEcoPrePlace` passes (0 failures), this is a structural P&R divergence.** Investigate the netlist before concluding it cannot be fixed.

Root cause: P&R tools insert HFS buffer trees for high-fanout signals. ECO-inserted gates may reference Synthesize net names that are renamed or split in P&R. The correct fix is always `fix_named_wire` — rewire the gate input to use the correct P&R net name. Use Priority 3 structural driver trace (eco_netlist_studier) to find the P&R alias.

**eco_fm_analyzer classification (Mode G) — only after confirming netlist fix is impossible:**
- Detect: `FmEqvEcoRouteVsEcoPrePlace` PASS (0 failures), `FmEqvEcoPrePlaceVsEcoSynthesize` FAIL (≥ 10 failures)
- Step 1 — Try fix_named_wire first: trace each failing DFF D-input back to the ECO gate. Check whether any ECO gate input uses a Synthesize net name that is renamed in PrePlace. If found → `fix_named_wire` is the correct action (Mode H). Do NOT proceed to suppression.
- Step 2 — Only if all ECO gate inputs are correct AND the failures are pure HFS consumer cascades (none of the failing DFFs match any `target_register` in RTL diff, AND Priority 3 structural trace confirms no fixable net): classify Mode G.
- Mode G action: `set_dont_verify` scoped to the common hierarchy prefix of all failing paths. NEVER use wildcard `*` beyond the proven failing scope.

**NEVER apply Mode G suppression when Route-vs-PrePlace also fails** — that indicates a real ECO error requiring a netlist fix, not a structural mismatch.

---

## RULE 23 — New Condition Signals May Be New Ports From the Same ECO

**When building an intermediate net gate chain (Step 0c), new condition signals may not exist in the PreEco netlist because they are simultaneously being added as new input ports by `new_port` / `port_declaration` changes in the same ECO.**

Do NOT fail or skip a gate entry simply because its input signal is absent from the PreEco netlist. First check whether the signal appears in the RTL diff JSON as a `new_port` or `port_declaration` change:
- If yes → set `input_from_change` referencing the port_declaration entry and mark `new_port_dependency: true`. The signal will exist after eco_applier Pass 2 (port_declaration) runs, within the same decompress/recompress cycle.
- If no → apply Priority 1/2 alias lookup; if still not found, flag as SKIPPED.

This ordering works because eco_applier processes: Pass 1 (new_logic gate insertion) → Pass 2 (port_declaration adds the signal) → all passes operate on the same temp file before recompression. The gate inserted in Pass 1 references a net that is declared in Pass 2 — the final recompressed netlist is consistent.

---

## RULE 24 — Port List Depth Tracking Must Search Full Module Scope Including Long P&R Port Lists

**When finding the port list closing `)` using depth tracking in PORT_DECL Step 2, the search range must be `range(mod_idx, endmodule_idx)` — NOT `range(mod_idx, len(lines))` without an upper bound, and NOT a fixed-size window.**

The `endmodule_idx` must be found by matching lines whose non-comment content equals `endmodule` (strip trailing `// comments` before comparing). P&R stage port lists are significantly longer than Synthesis stage port lists because P&R adds scan chain, test, and clock distribution ports. A port list in a Synthesis stage netlist may be significantly shorter than the same module's port list in a P&R stage netlist. The depth tracking loop must complete over the full range without artificial limits.

If `port_list_close_idx` is None after the loop: do NOT silently proceed. Record as SKIPPED with a detailed reason (which module, which line range was searched) so the issue is visible in the Step 4 RPT and can be debugged without re-running the entire flow.

---

## RULE 25b — eco_applier Must Self-Validate Verilog Before Recompressing

**eco_applier Step 4b is MANDATORY and must pass before Step 5 (recompress).** The 4 checks (duplicate ports, unclosed port list, duplicate direction declarations, module count) take seconds and prevent FM ABORT conditions that waste 1-2 hour FM slots.

All confirmed FM ABORTs (FM-599, FE-LINK-7) in this flow traced back to eco_applier producing invalid Verilog. FM is NOT the right place to discover these — eco_applier must validate its own output first.

If Step 4b fails → record VERIFY_FAILED, do NOT recompress, do NOT submit FM. The ORCHESTRATOR/ROUND_ORCHESTRATOR will handle the failure without wasting an FM slot.

---

## RULE 25 — Run Pre-FM Integrity Checks Before Every FM Submission

**Before submitting FM (Step 6), always run Step 5 (Pre-FM Quality Checker) — the 4 pre-FM checks:**
1. No SKIPPED entries for port_declaration or port_connection changes
2. No Verilog syntax errors (unbalanced parentheses) in any PostEco stage
3. All 3 stages contain ECO cells (non-zero `eco_<jira>_` count)
4. Port declaration signals present in all 3 stages

FM jobs run for 1–2 hours. A corrupt netlist or missing port declaration causes an immediate FM 599 error (read failure) or N/A results, wasting the entire slot. The 4 pre-FM checks take seconds and catch these issues before the FM job is submitted.

---

---

## RULE 26 — FM ABORT (N/A) Goes to ROUND_ORCHESTRATOR — Never Self-Fix

**When FM produces N/A results (no matching/failing points — FM aborted before comparison), the agent MUST NOT attempt to diagnose or fix the issue itself. It MUST hand off to ROUND_ORCHESTRATOR exactly as it would for a real FAIL.**

This applies to:
- **eco_fm_runner**: When spec shows N/A or no failing points — write eco_fm_verify.json with `status: "ABORT"` and **EXIT IMMEDIATELY**. Do NOT re-submit FM. Do NOT apply any patches. Do NOT loop.
- **ORCHESTRATOR After Step 6**: When eco_fm_verify.json shows ABORT or FAIL — write round_handoff.json + spawn ROUND_ORCHESTRATOR + HARD STOP. Do NOT try to fix the netlist, SVF, or cell types yourself.
- **ROUND_ORCHESTRATOR After Step 6**: When eco_fm_verify.json shows ABORT or FAIL — update round_handoff.json + spawn next ROUND_ORCHESTRATOR + EXIT. Do NOT re-submit FM or apply inline patches.

**The chain for ABORT:**
```
FM ABORTS (N/A)
→ eco_fm_runner writes abort result → EXIT
→ ORCHESTRATOR/ROUND_ORCHESTRATOR reads result → writes round_handoff.json → spawns next agent → HARD STOP
→ ROUND_ORCHESTRATOR (next instance): Step 6d eco_fm_analyzer diagnoses abort type
→ Steps 6f, 4, 5: fix + re-run FM
```

**Why this rule exists:** Agents see N/A results (no matching or failing points report) and interpret it as "something went wrong that I must fix immediately." This causes them to apply patches, re-submit FM, or loop — all within the same round — bypassing the ROUND_ORCHESTRATOR diagnosis chain entirely and producing double-applied or corrupt netlists.

> **Confirmed failure mode:** A confirmed ECO run where the ORCHESTRATOR looped FM multiple times internally without spawning ROUND_ORCHESTRATOR, producing double-applied PostEco netlists.

---



## RULE 27 — Netlist Fix Priority: Never Use Tune Files or SVF as Shortcuts

**The correct fix for any FM failure is always a netlist change.** `set_dont_verify`, `set_user_match`, and tune file updates are not fixes — they suppress FM's ability to verify the ECO and produce an unverified result.

**SVF updates are for MANUAL ENGINEERS ONLY. The AI flow is PROHIBITED from applying any SVF update.**

`set_dont_verify`, `set_user_match`, and `guide_eco_change` are SVF commands. The AI flow must NEVER write them to `EcoChange.svf` or any `eco_svf_entries.tcl`. Step 4b (eco_svf_updater) is permanently disabled. `svf_update_needed` is always `false`.

**Priority order for every FM failing point:**

| Priority | Action | When |
|----------|--------|------|
| **1 — Netlist fix** | Rewire, re-insert, fix_named_wire, correct port connections | ALWAYS try this first |
| **2 — MANUAL_ONLY** | Report to engineer; stop the fix loop | Only when Priority 1 is proven impossible (net absent via Priority 3 structural trace AND ECO is architecturally correct) |
| **NEVER** | SVF updates (`set_dont_verify`, `set_user_match`) | The AI flow is prohibited from applying SVF |
| **NEVER** | Tune file updates to work around FM failures | Tune files mask problems, not fix them |

**HFS net rename = netlist fix, not SVF:**
When an ECO-inserted gate uses a net in Synthesize that is renamed by P&R (HFS distribution, CTS buffering), the fix is `fix_named_wire` — rewire the gate input to the correct P&R net name. Do NOT suppress with `set_dont_verify`. The named wire approach makes the ECO structurally correct in P&R stages. Using suppression means the ECO gate has a wrong input in P&R stages — it just passes FM without being correct.

**A passing FM via SVF suppression is NOT a verified ECO.** The goal is zero failing points via correct netlist, not zero failing points via suppression.

---

## RULE 28 — eco_applier: Four-Pass Processing Order is Mandatory

Within each stage's decompress/edit/recompress cycle, changes MUST be applied in this exact order:

1. **Pass 1 — new_logic insertions** (new_logic_dff, new_logic_gate): insert all new cells so their output nets exist
2. **Pass 2 — port_declaration**: update module port lists and direction declarations
3. **Pass 3 — port_connection**: add `.port(net)` connections to module instance blocks
4. **Pass 4 — rewire**: change pin connections on existing cells

**Why this order is load-bearing:** Port declarations must exist before connections reference them. New cells must exist before rewires reference their output nets. Reversing any step causes silent failures — rewires skip because the target cell doesn't exist yet, port connections skip because the port isn't declared yet.

**Round 2+ Surgical Patch Mode:** Never revert PostEco to PreEco. ROUND_ORCHESTRATOR Step 6b backs up current PostEco BEFORE spawning eco_applier. eco_applier uses the backup MD5 as the verified starting point. Only entries with `force_reapply: true` are undone and re-applied; all other ALREADY_APPLIED entries are kept as-is.

---

## RULE 29 — eco_netlist_studier: Phase 0 Before Phase 1

When eco_netlist_studier processes changes, **Phase 0 (new_logic and new_port changes) MUST complete before Phase 1 (FM-returned cells from fenets)**:

- **Phase 0**: Process all `new_logic_dff`, `new_logic_gate`, `new_port`, `port_promotion`, `port_declaration`, `port_connection` changes — inserting new cells and establishing port connectivity in the study JSON
- **Phase 1**: Process FM find_equivalent_nets results — identifying existing cells to rewire based on confirmed cell lists

**Why:** Phase 1 rewires reference output nets from Phase 0 new_logic cells. If Phase 1 runs first, those output nets don't exist yet → FM-returned cells appear unresolvable → rewires are incorrectly skipped → ECO is incomplete.

---

## RULE 30 — eco_fm_analyzer: Check F (Unresolved Condition Inputs) Runs First

Before any failure mode classification (Mode A through H), eco_fm_analyzer MUST run **Check F — Unresolved condition inputs**:

Scan `eco_preeco_study.json` for any gate input containing `PENDING_FM_RESOLUTION:<signal>`. If found, ALL downstream failures (DFF0X, non-equivalent DFFs) likely trace back to this root cause — they will appear as Mode A or Mode H without this upstream cause being obvious.

**Action:** Set `needs_rerun_fenets: true` (first occurrence) or `action: structural_trace` (if prior rerun already returned FM-036). Do NOT classify Mode A/H/E until condition inputs are resolved.

**Why:** An unresolved condition input means an entire gate chain has wrong connections. Diagnosing Mode A or Mode H on downstream DFFs without fixing the root condition input wastes rounds on symptoms rather than cause.

---

## RULE 31 — eco_pre_fm_checker: Four Checks Are FM-Abort Preventers

Before every FM submission, eco_pre_fm_checker runs checks that prevent FM-599 (Verilog abort) and FM FE-LINK-7 (port not defined) — FM aborts that waste 1-2 hours per occurrence:

- **Check A** (Stage consistency): ECO cells present in all 3 stages — mismatches cause thousands of non-equivalent stage-to-stage DFFs
- **Check F1** (Duplicate wire): `wire X; wire X;` in same module → FM SVR-9 → FM-599
- **Check F2** (Implicit wire conflict): `wire X;` + `.anypin(X)` port connection → FM SVR-9 → FM-599
- **Check F3** (Duplicate port connection): `.pin(net)` twice in same instance → FM-599
- **Check G** (Port direction completeness): port in module header without `input/output` in body → FM-599

These checks run in seconds. FM-599 aborts are detected only after 1-2 hours of FM runtime. **Never skip eco_pre_fm_checker** — it is the last gate before an expensive FM slot is consumed.

---

## RULE 32 — Always Use Real RTL-Named Net, Not HFS Alias, When Both Exist

**Applies to:** eco_netlist_studier (port_connections_per_stage), eco_applier (all passes)

When selecting a net to write into any `port_connections_per_stage` entry (gate inputs, rewire targets, DFF D-input, port connections), **always prefer the real RTL-named net over any P&R-generated HFS alias** when both exist in the current stage's PostEco netlist.

| Net type | How to identify | Preference |
|----------|----------------|-----------|
| **Real net** | Matches `old_token` or `new_token` from RTL diff JSON. Found in RTL source files (`data/SynRtl/*.v` or `data/PreEco/SynRtl/*.v`) as a `reg`, `wire`, or port declaration. Stable across P&R runs. | **USE FIRST** |
| **P&R alias** | Does NOT appear in RTL source. Exists only in gate-level netlists (Synthesize/PrePlace/Route). Created by P&R tools as buffer/clone nets for high-fanout signals. May change name between P&R runs. | Use ONLY if real net absent |

**Detection method — is a net a P&R alias?**
```bash
# A net is a P&R alias if it does NOT exist in the RTL source:
grep -rw "<net_name>" <REF_DIR>/data/PreEco/SynRtl/ | grep -v "^Binary"
# count = 0 → P&R alias (not from RTL source)
# count > 0 → real RTL-named net
```

**Rule:** For every net connection:
1. Check if real RTL-named net exists in current stage PostEco: `grep -cw "<real_net>" <PostEco_stage>` — if ≥ 1, use it.
2. Only if count = 0 → fall back to P&R alias search via Priority 2/3 structural trace.

**Why:** HFS aliases change between P&R runs. Using an alias in Round 1 that gets renamed in Round 2 causes SKIPPED/UNRESOLVABLE entries. Real RTL-named nets are stable across rounds, make eco_fm_analyzer diagnosis cleaner, and avoid false Mode H classifications.

---

## RULE 33 — Wire Declaration: Three Categories, No Exceptions

**Applies to:** eco_applier, eco_netlist_studier, and rtl_diff_analyzer.

**NEVER add explicit `wire <net_name>;` for:**
- Nets created implicitly by port connections (a net appearing as the connected signal in ≥ 2 `port_connection` entries in the same parent module scope) — Verilog creates these implicitly; an explicit `wire <net_name>;` causes FM-599
- Nets created by renaming an original driver cell's output (the renamed cell binding `.<output_pin>(<new_name>)` creates the implicit wire)

**MUST add explicit `wire <net_name>;` for:**
- New intermediate nets between ECO-inserted gates where `needs_explicit_wire_decl: true` is set in the study JSON — these nets are coined fresh; they do not appear in any port connection or cell binding yet, so Verilog cannot create them implicitly

eco_applier reads the `needs_explicit_wire_decl` flag to implement this rule. eco_pre_fm_checker Check F (`--strict`) catches any violations.

> **This rule prevents:** FM-599 errors caused by either (a) adding explicit `wire <net_name>;` for nets already created implicitly by port connections, or (b) omitting explicit `wire <net_name>;` for brand-new intermediate nets that have no other declaration path.

---

## RULE 34 — FM Failure Mode Reference Table (eco_fm_analyzer output)

| Mode | Code | Meaning | Safe auto-fix | Manual flag |
|------|------|---------|---------------|-------------|
| A | `A` | ECO change not correctly applied (SKIPPED/wrong net/wrong gate) | Re-apply with corrected approach | No |
| B | `B` | Wrong cell rewired — ECO gate drives unrelated DFFs | Exclude the rewire entry | Sometimes |
| C | `C` | Partial progress — confirmed entry missing from eco_applied | Re-apply missing entry | No |
| D | `D` | Stage mismatch — cell/net name differs between stages | Grep PostEco for correct name | No |
| E | `E` | Pre-existing failure — no ECO contact (5-condition proof required) | set_dont_verify | Yes — engineer |
| F1 | `F1` | d_input_decompose_failed with intermediate_net_insertion | Invert MUX constants / pivot | No |
| F2 | `F2` | d_input_decompose_failed with no fallback strategy | manual_only | Yes |
| F3 | `F3` | Pre-existing DFF downstream of wrong ECO gate chain | Fix ECO gate (Mode A) | No |
| G | `G` | Structural P&R stage mismatch (HFS cone divergence) | fix_named_wire or set_dont_verify | Sometimes |
| H | `H` | Gate input driven only through hierarchical submodule bus | fix_named_wire | No |
| `INCOMPLETE_AND_TERM` | — | and_term gate drove new net instead of port directly (GAP-15) | Re-study with module_port_direct_gating | No |
| `INTENTIONAL_CASCADE` | — | DFF downstream of gated port — value intentionally changed by ECO | manual_only immediately (Round 1); engineer applies set_dont_verify | Yes — engineer |
| `WRONG_GATE_STRUCTURE` | — | MUX2 cascade in d_input_decompose_failed creates FM-unverifiable structure vs RTL synthesis | Re-study with structural_insertion; discover compound gate types from PreEco — never MUX2 | No — netlist fixable |
| `CTS_CLOCK_RENAMED` | — | ECO DFF CP pin renamed by CTS in Route — PrePlace and Route use different clock net names | Rewire CP to CTS-assigned clock net found in neighbour DFF of same clock domain in Route PostEco | No — netlist fix |
| `CTS_BBNET_INPUT` | — | ECO gate input passes through CTS multi-driver merged cell in Route → FM black-boxes → DFF0X | Rewire gate input to primary input port declaration for same signal (single driver, FM-traceable) | No — netlist fix |

ECO-inserted DFFs (`eco_<jira>_` pattern) are **never** Mode E or subject to `set_dont_verify`.

---

## RULE 35 — MAX_ROUNDS is the ONLY exit (manual_only ABOLISHED)

- **`manual_only` is ABOLISHED** — eco_fm_analyzer never prescribes it. For every failure, prescribe a progressive action: conservative_constant, try_structural_insertion, try_alternative_pivot, move_gate_to_submodule, cascade_verified_skip.
- **MAX_ROUNDS**: Round 10 completed with FM still failing → spawn FINAL_ORCHESTRATOR with `status: MAX_ROUNDS`. This is the only exit besides FM_PASSED.
- No MANUAL_LIMIT. ROUND_ORCHESTRATOR always runs all 10 rounds or until FM passes.
- FINAL_ORCHESTRATOR maps: MAX_ROUNDS → "FAIL — Max rounds reached"; FM_PASSED → "PASS".

---

## RULE 36 — round_handoff.json Required Fields

Every ORCHESTRATOR and ROUND_ORCHESTRATOR MUST write these fields before spawning the next agent:

```json
{ "tag": "str", "ref_dir": "path", "tile": "str", "jira": "str",
  "base_dir": "path", "ai_eco_flow_dir": "path",
  "round": 1,
  "eco_fm_tag": "20260427HHMMSS | null (if FM not run)",
  "status": "FM_PASSED | FM_FAILED | MANUAL_LIMIT | MAX_ROUNDS",
  "pre_fm_check_failed": false }
```
Missing any field → the spawned agent cannot recover state correctly.
