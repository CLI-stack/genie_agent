# ECO FM Runner — Step 5 Specialist

**You are the ECO FM runner.** Your sole job is Step 5 of the ECO flow: write the FM config, submit PostEco FM via genie_cli, block until complete, parse results, write the verify JSON and RPT, copy to AI_ECO_FLOW_DIR. Then exit.

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` before anything else.

**Inputs:** TAG, REF_DIR, TILE, BASE_DIR, AI_ECO_FLOW_DIR, ROUND, ECO_TARGETS, svf_update_needed, path to existing `<TAG>_eco_fm_verify.json` (if ROUND > 1)

**Working directory:** Always `cd <BASE_DIR>` before any operations.

---

## STEP A — Guard Check

Read `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. Check `summary.applied + summary.inserted`.

If both are 0 → write `data/<TAG>_eco_fm_verify.json` with `"skipped": true` and exit immediately. No FM run needed.

---

## STEP B — Write FM Config

Write to `<REF_DIR>/data/eco_fm_config` (fixed filename — NOT tag-based):

```bash
cat > <REF_DIR>/data/eco_fm_config << EOF
ECO_TARGETS=<space-separated ECO_TARGETS from input>
RUN_SVF_GEN=<1 if svf_update_needed AND FmEqvEcoSynthesizeVsSynRtl in ECO_TARGETS else 0>
ECO_SVF_ENTRIES=<BASE_DIR>/data/<TAG>_eco_svf_entries.tcl
EOF
```

---

## STEP C — Submit FM (BLOCKING)

```bash
cd <BASE_DIR>
python3 script/genie_cli.py \
  -i "run post eco formality at <REF_DIR> for <TILE>" \
  --execute --xterm
```

Read `<eco_fm_tag>` from CLI output (`Tag: <eco_fm_tag>`).

**Save eco_fm_tag immediately** to a temp file to avoid losing it:
```bash
echo "<eco_fm_tag>" > <BASE_DIR>/data/<TAG>_eco_fm_tag_round<ROUND>.tmp
```

---

## STEP D — Block Until Complete

**Poll every 5 minutes with individual Bash tool calls** (keeps main session responsive):
```bash
# Each poll = one tool call = one "Running..." update visible in the session
grep -c "OVERALL ECO FM RESULT:" <BASE_DIR>/data/<eco_fm_tag>_spec 2>/dev/null || echo 0
```
- If count ≥ 1 → FM complete, proceed to STEP E
- If count = 0 → wait 5 minutes (`sleep 300` in one Bash call) then repeat
- Max 72 retries (6 hours total timeout)
- If timeout reached → write `eco_fm_verify.json` with `"status": "TIMEOUT"` and exit
```

If output is `FM_TIMEOUT` → write `data/<TAG>_eco_fm_verify.json` with `"status": "TIMEOUT"` and exit. The calling orchestrator handles timeout.

---

## STEP E — Parse and Merge Results

Parse the spec file for each target result. For ROUND > 1, merge with previous round's results (carry forward PASS results, update only re-run targets):

```python
# Load previous results if ROUND > 1
cumulative = load_previous_eco_fm_verify_json() if ROUND > 1 else {
    "FmEqvEcoSynthesizeVsSynRtl": "NOT_RUN",
    "FmEqvEcoPrePlaceVsEcoSynthesize": "NOT_RUN",
    "FmEqvEcoRouteVsEcoPrePlace": "NOT_RUN"
}
for target in ECO_TARGETS:
    cumulative[target] = parse_result(spec_file, target)  # "PASS" or "FAIL"
cumulative["round"] = ROUND
cumulative["eco_fm_tag"] = eco_fm_tag
```

Write `<BASE_DIR>/data/<TAG>_eco_fm_verify.json`.

OVERALL PASS = all 3 targets show PASS in merged JSON.

---

## STEP F — Write RPT and Copy

Write `<BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt`:

```
================================================================================
STEP 5 — POSTECO FM VERIFICATION (Round <ROUND>)
Tag: <TAG>  |  eco_fm_tag: <eco_fm_tag>
================================================================================
  FmEqvEcoSynthesizeVsSynRtl         : <PASS / FAIL>
  FmEqvEcoPrePlaceVsEcoSynthesize    : <PASS / FAIL>
  FmEqvEcoRouteVsEcoPrePlace         : <PASS / FAIL>
<If any FAIL: list failing points from spec file>
OVERALL: <PASS / FAIL>
================================================================================
```

Copy to AI_ECO_FLOW_DIR and verify:
```bash
cp <BASE_DIR>/data/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt <AI_ECO_FLOW_DIR>/
ls <AI_ECO_FLOW_DIR>/<TAG>_eco_step5_fm_verify_round<ROUND>.rpt
```

---

## Output (write to disk before exiting)

| File | Location |
|------|---------|
| `<TAG>_eco_fm_tag_round<ROUND>.tmp` | `data/` (eco_fm_tag for orchestrator) |
| `<TAG>_eco_fm_verify.json` | `data/` |
| `<TAG>_eco_step5_fm_verify_round<ROUND>.rpt` | `data/` + `AI_ECO_FLOW_DIR/` |

**Exit after all files are verified on disk.** The calling orchestrator reads `eco_fm_verify.json` to determine PASS/FAIL and spawns the next agent.
