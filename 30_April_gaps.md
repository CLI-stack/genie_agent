# ECO Auto-Flow Gaps — 30 April 2026

Gaps identified during live 9868/9899 runs. Each entry has: symptom, root cause, fix needed.

---

## GAP-1 — ORCHESTRATOR Spawn-Never-Happens (CRITICAL)

**Symptom:** ORCHESTRATOR completes Steps 1–6, writes `round_handoff.json` with `FM_FAILED`, then hard-stops. ROUND_ORCHESTRATOR is never spawned or runs but immediately stops. Flow stalls with no Round 2.

**Root cause:** After running Steps 1–6 (1–2 hours of FM, file reads, context-heavy analysis), ORCHESTRATOR has consumed ~80% of its context window. By the time it reaches the spawn call, context is exhausted. The Agent() tool call either never executes or produces output text with no actual tool invocation.

**Evidence:** `round_handoff.json` exists with `status: FM_FAILED`, `round: 1` — but no `eco_applied_round2.json`, no `eco_step3_netlist_study_round2.rpt`. Flow frozen permanently.

**Occurs:** Every run, consistently. "It always like this."

**Fix needed:** External resume/monitor mechanism in `genie_cli.py`:
- Detect stalled flow: `round_handoff.json` exists with `status: FM_FAILED` AND no `eco_applied_round{N+1}.json` for round N
- Add `--eco-resume <TAG>` flag to genie_cli.py that reads `round_handoff.json` + `eco_fixer_state` and emits `ECO_ANALYZE_MODE_ENABLED` signal pointing to the existing results
- Claude.md ORCHESTRATOR trigger handler then spawns ROUND_ORCHESTRATOR directly without re-running Steps 1–6

**Workaround (manual):** `python3 script/genie_cli.py --analyze-fixer-only <TAG>` — but this restarts from Round 1 studier, losing FM analysis context.

---

## GAP-2 — eco_applier Agent Deferral (FIXED 30-Apr)

**Symptom:** Step 4 eco_applier deferred port_declarations to "Round 2" with reason "application pending". Step 5 accepted this as WARNING. FM ABORT on all 3 targets.

**Root cause:** eco_applier.md had no explicit rule against deferral. Agent invented "defer to Round 2" behavior. eco_pre_fm_checker.md Check B only validated APPLIED entries, not SKIPPED ones.

**Fix applied:**
- `eco_applier.md`: Added ABSOLUTE RULE — no deferral. All entries applied in current round or genuine FAIL.
- `eco_pre_fm_checker.md` + `eco_pre_fm_check.py`: Check B now FAILs on any port_declaration with SKIPPED/deferred status.

---

## GAP-3 — eco_passes_2_4.py Port Connection Without Comma (FIXED 30-Apr)

**Symptom:** Port connection inserted on same line as last existing port without comma. FM: "Expected ',' or ')' but found '.'". FM-599 ABORT.

**Root cause:** Depth tracker in `apply_port_connection()` found wrong `inst_close` when instance has multi-line `{ }` bus port connections (100+ ports). Used `close_line` modification approach — fragile when depth tracking is inaccurate.

**Fix applied:** `eco_passes_2_4.py` — replaced fragile `close_line` modification with robust insert-on-new-line approach: find last non-empty port line, ensure it ends with `,`, insert new port as dedicated new line before `inst_close`.

---

## GAP-4 — validate_verilog_netlist.py Missing SVR Checks (FIXED 30-Apr)

**Symptom:** FM-599 ABORT from missing comma between ports not caught by Step 5 validator. eco_check8.sh parse_stage() didn't FAIL on new error patterns.

**Fix applied:** Added 4 new checks to `validate_verilog_netlist.py`:
- `SVR4_missing_comma`: `.port(net) .port(net)` without comma
- `SVR4_dup_port`: same port name twice in module header
- `SVR4_empty_connection`: `.port()` empty net
- `SVR14_scalar_indexed`: `scalar[N]` bus indexing on wire

Added all 4 to `eco_check8.sh` parse_stage() FAIL pattern.

---

## GAP-5 — Step 5 Agent Judgment (FIXED 30-Apr)

**Symptom:** Agent-based Step 5 made judgment calls — classified FAIL conditions as WARNING, let FM proceed, FM ABORT.

**Fix applied:** `eco_pre_fm_check.py` — new deterministic script (6 checks, no agent judgment). eco_pre_fm_checker.md updated: agent runs script, trusts exit code, no overrides. 16/16 test cases pass.

---

## OPEN GAPS (not yet fixed)

### GAP-6 — ROUND_ORCHESTRATOR Max Rounds Only 10

Current `max_rounds=10`. For complex ECOs (9905-class), 10 rounds may not be enough if decompose fails in multiple stages. Consider making max_rounds configurable per JIRA complexity.

### GAP-7 — eco_9899_9 Route Permanently Inaccessible

`DcqArb1_QualPhArbReqVld` driver removed by P&R in Route. AI flow cannot fix. Requires engineer to add port chain. Flow should detect UNRESOLVABLE conditions earlier (Round 1 FM) and immediately classify as ENGINEER_ONLY to avoid wasting rounds.

### GAP-8 — 2-bit Bus Assignment in NxtRdCtrEntry (9905)

JIRA 9905 has `assign NxtRdCtrEntry[1:0] = ...` with replication operators `{2{...}}`. Our `d_input_decompose_failed` path does intermediate_net_insertion on scalar (1-bit) pivot — 2-bit bus not handled. Flow will fail to insert gates for this change.

---

*Last updated: 2026-04-30*
