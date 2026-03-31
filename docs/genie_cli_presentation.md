# Genie CLI - Alternative Operational Mode for Rosenhorn Agent Flow

## Presentation Slides

---

## Slide 1: Title

# Genie CLI
## A Faster Way to Use the Rosenhorn Agent Flow

**Presenter:** [Your Name]
**Date:** February 2026

*Bypass email, get results faster*

---

## Slide 2: The Challenge with Email Flow

### Current Email-Based Workflow

```
User → Send Email to VTO → vtoHybridModel.py → Execute Scripts → Email Reply
```

**Pain Points:**

| Issue | Impact |
|-------|--------|
| Email round-trip delay | Minutes of waiting |
| No real-time feedback | Can't see progress |
| Hard to iterate | Each retry requires new email |
| Debugging difficulty | Limited visibility into execution |

**Question:** *Can we execute the same tasks without email overhead?*

---

## Slide 3: Introducing Genie CLI

### The Solution: Direct CLI Bypass

```
User → genie_cli.py → Execute Scripts → Immediate Results
```

**What is Genie CLI?**
- Alternative entry point to the Rosenhorn Agent Flow
- Bypasses email system entirely
- Executes the **same scripts** with the **same configuration**
- Integrated with Claude Code for natural language interaction

**Location:** `main_agent/script/genie_cli.py`

---

## Slide 4: Architecture Comparison

### Two Ways to Use the Agent

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   EMAIL FLOW (Original)         CLI FLOW (Genie)            │
│   ┌─────────────────┐          ┌─────────────────┐          │
│   │  mail_centre/   │          │  main_agent/    │          │
│   │ vtoHybridModel  │          │  genie_cli.py   │          │
│   └────────┬────────┘          └────────┬────────┘          │
│            │                            │                   │
│       Send Email                   /agent skill             │
│       to VTO                       or natural language      │
│            │                       or CLI directly          │
│            │                            │                   │
│            └────────────┬───────────────┘                   │
│                         ▼                                   │
│          ┌──────────────────────────────┐                   │
│          │   Same Scripts Executed      │                   │
│          │   script/rtg_oss_feint/      │                   │
│          └──────────────────────────────┘                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Slide 5: Key Features

### What's New in Genie CLI

| Feature | Description |
|---------|-------------|
| **Three Execution Modes** | Dry-run, Background, Xterm Popup |
| **Xterm Mode** | Visual monitoring in popup window |
| **Auto-Workspace** | Creates P4 workspace automatically for static checks |
| **Email on Completion** | Optional email notification when task finishes |
| **Task Management** | Kill running tasks, check status |
| **Natural Language** | Ask Claude naturally, it calls genie_cli |

### Supported Tasks
- Static checks (CDC/RDC, Lint, SPG_DFT, build_rtl)
- TileBuilder operations (branch, monitor, timing reports)
- Params/Tune management
- P4 operations

---

## Slide 6: Execution Modes

### Three Ways to Run Tasks

| Mode | Command | Use Case |
|------|---------|----------|
| **Dry Run** | `genie_cli.py -i "..."` | Preview what will execute |
| **Background** | `genie_cli.py -i "..." --execute` | Set and forget |
| **Xterm Popup** | `genie_cli.py -i "..." --execute --xterm` | Visual monitoring |

### Xterm Mode Benefits
- See real-time output in popup window
- Window closes automatically on completion
- Output captured to log file
- Email sent on completion (if `--email` flag used)

```bash
# Example: Timing report with xterm and email
python3 genie_cli.py -i "report timing for /proj/xxx/tile" --execute --xterm --email
```

---

## Slide 7: Usage Examples

### Via Claude Code (Natural Language)

```
report timing and area for /proj/xxx/tile_dir, execute in xterm and send email

run full_static_check for umc9_3, execute in xterm and send email to debuggers

run lint at /proj/xxx for umc9_3 and send email when done
```

### Via /agent Skill

```
/agent report timing and area for /proj/xxx/tile_dir
/agent run cdc_rdc at /proj/xxx/tree_dir for umc9_3
/agent --list
```

### Via CLI Directly

```bash
python3 genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email
python3 genie_cli.py --status 20260215202931
python3 genie_cli.py --kill 20260215202931
```

---

## Slide 8: Auto-Workspace Creation

### Static Checks Without Specifying Directory

**Old way (Email):** Must provide tree directory

**New way (Genie CLI):** Directory is optional!

```bash
# Auto-creates workspace and runs all static checks
python3 genie_cli.py -i "run full_static_check for umc9_3" --execute --xterm --email
```

### What Happens Automatically:

1. Creates workspace directory using disk from `assignment.csv`
2. Runs `p4_mkwa` to sync codebase based on IP:
   - `umc9_3` → Default UMC trunk
   - `umc9_2` → `UMC_9_2_WEISSHORN_TRUNK` branch
3. Verifies sync success
4. Executes requested static checks
5. Sends email with results

**Workspace naming:** `umc_<project>_<timestamp>`

---

## Slide 9: Benefits Summary

### Email Flow vs Genie CLI

| Aspect | Email Flow | Genie CLI |
|--------|------------|-----------|
| **Speed** | Minutes (round-trip) | Seconds |
| **Feedback** | Wait for email | Real-time |
| **Monitoring** | Check inbox | Xterm popup or log file |
| **Iteration** | New email each time | Quick re-run |
| **Debugging** | Limited | Full visibility |
| **Integration** | Standalone | Claude Code native |

### Who Should Use It?

- **Engineers using Claude Code** → Use Genie CLI
- **Remote/automated submissions** → Use Email Flow
- **Quick iterations during debug** → Use Genie CLI
- **Scheduled tasks** → Use Email Flow

---

## Slide 10: Getting Started

### Quick Start Commands

```bash
# List all available instructions
python3 script/genie_cli.py --list

# Dry run (preview command)
python3 script/genie_cli.py -i "report timing for /proj/xxx/tile"

# Execute in background with email
python3 script/genie_cli.py -i "run lint at /proj/xxx for umc9_3" --execute --email

# Execute in xterm popup with email
python3 script/genie_cli.py -i "report timing for /proj/xxx/tile" --execute --xterm --email

# Check task status
python3 script/genie_cli.py --status <tag>

# Kill running task
python3 script/genie_cli.py --kill <tag>
```

### Documentation

- `CLAUDE.md` - Full documentation
- `.claude/skills/agent/SKILL.md` - Agent skill reference

### Questions?

---

## End of Presentation

