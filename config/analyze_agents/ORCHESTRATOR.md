# Analyze Mode Orchestrator Guide

**You are the orchestrator agent.** The main Claude session has spawned you with a fresh context window to handle the full analysis or analyze-fixer flow. Your inputs (TAG, CHECK_TYPE, REF_DIR, IP, BASE_DIR, etc.) were passed in your prompt. Execute the flow below and notify the main session when done.

---

## PRE-FLIGHT: Load Critical Rules (Do This First)

**Before taking any other action:**

1. Read `config/analyze_agents/shared/CRITICAL_RULES.md`
2. Store its full content as `CRITICAL_RULES_BLOCK`
3. Prepend `CRITICAL_RULES_BLOCK` at the **very TOP** of EVERY sub-agent prompt you construct

This ensures agents always have the most critical rules at the top of their context before detailed instructions, preventing context-displacement of key rules during long analysis runs.

---

## CRITICAL: MUST USE TASK TOOL TO INVOKE SUB-AGENTS

**DO NOT analyze reports directly.** You MUST use the Task tool to spawn specialized sub-agents.

**❌ NEVER run `ps`, `sleep`, `cat <pid_file>`, or any polling bash commands yourself.**
**✅ For task monitoring: spawn a monitor Task agent (haiku). It does the polling. You just wait for it to return.**

### Why Invoke Agents (Not Do It Yourself)?

| Approach | Problem |
|----------|---------|
| **Main session reads reports directly** | Uses too much context, slow, may miss details |
| **Spawn agents via Task tool** | Parallel execution, specialized prompts, better results |

### Mandatory Agent Invocations

When `--analyze` mode is detected, you **MUST** spawn these agents using the Task tool:

```
# For CDC/RDC:
Task(description="CDC/RDC Precondition Analysis", subagent_type="general-purpose", prompt="<from cdc_rdc/precondition_agent.md>")
Task(description="CDC/RDC Violation Extraction", subagent_type="general-purpose", prompt="<from cdc_rdc/violation_extractor.md>")

# For Lint:
Task(description="Lint Violation Extraction", subagent_type="general-purpose", prompt="<from lint/violation_extractor.md>")

# For SpgDFT:
Task(description="SpgDFT Precondition Analysis", subagent_type="general-purpose", prompt="<from spgdft/precondition_agent.md>")
Task(description="SpgDFT Violation Extraction", subagent_type="general-purpose", prompt="<from spgdft/violation_extractor.md>")
```

### What NOT To Do

❌ **WRONG:** Reading cdc_report.rpt directly in main session
❌ **WRONG:** Parsing violations yourself without spawning agents
❌ **WRONG:** Skipping agent invocation to "save time"

✅ **RIGHT:** Always spawn Task agents for each analysis step
✅ **RIGHT:** Run agents in parallel when possible
✅ **RIGHT:** Compile results from agent outputs into HTML report

---

## Directory Structure

```
config/analyze_agents/
├── ORCHESTRATOR.md              # This file
├── LEARNING.md                  # Index — points to per-check LEARNING.md files
├── cdc_rdc/                     # CDC/RDC specific agents
│   ├── LEARNING.md              # CDC/RDC past fixes knowledge base
│   ├── precondition_agent.md    # Check inferred clks/rsts, unresolved modules
│   ├── violation_extractor.md   # Parse CDC/RDC violations
│   └── rtl_analyzer.md          # Analyze CDC crossings in RTL
├── lint/                        # Lint specific agents
│   ├── LEARNING.md              # Lint past fixes knowledge base
│   ├── violation_extractor.md   # Parse lint violations
│   └── rtl_analyzer.md          # Analyze undriven ports, etc.
├── spgdft/                      # SpgDFT specific agents
│   ├── LEARNING.md              # SpgDFT past fixes knowledge base
│   ├── precondition_agent.md    # Check blackbox modules
│   ├── violation_extractor.md   # Parse DFT violations
│   └── rtl_analyzer.md          # Analyze TDR ports, etc.
└── shared/                      # Shared agents
    ├── library_finder.md        # Find missing libraries
    ├── fix_consolidator.md      # Deduplicate + verify RTL analyzer fix suggestions
    └── report_compiler.md       # Generate HTML report
```

---

## LEARNING.md - Past Fixes Knowledge Base

**IMPORTANT:** Before analyzing violations, agents SHOULD check their check-type-specific LEARNING.md for similar past issues.

**Paths (one per check type — load only the relevant one):**
- CDC/RDC: `config/analyze_agents/cdc_rdc/LEARNING.md`
- Lint: `config/analyze_agents/lint/LEARNING.md`
- SpgDFT: `config/analyze_agents/spgdft/LEARNING.md`

**Purpose:** Each file contains documented past violations and their solutions for that check type. When analyzing a new violation:

1. **Check the relevant LEARNING.md first** - Is there a similar pattern already documented?
2. **Apply known fix** - If a matching pattern exists, recommend the same solution

**DO NOT update any LEARNING.md automatically.** Updates are managed manually by the user only.

---

## Overview

When `--analyze` mode is enabled:

1. **Monitoring** - Spawn ONE foreground haiku agent to poll for task completion (orchestrator blocks on this Task call — all sleep/polling happens inside the monitor, not the orchestrator)
2. **Live analysis** - When complete, run analysis agents
3. **Compile & email** - Generate HTML and send email
4. **Minimal output** - Just say "Analysis complete. Email sent."

### Why This Approach?

| Phase | Mode | Why |
|-------|------|-----|
| **Monitoring** | Foreground Task (blocks orchestrator) | Monitor polls/sleeps internally — orchestrator does NOT run sleep commands itself |
| **Analysis** | Live sub-agents | Quick, parallel, each with focused context |

### Flow

**❌ DO NOT run `ps`, `cat`, `sleep`, or any bash commands yourself to monitor the task.**
**✅ MUST spawn a Task agent to do the monitoring. The Task call blocks the orchestrator until the monitor returns.**

```
1. Detect ANALYZE_MODE_ENABLED
         │
         ├── If SKIP_MONITORING=true (--analyze-only mode, task already complete):
         │     → Skip steps 2-3, go directly to step 4
         │
2. Spawn monitor Task agent (foreground, haiku) — see "Monitoring Agent" section below
         │ Orchestrator blocks on this Task call.
         │ Monitor does all ps/cat/sleep internally.
         │ Orchestrator NEVER runs sleep or ps commands itself.
         │
3. Monitor Task returns with status:
         │
         ├── If skip_analysis=true (spec file has error):
         │     → Say: "Task failed. Skipping analysis."
         │     → STOP - do not invoke analysis agents
         │
         └── If skip_analysis=false (spec file OK):
               │
4. Spawn IN PARALLEL — precondition agents + violation extractor agents
   - Precondition agents ALWAYS run (even if clean — result belongs in report)
   - Violation extractor agents ALWAYS run (need counts to decide next step)
               │
5. Collect results from step 4 → apply SKIP LOGIC before spawning RTL analyzers:
   │
   ├── Library Finder:
   │     Skip if: unresolved_modules == 0 AND blackbox_modules == 0
   │     (CDC precondition) or needs_library_search == false (SpgDFT precondition)
   │
   ├── CDC RTL Analyzers:
   │     Skip if: CDC extractor focus_violations == 0
   │     If skip: note "CDC CLEAN" in report, no RTL analysis needed
   │
   ├── RDC RTL Analyzers:
   │     Skip if: RDC extractor focus_violations == 0
   │     If skip: note "RDC CLEAN" in report, no RTL analysis needed
   │
   ├── Lint RTL Analyzers:
   │     Skip if: Lint extractor focus_violations == 0
   │     If skip: note "Lint CLEAN" in report, no RTL analysis needed
   │
   └── SpgDFT RTL Analyzers:
         Skip if: SpgDFT extractor focus_violations == 0
         If skip: note "SpgDFT CLEAN" in report, no RTL analysis needed
               │
6. Spawn whichever RTL analyzer agents are NOT skipped (in PARALLEL)
               │
7. Spawn Fix Consolidator agent(s) — deduplicate + verify RTL analyzer fixes
   - One per check type that had violations (cdc/rdc/lint/spgdft)
   - Writes: data/<tag>_consolidated_<check>.json
               │
8. Compile HTML report from all agent outputs
   - Report compiler reads consolidated JSON for recommendations section
   - Write to data/<tag>_analysis.html
               │
9. Send email with HTML report
               │
10. Say: "Analysis complete. Email sent."
```

**IMPORTANT:** If monitoring agent returns `skip_analysis=true`, DO NOT invoke any analysis agents. Just report the failure.

---

## Detection

When genie_cli.py runs with `--analyze`, it prints:
```
ANALYZE_MODE_ENABLED
TAG: <tag>
CHECK_TYPE: <check_type>
REF_DIR: <ref_dir>
IP: <ip>
LOG_FILE: <log_file>
SPEC_FILE: <spec_file>
```

Also creates: `data/<tag>_analyze` with the same info.

---

## Agent Teams by Check Type

**CRITICAL:** Only invoke agents for the specific check type requested:

| check_type | Agents to Invoke | DO NOT Invoke |
|------------|------------------|---------------|
| `cdc_rdc` | CDC/RDC Precondition, CDC/RDC Violation Extractor, CDC/RDC RTL Analyzers | Lint, SpgDFT |
| `lint` | Lint Violation Extractor, Lint RTL Analyzers | CDC/RDC, SpgDFT |
| `spg_dft` | SpgDFT Precondition, SpgDFT Violation Extractor, SpgDFT RTL Analyzers | CDC/RDC, Lint |
| `full_static_check` | ALL agents from all three flows | - |

### CDC/RDC Flow

**IMPORTANT:** When check_type is `cdc_rdc`, agents MUST analyze BOTH reports:
- `cdc_report.rpt` (Section 2 for preconditions, Section 3 for CDC violations)
- `rdc_report.rpt` (Section 2 for preconditions, Section 5 for RDC violations)

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │ 1. CDC/RDC Precondition │ (haiku, quick)
              │    cdc_rdc/precondition │
              │    READS BOTH REPORTS:  │
              │    - cdc_report.rpt     │
              │    - rdc_report.rpt     │
              │    EXTRACTS:            │
              │    - Inferred clks/rsts │
              │    - Unresolved modules │
              │    - Blackbox modules   │
              └────────────┬────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌─────────────────────┐           ┌─────────────────────┐
│ 2a. Library Finder  │           │ 2b. CDC/RDC Violation│
│  (if unresolved)    │           │     Extractor        │
│  shared/library_    │           │  cdc_rdc/violation_  │
│  (haiku)            │           │  READS BOTH REPORTS: │
│                     │           │  - CDC Section 3     │
│                     │           │  - RDC Section 5     │
│                     │           │  (sonnet)            │
└─────────────────────┘           └──────────┬──────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
    ┌─────────┴─────────┐          ┌─────────┴─────────┐          ┌─────────┴─────────┐
    │   CDC Violations  │          │   RDC Violations  │          │                   │
    │  (up to 10, or 20 │          │  (up to 10, or 20 │          │                   │
    │  if RDC clean)    │          │  if CDC clean)    │          │                   │
    └────────┬──────────┘          └────────┬──────────┘          │                   │
             │                              │                     │                   │
    ┌────────┴────────┐            ┌────────┴────────┐            │                   │
    ▼                 ▼            ▼                 ▼            ▼                   │
┌────────┐     ┌────────┐    ┌────────┐     ┌────────┐    ┌────────┐                  │
│CDC RTL │     │CDC RTL │    │RDC RTL │     │RDC RTL │    │  ...   │                  │
│Analyzer│     │Analyzer│    │Analyzer│     │Analyzer│    │        │                  │
│(viol 1)│     │(viol 2)│    │(viol 1)│     │(viol 2)│    │        │                  │
│cdc_rdc/│     │cdc_rdc/│    │cdc_rdc/│     │cdc_rdc/│    │        │                  │
│rtl_    │     │rtl_    │    │rtl_    │     │rtl_    │    │        │                  │
│(haiku) │     │(haiku) │    │(haiku) │     │(haiku) │    │        │                  │
└────────┘     └────────┘    └────────┘     └────────┘    └────────┘                  │
```

### Lint Flow

**One RTL analyzer agent per unique RTL file — handles ALL violations in that file in one pass.**

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │ 1. Lint Violation       │ (sonnet, medium)
              │    Extractor            │
              │    lint/violation_      │
              │    - Parse ALL unwaived │
              │    - Filter RSMU/DFT    │
              │    - Group by RTL file  │
              └────────────┬────────────┘
                           │ violations_by_file: {file1: [...], file2: [...], ...}
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
  ┌────────────┐    ┌────────────┐    ┌────────────┐
  │ Lint RTL   │    │ Lint RTL   │    │ Lint RTL   │
  │ Analyzer   │    │ Analyzer   │    │ Analyzer   │
  │ (file 1)   │    │ (file 2)   │    │ (file N)   │
  │ ALL viols  │    │ ALL viols  │    │ ALL viols  │
  │ in file 1  │    │ in file 2  │    │ in file N  │
  │ (sonnet)   │    │ (sonnet)   │    │ (sonnet)   │
  └────────────┘    └────────────┘    └────────────┘
         152 violations across 15 files = 15 agents (not 152)
```

### SpgDFT Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │ 1. SpgDFT Precondition  │ (haiku, quick)
              │    spgdft/precondition  │
              │    - Blackbox modules   │
              └────────────┬────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌─────────────────────┐           ┌─────────────────────┐
│ 2a. Library Finder  │           │ 2b. SpgDFT Violation│
│  (if blackbox)      │           │     Extractor       │
│  shared/library_    │           │  spgdft/violation_  │
│  (haiku)            │           │  (sonnet)           │
└─────────────────────┘           └──────────┬──────────┘
                                             │
                           ┌─────────────────┼─────────────────┐
                           ▼                 ▼                 ▼
                    ┌────────────┐    ┌────────────┐    ┌────────────┐
                    │ SpgDFT RTL │    │ SpgDFT RTL │    │ SpgDFT RTL │
                    │ Analyzer   │    │ Analyzer   │    │ Analyzer   │
                    │ (viol 1)   │    │ (viol 2)   │    │ (viol N)   │
                    │ spgdft/    │    │ spgdft/    │    │ spgdft/    │
                    │ rtl_       │    │ rtl_       │    │ rtl_       │
                    │ (sonnet)   │    │ (sonnet)   │    │ (sonnet)   │
                    └────────────┘    └────────────┘    └────────────┘
```

### Full Static Check Flow

**ONLY** for `full_static_check` - runs all three flows in parallel, then produces **3 SEPARATE reports and 3 SEPARATE emails**:

```
1. CDC/RDC Flow → all agents complete
2. Lint Flow    → all agents complete    (parallel with CDC/RDC)
3. SpgDFT Flow  → all agents complete    (parallel with CDC/RDC)
              │
4. Spawn 3 PARALLEL report compilers, each for ONE check type:
   ├── CDC/RDC Report Compiler → data/<tag>_analysis_cdc.html
   ├── Lint Report Compiler    → data/<tag>_analysis_lint.html
   └── SpgDFT Report Compiler  → data/<tag>_analysis_spgdft.html
              │
5. Send 3 SEPARATE emails:
   ├── python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
   ├── python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
   └── python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft
```

Each report covers ONLY its own check type — full detail, same as running that check individually.
Each recipient gets 3 emails in their inbox, one per check type.

**For individual check types (`cdc_rdc`, `lint`, `spg_dft`):**
- Only run that specific flow
- Spawn ONE report compiler for that check type only
- Send ONE email with `--check-type <check_type>`

---

## Monitoring Agent

### ⛔ FORBIDDEN — DO NOT DO ANY OF THESE:
```
❌ Bash(ps -p <PID> ...)
❌ Bash(python3 -c "import time, os, sys ...")
❌ Bash(sleep 30 ...)
❌ Bash(python3 script/genie_cli.py --status ...)
❌ Bash(cat data/<tag>_pid ...)
❌ Bash(while true; do ... done)
❌ Any bash polling loop of any kind
```

**If you find yourself writing ANY of the above — STOP. You are doing it wrong.**

### ✅ CORRECT — ONLY DO THIS:

Spawn ONE Task agent (haiku, foreground). The orchestrator blocks on this single Task call. The monitor agent does all ps/sleep/cat internally.

```python
Task tool:
  subagent_type: "general-purpose"
  model: "haiku"
  description: "Monitor static check completion"
  run_in_background: false
  prompt: |
    Monitor the static check task for completion.

    TAG: <tag>
    PID_FILE: data/<tag>_pid
    Log file: <log_file>
    Spec file: <spec_file>

    **FAST MONITORING - Check every 15-30 seconds:**

    1. **PID CHECK (do this first):**
       - Run: ls data/<tag>_pid 2>/dev/null
       - If PID file does NOT exist → Task ended, go to step 2
       - If PID file exists, read PID: cat data/<tag>_pid
       - Check if process running: ps -p <PID> -o pid= 2>/dev/null
       - If process NOT running → Task ended, go to step 2
       - If process still running → wait 15-30 seconds, repeat step 1

    2. **SPEC FILE CHECK (after PID ends):**
       - Read the spec file: cat data/<tag>_spec
       - Check for ERROR indicators in spec file:
         - "Error" or "ERROR"
         - "Failed" or "FAILED"
         - "error:" or "failed:"

       **If spec file has ERROR/FAILED content:**
       - Return: status="failed", skip_analysis=true
       - Message: "Task failed - spec file shows error, skipping analysis"

       **If spec file is empty or missing:**
       - Do NOT declare failure — go to step 3 (log file check)

       **If spec file has valid non-empty content with no errors:**
       - Return: status="complete", skip_analysis=false
       - Message: "Task completed successfully, proceed with analysis"

    3. **LOG FILE CHECK (only if spec is empty or missing):**
       - Read the log file provided in LOG_FILE input
       - Look for: "Static check completed successfully"
         - If found → Return: status="complete", skip_analysis=false
         - Message: "Task completed successfully (spec empty — lint results in report file)"
       - Look for: "ERROR" or "FAILED" or "Static check failed"
         - If found → Return: status="failed", skip_analysis=true
         - Message: "Task failed - log file shows error"
       - If neither found: wait 15 seconds, re-check log (repeat up to 3 times)
       - If still inconclusive → Return: status="failed", skip_analysis=true
         - Message: "Task outcome unclear after log check — treating as failed"

    **RETURN IMMEDIATELY after step 2 or 3.**

    Return format:
    {
      "status": "complete" or "failed",
      "skip_analysis": true or false,
      "message": "description of result"
    }
```

## File-Based Intermediate Storage

**Every agent writes its findings to a JSON file in `data/`.
The report compiler reads from these files — NOT from context.**

### File Naming Convention

| Agent | Output File |
|-------|------------|
| CDC/RDC Precondition | `data/<tag>_precondition_cdc.json` |
| SpgDFT Precondition | `data/<tag>_precondition_spgdft.json` |
| CDC/RDC Extractor | `data/<tag>_extractor_cdc.json` |
| Lint Extractor | `data/<tag>_extractor_lint.json` |
| SpgDFT Extractor | `data/<tag>_extractor_spgdft.json` |
| CDC RTL Analyzer (violation N) | `data/<tag>_rtl_cdc_<N>.json` |
| RDC RTL Analyzer (violation N) | `data/<tag>_rtl_rdc_<N>.json` |
| Lint RTL Analyzer (file N) | `data/<tag>_rtl_lint_<N>.json` (N = file index, contains ALL violations for that file) |
| SpgDFT RTL Analyzer (violation N) | `data/<tag>_rtl_spgdft_<N>.json` |
| Fix Consolidator (CDC) | `data/<tag>_consolidated_cdc.json` |
| Fix Consolidator (RDC) | `data/<tag>_consolidated_rdc.json` |
| Fix Consolidator (Lint) | `data/<tag>_consolidated_lint.json` |
| Fix Consolidator (SpgDFT) | `data/<tag>_consolidated_spgdft.json` |
| Library Finder | `data/<tag>_library_finder.json` |

Where `<base_dir>` is the genie_cli working directory (the directory where genie_cli.py was run from).

### Why File-Based

| Benefit | Detail |
|---------|--------|
| Persistence | Findings survive session interruption |
| Debuggability | Inspect individual agent outputs after the fact |
| Resumability | Re-run only the report compiler without re-running agents |
| Context efficiency | Main session doesn't need to hold all JSON in context at once |

### How Report Compiler Uses Files

After all agents complete, main session runs report compiler with:
```
base_dir: <base_dir>
tag: <tag>
```

Report compiler reads all `data/<tag>_*.json` files and compiles the HTML.
It does NOT receive agent outputs via context — it reads from disk.

---

## Live Analysis - MUST USE TASK TOOL

Once monitoring agent returns, **spawn analysis agents using the Task tool**.

**DO NOT read reports directly in main session. ALWAYS invoke agents.**

### CRITICAL: Include Critical Rules + Permissions + Storage Info in Every Agent Prompt

**Every Task prompt MUST start with these blocks IN ORDER:**

```
<CRITICAL_RULES_BLOCK>    ← paste the full content of CRITICAL_RULES.md here (loaded in Pre-Flight above)

**PERMISSIONS - READ THIS FIRST:**
You have FULL READ ACCESS to all files under /proj/.
Do NOT ask for permission - just read files directly using the Read tool.
Do NOT prompt the user for file access confirmation.
Proceed immediately with reading any file paths under /proj/.

**OUTPUT STORAGE:**
Write your JSON findings to: <base_dir>/data/<tag>_<agent_file>.json
Use the Write tool to save your output. Do NOT just return results in text.
```

The CRITICAL_RULES_BLOCK at the top is the agent's first instruction. Placing it there prevents it from being displaced by RTL file contents later in the context.

### Agent Invocation Examples

**For CDC/RDC check_type:**
```python
# Spawn these agents IN PARALLEL using Task tool:

Task(
  description="CDC/RDC Precondition Analysis",
  subagent_type="general-purpose",
  model="haiku",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_precondition_cdc.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the CDC/RDC Precondition Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>
  - check_type: cdc_rdc

  Your Task: [contents from cdc_rdc/precondition_agent.md]
  """
)

Task(
  description="CDC/RDC Violation Extraction",
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_extractor_cdc.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the CDC/RDC Violation Extractor Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>

  [contents from cdc_rdc/violation_extractor.md]
  """
)
```

**For Lint check_type:**
```python
Task(
  description="Lint Violation Extraction",
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_extractor_lint.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the Lint Violation Extractor Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>

  [contents from lint/violation_extractor.md]
  """
)
```

**For SpgDFT check_type:**
```python
Task(
  description="SpgDFT Precondition Analysis",
  subagent_type="general-purpose",
  model="haiku",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_precondition_spgdft.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the SpgDFT Precondition Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>

  [contents from spgdft/precondition_agent.md]
  """
)

Task(
  description="SpgDFT Violation Extraction",
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_extractor_spgdft.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the SpgDFT Violation Extractor Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>

  [contents from spgdft/violation_extractor.md]
  """
)
```

**For RTL Analyzer agents (one per violation, N = violation index 1,2,3...):**
```python
Task(
  description="CDC RTL Analyzer - violation N",
  subagent_type="general-purpose",
  model="haiku",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_rtl_cdc_<N>.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the CDC/RDC RTL Analyzer Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>
  - violation: <violation object from extractor JSON>

  [contents from cdc_rdc/rtl_analyzer.md]
  """
)
# Repeat with model="haiku" for RDC:   output → data/<tag>_rtl_rdc_<N>.json   (N = violation index)
# Repeat with model="sonnet" for SpgDFT: output → data/<tag>_rtl_spgdft_<N>.json (N = violation index)

# For Lint: one agent per UNIQUE RTL FILE (not per violation):
Task(
  description="Lint RTL Analyzer - file N (<rtl_filename>)",
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_rtl_lint_<N>.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the Lint RTL Analyzer Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>
  - rtl_file: <rtl_file_path>                    ← one specific RTL file
  - violations: <list of all violations in this file from violations_by_file[rtl_file]>
  - file_index: <N>

  [contents from lint/rtl_analyzer.md]
  """
)
# Spawn one such agent per entry in violations_by_file — all in PARALLEL
# e.g., 152 violations across 15 files → spawn 15 agents simultaneously
```

**For Library Finder:**
```python
Task(
  description="Library Finder",
  subagent_type="general-purpose",
  model="haiku",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to: <base_dir>/data/<tag>_library_finder.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the Library Finder Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>
  - tile: <tile>
  - modules: <list from precondition JSON>

  [contents from shared/library_finder.md]
  """
)
```

**For Fix Consolidator (spawn after all RTL analyzers complete, one per non-clean check type):**
```python
Task(
  description="Fix Consolidator - CDC/RDC",
  subagent_type="general-purpose",
  model="sonnet",
  prompt="""**PERMISSIONS - READ THIS FIRST:**
  You have FULL READ ACCESS to all files under /proj/.
  Do NOT ask for permission - just read files directly using the Read tool.

  **OUTPUT STORAGE:**
  Write your JSON findings to:
    <base_dir>/data/<tag>_consolidated_cdc.json
    <base_dir>/data/<tag>_consolidated_rdc.json
  Use the Write tool to save. Do NOT just return results in text.

  You are the Fix Consolidator Agent.

  Input:
  - tag: <tag>
  - base_dir: <base_dir>
  - ref_dir: <ref_dir>
  - ip: <ip>
  - check_type: cdc_rdc

  [contents from shared/fix_consolidator.md]
  """
)
# For lint:    check_type=lint    → consolidated_lint.json
# For spg_dft: check_type=spg_dft → consolidated_spgdft.json
```

### Reference Documents

The agent instruction files provide guidance on WHAT to look for:

| Check | Reference | Key Points |
|-------|-----------|------------|
| CDC/RDC Precondition | `cdc_rdc/precondition_agent.md` | **BOTH** CDC+RDC Section 2, inferred clks/rsts, unresolved |
| CDC/RDC Violations | `cdc_rdc/violation_extractor.md` | **BOTH** CDC Section 3 + RDC Section 5, filter RSMU/DFT |
| CDC/RDC RTL | `cdc_rdc/rtl_analyzer.md` | Find signal, check sync, recommend fix — **use MODULE name not instance name** for `cdc custom sync` |
| Lint Violations | `lint/violation_extractor.md` | Parse unwaived section |
| Lint RTL | `lint/rtl_analyzer.md` | Check undriven ports |
| SpgDFT Precondition | `spgdft/precondition_agent.md` | Blackbox modules |
| SpgDFT Violations | `spgdft/violation_extractor.md` | Parse moresimple.rpt, **ERROR severity only** |
| SpgDFT RTL | `spgdft/rtl_analyzer.md` | TDR ports, tie-offs |
| Library Search | `shared/library_finder.md` | Find lib.list, search for modules |
| Fix Consolidator | `shared/fix_consolidator.md` | Deduplicate fixes, detect instance name confusion, rank by coverage |
| HTML Report | `shared/report_compiler.md` | Beautiful HTML template — reads consolidated JSON for recommendations |

### CRITICAL — Tech-Cell Constraint: Module Name vs Instance Name

When the CDC RTL Analyzer traces through sync wrappers (e.g., `UMCSYNC` → `techind_sync` → deepest tech cell), it must identify the correct name for `cdc custom sync`.

In Verilog, every instantiation line has the form:
```verilog
<MODULE_NAME>  <instance_name>  (.port(signal), ...);
```

- **`<MODULE_NAME>`** (first token) — this is what `cdc custom sync` requires
- **`<instance_name>`** (second token) — this is just a label; **do NOT use in constraints**

The instance name often looks similar to a cell name (e.g., it may contain process/VT suffixes), which makes it easy to confuse with the module name. Always read the actual implementation file and take the **first token** of the instantiation line.

Also read the clock port from the instantiation's port connections (`.CP(...)`, `.CLK(...)`, etc.) — it varies by cell family and must match what is used in `netlist port domain` entries.

### Analysis Steps (Using Task Tool)

**MUST use Task tool for each step - DO NOT analyze directly in main session:**

```
1. Spawn IN PARALLEL — precondition agent(s) + violation extractor agent(s):
   - CDC/RDC precondition: Task(prompt from cdc_rdc/precondition_agent.md)
   - SpgDFT precondition:  Task(prompt from spgdft/precondition_agent.md)
   - CDC/RDC extractor:    Task(prompt from cdc_rdc/violation_extractor.md)
   - Lint extractor:       Task(prompt from lint/violation_extractor.md)
   - SpgDFT extractor:     Task(prompt from spgdft/violation_extractor.md)
   NOTE: Precondition agents ALWAYS run regardless of counts.

2. Collect results → apply SKIP LOGIC:

   ┌─────────────────────────────────────────────────────────────────────┐
   │                         SKIP LOGIC TABLE                            │
   ├─────────────────────────┬───────────────────────────────────────────┤
   │ Agent                   │ Skip Condition                            │
   ├─────────────────────────┼───────────────────────────────────────────┤
   │ Library Finder          │ unresolved == 0 AND blackbox == 0         │
   │ CDC RTL Analyzers       │ CDC focus_violations == 0                 │
   │ RDC RTL Analyzers       │ RDC focus_violations == 0                 │
   │ Lint RTL Analyzers      │ Lint focus_violations == 0                │
   │ (one per RTL file)      │ (skip entire lint RTL analysis if clean)  │
   │ SpgDFT RTL Analyzers    │ SpgDFT focus_violations == 0             │
   │ CDC/RDC Precondition    │ NEVER skip                                │
   │ SpgDFT Precondition     │ NEVER skip                                │
   └─────────────────────────┴───────────────────────────────────────────┘

   When an agent is skipped → mark that section as "CLEAN" in the report.
   Do NOT spawn agents for clean checks.

3. Spawn whichever RTL analyzer agents are NOT skipped (in PARALLEL)
   - CDC: up to 10 violations in parallel (or up to 20 if RDC is CLEAN)
   - RDC: up to 10 violations in parallel (or up to 20 if CDC is CLEAN)
   - Lint: one agent per unique RTL file (all violations in that file handled by one agent)
   - SpgDFT: up to N violations in parallel
   - Library Finder: 1 agent (if unresolved/blackbox > 0)

3.5 Spawn Fix Consolidator agent(s) IN PARALLEL — one per check type with violations:
   - Reference: shared/fix_consolidator.md
   - Skip if that check type was CLEAN (no RTL analyzers ran for it)
   - cdc_rdc → spawn 2 in parallel: consolidated_cdc.json + consolidated_rdc.json
   - lint    → spawn 1: consolidated_lint.json
   - spg_dft → spawn 1: consolidated_spgdft.json
   - full_static_check → spawn up to 4 in parallel (all non-clean types)
   - Input: tag, base_dir, ref_dir, ip, check_type (cdc_rdc | lint | spg_dft)

4. Collect all agent results
   - DO NOT read reports yourself - use agent outputs

5. Compile HTML report(s) using the report_compiler agent via Task tool:
   - Report compiler reads data/<tag>_consolidated_<check>.json for recommendations
   - For single check types (`cdc_rdc`, `lint`, `spg_dft`):
     Spawn ONE report compiler Task for that check type.
     Output: data/<tag>_analysis_<check>.html  (cdc / lint / spgdft)
   - For `full_static_check`:
     Spawn 3 PARALLEL report compiler Tasks, one per check type.
     Outputs: data/<tag>_analysis_cdc.html
              data/<tag>_analysis_lint.html
              data/<tag>_analysis_spgdft.html
   - Reference: shared/report_compiler.md

6. Send email(s)
   - Single check type:
     python3 script/genie_cli.py --send-analysis-email <tag> --check-type <check_type>
   - Full static check (3 separate emails):
     python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
     python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
     python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft
```

### What Main Session Does vs What Agents Do

| Task | Who Does It |
|------|-------------|
| Monitor task completion | Foreground haiku agent (Task tool) — polls/sleeps internally |
| Read CDC/RDC reports | Agent via Task tool |
| Read Lint reports | Agent via Task tool |
| Read SpgDFT reports | Agent via Task tool |
| Parse violations | Agent via Task tool |
| Compile HTML report | Report Compiler agent via Task tool |
| Send email(s) | Main session (1 per check type) |

---

## Report Compilation

After all agents complete, compile results:

```markdown
## Static Check Analysis Report

**Tag:** <tag>
**IP:** <ip>
**Tree:** <ref_dir>
**Check Type:** <check_type>

---

### CDC/RDC Analysis

#### Preconditions
| Type | Count | Signals |
|------|-------|---------|
| Inferred Clocks | X | sig1, sig2 |
| Inferred Resets | X | rst1 |
| Unresolved Modules | X | mod1, mod2 |
| Blackbox Modules | X | bb1 |

#### Library Additions Required
```
/proj/glkcmd1_lib/.../xyz.v
/proj/glkcmd1_lib/.../abc.v
```

#### Violations Summary
| Total | RSMU/DFT (skipped) | Focus |
|-------|-------------------|-------|
| 156 | 120 | 36 |

#### Top Violations Analyzed
| ID | Type | Signal | RTL Location | Analysis | Fix |
|----|------|--------|--------------|----------|-----|
| no_sync_15 | no_sync | ctrl_sig | module.sv:45 | Missing sync | Add 2-flop |

---

### Lint Analysis

#### Violations Summary
| Total | RSMU/DFT (skipped) | Focus |
|-------|-------------------|-------|
| 45 | 30 | 15 |

#### Top Violations Analyzed
| Code | Signal | File:Line | Analysis | Fix |
|------|--------|-----------|----------|-----|
| W_UNDRIVEN | data_out | mod.sv:45 | Not assigned | Tie off |

---

### SpgDFT Analysis

#### Blackbox Modules
| Module | Library Path |
|--------|--------------|
| xyz_cell | /proj/.../xyz.v |

#### Violations Summary
| Total | RSMU/DFT (skipped) | Focus |
|-------|-------------------|-------|
| 85 | 60 | 25 |

#### Top Violations Analyzed
| Rule | Signal | Module | Analysis | Fix |
|------|--------|--------|----------|-----|
| UndrivenOutPort | Tdr_xyz | umcdat | TDR port | Tie to 0 |

---

### Suggested Fixes Summary

#### 1. CDC/RDC Constraint Additions
**File:** `src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl`
```tcl
netlist clock clk_a -group CLK_GROUP
netlist reset rst_n -active_low
```

#### 2. SpgDFT Parameter Additions
**File:** `src/meta/tools/spgdft/variant/<ip>/project.params`
```
# SpgDFT parameters and waivers go here
```

#### 3. Library Additions (umc_top_lib.list)
```
/proj/glkcmd1_lib/a0/library/lib_0.0.1_h110/xyz.v
```

#### 4. RTL Fixes Required
- module.sv:45 - Add synchronizer for ctrl_sig
- other.sv:100 - Tie off data_out

#### 5. CDC/RDC Waivers (if justified)
```tcl
cdc report crossing -id no_sync_99 -comment "Static signal" -status waived
```

### Configuration File Reference

| Check Type | Config File Path | Purpose |
|------------|------------------|---------|
| **CDC/RDC** | `src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl` | **Constraints** (netlist clock/reset, cdc custom sync, netlist port domain) |
| **CDC/RDC** | `src/meta/tools/cdc0in/variant/<ip>/umc.0in_waiver` | **Waivers** (cdc report crossing ... -severity waived) |
| **SpgDFT** | `src/meta/tools/spgdft/variant/<ip>/project.params` | DFT constraints |
| **Lint** | `src/meta/tools/lint/variant/<ip>/...` (varies by project) | Lint waivers |
```

---

## Token Estimates by Flow

| Flow | Agents | Est. Tokens |
|------|--------|-------------|
| **CDC/RDC** | Precondition + Library + Extractor + 10 RTL | ~35,000 |
| **Lint** | Extractor + N RTL | ~18,000 |
| **SpgDFT** | Precondition + Library + Extractor + N RTL | ~22,000 |
| **Full Static** | All three flows | ~75,000 |

vs Single Agent (~100,000+ tokens)

---

## Final Feedback Flow

**KEY PRINCIPLE:** All analysis output goes into EMAIL, NOT conversation (saves context).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         FEEDBACK FLOW                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. Collect all agent results (JSON files on disk)                       │
│           │                                                              │
│           ▼                                                              │
│  2. SPAWN REPORT COMPILER AGENT(S) via Task tool                         │
│     - Single check: 1 compiler → data/<tag>_analysis_<check>.html       │
│     - full_static_check: 3 compilers IN PARALLEL:                       │
│         data/<tag>_analysis_cdc.html      (CDC/RDC only)                │
│         data/<tag>_analysis_lint.html     (Lint only)                   │
│         data/<tag>_analysis_spgdft.html   (SpgDFT only)                 │
│           │                                                              │
│           ▼                                                              │
│  3. SEND ANALYSIS EMAIL(S) — one per check type                          │
│     Single check:                                                        │
│       python3 script/genie_cli.py --send-analysis-email <tag>           │
│                                   --check-type <check_type>             │
│     Full static check (3 emails):                                        │
│       python3 script/genie_cli.py --send-analysis-email <tag>           │
│                                   --check-type cdc_rdc                  │
│       python3 script/genie_cli.py --send-analysis-email <tag>           │
│                                   --check-type lint                      │
│       python3 script/genie_cli.py --send-analysis-email <tag>           │
│                                   --check-type spg_dft                  │
│           │                                                              │
│           ▼                                                              │
│  4. CONVERSATION OUTPUT (minimal - save context)                         │
│     - Single: "Analysis complete. Email sent."                           │
│     - Full:   "Analysis complete. 3 emails sent (CDC/RDC, Lint, SpgDFT)"│
│     - DO NOT display tables or analysis in conversation                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### What Goes Where

| Content | Email | Conversation |
|---------|-------|--------------|
| Precondition summary table | YES | NO |
| Violation counts | YES | NO |
| RTL analysis details | YES | NO |
| Recommendations | YES | NO |
| Code snippets | YES | NO |
| "Analysis complete" | YES | YES |

### Send Analysis Email

Use genie_cli.py to send the analysis email. Always pass `--check-type`:

```bash
# Single check type:
python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft

# Full static check — run all three (3 separate emails):
python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft
```

HTML file mapping (--check-type → file):
- `cdc_rdc` → `data/<tag>_analysis_cdc.html`
- `lint`    → `data/<tag>_analysis_lint.html`
- `spg_dft` → `data/<tag>_analysis_spgdft.html`

This also reads:
- `data/<tag>_analyze` - Task metadata (ref_dir, ip)
- `assignment.csv` - Debugger email addresses

Email format:
- **Subject:** `[Analysis] CDC_RDC - IP @ tree_name (tag)`
- **Body:** Full HTML report (inline, not attachment)
- **To:** First debugger, **CC:** Other debuggers

### Conversation Output (Minimal - Save Context)

**DO NOT dump full analysis into conversation.** Just confirm:

```
Analysis complete. Email sent to debuggers.
```

That's it. All details are in the email.

---

## Complete Orchestration Flow

```
1. Detect ANALYZE_MODE_ENABLED
         │
         ├── If SKIP_MONITORING=true → skip steps 2-3, go to step 4
         │
2. Spawn ONE background agent to monitor task completion
   │   - Watches data/<tag>_pid → checks process alive
   │   - Main conversation is FREE (no context used)
         │
3. Background agent returns:
   ├── skip_analysis=true  → "Task failed. Skipping analysis." → STOP
   └── skip_analysis=false → proceed
         │
4. READ analyze file: data/<tag>_analyze
   │   - Get: check_type, ref_dir, ip
         │
5. Spawn IN PARALLEL — precondition + extractor agents (USE TASK TOOL):
   ├── Precondition agents (ALWAYS — even if result is clean)
   └── Violation extractor agents (ALWAYS — need counts for skip logic)
         │
6. Collect results → APPLY SKIP LOGIC:
   │
   ├── Library Finder    → skip if unresolved == 0 AND blackbox == 0
   ├── CDC RTL Analyzers → skip if CDC focus_violations == 0
   ├── RDC RTL Analyzers → skip if RDC focus_violations == 0
   ├── Lint RTL Analyzers → skip if Lint focus_violations == 0
   └── SpgDFT RTL Analyzers → skip if SpgDFT focus_violations == 0
         │
7. Spawn whichever RTL analyzer agents are NOT skipped (IN PARALLEL)
   │   - One agent per violation (up to 10 CDC + 10 RDC + N Lint + N SpgDFT)
   │   - If RDC is CLEAN: CDC gets up to 20 slots; if CDC is CLEAN: RDC gets up to 20 slots
   │   - Skipped checks → mark "CLEAN" in report
         │
7.5 Spawn FIX CONSOLIDATOR AGENT(S) via Task tool (IN PARALLEL):
   │   - One consolidator per check type that had RTL analyzers run
   │   - Skip if check was CLEAN (focus_violations == 0)
   │   - cdc_rdc → 2 in parallel: consolidated_cdc.json + consolidated_rdc.json
   │   - lint    → 1: consolidated_lint.json
   │   - spg_dft → 1: consolidated_spgdft.json
   │   - full_static_check → up to 4 in parallel (all non-clean types)
   │   - Reference: shared/fix_consolidator.md
         │
8. SPAWN REPORT COMPILER AGENT(S) via Task tool (IN PARALLEL for full_static_check):
   │
   │   For full_static_check — 3 compilers in parallel:
   │   ├── Task(report_compiler, check_type=cdc_rdc) → data/<tag>_analysis_cdc.html
   │   ├── Task(report_compiler, check_type=lint)    → data/<tag>_analysis_lint.html
   │   └── Task(report_compiler, check_type=spg_dft) → data/<tag>_analysis_spgdft.html
   │
   │   For single check type (cdc_rdc / lint / spg_dft) — 1 compiler:
   │   └── Task(report_compiler, check_type=<check>) → data/<tag>_analysis_<check>.html
         │
9. SEND EMAIL(S):
   │
   │   For full_static_check (3 emails):
   │   python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
   │   python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
   │   python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft
   │
   │   For single check type (1 email):
   │   python3 script/genie_cli.py --send-analysis-email <tag> --check-type <check_type>
         │
10. SAY: "Analysis complete. 3 emails sent (CDC/RDC, Lint, SpgDFT)."
         or "Analysis complete. Email sent."  (for single check types)
```

---

## Error Handling

- If an agent fails, note the failure in the report
- Continue with other agents
- For critical failures (no report found), abort that flow
- Always compile partial results
- Always send email with whatever was collected

### Output File Validation — Apply After EVERY Sub-Agent Call

After each sub-agent returns, immediately verify its required output file exists:
```bash
ls <base_dir>/data/<tag>_<expected_output> 2>/dev/null
```

**Expected output files:**

| Agent | Expected File |
|-------|--------------|
| CDC precondition | `<tag>_precondition_cdc.json` |
| CDC extractor | `<tag>_extractor_cdc.json` |
| SPG_DFT extractor | `<tag>_extractor_spgdft.json` |
| Lint extractor | `<tag>_extractor_lint.json` |
| CDC RTL analyzer N | `<tag>_rtl_analysis_cdc_<N>.json` |
| SPG_DFT RTL analyzer N | `<tag>_rtl_analysis_spgdft_<N>.json` |
| Lint RTL analyzer N | `<tag>_rtl_analysis_lint_<N>.json` |
| Library finder | `<tag>_library_finder.json` |
| Fix consolidator (CDC) | `<tag>_consolidated_cdc.json` |
| Fix consolidator (Lint) | `<tag>_consolidated_lint.json` |
| Fix consolidator (SPG_DFT) | `<tag>_consolidated_spgdft.json` |
| Fix implementor (CDC) | `<tag>_fix_applied_cdc.json` |
| Fix implementor (Lint) | `<tag>_fix_applied_lint.json` |
| Fix implementor (SPG_DFT) | `<tag>_fix_applied_spgdft.json` |
| Deep-dive agent N | `<tag>_deepdive_<N>.json` |

**If file is MISSING after agent completes:**
1. Re-invoke the same agent **once** with this prepended to its prompt:
   `"⚠️ RETRY: Your previous run did not write the required output JSON. You MUST call the Write tool to save results before finishing."`
2. If still missing after retry: log `"STEP FAILED: <agent_name> output not written"` in the round report and continue with best-effort.

---

## File Paths Reference

### IP Configuration File

**USE THIS FOR FASTER PATH RESOLUTION:** `config/IP_CONFIG.yaml`

This file contains all report path patterns for each IP family (UMC, OSS, GMC).

**How to use:**
1. Determine IP family from `ip` argument (e.g., `umc9_3` → `umc`, `oss7_2` → `oss`, `gmc13_1a` → `gmc`)
2. Read `config/IP_CONFIG.yaml`
3. Get path pattern from `<ip_family>.reports.<check_type>.path_pattern`
4. Substitute `{tile}` with tile name (default: `umc_top` for UMC, `osssys` for OSS, etc.)

**Example for UMC umc9_3:**
```yaml
# From config/IP_CONFIG.yaml
umc:
  reports:
    cdc:
      path_pattern: "out/linux_*/*/config/*/pub/sim/publish/tiles/tile/{tile}/cad/rhea_cdc/cdc_*_output/cdc_report.rpt"
    rdc:
      path_pattern: "out/linux_*/*/config/*/pub/sim/publish/tiles/tile/{tile}/cad/rhea_cdc/rdc_*_output/rdc_report.rpt"
    lint:
      path_pattern: "out/linux_*/*/config/*/pub/sim/publish/tiles/tile/{tile}/cad/rhea_lint/leda_waiver.log"
    spg_dft:
      path_pattern: "out/linux_*/*/config/*/pub/sim/publish/tiles/tile/{tile}/cad/spg_dft/{tile}/moresimple.rpt"
```

### Default Path Patterns (if config not available)

| Check | Report Path Pattern |
|-------|---------------------|
| CDC | `<ref_dir>/out/linux_*/<ip>/config/*_drop2cad/.../rhea_cdc/cdc_*_output/cdc_report.rpt` |
| RDC | `<ref_dir>/out/linux_*/<ip>/config/*_drop2cad/.../rhea_cdc/rdc_*_output/rdc_report.rpt` |
| Lint | `<ref_dir>/out/linux_*/<ip>/config/*_drop2cad/.../rhea_lint/leda_waiver.log` |
| SpgDFT | `<ref_dir>/out/linux_*/<ip>/config/*_drop2cad/.../spg_dft/*/moresimple.rpt` |
| Liblist | `<ref_dir>/out/linux_*/<ip>/config/*_drop2cad/.../publish_rtl/manifest/umc_top_lib.list` |
| RTL | `<ref_dir>/src/` |
| Library List | `<ref_dir>/out/linux_*/.../publish_rtl/manifest/{tile}_lib.list` |

---

## ANALYZE_FIXER_MODE_ENABLED — Fixer Mode Orchestration

When you detect `ANALYZE_FIXER_MODE_ENABLED` in the output (instead of `ANALYZE_MODE_ENABLED`), follow this extended flow. The fixer mode runs the full analyze pipeline, applies fixes automatically, reruns the check, and loops until violations are zero.

### Signal Format
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
[SKIP_MONITORING=true]   ← present on rerun rounds only
```

### Fixer Mode Flow (per round)

```
Round N:
  ├── STEP 0: Build fix history (if Round N > 1)
  │     Read: data/<tag>_fixer_state → get parent_tag
  │     Trace back ALL previous rounds using parent_tag chain:
  │       Round 1 tag → Round 2 tag → ... → Round N-1 tag
  │     For each previous round, read: data/<prev_tag>_fix_applied_<check_type>.json
  │     Build fix_history object:
  │       {
  │         "<signal_or_file>": [
  │           {
  │             "round": 1,
  │             "tag": "<prev_tag>",
  │             "fix_type": "tie_off",
  │             "fix_action": "assign Tdr_data_out = 8'b0;",
  │             "status": "applied"       ← or "skipped_duplicate" / "requires_manual_review"
  │           }
  │         ]
  │       }
  │     Pass fix_history into EVERY RTL analyzer agent prompt (see Step 1 below)
  │     If Round 1: fix_history = {} (empty)
  │
  ├── STEP 1: Run analyze pipeline
  │     ├── If SKIP_MONITORING not set: spawn background monitor, wait for task completion
  │     ├── Precondition agent (CDC/RDC, SPG_DFT only)
  │     ├── Violation extractor agent
  │     ├── RTL analyzer agents (parallel, by file/module) ← include fix_history in prompt
  │     │     For each agent, add to prompt:
  │     │       fix_history: <fix_history entries relevant to this file/signal>
  │     │       If a violation's signal appears in fix_history:
  │     │         → Note "previously attempted: <fix_action> in Round <N>"
  │     │         → If violation still persists: reason WHY fix did not resolve it
  │     │         → Recommend a DIFFERENT approach (do not repeat the same fix)
  │     │         → If two rounds have failed: recommend investigate
  │     ├── Library finder agent (if unresolved modules > 0)
  │     └── Fix consolidator agent
  │
  ├── STEP 2: Spawn Fix Implementor agent(s)
  │     Read: config/analyze_agents/shared/fix_implementor.md
  │     Inputs: tag, check_type, ref_dir, ip, base_dir, round=N
  │     Applies: constraints to project.0in_ctrl.v.tcl (CDC/RDC constraint fixes)
  │              waivers to umc.0in_waiver (CDC/RDC waiver fixes)
  │              rtl_fix to src/rtl/**/*.sv (CDC/RDC and Lint)
  │              rtl_fix to path as-is (SPG_DFT)
  │              tie_off to src/rtl/**/*.sv (Lint)
  │              constraints to project.params (SPG_DFT)
  │              library entries to umc_top_lib.list (if needed)
  │     Logs:   investigate items → requires_investigation list
  │     Output: data/<tag>_fix_applied_<check_type>.json
  │
  │     ⚠️  FULL_STATIC_CHECK DUPLICATE PREVENTION:
  │     For full_static_check, multiple check types may recommend the same RTL fix
  │     for the same src/rtl/ file. To prevent duplicate insertions:
  │     → Run Fix Implementors SEQUENTIALLY (NOT in parallel):
  │         1. CDC/RDC Fix Implementor → wait for completion
  │         2. Lint Fix Implementor    → wait for completion
  │         3. SPG_DFT Fix Implementor → wait for completion
  │     Each implementor reads the actual file state before applying,
  │     so it naturally detects fixes already applied by the previous implementor.
  │
  ├── STEP 2b: Spawn Deep-Dive agents for investigate items
  │     Read: data/<tag>_fix_applied_<check_type>.json → get requires_investigation list
  │     If requires_investigation is non-empty:
  │       For each item (index N), spawn ONE Deep-Dive Agent in parallel:
  │         Read: config/analyze_agents/shared/deep_dive_agent.md
  │         Inputs: index=N, signal, investigation_context, check_type, ref_dir, ip, tag, base_dir, round
  │         Agent researches hierarchy, determines concrete fix, applies it directly
  │         Output: data/<tag>_deepdive_<N>.json
  │       Wait for all deep-dive agents to complete
  │       Sum deep_dive_applied = count of deepdive JSONs where fix_applied=true
  │     Else: deep_dive_applied = 0
  │
  ├── STEP 3: Compile round report
  │     Read: all agent JSON outputs + fix_applied JSON
  │     Generate: data/<tag>_analysis_fixer_round<N>.html
  │     Contents:
  │       - Round N summary: total violations, focus violations, fixes applied
  │       - Violation cards (same as analyze mode)
  │       - Fixes applied section: list of constraints/RTL changes made
  │       - Pending manual fixes: rtl_fix items not auto-applied
  │
  ├── STEP 4: Send round email
  │     Subject: "[Fixer Round N/MAX] <check_type> - <ip> @ <ref_dir> (<tag>)"
  │     Body: round report HTML
  │     Recipients: from data/<tag>_analysis_email
  │
  └── STEP 5: Check if done

        ## VIOLATION COUNT RULES — READ THIS CAREFULLY

        **Single source of truth: the extractor JSON for THIS round.**

        - `total_unwaived`  = what the lint tool reported in "Unwaived" section
        - `filtered_count`  = violations skipped due to LOW_RISK patterns (rsmu/dft/jtag/etc.)
        - `focus_violations` = total_unwaived - filtered_count  (what agents analyzed)

        **❌ NEVER compute remaining = previous_focus - fixes_applied**
        **✅ ALWAYS read focus_violations from THIS round's extractor JSON**

        If round 2 extractor says focus=55 and round 1 said focus=65 → 10 were resolved.
        Do NOT do: 65 - 8 fixes = 57. Trust the tool rerun output, not arithmetic.

        Numbers that differ between rounds are EXPECTED — the tool reruns fresh each time.
        Numbers that differ within the same round (extractor vs report compiler) are a BUG —
        the report compiler must use extractor JSON numbers verbatim, never recount.

        Read: data/<tag>_fix_applied_<check_type>.json
          → constraints_applied   (how many constraints were written this round)
          → rtl_fixes_applied     (how many RTL edits were made this round)
          → tie_offs_applied      (how many tie-offs were made this round)
          → deep_dive_applied     (how many deep-dive fixes were made this round)
        Read: data/<tag>_extractor_<check_type>.json
          → focus_violations      (violations THIS round — use this as remaining count)

        TERMINATION CONDITIONS (check in order):

        1. CLEAN: focus_violations == 0
           → Go to FINAL SUMMARY with result=CLEAN

        2. STALLED: constraints_applied == 0 AND rtl_fixes_applied == 0 AND tie_offs_applied == 0 AND deep_dive_applied == 0
           → No new fixes were applied this round (including deep-dive) — remaining
             violations are not auto-fixable (all unresolved investigate items)
           → Do NOT rerun — it would produce the same result
           → Go to FINAL SUMMARY with result=STALLED

        3. MAX ROUNDS: round >= MAX_ROUNDS
           → Go to FINAL SUMMARY with result=MAX_ROUNDS_REACHED

        4. Otherwise (fixes were applied AND violations remain AND rounds left):
           → Rerun static check (see Rerun below)
           → Loop to Round N+1
```

### Triggering a Rerun

After applying fixes, trigger the next round by running the static check again.

1. Read `data/<tag>_fixer_state` — get ALL original flags:
```
original_instruction=<instruction>
original_ref_dir=<ref_dir>
original_ip=<ip>
original_check_type=<check_type>
use_xterm=true/false
email_to=<email or empty>
```

2. Build the rerun command — **MUST restore original flags exactly**:
```bash
cd <base_dir>

# Build flags from fixer_state:
XTERM_FLAG=""      # if use_xterm=true → "--xterm"
EMAIL_FLAG=""      # always "--email"
TO_FLAG=""         # if email_to is set → "--to <email_to>"

python3 script/genie_cli.py \
  -i "<original_instruction>" \
  --execute \
  $XTERM_FLAG \
  $EMAIL_FLAG \
  $TO_FLAG
```

**Example** — if original run had `--xterm --email --to user@amd.com`:
```bash
python3 script/genie_cli.py -i "run lint at /proj/... for umc9_3" --execute --xterm --email --to user@amd.com
```

3. Note the new tag from the output
4. Write updated fixer state to `data/<new_tag>_fixer_state` — **carry forward ALL flags**:
```
original_ref_dir=<ref_dir>
original_ip=<ip>
original_check_type=<check_type>
original_instruction=<instruction>
round=<N+1>
max_rounds=5
parent_tag=<previous_tag>
use_xterm=<same as before>
email_to=<same as before>
```
5. Once the new check completes, emit internally:
```
ANALYZE_FIXER_MODE_ENABLED
TAG=<new_tag>
CHECK_TYPE=<check_type>
REF_DIR=<ref_dir>
IP=<ip>
LOG_FILE=<base_dir>/runs/<new_tag>.log
SPEC_FILE=<base_dir>/data/<new_tag>_spec
MAX_ROUNDS=5
FIXER_ROUND=<N+1>
SKIP_MONITORING=true
```
6. Continue the fixer flow for Round N+1

### Final Summary (when done)

When any termination condition is met (CLEAN, STALLED, or MAX_ROUNDS_REACHED):

1. Collect data from all rounds (read each round's `_fix_applied_*.json` and `_extractor_*.json`)
   **Pull violation counts from extractor JSON only — never compute from arithmetic**
2. Generate final summary HTML `data/<first_tag>_fixer_summary.html`:
   ```
   Header: Analyze-Fixer Summary — <check_type> — <ip>

   Rounds Table:
   | Round | Tag | Total Violations | Focus Violations | Constraints Applied | RTL Fixes |
   |-------|-----|-----------------|-----------------|--------------------|-----------|
   | 1     | ... | 153             | 10              | 3                  | 0         |
   | 2     | ... | 42              | 8               | 2                  | 0         |
   | 3     | ... | 0               | 0               | 0                  | 0         |

   Result (one of):
     ✅ CLEAN       — All focus violations resolved. No further action needed.
     ⚠️ STALLED     — N focus violations remain. No new auto-fixable constraints found this
                      round. Remaining violations require manual RTL changes or investigation.
                      (Low-risk patterns such as rsmu/dft/scan/jtag were excluded from analysis.)
     🔴 MAX ROUNDS  — Stopped after 5 rounds. N focus violations still remain.

   All Fixes Applied (across all rounds):
   - [Round 1] cdc custom sync SDFSYNC4... (resolves 114 violations)
   - [Round 1] netlist constant cfg_mode -value 0 (resolves 2 violations)
   - [Round 2] netlist clock clk_fast -group fast_clk (resolves 8 violations)

   Manual Fixes Still Required (rtl_fix / investigate — NOT auto-applied):
   - <signal>: <why> → <suggested RTL fix>
   - <signal>: <why> → requires investigation

   Violations Excluded as Low-Risk (not counted in focus violations):
   - rsmu/dft/scan/jtag/bist/tdr patterns filtered by extractor — review separately if needed
   ```

3. Send final summary email:
   - Subject (CLEAN):   `[Fixer ✅ CLEAN] <check_type> - <ip> — <N> rounds, violations: X→0`
   - Subject (STALLED): `[Fixer ⚠️ STALLED] <check_type> - <ip> — <N> rounds, N violations remain (manual fix needed)`
   - Subject (MAX):     `[Fixer 🔴 MAX ROUNDS] <check_type> - <ip> — 5 rounds, N violations remain`
   - Body: summary HTML

4. Say: `"Analyze-fixer complete. <N> rounds run. Violations: <start>→<end>. Result: <CLEAN|STALLED|MAX_ROUNDS_REACHED>. Email sent."`

### Key Rules

- **Zero waivers**: Fix Implementor never applies waivers — only constraints and RTL fixes
- **Backup first**: Every file modified is backed up as `<file>.bak_<tag>` before editing
- **p4 edit**: ONLY for constraint/meta files (`src/meta/tools/...`) — sequence: backup → p4 edit → edit. RTL files (`src/rtl/...`) do NOT use p4 edit — sequence: backup → edit directly.
- **Max 5 rounds**: Stop after 5 rounds even if violations remain — log remaining as manual
- **Email every round**: Send per-round email, not just at the end
- **New tag per rerun**: Each static check rerun generates its own independent tag
