# Agent Skill (Genie CLI)

Send instructions directly to the Agent Flow **without going through email**.

## Trigger
`/agent`

## Background

The Agent Flow originally works via **EMAIL**:
- User sends email to VTO → `vtoHybridModel.py` processes → executes scripts → replies via email

This skill provides an **alternative bypass** using `genie_cli.py`:
- User invokes `/agent` or asks naturally → Claude calls `genie_cli.py` → executes same scripts → returns results directly

## Description

This skill provides a direct interface to the PD Agent (Agent Flow) for executing tasks like:
- CDC/RDC checks
- Lint checks
- Spyglass DFT checks
- TileBuilder operations (branch, monitor, params update)
- Static check runs
- P4 file submissions
- And more...

**Benefits over Email:**
- Faster execution (no email round-trip)
- Real-time feedback in Claude Code
- Visual monitoring with xterm mode
- Easier debugging and iteration

## Usage

```
/agent <instruction>
```

## Examples

```
/agent run cdc_rdc at /proj/rtg_oss_er_feint2/xxx/tile1
/agent monitor supra run at /proj/rtg_oss_er_feint2/xxx/umccmd_Jan26
/agent run lint at /proj/xxx/ip_dir
/agent report timing and area for /proj/xxx/tile_dir
/agent report utilization at /proj/xxx/tile_dir
/agent report formality for umccmd at /proj/xxx/tile_dir
/agent list tilebuilder directories at /proj/xxx
/agent --list
```

### Supra Regression with Params Example
```
/agent run supra regression for umcdat target FxSynthesize at /proj/xxx/tiles with params <: NICKNAME = umcdat_NO_DSO_23Feb
SYN_VF_FILE = /proj/xxx/umc_top.vf :>
```

### Natural Language (Recommended)

You can also just ask naturally without `/agent`:
```
report timing and area for /proj/xxx/tile_dir, execute in xterm and send email
run full_static_check for umc9_3, execute in xterm and send email to debuggers
run lint at /proj/xxx for umc9_3 and send email when done
```

Claude will parse your request and invoke the appropriate genie_cli command with the right flags.

## How It Works

1. When you invoke `/agent <instruction>`, Claude will:
   - Parse the instruction using the same keyword/instruction mapping as the email system
   - Identify the corresponding script to execute
   - Extract arguments (directories, tiles, targets, etc.) from your instruction
   - Execute the script directly

2. The agent uses these configuration files (same as email flow):
   - `keyword.csv` - Keyword to one-hot encoding
   - `instruction.csv` - Instruction patterns to script mapping
   - `arguement.csv` - Argument type definitions
   - `assignment.csv` - Project/tile assignments

## Agent Location

The agent runs from the current `main_agent` directory (your working directory).

## CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--instruction` | `-i` | The instruction to parse and execute |
| `--execute` | `-e` | Actually execute the command (default is dry-run) |
| `--xterm` | `-x` | Run task in xterm popup window (interactive mode) |
| `--email` | `-m` | Send results to debugger emails from assignment.csv |
| `--to EMAIL` | | Override email recipients (comma-separated). Use with `--email` |
| `--list` | `-l` | List all available instructions |
| `--status` | `-s` | Check status of a task by tag |
| `--tasks` | `-t` | List tasks: `running`, `today`, `yesterday`, or `YYYY-MM-DD` |
| `--kill` | `-k` | Kill a running background task by tag |
| `--setup-user` | | Setup user-specific directory for multi-user environment |
| `--user-email EMAIL` | | Email address for `--setup-user` (required, or will prompt) |
| `--user-disk PATH` | | Disk path for `--setup-user` (required, or will prompt) |
| `--analyze` | `-a` | Claude Code monitors task and analyzes results (static checks only) |
| `--analyze-only TAG` | | Skip running check — analyze existing results for TAG directly |

## Execution Modes

The agent supports three execution modes:

| Mode | Flags | Description |
|------|-------|-------------|
| **Dry Run** | (none) | Shows what would execute without running |
| **Background** | `--execute` | Runs in background, frees terminal |
| **Xterm Popup** | `--execute --xterm` | Opens xterm window for live monitoring |
| **Analyze Mode** | `--execute --analyze` | Claude Code monitors and analyzes results |
| **Analyze-Only** | `--analyze-only <tag>` | Skip run — analyze existing results directly |

**When to use xterm mode:**
- Visual monitoring of long-running tasks
- Debugging when you need to see real-time output
- Tasks where you want to watch progress (timing reports, static checks)

**When to use analyze mode:**
- When you want Claude Code to automatically analyze static check results after completion
- Only works with: `cdc_rdc`, `lint`, `spg_dft`, `full_static_check`
- Claude monitors the task, waits for completion, then analyzes violations with priority-based approach

## Execution Steps

When this skill is invoked:

1. **Parse the instruction argument**
   - If no instruction provided after `/agent`, ask user what they want to do
   - If `--list` is provided, show all available instructions

2. **Run the CLI parser to identify the command (dry-run first)**
   ```bash
   python3 script/genie_cli.py -i "<user_instruction>"
   ```

3. **Review the dry-run output**
   - Show user the matched instruction and script
   - Show extracted arguments
   - Confirm this is what they want

4. **Execute if user confirms (or auto-execute for simple tasks)**
   ```bash
   python3 script/genie_cli.py -i "<user_instruction>" --execute
   ```

5. **Execute with email notification (optional)**
   ```bash
   python3 script/genie_cli.py -i "<user_instruction>" --execute --email
   ```

6. **Execute in xterm popup (for visual monitoring)**
   ```bash
   python3 script/genie_cli.py -i "<user_instruction>" --execute --xterm --email
   ```

7. **For long-running tasks (like monitor, supra regression)**
   - Task runs in background (or in xterm if `--xterm` specified)
   - PID is saved to `data/<tag>_pid`
   - Report the tag to user for tracking

## Available Instructions

Common instructions the agent understands:

### Static Checks
- `run cdc_rdc at <directory>` - Run CDC/RDC check
- `run lint at <directory>` - Run lint check
- `run spg_dft at <directory>` - Run Spyglass DFT
- `run build_rtl at <directory>` - Build RTL
- `run full_static_check at <directory>` - Run all static checks
- `run full_static_check for <ip>` - **Auto-creates workspace** and runs all checks
- `summarize static check run at <directory>` - Summarize static check results

**Supported Projects:**
| Project | IP Examples | Codeline |
|---------|-------------|----------|
| UMC | `umc9_2`, `umc9_3`, `umc17_0` | `umc_ip` |
| OSS | `oss7_2`, `oss8_0` | `oss_ip` |
| GMC | `gmc13_1a` | `umc4` |

**Auto-Workspace Creation:** If no directory is specified for static checks, the agent automatically:
1. Creates a workspace using disk from `assignment.csv`
2. Runs `p4_mkwa` to sync the codebase (codeline based on IP)
3. Executes the requested checks

### TileBuilder Operations
- `branch from <directory>` - Create TileBuilder branch
- `run supra regression for <tile> target <target> at <directory>` - Run supra regression with target
- `run supra regression for <tile> at <directory> with params <: PARAM = value :>` - Run with custom params
- `monitor supra run at <directory>` - Monitor running TileBuilder job
- `update status for supra run at <directory>` - Check supra run status
- `report timing and area for <directory>` - Extract timing/area metrics
- `report utilization at <directory>` - Report utilization metrics
- `report formality at <directory>` - Report Formality (FM) verification results
- `rerun <target> at <directory>` - Rerun specific target
- `stop run at <directory>` - Stop running job
- `list tilebuilder directories at <directory>` - List TB directories
- `remove TB dir at <directory>` - Delete TileBuilder directory

#### Supra Regression with Params

To run supra regression with custom parameters, use the `<: ... :>` block syntax:

```
run supra regression for <tile> target <target> at <tiles_directory> with params <: NICKNAME = my_run_name
SYN_VF_FILE = /path/to/vf/file.vf :>
```

**Common Params:**
| Param | Description |
|-------|-------------|
| `NICKNAME` | Custom run name suffix (e.g., `umcdat_NO_DSO_23Feb`) |
| `SYN_VF_FILE` | RTL verilog filelist path |
| `TILES_TO_RUN` | Override tile to run |
| `DSO_USE` | Enable/disable DSO (`0` or `1`) |

**Example:**
```bash
python3 script/genie_cli.py -i "run supra regression for umcdat target FxSynthesize at /proj/xxx/tiles with params <: NICKNAME = umcdat_test_run
SYN_VF_FILE = /proj/xxx/umc_top.vf :>" --execute --xterm --email
```

**Script Used:** `make_tilebuilder_run.csh`

### Params/Tune Management
- `update params at <directory>` - Update TileBuilder params
- `add params at <directory>` - Add new params
- `update params to params center` - Push params to center
- `update params from params center` - Pull params from center
- `update tune to tune center` - Push tune to center
- `update tune from tune center` - Pull tune from center
- `add command to <tune>` - Add command to tune file

### Waivers/Constraints
- `add cdc_rdc waiver at <directory>` - Add CDC/RDC waiver
- `add cdc_rdc constraint at <directory>` - Add CDC/RDC constraint
- `update cdc_rdc waiver at <directory>` - Update CDC/RDC waiver
- `update cdc_rdc config at <directory>` - Update CDC/RDC config
- `update cdc_rdc version at <directory>` - Update CDC/RDC version
- `add lint waiver at <directory>` - Add lint waiver
- `update lint waiver at <directory>` - Update lint waiver
- `update spg_dft parameters at <directory>` - Update Spyglass DFT parameters
- `add spyglass dft parameters at <directory>` - Add Spyglass DFT parameters

### P4/Version Control
- `sync up new tree at <directory>` - Sync new P4 tree
- `sync up new tree from branch <p4_path>` - Sync with auto-detected branch
- `sync up new tree from branch <p4_path> at changelist <CL>` - Sync specific changelist
- `check changelist number for <directory>` - Check P4 changelist
- `submit files at <directory>` - Submit P4 files

### Analyze Existing Results (No Re-run)
- `analyze cdc_rdc at <directory> for <ip>` - Analyze existing CDC/RDC results
- `analyze cdc_rdc results at <directory> for <ip>` - Same as above
- `analyze lint at <directory> for <ip>` - Analyze existing lint results
- `analyze spg_dft at <directory> for <ip>` - Analyze existing SpgDFT results
- `analyze full_static_check at <directory> for <ip>` - Analyze all existing results

Or use `--analyze-only <tag>` directly when you know the tag:
```bash
python3 script/genie_cli.py --analyze-only 20260330201659
```

### RTL Analysis
- `analyze clock reset structure at <directory>` - Analyze clock/reset hierarchy from RTL

#### Sync Tree with Branch Auto-Detection

The CLI can auto-detect the branch name from a P4 depot path:

```bash
# Auto-detect branch from P4 path
python3 script/genie_cli.py -i "sync up new tree for umc9_3 from branch //depot/umc_ip/branches/UMC_14_2_WHLP_BRANCH" --execute --email

# With specific changelist
python3 script/genie_cli.py -i "sync up new tree for umc9_3 from branch //depot/umc_ip/branches/UMC_14_2_WHLP_BRANCH at changelist 12345678" --execute --email
```

**Detected Arguments:**
- `p4File`: `//depot/umc_ip/branches/UMC_14_2_WHLP_BRANCH`
- `integer`: `12345678` (changelist)
- Branch extracted: `UMC_14_2_WHLP_BRANCH`

**Script Used:** `sync_tree.csh`

## Output

The agent will:
1. Create a unique tag (timestamp-based) for tracking
2. Create a data directory at `data/<tag>/`
3. Create a run script at `runs/<tag>.csh`
4. Save process PID to `data/<tag>_pid` (for background tasks)
5. Execute and report results back

### Retrieving Task Output

**IMPORTANT:** After executing a task, always check the `data/<tag>_spec` file for the actual output/results:

```bash
cat data/<tag>_spec
```

The `_spec` file contains the formatted results from the task execution. The log file (`runs/<tag>.log`) may show script execution details but the actual output is written to `data/<tag>_spec`.

**Example workflow:**
1. Execute task: `python3 script/genie_cli.py -i "check changelist number for /proj/xxx" --execute`
2. Note the tag from output (e.g., `20260210014814`)
3. Wait briefly for execution to complete
4. Read the results: `cat data/20260210014814_spec`

### Task Management

**Check task status:**
```bash
python3 script/genie_cli.py --status <tag>
```

**Kill a running task:**
```bash
python3 script/genie_cli.py --kill <tag>
```

**PID Tracking:**
- Each background task saves its PID to `data/<tag>_pid`
- Kill command uses saved PID to terminate the entire process group
- Automatically removes PID file and email flag file on kill

### Email Notification

When `--email` flag is used:
- Email flag file is created: `data/<tag>_email`
- Email is sent on task completion (success OR failure)
- Email includes task results from `data/<tag>_spec`
- Sent to all debuggers from `assignment.csv` (first as To, rest as CC)

**Immediate results** (email sent right away):
- `summarize static check`
- `report timing and area`
- `report utilization`
- `list tilebuilder directories`
- `check changelist number`

**Long-running tasks** (email sent when task completes):
- `run cdc_rdc`
- `run lint`
- `run spg_dft`
- `branch from`
- `start supra regression`

## Multi-User Setup

The genie_agent directory supports **multiple users** with shared scripts but isolated data. Each user has their own data directories while sharing the same scripts.

### Setting Up Your User Directory

**First-time setup (run once) - email and disk path are required:**
```bash
cd <genie_agent_dir>

# Option 1: Provide email and disk path on command line
python3 script/genie_cli.py --setup-user --user-email Your.Name@amd.com --user-disk /proj/<your_disk>/your_username

# Option 2: Will prompt for email and disk path interactively
python3 script/genie_cli.py --setup-user
```

**Setup prompts:**
1. **Email address** (required): Your AMD email for notifications (e.g., `Firstname.Lastname@amd.com`)
2. **Disk path** (required): Your working disk for storing runs and outputs (e.g., `/proj/rtg_oss_er_feint1/your_username`)

This creates:
```
users/$USER/
├── assignment.csv       # Your settings (with email and disk pre-configured)
├── data/               # Your task data
├── runs/               # Your run logs
├── params_centre/      # Your params repository
├── log_centre/         # Your centralized logs
├── tune_centre/        # Your tune files
└── script -> ...       # Symlink to shared scripts
```

### Running from Your User Directory

After setup, always run from your user directory:
```bash
cd <genie_agent_dir>/users/$USER
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
```

### Configuring assignment.csv

The assignment.csv is pre-configured with your email and disk path. You can edit it to customize:
- `project,your_project` - Your project name
- Add more `debugger,another.email@amd.com` lines for additional recipients

### Benefits

- **Isolated data**: Your tasks don't interfere with other users
- **Shared scripts**: All users benefit from script updates
- **Personal settings**: Each user has their own assignment.csv
- **Clean workspace**: No conflicts with other users' runs

## Notes

- This skill does NOT interfere with the email-based agent flow
- Uses separate task tracking (tasksModelCLI.csv vs tasksModel.csv)
- **Same scripts and logic as email flow**, just different entry point
- Results are reported directly back to you in the conversation
- Generated run scripts saved to: `runs/<tag>.csh`
- Execution logs saved to: `runs/<tag>.log`

## Email vs CLI Comparison

| Feature | Email Flow (Original) | CLI Flow (Genie) |
|---------|----------------------|------------------|
| Entry Point | Send email to VTO | `/agent` skill or ask naturally |
| Location | `mail_centre/` | `main_agent/` |
| Response | Email reply | Direct in conversation |
| Speed | Minutes (email round-trip) | Seconds |
| Monitoring | Check email | Real-time or xterm popup |
| Scripts Used | Same | Same |
| Config Files | Same | Same |

---

## Analyze Mode (`--analyze`) - Agent Teams Architecture

The `--analyze` flag enables **Claude Code to monitor and analyze static check results** using a specialized **Agent Teams architecture**. This provides intelligent violation analysis with minimal context usage.

### Supported Check Types

| Check Type | Agents Invoked | What Gets Analyzed |
|------------|----------------|-------------------|
| `cdc_rdc` | CDC/RDC Precondition, Violation Extractor, RTL Analyzers | **BOTH** cdc_report.rpt AND rdc_report.rpt |
| `lint` | Lint Violation Extractor, RTL Analyzers | leda_waiver.log (unwaived errors) |
| `spg_dft` | SpgDFT Precondition, Violation Extractor, RTL Analyzers | moresimple.rpt (blackbox, DFT errors) |
| `full_static_check` | ALL agents from all three flows | All of the above |

### Usage

```bash
# Run CDC/RDC with analyze mode
python3 script/genie_cli.py -i "run cdc_rdc at /proj/xxx for umc9_3" --execute --analyze --email

# Run full static check with analyze mode in xterm
python3 script/genie_cli.py -i "run full_static_check for umc17_0" --execute --xterm --analyze --email
```

### Agent Teams Architecture

The analysis uses specialized agents organized by check type:

```
config/analyze_agents/
├── ORCHESTRATOR.md              # Main orchestration guide
├── cdc_rdc/                     # CDC/RDC specific agents
│   ├── precondition_agent.md    # Inferred clks/rsts, unresolved modules
│   ├── violation_extractor.md   # CDC Section 3 + RDC Section 5
│   └── rtl_analyzer.md          # Analyze CDC crossings in RTL
├── lint/                        # Lint specific agents
│   ├── violation_extractor.md   # Parse unwaived violations
│   └── rtl_analyzer.md          # Analyze undriven ports
├── spgdft/                      # SpgDFT specific agents
│   ├── precondition_agent.md    # Check blackbox modules
│   ├── violation_extractor.md   # Parse DFT violations
│   └── rtl_analyzer.md          # Analyze TDR ports
└── shared/                      # Shared agents
    ├── library_finder.md        # Find libraries from lib.list
    └── report_compiler.md       # Generate HTML report
```

### How It Works

1. **Task Execution**: genie_cli.py launches static check in background
2. **Signal Detection**: Prints `ANALYZE_MODE_ENABLED` with task metadata
3. **Background Monitoring**: Claude spawns ONE haiku agent to monitor log for completion
4. **Live Analysis**: Upon completion, Claude runs analysis LIVE (not background):
   - Uses `config/IP_CONFIG.yaml` for fast report path resolution
   - Invokes only the relevant agents for the check type
   - For `cdc_rdc`: analyzes BOTH CDC and RDC reports
5. **HTML Report**: Compiles all results into `data/<tag>_analysis.html`
6. **Email Results**: Full HTML analysis sent to debuggers (ALL details in email, NOT conversation)

### IP Configuration (Fast Path Resolution)

Agents use `config/IP_CONFIG.yaml` to quickly find report paths:

| IP Family | Examples | Default Tile | Path Pattern |
|-----------|----------|--------------|--------------|
| `umc` | umc9_3, umc17_0 | `umc_top` | `out/linux_*/*/config/*/pub/sim/publish/tiles/tile/{tile}/cad/...` |
| `oss` | oss7_2, oss8_0 | `osssys` | Similar with `*_dc_elab` config |
| `gmc` | gmc13_1a | varies | Similar |

### Library Finder (Dynamic Discovery)

For blackbox modules, the Library Finder searches lib.list files (**NOT hardcoded paths**):

**Priority Order:**
1. `<ref_dir>/out/.../publish_rtl/manifest/{tile}_lib.list` (actual build library list)
2. `<ref_dir>/src/meta/tools/spgdft/variant/{ip}/project.params`
3. `<ref_dir>/src/meta/tools/cdc0in/variant/{ip}/{tile}_lib.list`

### LOW_RISK Patterns (Filtered Out)

| Pattern | Description |
|---------|-------------|
| `rsmu`, `RSMU` | Reset Scan MUX |
| `rdft`, `RDFT` | DFT related |
| `dft_`, `DFT_` | DFT prefix |
| `jtag`, `JTAG` | JTAG debug |
| `scan_`, `SCAN_` | Scan chain |
| `bist_`, `BIST_` | Built-in self test |
| `test_mode` | Test mode |
| `sms_fuse` | Fuse signals |
| `tdr_`, `TDR_` | Test Data Register |

### Output Flow

| Content | Email | Conversation |
|---------|-------|--------------|
| Precondition summary table | YES | NO |
| Violation counts and types | YES | NO |
| RTL analysis details | YES | NO |
| Recommendations & code snippets | YES | NO |
| "Analysis complete. Email sent." | YES | YES |

### Reference

For detailed orchestration guide, see: `config/analyze_agents/ORCHESTRATOR.md`

---

## Clock/Reset Structure Analyzer

Analyze RTL clock and reset structures with hierarchical port tracing:

```
/agent analyze clock reset structure at /proj/xxx/tree_dir for umc17_0
```

**What it Does:**
1. Parses .vf file to find all RTL source files
2. Identifies primary clocks (UCLKin0, DFICLKin0, Cpl_REFCLK, etc.)
3. Identifies primary resets (Cpl_PWROK, Cpl_RESETn, etc.)
4. Traces signal paths with **recursive port-name-following** (follows signal through port name changes)
5. Detects clock gating cells (ati_clock_gate, UMCCLKGATER)
6. Detects CDC synchronizers (techind_sync, UMCSYNC)

**Output Files:**
| File | Description |
|------|-------------|
| `clock_reset_report.rpt` | Text report with clock/reset hierarchy |
| `clock_reset_report.html` | HTML report for email |
| `clock_reset_clock.png` | Clock hierarchy diagram |
| `clock_reset_reset.png` | Reset hierarchy diagram |

**Hierarchical Port Tracing Example:**
```
UCLKin0 (top input)
  └─→ umc0 (umc).UCLK
    └─→ umcdat (umcdat).UCLK
      └─→ I_CHGATER_UCLK_FuncCGCG (UMCCLKGATER).C [GATING]
        └─→ I_CLKGATER (ati_clock_gate).clk_src [GATING]
```

**DOT Diagram Legend:**
| Shape | Clock Diagram | Reset Diagram |
|-------|---------------|---------------|
| Ellipse | Primary Clock | Primary Reset |
| Diamond | Clock Gating Cell | Reset Gen/Control |
| Hexagon | CDC Synchronizer | CDC Synchronizer |
| Box | Module Instance | Module Instance |

**Direct Script Usage:**
```bash
python3 script/rtg_oss_feint/clock_reset_analyzer.py <vf_file> --top <top_module> --output <report.rpt> --html <report.html> --dot <prefix>
```

---

**Version:** 2.1
**Last Updated:** 2026-03-17

**Changelog:**
- v2.2: Added `--analyze-only <tag>` flag and analyze instruction variants — skip re-running static check, go straight to analysis on existing results; emits `SKIP_MONITORING=true` so orchestrator skips monitoring step
- v2.1: Enhanced `--analyze` mode with Agent Teams architecture - specialized agents per check type, IP_CONFIG.yaml for fast path resolution, dynamic library discovery from lib.list files, improved HTML report colors, analyzes BOTH CDC and RDC reports for `cdc_rdc` check type
- v2.0: Added `--analyze` mode - Claude Code monitors and analyzes static check results with priority-based approach
- v1.9: Removed `--agent-team` and `--self-debug` (disabled — MultiAgentOrchestrator not robust enough for script-based flow)
- v1.8: Added `--user-disk` to `--setup-user` - disk path now required during first-time setup
- v1.7: Added GMC project support (gmc13_1a) with auto-detection routing
- v1.6: Added `--setup-user` for multi-user environment setup, `--to` email override option
- v1.5: Added Clock/Reset Structure Analyzer with recursive port-name-following tracing
- v1.4: Added P4 branch auto-detection from depot paths, changelist support, aligned all argument types with vtoHybridModel.py
- v1.3: Added supra regression with params (`<: PARAM = value :>` syntax), documented `make_tilebuilder_run.csh`
- v1.2: Added xterm mode (`--xterm`), auto-workspace creation, natural language examples
- v1.1: Initial skill documentation
