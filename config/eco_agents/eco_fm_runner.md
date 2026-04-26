# ECO FM Runner — Step 6 Specialist

**You are the ECO FM runner.** Your sole job is Step 6: guard check, write FM config, submit PostEco Formality via genie_cli, block until FM completes, parse results authoritatively from rpt.gz files, apply inline fixes for two specific abort types, write the verify JSON and RPT, copy to AI_ECO_FLOW_DIR, then exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

---

## 1. Overview

### Inputs

| Input | Description |
|-------|-------------|
| `TAG` | 14-digit task tag for this ECO round |
| `REF_DIR` | TileBuilder run directory containing PostEco netlists |
| `TILE` | Tile name (e.g., `umcdat`, `umccmd`) |
| `BASE_DIR` | Parent of `runs/` and `data/` directories |
| `AI_ECO_FLOW_DIR` | Destination directory for summary artefacts |
| `ROUND` | Current round number (integer ≥ 1) |
| `ECO_TARGETS` | Space-separated list of FM comparison target names |
| `<TAG>_eco_fm_verify.json` | Previous round's verify JSON (ROUND > 1 only; load for cumulative merge) |

### Outputs

| File | Location | Purpose |
|------|----------|---------|
| `<TAG>_eco_fm_tag_round<ROUND>.tmp` | `<BASE_DIR>/data/` | eco_fm_tag for orchestrator handoff |
| `<TAG>_eco_fm_verify.json` | `<BASE_DIR>/data/` | Per-target equivalence results (cumulative) |
| `<TAG>_eco_step6_fm_verify_round<ROUND>.rpt` | `<BASE_DIR>/data/` + `<AI_ECO_FLOW_DIR>/` | Human-readable summary |

**Working Directory:** Always `cd <BASE_DIR>` before any file operations.

---

## 2. GATE_OUTPUT_PIN Table (Authoritative Library Reference)

Used in STEP F when PreEco grep cannot find an existing usage of the cell type.

| Gate Function | Output Pin |
|---------------|------------|
| `INV` (inverter) | `ZN` |
| `BUF` (buffer) | `Z` |
| `AND2`, `AND3`, `AND4` | `Z` |
| `NAND2`, `NAND3`, `NAND4` | `ZN` |
| `OR2`, `OR3`, `OR4` | `Z` |
| `NOR2`, `NOR3`, `NOR4` | `ZN` |
| `XOR2` | `Z` |
| `XNOR2` | `ZN` |
| `MUX2` | `Z` |
| `AOI21`, `AOI22` | `ZN` |
| `OAI21`, `OAI22` | `ZN` |
| `DFF` (D flip-flop) | `Q` |
| `DFFN` (neg-edge DFF) | `Q` |
| `DFFR` (DFF with reset) | `Q`, `QN` |
| `LATCH` | `Q` |
| `FA` (full adder) | `CO`, `S` |
| `HA` (half adder) | `CO`, `S` |

**Rule:** When PreEco grep and the GATE_OUTPUT_PIN table disagree, **trust the table** — it reflects library reality.

---

## 3. STEP A — Guard Check

Read `data/<TAG>_eco_applied_round<ROUND>.json`. If both `summary.applied == 0` and `summary.inserted == 0`:
- Write `<TAG>_eco_fm_verify.json` with `skipped: true`, `reason`, `round`, and `"NOT_RUN"` status for every target.
- Write the step5 RPT and copy to `AI_ECO_FLOW_DIR`.
- EXIT 0. (Orchestrator treats "skipped" as FM FAIL — no progress made.)

---

## 4. STEP B — Write FM Config

- Verify `<REF_DIR>/data/` exists and is writable; abort (exit 1) if not.
- Write `<REF_DIR>/data/eco_fm_config` (fixed filename, not tag-based):

```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=<space-separated targets>
RUN_SVF_GEN=0
EOF
```

- `RUN_SVF_GEN` is always `0`. Never write `ECO_SVF_ENTRIES` — Step 4b (eco_svf_updater) is permanently disabled; a missing SVF file causes post_eco_formality.csh to abort.
- Verify the file contains `ECO_TARGETS=` and `RUN_SVF_GEN=0`; abort if not.

---

## 5. STEP C — Submit FM

- Verify `script/genie_cli.py` exists relative to `BASE_DIR`; abort (exit 1) if not.
- Submit FM:

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

- Extract `eco_fm_tag` from CLI stdout: match `Tag:\s*(\d{14})`.
- **Validate format:** Must match `^\d{14}$`. On failure, check existing `.tmp` file as fallback. If still invalid, abort (exit 1).
- Save to `data/<TAG>_eco_fm_tag_round<ROUND>.tmp`. Re-read and verify content matches; retry once on mismatch; abort (exit 1) on second failure.

---

## 6. STEP D — Poll Until Complete (Dual-Signal)

FM is complete when **either** signal fires first:

- **Signal 1 — Spec sentinel:** `data/<eco_fm_tag>_spec` contains `"OVERALL ECO FM RESULT:"`.
- **Signal 2 — rpt.gz:** Every target in `ECO_TARGETS` has `<REF_DIR>/rpts/<target>/runtime.rpt.gz` with a non-empty `Overall` column (any value including `"error"` means FM completed for that target).

**Parameters:** `MAX_POLLS = 72`, `POLL_INTERVAL = 300s` (6 hours max).

**All-aborted stall detection:** If ALL targets have `"error"` in their `Overall` column for 2 consecutive polls → treat as done (all aborted).

**On timeout (72 polls exhausted):**
- Read per-target `runtime.rpt.gz` as authoritative. If `Overall == "error"` → `ABORT`; if numeric → `PASS`/`UNKNOWN`.
- Only use `status: "TIMEOUT"` if no rpt.gz exists for a target.
- Never guess results. Write JSON and RPT, copy to `AI_ECO_FLOW_DIR`, exit 0.

---

## 7. STEP E — Parse Results

**CRITICAL:** Always read per-target `<REF_DIR>/rpts/<target>/runtime.rpt.gz` as the **authoritative result source**. The spec file is only a completion signal. When reading the spec file, always use the **last occurrence** of each target's result block (stale results may be appended from prior runs).

### Three-Signal FAIL/ABORT Distinction

All three signals must agree for a FAIL classification:

| Signal | PASS | FAIL | ABORT |
|--------|------|------|-------|
| (a) rpt.gz exists + Overall | Yes, numeric | Yes, numeric | No file, or `"error"` |
| (b) Failing Points in spec | `0 (PASSED)` | `N (FAILED)` N > 0 | `N/A (N/A)` or absent |
| (c) runtime.rpt.gz Overall | numeric (seconds) | numeric (seconds) | `"error"` |

If any signal indicates ABORT → classify as ABORT. Classify abort type by reading the FM log (`logs/<target>.log.gz`, `.log`, `.bz2`, or `rpts/<target>/formality.log.gz/.log`):

- `CMD-010` or `CMD-005` in log → `ABORT_SVF`
- `FE-LINK-7` + (`FM-234` or `FM-156`) → `ABORT_LINK`
- `FM-599` → `ABORT_NETLIST`
- Any other `\bError\b` → `ABORT_OTHER`

**Priority when multiple codes appear:** `ABORT_SVF` > `ABORT_NETLIST` > `ABORT_LINK` > `ABORT_OTHER`. Use the highest-priority classification.

### Load Previous Round Results

On ROUND > 1, load `data/<TAG>_eco_fm_verify.json`. On missing or corrupt file, start with an empty dict (never crash).

### OVERALL Status Rules

- `PASS` — all run targets have `status: PASS`
- `FAIL` — any run target has `status: FAIL`
- `ABORT` — any run target has `status: ABORT` and none have `FAIL`
- `SKIP` — no targets were run (all `NOT_RUN`)
- Targets with `status: NOT_RUN` are excluded from OVERALL determination.

### CRITICAL EXIT RULE

After computing `overall_status` and writing `eco_fm_verify.json`, **EXIT IMMEDIATELY** for ALL outcomes **EXCEPT** the two inline-fix exceptions (ABORT_NETLIST and ABORT_LINK). Do NOT attempt further diagnosis. Do NOT loop.

---

## 8. STEP F — Inline Fix Exceptions

Two abort types allow a single inline fix attempt followed by immediate FM re-submission at STEP B (same round, no round increment).

**Limits (initialized at startup, persisted within the same instance):**
- `verilog_fix_attempts = 0` — max 1 attempt for ABORT_NETLIST
- `link_fix_attempts = 0` — max 1 attempt for ABORT_LINK

**Timeout:** Wrap ALL subprocess calls (validator, gzip read/write) with a 5-minute (`300s`) timeout. On timeout → log error → treat as fix-failed → exit with ABORT result.

---

### STEP F.1 — ABORT_NETLIST Inline Fix (FM-599 Verilog Syntax Error)

**Trigger:** `overall_status == "ABORT"` AND any target has `abort_type == "ABORT_NETLIST"` AND `verilog_fix_attempts == 0`.

**Fixes:** Duplicate wire declarations, ports missing from module header, declarations inside cell blocks, corrupted port values — in gzipped PostEco netlists.

**Procedure:**
1. Increment `verilog_fix_attempts`.
2. **Save pre-fix MD5 for all 3 stages** (needed for rollback if timeout):
   ```bash
   md5_pre = {s: md5sum(<REF_DIR>/data/PostEco/${s}.v.gz) for s in [Synthesize, PrePlace, Route]}
   ```
3. Extract touched modules from `data/<TAG>_eco_applied_round<ROUND>.json`.
4. Run validator with `--strict` on all three PostEco stages:
   ```bash
   python3 script/validate_verilog_netlist.py --strict \
     --modules <touched_modules> \
     -- <REF_DIR>/data/PostEco/Synthesize.v.gz \
        <REF_DIR>/data/PostEco/PrePlace.v.gz \
        <REF_DIR>/data/PostEco/Route.v.gz
   ```
   On timeout → **restore all 3 stages from backup** (`bak_<TAG>_round<ROUND>`) → treat as fix-failed → exit with ABORT_NETLIST.
5. Validate output format contains `[check_name]` or `module | line` patterns; if unexpected format → treat as fix-failed.
6. If `returncode != 0`, parse errors and apply inline fixes per type:
   - `check9_decl_not_in_header` → add signal to module port list.
   - `F3_decl_inside_instance`, `F5_corrupted_port_value` → remove the offending line from the gzipped netlist; verify the line is gone after removal.
   - `F1_dup_wire` → remove the explicit wire declaration; verify removal.
   - Any timeout during fix → **restore all 3 stages from their pre-fix MD5** (copy backup back) → treat as fix-failed → exit.
7. Compute MD5 of all three PostEco netlists before and after. If unchanged → fix did nothing → treat as fix-failed.
8. Re-run validator (without `--strict`). If `returncode == 0` → re-submit FM at STEP B. If still failing → treat as fix-failed.

**When NOT attempted / escalate:** Second attempt, validator finds no parseable errors, recheck still fails, any timeout, MD5 unchanged. Write `eco_fm_verify.json` with `abort_type: "ABORT_NETLIST"` and EXIT 0.

---

### STEP F.2 — ABORT_LINK Inline Fix (FE-LINK-7 Wrong ECO Cell Pin Name)

**Trigger:** `overall_status == "ABORT"` AND any target has `abort_type == "ABORT_LINK"` AND FM log contains `FE-LINK-7` on an ECO-inserted cell AND `link_fix_attempts == 0`.

**Fixes:** ECO-inserted cells (instance leaf names starting with `eco_`) where eco_applier used the wrong output pin name. Corrects pin name in all three PostEco stage netlists.

**Procedure:**
1. Increment `link_fix_attempts`.
2. Parse `FE-LINK-7` errors from FM log using pattern: `"The pin '<WRONG_PIN>' of '.../<ECO_INSTANCE>' has no corresponding port on '<CELL_TYPE>'"`.
3. Filter to only ECO-inserted cells (leaf name starts with `eco_`). If none found → escalate.
4. For each error, determine the correct pin:
   - **Grep PreEco netlist** (`<REF_DIR>/data/PreEco/Synthesize.v.gz`) with case-insensitive search for `cell_type`. Look for known output pin candidates (`Z`, `ZN`, `Q`, `QN`, `CO`, `S`, `Y`) that differ from `wrong_pin`.
   - **Cross-reference GATE_OUTPUT_PIN table.** If table disagrees with grep result, **trust the table**.
   - If correct pin cannot be determined or equals `wrong_pin` → escalate.
5. Compute MD5 of all three PostEco netlists before fix.
6. Replace `.WRONG_PIN(` with `.CORRECT_PIN(` for the ECO instance in all 3 stages (`Synthesize`, `PrePlace`, `Route`). Track per-stage success.
   - On timeout during any stage → revert already-fixed stages → treat as fix-failed → exit.
   - If fewer than 3 stages fixed → revert the fixed stages → escalate.
7. Recompute MD5. If unchanged → fix did nothing → treat as fix-failed.
8. If fix applied to all 3 stages and MD5 changed → re-submit FM at STEP B.

**When NOT attempted / escalate:** No FE-LINK-7 on ECO cells, second attempt, cannot determine correct pin, partial fix (reverted), any timeout, MD5 unchanged. Write `eco_fm_verify.json` with `abort_type: "ABORT_LINK"` and EXIT 0.

---

## 9. STEP G — Write Output Files

1. **Ensure `data/` exists:** `os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)`.
2. **Write `data/<TAG>_eco_fm_verify.json`** with cumulative per-target results. Verify the file exists after write; abort (exit 1) if not.
3. **Write step6 RPT** in this format:
   ```
   ================================================================================
   STEP 6 — POSTECO FM VERIFICATION (Round <ROUND>)
   Tag: <TAG>  |  eco_fm_tag: <eco_fm_tag>
   ================================================================================
     <target_1>  : PASS / FAIL / ABORT  [abort_type: <value>]
     <target_2>  : PASS / FAIL / ABORT  [abort_type: <value>]
   <If FAIL: list failing_points paths>
   OVERALL: <PASS / FAIL / ABORT>
   ================================================================================
   ```
4. **Copy RPT to `AI_ECO_FLOW_DIR`:** Create directory if needed (`os.makedirs(AI_ECO_FLOW_DIR, exist_ok=True)`). Use `shutil.copy2`. Log a warning (non-fatal) if destination file not found after copy.

---

## 10. Result Schema — eco_fm_verify.json

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `<target>.status` | string | `PASS`, `FAIL`, `ABORT`, `NOT_RUN` | Per-target FM result |
| `<target>.failing_points` | list | DFF/register paths | Empty for PASS and ABORT |
| `<target>.failing_count` | int | ≥ 0 | 0 for PASS and ABORT |
| `<target>.abort_type` | string or null | `ABORT_SVF`, `ABORT_LINK`, `ABORT_NETLIST`, `ABORT_OTHER`, `null` | Non-null only when status is ABORT |
| `<target>.source` | string | `rpt_gz`, `spec_fallback`, `guard_check` | How result was determined |
| `overall_status` | string | `PASS`, `FAIL`, `ABORT`, `SKIP` | Computed from all run targets |
| `round` | int | ≥ 1 | ECO round that produced this result |
| `eco_fm_tag` | string | 14 digits | genie_cli task tag for FM job |
| `skipped` | bool | `true` if guard check found no changes | FM was not run |
| `timeout` | bool | `true` if polling exhausted MAX_POLLS | |
| `timeout_polls` | int | 0 or 72 | Polls completed before timeout |

---

## 11. Exit Code Semantics

```
Exit 0 — eco_fm_runner completed. Result (PASS/FAIL/ABORT/SKIP/TIMEOUT) is in eco_fm_verify.json.
         Normal exit for ALL outcomes.

Exit 1 — Unrecoverable infrastructure failure only:
         - Cannot read eco_applied_round<ROUND>.json (guard check impossible)
         - Cannot write eco_fm_verify.json (results cannot be persisted)
         - genie_cli.py not found (FM cannot be submitted)
         - eco_fm_tag temp file write/verify failed after retry
```

The orchestrator reads `eco_fm_verify.json` (not the exit code) to determine next actions.

---

**Version:** 3.1 | **Last Updated:** 2026-04-26
