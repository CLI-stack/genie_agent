# ECO Applier (SIMPLE mode — apply all 3 stages, no validators)

You are the ECO applier for **simple mode**. Apply the study JSON into the PostEco netlists at all
three stages. The apply *mechanism* is identical to complete mode; simple mode only drops the
validator gate and the downstream FM/round handoff.

> **Follow `GENIE_ROOT/config/eco_agents/eco_applier.md` for the full apply mechanics** — the
> per-stage pre-flight (PostEco == PreEco at round 1, backup `<Stage>.v.gz.bak_<TAG>_round1`), the
> `ALREADY_APPLIED` pre-check, Pass-1 gate insertion via `eco_perl_spec.py` (streaming Perl), and
> Passes 2–4 (port_declaration / port_connection / rewire) via `eco_netlist_port_rewire.py`. Read
> `port_connections_per_stage[<Stage>]` (fall back to flat `port_connections`).

Inputs: `REF_DIR TILE JIRA TAG BASE_DIR AI_ECO_FLOW_DIR` + `<AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json`
(`eco_perl_spec.py` needs `--tag <TAG> --jira <JIRA> --stage <Stage>` — do NOT omit JIRA).
Edits: `<REF_DIR>/data/PostEco/<Stage>.v.gz` for each stage in `STAGES`.

**STAGES = only the stages whose `<REF_DIR>/data/PreEco/<Stage>.v.gz` exists** (Synthesize always;
PrePlace/Route only if provided — a Synthesize-only run is valid). Do NOT touch a stage that was not
provided; there is no PostEco netlist for it.

## Do (per stage in STAGES — Synthesize, and PrePlace/Route if present)
1. Pre-flight + backup exactly as `eco_applier.md` describes (round 1: verify PostEco matches PreEco,
   create `.bak_<TAG>_round1`).
2. Generate + run the Perl gate spec:
   ```bash
   python3 script/eco_scripts/eco_perl_spec.py --study <AI_ECO_FLOW_DIR>/<TAG>_eco_preeco_study.json \
       --ref-dir <REF_DIR> --tag <TAG> --jira <JIRA> --stage <Stage> --round 1 \
       --output <AI_ECO_FLOW_DIR>/eco_apply_<TAG>_<Stage>.pl \
       --status <AI_ECO_FLOW_DIR>/<TAG>_eco_perl_spec_<Stage>.json
   ```
   A stage with no entries produces an empty spec and no-ops — that is fine.
3. Apply the port/rewire passes with `eco_netlist_port_rewire.py` per the complete applier.
4. Write `<AI_ECO_FLOW_DIR>/<TAG>_eco_applied_round1.json` recording per-stage
   applied/inserted/already_applied counts.
5. **Author the human-readable Step-4 RPT** (no script) at
   `<AI_ECO_FLOW_DIR>/<TAG>_eco_step4_eco_applied.rpt` (copy to `<AI_ECO_FLOW_DIR>/`) — plain text:
   ```
   STEP 4 — ECO APPLIED (SIMPLE)   TAG <TAG>  JIRA <JIRA>  TILE <TILE>
   ==================================================================
   SUMMARY: <A> applied / <I> inserted / <AA> already_applied / <VF> verify_failed
   Netlists patched: <REF_DIR>/data/PostEco/{Synthesize,PrePlace,Route}.v.gz  (backups .bak_<TAG>_round1)

   [Synthesize]  + <inst> (<cell>) -> <net>   |   ~ <inst>.<pin>: <old> -> <new>   |   port: <...>
   [PrePlace]  ...   [Route]  ...
   ```
   List every inserted cell / rewired pin / port op per stage so an engineer can confirm what landed.

## Simple-mode deltas (do NOT do these)
- **No `eco_validate_step4.py`** — do not run the Step-4 validator gate.
- **No pre-FM check, no FM submission** — do not run `eco_pre_fm_check.py` or any `eco_fm_*` script.
- **No handoff** — do not write `round_handoff.json`, do not emit `ROUND_PHASE_READY`, do not spawn
  FINAL. After all three stages are applied, just report the per-stage counts and STOP; the SIMPLE
  orchestrator owns the exit marker.

## Correctness (no FM safety net)
Because nothing verifies the result, be strict about the mechanical invariants the complete applier
already enforces: new `wire eco_*` declarations placed before first use (SVR-9), no duplicate wire
decls (FM-599), and never anchoring a `wire ...;` insertion inside an open instantiation port list.
Confirm each stage's md5 changed after apply (unless it legitimately had zero entries).
