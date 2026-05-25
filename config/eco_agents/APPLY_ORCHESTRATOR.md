# ECO APPLY Orchestrator (Phase B — Steps 4-6)

**You are the APPLY phase orchestrator.** STUDY_ORCHESTRATOR (Phase A) completed Steps 1-3 and emitted an `APPLY_PHASE_READY` signal. You handle Steps 4-6 in a fresh agent context.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md`, then `config/eco_agents/CRITICAL_RULES.md`. Then continue here.

**Inputs (from prompt):**
```
TAG          = <TAG>
REF_DIR      = <REF_DIR>
TILE         = <TILE>
JIRA         = <JIRA>
BASE_DIR     = <BASE_DIR>
AI_ECO_FLOW_DIR = <AI_ECO_FLOW_DIR>
HANDOFF_PATH = <BASE_DIR>/data/<TAG>_phase_a_handoff.json
```

---

## PRE-FLIGHT (mandatory)

Read `<HANDOFF_PATH>` and verify Phase A artifacts on disk before any Step 4 work:

```bash
test -s <HANDOFF_PATH>                                              # non-empty
python3 -c "
import json, sys
h = json.loads(open('<HANDOFF_PATH>').read())
assert h.get('tag') == '<TAG>', f'TAG mismatch: handoff={h.get(\"tag\")} prompt=<TAG>'
assert h.get('phase_a_status') == 'READY_FOR_PHASE_B', f'Phase A not ready: {h.get(\"phase_a_status\")}'
for k in ('rtl_diff', 'fenets_rename_map', 'preeco_study'):
    p = h['artifacts'][k]
    assert __import__('os').path.exists(p), f'Phase A artifact missing: {p}'
print('PRE-FLIGHT OK')
"
```

Failure → write `<TAG>_round_handoff.json` with `status: 'PHASE_A_HANDOFF_INVALID'` + EXIT.

**MANDATORY: Task-tracking for live progress visibility (per Claude Code UI conventions)**

Immediately after pre-flight passes, create one task per step you will execute:

```python
TaskCreate(subject="Step 4: Apply ECO to PostEco",       activeForm="Applying ECO to PostEco netlists")
TaskCreate(subject="Step 5: Pre-FM Quality Check",       activeForm="Running pre-FM quality checks")
TaskCreate(subject="Step 6: PostEco FM Verification",    activeForm="Submitting PostEco Formality Verification")
```

Before invoking each step's sub-agent: `TaskUpdate(taskId=<step_task>, status="in_progress")`.
After step's checkpoint passes: `TaskUpdate(taskId=<step_task>, status="completed")`.
For Step 6 FM polling, refresh `activeForm` periodically with elapsed time + per-target status:
`TaskUpdate(taskId=step6_task, activeForm=f"FM polling — {elapsed_min} min, Synth={s} PP={p} Route={r}")`.

---

## STEP 4 — Apply ECO to PostEco Netlists

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_applier.md` prepended. Pass:
- `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (current round number — 1 for initial run, 2/3/... for fixer loop), `AI_ECO_FLOW_DIR`
- The PreEco study JSON from Step 3: `<BASE_DIR>/data/<TAG>_eco_preeco_study.json` — this is the **fully enriched** JSON written by eco_netlist_verifier (not the initial skeleton from eco_netlist_studier). It contains `port_connections_per_stage` for all 3 stages, auto-added port_declaration and consumer rewire entries, and all GAP-15 corrections.
- Task: For each confirmed cell, backup PostEco netlist (using `bak_<TAG>_round<ROUND>` naming), locate same cell, verify old_net on pin, replace with new_net (rewire) or auto-insert inverter (new_logic), recompress, verify
- Output: `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`

Wait for eco_applier sub-agent to complete.

**CHECKPOINT:** Verify `data/<TAG>_eco_applied_round<ROUND>.json` exists and contains a `summary` field. Check that backup files `<REF_DIR>/data/PostEco/<Stage>.v.gz.bak_<TAG>_round<ROUND>` exist for each stage that had confirmed cells. Do NOT continue to Step 5 (Pre-FM Quality Checker) if file is missing.

**Generate Step 4 RPT from JSON (ORCHESTRATOR responsibility — NOT eco_applier):**

```bash
cd <BASE_DIR> && python3 script/eco_scripts/eco_rpt_generator.py step4 \
    --applied data/<TAG>_eco_applied_round<ROUND>.json \
    --tag <TAG> --jira <JIRA> --round <ROUND> \
    --output  data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt
```
Output format: header with summary counts, then `[stage]` sections each listing every entry as `STATUS name type=...` plus a `→` detail line (cell_type for INSERTED, rename for rewire APPLIED, reason for ALREADY_APPLIED/SKIPPED/VERIFY_FAILED). Every entry is self-explanatory — no one-liners without context.

Copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied_round<ROUND>.rpt
```

---


---

## STEP 5 — Pre-FM Quality Checker (MANDATORY)

**BEFORE spawning eco_pre_fm_checker: run eco_check8.sh directly from the ORCHESTRATOR.**

This is the most critical syntax gate. eco_check8.sh runs the Verilog validator deterministically — it CANNOT be skipped or replaced by manual grepping. Run it NOW:

```bash
cd <BASE_DIR>
bash script/eco_scripts/eco_check8.sh \
    <BASE_DIR> <REF_DIR> <TAG> 1 \
    data/<TAG>_eco_applied_round1.json
CHECK8_EXIT=$?
```

Read `data/<TAG>_eco_check8_round1.json`. If any stage is FAIL → **do NOT spawn eco_pre_fm_checker yet**. Fix the syntax issues first using the inline fix procedures in eco_pre_fm_checker.md, then re-run eco_check8.sh. Only proceed when all 3 stages are PASS.

Pass `CHECK8_RESULT_PATH=data/<TAG>_eco_check8_round1.json` to the eco_pre_fm_checker sub-agent — it reads this pre-computed result directly (does NOT re-run eco_check8.sh).

**Spawn a sub-agent (general-purpose)** with `config/eco_agents/eco_pre_fm_checker.md` prepended. Pass:
- `TAG`, `REF_DIR`, `BASE_DIR`, `ROUND=1`, `AI_ECO_FLOW_DIR`
- Path to applied JSON: `<BASE_DIR>/data/<TAG>_eco_applied_round1.json`
- `CHECK8_RESULT_PATH=<BASE_DIR>/data/<TAG>_eco_check8_round1.json` (pre-computed by ORCHESTRATOR — do NOT re-run)

Wait for sub-agent to complete.

**Read result — gate FM submission:**

**MANDATORY JSON INTEGRITY GATE** — run BEFORE schema validation. Catches `PASS_OVERRIDE` tampering by the agent (a real failure mode observed in 9868 R2):
```bash
python3 script/eco_scripts/eco_validate_pre_fm_integrity.py \
    --check-json data/<TAG>_eco_pre_fm_check_round1.json
# exit 1 → tampered or contradictory; abort and re-spawn eco_pre_fm_checker on a fresh file
```

**MANDATORY JSON SCHEMA VALIDATION** — verify the eco_pre_fm_checker followed the output contract:
```python
check = load(f"data/{TAG}_eco_pre_fm_check_round1.json")

# Validate required fields — if any missing, eco_pre_fm_checker did not follow the schema
required = ["tag", "round", "passed", "attempts", "issues_found", "issues_fixed",
            "issues_unresolved", "warnings", "check_summary"]
missing = [f for f in required if f not in check]
if missing:
    raise RuntimeError(f"eco_pre_fm_checker JSON missing required fields: {missing}. "
                       f"Re-spawn eco_pre_fm_checker to produce a conformant JSON.")

# Validate check_summary has check8_verilog_validator
if "check8_verilog_validator" not in check.get("check_summary", {}):
    raise RuntimeError("eco_pre_fm_checker JSON missing check_summary.check8_verilog_validator. "
                       "The --strict Verilog validator was not run. Re-spawn eco_pre_fm_checker.")

if check["passed"]:
    # All checks passed → proceed to Step 6
    pass
else:
    # -----------------------------------------------------------------------
    # STEP 5 SELF-HEALING LOOP — MANDATORY. DO NOT SKIP.
    # -----------------------------------------------------------------------
    # Step 5 failures are APPLIER-SIDE issues (missing ports, wrong cell,
    # duplicate wire, semantic mismatch). They MUST be fixed here before FM.
    # NEVER escalate Step 5 failures to ROUND_ORCHESTRATOR — ROUND is ONLY
    # for FM logical mismatches that require re-studying the netlist.
    #
    # Each iteration:
    #   1. Read failures from pre_fm_check JSON
    #   2. Fix each failure directly: update study JSON + re-apply + re-check
    #   3. Re-run eco_pre_fm_checker
    #   Repeat up to MAX_HEAL times until PASS.
    # -----------------------------------------------------------------------

    MAX_HEAL = 5
    heal_attempt = 0

    while not check["passed"] and heal_attempt < MAX_HEAL:
        heal_attempt += 1
        failures = check.get("failures", [])
        print(f"Step 5 self-heal attempt {heal_attempt}/{MAX_HEAL} — {len(failures)} failures")

        # Read every failure message. For each one:
        #   1. Identify the affected entry in data/<TAG>_eco_preeco_study.json
        #      (by instance_name, cell_name, port_name, or module_name).
        #   2. Fix the study JSON directly — update the field that caused the failure
        #      (e.g. wrong cell_type, missing port_declaration, wrong per_stage_cell,
        #      incorrect net name, missing wire declaration, etc.).
        #   3. If the fix requires a PostEco netlist change (not just study JSON),
        #      use eco_applier or eco_passes_2_4.py with force_reapply to re-apply
        #      only the affected entries.
        # Do NOT call ROUND_ORCHESTRATOR. Fix it here.

        # After fixing, re-apply and re-validate:
        bash eco_applier --force-reapply (re-applies updated study JSON entries)
        bash eco_check8.sh (syntax check all 3 stages)
        spawn eco_pre_fm_checker sub-agent (ROUND=1, CHECK8_RESULT_PATH=data/{TAG}_eco_check8_round1.json)
        check = load(f"data/{TAG}_eco_pre_fm_check_round1.json")

    if check["passed"]:
        pass  # self-healing succeeded → proceed to Step 6
    else:
        # MAX_HEAL exhausted — applier cannot fix this automatically.
        # This is NOT an FM issue — do NOT spawn ROUND_ORCHESTRATOR.
        # ROUND is only for FM logical mismatches. Step 5 failure = applier bug.
        write_round_handoff({
            "status": "STOP",
            "eco_fm_tag": "NOT_RUN_PRE_FM_CHECK_FAILED",
            "pre_fm_check_failed": True,
            "heal_attempts": heal_attempt,
            "next_phase": "STOP",
            "next_phase_reason": (
                f"Step 5 pre_fm_check failed after {heal_attempt} self-healing attempts. "
                f"This is an applier-side issue — NOT an FM logical mismatch. "
                f"Do NOT spawn ROUND_ORCHESTRATOR. Fix the study JSON / applier manually. "
                f"Unresolved failures: {check.get('failures', [])}"
            )
        })
        write apply_phase_exited.marker
        HARD STOP  # Report failures. No FM. No ROUND. Human intervention needed.
```

> **Why before FM:** FM stage-to-stage comparisons (PrePlace vs Synthesize, Route vs PrePlace) fail when stages have different ECO changes applied — e.g., a port added to Synthesize but SKIPPED in PrePlace causes thousands of non-equivalent DFFs. This check takes seconds. FM takes 1-2 hours.

---

## STEP 6 — PostEco Formality Verification

**MANDATORY pre-FM gate — verify Step 5 JSON exists and passed:**
```bash
ls <BASE_DIR>/data/<TAG>_eco_pre_fm_check_round1.json
```
If this file does NOT exist → Step 5 was never run → ABORT. Re-spawn eco_pre_fm_checker. **FM must NEVER be submitted without a passing Step 5 JSON.** No exceptions.

**Spawn a sub-agent (general-purpose)** with the content of `config/eco_agents/eco_fm_runner.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `BASE_DIR`, `AI_ECO_FLOW_DIR`, `ROUND=1`
- `ECO_TARGETS=FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace`
- Task: write FM config, submit FM, block until complete, parse results, write verify JSON + RPT

Wait for the sub-agent to complete.

**CHECKPOINT:** Verify ALL of the following:
```bash
ls <BASE_DIR>/data/<TAG>_eco_fm_verify.json
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step6_fm_verify_round1.rpt
```
Also read `data/<TAG>_eco_fm_tag_round1.tmp` to get `eco_fm_tag` — save it to `eco_fixer_state` if FM failed.

---

### Step 6 Notes (reference — do NOT execute yourself)

> **HARD RULE: ORCHESTRATOR runs PostEco FM EXACTLY ONCE — Round 1 only, all 3 targets.**
> If FM fails after Round 1: do NOT re-run FM. Do NOT write a new eco_fm_config. Do NOT call genie_cli again.
> Instead: write round_handoff.json with `next_phase: ROUND` → emit `ROUND_PHASE_READY` signal block → write exit sentinel → HARD STOP. The main session spawns ROUND_ORCHESTRATOR for round 2.
> Subsequent rounds (Round 2+) are spawned by the main session per `ROUND_PHASE_READY` signal. Each ROUND_ORCHESTRATOR instance runs FM exactly once for its round and then emits its own `ROUND_PHASE_READY` (for round N+1) or spawns FINAL_ORCHESTRATOR.

Full implementation is in `eco_fm_runner.md`. Key rules for the Round 1 sub-agent: write eco_fm_config with ALL 3 targets (fixed filename, not tag-based), poll every 5 minutes with individual Bash tool calls (max 72 polls = 6h), write tmp file with eco_fm_tag.

### Step 6a — Write FM config file (Round 1 only — all 3 targets)

Write to `<REF_DIR>/data/eco_fm_config` — **fixed filename inside refDir** (NOT tag-based):
```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace
RUN_SVF_GEN=0
EOF
```

`RUN_SVF_GEN=0` always — SVF generation is disabled. The AI flow never applies SVF.

### Step 6b — Run PostEco FM (once)

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

The script reads `<REF_DIR>/data/eco_fm_config` automatically.

**[eco_fm_runner sub-agent does this — not the ORCHESTRATOR]** eco_fm_runner reads the tag from CLI output, saves it to `<BASE_DIR>/data/<TAG>_eco_fm_tag_round<ROUND>.tmp`, polls `data/<eco_fm_tag>_spec` every 5 minutes until `OVERALL ECO FM RESULT:` appears.

**[eco_fm_runner sub-agent does this — not the ORCHESTRATOR]** eco_fm_runner parses results and writes `<BASE_DIR>/data/<TAG>_eco_fm_verify.json`. Since all 3 FM targets are always run every round, the JSON is fully updated from the current round — no "carry forward" from prior rounds. Every target has a fresh result:
```json
{
  "FmEqvEcoSynthesizeVsSynRtl": "PASS",
  "FmEqvEcoPrePlaceVsEcoSynthesize": "PASS",
  "FmEqvEcoRouteVsEcoPrePlace": "PASS",
  "failing_points": [],
  "round": 1,
  "eco_fm_tag": "<eco_fm_tag>"
}
```

**OVERALL PASS** (as determined by the ORCHESTRATOR after the sub-agent completes) = all 3 targets show PASS in the merged `eco_fm_verify.json`.

**[eco_fm_runner sub-agent does this — not the ORCHESTRATOR]** eco_fm_runner writes `data/<TAG>_eco_step6_fm_verify_round1.rpt` and copies it to `AI_ECO_FLOW_DIR/`.

**CHECKPOINT:** Verify both `data/<TAG>_eco_fm_verify.json` and `data/<TAG>_eco_step6_fm_verify_round<ROUND>.rpt` exist and are non-empty.

> **CONTEXT PRESSURE WARNING:** After eco_fm_runner completes, your context window may be near its limit. The ONLY remaining actions are: write round_handoff.json → write eco_fixer_state (if FAIL) → spawn next agent → HARD STOP. Make NO other tool calls. Do NOT read any extra files. Do NOT summarize. Do NOT generate reports. The SPAWN must happen — it is your only remaining mandatory action. If you run out of context without spawning, the RESUMPTION CHECK at the top of this file will recover the flow on the next invocation.

---

---

## STEP 6 — ABORT inline recovery loop (v1 schema — single canonical `verdict`)

When Step 6 (eco_fm_runner) finishes, the deterministic `eco_fm_status_collector.py` has written `<TAG>_eco_fm_verify.json` with the canonical v1 schema — single source of truth for FM status. **Read ONLY the `verdict` field for branching.** Legacy fields (`overall_status`, `status`) are gone in v1.

The status_collector also embeds per-target `abort_pattern` + `abort_evidence` + `log_path` directly in the JSON. `abort_recovery_agent` reads those to drive its dispatch (mechanical OR reasoning mode — no whitelist gate at this layer).

### Inline loop (max 10 iterations per orchestrator round)

**No whitelist gate.** Every ABORT_* verdict spawns `abort_recovery_agent`. The agent itself decides whether to apply a YAML mechanical action (when pattern is whitelisted) or to enter reasoning mode (when pattern is `unknown` or non-whitelisted). The orchestrator just iterates.

```python
attempt = 0
while attempt < 10:
    fm = read_json(f'data/{TAG}_eco_fm_verify.json')
    verdict = fm.get('verdict', 'UNKNOWN')

    if verdict == 'PASS':
        break  # converged
    if verdict == 'FAIL':
        break  # logical mismatch — needs Mode A-H analyzer (ROUND_ORCHESTRATOR)
    if not verdict.startswith('ABORT_'):
        break  # NOT_RUN / PARTIAL / UNKNOWN — investigate FM scheduling, not netlist

    attempt += 1
    # Spawn abort_recovery_agent — agent dispatches mechanical vs reasoning internally
    Agent(
        description=f'Patch ABORT iter {attempt}/10',
        subagent_type='general-purpose',
        prompt=f'''Read config/eco_agents/CRITICAL_RULES.md Top-10 first.
Then read config/eco_agents/abort_recovery_agent.md and execute.

Inputs:
TAG          = {TAG}
REF_DIR      = {REF_DIR}
BASE_DIR     = {BASE_DIR}
ROUND        = {ROUND}
ATTEMPT      = {attempt}
FM_VERIFY_PATH = {BASE_DIR}/data/{TAG}_eco_fm_verify.json
HANDOFF_PATH = {BASE_DIR}/data/{TAG}_round_handoff.json
'''
    )

    summary = read_json(f'data/{TAG}_abort_recovery_attempt{attempt}.json')
    # Loop continues only when a real patch landed. Anything else → exit.
    if summary.get('status') != 'PATCH_APPLIED':
        break  # PATCH_INCOMPLETE / PATCH_NOOP / REASONING_REFUSED / etc.

    # ── Selective rerun — only re-submit the targets the recovery agent
    #    actually patched. Prior-PASS targets keep their rpt.gz files on
    #    disk; status_collector reads them and reports their PASS verdict
    #    unchanged. Saves 30-60 min per untouched target.
    ALL_TARGETS = {'FmEqvEcoSynthesizeVsSynRtl',
                   'FmEqvEcoPrePlaceVsEcoSynthesize',
                   'FmEqvEcoRouteVsEcoPrePlace'}
    patched = set(summary.get('patched_targets') or [])
    # Always include any target still in ABORT (the patch may have been
    # cross-target, or recovery agent may have understated scope). Never
    # rerun targets that were already PASS.
    aborted_now = {t for t, info in fm.get('per_target', {}).items()
                   if (info.get('verdict') or '').startswith('ABORT_')}
    rerun_targets = (patched | aborted_now) & ALL_TARGETS
    if not rerun_targets:
        # Defensive fallback — if the agent emitted nothing actionable,
        # rerun all 3 to avoid silently locking in a stale PASS.
        rerun_targets = ALL_TARGETS

    # Resubmit FM — eco_fm_runner re-invokes status_collector via the csh
    spawn_eco_fm_runner(targets=sorted(rerun_targets))

# After loop:
final = read_json(f'data/{TAG}_eco_fm_verify.json').get('verdict', 'UNKNOWN')

# ── Auto-grow YAML pattern library on PASS ─────────────────────────────────
# When reasoning-mode patches led to FM PASS, append validated suggestions to
# eco_fm_abort_patterns_auto.yaml so future runs handle the same ABORT
# mechanically (faster) instead of via reasoning mode (slower).
if final == 'PASS':
    grow_auto_yaml_from_attempts(TAG, BASE_DIR, attempt_count=attempt)
```

### Auto-grow YAML library after PASS

When the recovery loop ends with `verdict == "PASS"` AND any iteration was reasoning-mode (not pure mechanical), scan attempt summaries for `yaml_pattern_suggestion` fields and append validated entries to `config/eco_agents/eco_fm_abort_patterns_auto.yaml`.

```python
def grow_auto_yaml_from_attempts(TAG, BASE_DIR, attempt_count):
    import yaml, re
    main_yaml = 'config/eco_agents/eco_fm_abort_patterns.yaml'
    auto_yaml = 'config/eco_agents/eco_fm_abort_patterns_auto.yaml'
    valid_classes = {'ABORT_NETLIST', 'ABORT_LINK', 'ABORT_SVF', 'ABORT_OTHER'}

    # Existing pattern kinds (main + auto) — for collision check
    existing_kinds = set()
    for path in (main_yaml, auto_yaml):
        if Path(path).is_file():
            existing_kinds |= set((yaml.safe_load(open(path)) or {}).get('patterns', {}).keys())

    new_entries = {}
    for n in range(1, attempt_count + 1):
        summary_path = f'{BASE_DIR}/data/{TAG}_abort_recovery_attempt{n}.json'
        if not Path(summary_path).is_file():
            continue
        summary = read_json(summary_path)
        if summary.get('status') != 'PATCH_APPLIED':
            continue
        for patch in summary.get('patches_applied', []):
            sug = patch.get('yaml_pattern_suggestion')
            if not sug:
                continue
            kind = sug.get('kind', '').strip()

            # Validation gate 1 — kind doesn't collide
            if not kind or kind in existing_kinds or kind in new_entries:
                log_reject(kind, "duplicate or empty kind"); continue
            # Validation gate 2 — abort_class is in enum
            if sug.get('abort_class') not in valid_classes:
                log_reject(kind, "invalid abort_class"); continue
            # Validation gate 3 — regex compiles
            try:
                rx_flags = (re.MULTILINE if sug.get('multiline') else 0) \
                         | (re.IGNORECASE if sug.get('ignore_case') else 0)
                compiled = re.compile(sug['regex'], rx_flags)
            except (KeyError, re.error) as e:
                log_reject(kind, f"regex compile failed: {e}"); continue
            # Validation gate 4 — regex matches the cited evidence
            evidence_log = patch.get('evidence_log_path') or \
                           patch.get('log_excerpts', [{}])[0].get('text', '')
            if evidence_log and not compiled.search(evidence_log):
                log_reject(kind, "regex does not match cited evidence"); continue

            # All gates passed — stage for write
            new_entries[kind] = {
                'abort_class':      sug['abort_class'],
                'regex':            sug['regex'],
                'multiline':        sug.get('multiline', False),
                'ignore_case':      sug.get('ignore_case', False),
                'severity':         sug.get('severity', 'medium'),
                'suggested_action': sug.get('suggested_action', ''),
                'recovery':         sug.get('recovery', {'whitelist': False}),
                '_provenance': {
                    'tag':         TAG,
                    'round':       ROUND,
                    'attempt':     n,
                    'auto_grown':  True,
                },
            }

    if not new_entries:
        return  # nothing to write

    # Validation gate 5 (already implicit) — FM PASSed at end of loop, so
    # the fix actually worked. Append.
    auto_doc = {}
    if Path(auto_yaml).is_file():
        auto_doc = yaml.safe_load(open(auto_yaml)) or {}
    auto_doc.setdefault('schema_version', 1)
    auto_doc.setdefault('patterns', {})
    auto_doc['patterns'].update(new_entries)
    # Atomic write — temp + rename
    tmp = auto_yaml + '.tmp'
    with open(tmp, 'w') as f:
        yaml.safe_dump(auto_doc, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, auto_yaml)
    print(f'YAML auto-grown: appended {len(new_entries)} pattern(s) to {auto_yaml}')
```

**Validation gates (all must pass before any append):**
1. `kind` is non-empty and does NOT collide with existing patterns (main OR auto)
2. `abort_class` is one of `ABORT_NETLIST | ABORT_LINK | ABORT_SVF | ABORT_OTHER`
3. `regex` compiles via `re.compile()` with the requested flags
4. `regex` actually matches the FM log excerpt cited as evidence (proves the suggestion isn't a hallucination)
5. FM moved from ABORT → PASS in this round (implicit — only runs in the `final == 'PASS'` branch)

Rejected suggestions are logged to `<TAG>_yaml_suggestion_rejected_attempt<N>.json` for visibility but do NOT block the PASS outcome.

**Engineer review:** `eco_fm_abort_patterns_auto.yaml` is git-tracked. Engineer can:
- Promote a pattern by moving it into `eco_fm_abort_patterns.yaml` (and removing from auto)
- Edit a pattern (refine regex, change recovery.whitelist) in place
- Delete a bad pattern — next run will use main YAML only

The `_provenance` block on each auto entry shows `tag` / `round` / `attempt` so engineer can trace back to which run discovered it.

### Branch on final verdict (after loop exit)

The verdict drives `next_phase` in `round_handoff.json` (see "After Step 6 — Hand off to next phase" below). Mapping:

| Final verdict + condition | `next_phase` | Hand off |
|---|---|---|
| `PASS` | `FINAL` | spawn FINAL_ORCHESTRATOR inline |
| `FAIL` (logical mismatch — Mode A-H) | `ROUND` | emit `ROUND_PHASE_READY` signal block, EXIT |
| `ABORT_*` and attempt == 10 (loop exhausted) | `STOP` | no spawn, no signal — write reason in handoff and EXIT |
| `ABORT_*` and recovery agent returned `REASONING_REFUSED` | `STOP` | no spawn |
| `ABORT_*` and recovery agent returned `PATCH_INCOMPLETE` | `STOP` | no spawn |
| `NOT_RUN` / `PARTIAL` / `UNKNOWN` | `STOP` | no spawn |

### Notes on the branch table

- **ABORT_* never produces `next_phase: ROUND`.** ABORT must be solved in the same round by `abort_recovery_agent` (mechanical or reasoning mode). After the 10-iter loop exhausts, this orchestrator writes the handoff (`next_phase: STOP`) and exits silently. The recovery_agent owns ABORT recovery end-to-end.
- **`next_phase: ROUND` is for FAIL only.** FAIL = logical mismatch in the design (Mode A-H), which is what re-study + re-apply addresses. ABORT = elaboration / parse error in HOW the netlist was edited — re-study won't help.
- **Reasoning-mode patches are validated.** `PATCH_APPLIED` means the patched netlist passes the relevant pre-FM check. If recovery_agent fixed something but the check still fails, it returns `PATCH_INCOMPLETE` and the loop exits with `next_phase: STOP`.
- **Loop bound = 10 attempts.** Per-attempt MD5 verification + ONE-direct-edit-only rule keeps blast radius bounded.

---

## After Step 6 — Hand off to next phase

> **ANTI-PATTERN WARNING — READ FIRST:**
> Your ONLY job here is: (A) write `round_handoff.json` with `next_phase`, (B) signal OR spawn per `next_phase`, (C) write exit sentinel and STOP.
> Do NOT run Steps 7 or 8. Do NOT generate reports. Do NOT send emails. Do NOT write `eco_summary.rpt` or `eco_report.html`.
> Those files are FINAL_ORCHESTRATOR's responsibility.
> Do NOT spawn ROUND_ORCHESTRATOR yourself — the main session spawns it after detecting the `ROUND_PHASE_READY` signal block.

### Mandatory Step A — Write round_handoff.json FIRST

Decide `next_phase` from the FM verdict:

| Condition | `next_phase` |
|---|---|
| FM verdict = `PASS` | `FINAL` |
| FM verdict = `FAIL` (logical mismatch) | `ROUND` |
| FM verdict = `FAIL` from pre_fm_check (Step 5 failure — FM never submitted) | `ROUND` |
| FM verdict = `ABORT_*` AND inline loop exhausted (10 attempts) | `STOP` |
| FM verdict = `ABORT_*` AND recovery_agent returned `REASONING_REFUSED` | `STOP` |
| FM verdict = `ABORT_*` AND recovery_agent returned `PATCH_INCOMPLETE` | `STOP` |
| FM verdict = `NOT_RUN` / `PARTIAL` / `UNKNOWN` | `STOP` |

Write `<BASE_DIR>/data/<TAG>_round_handoff.json` **before any signal/spawn**:

```json
{
  "tag": "<TAG>",
  "ref_dir": "<REF_DIR>",
  "tile": "<TILE>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "ai_eco_flow_dir": "<REF_DIR>/AI_ECO_FLOW_<TAG>",
  "round": 1,
  "fenets_tag": "<fenets_tag>",
  "eco_fm_tag": "<eco_fm_tag>",
  "status": "<FM_PASSED|FM_FAILED|FM_ABORT_EXHAUSTED|FM_PARTIAL>",
  "next_phase": "<ROUND|FINAL|STOP>",
  "next_phase_reason": "<short note: e.g. 'logical mismatch — Mode A-H', 'FM PASS', 'abort loop exhausted after 10 attempts', 'pre_fm_check failed'>",
  "pre_fm_check_failed": <true|false>
}
```

**CHECKPOINT — MANDATORY:** Verify the file exists and is non-empty:
```bash
ls -la <BASE_DIR>/data/<TAG>_round_handoff.json
```
If missing or empty — write it again. Do NOT proceed until confirmed on disk.

### Mandatory Step B — Signal OR spawn per `next_phase`

#### `next_phase: FINAL` → spawn FINAL_ORCHESTRATOR as a sub-agent (do NOT do FINAL work yourself)

**CRITICAL: Do NOT write eco_summary.rpt, eco_report.html, or send email yourself.** All of that belongs to FINAL_ORCHESTRATOR. Your only job here is to spawn it.

**Do NOT emit `ROUND_PHASE_READY` when `next_phase=FINAL`.** The two branches are mutually exclusive.

**Spawn a sub-agent (general-purpose)** with content of `config/eco_agents/FINAL_ORCHESTRATOR.md` prepended. Pass:
- `TAG`, `REF_DIR`, `TILE`, `JIRA`, `BASE_DIR`
- `ROUND_HANDOFF_PATH`: `<BASE_DIR>/data/<TAG>_round_handoff.json`
- `TOTAL_ROUNDS`: 1

Wait for the sub-agent to complete before writing the exit sentinel.

#### `next_phase: ROUND` → emit `ROUND_PHASE_READY` signal block + EXIT (no spawn)

The main session detects `ROUND_PHASE_READY` (per CLAUDE.md ECO Round Mode) and spawns `ROUND_ORCHESTRATOR` for round 2 in fresh context.

Append this block to `<SPEC_FILE>`:
```
ROUND_PHASE_READY
TAG=<TAG>
REF_DIR=<REF_DIR>
TILE=<TILE>
JIRA=<JIRA>
BASE_DIR=<BASE_DIR>
AI_ECO_FLOW_DIR=<REF_DIR>/AI_ECO_FLOW_<TAG>
LOG_FILE=<LOG_FILE>
SPEC_FILE=<SPEC_FILE>
ROUND=2
HANDOFF_PATH=<BASE_DIR>/data/<TAG>_round_handoff.json
```

Then write `<BASE_DIR>/data/<TAG>_eco_fixer_state`:
```json
{
  "round": 1,
  "tag": "<TAG>",
  "tile": "<TILE>",
  "ref_dir": "<REF_DIR>",
  "jira": "<JIRA>",
  "base_dir": "<BASE_DIR>",
  "ai_eco_flow_dir": "<REF_DIR>/AI_ECO_FLOW_<TAG>",
  "max_rounds": 10,
  "rerun_count_in_round": 0,
  "strategies_tried": [],
  "fm_results_per_round": [
    {
      "round": 1,
      "eco_fm_tag": "<eco_fm_tag>",
      "failing_targets": ["<list>"],
      "failing_count": {"<target>": "<N>"}
    }
  ]
}
```

#### `next_phase: STOP` → no signal, no spawn

Write a one-line note to SPEC_FILE describing why (e.g., `STOP: ABORT loop exhausted after 10 attempts`). Main session reads `next_phase` from handoff and reports stop reason to user.

### Mandatory Step C — Write EXIT sentinel + HARD STOP

The main session uses this marker to verify you honored the EXIT CONTRACT (per CLAUDE.md ECO Apply Mode block).

```bash
date -Iseconds | xargs -I{} echo "exited {}" > <BASE_DIR>/data/<TAG>_apply_phase_exited.marker
ls -la <BASE_DIR>/data/<TAG>_apply_phase_exited.marker
```

This is the LAST file you write. After this:

**Your task ends here. Make no further tool calls. Return your status to the caller.**

You MUST stop after writing the sentinel. Do not:
- Run any bash commands after the sentinel write
- Write any more files
- Read any more files
- Generate any reports or emails
- Spawn ROUND_ORCHESTRATOR yourself (main session does it)
- "Help" the next ROUND or FINAL agent by doing their work early
- Read STUDY-phase MDs (rtl_diff_analyzer / eco_fenets_runner / eco_netlist_studier)

**If you find yourself at this point about to call any tool — STOP. The EXIT CONTRACT forbids further activity.**

---

## Output Files (this agent produces)

| File | Content |
|------|---------|
| `data/<TAG>_eco_analyze` | Metadata: tile, ref_dir, tag, jira |
| `data/<TAG>_eco_rtl_diff.json` | RTL diff analysis + nets to query |
| `data/<fenets_tag>_find_equivalent_nets_raw.rpt` | Raw FM output — all 3 targets concatenated |
| `data/<TAG>_eco_step2_fenets.rpt` | Step 2 RPT — find_equivalent_nets results |
| `data/<TAG>_eco_preeco_study.json` | PreEco netlist confirmation |
| `data/<TAG>_eco_applied_round1.json` | ECO changes applied/inserted/skipped (Round 1) |
| `<REF_DIR>/data/eco_fm_config` | FM run config (fixed filename) |
| `data/<TAG>_eco_fm_verify.json` | PostEco FM verification results (Round 1) |
| `data/<TAG>_eco_fixer_state` | Round tracking (if FM fails) |
| `data/<TAG>_eco_step1_rtl_diff.rpt` | Step 1 RPT (written by rtl_diff_analyzer) |
| `data/<TAG>_eco_step3_netlist_study_round1.rpt` | Step 3 RPT (written by eco_netlist_studier) |
| `data/<TAG>_eco_step4_eco_applied_round1.rpt` | Step 4 RPT Round 1 (written by eco_applier) |
| `data/<TAG>_eco_step6_fm_verify_round1.rpt` | Step 6 RPT Round 1 |
| `data/<TAG>_round_handoff.json` | Handoff with `next_phase` (ROUND→signal main session; FINAL→spawned inline; STOP→no spawn) |
| `data/<TAG>_apply_phase_exited.marker` | EXIT sentinel — main session polls for this to confirm clean exit |
