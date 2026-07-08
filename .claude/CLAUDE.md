# UVM RTL Testbench - Claude Code Instructions

This directory provides Claude Code with skills, agents, and templates for UVM-based RTL verification work.

---

## External References

| Topic | Reference File |
|-------|----------------|
| **CDC/RDC Issues** | `docs/Questa_CDC_RDC_Complete_Reference.md` |
| **DSO.ai Guide** | `/proj/rtg_oss_er_feint2/abinbaba/ROSENHORN_DSO_v2/main/pd/tiles/.claude/CLAUDE.md` |

---


## CRITICAL: Genie CLI Working Directory

**ALWAYS run `genie_cli.py` from the user-specific directory (`users/$USER`), NEVER from the project root.**

```bash
# CORRECT ✓
cd $PWD/users/$USER
python3 script/genie_cli.py -i "..." --execute

# WRONG ✗ — never run from project root
cd $PWD
python3 script/genie_cli.py -i "..." --execute
```

This ensures task data, logs, and run scripts go to the correct user-isolated directories (`users/$USER/data/`, `users/$USER/runs/`) and the correct `assignment.csv` is used.

---

## Quick Start - Slash Commands

### UVM Generation
`/uvm-driver`, `/uvm-monitor`, `/uvm-scoreboard`, `/uvm-agent`, `/uvm-env`, `/uvm-sequence`, `/uvm-test`

### RTL Analysis
`/rtl-analyze`, `/rtl-debug`, `/waveform-debug`

### Simulation
`/compile`, `/simulate`, `/coverage`, `/regress`

### Code Review
`/review-sv`, `/review-uvm`

### PD Agent
`/agent` - Send instructions to Genie CLI

---

## Genie CLI - Complete Reference

### CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--instruction` | `-i` | The instruction to parse and execute |
| `--execute` | `-e` | Actually execute (default is dry-run) |
| `--xterm` | `-x` | Run in xterm popup window |
| `--email` | `-m` | Send results to debuggers from assignment.csv |
| `--to EMAIL` | | Override email recipients — **MUST be used together with `--email`**, e.g. `--email --to Azman.BinBabah@amd.com` |
| `--analyze` | `-a` | Claude monitors and analyzes results (static checks) |
| `--analyze-only TAG` | | Skip running check — analyze existing results for TAG directly |
| `--analyze-fixer` | | Analyze + auto-apply constraint fixes + rerun loop until clean (max 10 rounds) |
| `--analyze-fixer-only TAG` | | Skip running check — run analyze-fixer on existing results for TAG directly |
| `--list` | `-l` | List all available instructions |
| `--status TAG` | `-s` | Check task status by tag |
| `--tasks` | `-t` | List tasks: `running`, `today`, `yesterday`, `YYYY-MM-DD` |
| `--kill TAG` | `-k` | Kill a running task by tag |
| `--setup-user` | | Setup user directory for multi-user |

### Execution Modes

| Mode | Command | Behavior |
|------|---------|----------|
| **Dry Run** | No `--execute` | Shows command without running |
| **Background** | `--execute` | Runs detached, logs to `runs/<tag>.log` |
| **Xterm** | `--execute --xterm` | Opens popup window with live output |
| **Analyze** | `--execute --analyze` | Claude monitors + analyzes results |
| **Analyze-Only** | `--analyze-only <tag>` | Skip run — analyze existing results directly |
| **Analyze-Fixer** | `--execute --analyze-fixer` | Analyze → auto-fix → rerun loop until violations = 0 |
| **Analyze-Fixer-Only** | `--analyze-fixer-only <tag>` | Skip run — analyze-fixer on existing results directly |

### Examples

```bash
# Static checks
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --email
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
python3 script/genie_cli.py -i "run full_static_check for umc9_3" --execute --xterm --email

# With analysis
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --analyze --email

# With analyze-fixer (analyze + auto-apply fixes + rerun loop)
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --analyze-fixer --email
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --analyze-fixer --email

# Analyze-fixer on existing results (skip re-running the check)
python3 script/genie_cli.py --analyze-fixer-only <tag>
python3 script/genie_cli.py -i "fix cdc_rdc at /proj/xxx for umc9_3" --execute
python3 script/genie_cli.py -i "analyze and fix lint at /proj/xxx for umc9_3" --execute

# Analyze existing results (skip re-running the check)
python3 script/genie_cli.py --analyze-only <tag>
python3 script/genie_cli.py -i "analyze cdc_rdc results at /proj/xxx/tree_dir for umc17_0" --execute

# TileBuilder
python3 script/genie_cli.py -i "report timing and area for /proj/xxx/tile_dir" --execute --email
python3 script/genie_cli.py -i "monitor supra run at /proj/xxx for target FxSynthesize" --execute --xterm

# Supra regression with params
python3 script/genie_cli.py -i "run supra regression for umcdat target FxSynthesize at /proj/xxx/tiles with params <: NICKNAME = my_run
SYN_VF_FILE = /proj/xxx/umc_top.vf :>" --execute --xterm --email

# Task management
python3 script/genie_cli.py --status <tag>
python3 script/genie_cli.py --kill <tag>
python3 script/genie_cli.py --tasks running
```

### How Instruction Matching Works

1. **One-Hot Encoding**: User input tokenized, matched against `keyword.csv` (252 keywords)
2. **Instruction Match**: Encoded input compared to `instruction.csv` patterns (>50% coverage)
3. **Argument Extraction**: Paths, IPs, params extracted via `arguement.csv` + `patterns.csv`
4. **Script Execution**: Matched script executed with substituted variables

**Example:**
```
Input: "run lint at /proj/xxx for umc9_3"
Keywords: "run", "lint" → matched to "could you run lint"
Script: static_check_unified.csh $refDir $ip $checkType
```

### Configuration Files

| File | Purpose | Example |
|------|---------|---------|
| `keyword.csv` | 252 keywords + synonyms for one-hot encoding | `run,execute,start,kick off` |
| `instruction.csv` | 74 patterns mapped to scripts | `could you run cdc_rdc` → `static_check_unified.csh` |
| `arguement.csv` | Argument types: `ip`, `tile`, `target`, `checkType`, `params` | `umc9_3,ip` |
| `patterns.csv` | Regex patterns for waivers, constraints, dates | `^(cdc\|resetcheck)\s+report.*,waiver,I` |
| `assignment.csv` | User config: `debugger`, `disk`, `project` | `debugger,user@amd.com` |

### Pattern Detection

| Pattern | Detection | Example |
|---------|-----------|---------|
| `refDir` | `os.path.isdir()` check | `/proj/xxx/tree_dir` |
| `ip` | From arguement.csv | `umc9_3`, `umc17_0`, `oss8_0` |
| `tile` | From arguement.csv | `umcdat`, `umccmd`, `osssys` |
| `target` | From arguement.csv | `FxSynthesize`, `FxPlace`, `FxRoute` |
| `checkType` | From arguement.csv | `cdc_rdc`, `lint`, `spg_dft`, `full_static_check` |
| `params` | `PARAM = VALUE` format | `NICKNAME = test_run` |
| `integer` | `^[0-9]+$` | `12345678` (changelist) |
| `tune` | Starts with `tune/` | `tune/FxPlace/opt.tcl` |
| `p4File` | Starts with `//depot/` | `//depot/umc_ip/branches/UMC_BRANCH` |
| `waiver` | Regex from patterns.csv | `cdc report crossing -id xxx` |
| `constraint` | Regex from patterns.csv | `netlist clock clk_main` |

### Params Block Format

Use `<: ... :>` for multiple parameters:
```
with params <: PARAM1 = value1
PARAM2 = value2 :>
```

### Task Output Files

| File | Purpose |
|------|---------|
| `runs/<tag>.log` | Execution log |
| `runs/<tag>.csh` | Generated run script |
| `data/<tag>_spec` | Task output/results |
| `data/<tag>_pid` | Process ID |
| `data/<tag>_email` | Email flag |
| `data/<tag>_analyze` | Analyze mode metadata |
| `data/<tag>_analysis.html` | Analysis report |
| `data/<tag>_fixer_state` | Fixer round state (round, ref_dir, ip, check_type) |
| `data/<tag>_fix_applied_cdc.json` | Applied constraint fixes per round (CDC/RDC) |
| `data/<tag>_fix_applied_lint.json` | Applied RTL fixes per round (Lint) |
| `data/<tag>_fix_applied_spgdft.json` | Applied constraint fixes per round (SPG_DFT) |
| `data/<tag>_analysis_fixer.html` | Per-round report (violations + fixes applied) |
| `data/<tag>_fixer_summary.html` | Final summary across all rounds |

### Email Notification

- Sent on task completion (success or failure)
- Recipients from `assignment.csv` (debugger field)
- Subject format: `[Status] TASK_TYPE - tile @ directory (tag)`
- Status: `[Success]`, `[Failed]`, `[Completed]`

### Multi-User Setup (--setup-user)

For new users to use genie_agent (multi-user environment):

```bash
cd <path_to_genie_agent>

# Option 1: Provide email and disk on command line
python3 script/genie_cli.py --setup-user --user-email Your.Name@amd.com --user-disk /proj/<your_disk>/username

# Option 2: Interactive prompts
python3 script/genie_cli.py --setup-user
```

**Creates user directory structure:**
```
users/$USER/
├── assignment.csv       # User settings (email, disk pre-configured)
├── data/               # Task data
├── runs/               # Run logs
├── params_centre/      # Params repository
├── log_centre/         # Centralized logs
├── tune_centre/        # Tune files
├── script -> ...       # Symlink to shared scripts
├── csh -> ...          # Symlink to shared csh
└── *.csv -> ...        # Symlinks to shared CSVs
```

**Then run from user directory:**
```bash
cd users/$USER
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
```

---

## Available Commands

| Category | Commands |
|----------|----------|
| **Static Checks** | `run cdc_rdc`, `run lint`, `run spg_dft`, `run full_static_check`, `summarize static check` |
| **Analyze** | `analyze cdc_rdc at <dir>`, `analyze lint at <dir>`, `analyze spg_dft at <dir>`, `analyze full_static_check at <dir>` |
| **TileBuilder** | `branch from`, `run supra regression`, `monitor supra run`, `report timing and area`, `report formality` |
| **Params/Tune** | `update params`, `add command to <tune>` |
| **Waivers** | `add cdc_rdc waiver`, `add lint waiver`, `update cdc_rdc constraint` |
| **P4** | `sync up new tree`, `check changelist number`, `submit files` |
| **RTL** | `analyze clock reset structure` |

---

## Key Paths

| Item | Path |
|------|------|
| CLI Script | `script/genie_cli.py` |
| Static Check Scripts | `script/rtg_oss_feint/umc/`, `oss/`, `gmc/` |
| TileBuilder Scripts | `script/rtg_oss_feint/supra/` |
| Analyze Agents | `config/analyze_agents/` |
| Task Logs | `runs/<tag>.log` |
| Task Output | `data/<tag>_spec` |

---

## Project IPs

| Project | IPs | Codeline |
|---------|-----|----------|
| UMC | `umc9_2`, `umc9_3`, `umc17_0` | `umc_ip` |
| OSS | `oss7_2`, `oss8_0` | `oss_ip` |
| GMC | `gmc13_1a` | `umc4` |

---

## Analyze Mode (--analyze)

When `ANALYZE_MODE_ENABLED` is detected in output:

1. **Spawn ONE orchestrator agent** (general-purpose) — do NOT do the analysis yourself:
   ```
   Agent(
     description="Analyze <check_type> for <ip>",
     subagent_type="general-purpose",
     prompt="""
     You are the analyze orchestrator. Read config/analyze_agents/ORCHESTRATOR.md and
     execute the full analyze flow for the following inputs:

     TAG=<tag>
     CHECK_TYPE=<check_type>
     REF_DIR=<ref_dir>
     IP=<ip>
     LOG_FILE=<log_file>
     SPEC_FILE=<spec_file>
     BASE_DIR=<parent of the 'runs/' folder in LOG_FILE>
     SKIP_MONITORING=<true if present in signal, otherwise false>
     """
   )
   ```
2. When the agent completes, say only: `"Analysis complete. Email sent."`

**Signal format:**
```
ANALYZE_MODE_ENABLED
TAG=<tag>
CHECK_TYPE=<check_type>
REF_DIR=<ref_dir>
IP=<ip>
LOG_FILE=<log_file>
SPEC_FILE=<spec_file>
SKIP_MONITORING=true   ← only present when using --analyze-only or analyze instructions
```

---

## Analyze-Fixer Mode (--analyze-fixer)

When `ANALYZE_FIXER_MODE_ENABLED` is detected in output:

1. **Spawn ONE orchestrator agent** (general-purpose) — do NOT do the analysis or fixing yourself:
   ```
   Agent(
     description="Analyze-fixer <check_type> for <ip> round <N>",
     subagent_type="general-purpose",
     prompt="""
     You are the analyze-fixer orchestrator. Read config/analyze_agents/ORCHESTRATOR.md and
     execute the full analyze-fixer flow for the following inputs:

     TAG=<tag>
     CHECK_TYPE=<check_type>
     REF_DIR=<ref_dir>
     IP=<ip>
     LOG_FILE=<log_file>
     SPEC_FILE=<spec_file>
     BASE_DIR=<parent of the 'runs/' folder in LOG_FILE>
     MAX_ROUNDS=<max_rounds>
     FIXER_ROUND=<N>
     SKIP_MONITORING=<true if present in signal, otherwise false>
     """
   )
   ```
2. When the agent completes, say only: `"Analyze-fixer complete. <result>. Email sent."`

**Signal format:**
```
ANALYZE_FIXER_MODE_ENABLED
TAG=<tag>
CHECK_TYPE=<check_type>
REF_DIR=<ref_dir>
IP=<ip>
LOG_FILE=<log_file>
SPEC_FILE=<spec_file>
MAX_ROUNDS=5
FIXER_ROUND=<N>
```

---

## ECO Phase Spawning Pattern

Used for both STUDY (Phase A), APPLY (Phase B), and ROUND (Phase C):

```python
task_id = Agent(description=..., prompt=..., run_in_background=True)
# Sentinel: <BASE_DIR>/data/<TAG>_<phase>_phase_exited.marker
# The agent itself owns ALL polling — internally, as part of its own task.
# It spawns its sub-agents (rtl_diff_analyzer, eco_fenets_runner, etc.),
# polls THEIR sentinels/long-running waits inside its own Bash calls,
# and writes the phase-exit sentinel as its final action before STOP.
#
# The PARENT Claude session does NOT run Bash(sleep N) polling tasks.
# Background agents auto-notify on completion. Wait for the notification,
# then verify the sentinel + handoff JSON exist.
```

**Hard rule:** Never run `Bash(sleep N && ls <sentinel>)` from the parent session. That blocks the foreground and prevents the user from interacting. Polling belongs INSIDE the spawned agent (in its own Bash calls, which run within the agent's isolated context). Parent reads the sentinel only after the auto-notification arrives.

---

## ECO Analyze Mode (Phase A — STUDY)

**Trigger:** `"analyze eco at <refdir> for <tile>"` or `"run eco analysis at <refdir> for <tile>"`
**Signal:** `ECO_ANALYZE_MODE_ENABLED` block (with `TAG REF_DIR TILE JIRA LOG_FILE SPEC_FILE`)

When detected:

1. Spawn STUDY agent (background):
   ```
   task_id = Agent(
     description="ECO STUDY (Steps 1-3 ONLY) <tile>",
     subagent_type="general-purpose",
     run_in_background=True,
     prompt="""
     PHASE A — ECO STUDY (Steps 1-3 ONLY).

     READ: config/eco_agents/CRITICAL_RULES.md, then config/eco_agents/STUDY_ORCHESTRATOR.md.
     EXECUTE: Steps 1, 2, 3 only.
     SCOPE: rtl_diff_analyzer.md, eco_fenets_runner.md, eco_netlist_studier.md,
            eco_validate_step{1,2,3}.py, eco_pick_sibling.py, eco_fenets_*.py.
            Do NOT read any APPLY-phase or next-phase file.

     EXIT — final actions in order:
       1. Write <BASE_DIR>/data/<TAG>_phase_a_handoff.json
       2. Emit APPLY_PHASE_READY block to SPEC_FILE
       3. Write <BASE_DIR>/data/<TAG>_study_phase_exited.marker (one-line: exited <ISO_TIMESTAMP>)
       4. One-line summary. STOP.

     INPUTS:
     TAG=<tag>  REF_DIR=<ref_dir>  TILE=<tile>  JIRA=<jira>
     LOG_FILE=<log_file>  SPEC_FILE=<spec_file>
     BASE_DIR=<parent of LOG_FILE's runs/ folder>
     AI_ECO_FLOW_DIR=<REF_DIR>/AI_ECO_FLOW_<TAG>
     """
   )
   ```

2. **Wait for the agent's auto-notification** — do NOT issue `Bash(sleep N)` polling tasks. The agent does its own internal polling (Steps 1-3 sub-spawns + fenets long-wait) within its own context.

3. When notification arrives, verify sentinel `<BASE_DIR>/data/<TAG>_study_phase_exited.marker` + `<TAG>_phase_a_handoff.json` exist. If either missing → STOP.

4. Say only: `"STUDY phase complete. Waiting for APPLY_PHASE_READY signal."`

---

## ECO Apply Mode (Phase B — APPLY)

**Trigger:** `APPLY_PHASE_READY` block in output AND `<BASE_DIR>/data/<TAG>_study_phase_exited.marker` exists.

**MANDATORY SPAWN-LEVEL GATE — BEFORE spawning APPLY:** check `<BASE_DIR>/data/<TAG>_eco_validate_step3.json`. This canonical file is written ONLY when Step 3 passes (it is REMOVED on failure), so **its ABSENCE means Step 3 did not pass**. If the file is **absent OR** `passed != true`, REFUSE to spawn APPLY regardless of what the STUDY orchestrator wrote in `phase_a_status` — say `"STUDY did not pass Step 3 validator. Refusing to spawn APPLY. Re-spawn STUDY to fix issues (failing issues are in the newest data/<TAG>_eco_validate_step3_iter*.json)."` and STOP. Test existence FIRST — do NOT bare-`open()` the file (it may not exist). The agent CANNOT override this gate; the validator JSON is the single source of truth.

**MANDATORY SPAWN-LEVEL GATE #2 — functional precheck (structural gate #1 is NOT enough):** ALSO check `<BASE_DIR>/data/<TAG>_eco_functional_precheck.json`. The step-3 validator only proves the study is *structurally* complete; this second file proves the emitted logic *computes the intended function* (independent netlist-sim oracle). It is written on every precheck run with a `passed` bool (`passed=false` on any functional mismatch). If the file is **absent OR** `passed != true`, REFUSE to spawn APPLY — say `"STUDY passed structural validation but the functional precheck did NOT pass (or was not run). Refusing to spawn APPLY. Re-spawn STUDY to fix the wrong study entry (failing changes are in data/<TAG>_eco_functional_precheck.json results[] with status FAIL)."` and STOP. Test existence FIRST — do NOT bare-`open()`. Both gates must pass to spawn APPLY; the agent CANNOT override either.

1. Spawn APPLY agent (background):
   ```
   task_id = Agent(
     description="ECO APPLY (Steps 4-6 ONLY) <tile>",
     subagent_type="general-purpose",
     run_in_background=True,
     prompt="""
     PHASE B — ECO APPLY (Steps 4-6 ONLY).

     READ: config/eco_agents/CRITICAL_RULES.md, then config/eco_agents/APPLY_ORCHESTRATOR.md.
     EXECUTE: Steps 4, 5, 6 only (Step 6 ABORT → inline abort_recovery_agent loop).
     PRE-FLIGHT: verify HANDOFF_PATH + all Phase-A artifacts exist on disk.
     SCOPE: eco_applier.md, eco_pre_fm_checker.md, eco_fm_runner.md,
            abort_recovery_agent.md, eco_fm_abort_patterns.yaml,
            eco_perl_spec.py, eco_passes_2_4.py, eco_pre_fm_check.py,
            eco_validate_step4.py, eco_fm_status_collector.py,
            eco_extract_fm_abort_cause.py.
            Do NOT re-read STUDY-phase files; consume Phase-A JSON artifacts.

     EXIT — final actions in order:
       1. Write <BASE_DIR>/data/<TAG>_round_handoff.json with `next_phase: ROUND|FINAL|STOP`
       2. If next_phase=ROUND  → emit ROUND_PHASE_READY block to SPEC_FILE
          If next_phase=FINAL  → spawn FINAL_ORCHESTRATOR directly (foreground; short task)
          If next_phase=STOP   → no spawn (max rounds / unrecoverable error)
       3. Write <BASE_DIR>/data/<TAG>_apply_phase_exited.marker (one-line: exited <ISO_TIMESTAMP>)
       4. One-line summary. STOP.

     INPUTS:
     TAG=<tag>  REF_DIR=<ref_dir>  TILE=<tile>  JIRA=<jira>
     LOG_FILE=<log_file>  SPEC_FILE=<spec_file>
     BASE_DIR=<base_dir>  AI_ECO_FLOW_DIR=<ai_eco_flow_dir>
     HANDOFF_PATH=<base_dir>/data/<tag>_phase_a_handoff.json
     """
   )
   ```

2. **Wait for the agent's auto-notification** — do NOT issue `Bash(sleep N)` polling tasks. The APPLY agent owns its own internal polling for Step 4 applier, Step 5 pre-FM, Step 6 FM long-wait + ABORT recovery loop.

3. When notification arrives, verify sentinel `<BASE_DIR>/data/<TAG>_apply_phase_exited.marker` + `<TAG>_round_handoff.json` exist. Read `next_phase` field.

4. Branch on next_phase:
   - `ROUND` → proceed to ECO Round Mode (below)
   - `FINAL` → say `"ECO analysis complete. Email sent."` (APPLY already spawned FINAL inline)
   - `STOP`  → say `"ECO analysis stopped: <reason from handoff>"`

**APPLY_PHASE_READY signal format** (emitted by STUDY into SPEC_FILE):
```
APPLY_PHASE_READY
TAG=<tag>  REF_DIR=<ref_dir>  TILE=<tile>  JIRA=<jira>
BASE_DIR=<base_dir>  AI_ECO_FLOW_DIR=<ai_eco_flow_dir>
LOG_FILE=<log_file>  SPEC_FILE=<spec_file>
HANDOFF_PATH=<base_dir>/data/<tag>_phase_a_handoff.json
```

---

## ECO Round Mode (Phase C — ROUND_ORCHESTRATOR per round)

**Trigger:** `ROUND_PHASE_READY` block in output AND prior phase's exit sentinel exists (APPLY's for round 2, ROUND_<N-1>'s for round N+1).

Loop while next_phase = ROUND, max 10 rounds total:

1. Spawn ROUND agent (background):
   ```
   task_id = Agent(
     description="ECO ROUND <N> (single round) <tile>",
     subagent_type="general-purpose",
     run_in_background=True,
     prompt="""
     PHASE C — ECO ROUND <N> (one round only — analyzer + re-study + re-apply + re-FM).

     READ: config/eco_agents/CRITICAL_RULES.md, then config/eco_agents/ROUND_ORCHESTRATOR.md.
     EXECUTE: one round's analyzer pipeline (Step 6d) + re_studier + applier + Step 5 + Step 6.
     SCOPE: ROUND_ORCHESTRATOR.md, eco_fm_analyzer.md, eco_re_studier_evidence_contract.md,
            eco_netlist_re_studier.md, eco_netlist_verifier.md, eco_applier.md,
            eco_pre_fm_checker.md, eco_fm_runner.md, abort_recovery_agent.md,
            eco_fm_abort_patterns.yaml, plus their script counterparts.
            Do NOT re-read STUDY-phase files. Do NOT spawn ROUND_<N+1> yourself —
            emit ROUND_PHASE_READY signal and exit; main session spawns next round.

     EXIT — final actions in order:
       1. Update <BASE_DIR>/data/<TAG>_round_handoff.json with `next_phase: ROUND|FINAL|STOP`
       2. If next_phase=ROUND   → emit ROUND_PHASE_READY block to SPEC_FILE
          If next_phase=FINAL   → spawn FINAL_ORCHESTRATOR directly (foreground)
          If next_phase=STOP    → no spawn
       3. Write <BASE_DIR>/data/<TAG>_round<N>_phase_exited.marker (one-line: exited <ISO_TIMESTAMP>)
       4. One-line summary. STOP.

     INPUTS:
     TAG=<tag>  REF_DIR=<ref_dir>  TILE=<tile>  JIRA=<jira>
     LOG_FILE=<log_file>  SPEC_FILE=<spec_file>
     BASE_DIR=<base_dir>  AI_ECO_FLOW_DIR=<ai_eco_flow_dir>
     ROUND=<N>  HANDOFF_PATH=<base_dir>/data/<tag>_round_handoff.json
     """
   )
   ```

2. **Wait for the agent's auto-notification** — do NOT issue `Bash(sleep N)` polling tasks. The ROUND agent owns its own internal polling for Step 6d analyzer, re_studier, applier, Step 5, Step 6 FM.

3. When notification arrives, verify sentinel `<BASE_DIR>/data/<TAG>_round<N>_phase_exited.marker` + `<TAG>_round_handoff.json` exist. Read `next_phase`.

4. Branch:
   - `ROUND` AND round_count < 5 → loop, spawn ROUND_<N+1>
   - `ROUND` AND round_count >= 10 → say `"ECO max rounds (10) hit without convergence."`, STOP
   - `FINAL` → say `"ECO analysis complete. Email sent."` (ROUND already spawned FINAL inline)
   - `STOP`  → say `"ECO analysis stopped: <reason>"`

**ROUND_PHASE_READY signal format** (emitted by APPLY or prior ROUND):
```
ROUND_PHASE_READY
TAG=<tag>  REF_DIR=<ref_dir>  TILE=<tile>  JIRA=<jira>
BASE_DIR=<base_dir>  AI_ECO_FLOW_DIR=<ai_eco_flow_dir>
LOG_FILE=<log_file>  SPEC_FILE=<spec_file>
ROUND=<next_round_number>
HANDOFF_PATH=<base_dir>/data/<tag>_round_handoff.json
```

---

**Version:** 2.4 | **Last Updated:** 2026-04-01

**Note:** Detailed documentation loads on-demand from `.claude/rules/` when working with relevant files.
