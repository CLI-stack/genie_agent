# UVM RTL Testbench - Claude Code Instructions

This directory provides Claude Code with skills, agents, and templates for UVM-based RTL verification work.

---

## External References

| Topic | Reference File |
|-------|----------------|
| **CDC/RDC Issues** | `/home/abinbaba/Questa_CDC_RDC_Complete_Reference.md` |
| **DSO.ai Guide** | `/proj/rtg_oss_er_feint2/abinbaba/ROSENHORN_DSO_v2/main/pd/tiles/.claude/CLAUDE.md` |

---

## Source Control

This directory is the **main repository** for genie_agent, hosted on GitHub:
```
https://github.com/azmangenji/genie_agent
```

Any changes made here should be committed and pushed to git:
```bash
git add <file>
git commit -m "your message"
git push
```

**Git Identity (one-time setup):**
```bash
git config --global user.name "Azman Bin Babah"
git config --global user.email "abinbaba@amd.com"
```

Others can clone the latest version with:
```bash
git clone https://github.com/azmangenji/genie_agent.git
```

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
| `--to EMAIL` | | Override email recipients |
| `--analyze` | `-a` | Claude monitors and analyzes results (static checks) |
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

### Examples

```bash
# Static checks
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --email
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
python3 script/genie_cli.py -i "run full_static_check for umc9_3" --execute --xterm --email

# With analysis
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --analyze --email

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
| `instruction.csv` | 60 patterns mapped to scripts | `could you run cdc_rdc` → `static_check_unified.csh` |
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

### Email Notification

- Sent on task completion (success or failure)
- Recipients from `assignment.csv` (debugger field)
- Subject format: `[Status] TASK_TYPE - tile @ directory (tag)`
- Status: `[Success]`, `[Failed]`, `[Completed]`

### Multi-User Setup (--setup-user)

For new users to use genie_agent (multi-user environment):

```bash
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent

# Option 1: Provide email and disk on command line
python3 script/genie_cli.py --setup-user --user-email Your.Name@amd.com --user-disk /proj/rtg_oss_er_feint1/username

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
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent/users/$USER
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
```

---

## Available Commands

| Category | Commands |
|----------|----------|
| **Static Checks** | `run cdc_rdc`, `run lint`, `run spg_dft`, `run full_static_check`, `summarize static check` |
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

When `ANALYZE_MODE_ENABLED` is detected in output, you MUST:

1. **Read the orchestrator**: `config/analyze_agents/ORCHESTRATOR.md`
2. **Spawn background monitor** to watch for task completion
3. **On completion**: Spawn analysis agents per check_type
4. **Compile HTML report**: Write to `data/<tag>_analysis.html`
5. **Send email** with full analysis
6. **Say only**: "Analysis complete. Email sent."

**Signal format:**
```
ANALYZE_MODE_ENABLED
TAG: <tag>
CHECK_TYPE: <check_type>
REF_DIR: <ref_dir>
IP: <ip>
LOG_FILE: <log_file>
SPEC_FILE: <spec_file>
```

**Agent Teams** (in `config/analyze_agents/`):
- CDC/RDC: precondition, violation_extractor, rtl_analyzer
- Lint: violation_extractor, rtl_analyzer
- SpgDFT: precondition, violation_extractor, rtl_analyzer
- Shared: library_finder, report_compiler

---

**Version:** 2.2 | **Last Updated:** 2026-03-18

**Note:** Detailed documentation loads on-demand from `.claude/rules/` when working with relevant files.
