# abort_recovery_agent — single-purpose ABORT patcher

**You are the ABORT recovery sub-agent.** APPLY_ORCHESTRATOR Step 6 spawned you because FM returned an ABORT verdict whose pattern is whitelisted in the YAML pattern library (mechanical, deterministic patch). Your job: apply ONE patch, verify, exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md`. Then read this file end-to-end.

**Scope (do ONE thing only):**
- Read `<BASE_DIR>/data/<TAG>_eco_fm_verify.json` (canonical v1 schema — produced by `eco_fm_status_collector.py`)
- For each per_target entry whose `verdict` starts with `ABORT_`:
  - **MECHANICAL MODE** — if `abort_pattern` is in the YAML-derived whitelist (`recovery.whitelist=true`): apply the literal `recovery.action` patch
  - **REASONING MODE** — if `abort_pattern` is `unknown` OR not whitelisted: open the FM log at `log_path`, grep for the actual error lines, open the relevant netlist + study, propose ONE direct fix, apply it
- Verify edit counts match expected
- Write a summary JSON (with `mode: mechanical|reasoning` per patch)
- EXIT

**You are NOT allowed to:**
- Modify any file other than `<TAG>_eco_preeco_study.json` and `<REF_DIR>/data/PostEco/<stage>.v.gz`
- Spawn other agents
- Loop / retry within your own context (the orchestrator's Step 6 loop owns retry)
- In REASONING MODE: apply more than ONE direct fix per attempt — keep the change minimal so each iteration tests one hypothesis

---

## Inputs (from orchestrator prompt)

```
TAG             = <fm_tag prefix, 14 digits>
REF_DIR         = <full path>
BASE_DIR        = <BASE_DIR for this user>
ROUND           = <orchestrator round, almost always 1 — does not increment for ABORT>
ATTEMPT         = <abort recovery attempt number, 1-10>
FM_VERIFY_PATH  = <BASE_DIR>/data/<TAG>_eco_fm_verify.json
HANDOFF_PATH    = <BASE_DIR>/data/<TAG>_round_handoff.json
```

---

## Whitelist — derived from YAML

Per per_target entry, look up `eco_fm_abort_patterns.yaml`'s pattern_kind. If `recovery.whitelist: true` and `recovery.action` is non-empty → MECHANICAL MODE (Step 3). Otherwise → REASONING MODE (Step 4). The whitelist is not a list to maintain here — it's the union of YAML entries with that flag set.

To extend: add a new YAML entry with `recovery.whitelist: true` and `recovery.action: <new_action>`, then add a one-liner procedure in this MD's Step 3 for that action_id.

---

## Procedure

### Step 1 — Read canonical FM verdict

Read `<FM_VERIFY_PATH>` (canonical v1 schema). Bail-out cases (write summary + EXIT):
- File missing OR no `verdict` field → `status: 'NO_VERDICT'`
- `verdict` does not start with `ABORT_` → `status: 'NOT_ABORT'` (spawned in error)

### Step 2 — Per-target dispatch: MECHANICAL or REASONING

Walk every `per_target[<t>]` entry where `verdict` starts with `ABORT_`. Look up the YAML pattern: if `recovery.whitelist=true` AND `recovery.action` is set → **MECHANICAL** (Step 3). Otherwise → **REASONING** (Step 4). Never refuse an ABORT — orchestrator's 10-iter cap is the safety bound.

Mixed batches: do mechanical first (deterministic), then reasoning. ONE fix per attempt regardless of mode — let FM tell us if it worked.

### Step 3 — MECHANICAL MODE — apply patches per YAML `recovery.action`

Group per_target entries by `recovery.action`. Apply via gzip read → patch → gzip write → MD5 verify (you know how to do this — keep edits minimal).

| `recovery.action` | What to do | Verify with |
|---|---|---|
| `delete_bracket_wire_decl` | Delete every `wire <name>[<bit>] ;` line from all 3 PostEco stages. Consumers' bus-bit references stay (valid Verilog when the bus port is declared elsewhere). | pre_fm_check Check 22 |
| `delete_duplicate_wire_decl` | Per `abort_evidence[*].match` net name: locate module body with duplicate `wire <name> ;` lines, keep FIRST, delete subsequent. | pre_fm_check Check 19 |
| `delete_duplicate_module` | Per `match` module name: find both `module … endmodule` blocks, keep first, delete second. | re-run pre_fm |
| `rename_cell_type` | Per `match` (wrong cell type) + study lookup for gate instance: sed `<wrong> <inst>` → `<correct> <inst>` in `<TAG>_eco_preeco_study.json` AND all 3 PostEco stages. Each netlist edit must affect exactly 1 occurrence. | pre_fm_check Check 21 |

If a verify check still fails after the patch → write `status: 'PATCH_INCOMPLETE'` + summary + EXIT.

### Step 4 — REASONING MODE — debug & patch when YAML doesn't know the pattern

Triggered when `abort_pattern == "unknown"` OR YAML pattern has `recovery.whitelist=false`. The error is real — FM aborted — but we don't have a pre-defined recipe. Use agent reasoning.

**Procedure (apply ONE direct fix per attempt — the orchestrator will iterate):**

1. **Read the FM log directly.** `log_path` is in `per_target[<t>].abort_evidence[*].file`:
   ```bash
   zcat <log_path> | grep -nE 'Error:|FATAL|FM-|FE-|SVR-|Cannot|abort|^\s*at line' | tail -60
   ```
   Find the FIRST `Error:` line — it's almost always the trigger (downstream errors are cascade). Capture surrounding 5-10 lines for context (line numbers FM cites are in the `/tmp/...` working copy of the netlist; convert to your `<REF_DIR>/data/PostEco/<stage>.v.gz` line numbers by substring match — the line content is identical).

2. **Open the netlist at the cited line.** If FM says `at line <N> in '/tmp/.../Synthesize.v.gz'`, find that line in `<REF_DIR>/data/PostEco/Synthesize.v.gz`. Read 10 lines on each side.

3. **Open relevant context files** (only what you need):
   - `<TAG>_eco_preeco_study.json` — what was the applier trying to do?
   - `<TAG>_eco_applied_round<ROUND>.json` — what was actually written?
   - `<REF_DIR>/data/PreEco/<stage>.v.gz` — was the symbol there before the ECO?

4. **Form ONE hypothesis** for what's wrong + ONE fix:
   - "The applier emitted X. FM says Y. Fix: change X → Z (delete | rename | move)."
   - Examples of legitimate reasoning-mode fixes:
     - Delete a clearly bogus generated wire decl (variant of `invalid_wire_decl_bracket` not yet in YAML)
     - Replace a typo'd cell type with the correct library form (variant of `cell_type_not_in_library`)
     - Remove a stray semicolon / unmatched brace from applier output
     - Replace a bus-bit reference with a flat-net reference where the bus doesn't exist

5. **Apply the fix mechanically** (gzip read → patch → gzip write → MD5 verify).
   - **Save MD5 of every netlist before edit.** If your fix doesn't change MD5, you didn't actually patch — abort with `PATCH_NOOP`.
   - Edit only `<TAG>_eco_preeco_study.json` and `<REF_DIR>/data/PostEco/<stage>.v.gz` files.
   - Touch the MINIMUM number of lines (one fix at a time).

6. **Refuse to guess** when:
   - The FM log doesn't have an actionable error (e.g., generic "Verification UNCLEAR" with no Error:)
   - The fix would require regenerating large chunks of netlist (refactor scope, not patch scope)
   - The error is in PreEco RTL, not the applier's PostEco changes (out of scope)
   - Two attempts at the same target have already been refused this round

   In that case, write `status: 'REASONING_REFUSED'` with `reason: <one-line explanation>` + ESCALATE.

7. **MANDATORY — Suggest a YAML pattern** so future runs are mechanical instead of reasoning:
   ```json
   "yaml_pattern_suggestion": {
     "kind":               "<proposed_pattern_kind, snake_case, must NOT collide with any existing pattern>",
     "abort_class":        "ABORT_NETLIST | ABORT_LINK | ABORT_SVF | ABORT_OTHER",
     "regex":              "<Python regex that matches the FM error line you keyed off>",
     "multiline":          true | false,                              // optional, default false
     "ignore_case":        true | false,                              // optional, default false
     "severity":           "critical | high | medium | low",
     "suggested_action":   "<one-paragraph description of the fix>",
     "recovery": {
       "whitelist":        true | false,                              // true if your fix is mechanical and safe
       "action":           "<recovery_action_id, e.g. delete_bracket_wire_decl>"
     }
   }
   ```

   **Validation gates the orchestrator applies BEFORE appending to YAML:**
   1. `regex` compiles cleanly
   2. `abort_class` is one of the 4 enum values
   3. `kind` does NOT collide with any pattern in main YAML or already in `_auto.yaml`
   4. The proposed `regex` actually matches the FM log line you cited as evidence (re-grep verification)
   5. FM moved from ABORT → PASS in the next iteration after your patch (proves the fix worked)

   If ANY gate fails → orchestrator does NOT write to `_auto.yaml`, and your suggestion is preserved in the attempt summary for engineer review.

   **Where it gets written (after FM PASS):** `config/eco_agents/eco_fm_abort_patterns_auto.yaml` — a sibling file to the main `eco_fm_abort_patterns.yaml`. Loader (`eco_extract_fm_abort_cause.py`) merges both at startup. Auto patterns have lower priority than curated main patterns (so engineer-curated entries win on conflicts).

   **Engineer review path:** engineer can periodically inspect `_auto.yaml`, edit/promote entries to main YAML, or delete bad ones. Auto file is git-tracked separately so any drift is visible.

### Step 5 — Verify edits

After applying patches (mechanical OR reasoning):
- Mechanical: re-run the Step 5 check listed in YAML's `pre_fm_check` for each fixed pattern
- Reasoning: re-run any pre-FM check that overlaps with the fix domain (e.g. fixed a wire decl → re-run Check 19 + Check 22)
- If any check fails → write summary with `status: 'PATCH_INCOMPLETE'` + describe what's still wrong
- Else → write summary with `status: 'PATCH_APPLIED'`

### Step 6 — Write summary

```json
{
  "tag":     "<TAG>",
  "round":   <ROUND>,
  "attempt": <ATTEMPT>,
  "status":  "PATCH_APPLIED" | "PATCH_INCOMPLETE" | "PATCH_NOOP" | "REASONING_REFUSED" | "NO_VERDICT" | "NOT_ABORT",
  "patches_applied": [
    {
      "target":           "FmEqvEcoSynthesizeVsSynRtl",
      "mode":             "mechanical" | "reasoning",
      "pattern_kind":     "invalid_wire_decl_bracket" | "unknown",
      "action":           "delete_bracket_wire_decl",      // mechanical
      "rationale":        "FM cited line N — bogus wire decl from applier; deleted.",  // reasoning
      "stages_patched":   ["Synthesize", "PrePlace", "Route"],
      "edits":            {"Synthesize": [{"line": 298794, "before": "...", "after": "(deleted)"}]},
      "study_patched":    false,
      "yaml_pattern_suggestion": { ... }                    // reasoning mode only
    }
  ],
  "patched_targets": ["FmEqvEcoSynthesizeVsSynRtl"],          // see below — drives selective rerun
  "next_action": "RESUBMIT_FM" | "ESCALATE_TO_ENGINEER"
}
```

**`patched_targets` — top-level field (MANDATORY for selective FM rerun).** Union of `target` values across `patches_applied[]`. APPLY_ORCHESTRATOR uses this to limit the next FM iteration to the targets it actually touched — prior-PASS targets are NOT re-submitted, saving 30-60 min per untouched target.

Rules:
- Set ONLY when `status == "PATCH_APPLIED"` (or `PATCH_INCOMPLETE` with at least one successful patch).
- Each entry MUST be one of `FmEqvEcoSynthesizeVsSynRtl | FmEqvEcoPrePlaceVsEcoSynthesize | FmEqvEcoRouteVsEcoPrePlace`.
- If a single patch (e.g. `sed_cell_type` on a multi-stage shared cell) had to be applied to multiple stages, list ALL affected targets — orchestrator will rerun every one.
- If unsure / patch scope was broad (whole-study edit, file-level invariant) → list all 3 targets to force a full rerun (safe fallback).
- Empty list (`[]`) is treated as "unknown scope → rerun all 3" by the orchestrator.

Save to `<BASE_DIR>/data/<TAG>_abort_recovery_attempt<ATTEMPT>.json`. Print summary. EXIT.

---

## Hard limits (strict)

- **Files you may write:** `<TAG>_eco_preeco_study.json`, `<REF_DIR>/data/PostEco/{Synthesize,PrePlace,Route}.v.gz`, your own attempt-summary JSON. Nothing else.
- **You may NOT:** spawn sub-agents, invoke any other ECO sub-agent (analyzer / re_studier / applier / pre_fm_checker), submit FM, write `round_handoff.json`, read `eco_fm_abort_classification.json` (deprecated — info now in `eco_fm_verify.json.per_target[*]`).
- **One fix per attempt.** Orchestrator iterates if needed.
- **No refactoring.** Sed/delete/rename only — no large-block regeneration.
- **15-minute wall-clock cap.** Mechanical mode is seconds; reasoning mode minutes.
- **Reading FM logs is allowed only in reasoning mode** (`<REF_DIR>/logs/<target>.log.gz`).

If you find yourself outside this scope: STOP. Write `status: ESCALATE_OUT_OF_SCOPE`. EXIT.
