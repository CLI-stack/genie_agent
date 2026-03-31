# Analyze Mode — Complete Flow Guide

From `run full_static_check in analyze mode` to the final email report.

---

## Entry Points

There are **two ways** to trigger analyze mode:

### Entry Point A — Run + Analyze (`--analyze`)

Run the static check AND analyze when it completes:

```bash
python3 script/genie_cli.py \
  -i "run full_static_check at /proj/rtg_oss_er_feint1/abinbaba/umc_grimlock_Mar18 for umc17_0" \
  --execute --analyze --email
```

### Entry Point B — Analyze Existing Results (`--analyze-only` or analyze instruction)

Skip running the static check — go straight to analyzing an already-completed run:

```bash
# By tag (when you know the tag)
python3 script/genie_cli.py --analyze-only 20260318200049

# By instruction (natural language)
python3 script/genie_cli.py \
  -i "analyze full_static_check at /proj/rtg_oss_er_feint1/abinbaba/umc_grimlock_Mar18 for umc17_0" \
  --execute
```

Supported analyze instructions:
- `analyze cdc_rdc at <dir> for <ip>`
- `analyze cdc_rdc results at <dir> for <ip>`
- `analyze lint at <dir> for <ip>`
- `analyze spg_dft at <dir> for <ip>`
- `analyze full_static_check at <dir> for <ip>`
- `analyze static check results at <dir> for <ip>`

**Key difference:** Entry Point B emits `SKIP_MONITORING=true` — Claude skips the background monitor and goes straight to analysis agents.

---

## 1. The Run Command (Entry Point A)

### What genie_cli does

1. **Tokenizes the instruction** — splits into keywords, matches against `keyword.csv` (257 keywords, including `full_static_check`, `results`)
2. **Matches instruction** — compares token pattern against `instruction.csv` (74 patterns, >50% keyword coverage required)
3. **Extracts arguments** — pulls `ref_dir` (path detection via `os.path.isdir()`), `ip` (from `arguement.csv`), `checkType` (`full_static_check`)
4. **Resolves script** — maps matched instruction to `static_check_unified.csh $refDir $ip $checkType`
5. **Generates a tag** — timestamp-based, e.g., `20260318200049`
6. **Writes run script** to `runs/<tag>.csh`
7. **Launches detached** — `nohup csh runs/<tag>.csh &`, saves PID to `data/<tag>_pid`
8. **Prints ANALYZE_MODE_ENABLED signal:**

```
ANALYZE_MODE_ENABLED
TAG=20260318200049
CHECK_TYPE=full_static_check
REF_DIR=/proj/rtg_oss_er_feint1/abinbaba/umc_grimlock_Mar18
IP=umc17_0
LOG_FILE=runs/20260318200049.log
SPEC_FILE=data/20260318200049_spec
```

9. **Writes** `data/<tag>_analyze` with the same metadata (persists for `--send-analysis-email`)
10. **Creates** `data/<tag>_email` flag file with recipient list (from `assignment.csv`)

### Entry Point B signal (analyze-only)

Same signal, with one extra line:

```
ANALYZE_MODE_ENABLED
TAG=20260330202812
CHECK_TYPE=full_static_check
REF_DIR=/proj/rtg_oss_er_feint1/abinbaba/umc_grimlock_Mar30071651
IP=umc17_0
LOG_FILE=runs/20260330202812.log
SPEC_FILE=data/20260330202812_spec
SKIP_MONITORING=true
```

`SKIP_MONITORING=true` → Claude skips steps 2–3 entirely and jumps to step 4.

---

## 2. Background Monitor Phase (Entry Point A only)

Claude immediately spawns a **haiku** agent in the background:

```
Main conversation: FREE (no context consumed waiting)
Background agent: watching task completion
```

### What the monitor does

Every 15–30 seconds:

```
1. ls data/<tag>_pid         → does PID file exist?
   NO  → task ended, go to step 2
   YES → read PID, run: ps -p <PID> -o pid=
         process still running? → wait, repeat
         process gone?          → go to step 2

2. Read data/<tag>_spec
   Missing or empty           → status=failed,   skip_analysis=true
   Contains ERROR/FAILED      → status=failed,   skip_analysis=true
   Has valid content          → status=complete,  skip_analysis=false
```

### skip_analysis gate

| Value | Meaning | Next action |
|-------|---------|-------------|
| `true` | Tool run failed (compile error, license timeout, crash) | Stop. Say "Task failed. Skipping analysis." |
| `false` | Tool ran successfully, reports available | Proceed to analysis |

**This gate separates tool health from RTL cleanliness.** A crashed tool has `skip_analysis=true`. A tool that ran cleanly but found 0 violations has `skip_analysis=false`.

> **Entry Point B skips this entire phase.** `SKIP_MONITORING=true` means the check already completed — no monitor needed.

---

## 3. Static Check Tool Running (background, Entry Point A only)

While the monitor waits, `static_check_unified.csh` runs the actual EDA tools:

| Check | Tool | Report |
|-------|------|--------|
| CDC | Questa CDC (`0-in`) | `cdc_report.rpt` |
| RDC | Questa RDC (`0-in`) | `rdc_report.rpt` |
| Lint | Leda / SpyGlass | `leda_waiver.log` |
| SpgDFT | SpyGlass DFT | `moresimple.rpt` |

On completion, the script:
- Writes results summary to `data/<tag>_spec`
- For full_static_check: also writes `data/<tag>_spg_dft_email.spec`, `data/<tag>_cdc_rdc_email.spec`, `data/<tag>_lint_email.spec`
- Deletes `data/<tag>_pid` (signals monitor that process ended)

---

## 4. Analysis Phase — Parallel Agent Spawning

Once monitor returns `skip_analysis=false` (or `SKIP_MONITORING=true` from Entry Point B), Claude reads `data/<tag>_analyze` to get `check_type`, `ref_dir`, `ip`.

For `full_static_check`, **ALL three flows run**. Agents are spawned in parallel:

### Wave 1 — Precondition + Extractor (always run)

```
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│  CDC/RDC             │   │  Lint                │   │  SpgDFT              │
│  Precondition        │   │  Violation           │   │  Precondition        │
│  (haiku)             │   │  Extractor           │   │  (haiku)             │
│                      │   │  (sonnet)            │   │                      │
│  Reads:              │   │                      │   │  Reads:              │
│  - cdc_report.rpt    │   │  Reads:              │   │  - moresimple.rpt    │
│    Section 2         │   │  - leda_waiver.log   │   │                      │
│  - rdc_report.rpt    │   │                      │   │  Extracts:           │
│    Section 3         │   │  Extracts:           │   │  - BlackboxModule    │
│                      │   │  - Error violations  │   │    entries only      │
│  Extracts:           │   │  - Filters rsmu/dft  │   │                      │
│  - Inferred clocks   │   │  - Up to 10 focus    │   │  Returns:            │
│  - Inferred resets   │   │                      │   │  - blackbox count    │
│  - Unresolved mods   │   │                      │   │  - module names      │
│  - Blackbox mods     │   │                      │   │  - needs_library?    │
│                      │   │                      │   │                      │
│  ALSO reads          │   │                      │   │                      │
│  constraint file     │   │                      │   │                      │
│  BEFORE suggesting   │   │                      │   │                      │
│  any fix             │   │                      │   │                      │
└──────────────────────┘   └──────────────────────┘   └──────────────────────┘

┌──────────────────────┐                              ┌──────────────────────┐
│  CDC/RDC             │                              │  SpgDFT              │
│  Violation           │                              │  Violation           │
│  Extractor           │                              │  Extractor           │
│  (sonnet)            │                              │  (sonnet)            │
│                      │                              │                      │
│  Reads:              │                              │  Source of truth:    │
│  - cdc_report.rpt    │                              │  Does NOT re-parse   │
│    Section 3         │                              │  moresimple.rpt      │
│  - rdc_report.rpt    │                              │                      │
│    Section 5         │                              │  Reads spec file:    │
│                      │                              │  full_static_check:  │
│  Filters:            │                              │  <tag>_spg_dft_      │
│  - ERROR only        │                              │  email.spec          │
│  - rsmu/rdft/dft_    │                              │  Individual run:     │
│    jtag/scan/bist    │                              │  <tag>_spec          │
│                      │                              │                      │
│  Bucket coverage:    │                              │  Parses:             │
│  - Groups by type    │                              │  - Summary table     │
│  - 2-3 per bucket    │                              │  - "Unfiltered Error │
│  - All types covered │                              │    Details:" section │
└──────────────────────┘                              └──────────────────────┘
```

> **SpgDFT Extractor important:** It reads the pre-computed "Unfiltered Error Details:" section from the spec file — NOT by re-parsing moresimple.rpt. The run script already determines what is filtered vs unfiltered. This avoids re-implementing the filter logic and guarantees the correct violations are extracted.

---

## 5. Skip Logic Gate

After Wave 1 completes, Claude evaluates each check before spawning RTL analyzers:

```
CDC/RDC Extractor result:
  focus_violations == 0? → SKIP CDC RTL Analyzers → mark "CDC CLEAN" in report
  focus_violations  > 0? → spawn CDC RTL Analyzers (up to 5 violations)

  focus_violations == 0? → SKIP RDC RTL Analyzers → mark "RDC CLEAN" in report
  focus_violations  > 0? → spawn RDC RTL Analyzers (up to 5 violations)

Lint Extractor result:
  focus_violations == 0? → SKIP Lint RTL Analyzers → mark "Lint CLEAN" in report
  focus_violations  > 0? → spawn Lint RTL Analyzers (up to N violations in parallel)

SpgDFT Extractor result:
  focus_violations == 0? → SKIP SpgDFT RTL Analyzers → mark "SpgDFT CLEAN" in report
  focus_violations  > 0? → spawn SpgDFT RTL Analyzers (up to N violations in parallel)

CDC/RDC Precondition result:
  unresolved == 0 AND blackbox == 0? → SKIP Library Finder
  unresolved  > 0 OR  blackbox  > 0? → spawn Library Finder

SpgDFT Precondition result:
  needs_library_search == false? → SKIP Library Finder
  needs_library_search == true?  → spawn Library Finder
```

**Precondition agents are NEVER skipped** — even a "0 inferred, all clean" result belongs in the report.

---

## 6. Wave 2 — RTL Analyzer Agents (parallel, per violation)

One agent per selected violation, all running in parallel:

### CDC/RDC RTL Analyzer (haiku, per violation)

For each focus violation:

1. **Checks LEARNING.md first** — if matching past violation found, applies known fix immediately
2. **Finds RTL file** — `grep -r <signal_name> src/ --include="*.sv" --include="*.v"`
3. **Understands the signal deeply:**
   - Declaration type, width, direction
   - What logic drives it (combinational vs sequential)
   - Source clock domain (`always @(posedge src_clk)`)
   - Destination clock domain (`always @(posedge dst_clk)`)
   - Signal behavior (frequently toggling vs quasi-static vs pulse)
4. **Searches for existing synchronizers** — `*_d1`, `*_d2`, `*_sync`, `techind_sync`, `async_fifo`, gray code
5. **Deep tech-cell tracing** (for `no_sync` violations): if UMCSYNC/techind_sync wrapper found, traces all the way to the leaf technology cell
   - Reads wrapper → finds what it instantiates → reads that file → finds deepest cell
   - **Module name vs Instance name:** `<MODULE_NAME>  <instance_name>  (.port...)` — always use the MODULE name (first token) for `cdc custom sync`, never the instance name (second token)
   - `-type` selection: `two_dff` for multi-stage cells (e.g., SYNC3/SYNC4), `idff` for dual-clock cells, `dff` for single flop
6. **Reads constraint file** — is it already waived or constrained?
7. **Formulates WHY statement** — not just WHAT, but WHY no synchronizer exists
8. **Assigns risk** — HIGH (real bug) / MEDIUM (needs constraint) / LOW (quasi-static/test-only)
9. **Recommends fix:**
   - `rtl_fix` — real CDC bug, missing synchronizer
   - `waiver` — quasi-static/test-only signal, safe with justification
   - `constraint` — tool needs clock/reset hint or tech-cell registration

### Lint RTL Analyzer (sonnet, per violation)

For each unwaived error violation:

1. **Checks lint/LEARNING.md first**
2. **Reads RTL at flagged line** (±20 lines context)
3. **Asks:** Is it in a disabled `generate` block? Is it a DFT/TDR port by name? Is parent connecting it? Is it legacy/future-use?
4. **Checks lint waivers file** for existing waivers
5. **Fix types:** `rtl_fix` / `tie_off` / `filter`

### SpgDFT RTL Analyzer (sonnet, per violation)

For each ERROR violation (non-blackbox):

1. **Checks spgdft/LEARNING.md first**
2. **Identifies violation type from message:**
   - "not disabled" + "test-mode" + async/set/reset → async signal not disabled → `SPGDFT_PIN_CONSTRAINT`
   - "not controlled by testclock" → clock not controllable → SGDC constraint
   - "undriven" + port → undriven port → tie-off or filter
3. **Reads `project.params`** for existing constraints/waivers
4. **Fix types:** `rtl_fix` / `tie_off` / `SPGDFT_PIN_CONSTRAINT` / `sgdc_constraint` / `filter`

### Library Finder (haiku, once)

If unresolved/blackbox modules found:
1. **Finds lib.list** — checks manifest, then SpgDFT params, then CDC lib.list
2. **For each blackbox module:** `grep -l "module <name>" <library_files>`
3. **Returns:** library path, whether it's already in lib.list, and what to add

---

## 7. File-Based Intermediate Storage

Each agent writes its JSON findings to disk. The report compiler reads from disk — not from context.

### Naming Convention

| File | Written by |
|------|-----------|
| `data/<tag>_precondition_cdc.json` | CDC/RDC Precondition Agent |
| `data/<tag>_precondition_spgdft.json` | SpgDFT Precondition Agent |
| `data/<tag>_extractor_cdc.json` | CDC/RDC Violation Extractor |
| `data/<tag>_extractor_lint.json` | Lint Violation Extractor |
| `data/<tag>_extractor_spgdft.json` | SpgDFT Violation Extractor |
| `data/<tag>_rtl_cdc_<N>.json` | CDC RTL Analyzer (one per violation, N=1,2,3…) |
| `data/<tag>_rtl_rdc_<N>.json` | RDC RTL Analyzer (one per violation) |
| `data/<tag>_rtl_lint_<N>.json` | Lint RTL Analyzer (one per violation) |
| `data/<tag>_rtl_spgdft_<N>.json` | SpgDFT RTL Analyzer (one per violation) |
| `data/<tag>_library_finder.json` | Library Finder Agent |
| `data/<tag>_consolidated_cdc.json` | Fix Consolidator — CDC |
| `data/<tag>_consolidated_rdc.json` | Fix Consolidator — RDC |
| `data/<tag>_consolidated_lint.json` | Fix Consolidator — Lint |
| `data/<tag>_consolidated_spgdft.json` | Fix Consolidator — SpgDFT |

### Why file-based?

- **Survives context interruption** — agent findings not lost if session resets
- **No context bloat** — report compiler reads only what it needs
- **Resumable** — partially completed analyses can be resumed by reading existing JSON files
- **Auditable** — each agent's raw output can be inspected independently

---

## 7.5. Wave 2.5 — Fix Consolidator (NEW)

After all RTL analyzers complete, Claude spawns **Fix Consolidator** agents in parallel — one per check type that had violations. This runs BEFORE the report compiler.

### Why Fix Consolidator?

RTL analyzer agents run independently in parallel, which can cause:

| Problem | Example |
|---------|---------|
| **Duplicate fixes** | Agents 2 and 4 both suggest registering the same tech cell |
| **Instance name confusion** | Agent traces to `hdsync4msfqxss1us_ULVT` (instance name in violation path) instead of the module name |
| **Shallow traces** | Agent stopped at UMCSYNC wrapper instead of tracing to the leaf tech cell |

The Fix Consolidator reads all RTL analyzer JSON outputs for its check type, detects these issues, and writes a single unified, deduplicated fix set.

### When to spawn

| Check type | Spawn condition | Output |
|---|---|---|
| CDC | CDC focus > 0 | `data/<tag>_consolidated_cdc.json` |
| RDC | RDC focus > 0 | `data/<tag>_consolidated_rdc.json` |
| Lint | Lint focus > 0 | `data/<tag>_consolidated_lint.json` |
| SpgDFT | SpgDFT focus > 0 | `data/<tag>_consolidated_spgdft.json` |

Skip consolidator if that check was CLEAN. The report compiler reads consolidated JSON for the recommendations section.

---

## 8. Report Compilation — 3 Parallel Compiler Agents

After Wave 2.5 completes, Claude spawns **3 report compiler agents in parallel** via the Task tool — one per check type. Each compiler is independent and writes its own HTML file.

### HTML Report Style — Light / Clean

| Element | Style |
|---------|-------|
| Background | White `#ffffff` |
| Body font | 15px Arial, dark text `#1a1a1a` |
| Layout | Simple tables, no flowchart/arrows/gates |
| Section headers | Bold with 4px colored left border |
| Violation cards | White bg, thin border + colored left stripe |
| Root cause block | Amber tint `#fffbeb` |
| Fix block | Green tint `#f0fdf4` |
| Code snippets | Light gray `#f5f5f5`, dark text |
| Status badges | Soft red `#fee2e2` / green `#d1fae5` |

Accent colors per check type:
- CDC/RDC → red `#c0392b`
- Lint → amber `#d97706`
- SpgDFT → green `#059669`

### Output files

| Compiler | Reads | Writes |
|----------|-------|--------|
| CDC/RDC compiler | `_precondition_cdc.json`, `_extractor_cdc.json`, `_rtl_cdc_*.json`, `_rtl_rdc_*.json`, `_consolidated_cdc/rdc.json`, `_library_finder.json` | `data/<tag>_analysis_cdc.html` |
| Lint compiler | `_extractor_lint.json`, `_rtl_lint_*.json`, `_consolidated_lint.json` | `data/<tag>_analysis_lint.html` |
| SpgDFT compiler | `_precondition_spgdft.json`, `_extractor_spgdft.json`, `_rtl_spgdft_*.json`, `_consolidated_spgdft.json`, `_library_finder.json` | `data/<tag>_analysis_spgdft.html` |

### Report sections (per compiler)

| Section | Content |
|---------|---------|
| Header | Tag, IP, check type label, tree directory |
| Summary table | Total / Filtered (DFT/RSMU) / Focus / Status badge per check |
| Precondition table | Inferred clocks/resets, unresolved/blackbox modules, action per signal |
| Library additions | Module name → library path (if found) |
| Violations by type | Bucket breakdown with counts |
| Violation cards | Signal, clock crossing, RTL location, root cause (WHY), fix + code snippet |
| Recommendations | High / Medium / Low priority grouped lists |
| Config files | Constraint/params file path for that check type |

**For single check types** (`cdc_rdc`, `lint`, `spg_dft`): only 1 compiler agent is spawned for that check type.

---

## 9. Email — 3 Separate Emails (main session)

For `full_static_check`, three separate emails are sent — one per check type:

```bash
python3 script/genie_cli.py --send-analysis-email <tag> --check-type cdc_rdc
python3 script/genie_cli.py --send-analysis-email <tag> --check-type lint
python3 script/genie_cli.py --send-analysis-email <tag> --check-type spg_dft
```

For single check types, one email:

```bash
python3 script/genie_cli.py --send-analysis-email <tag> --check-type <check_type>
```

### What `--check-type` does

| `--check-type` | HTML file read | Email subject |
|----------------|---------------|---------------|
| `cdc_rdc` | `data/<tag>_analysis_cdc.html` | `[Analysis] CDC/RDC - umc17_0 @ tree_name (tag)` |
| `lint` | `data/<tag>_analysis_lint.html` | `[Analysis] LINT - umc17_0 @ tree_name (tag)` |
| `spg_dft` | `data/<tag>_analysis_spgdft.html` | `[Analysis] SPG/DFT - umc17_0 @ tree_name (tag)` |

### Common behaviour (all emails)

1. Reads `data/<tag>_analyze` — gets ref_dir, ip for subject line
2. Gets recipients from `assignment.csv`
3. Full HTML inline in body (not attachment — AMD mail relay blocks large attachments)
4. First recipient = To, remaining = CC

### Main conversation output

```
Analysis complete. 3 emails sent (CDC/RDC, Lint, SpgDFT).
```

For single check types:

```
Analysis complete. Email sent.
```

Nothing else. All detail is in the emails. This keeps the main conversation context clean.

---

## 10. LEARNING.md System

Three separate knowledge base files, one per check type:

| File | Check type |
|------|-----------|
| `config/analyze_agents/cdc_rdc/LEARNING.md` | CDC/RDC |
| `config/analyze_agents/lint/LEARNING.md` | Lint |
| `config/analyze_agents/spgdft/LEARNING.md` | SpgDFT |

RTL analyzer agents read the relevant LEARNING.md **before** analyzing any violation. If a matching pattern is found, the known fix is applied immediately without re-analyzing from scratch.

**Only the user updates LEARNING.md manually. Agents never write to it.**

---

## 11. Full Flow Summary

```
     Entry Point A                                  Entry Point B
   --analyze flag                           --analyze-only <tag>
 "run full_static_check                  "analyze full_static_check
    ... --analyze"                           at <dir> for <ip>"
          │                                          │
          ▼                                          │
 ┌─────────────────────┐                             │
 │    genie_cli.py     │                             │
 │  Match instruction  │                             │
 │  Generate tag       │                             │
 │  Launch EDA tools   │                             │
 │  (nohup, detached)  │                             │
 │  Print signal:      │                             │
 │  ANALYZE_MODE_      │                             │
 │  ENABLED            │                             │
 └──────────┬──────────┘                             │
            │                                        │ + SKIP_MONITORING=true
            ▼                                        │
 ┌─────────────────────┐                             │
 │  Background Monitor │◄── (Entry A only) ──────────┘ (Entry B skips this)
 │  (haiku agent)      │
 │  Poll _pid every    │
 │  15–30 seconds      │
 └──────────┬──────────┘
            │
     ┌──────┴──────┐
     ▼             ▼
  spec ERROR    spec OK
  skip=true    skip=false
     │             │
     ▼             │
 "Task failed.     │
  Skipping         │
  analysis."       │
  (STOP)           │
                   │
  ┌────────────────┘ OR SKIP_MONITORING=true (Entry B)
  │
  ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │                     Wave 1  (ALL 5 in PARALLEL)                  │
 │                                                                  │
 │  ┌──────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
 │  │ CDC/RDC          │  │ CDC/RDC         │  │ Lint           │  │
 │  │ Precondition     │  │ Violation       │  │ Violation      │  │
 │  │ (haiku)          │  │ Extractor       │  │ Extractor      │  │
 │  │                  │  │ (sonnet)        │  │ (sonnet)       │  │
 │  │ Reads:           │  │                 │  │                │  │
 │  │ cdc_report.rpt   │  │ Reads:          │  │ Reads:         │  │
 │  │ rdc_report.rpt   │  │ CDC Section 3   │  │ leda_waiver    │  │
 │  │ constraint file  │  │ RDC Section 5   │  │ .log           │  │
 │  └──────────────────┘  └─────────────────┘  └────────────────┘  │
 │                                                                  │
 │  ┌──────────────────┐  ┌─────────────────┐                       │
 │  │ SpgDFT           │  │ SpgDFT          │                       │
 │  │ Precondition     │  │ Violation       │                       │
 │  │ (haiku)          │  │ Extractor       │                       │
 │  │                  │  │ (sonnet)        │                       │
 │  │ Reads:           │  │                 │                       │
 │  │ moresimple.rpt   │  │ Reads spec file │                       │
 │  │ (blackbox only)  │  │ (not rpt)       │                       │
 │  └──────────────────┘  └─────────────────┘                       │
 │                                                                  │
 │  Writes: data/<tag>_precondition_*.json                          │
 │          data/<tag>_extractor_*.json                             │
 └────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │                       Skip Logic Gate                            │
 │                                                                  │
 │  CDC  focus == 0  →  CDC CLEAN   (skip CDC RTL analyzers)        │
 │  RDC  focus == 0  →  RDC CLEAN   (skip RDC RTL analyzers)        │
 │  Lint focus == 0  →  Lint CLEAN  (skip Lint RTL analyzers)       │
 │  SpgDFT focus==0  →  DFT CLEAN   (skip SpgDFT RTL analyzers)     │
 │  unresolved/blackbox == 0  →  skip Library Finder                │
 └────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │               Wave 2  (ALL in PARALLEL, one per violation)       │
 │                                                                  │
 │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────┐  │
 │  │ CDC RTL     │  │ RDC RTL     │  │ Lint RTL    │  │ SpgDFT │  │
 │  │ Analyzer×N  │  │ Analyzer×N  │  │ Analyzer×N  │  │ RTL    │  │
 │  │ (haiku)     │  │ (haiku)     │  │ (sonnet)    │  │ Anlyzr │  │
 │  │             │  │             │  │             │  │ (snt)  │  │
 │  │ RTL trace   │  │ RTL trace   │  │ RTL read    │  │        │  │
 │  │ Tech-cell   │  │ constraint  │  │ Waiver chk  │  │ Param  │  │
 │  │ -type select│  │ check       │  │             │  │ check  │  │
 │  └─────────────┘  └─────────────┘  └─────────────┘  └────────┘  │
 │                                                                  │
 │  ┌──────────────────────────────┐                                │
 │  │  Library Finder (haiku)      │  ← only if unresolved/blackbox │
 │  │  Finds missing lib paths     │                                │
 │  └──────────────────────────────┘                                │
 │                                                                  │
 │  Writes: data/<tag>_rtl_<check>_<N>.json                         │
 │          data/<tag>_library_finder.json                          │
 └────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │            Wave 2.5  Fix Consolidators  (PARALLEL)               │
 │                                                                  │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
 │  │ CDC/RDC          │  │ Lint             │  │ SpgDFT         │  │
 │  │ Consolidator     │  │ Consolidator     │  │ Consolidator   │  │
 │  │ (sonnet)         │  │ (sonnet)         │  │ (sonnet)       │  │
 │  │                  │  │                  │  │                │  │
 │  │ Deduplicates     │  │ Deduplicates     │  │ Deduplicates   │  │
 │  │ Checks instance  │  │ fixes across     │  │ fixes across   │  │
 │  │ vs module name   │  │ parallel agents  │  │ parallel agents│  │
 │  │ Verifies tech-   │  │                  │  │                │  │
 │  │ cell traces      │  │                  │  │                │  │
 │  └──────────────────┘  └──────────────────┘  └────────────────┘  │
 │  (only spawned for check types with focus_violations > 0)        │
 │                                                                  │
 │  Writes: data/<tag>_consolidated_<check>.json                    │
 └────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │            Wave 3  Report Compilers  (3 in PARALLEL)             │
 │                                                                  │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
 │  │ CDC/RDC          │  │ Lint             │  │ SpgDFT         │  │
 │  │ Report Compiler  │  │ Report Compiler  │  │ Report Compiler│  │
 │  │                  │  │                  │  │                │  │
 │  │ Reads all        │  │ Reads all        │  │ Reads all      │  │
 │  │ _cdc/rdc JSON    │  │ _lint JSON       │  │ _spgdft JSON   │  │
 │  │                  │  │                  │  │                │  │
 │  │ Writes:          │  │ Writes:          │  │ Writes:        │  │
 │  │ _analysis_       │  │ _analysis_       │  │ _analysis_     │  │
 │  │ cdc.html         │  │ lint.html        │  │ spgdft.html    │  │
 │  └──────────────────┘  └──────────────────┘  └────────────────┘  │
 └────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
              Send 3 separate emails (main session):
              --send-analysis-email <tag> --check-type cdc_rdc
              --send-analysis-email <tag> --check-type lint
              --send-analysis-email <tag> --check-type spg_dft
                              │
                              ▼
        "Analysis complete. 3 emails sent (CDC/RDC, Lint, SpgDFT)."
```

---

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| Two entry points (--analyze vs --analyze-only) | Sometimes check already ran; no need to re-run just to analyze |
| SKIP_MONITORING=true signal | Claude can immediately skip the monitor step when task is already done |
| Precondition always runs | Clean result ("all user-specified") is still useful info in the report |
| skip_analysis separates tool health from RTL cleanliness | A crashed tool is not the same as a clean RTL |
| RTL analyzers skip when 0 violations | No point spawning agents with nothing to analyze |
| One RTL analyzer agent per violation | Parallel execution, each has focused context |
| Fix Consolidator after RTL analyzers | Deduplicates across parallel agents; catches instance-name vs module-name confusion |
| SpgDFT extractor reads spec file | Run script already filters correctly; re-parsing moresimple.rpt would reimplement the filter logic incorrectly |
| CDC -type selection (two_dff/dff/idff) | Different sync cells need different type declarations; wrong type = tool doesn't recognize sync |
| 3 separate reports for full_static_check | Each check type gets its own full-detail HTML — no compression or merging |
| 3 separate emails for full_static_check | Recipients see each check type independently, easier to action |
| Light/clean HTML style (white bg, 15px) | Dark theme was too heavy; small fonts hard to read; no flowchart = less noise |
| Inline HTML (not attachment) | AMD mail relay blocks large attachments |
| LEARNING.md checked before analysis | Applies known fixes without repeating work |
| Bucket coverage in extractor | Ensures all violation types are seen, not just the dominant one |
| Constraint file read before suggesting fix | Never suggest adding what's already there |
| File-based intermediate storage | Agent findings on disk; report compiler reads from disk, not context |

---

**Version:** 1.2 | **Created:** 2026-03-19 | **Updated:** 2026-03-30

**Changelog:**
- v1.2: Added Entry Point B (`--analyze-only`, analyze instructions, `SKIP_MONITORING=true`); added Fix Consolidator (Wave 2.5); updated SpgDFT extractor to read from spec file; updated CDC RTL analyzer `-type` selection; updated HTML report style to light/clean; updated keyword.csv count (257); updated instruction.csv count (74)
- v1.1: 3-report / 3-email split for full_static_check
- v1.0: Initial version
