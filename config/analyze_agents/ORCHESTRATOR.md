# Analyze Mode Orchestrator Guide

This guide tells the main Claude session how to orchestrate the agent teams for static check analysis.

---

## CRITICAL: MUST USE TASK TOOL TO INVOKE AGENTS

**DO NOT analyze reports directly in the main session.** You MUST use the Task tool to spawn specialized agents.

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

1. **Background monitoring** - Spawn ONE background agent to monitor task completion
2. **Live analysis** - When complete, run analysis in main conversation (NOT background)
3. **Compile & email** - Generate HTML and send email
4. **Minimal output** - Just say "Analysis complete. Email sent."

### Why This Approach?

| Phase | Mode | Why |
|-------|------|-----|
| **Monitoring** | Background | Task can take hours, don't waste context waiting |
| **Analysis** | Live | Quick, immediate results, easy to compile |

### Flow

```
1. Detect ANALYZE_MODE_ENABLED
         │
2. Spawn BACKGROUND agent to monitor task completion
         │ (main conversation free, no context used)
         │
3. Background agent returns with status:
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
7. Compile HTML report from all agent outputs
   - Write to data/<tag>_analysis.html
               │
8. Send email with HTML report
               │
9. Say: "Analysis complete. Email sent."
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
    │   (up to 5)       │          │   (up to 5)       │          │                   │
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
              │    - Parse unwaived     │
              │    - Filter RSMU/DFT    │
              └────────────┬────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
  ┌────────────┐    ┌────────────┐    ┌────────────┐
  │ Lint RTL   │    │ Lint RTL   │    │ Lint RTL   │
  │ Analyzer   │    │ Analyzer   │    │ Analyzer   │
  │ (viol 1)   │    │ (viol 2)   │    │ (viol N)   │
  │ lint/rtl_  │    │ lint/rtl_  │    │ lint/rtl_  │
  │ (sonnet)   │    │ (sonnet)   │    │ (sonnet)   │
  └────────────┘    └────────────┘    └────────────┘
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

## Background Monitoring Agent

Spawn ONE background agent to monitor task completion:

```python
Task tool:
  subagent_type: "general-purpose"
  model: "haiku"
  description: "Monitor static check completion"
  run_in_background: true
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
       - Check if spec file exists and has content
       - Look for ERROR indicators in spec file:
         - "Error" or "ERROR"
         - "Failed" or "FAILED"
         - "error:" or "failed:"
         - Empty file or file not found

       **If spec file shows ERROR or is missing:**
       - Return: status="failed", skip_analysis=true
       - Message: "Task failed - spec file shows error, skipping analysis"

       **If spec file looks OK (has valid output):**
       - Return: status="complete", skip_analysis=false
       - Message: "Task completed successfully, proceed with analysis"

    **RETURN IMMEDIATELY after step 2.**

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
| Lint RTL Analyzer (violation N) | `data/<tag>_rtl_lint_<N>.json` |
| SpgDFT RTL Analyzer (violation N) | `data/<tag>_rtl_spgdft_<N>.json` |
| Library Finder | `data/<tag>_library_finder.json` |

Where `<base_dir>` is the genie_cli working directory (e.g., `/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent`).

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

### CRITICAL: Include Permissions + Storage Info in Every Agent Prompt

**Every Task prompt MUST start with this block:**

```
**PERMISSIONS - READ THIS FIRST:**
You have FULL READ ACCESS to all files under /proj/.
Do NOT ask for permission - just read files directly using the Read tool.
Do NOT prompt the user for file access confirmation.
Proceed immediately with reading any file paths under /proj/.

**OUTPUT STORAGE:**
Write your JSON findings to: <base_dir>/data/<tag>_<agent_file>.json
Use the Write tool to save your output. Do NOT just return results in text.
```

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
# Repeat with model="haiku" for RDC:   output → data/<tag>_rtl_rdc_<N>.json
# Repeat with model="sonnet" for Lint:  output → data/<tag>_rtl_lint_<N>.json
# Repeat with model="sonnet" for SpgDFT: output → data/<tag>_rtl_spgdft_<N>.json
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

### Reference Documents

The agent instruction files provide guidance on WHAT to look for:

| Check | Reference | Key Points |
|-------|-----------|------------|
| CDC/RDC Precondition | `cdc_rdc/precondition_agent.md` | **BOTH** CDC+RDC Section 2, inferred clks/rsts, unresolved |
| CDC/RDC Violations | `cdc_rdc/violation_extractor.md` | **BOTH** CDC Section 3 + RDC Section 5, filter RSMU/DFT |
| CDC/RDC RTL | `cdc_rdc/rtl_analyzer.md` | Find signal, check sync, recommend fix (for both CDC & RDC violations) |
| Lint Violations | `lint/violation_extractor.md` | Parse unwaived section |
| Lint RTL | `lint/rtl_analyzer.md` | Check undriven ports |
| SpgDFT Precondition | `spgdft/precondition_agent.md` | Blackbox modules |
| SpgDFT Violations | `spgdft/violation_extractor.md` | Parse moresimple.rpt, **ERROR severity only** |
| SpgDFT RTL | `spgdft/rtl_analyzer.md` | TDR ports, tie-offs |
| Library Search | `shared/library_finder.md` | Find lib.list, search for modules |
| HTML Report | `shared/report_compiler.md` | Beautiful HTML template |

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
   │ SpgDFT RTL Analyzers    │ SpgDFT focus_violations == 0             │
   │ CDC/RDC Precondition    │ NEVER skip                                │
   │ SpgDFT Precondition     │ NEVER skip                                │
   └─────────────────────────┴───────────────────────────────────────────┘

   When an agent is skipped → mark that section as "CLEAN" in the report.
   Do NOT spawn agents for clean checks.

3. Spawn whichever RTL analyzer agents are NOT skipped (in PARALLEL)
   - CDC: up to 5 violations in parallel
   - RDC: up to 5 violations in parallel
   - Lint: up to N violations in parallel
   - SpgDFT: up to N violations in parallel
   - Library Finder: 1 agent (if unresolved/blackbox > 0)

4. Collect all agent results
   - DO NOT read reports yourself - use agent outputs

5. Compile HTML report(s) using the report_compiler agent via Task tool:
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
| Monitor task completion | Background agent (Task tool) |
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

| Check Type | Config File Path |
|------------|------------------|
| **CDC/RDC** | `src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl` |
| **SpgDFT** | `src/meta/tools/spgdft/variant/<ip>/project.params` |
| **Lint** | `src/meta/tools/lint/variant/<ip>/...` (varies by project) |
```

---

## Token Estimates by Flow

| Flow | Agents | Est. Tokens |
|------|--------|-------------|
| **CDC/RDC** | Precondition + Library + Extractor + 5 RTL | ~22,000 |
| **Lint** | Extractor + 5 RTL | ~18,000 |
| **SpgDFT** | Precondition + Library + Extractor + 5 RTL | ~22,000 |
| **Full Static** | All three flows | ~62,000 |

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
   │   - One agent per violation (up to 5 CDC + 5 RDC + N Lint + N SpgDFT)
   │   - Skipped checks → mark "CLEAN" in report
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
