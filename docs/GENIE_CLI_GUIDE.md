# Genie CLI Guide

Direct interface from Claude Code to the Rosenhorn Agent Flow.

---

## Overview

The Genie CLI allows you to send instructions directly to the PD Agent without going through email. It uses the same keyword/instruction mapping as the email system but executes tasks immediately.

```
Email Flow (unchanged):
  Email → tasksMail.csv → vtoHybridModel.py → tasksModel.csv → vtoExecution.csh

CLI Flow (new, separate):
  You → Claude Code → genie_cli.py → direct script execution
```

The two flows are completely independent and don't interfere with each other.

---

## Quick Start

### Method 1: Ask Claude Code Naturally

Just tell Claude what you want:

```
"Run CDC/RDC check at /proj/xxx/tree_dir"
"Monitor supra run at /proj/xxx/tile_dir for target FxSynthesize"
"Summarize static check run at /proj/xxx/tree_dir"
```

Claude will use the CLI to execute your request.

### Method 2: Use the /agent Skill

```
/agent run cdc_rdc at /proj/xxx/tree_dir
/agent monitor supra run at /proj/xxx/tile_dir
/agent --list
```

### Method 3: Run CLI Directly

```bash
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent

# List available commands
python3 script/genie_cli.py --list

# Dry run (see what will execute)
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx"

# Actually execute
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx" --execute

# Execute with email notification
python3 script/genie_cli.py -i "summarize static check at /proj/xxx" --execute --email

# Check task status
python3 script/genie_cli.py --status <tag>

# Kill a running task
python3 script/genie_cli.py --kill <tag>
```

### Method 4: Run with Email Notification

Ask Claude and request email:
```
"Summarize static check at /proj/xxx and send email"
```

Or use CLI directly:
```bash
python3 script/genie_cli.py -i "summarize static check at /proj/xxx" --execute --email
```

**Note:** Email is sent to debuggers configured in `assignment.csv`

---

## CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--instruction` | `-i` | The instruction to parse and execute |
| `--execute` | `-e` | Actually execute the command (default is dry-run) |
| `--email` | `-m` | Send results to debugger emails from assignment.csv |
| `--list` | `-l` | List all available instructions |
| `--status` | `-s` | Check status of a task by tag |
| `--kill` | `-k` | Kill a running background task by tag |

---

## Available Commands

### Static Checks

| Command | Description |
|---------|-------------|
| `run cdc_rdc at <dir>` | Run CDC/RDC check |
| `run lint at <dir>` | Run lint check |
| `run spg_dft at <dir>` | Run Spyglass DFT check |
| `run build_rtl at <dir>` | Build RTL |
| `run full_static_check at <dir>` | Run all static checks |
| `summarize static check run at <dir>` | Get summary of results |

**Examples:**
```
run cdc_rdc at /proj/rtg_oss_er_feint1/abinbaba/umc_rosenhorn_Feb9
summarize static check run for umc9_3 at /proj/xxx/tree_dir
```

---

### TileBuilder / Supra Operations

| Command | Description |
|---------|-------------|
| `monitor supra run at <dir> for target <target>` | Monitor TileBuilder job |
| `start supra regression <tile> at <dir>` | Start supra regression |
| `run supra regression for target <target> at <dir>` | Run specific target |
| `branch from <dir>` | Create TileBuilder branch |
| `rerun <target> at <dir>` | Rerun a target |
| `stop run at <dir>` | Stop running job |
| `report timing and area for <dir>` | Extract timing/area metrics |
| `report utilization at <dir>` | Extract utilization |
| `list tilebuilder directories at <dir>` | List TB directories |
| `remove TB dir <dir>` | Delete TileBuilder directory |
| `update status for supra run at <dir>` | Check supra status |

**Examples:**
```
monitor supra run at /proj/xxx/umccmd_Jan26 for target FxSynthesize
report timing and area for /proj/xxx/tile_dir
list tilebuilder directories at /proj/xxx
```

---

### Params & Tune Updates

| Command | Description |
|---------|-------------|
| `update params at <dir>` | Update TileBuilder params |
| `add params at <dir>` | Add TileBuilder params |
| `update params to params center` | Push params to center |
| `update params from params center` | Pull params from center |
| `update tune to tune center` | Push tune to center |
| `update tune from tune center` | Pull tune from center |
| `add command to <tune>` | Add command to tune file |

---

### CDC/RDC/Lint/SPG_DFT Waivers & Updates

| Command | Description |
|---------|-------------|
| `add cdc_rdc waiver at <dir>` | Add CDC/RDC waiver |
| `add cdc_rdc constraint at <dir>` | Add CDC/RDC constraint |
| `add cdc_rdc config at <dir>` | Add CDC/RDC config |
| `update cdc_rdc waiver at <dir>` | Update CDC/RDC waiver |
| `update cdc_rdc config at <dir>` | Update CDC/RDC config |
| `update cdc_rdc version at <dir>` | Update CDC/RDC version |
| `add lint waiver at <dir>` | Add lint waiver |
| `update lint waiver at <dir>` | Update lint waiver |
| `update spg_dft parameters at <dir>` | Update Spyglass DFT parameters |
| `add spyglass dft parameters at <dir>` | Add Spyglass DFT parameters |

---

### P4 Operations

| Command | Description |
|---------|-------------|
| `sync up new tree at <dir>` | Sync P4 tree |
| `check changelist number for <dir>` | Check P4 CL |
| `submit files at <dir>` | Submit P4 files |

---

## Command Syntax

The CLI uses flexible matching - you don't need exact phrasing. These all work:

```
# These are equivalent:
run cdc_rdc at /proj/xxx
could you run cdc_rdc at /proj/xxx
run cdc_rdc check at /proj/xxx

# These are equivalent:
monitor supra run at /proj/xxx for target FxSynthesize
monitor supra at /proj/xxx target FxSynthesize
```

### Arguments

| Argument | How to Specify |
|----------|----------------|
| Directory | Full path: `/proj/rtg_oss_er_feint1/xxx` |
| Target | Target name: `FxSynthesize`, `FxPrePlace`, etc. |
| Tile | Tile name: `umccmd`, `umcdat`, etc. |
| IP | IP name: `umc9_3`, `umc14_2`, etc. |

---

## Output & Tracking

When a command executes:

1. **Tag Created:** Unique timestamp ID (e.g., `20260209221119`)
2. **Data Directory:** `data/<tag>/` - stores results
3. **Spec File:** `data/<tag>_spec` - summary output
4. **Run Script:** `runs/<tag>.csh` - the executed script
5. **Log File:** `runs/<tag>.log` - execution log
6. **PID File:** `data/<tag>_pid` - process ID for background tasks
7. **Email Flag:** `data/<tag>_email` - triggers email on completion

### Check Results

```bash
# View spec file (summary)
cat data/20260209221119_spec

# View run log
cat runs/20260209221119.log

# Check PID of running task
cat data/20260209221119_pid
```

---

## Task Management

### Check Task Status

```bash
python3 script/genie_cli.py --status <tag>
```

### Kill a Running Task

```bash
python3 script/genie_cli.py --kill <tag>
```

This will:
- Kill the entire process group (including child processes)
- Remove the PID file (`data/<tag>_pid`)
- Remove the email flag file (`data/<tag>_email`)

### PID Tracking

Each background task saves its process ID:
- **PID file location:** `data/<tag>_pid`
- Used by `--kill` to terminate tasks
- Falls back to grep-based process search if PID file missing

---

## Email Notification

When `--email` flag is used:
- Email flag file is created: `data/<tag>_email`
- Email is sent on task completion (success OR failure)
- Email includes task results from `data/<tag>_spec`
- Sent to all debuggers from `assignment.csv` (first as To, rest as CC)

### Immediate Results (email sent right away)
- `summarize static check`
- `report timing and area`
- `report utilization`
- `list tilebuilder directories`
- `check changelist number`

### Long-Running Tasks (email sent when task completes)
- `run cdc_rdc`
- `run lint`
- `run spg_dft`
- `branch from`
- `start supra regression`

---

## Examples

### Example 1: Run CDC/RDC Check

**You say:**
```
Run CDC/RDC at /proj/rtg_oss_er_feint1/abinbaba/umc_rosenhorn_Feb9082038
```

**Claude does:**
1. Parses instruction → matches to `static_check_unified.csh`
2. Extracts directory argument
3. Executes the check
4. Reports results back

---

### Example 2: Monitor TileBuilder Run

**You say:**
```
Monitor supra run at /proj/rtg_oss_er_feint2/abinbaba/ROSENHORN_DSO_v2/main/pd/tiles/umccmd_Jan26162737 for target FxSynthesize
```

**Claude does:**
1. Parses instruction → matches to `monitor_tilebuilder.csh`
2. Extracts directory and target
3. Launches monitor in xterm
4. Reports tag for tracking

---

### Example 3: Get Static Check Summary

**You say:**
```
Summarize static check run for umc9_3 at /proj/rtg_oss_er_feint1/abinbaba/umc_rosenhorn_Feb9082038
```

**Claude returns:**
```
| Check   | Errors | Unfiltered |
|---------|--------|------------|
| Lint    | 0      | -          |
| CDC     | 110    | 16         |
| RDC     | 0      | 0          |
| SpgDFT  | 15     | 3          |
```

---

### Example 4: Report Timing & Area

**You say:**
```
Report timing and area for /proj/xxx/tile_dir
```

**Claude does:**
1. Runs `synthesis_timing.csh`
2. Extracts timing and area metrics
3. Returns formatted table

---

## Troubleshooting

### "Could not match instruction"

The instruction wasn't recognized. Try:
1. Check spelling
2. Use simpler phrasing
3. Run `python3 script/genie_cli.py --list` to see available commands

### Directory not found

Make sure the path:
1. Is a full absolute path starting with `/proj/`
2. Actually exists
3. Has no trailing spaces

### Script errors

Check the log file:
```bash
cat runs/<tag>.log
```

---

## File Locations

| File | Purpose |
|------|---------|
| `script/genie_cli.py` | CLI tool |
| `keyword.csv` | Keyword definitions |
| `instruction.csv` | Instruction → script mapping |
| `arguement.csv` | Argument type definitions |
| `assignment.csv` | Project/tile assignments |
| `data/<tag>_spec` | Task results |
| `data/<tag>_pid` | Process ID for background tasks |
| `data/<tag>_email` | Email notification flag |
| `runs/<tag>.csh` | Generated run scripts |
| `runs/<tag>.log` | Execution logs |

---

## Adding New Commands

To add a new command to the agent:

1. **Add keywords** to `keyword.csv` (if needed)
2. **Add instruction** to `instruction.csv`:
   ```
   could you <action> at following directory,script/path.csh $arg1 $arg2 $tag
   ```
3. CLI will automatically pick up the new instruction

---

## Setup for New Users

To enable the `/agent` skill in your own Claude Code environment, follow these steps:

### Prerequisites

- Access to Claude Code (Genie)
- A project directory with `.claude/` folder

### Step 1: Create the Skills Directory

```bash
# Navigate to your project
cd <your_project_directory>

# Create the agent skill directory
mkdir -p .claude/skills/agent
```

### Step 2: Copy the Skill File

```bash
# Copy the agent skill definition
cp /proj/rtg_oss_er_feint2/abinbaba/genie/.claude/skills/agent/SKILL.md \
   <your_project>/.claude/skills/agent/SKILL.md
```

### Step 3: (Optional) Copy Documentation

```bash
# Create docs directory
mkdir -p <your_project>/docs

# Copy the guides
cp /proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/docs/GENIE_CLI_GUIDE.md \
   <your_project>/docs/

cp /proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/docs/GENIE_QUICK_REFERENCE.md \
   <your_project>/docs/
```

### Complete Setup Script

Run this script to set up everything at once:

```bash
#!/bin/bash
# Setup script for Genie CLI /agent skill

# Set your project directory
PROJECT_DIR="<your_project_directory>"

# Source locations
SKILL_SOURCE="/proj/rtg_oss_er_feint2/abinbaba/genie/.claude/skills/agent"
DOCS_SOURCE="/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/docs"

# Create directories
mkdir -p "$PROJECT_DIR/.claude/skills/agent"
mkdir -p "$PROJECT_DIR/docs"

# Copy skill file
cp "$SKILL_SOURCE/SKILL.md" "$PROJECT_DIR/.claude/skills/agent/"

# Copy documentation
cp "$DOCS_SOURCE/GENIE_CLI_GUIDE.md" "$PROJECT_DIR/docs/"
cp "$DOCS_SOURCE/GENIE_QUICK_REFERENCE.md" "$PROJECT_DIR/docs/"

echo "Setup complete!"
echo "You can now use '/agent' command in Claude Code"
```

### What Gets Copied

| Source | Destination | Purpose |
|--------|-------------|---------|
| `.claude/skills/agent/SKILL.md` | Your `.claude/skills/agent/` | Enables `/agent` command |
| `docs/GENIE_CLI_GUIDE.md` | Your `docs/` | Full user guide |
| `docs/GENIE_QUICK_REFERENCE.md` | Your `docs/` | Quick reference |

### Important Notes

1. **Shared Agent:** All users share the same central agent at:
   ```
   /proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/
   ```
   You don't need to copy the agent itself - just the skill file.

2. **No Agent Copy Needed:** The CLI tool (`genie_cli.py`) and all configuration files (`keyword.csv`, `instruction.csv`, etc.) remain in the central location.

3. **Permissions:** Ensure you have read access to the agent directory.

### Verify Setup

After copying, verify the setup:

```bash
# Check skill file exists
ls -la <your_project>/.claude/skills/agent/SKILL.md

# Test the CLI directly
cd /proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent
python3 script/genie_cli.py --list
```

### Start Using

Once set up, start Claude Code in your project and use:

```
/agent run cdc_rdc at /proj/xxx/tree_dir
```

Or just ask naturally:

```
"Run CDC/RDC check at /proj/xxx/tree_dir"
```

---

## File Locations Summary

### Central Agent (shared by all users)
```
/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/
├── script/genie_cli.py          # CLI tool
├── keyword.csv                   # Keyword definitions
├── instruction.csv               # Instruction mappings
├── arguement.csv                 # Argument definitions
├── assignment.csv                # Project/tile assignments
├── data/                         # Task results
└── runs/                         # Run scripts and logs
```

### Skill File (copy to your project)
```
/proj/rtg_oss_er_feint2/abinbaba/genie/.claude/skills/agent/SKILL.md
```

### Documentation (optional, copy to your project)
```
/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/docs/
├── GENIE_CLI_GUIDE.md            # This guide
└── GENIE_QUICK_REFERENCE.md      # Quick reference
```

---

## Support

For issues or questions:
- Check this guide
- Review the instruction list: `python3 script/genie_cli.py --list`
- Check error logs in `runs/<tag>.log`

---

**Version:** 1.2
**Last Updated:** 2026-02-10

**Change Log:**
- v1.2: Added CLI options table, task management (--status, --kill), PID tracking, email notification details, SPG_DFT commands
- v1.1: Initial version with basic commands
