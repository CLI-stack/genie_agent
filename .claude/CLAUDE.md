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
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent/users/$USER
python3 script/genie_cli.py -i "..." --execute

# WRONG ✗ — never run from project root
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent
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
| `--analyze-fixer` | | Analyze + auto-apply constraint fixes + rerun loop until clean (max 5 rounds) |
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

**Version:** 2.4 | **Last Updated:** 2026-04-01

**Note:** Detailed documentation loads on-demand from `.claude/rules/` when working with relevant files.
