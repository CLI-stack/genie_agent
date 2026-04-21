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

> **Root cause of confirmed bug:** Agent saw `AI_ECO_FLOW_20260416221446` and `AI_ECO_FLOW_20260417040218` in REF_DIR, copied fenets RPTs from them, and skipped Step 2 entirely.

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

All hierarchy paths in ECO changes use **instance names** (e.g., `I_ARB`, `I_TIM`), not module names (e.g., `umcarb`, `umctim`). Confusing the two will cause the applier to fail to locate cells.

---

## RULE 8 — Email is Mandatory at Every Stage

- ROUND_ORCHESTRATOR: per-round email (Step 6a) is mandatory BEFORE revert (Step 6b). Never skip.
- FINAL_ORCHESTRATOR: final email (Step 8) is mandatory. Verify `Email sent successfully` before cleanup.
- Retry once on failure. Never silently skip.

---

## RULE 9 — Single-Occurrence Rule for PostEco Edits

If `old_net` appears more than once on a given pin in the PostEco netlist, **skip and report AMBIGUOUS**. Do not apply a partial or guessed rewire.

---

## RULE 10 — No-Equiv-Nets Retries Are Mandatory (ORCHESTRATOR only)

When FM returns No Equivalent Nets or FM-036 in Step 2, retries MUST be attempted before falling back to grep/stage fallback. Retry direction is always **deeper** (add sub-instance level) — never shallower.

The retry strategies in Step 2 of ORCHESTRATOR.md are NOT optional. Only after all retries are exhausted may fallback be applied.

---

## RULE 11 — SVF Command Format Must Match FM Version

EcoChange.svf must use `guide_eco_change` (not `eco_change`) for FM version X-2025.06-SP3-VAL-20251201 and later. Using `eco_change` causes an elaboration failure (CMD-005) that fails all 3 FM targets before any comparison occurs.

When writing SVF entries (Step 4b), always use the `guide_eco_change` format. Validate the SVF format before running PostEco FM.

> **Root cause of confirmed bug (DEUMCIPRTL-9868 Round 1):** `EcoChange.svf` used `eco_change` — all 3 FM targets failed with elaboration error.

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

> **Root cause of confirmed bug (DEUMCIPRTL-9868 Round 1):** eco_applier only modified Synthesize; PrePlace and Route were unchanged.

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

> **Root cause of confirmed bug (DEUMCIPRTL-9868 Round 2):** eco_applier sub-agent exited after writing JSON due to context pressure. RPT was never written. ORCHESTRATOR checkpoint only checked JSON — missed the missing RPT. Round 2 RPT never appeared in AI_ECO_FLOW_DIR.

---

## Quick Checklist — Before Each Step Transition

| Before entering... | Verify on disk (JSON + RPT) |
|--------------------|---------------------------|
| Step 2 | `data/<TAG>_eco_rtl_diff.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step1_rtl_diff.rpt` ✓ |
| Step 3 | `data/<TAG>_eco_step2_fenets.rpt` ✓ + all fenets raw RPTs in AI_ECO_FLOW_DIR ✓ |
| Step 4 | `data/<TAG>_eco_preeco_study.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step3_netlist_study.rpt` ✓ |
| Step 4b | `data/<TAG>_eco_applied_round<N>.json` ✓ + `AI_ECO_FLOW_DIR/<TAG>_eco_step4_eco_applied_round<N>.rpt` ✓ + all 3 stages md5-differ from backup ✓ |
| Step 5 | `data/<TAG>_eco_svf_entries.tcl` ✓ (if new_logic) |
| After Step 5 | `data/<TAG>_round_handoff.json` ✓ — then spawn — then STOP |
| Step 7b | `data/<TAG>_eco_summary.rpt` ✓ |
| Step 8 | `data/<TAG>_eco_report.html` ✓ |

---

*Last updated: 2026-04-21 — rules added from confirmed bugs in DEUMCIPRTL-9874 (spawn/handoff violation, AI_ECO_FLOW reuse) and DEUMCIPRTL-9868 Round 1 (SVF format, partial stage application).*
