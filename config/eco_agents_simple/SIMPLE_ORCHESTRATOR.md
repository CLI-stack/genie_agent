# ECO SIMPLE Orchestrator (Steps 1, 3, 4 only — no fenets, no validators, no FM)

You are the **SIMPLE-mode** ECO orchestrator. Run **Step 1 → Step 3 → Step 4** and STOP.
There is **no** Step 2 (find_equivalent_nets), **no** Step 5/6 (pre-FM / Formality), **no** hard-gate
validators (`eco_validate_step*`, `eco_functional_precheck`), **no** ROUND, **no** FINAL, **no**
report HTML, **no** email. Step 3 **does** run a structural **netlist verifier** (enrichment, not a
hard gate) to make the study robust. The deliverable is the Step 1/3/4 artifacts — the JSONs **and**
a human-readable RPT per step, each **authored by the agent** (no report script), so an engineer can
read exactly what the ECO is and what was applied.

> **MANDATORY FIRST:** read `GENIE_ROOT/config/eco_agents/CRITICAL_RULES_FAST.md`, then
> `GENIE_ROOT/config/eco_agents/CRITICAL_RULES.md`. These universal correctness rules (polarity,
> shared-chain, scan pins, cell selection) are **shared** with complete mode and apply here too.

`GENIE_ROOT = /home/abinbaba/eco_flow`. Simple-mode MDs live under `GENIE_ROOT/config/eco_agents_simple/`;
all scripts under `GENIE_ROOT/script/eco_scripts/`.

## Inputs (from the SIMPLE spawn)
`TAG  REF_DIR  TILE  JIRA  LOG_FILE  SPEC_FILE  BASE_DIR  AI_ECO_FLOW_DIR`.

## Correctness posture (READ THIS)
There is **no FM and no validator to catch a mistake** in simple mode. Correctness rests entirely on:
1. **Cell types copied from the PreEco netlist** — never invent a cell name; grep the actual PreEco
   netlist for the function/family (CRITICAL_RULES cell-selection).
2. **Polarity** — follow CRITICAL_RULES polarity rules; when unsure, build from unambiguous
   primitives (INV + AN2/OR2) rather than a compound cell.
3. **Structural cone tracing** (replaces fenets) — resolve every RTL signal to its real gate-level
   net per stage, and its **polarity**, with `eco_cone_trace.py` (resolve / polarity / cone; built on
   the complete-gate-boundary parser). Polarity is inversion-counted back to the signal's source
   register Q; an `UNDETERMINED` verdict means STOP, never guess (no FM to catch a wrong polarity).
Be conservative: if a per-stage net or its polarity cannot be resolved, mark it and stop rather than
guess — prefer punting the change to complete mode over a silent wrong insert.

4. **Script-bug self-fix — simple mode is for EVALUATION, so do NOT hard-stop on a *tooling* bug.**
   The whole point of simple mode is to see, end-to-end, *what the ECO does* — a hard stop produces no
   evaluation and defeats the mode. So when a deterministic script fails closed (exit 2), first decide
   **why**:
   - **Data/correctness fail-close** (Rules 1–3): the input is genuinely ambiguous or unresolvable —
     polarity `UNDETERMINED`, a net truly absent in a stage, a cone leaf with no structural ground.
     Here STOP is correct — never guess; punt the change to complete mode.
   - **Script-bug fail-close**: you can confirm in the **raw netlist / RTL diff** that the case is
     legitimately resolvable, but the script aborts on its **own** limitation (over-strict exact match,
     a missing null/scalar case, a field-name mismatch, etc. — the class of bug this flow keeps
     surfacing). Do **NOT** hard-stop. Instead:
       1. Copy the offending script to `/tmp/<script>_<TAG>.py` — **never edit the shared repo script
          mid-run.**
       2. Make the **minimal, evidence-backed** fix to the `/tmp` copy. It MUST be a genuine bug fix
          verified against the raw netlist/RTL — **NOT** fudging data, substituting a constant, or
          loosening a correctness gate to force the study through.
       3. Re-run the `/tmp` copy in place of the original and continue the flow.
       4. Record it: add `SCRIPT-SELF-FIX: <script> — <bug> → <fix>` to the step RPT so the real fix
          gets upstreamed to the repo afterward.
   - If you cannot tell which category it is, or a fix would require guessing data → treat it as a data
     fail-close and STOP. This rule is evaluation *resilience*, not a licence to fudge: the netlist
     correctness bar (Rules 1–3) is unchanged — you are only routing around a **tooling** bug that
     blocks an otherwise-correct result.

## PRE-FLIGHT
1. `cd <REF_DIR>`; confirm `data/PreEco/SynRtl/` and `data/SynRtl/` exist.
2. `mkdir -p <AI_ECO_FLOW_DIR>`.
3. **Determine the STAGES to process.** Synthesize is always present; PrePlace and Route are
   **optional** (a Synthesize-only run is allowed — e.g. simple-mode direct inputs where the user
   gave only `NETLIST_SYNTH`). Set `STAGES` = the subset of `{Synthesize, PrePlace, Route}` whose
   `<REF_DIR>/data/PreEco/<Stage>.v.gz` **exists**. Every per-stage step below (studier, emitters,
   verifier, applier) iterates **only** `STAGES` — never assume PrePlace/Route are present. If a
   stage's PreEco netlist is absent, it is simply not processed (no error).
4. Return to `<BASE_DIR>` (GENIE_ROOT) for running scripts.

---

## PROGRESS RELAY (MANDATORY — read before running any step)
Simple mode is fast and strictly sequential; do NOT run sub-agents in the background and do NOT
yield the turn silently. Two rules:

1. **Spawn sub-agents in the FOREGROUND (blocking).** Every sub-agent here (rtl_diff_analyzer,
   studier, verifier, applier) is short and each step depends on the previous one, so call `Agent`
   **without** `run_in_background` and wait for its result inline. The background/auto-notify pattern
   in the project CLAUDE.md is for complete mode's hours-long FM/fenets phases — it does NOT apply to
   simple mode and is what makes the flow appear to "stop after spawning."
2. **Relay one line after EVERY step, and at EVERY stop.** Immediately after each checkpoint (pass or
   fail) emit a single status line to the session before doing anything else — e.g.
   `"Step 1 done — 6 changes (1 new_logic_dff, 2 new_port, ...); rtl_diff.json + rpt written. Next: Step 3-pre."`
   If you STOP at a checkpoint, the status line MUST state **what was already completed** (which
   artifacts exist) and **why** you are stopping — never end the turn without it. The main session
   only sees these lines; a silent spawn-then-stop is a bug.

---

## STEP 1 — RTL Diff Analysis
Spawn a **foreground general-purpose sub-agent** (blocking — no `run_in_background`) with the content
of `GENIE_ROOT/config/eco_agents_simple/rtl_diff_analyzer.md` prepended. Pass
`REF_DIR TILE JIRA TAG BASE_DIR AI_ECO_FLOW_DIR`.
Output: `<AI_ECO_FLOW_DIR>/<TAG>_eco_rtl_diff.json`. **Relay a one-line status when it returns.**

**CHECKPOINT:** the file exists and has ≥1 entry in `changes[]`, AND the agent-authored human-readable
`<TAG>_eco_step1_rtl_diff.rpt` exists (the "what is this ECO" reference). **Do NOT run `eco_validate_step1.py`.**
If empty/missing → STOP, and relay: what was found (or that nothing was) + the reason.
**Relay:** `"Step 1 OK — <N> changes (<by type>). Next: Step 3-pre GAP-15."`

**New-DFF changes ARE built in simple mode (structurally, no fenets).** If the RTL diff has any
`change_type == "new_logic_dff"`, the Step-3 studier still emits it via `eco_emit_dff_entry.py` — but
with a **structural (empty) rename map** instead of the fenets one. `eco_emit_dff_entry.py`'s
`resolve_cp_per_stage` falls back to the **bare clock name** when a rename-map key is absent (clock
nets are global and survive P&R renaming), and SI/SE come from `resolve_neighbor_dff_si_se` (a
structural grep of a neighbour DFF in the host module) — neither needs fenets. Do NOT stop on
`new_logic_dff`. The only correctness gate is the verifier's per-stage check that the resolved CP
(bare clock) net actually **exists in each stage netlist**; if it does not resolve for some stage,
the verifier flags that entry `NET-ABSENT-IN-STAGE` and the Step-3 CHECKPOINT (below) stops on it.

---

## STEP 2 — SKIPPED
Simple mode does **not** run `find_equivalent_nets`. The per-stage gate-net resolution that fenets
normally provides is done by **structural cone tracing** inside Step 3 (studier + emitters +
`eco_resolve_synth_internal.py`). Do NOT run any `eco_fenets_*` script.

---

## STEP 3 — Netlist Study (structural cone tracing, no validators)
**3-pre. GAP-15 classification (MANDATORY — the studier + verifier require it).** Run the structural
and_term port classifier (same as complete mode; it is a classifier, not a validator):
```bash
python3 script/eco_scripts/eco_and_term_port_check.py \
    --rtl-diff <AI_ECO_FLOW_DIR>/<TAG>_eco_rtl_diff.json --ref-dir <REF_DIR> \
    --output <AI_ECO_FLOW_DIR>/<TAG>_eco_and_term_port_check.json
```
Verify stdout shows `ECO_SCRIPT_LAUNCHED: eco_and_term_port_check.py`. Pass
`GAP15_CHECK_PATH=<AI_ECO_FLOW_DIR>/<TAG>_eco_and_term_port_check.json` to BOTH the studier
(3a) and the verifier (3c) — they read `is_output_port`/`strategy` for each `and_term` from it and do
NOT re-derive it. (No-op JSON if there are no `and_term` changes.)

Relay: `"Step 3-pre OK — GAP-15 classified (<N> and_term / none). Next: 3a studier."`

**3a.** Spawn a **foreground** sub-agent (blocking — no `run_in_background`) with
`GENIE_ROOT/config/eco_agents_simple/eco_netlist_studier.md` prepended. Pass
`REF_DIR TILE JIRA TAG BASE_DIR AI_ECO_FLOW_DIR`, the RTL-diff path, and `GAP15_CHECK_PATH`. It builds
`<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json` by tracing cones directly in the PreEco netlist
(no fenets rename map). Wait for its result, then **relay:**
`"Step 3a OK — studier built <N> study entries/stage. Next: 3b emitters."`

**3b. Run the deterministic emitter chain — WITHOUT `--rename-map`** (they fall back to structural
netlist resolution). Run from `<BASE_DIR>`, in this order (each is fail-closed; study untouched on error):
```bash
S=<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json
R=<AI_ECO_FLOW_DIR>/<TAG>_eco_rtl_diff.json
python3 script/eco_scripts/eco_expand_chains.py       --rtl-diff $R --study $S --ref-dir <REF_DIR> --jira <JIRA> --output $S
python3 script/eco_scripts/eco_emit_eq_decode.py      --rtl-diff $R --study $S --jira <JIRA> --ref-dir <REF_DIR> --output $S
python3 script/eco_scripts/eco_emit_priority_force.py --rtl-diff $R --study $S --jira <JIRA> --ref-dir <REF_DIR> --output $S
python3 script/eco_scripts/eco_cone_rebuild.py --emit-into-study --rtl-diff $R --study $S --jira <JIRA> --ref-dir <REF_DIR> --output $S
python3 script/eco_scripts/eco_emit_uniquify.py       --rtl-diff $R --study $S --jira <JIRA> --ref-dir <REF_DIR> --output $S
python3 script/eco_scripts/eco_emit_rewire_finalize.py --study $S --ref-dir <REF_DIR> --output $S
```
Verify each prints its `ECO_SCRIPT_LAUNCHED:` line. **No `--rename-map` is passed** — the emitters
use their structural fallback (netlist D-net lookup / bus-bit flatten / driver trace).

**On any emitter abort (exit 2) → apply the Correctness-posture Rule 4 (script-bug self-fix), do NOT
reflexively hard-stop.** Each emitter is fail-closed (study untouched) when a cone leaf cannot be
grounded. First decide *why* (Rule 4):
- **Script-bug fail-close** — you can confirm in the raw netlist / RTL diff that the leaf IS
  resolvable but the emitter aborted on its own limitation (over-strict match, missing scalar/null
  case, field-name mismatch — the class this flow keeps hitting). Then **self-fix per Rule 4**: copy
  the emitter to `/tmp/<name>_<TAG>.py`, make the minimal evidence-backed fix, re-run the `/tmp` copy,
  continue the chain, and relay
  `"Step 3b SCRIPT-SELF-FIX: <name> — <bug> → <fix>; emitter re-run OK, continuing."` (also add the
  `SCRIPT-SELF-FIX:` line to the step-3 RPT for upstreaming).
- **Data fail-close** — the leaf is genuinely unresolvable without fenets (Rules 1–3). Then STOP (do
  not run the remaining emitters, verifier, or apply) and **relay what completed + why**:
  `"Step 3b STOPPED at emitter <name> (exit 2) — <cone leaf> genuinely unresolvable without fenets.
  Completed: Step 1, 3-pre, 3a, emitters <list>. Study untouched. Re-run <TAG> in complete mode."`

If all emitters pass, **relay:** `"Step 3b OK — emitter chain clean (<list>). Next: 3c verifier."`

**3c. Spawn the SIMPLE netlist verifier (structural enrichment — makes the study robust).** Spawn a
**foreground** sub-agent (blocking — no `run_in_background`) with
`GENIE_ROOT/config/eco_agents_simple/eco_netlist_verifier.md` prepended
(pass `REF_DIR TILE JIRA TAG BASE_DIR AI_ECO_FLOW_DIR` + `GAP15_CHECK_PATH`). It runs the complete verifier's enrichment checks
**structurally** (no fenets): per-stage net resolution (`eco_cone_trace.py resolve` /
`eco_resolve_synth_internal.py`, dropping the fenets priorities), a mandatory **per-stage polarity**
check for every bound input (`eco_cone_trace.py polarity` vs the source register Q), cone
verification, and the port-boundary / consumer-cascade / UNCONNECTED / PENDING auto-adds. It writes
the enriched study back + `<TAG>_eco_step3_netlist_verify.rpt`. Wait for its result, then **relay:**
`"Step 3c OK — verifier enriched study, <N> auto-adds, flags: <none|list>. Next: Step 4 apply."`

**CHECKPOINT:** `<TAG>_eco_preeco_study.json` has entries for ≥1 stage, and BOTH the agent-authored
`<TAG>_eco_step3_netlist_study.rpt` (what the gate-level ECO does) and `<TAG>_eco_step3_netlist_verify.rpt` exist. If
the verifier flagged any `NET-ABSENT-IN-STAGE`, `UNRESOLVABLE`, or `polarity_undetermined` entry →
**STOP and relay** what completed + the exact flagged entries:
`"Step 3 STOPPED — verifier flagged <entry>: <NET-ABSENT/polarity_undetermined>. Completed Steps 1,
3-pre, 3a, 3b, 3c; study + both step3 RPTs written under <AI_ECO_FLOW_DIR>. Punt the flagged change
to complete mode."` (simple mode must not apply an unresolved/ambiguous study.) **Do NOT run
`eco_validate_step3.py` or `eco_functional_precheck.py`** — those hard-gate validators stay off; the
simple verifier is the robustness layer.

---

## STEP 4 — Apply to the present stages (no validators)
Spawn a **foreground** sub-agent (blocking — no `run_in_background`) with
`GENIE_ROOT/config/eco_agents_simple/eco_applier.md` prepended.
Pass `REF_DIR TILE JIRA TAG BASE_DIR AI_ECO_FLOW_DIR` and the study path
`<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json` (the applier's `eco_perl_spec.py` needs
`--tag <TAG> --jira <JIRA> --stage <Stage>` for `eco_*` net naming — do NOT omit JIRA).
It applies the study into `<REF_DIR>/data/PostEco/<Stage>.v.gz` for **each stage in `STAGES`** via
`eco_perl_spec.py` (gates) + `eco_netlist_port_rewire.py` (ports/rewires), one pass per present stage
(Synthesize always; PrePlace/Route only if provided).
**Do NOT run `eco_validate_step4.py`, `eco_pre_fm_check.py`, or any FM script.**

**CHECKPOINT:** the agent-authored `<TAG>_eco_step4_eco_applied.rpt` exists, AND each **present** PostEco
stage netlist md5 changed vs its PreEco baseline (proves gates landed),
OR the stage legitimately had no entries. **Relay:**
`"Step 4 OK — applied <A> / inserted <I> across <stages>; PostEco patched (md5 changed). Next: EXIT."`

---

## EXIT
1. Write `<AI_ECO_FLOW_DIR>/<TAG>_simple_phase_exited.marker` (one line: `exited <ISO_TIMESTAMP>`).
2. One-line summary: `"SIMPLE mode complete — Steps 1,3,4 done. Human-readable RPTs
   (step1_rtl_diff / step3_netlist_study / step4_eco_applied) + JSONs under <AI_ECO_FLOW_DIR>;
   PostEco netlists patched. No FM/validators run (simple mode)."`
3. STOP. Do not spawn ROUND/FINAL, do not emit any phase-ready signal, do not send email.
