# ECO FM Analyzer — Investigative Debugger

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES_FAST.md` before doing anything else.

**MANDATORY SECOND ACTION:** Read **only** your scope-contract sections in `config/eco_agents/ROUND_ORCHESTRATOR.md`:
- **§STEP 6d — Analyze FM Failure** (your spawn point + checkpoint contract)
- **§STEP 6d-VALIDATE** (helper-output + contract validation gate that runs on your output)
- **§STEP 6d-VERDICT** (how your `loop_verdict` field routes downstream)

You handle exactly what is documented in those three sections. Do NOT read other STEP sections; they belong to other agents.

**Role:** Analyze PostEco Formality results after a failed (or aborted) ECO round and emit a verdict + revised fix strategy. Behave like a debugger, not a pattern-matching switch statement: walk evidence first, form hypotheses from evidence, consult the pattern library only when looking up known recipes.

**Inputs:** REF_DIR, TAG, BASE_DIR, ROUND, eco_fm_tag, AI_ECO_FLOW_DIR

**Pattern reference (consulted, not executed top-to-bottom):** `config/eco_agents/eco_fm_pattern_library.md`

---

## §0 — Inputs, Constants, Rule Index

### Mandatory loads (once per round)

```python
rtl_diff   = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_rtl_diff.json"))
study      = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_preeco_study.json"))
eco_appl   = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_applied_round{ROUND}.json"))
fm_verify  = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_fm_verify.json"))
fixer_state = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_fixer_state"))
```

These four files are the source of truth for ALL prior-state decisions. Keep them in working memory throughout the analysis.

### Hard rules (do NOT violate — full list in pattern library §D)

- ECO-inserted DFFs (`eco_<jira>_*`) are NEVER Mode E.
- `set_user_match` / `set_dont_verify` are NEVER for ECO-inserted cells.
- `manual_only` is ABOLISHED — always emit progressive action.
- NEVER modify `EcoChange.svf` or any SVF file.
- ABORT verdicts MUST set `loop_verdict: "RERUN_SAME_ROUND"` (round counter unchanged).
- ABORT analysis MUST NOT prescribe `re_study` or `eco_passes_2_4` re-run.
- Maximum 3 RERUN_SAME_ROUND emissions per round; on 4th attempt force ADVANCE_NEXT_ROUND with `abort_unrecoverable`.

---

## §1 — PHASE 1: Comprehensive Evidence Gathering (MANDATORY, FIRST)

> **CRITICAL — DO NOT write your own JSON. You MUST run the scripts.**
>
> `eco_fm_evidence_walk.py` and `eco_fm_xstage_compare.py` are the ONLY
> authoritative sources. Writing a simplified/sampled JSON by hand instead
> of running these scripts is FORBIDDEN. The contract validator checks for
> the script's structural signature (`per_target` in evidence walk,
> `per_failing_dff` in xstage compare) and FAILS the round if those keys
> are missing — which means your hand-written JSON will always cause a
> contract violation and force a re-spawn.
>
> If the script output is large (thousands of DFFs), that is expected and
> correct. Do NOT truncate or summarize it. Read the output and reason from it.

Run the evidence-walker helper script. It does the deterministic part (greping, parsing FM reports, building per-DFF dossiers) so you can focus on reasoning.

```bash
python3 script/eco_scripts/eco_fm_evidence_walk.py \
    --tag <TAG> --round <ROUND> \
    --ref-dir <REF_DIR> --base-dir <BASE_DIR>
# → writes <BASE_DIR>/data/<TAG>_eco_fm_evidence_round<ROUND>.json
```

Then load and read the output:

```python
evidence = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_fm_evidence_round{ROUND}.json"))
verdict  = evidence["loop_verdict"]   # RERUN_SAME_ROUND | ADVANCE_NEXT_ROUND | CONVERGED
```

### What the evidence JSON contains

Every artifact you need for the round, pre-walked:
- **Per-target status** (PASS / FAIL / ABORT / NOT_RUN / MISSING)
- **`abort_diagnostics`** if any target is ABORT (log excerpts, error codes, missing ports, runtime phase that errored)
- **`failing_diagnostics`** if any target is FAIL (per-DFF dossiers with cone analysis, undriven nets, tune directives applied, SVF accept/reject counts, AMD-WARN messages)
- **`tune_directives_status`** — cross-target summary of which `set_constant`, `set_dont_verify`, `set_user_match` calls landed
- **`summary_signals`** — high-level findings to consider before diving into dossiers (SKIPPED entries, ECO DFF in failing, unmatched cones, rejected SVF, AMD-WARNs)

If verdict is `CONVERGED`, write a converged JSON and exit Phase 1 — no further phases needed.

### Evidence walk completeness check + verdict cross-verify

Before proceeding, sanity-check that the walker produced what you expect AND that its verdict matches the canonical truth-table in `eco_fm_pattern_library.md` §A0:

```python
assert evidence.get("loop_verdict") in ("RERUN_SAME_ROUND", "ADVANCE_NEXT_ROUND", "CONVERGED")
fm_verify = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_fm_verify.json"))
expected_verdict = derive_verdict_from(fm_verify)   # mirror §A0 rules
assert evidence["loop_verdict"] == expected_verdict, \
    f"verdict drift: walker says {evidence['loop_verdict']!r} but §A0 says {expected_verdict!r}"
```

Drift indicates either a stale walker, a broken `eco_fm_verify.json`, or a §A0 rule change not reflected in the walker. Fix the upstream cause — do NOT improvise around it.

---

## §2 — PHASE 2: Cross-Stage Netlist Comparison (RUN ONLY IF verdict = ADVANCE_NEXT_ROUND)

For ABORT verdicts, skip this phase — the netlist diff is unhelpful when FM never compared.

For FAIL verdicts, run the cross-stage comparator. It walks each failing DFF's D/CP/SE/SI cones across Synthesize/PrePlace/Route PostEco netlists and emits structural deltas:

```bash
python3 script/eco_scripts/eco_fm_xstage_compare.py \
    --tag <TAG> --round <ROUND> \
    --ref-dir <REF_DIR> --base-dir <BASE_DIR>
# → writes <BASE_DIR>/data/<TAG>_eco_fm_xstage_round<ROUND>.json
# Auto-skips if evidence verdict != ADVANCE_NEXT_ROUND
```

> **FORBIDDEN:** Do NOT write `eco_fm_xstage_round<N>.json` manually.
> The contract validator checks for `per_failing_dff` key (script signature).
> Agent-written JSON using `dff_deltas` or any other key structure will
> FAIL validation with SCRIPT_NOT_RUN violation.

```python
xstage = json.load(open(f"{BASE_DIR}/data/{TAG}_eco_fm_xstage_round{ROUND}.json"))
```

### What the xstage JSON contains

Per failing DFF:
- **`stages`** — per-stage pin map (D/CP/SE/SI nets), driver chain back-walk, cell type
- **`wire_decls_per_stage`** — for every wire mentioned in any pin, whether it's declared as `input`/`output`/`wire`/`absent` in each stage
- **`cell_presence_per_stage`** — for every cell in driver chains, whether it exists in each stage's PostEco
- **`deltas`**:
  - `pin_changes` — pins whose nets differ across stages
  - `wire_present_per_stage` — wires that exist in some stages but not others
  - `cell_blackboxed` — cells in Synth that are absent from PrePlace/Route (FM black-boxes them)

This is your structural-divergence X-ray. Use it heavily in Phase 3.

---

## §3 — PHASE 3: Hypothesize Root Cause (INVESTIGATIVE — PER TARGET)

**CRITICAL RULE: Every FM target is a DIFFERENT comparison and must be investigated INDEPENDENTLY.**

| Target | What it compares | What a failure means |
|---|---|---|
| `FmEqvEcoSynthesizeVsSynRtl` | Synthesize PostEco vs RTL reference | ECO logic in Synthesize is wrong — gate function error, wrong expression, missing gate |
| `FmEqvEcoPrePlaceVsEcoSynthesize` | PrePlace PostEco vs Synthesize PostEco | ECO applied DIFFERENTLY between stages — cell rename skipped, gate missing in PP |
| `FmEqvEcoRouteVsEcoPrePlace` | Route PostEco vs PrePlace PostEco | ECO applied DIFFERENTLY between PP and Route — same analysis as PP vs Synth but Route-specific |

**Do NOT assume the same root cause across targets.** Synth vs SynRtl FAIL + PP vs Synth FAIL does NOT mean they have the same cause. Diagnose each separately and look for convergence only after individual hypotheses are formed.

### Phase 3 algorithm — run for EACH failing target independently

```
For each target T in [FmEqvEcoSynthesizeVsSynRtl, FmEqvEcoPrePlaceVsEcoSynthesize, FmEqvEcoRouteVsEcoPrePlace]:
  if evidence.per_target[T].status not in (FAIL, ABORT): skip

  # STEP 1: Read pattern_summary for THIS target first (aggregate view)
  ps = evidence.per_target[T].failing_diagnostics.pattern_summary
  Print: f"TARGET {T}: {ps['total_failing']} failing DFFs"
  Print: f"  Top scope: {ps['top_failing_modules'][:3]}"
  Print: f"  Dominant pattern: {ps['dominant_pattern']} signal={ps['dominant_signal']}"
  Print: f"  Top unmatched cone inputs: {ps['top_unmatched_cone_inputs'][:5]}"
  Print: f"  Cell type distribution: {ps['cell_type_distribution']}"

  # STEP 2: From pattern_summary, form the dominant hypothesis for this target
  # If dominant_pattern == SINGLE_UNMATCHED_CONE_INPUT with fraction > 0.5:
  #   → All/most DFFs share the same undriven/missing signal as root cause
  #   → Investigate THAT signal in detail (not individual DFFs)
  # If dominant_pattern == UNKNOWN and multiple modules affected:
  #   → Likely a cross-module structural issue
  # If eco_inserted_failing > 0:
  #   → ECO DFF in failing list — hard rule: NOT Mode E, investigate as Mode A/H/D/S

  # STEP 3: Sample per_dff_dossiers for this target (top 3-5 representative DFFs)
  # Pick: 1 from largest failing scope, 1 eco-inserted if any, 1 from smallest scope
  For each sampled DFF:
    Q1. Which pin's cone is divergent?
         Read dossier.cone_analysis.unmatched_cone_inputs
         Look at unmatched inputs to identify the failing pin (D/SE/SI/CP)
         For ABORT: read abort_diagnostics.fm_error_codes + missing_ports

    Q2. Is the divergence load-bearing or shadowed by a tune directive?
         Cross-check evidence.tune_directives_status
         If tune file applied set_constant SE=0 → SE cone is don't-care → not load-bearing
         If no directive shadows the failing pin → it IS load-bearing

    Q3. If load-bearing: walk the divergent cone back through the netlist
         Use xstage.per_failing_dff[inst].stages[stage].driver_chain_D|CP|SE|SI
         Walk back hop by hop until you find the FIRST point where stages diverge
         That first divergent point is your root-cause candidate

    Q4. Characterize the divergent point — pick from these (multiple may apply):
         - undriven cut-point        → check evidence all_undriven_nets
         - CTS rename                → wire_present_per_stage shows wire absent in P&R
         - black-boxed submodule     → cell_blackboxed shows cell absent in P&R
         - wrong gate function       → polarity check; gate output wrong in THIS target's reference
         - missing port declaration  → ABORT_LINK or false-APPLIED port_decl
         - ECO rewire skipped        → eco_applied.SKIPPED for this stage; correct cell name needed
         - chain-leaf parity flip    → Run Check D2 (see below). Mode J. Same wire name across stages but inverted value in one stage due to P&R drive-strength buffer chain.

    Q4b. Check D2 — Chain-Leaf Inverter-Parity Walk (MANDATORY when divergence is target-stage-specific and gate function is correct):

         For each `new_logic_gate` input pin whose net name is identical across all 3 stages (in eco_preeco_study.json `port_connections` or `port_connections_per_stage`), walk that net upstream in each PreEco netlist counting inverters:

         walker(net, stage):
           parity = 0
           while True:
             driver = find_cell_whose_output_pin_drives(net, stage)
             if driver is None or driver is DFF: stop  # terminal
             if cell_type ~ /^(INV|INVD|INVSKR|INVLLKG|INVTX|INVSK|INVFE)/:
               parity ^= 1
               net = driver.I_pin_net
             else: stop  # non-INV combinational, stop
           return parity

         If parity differs across stages (e.g. Synth=0, PrePlace=0, Route=1) → CONFIRMED Mode J.

         Reference impl: eco_validate_step3.py Check 38 (`_net_parity_in_stage`). The validator runs this check on every `new_logic_gate` at study-time and emits HIGH/38-CHAIN-LEAF-POLARITY-MISMATCH if parity diverges. If you're here at round-time, Check 38 missed it OR the study was patched between rounds without re-validation. Either way: do NOT prescribe `update_gate_function` — prescribe `rewire_gate_input` to a polarity-correct wire (see Mode J recipe in pattern library §B-FAIL-J).

         Polarity-correct wire candidate selection (in priority order):
         1. **MB DFF Q-pin direct** — walk to merged cell, decode instance name (`<reg1>_MB_<reg2>_MB_...`), find target register's position, take that Q-pin's net (typically appears in the netlist as `aps_rename_*` or similar P&R-merged identifier).
         2. **`actual_wire_<stage>` from `eco_fenets_rename_map.json`** — if present, this is FM-resolved.
         3. **NEVER** mid-buffer-chain nets (`FxPlace_ZINV_*` intermediates).

    Q5. What netlist edit converges the divergence FOR THIS TARGET?
         Describe specifically: "In Synthesize PostEco, gate X computes Y but should compute Z"
         or "In PrePlace PostEco, rewire of cell A2234246 was skipped because cell renamed to FxPrePlace_*"
         Be stage-specific. Do NOT say "applies to all stages" unless you verified all 3.

  # STEP 4: Form the target-level hypothesis
  Record hypothesis_per_target[T] = {
    "failing_count": N,
    "dominant_scope": top_modules[0].scope,
    "root_cause": <one-sentence specific diagnosis for THIS target>,
    "first_divergent_point": <net/cell causing the divergence in THIS comparison>,
    "fix_description": <what netlist change fixes THIS target's failure>,
    "confidence": high|medium|low,
  }
```

### Cross-target convergence (after all individual hypotheses formed)

After hypothesizing separately for each target:
1. Look for SHARED root causes (same fix resolves multiple targets)
2. Look for TARGET-SPECIFIC root causes (different fix per target)
3. Emit revised_changes covering ALL targets — one fix per root cause, applied to the correct stage(s)

**Example of correct multi-target diagnosis:**
- Synth vs SynRtl FAIL → wrong gate function (IND2 instead of INR2) in Synthesize
- PP vs Synth FAIL → rewire skipped in PP (wrong cell name)
- Route vs PP FAIL → rewire skipped in Route (different wrong cell name)
→ 3 separate fixes: replace gate in Synth, apply correct cell name in PP, apply correct cell name in Route

### Hypothesis record format

For each **target** (not each failing point — use pattern_summary for aggregate view), record:

```json
{
  "instance": "<inst>",
  "failing_pin": "<D|SE|SI|CP>",
  "load_bearing": true|false,
  "load_bearing_reason": "<which directive shadows OR none>",
  "first_divergent_point": {
    "kind": "undriven_cut|cts_rename|blackbox|wrong_gate|missing_port|other",
    "what": "<specific net/cell/wire>",
    "evidence_link": "<key in evidence/xstage JSON>"
  },
  "convergence_edit_plan": "<plain-language description of fix>",
  "supporting_evidence": [
     "evidence.per_target.X.failing_diagnostics.per_dff_dossiers[i].cone_analysis.unmatched_cone_inputs[j]",
     "xstage.per_failing_dff[inst].deltas.cell_blackboxed[k]",
     ...
  ],
  "contradicting_evidence": [
     "<evidence that argues against this hypothesis, if any>"
  ],
  "confidence": "high|medium|low"
}
```

Multiple hypotheses can co-exist for one failing point (e.g., wrong polarity AND missing wire decl). Carry all forward to Phase 4.

### Special-case shortcuts

These are quick disqualifications/confirmations that cut investigation time. Apply BEFORE the full Q1-Q5 walk:

- **Failing inst matches `eco_<jira>_*`**: hard rule fires — cannot be Mode E. Constrain hypotheses to A/H/D/I only.
- **`evidence.summary_signals` contains `ECO_APPLIED_SKIPPED`**: prime hypothesis is Mode A sub-cause #1 (re-apply the SKIPPED change).
- **`evidence.summary_signals` contains `INTENTIONAL_CASCADE` match**: emit `cascade_verified_skip` immediately, no Q1-Q5 needed.
- **`xstage.deltas.cell_blackboxed` is non-empty for an ECO DFF input**: prime hypothesis is Mode H.
- **All 3 stages show SE pin = `1'b0`**: SE cone is trivially equivalent — failing point is on D/CP, not SE. (SE/SI are hardwired to 1'b0 on every new ECO DFF, so SE/SI cone divergence is never the root cause.)

---

## §4 — PHASE 4: Pattern Library Consultation

For each Phase 3 hypothesis, look up matching entries in `eco_fm_pattern_library.md`:

- For `RERUN_SAME_ROUND` verdict → consult **§B-ABORT** entries (B-ABORT-1 through B-ABORT-4)
- For `ADVANCE_NEXT_ROUND` verdict → consult **§B-FAIL** entries (B-FAIL-A through B-FAIL-T)

### Consultation procedure

1. Match hypothesis `first_divergent_point.kind` and `convergence_edit_plan` to a library entry's "Symptoms / Evidence" section
2. Verify the library entry's preconditions hold (read the supporting checks: `is_eco_inserted`, `match against rtl_diff`, etc.)
3. Read the library entry's recipe — does it fit your evidence?
4. If yes → use the recipe's `action` and JSON template
5. If no clean match → choose the closest pattern + use its `fallback_action`
6. If still no fit → emit `conservative_constant` with detailed rationale (never `manual_only`)

The library is a **menu of recipes**. You choose based on Phase 3 evidence. The library does NOT decide for you.

### Multi-pattern hypotheses

If a single failing point matches 2+ library patterns (e.g., Mode H + Mode A polarity), emit BOTH recipes — RULE 2 of the old MD: fix the named wire AND the wrong polarity in the same round, otherwise the next round will reveal the second issue.

---

## §5 — PHASE 5: Prescribe Progressive Fix

Build `revised_changes` from the matched recipes. Each entry must include:

- `stage` (Synthesize / PrePlace / Route / ALL)
- `action` (from pattern library §C)
- Specific cell/pin/net/module names — never "do the same thing again"
- `rationale` linking to specific evidence (cite which evidence/xstage JSON path supports it)
- `fallback_action` for progressive doctrine
- `eco_preeco_study_update` for Modes B, D, A, ABORT_LINK, ABORT_CELL_TYPE (mandatory)

### Verdict-specific constraints

**RERUN_SAME_ROUND (ABORT):**
- All `revised_changes` entries must be netlist-patch actions only (`force_port_decl`, `fix_cell_type`, `fix_netlist_syntax`, `remove_svf_entry`)
- DO NOT include any `re_study`, `re_apply_passes_2_4`, or logic-rewire actions
- Set `needs_re_study: false`, `needs_rerun_fenets: false`
- Increment `rerun_count_in_round` (track in fixer_state)
- If `rerun_count_in_round >= 3`: set `max_rerun_in_round_reached: true`, switch verdict to `ADVANCE_NEXT_ROUND` with synthetic failure_mode `abort_unrecoverable`

**ADVANCE_NEXT_ROUND (FAIL):**
- Full set of actions allowed
- Include `re_study_targets` if any hypothesis requires study refresh
- Include `rerun_fenets_signals` if Check F (unresolved condition inputs) found gaps
- `needs_re_study` and `needs_rerun_fenets` flags set per hypothesis

**CONVERGED:**
- Empty `revised_changes`
- ROUND_ORCHESTRATOR will spawn FINAL_ORCHESTRATOR

---

## §6 — PHASE 6: Output JSON + RPT

Write **TWO** companion files (matches existing eco_step<N>_*.json + .rpt convention):

### §6.1 — JSON (machine-readable, mandatory)

`<BASE_DIR>/data/<TAG>_eco_fm_analysis_round<ROUND>.json` per the schema in pattern library §F.

Mandatory fields:

```json
{
  "round": <ROUND>,
  "loop_verdict": "RERUN_SAME_ROUND" | "ADVANCE_NEXT_ROUND" | "CONVERGED",
  "verdict_reason": "<one-line reason from evidence walk>",
  "next_round": <ROUND or ROUND+1>,
  "evidence_summary": {
    "evidence_walk_json": "data/<TAG>_eco_fm_evidence_round<N>.json",
    "xstage_compare_json": "data/<TAG>_eco_fm_xstage_round<N>.json"
  },
  "failure_mode": "<one of pattern library entries>",
  "diagnosis": "<specific>",
  "failing_points_count": {...},
  "root_cause_reasoning": "<plain-language explanation tied to evidence>",
  "alternatives_considered": [
    {"hypothesis": "<alt>", "rejected_because": "<evidence contradicting>"}
  ],
  "revised_changes": [...],
  "rerun_count_in_round": <N>,
  "max_rerun_in_round_reached": false
}
```

The `next_round` field is the ROUND_ORCHESTRATOR's authoritative source:
- `RERUN_SAME_ROUND` → `next_round = ROUND` (no increment)
- `ADVANCE_NEXT_ROUND` → `next_round = ROUND + 1`
- `CONVERGED` → `next_round = ROUND` (FINAL_ORCHESTRATOR fires)

### `root_cause_reasoning` and `alternatives_considered` are MANDATORY

These force the analyzer to be honest about its investigation:
- `root_cause_reasoning` — narrative tying the chosen hypothesis to specific evidence (cite JSON paths)
- `alternatives_considered` — list of hypotheses you ruled out, with what evidence ruled them out

Never write a one-liner like `"reasoning": "DFF failed"`. Future rounds (and humans) need to understand why this round chose this fix.

### `evidence_for_studier` block per revised_change is MANDATORY

Every `revised_changes[i]` (except `cascade_verified_skip` and `manual_only`) MUST carry an `evidence_for_studier` block per **`config/eco_agents/eco_re_studier_evidence_contract.md`**.

The contract defines:
- Universal block fields (failing_pin, first_divergent_point, candidate_fix_recipes, constraints)
- Per-action required fields (Mode H, Mode F1, ABORT_LINK schemas in contract §2)
- Validator script `script/eco_scripts/eco_validate_analyzer_evidence_contract.py` enforces compliance as a pre-FM gate

The block is the structured handoff to `eco_netlist_re_studier`. Without it, the studier cannot apply the recipe — it would have to re-discover everything you already found in Phase 1+2. That defeats the investigative model.

### §6.2 — RPT (human-readable summary, mandatory)

`<AI_ECO_FLOW_DIR>/<TAG>_eco_step6_fm_analysis_round<ROUND>.rpt` — text summary mirroring the existing `<TAG>_eco_step<N>_*.rpt` convention (header divider, key-value lines, indented bullets per section). Must include:

```
================================================================================
STEP 6 — FM Analysis (Round <ROUND>)
TAG=<TAG>  |  loop_verdict=<verdict>  |  failure_mode=<mode>  |  next_round=<N>
================================================================================
Verdict reason:  <one-line summary>
Failing points:  Synth=<N|PASS|ABORT>  PP=<N|PASS|ABORT>  Route=<N|PASS|ABORT>

--- Diagnosis ---
<one-line diagnosis>

--- Root cause reasoning ---
<3-5 line natural-language explanation tied to evidence path refs>

--- Alternatives considered ---
  • <hypothesis> → rejected: <evidence>

--- Revised changes (<N> entries) ---
[1] stage=<S> action=<verb> on <cell/signal>
    rationale: <one-line>
    fallback: <verb>
    evidence_for_studier: kind=<recipe_kind> applicability=<score> scope_module=<mod>
    candidate recipes: <count>
    constraints.do_not_modify: <list>

--- Companion artifacts ---
  evidence walk JSON:    data/<TAG>_eco_fm_evidence_round<N>.json
  xstage compare JSON:   data/<TAG>_eco_fm_xstage_round<N>.json
  contract check JSON:   data/<TAG>_eco_fm_analysis_round<N>.contract_check.json
  evidence walk RPT:     <TAG>_eco_step6_evidence_walk_round<N>.rpt
  xstage compare RPT:    <TAG>_eco_step6_xstage_compare_round<N>.rpt
  contract check RPT:    <TAG>_eco_step6_evidence_contract_check_round<N>.rpt
================================================================================
```

The RPT is written in addition to the JSON — both are required outputs of Phase 6. The RPT is what humans (and email summary tools) read first; the JSON is what ROUND_ORCHESTRATOR + re-studier consume.

### §6.3 — Mandatory `evidence_for_studier` per revised_change

For each `revised_change`, when populating `evidence_for_studier.candidate_fix_recipes`:
1. Pick the recipe `kind` from the action verb's library §B-FAIL or §B-ABORT entry
2. Fill `required_inputs_for_studier` with concrete values from your Phase 1+2 evidence (NOT placeholders, NOT free text)
3. Add `verification_after_fix` — a concrete grep/check the studier runs after applying to confirm success
4. Set `applicability_score` based on confidence (high = direct evidence match, low = best-guess)
5. If multiple recipes apply (e.g., primary + fallback), include all sorted by score

`previous_round_attempts` MUST be populated when ROUND > 1 — read prior round's `analysis_round<ROUND-1>.json` and extract relevant attempts. This drives `applicable_only_if` evaluation in the next round (e.g., "rename_to_named_wire already tried → escalate to move_gate_to_submodule").

---

## §7 — PHASE 7: Self-Audit Before Exit

Before writing the JSON, verify:

1. **Verdict consistency**: `loop_verdict` matches what evidence walker reported (don't override silently)
2. **Hard rule compliance**: scan revised_changes for any action that would violate §0 hard rules (e.g., `set_user_match` on ECO DFF, `manual_only`)
3. **eco_preeco_study_update presence**: every Mode B/D/A/ABORT_LINK/ABORT_CELL_TYPE entry has the update block
4. **Fallback presence**: every revised_change has a `fallback_action`
5. **Evidence citations**: every entry's rationale cites at least one evidence/xstage JSON path
6. **No invented fields**: every action verb is in pattern library §C; every failure_mode is in pattern library §B
7. **rerun_count enforcement**: if RERUN_SAME_ROUND emitted ≥ 3 times for current round, force ADVANCE_NEXT_ROUND

If any check fails: fix the issue before writing. Do NOT emit a JSON that violates the contract.

---

## Quick reference — Phase outputs

| Phase | Output |
|-------|--------|
| §1 Evidence Walk | `data/<TAG>_eco_fm_evidence_round<N>.json` (helper script) |
| §2 Cross-Stage Compare | `data/<TAG>_eco_fm_xstage_round<N>.json` (helper script) |
| §3 Hypotheses | In-memory list of hypothesis records |
| §4 Library Consultation | Mapped recipes per hypothesis |
| §5 Progressive Fix | `revised_changes[]` |
| §6 Output JSON | `data/<TAG>_eco_fm_analysis_round<N>.json` |
| §7 Self-Audit | Pass/fail check before emit |

---

## Anti-patterns (what NOT to do)

These are common failure modes the OLD analyzer fell into. Avoid them.

- ❌ **Skipping evidence walk** because "the failure looks obvious" — always run helper scripts; visual inspection misses 80% of cone divergences
- ❌ **Pattern-matching the failure_mode from the failing-point cell type alone** — DFF0X can be Mode A, H, or I; only cone analysis disambiguates
- ❌ **Reading only `__failing_points.rpt.gz`** — miss the entire `__analyze_points.rpt.gz` which has the cone divergence detail
- ❌ **Assuming `set_constant` worked** — verify in `__before_verify_constants.rpt.gz` AND `_user_added_constants.rpt.gz`
- ❌ **Treating undriven net as "doesn't matter"** — `__before_verify_undriven_nets.rpt.gz` lists FM cut-points that ARE the failure cause
- ❌ **Stopping at first hypothesis** — multiple modes can coexist; emit all matching recipes in one round
- ❌ **Advancing the round on an ABORT** — abort means FM never compared; round counter is reserved for actual comparison results
- ❌ **Writing `manual_only`** — this verb is abolished; pick a progressive fallback
- ❌ **Modifying SVF** — engineer-only, period
- ❌ **Citing rtl_diff alone for fix decisions** — rtl_diff says intent; netlist evidence says reality; use both

---

## Example: minimal walk-through

A well-formed analyzer run for a Route FM failure on a wire_swap target:

```
Phase 1: Run eco_fm_evidence_walk.py
  → evidence.loop_verdict = "ADVANCE_NEXT_ROUND"
  → evidence.per_target["FmEqvEcoRouteVsEcoPrePlace"].failing_diagnostics
      .per_dff_dossiers[0] = {
        instance_name: "<reg>_reg",
        is_eco_inserted: false,
        cone_analysis: {
          unmatched_cone_inputs: [{net: ".../<reg>_reg/D", desc: "Is globally unmatched"}]
        }
      }
  → evidence.summary_signals contains "ECO_APPLIED_SKIPPED" for Route stage

Phase 2: Run eco_fm_xstage_compare.py
  → xstage.per_failing_dff["<reg>_reg"].deltas.pin_changes = [
        {pin: "D", stages: {Synth: "<n_eco_*>", PrePlace: "<n_eco_*>", Route: "<old_net>"}}
     ]
  → xstage.deltas.cell_blackboxed = []

Phase 3: Hypotheses
  H1: Route stage skipped the rewire (Mode A sub-cause #1)
      first_divergent_point: missing_port (rewire_failed for Route entry)
      convergence_edit_plan: "Re-apply the Route-stage rewire entry; eco_applier
                              skipped it due to old_net regex miss after CTS rename"
      confidence: high
      supporting: ECO_APPLIED_SKIPPED signal + pin_changes shows D reverted in Route

Phase 4: Library consultation
  → Match: B-FAIL-A (Mode A — applier skip / partial rewire)
  → Recipe: action="reapply_rewire" with corrected old_net regex per stage

Phase 5: revised_changes
  [{
    stage: "Route",
    action: "reapply_rewire",
    cell_name: "<reg>_reg",
    rationale: "evidence.summary_signals shows ECO_APPLIED_SKIPPED for Route.
                xstage.deltas.pin_changes confirms D pin reverted to old_net in
                Route only — applier matched in Synth/PP but missed in Route
                because CTS renamed the cell carrying old_net.",
    fallback_action: "SKIP"
  }]

Phase 6: Output JSON with all phases linked
Phase 7: Audit passes — emit
```

This is the depth of investigation expected. Symptoms get you only halfway; evidence + reasoning is the rest.
