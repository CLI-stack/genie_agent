# ECO Context Skill

Instantly resolves all standard ECO flow paths for a given TAG — FM logs, status RPTs, PostEco netlists, AI_ECO_FLOW artifacts, and cleanup targets.

## Trigger
`/eco-context`

## Usage
```
/eco-context <TAG>
/eco-context <TAG> fm           — show FM log and status paths only
/eco-context <TAG> cleanup      — show what to clean for a fresh restart
/eco-context <TAG> cleanup <N>  — show cleanup targets for round N
```

## What this skill does

When invoked with a TAG, read `<BASE_DIR>/data/<TAG>_round_handoff.json` to extract REF_DIR, then resolve and display ALL standard paths. Do NOT ask the user for paths — derive everything from the handoff JSON.

### Step 1 — Load context from handoff JSON
```bash
BASE_DIR=/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent/users/$USER
HANDOFF=$BASE_DIR/data/<TAG>_round_handoff.json
```
Read fields: `ref_dir`, `ai_eco_flow_dir`, `round`, `eco_fm_tag`, `tile`, `jira`

If handoff JSON missing, try `<TAG>_phase_a_handoff.json` as fallback.

### Step 2 — Resolve all paths

**FM status and logs** (under `REF_DIR/rpts/`):
```
REF_DIR/rpts/FmEqvEcoSynthesizeVsSynRtl/
  ├── FmEqvEcoSynthesizeVsSynRtl__failing_points.rpt.gz   ← failing DFF list
  ├── FmEqvEcoSynthesizeVsSynRtl__analyze_points.rpt.gz   ← cone analysis (why fail)
  ├── FmEqvEcoSynthesizeVsSynRtl__dpx_status.rpt.gz       ← PASS/FAIL verdict
  └── FmEqvEcoSynthesizeVsSynRtl.log.gz                   ← full FM log

REF_DIR/rpts/FmEqvEcoPrePlaceVsEcoSynthesize/   ← same structure
REF_DIR/rpts/FmEqvEcoRouteVsEcoPrePlace/         ← same structure
```

**AI ECO FLOW artifacts** (under `AI_ECO_FLOW_DIR/`):
```
AI_ECO_FLOW_DIR/
  ├── <TAG>_eco_step6_fm_verify_round<N>.rpt    ← round FM verdict + counts
  ├── <TAG>_eco_step6_evidence_walk_round<N>.rpt
  ├── <TAG>_eco_step4_eco_applied_round<N>.rpt
  └── <TAG>_eco_step3_netlist_study_round<N>.rpt
```

**BASE_DIR artifacts** (under `BASE_DIR/data/`):
```
BASE_DIR/data/
  ├── <TAG>_eco_rtl_diff.json
  ├── <TAG>_eco_preeco_study.json
  ├── <TAG>_eco_fm_verify.json            ← latest FM results (overwritten each round)
  ├── <TAG>_eco_fm_evidence_round<N>.json ← per-round evidence
  ├── <TAG>_eco_report_round<N>.html      ← round email HTML
  ├── <TAG>_round_handoff.json
  ├── <TAG>_study_phase_exited.marker
  ├── <TAG>_apply_phase_exited.marker
  └── <TAG>_round<N>_phase_exited.marker
```

**PostEco netlists** (patched by eco_applier):
```
REF_DIR/data/PostEco/
  ├── Synthesize.v.gz    ← bak_<TAG>_round<N> backups exist alongside
  ├── PrePlace.v.gz
  └── Route.v.gz
```

### Step 3 — For `/eco-context <TAG> fm`

Show FM status for all 3 targets:
```bash
for tgt in FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace; do
    zcat $REF_DIR/rpts/$tgt/${tgt}__dpx_status.rpt.gz 2>/dev/null | head -5 || echo "$tgt: no dpx_status yet"
    ls -lt $REF_DIR/rpts/$tgt/*.rpt.gz 2>/dev/null | grep "fail\|status" | head -3
done
```
Also read `BASE_DIR/data/<TAG>_eco_step6_fm_verify_round<N>.rpt` for the flow's consolidated verdict.

### Step 4 — For `/eco-context <TAG> cleanup [N]`

Cleanup targets for a fresh restart (ask user to confirm before executing):

**Round-level cleanup** (removes round N artifacts, reverts netlists to round N-1 state):
```bash
# BASE_DIR data artifacts for round N:
rm -f BASE_DIR/data/<TAG>_eco_applied_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_fm_evidence_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_fm_xstage_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_fm_analysis_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_fm_analysis_round<N>.contract_check.json
rm -f BASE_DIR/data/<TAG>_eco_report_round<N>.html
rm -f BASE_DIR/data/<TAG>_round<N>_phase_exited.marker
rm -f BASE_DIR/data/<TAG>_eco_step3_netlist_study_round<N>.rpt
rm -f BASE_DIR/data/<TAG>_eco_step4_eco_applied_round<N>.rpt
rm -f BASE_DIR/data/<TAG>_eco_validate_step3_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_validate_step3_round<N>_marker.txt
rm -f BASE_DIR/data/<TAG>_eco_check8_round<N>.json
rm -f BASE_DIR/data/<TAG>_eco_check8_round<N>_marker.txt
rm -f BASE_DIR/data/<TAG>_eco_fm_tag_round<N>.tmp
rm -f BASE_DIR/data/<TAG>_eco_pre_fm_check_round<N>.json

# AI_ECO_FLOW_DIR artifacts for round N:
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step3_netlist_study_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step4_eco_applied_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step6_fm_verify_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step6_evidence_walk_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step6_xstage_compare_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step6_fm_analysis_round<N>.rpt
rm -f AI_ECO_FLOW_DIR/<TAG>_eco_step5_pre_fm_check_round<N>.rpt

# Revert PostEco netlists to bak_<TAG>_round<N-1> (if backup exists):
for stage in Synthesize PrePlace Route; do
    bak=REF_DIR/data/PostEco/${stage}.v.gz.bak_<TAG>_round<N-1>
    if [ -f $bak ]; then cp $bak REF_DIR/data/PostEco/${stage}.v.gz; fi
done
```

**Full TAG cleanup** (removes ALL artifacts for this TAG, reverts netlists to pre-ECO state):
```bash
rm -f BASE_DIR/data/<TAG>_*
rm -f BASE_DIR/runs/<TAG>.*
# Revert PostEco netlists to bak_<TAG>_round1 (earliest backup):
for stage in Synthesize PrePlace Route; do
    bak=REF_DIR/data/PostEco/${stage}.v.gz.bak_<TAG>_round1
    if [ -f $bak ]; then cp $bak REF_DIR/data/PostEco/${stage}.v.gz; fi
done
# Remove AI_ECO_FLOW_DIR entirely (if only this TAG used it):
# rm -rf AI_ECO_FLOW_DIR   ← CONFIRM with user first
```

### Step 5 — Display summary

Always end with a compact table:
```
TAG:           <TAG>
JIRA:          <jira>
TILE:          <tile>
REF_DIR:       <ref_dir>
AI_ECO_FLOW:   <ai_eco_flow_dir>
BASE_DIR:      <base_dir>
Current round: <round>
FM tag:        <eco_fm_tag>

FM STATUS:
  Synth:  <PASS|FAIL|RUNNING|NOT_STARTED>  [N failing pts]
  PP:     <PASS|FAIL|RUNNING|NOT_STARTED>  [N failing pts]
  Route:  <PASS|FAIL|RUNNING|NOT_STARTED>  [N failing pts]
```

## Important rules

- **NEVER ask the user for REF_DIR, BASE_DIR, or AI_ECO_FLOW_DIR** — always derive from handoff JSON
- **NEVER run cleanup without user confirmation** — show the commands, ask first
- **FM is RUNNING** if `__dpx_status.rpt.gz` does not exist but `__pin_map.rpt` or `__matched_points.rpt.gz` exists (job launched but not finished)
- **FM is NOT_STARTED** if the rpts directory is empty or only has pre-run files
- Always show the latest round's FM results, not stale earlier round data
