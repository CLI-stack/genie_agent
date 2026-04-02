# Deep-Dive Agent

**PERMISSIONS:** You have FULL READ/WRITE ACCESS to all files under /proj/. Run Bash commands freely. Do not ask for permission.

Resolve a single `investigate` item by doing deep hierarchy research, then auto-applying the concrete fix.

---

## Inputs

| Input | Description |
|-------|-------------|
| `index` | Sequential index N — used for output file naming |
| `signal` | Signal name needing investigation |
| `investigation_context` | What to look for (from RTL analyzer's `fix_action` for investigate items) |
| `check_type` | `cdc_rdc`, `spg_dft`, or `lint` |
| `ref_dir` | Tree reference directory |
| `ip` | IP name |
| `tag` | Task tag |
| `base_dir` | Base agent directory |
| `round` | Current fixer round number |

---

## Step 1: Read the Investigation Context

The `investigation_context` field tells you specifically what to look for. Follow it exactly — do not do broad exploration.

Examples:
- "Check parent module umcdat_top for how cfg_enable is routed to this instance"
- "Find where req_pulse is consumed in clk_b domain — need to confirm it is not already gated"
- "Determine correct sync cell — look at nearby synchronizer instantiations in umcdat_core.sv"

---

## Step 2: Research

Do only what the investigation_context asks. Typical tasks:

- **Find parent module**: Grep for instantiation of the module containing the signal
- **Trace hierarchy**: Read the parent module to see how the signal is connected
- **Find sync cell pattern**: Read neighboring synchronizer instantiations in the same file
- **Check existing constraints**: Read `src/meta/tools/cdc0in/variant/<ip>/project.0in_ctrl.v.tcl`

---

## Step 3: Determine Concrete Fix

From your research, determine ONE of:

| Result | Action |
|--------|--------|
| Now have enough info for RTL fix | `fix_type: rtl_fix` — produce exact RTL lines + file + line number |
| Now have enough info for constraint | `fix_type: constraint` — produce exact TCL command |
| Still cannot determine safe fix | `fix_type: unresolved` — explain what is still missing |

---

## Step 4: Apply the Fix

If `fix_type` is `rtl_fix`:
1. Backup: `cp <rtl_file> <rtl_file>.bak_<tag>` (skip if backup already exists)
2. Check for duplicate — if `fix_action` already in file, skip
   (NO p4 edit for RTL files — modify directly)
4. Insert code block after `insert_after_line` using Edit tool with comment wrapper:
```verilog
// === Auto-applied by Deep-Dive Agent Round <round> [<tag>] ===
<fix_action lines>
// ============================================================
```

If `fix_type` is `constraint`:
1. Backup: `cp <constraint_file> <constraint_file>.bak_<tag>` (skip if exists)
2. `p4 edit <constraint_file>`
3. Check for duplicate — skip if already present
4. Append to end of constraint file:
```tcl
# === Auto-applied by Deep-Dive Agent Round <round> [<tag>] ===
<fix_action>
# ============================================================
```

If `fix_type` is `unresolved`:
- Do NOT modify any file
- Log the reason in output JSON

---

## Step 5: Write Output JSON

Write to `<base_dir>/data/<tag>_deepdive_<N>.json`:

```json
{
  "tag": "<tag>",
  "index": <N>,
  "signal": "<signal_name>",
  "check_type": "<check_type>",
  "round": <round>,
  "investigation_summary": "<what you found during research>",
  "fix_type": "rtl_fix | constraint | unresolved",
  "fix_action": "<exact RTL or TCL applied, or null>",
  "fix_applied": true,
  "target_file": "<file modified, or null>",
  "backup_created": "<backup path, or null>",
  "unresolved_reason": "<why fix could not be determined — only if unresolved>"
}
```

**MANDATORY: Write this file. The orchestrator reads it to compile the round report and update the STALLED check.**
