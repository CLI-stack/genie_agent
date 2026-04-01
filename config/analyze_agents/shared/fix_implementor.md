# Fix Implementor Agent

**PERMISSIONS:** You have FULL READ/WRITE ACCESS to all files under /proj/. Run Bash commands freely. Do not ask for permission.

This agent reads consolidated fix recommendations and applies them directly to the appropriate constraint or RTL files.

---

## Inputs

| Input | Description |
|-------|-------------|
| `tag` | Task tag (e.g., `20260401120000`) |
| `check_type` | `cdc_rdc`, `spg_dft`, or `lint` |
| `ref_dir` | Tree reference directory |
| `ip` | IP name (e.g., `umc9_3`, `umc17_0`) |
| `base_dir` | Base agent directory (e.g., `/proj/.../main_agent`) |
| `round` | Current fixer round number (1, 2, 3…) |

---

## Step 1: Read Consolidated Fixes

Read the relevant consolidated JSON file(s):
- CDC/RDC: `<base_dir>/data/<tag>_consolidated_cdc.json`
- SPG_DFT: `<base_dir>/data/<tag>_consolidated_spgdft.json`
- Lint: `<base_dir>/data/<tag>_consolidated_lint.json`

If the file does not exist, report "No consolidated fixes found for <check_type>" and stop.

---

## Step 2: Determine Target Files

### CDC/RDC
| File | Path | Apply when |
|------|------|-----------|
| Constraint file | `<ref_dir>/src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl` | Always (for constraint fixes) |
| Library list | `<ref_dir>/src/meta/tools/cdc0in/variant/<ip>/umc_top_lib.list` | Only if Library Finder found missing modules |

### SPG_DFT
| File | Path | Apply when |
|------|------|-----------|
| DFT params | `<ref_dir>/src/meta/tools/spgdft/variant/<ip>/project.params` | Always (for constraint fixes) |

### Lint
| File | Path | Apply when |
|------|------|-----------|
| RTL source files | `<ref_dir>/src/rtl/**/*.sv`, `*.v` — exact path from RTL analyzer output | For each rtl_fix violation |

---

## Step 3: Backup and P4 Edit

**Before modifying ANY file:**

1. Create a backup copy:
```bash
cp <target_file> <target_file>.bak_<tag>
```

2. Check out from Perforce:
```bash
p4 edit <target_file>
```

If `p4 edit` fails (file not in depot or already open), log the warning but continue — the file may be writable without p4.

---

## Step 4: Apply Fixes

### For CDC/RDC and SPG_DFT — Constraint Fixes Only

From the consolidated JSON, process only `fix_type: constraint` entries. Skip `rtl_fix` and `investigate`.

For each constraint fix:
1. Read the current target file
2. Check if the constraint is already present (string match) — skip if duplicate
3. Append to the end of the file under a dated comment block:

```tcl
# === Auto-applied by analyze-fixer Round <round> [<tag>] ===
<fix_action line 1>
<fix_action line 2>
# ============================================================
```

Use the Edit tool to append (old_string = last line of file, new_string = last line + new block).

For `rtl_fix` entries: do NOT apply — log them in output JSON as `manual_rtl_fixes_pending`.
For `investigate` entries: do NOT apply — log them as `requires_investigation`.

### For Lint — RTL Fixes

From the consolidated JSON, process `fix_type: rtl_fix` entries.

For each RTL fix:
1. Read the RTL file at the path specified in the fix
2. Backup the file: `cp <rtl_file> <rtl_file>.bak_<tag>`
3. Run `p4 edit <rtl_file>`
4. Apply the RTL change using the Edit tool — make the minimal targeted change specified in `fix_action`
5. Log the change in output JSON

**IMPORTANT for Lint RTL fixes:**
- Make MINIMAL changes only — fix exactly what the lint violation points to
- Do NOT refactor or restructure surrounding code
- If the fix is ambiguous or risky, log as `requires_manual_review` instead of applying

---

## Step 5: Library List Update (CDC/RDC only)

If the consolidated JSON contains library finder results (`library_additions` field):
1. Backup: `cp <liblist_file> <liblist_file>.bak_<tag>`
2. Run `p4 edit <liblist_file>`
3. For each missing library path, check if already in file
4. Append missing entries:
```
# Added by analyze-fixer Round <round> [<tag>]
<library_path>
```

---

## Step 6: Write Output JSON

Write to `<base_dir>/data/<tag>_fix_applied_<check_type_short>.json`:

Where `<check_type_short>`:
- `cdc_rdc` → `cdc`
- `spg_dft` → `spgdft`
- `lint` → `lint`

```json
{
  "tag": "<tag>",
  "check_type": "<check_type>",
  "round": <round>,
  "constraints_applied": <count>,
  "rtl_fixes_applied": <count>,
  "library_entries_added": <count>,
  "applied": [
    {
      "fix_type": "constraint",
      "target_file": "<path>",
      "fix_action": "<tcl command>",
      "resolves_violations": ["no_sync_xxx", "no_sync_yyy"]
    }
  ],
  "manual_rtl_fixes_pending": [
    {
      "signal": "<signal_name>",
      "rtl_file": "<path>",
      "why": "<root cause>",
      "suggested_fix": "<description of what to add>"
    }
  ],
  "requires_investigation": [
    {
      "signal": "<signal_name>",
      "reason": "<why it needs investigation>"
    }
  ],
  "files_modified": [
    "<path_to_constraint_file>",
    "<path_to_liblist_file>"
  ],
  "backups_created": [
    "<path>.bak_<tag>"
  ]
}
```

**MANDATORY: Write this file to disk using the Write tool. If you do not write it, the orchestrator cannot compile the round report.**

---

## Notes

- NEVER add waivers (`cdc report crossing -severity waived`) — target is zero waivers
- For CDC/RDC: only apply `constraint` type fixes (netlist constant, netlist clock, cdc custom sync, netlist port domain)
- For Lint: apply `rtl_fix` directly to RTL source with backup
- For SPG_DFT: only apply `constraint` type fixes to project.params
- Always check for duplicates before appending
- Always backup before editing
- Always p4 edit before modifying
