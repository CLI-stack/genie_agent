# Fix Implementor Agent

**PERMISSIONS:** You have FULL READ/WRITE ACCESS to all files under /proj/. Run Bash commands freely. Do not ask for permission.

This agent reads consolidated fix recommendations and applies them directly to the appropriate constraint or RTL files.

---

> ## CRITICAL: FILE MODIFICATION RULES
>
> **`p4 edit` applies to CONSTRAINT FILES ONLY — NOT RTL files.**
>
> ### Constraint / meta files (require `p4 edit`):
> - `src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl`  ← CDC/RDC **constraints** (netlist clock/reset, cdc custom sync)
> - `src/meta/tools/cdc0in/variant/<ip>/umc.0in_waiver`           ← CDC/RDC **waivers** (cdc report crossing -severity waived)
> - `src/meta/tools/cdc0in/variant/<ip>/umc_top_lib.list`
> - `src/meta/tools/spgdft/variant/<ip>/project.params`
>
> **Mandatory sequence for constraint/meta files:**
> ```
> 1. cp <file> <file>.bak_<tag>    ← backup
> 2. p4 edit <file>                ← checkout from Perforce (REQUIRED)
> 3. Edit tool                     ← only now modify
> ```
> If `p4 edit` fails: log the warning, check `ls -l <file>` — proceed only if writable.
>
> ### RTL source files (NO `p4 edit`):
> - `src/rtl/**/*.sv`, `src/rtl/**/*.v`
>
> **Mandatory sequence for RTL files:**
> ```
> 1. cp <file> <file>.bak_<tag>    ← backup
> 2. Edit tool                     ← modify directly (no p4 edit)
> ```

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

### Step 1b: Load Already-Applied Fixes (cross-check-type duplicate prevention)

Before applying any fix, check if other check types have already applied fixes in this round.
Read ALL existing `_fix_applied_*.json` files for this tag:
```bash
ls <base_dir>/data/<tag>_fix_applied_*.json 2>/dev/null
```

For each file found, collect every `fix_action` from the `applied[]` array into a set: `already_applied_actions`.

When applying any RTL fix below: if `fix_action` is in `already_applied_actions` → skip it, log as `skipped_duplicate_cross_check_type`.

---

## Step 2: Determine Target Files

### CDC/RDC
| File | Path | Apply when |
|------|------|-----------|
| Constraint file | `<ref_dir>/src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl` | For `constraint` fixes (netlist clock/reset, cdc custom sync, netlist port domain) |
| Waiver file | `<ref_dir>/src/meta/tools/cdc0in/variant/<ip>/umc.0in_waiver` | For `waiver` fixes (cdc report crossing ... -severity waived) |
| RTL source files | `<ref_dir>/src/rtl/**/<filename>` — resolved from filename (see Step 2a) | For `rtl_fix` fixes |
| Library list | `<ref_dir>/src/meta/tools/cdc0in/variant/<ip>/umc_top_lib.list` | Only if Library Finder found missing modules |

### SPG_DFT
| File | Path | Apply when |
|------|------|-----------|
| DFT params | `<ref_dir>/src/meta/tools/spgdft/variant/<ip>/project.params` | For `constraint` fixes |
| RTL source files | Path as-is from RTL analyzer output (no `src/rtl/` resolution needed) | For `rtl_fix` fixes |

### Lint
| File | Path | Apply when |
|------|------|-----------|
| RTL source files | `<ref_dir>/src/rtl/**/<filename>` — resolved from filename (see Step 2a); also `out/*/library/*/pub/src/rtl/` for library components | For `rtl_fix`, `tie_off`, and `pragma_suppress` violations |

---

## Step 2a: Resolve RTL File Paths (CDC/RDC and Lint only)

**Why:** CDC/RDC and Lint reruns execute `rhea_build`/`rhea_drop`, which **rebuilds `publish_rtl/` from library and src/rtl every time**. Any fix written directly to `publish_rtl/` will be wiped on the next rerun.

**Preferred fix target: `out/*/library/*/pub/src/rtl/`**
The library `pub/src/rtl/` path survives flow reruns — the flow copies FROM it, not over it. This is the correct target for ALL RTL files, including library components (e.g., `dsn_health-mathura_mcd-mcd`, `dftmisc-mathura-mcd`) that do NOT exist in the project `src/rtl/`.

**SPG_DFT does NOT run rhea_build** — its `publish_rtl/` is stable between rounds. RTL fix paths for SPG_DFT are used as-is (no resolution needed). Apply `rtl_fix` entries directly to the reported path.

**For each `rtl_fix` (or `tie_off`) entry in CDC/RDC or Lint consolidated JSON:**

1. Take `rtl_file` from the entry (may point to `publish_rtl/`, `library/tmp/`, `library/pub/`, or already `src/rtl/`)
2. Extract the filename: `basename = os.path.basename(rtl_file)` (e.g., `dsn_hp_block_mcd.v`)
3. **Priority 1 — Check `library/*/pub/src/rtl/`** (preferred, survives reruns):
```bash
find <ref_dir>/out -path "*/library/*/pub/src/rtl/*" -name "<basename>" 2>/dev/null
```
   - If exactly one match → use that path
   - If multiple matches → pick the one whose library name best matches the module context
4. **Priority 2 — Check `src/rtl/`** (fallback for project-owned files not in any library):
```bash
find <ref_dir>/src/rtl -name "<basename>" 2>/dev/null
```
   - If exactly one match → use that path
   - If multiple matches → pick the one whose directory best matches the module context
5. If no match in either location → log as `unresolvable_rtl_path`, skip this fix, add to `requires_investigation`

**Note:** `insert_after_line` from the consolidated JSON was recorded from `publish_rtl/` — since `library/pub/src/rtl/` is the source that generates `publish_rtl/`, the line numbers should match. If the file has already been modified, re-search for the context lines rather than using the line number blindly.

---

## Step 3: Backup and Checkout (file-type dependent)

**For constraint/meta files** (`src/meta/tools/...`):
```bash
cp <target_file> <target_file>.bak_<tag>
p4 edit <target_file>
```
If `p4 edit` fails: log the warning, check `ls -l <file>`, proceed only if writable.

**For RTL source files** (`src/rtl/...`):
```bash
cp <target_file> <target_file>.bak_<tag>
# NO p4 edit — modify directly
```

---

## Step 4: Apply Fixes

### For CDC/RDC — Constraint Fixes AND RTL Fixes

#### 4a: Constraint fixes (`fix_type: constraint`)

From the consolidated JSON, process all `fix_type: constraint` entries:
1. Read the constraint file
2. Check if already present (string match) — skip if duplicate
3. Append to end of file under a dated comment block:

```tcl
# === Auto-applied by analyze-fixer Round <round> [<tag>] ===
<fix_action line 1>
<fix_action line 2>
# ============================================================
```

Use the Edit tool to append (old_string = last line of file, new_string = last line + new block).

#### 4b: RTL fixes (`fix_type: rtl_fix`)

From the consolidated JSON, process all `fix_type: rtl_fix` entries. Check `fix_mode` to determine how to apply:

**`fix_mode: "insert"` (default when `fix_mode` is absent):**
1. Read the RTL file at path specified in `rtl_file`
2. Backup the file (once per file): `cp <rtl_file> <rtl_file>.bak_<tag>`
3. Check if `fix_action` lines already exist in the file — skip if duplicate
4. **Capture the before state**: line at `insert_after_line` and 2 lines of surrounding context (`diff_before`)
5. Insert `fix_action` block after line `insert_after_line` using the Edit tool with comment wrapper:
```verilog
// === Auto-applied by analyze-fixer Round <round> [<tag>] ===
<fix_action lines>
// ============================================================
```
6. **Capture the after state** (`diff_after`)
7. Log the full change in output JSON

**`fix_mode: "replace"`:**
1. Read the RTL file at path specified in `rtl_file`
2. Backup the file (once per file): `cp <rtl_file> <rtl_file>.bak_<tag>`
3. Read the exact content of line `replace_line` — this is the `old_string`
4. Check if the line already matches `fix_action` — skip if already applied
5. **Capture the before state**: line at `replace_line` and 2 lines of surrounding context (`diff_before`)
6. Replace the line content using the Edit tool: `old_string` = current line content, `new_string` = `fix_action`
7. **No comment wrapper** for replace-mode fixes — the replacement line IS the fix
8. **Capture the after state** (`diff_after`)
9. Log the full change in output JSON

If `fix_action` is vague/ambiguous → log as `requires_investigation` — do NOT guess.

#### 4c: Investigate entries (`fix_type: investigate`)

Do NOT apply — log them in `requires_investigation` list in output JSON.
The orchestrator will spawn a Deep-Dive Agent for each investigate item.

#### 4d: Waiver entries (`fix_type: waiver`)

Do NOT apply — **ZERO WAIVERS in fixer mode**. Log them in `requires_manual_waiver` list in output JSON with the correct target file path (`src/meta/tools/cdc0in/variant/<ip>/umc.0in_waiver`) so the user knows where to add them manually.

### For SPG_DFT — Constraint Fixes AND RTL Fixes

From the consolidated JSON, process `fix_type: constraint` AND `fix_type: rtl_fix` entries. Skip `investigate`.

For each constraint fix:
1. Read the current params file
2. Check if already present (string match) — skip if duplicate
3. Append under a dated comment block (same format as CDC/RDC above)

For each `rtl_fix` entry:
1. Use the `rtl_file` path as-is from the consolidated JSON — **do NOT resolve to `src/rtl/`** (SPG_DFT does not run rhea_build, so `publish_rtl/` is stable)
2. Backup the file (once per file): `cp <rtl_file> <rtl_file>.bak_<tag>` (no p4 edit)
3. Check if `fix_action` lines already exist — skip if duplicate
4. **Capture the before state**: line at `insert_after_line` + 2 lines context (`diff_before`)
5. Insert `fix_action` after `insert_after_line` using Edit tool with comment wrapper:
```verilog
// === Auto-applied by analyze-fixer Round <round> [<tag>] ===
<fix_action lines>
// ============================================================
```
6. **Capture the after state**: `diff_after` = context + comment wrapper + fix_action lines
7. Log the full change (rtl_file, backup_file, diff_before, diff_after) in output JSON

For `investigate` entries: log as `requires_investigation`.

### For Lint — RTL Fixes, Tie-offs, and Pragma Suppression

**ZERO WAIVERS for Lint.** Do NOT add entries to `src/meta/waivers/lint/variant/<ip>/umc_waivers.xml`. All violations must be addressed in RTL.

From the consolidated JSON, process `fix_type: rtl_fix`, `fix_type: tie_off`, and `fix_type: pragma_suppress` entries. Skip `investigate`.

#### For `rtl_fix` and `tie_off` entries:
1. Resolve RTL file path (Step 2a — prefer `out/*/library/*/pub/src/rtl/`, fallback `src/rtl/`)
2. Backup the file (once per file): `cp <rtl_file> <rtl_file>.bak_<tag>` (no p4 edit for RTL files)
3. Check for duplicates — skip if already applied
4. **Capture the before state**: 2 lines of surrounding context at the insertion/replacement point (`diff_before`)
5. Apply using the Edit tool:
   - **`rtl_fix` with `fix_mode: "insert"` (or no `fix_mode`)**: insert `fix_action` after `insert_after_line` with comment wrapper
   - **`rtl_fix` with `fix_mode: "replace"`**: replace the exact content of `replace_line` with `fix_action` (no comment wrapper — see 4b above for full replace-mode steps)
   - **`tie_off`**: insert the `assign` statement after the signal declaration line (always insert mode)
6. **Capture the after state** (`diff_after`)
7. Log the full change (rtl_file, backup_file, diff_before, diff_after) in output JSON

#### For `pragma_suppress` entries:

**iEDA SWAN inline pragma format**: `// spyglass disable <rule>  // <justification>` — do NOT use `disable_block`/`enable_block`.

1. Resolve RTL file path (same Step 2a logic — `out/*/library/*/pub/src/rtl/` preferred, `src/rtl/` fallback)
2. Backup the file (once per file): `cp <rtl_file> <rtl_file>.bak_<tag>` (no p4 edit)
3. Read the file; check that line `insert_after_line` does not already have `// spyglass disable <pragma_rule>` on the next line — skip if already present
4. **Capture the before state**: the line at `insert_after_line` + 1 line after (`diff_before`)
5. Apply using the Edit tool — insert ONE pragma line immediately after `insert_after_line`:
   ```
   // spyglass disable <pragma_rule>  // <fix_justification summary>
   ```
   Use the line at `insert_after_line` as the `old_string` anchor; `new_string` = original line + `\n    // spyglass disable <pragma_rule>  // <justification>`
6. **Capture the after state** (`diff_after`)
7. Log as `pragma_suppress` in applied list with `pragma_rule`, `insert_after_line`, rtl_file, backup_file, diff_before, diff_after

**No comment wrapper** — the pragma itself is the fix. Insert it bare.

**IMPORTANT for all Lint fixes:**
- Make MINIMAL changes only — fix exactly what the violation points to
- Do NOT refactor or restructure surrounding code
- If `fix_action` is ambiguous (e.g., no exact RTL line or no exact constraint text), log as `requires_manual_review` — do NOT guess
- Each RTL file is backed up only ONCE per round even if multiple fixes apply to it

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
  "tie_offs_applied": <count>,
  "pragma_suppresses_applied": <count>,
  "library_entries_added": <count>,
  "deep_dive_pending": <count>,
  "applied": [
    {
      "fix_type": "constraint",
      "target_file": "<full path to constraint file>",
      "fix_action": "<tcl command>",
      "resolves_violations": ["no_sync_xxx", "no_sync_yyy"]
    },
    {
      "fix_type": "rtl_fix",
      "target_file": "<full path to RTL file>",
      "backup_file": "<full path to RTL file>.bak_<tag>",
      "fix_action": "<exact RTL lines inserted>",
      "insert_after_line": 88,
      "diff_before": "<original lines at insertion point with 2 lines context>",
      "diff_after": "<same context lines + inserted comment wrapper + fix_action lines>",
      "resolves_violations": ["no_sync_yyy"]
    }
  ],
  "requires_investigation": [
    {
      "index": 1,
      "signal": "<signal_name>",
      "check_type": "<check_type>",
      "investigation_context": "<specific what-to-investigate description from rtl_fix_action>",
      "ref_dir": "<ref_dir>",
      "ip": "<ip>",
      "tag": "<tag>",
      "base_dir": "<base_dir>"
    }
  ],
  "manual_rtl_fixes_pending": [],
  "files_modified": [
    "<path_to_constraint_file>",
    "<path_to_rtl_file>",
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

- **ZERO WAIVERS across all check types** — no CDC waivers, no lint waivers, no SPG_DFT waivers
- For CDC/RDC: apply both `constraint` AND `rtl_fix` — `investigate` items are logged for Deep-Dive Agent
- For Lint: apply `rtl_fix`, `tie_off`, and `pragma_suppress` — do NOT touch `src/meta/waivers/lint/variant/<ip>/umc_waivers.xml`
- For SPG_DFT: apply both `constraint` (to `project.params`) AND `rtl_fix` (to path as-is) — log `investigate` only
- `pragma_suppress` inserts `// spyglass disable <rule>  // <justification>` on the line AFTER the offending statement — iEDA SWAN/LEDA inline format (NOT `disable_block`/`enable_block`); zero functional impact; one pragma per driver statement for multi-driver violations
- `xml_suppress` and `lint_constraint` are NOT valid fix types — do NOT apply them
- Always check for duplicates before applying any fix — both within the file (read actual file content) AND across check types (read existing `<tag>_fix_applied_*.json` from Step 1b)
- For `full_static_check`: fix implementors run sequentially (CDC → Lint → SPG_DFT) — never in parallel
- Always backup before editing: `cp <file> <file>.bak_<tag>` (once per file per round)
- `p4 edit <file>` ONLY for constraint/meta files (`src/meta/tools/...`) — NOT for RTL files (`src/rtl/...`)
- **CDC/RDC and Lint RTL fixes**: resolve to `out/*/library/*/pub/src/rtl/` first (preferred — survives reruns, works for library components), then fall back to `src/rtl/` — never edit `publish_rtl/` directly (wiped on every rerun)
- **SPG_DFT RTL fixes**: use path as-is — SPG_DFT does NOT run rhea_build, so `publish_rtl/` is stable between rounds
- If `fix_action` is vague or ambiguous, log as `requires_investigation` — do NOT guess
- `requires_investigation` list in output JSON is read by orchestrator to spawn Deep-Dive Agents — include full context (signal, investigation_context, ref_dir, ip, tag, base_dir)

---

## SELF-CHECK Before Finishing

Before ending your turn, verify:

1. **Did you write the output JSON to disk using the Write tool?** → If not, do it now — do NOT finish without it
2. **Did you run `p4 edit` on any `src/rtl/...` file?** → That is wrong — RTL files need no `p4 edit`
3. **Did you write RTL fixes to `publish_rtl/` paths?** → That is wrong — CDC/RDC and Lint fixes must target `out/*/library/*/pub/src/rtl/` (preferred) or `src/rtl/` (fallback)

Do NOT finish your turn until `data/<tag>_fix_applied_<check_type_short>.json` is written to disk.
