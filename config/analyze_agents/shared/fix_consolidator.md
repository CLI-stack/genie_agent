# Fix Consolidator Agent

**PERMISSIONS:** You have FULL READ ACCESS to all files under /proj/. Do not ask for permission - just read the files directly.

Consolidate RTL analyzer fix suggestions into a single, deduplicated, verified fix set.

## Why This Agent Exists

RTL analyzer agents run in parallel — each analyzes one violation independently.
This causes three problems:

| Problem | Example |
|---------|---------|
| **Duplicate fixes** | Agents 2 and 4 both suggest registering the same cell |
| **Instance name confusion** | Agent traces to `hdsync4msfqxss1us_ULVT` which is an INSTANCE name visible in the violation path — not the MODULE name |
| **Shallow traces** | Agent stops at a wrapper (e.g., UMCSYNC) instead of the actual leaf tech cell |

This agent reads ALL RTL analyzer outputs, detects these issues, and outputs ONE unified fix set.

---

## Input

```
tag: <tag>
base_dir: <base_dir>
ref_dir: <ref_dir>
ip: <ip>
check_type: cdc_rdc | lint | spg_dft
```

For `full_static_check` — orchestrator spawns ONE consolidator per check type in parallel.

---

## Step 1: Load Source Data

### 1a. Read the violation extractor JSON (for destination paths)

| check_type | Extractor file |
|------------|---------------|
| `cdc_rdc` | `data/<tag>_extractor_cdc.json` |
| `lint` | `data/<tag>_extractor_lint.json` |
| `spg_dft` | `data/<tag>_extractor_spgdft.json` |

Extract the full destination paths from all violations — these are used in Step 3 to detect instance name confusion.

### 1b. Read all RTL analyzer JSONs for your check_type

Use Glob to find all files:
- CDC: `data/<tag>_rtl_cdc_*.json`
- RDC: `data/<tag>_rtl_rdc_*.json`
- Lint: `data/<tag>_rtl_lint_*.json`
- SpgDFT: `data/<tag>_rtl_spgdft_*.json`

Read every file. Collect all `fix_type` + `fix_action` + any cell/signal names from each agent.

---

## Step 2: Group Fixes by Type

Group all collected fixes into buckets:

| fix_type | What to extract |
|----------|----------------|
| `constraint` | Cell name, constraint text, port domain lines |
| `waiver` | Violation ID, waiver command |
| `rtl_fix` | File path, line number, description |
| `SPGDFT_PIN_CONSTRAINT` | Signal path, value (0 or 1) |
| `sgdc_constraint` | Clock/signal name, constraint text |
| `tie_off` | Signal name, value |
| `filter` | Module/signal to filter |
| `investigate` | Flag for human review |

---

## Step 3: Instance Name vs Module Name Detection (ALL check types)

**This is the most critical step.**

### The Problem

RTL analyzer agents read violation destination paths like:
```
...sync_vt.hdsync4msfqxss1us_ULVT.inst_0.IQ_zint
```

An agent may report `hdsync4msfqxss1us_ULVT` as the "cell to register" — but this is actually the **INSTANCE name** of a wrapper module. The real MODULE name (needed for `cdc custom sync`) is one level deeper.

### Detection Rule

For each cell/signal name `X` suggested in a `constraint` fix:

```
Load all violation destination paths from extractor JSON.

For each path P:
  Split P by "."
  segments = P.split(".")

  For each segment S in segments (NOT the last segment):
    IF S == X or S starts with X:
      → X appears as a non-terminal segment (has children after it)
      → X is an INSTANCE name, NOT a module name
      → mark X as "instance_name_confusion = true"
      → discard this fix suggestion

  IF X appears ONLY as the last segment or NOT at all in paths:
    → X is likely the actual MODULE name
    → keep this fix suggestion
```

### Also Flag Shallow Traces

If an agent's suggested cell name is a known wrapper pattern, flag it:

| Wrapper pattern | Issue |
|----------------|-------|
| `UMCSYNC` | Too high — CDC tool ignores wrappers |
| `techind_sync` | May still be a wrapper — check further |
| `SYNC_CELL` | Generic wrapper name |

If the fix_action contains only a wrapper name with no leaf cell → flag as `shallow_trace = true`.

---

## Step 4: Deduplicate

After filtering out instance-name confusions and shallow traces:

### For `constraint` fixes (CDC/RDC):
- Group by **cell module name**
- If multiple agents suggest the same cell → keep ONE entry
- Merge any port domain lines from different agents

### For `waiver` fixes:
- Group by **violation ID**
- If same ID appears multiple times → keep ONE (prefer more specific justification)

### For `SPGDFT_PIN_CONSTRAINT`:
- Group by **signal path**
- If same signal appears multiple times → keep ONE (check value consistency: if agents disagree on 0 vs 1, flag for review)

### For `rtl_fix`:
- Group by **file:line**
- If multiple agents suggest the same location → keep ONE

### For `tie_off`:
- Group by **signal name**
- Deduplicate

---

## Step 5: Calculate Coverage

For each deduplicated fix, calculate how many violations it resolves:

```
For each fix F (cell name C):
  count = number of violation destination paths that contain C
         + number of violations where the fix_action from the agent mentions C

coverage[F] = count
```

Sort fixes by coverage (highest first) — the fix that resolves the most violations appears first.

---

## Step 6: Identify Unresolved Items

After deduplication, check if any violations are not covered by any fix:
- Violations where ALL agents returned `fix_type = investigate`
- Violations where all suggested cells were flagged as instance names (no deeper trace found)

List these as `unresolved` — they need manual investigation.

---

## Output JSON

Write to: `data/<tag>_consolidated_<check>.json`

Where `<check>` = `cdc` | `rdc` | `lint` | `spgdft`

For `cdc_rdc`, write TWO files: `_consolidated_cdc.json` and `_consolidated_rdc.json`

```json
{
  "check_type": "cdc_rdc",
  "tag": "<tag>",
  "ip": "<ip>",
  "summary": {
    "total_rtl_agents": 5,
    "duplicate_fixes_removed": 2,
    "instance_name_confusions_detected": 3,
    "shallow_traces_detected": 1,
    "unified_fixes": 3,
    "unresolved": 0
  },
  "discarded": [
    {
      "agent_index": 2,
      "suggested": "hdsync4msfqxss1us_ULVT",
      "reason": "instance_name_confusion — appears as non-terminal segment in violation paths",
      "evidence": "path contains ...hdsync4msfqxss1us_ULVT.inst_0.IQ_zint"
    },
    {
      "agent_index": 3,
      "suggested": "UMCSYNC",
      "reason": "shallow_trace — wrapper level, CDC tool does not recognize wrappers"
    }
  ],
  "unified_fixes": [
    {
      "fix_type": "constraint",
      "priority": "HIGH",
      "coverage": 114,
      "source_agent": 1,
      "cell_name": "SDFSYNC4QD1AMDBWP*CPDULVT",
      "verification": "module name — does NOT appear as non-terminal in violation paths",
      "fix_action": "cdc custom sync SDFSYNC4QD1AMDBWP*CPDULVT -type two_dff\nnetlist port domain D  -async -clock CP -module SDFSYNC4QD1AMDBWP*CPDULVT\nnetlist port domain Q  -clock CP        -module SDFSYNC4QD1AMDBWP*CPDULVT\nnetlist port domain SI -clock CP        -module SDFSYNC4QD1AMDBWP*CPDULVT",
      "target_file": "src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl",
      "resolves_violations": ["no_sync_11377", "no_sync_82109", "no_sync_20180", "...all no_sync"]
    },
    {
      "fix_type": "waiver",
      "priority": "LOW",
      "coverage": 2,
      "source_agent": 5,
      "fix_action": "cdc report crossing -id series_redundant_95130 -severity waived -message \"Intentional dual FPM paths ...\"\ncdc report crossing -id series_redundant_6522 -severity waived -message \"...\"",
      "target_file": "src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl",
      "resolves_violations": ["series_redundant_95130", "series_redundant_6522"]
    }
  ],
  "unresolved": []
}
```

---

## Output Storage

**MANDATORY — Write your JSON output to disk. Do NOT just return results as text.**

| check_type | Output Files |
|------------|-------------|
| `cdc_rdc` | `data/<tag>_consolidated_cdc.json` + `data/<tag>_consolidated_rdc.json` |
| `lint` | `data/<tag>_consolidated_lint.json` |
| `spg_dft` | `data/<tag>_consolidated_spgdft.json` |

Use the Write tool:
```
Write file: <base_dir>/data/<tag>_consolidated_<check>.json
Content: <your JSON output>
```

The report compiler reads this file for the Recommendations section.
If you do not write it, recommendations will be inconsistent.

---

## Instructions — Mandatory Execution Order

1. Read extractor JSON → collect all violation destination paths
2. Read all RTL analyzer JSONs → collect all fix suggestions
3. Group fixes by type
4. Run instance name detection on each constraint suggestion
5. Flag shallow traces
6. Deduplicate remaining fixes
7. Calculate coverage per fix
8. Identify unresolved violations
9. Write consolidated JSON

---

## Key Rule Reminder

```
Violation path: ...sync_vt.INSTANCE_NAME.inst_0.IQ_zint
                              ↑
                   Has children after it (.inst_0)
                   → INSTANCE name → discard as fix target

Correct fix target: a name that does NOT appear in the violation paths
                    (agent had to read the actual implementation file to find it)
```
