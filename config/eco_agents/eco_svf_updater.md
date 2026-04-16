# ECO SVF Updater — EcoChange.svf Entry Writer

**You are the ECO SVF updater.** Your job is to write `eco_change` guidance entries to a TCL file (`data/<TAG>_eco_svf_entries.tcl`). This file will be appended to `EcoChange.svf` **by `post_eco_formality.csh`** after FmEcoSvfGen regenerates it — do NOT directly modify EcoChange.svf here.

**Why not directly:** FmEcoSvfGen regenerates `EcoChange.svf` from scratch (runs `fm_eco_to_svf.pl`). Any direct append would be overwritten. The correct sequence is: FmEcoSvfGen runs → post_eco_formality appends the TCL file → FmEqvEcoSynthesizeVsSynRtl runs.

**Inputs:** REF_DIR, TAG, BASE_DIR, JIRA, ROUND, new_logic entries from `data/<TAG>_eco_applied_round<ROUND>.json`

---

## When to Run

Only run when `data/<TAG>_eco_applied_round<ROUND>.json` contains entries with `"change_type": "new_logic"` and `"status": "INSERTED"`. If no such entries exist, skip — no SVF update needed.

---

## STEP 1 — Read Applied JSON

```bash
cat <BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json
```

Collect all entries where `"change_type": "new_logic"` and `"status": "INSERTED"` from the **Synthesize** stage only. SVF `eco_change` is only required for `FmEqvEcoSynthesizeVsSynRtl` (RTL vs gate-level). PrePlace and Route stage-to-stage targets auto-match by instance name.

For each such entry, extract directly from eco_applied_round<ROUND>.json:
- `inv_inst_full_path` — full hierarchy path already computed by eco_applier (e.g., `<TILE>/<INST_A>/<INST_B>/eco_<jira>_001`)
- `inv_cell_type` — the std cell type as found in the netlist

No path reconstruction needed — eco_applier already built the full path.

---

## STEP 2 — Check for Duplicate Entries in TCL File

Before writing, verify the entry does not already exist in `data/<TAG>_eco_svf_entries.tcl`:

```bash
if [ -f "<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl" ]; then
    grep -c "<inv_inst>" <BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
fi
```

If count > 0: skip this entry (already written from a previous attempt) — report ALREADY_PRESENT.

---

## STEP 3 — Write TCL Entries File

Write (or append) to `<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl` (always use full absolute path). Use values from eco_applied_round<ROUND>.json — do NOT hardcode cell type or instance name:

```tcl
# ECO new_logic cell insertion — TAG=<TAG> JIRA=<JIRA>
eco_change \
  -type insert_cell \
  -instance { <inv_inst_full_path from eco_applied_round<ROUND>.json> } \
  -reference { <inv_cell_type from eco_applied_round<ROUND>.json> }
```

Example (all values read from eco_applied_round<ROUND>.json — nothing hardcoded):
```tcl
# ECO new_logic cell insertion — TAG=<TAG> JIRA=<JIRA>
eco_change \
  -type insert_cell \
  -instance { <tile>/<INST_A>/<INST_B>/eco_<jira>_<seq> } \
  -reference { <inv_cell_type> }
```

This file is referenced by `post_eco_formality.csh` via `ECO_SVF_ENTRIES=` in `<REF_DIR>/data/eco_fm_config`. The script appends it to `EcoChange.svf` automatically after FmEcoSvfGen completes.

---

## Output

Write result to `<BASE_DIR>/data/<TAG>_eco_svf_update.json` (full absolute path):

```json
{
  "tcl_file": "<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl",
  "svf_file": "<REF_DIR>/data/svf/EcoChange.svf",
  "note": "TCL entries written — will be appended by post_eco_formality.csh after FmEcoSvfGen",
  "entries": [
    {
      "inst_path": "<inv_inst_full_path from eco_applied_round<ROUND>.json>",
      "cell_type": "<inv_cell_type from eco_applied_round<ROUND>.json>",
      "status": "WRITTEN"
    }
  ]
}
```

Possible statuses: `WRITTEN`, `ALREADY_PRESENT`, `SKIPPED` (no new_logic entries).

---

## Output RPT

After writing the JSON, write `<BASE_DIR>/data/<TAG>_eco_step4b_svf.rpt`:

```
================================================================================
STEP 4b — SVF ENTRIES
Tag: <TAG>  |  JIRA: DEUMCIPRTL-<JIRA>
================================================================================

TCL File : <BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
SVF File : <REF_DIR>/data/svf/EcoChange.svf  (appended by post_eco_formality.csh)

Entries:
  <Repeat for each entry:>
  Instance  : <inv_inst_full_path>
  Cell Type : <inv_cell_type>
  Status    : <WRITTEN / ALREADY_PRESENT>

<If no new_logic entries:>
  (not applicable — no new_logic insertions in this round)

================================================================================
```

---

## Critical Rules

1. **Never directly modify EcoChange.svf** — write to the TCL file only; post_eco_formality handles the append
2. **Synthesize stage only** — only register cells for the Synthesize stage; PrePlace and Route auto-match by instance name
3. **Duplicate check** — skip if entry already present in the TCL file (safe for retries)
4. **Full hierarchy path** — use `<TILE>/<inst_hierarchy>/<inv_inst>` from module root, NOT the Formality DB path
5. **No hardcoded values** — all cell types and instance names come from `eco_applied_round<ROUND>.json`
