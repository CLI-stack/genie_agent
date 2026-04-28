#!/bin/bash
# eco_check8.sh — Run Verilog validator for Step 5 Check 8.
# Replaces agent reasoning with deterministic script invocation.
# Outputs per-stage PASS/FAIL JSON for eco_pre_fm_checker to include in its JSON.
#
# Usage:
#   bash script/eco_check8.sh \
#       <BASE_DIR> <REF_DIR> <TAG> <ROUND> <eco_applied_json>
#
# Output:
#   Writes <BASE_DIR>/data/<TAG>_eco_check8_round<ROUND>.json:
#   {
#     "Synthesize": "PASS|FAIL",
#     "PrePlace":   "PASS|FAIL",
#     "Route":      "PASS|FAIL",
#     "errors":     ["<error line>", ...]
#   }
#   Exit 0 = all PASS, Exit 1 = any FAIL

BASE_DIR=$1
REF_DIR=$2
TAG=$3
ROUND=$4
APPLIED_JSON=$5

SCRIPT="${BASE_DIR}/script/validate_verilog_netlist.py"
OUT_JSON="${BASE_DIR}/data/${TAG}_eco_check8_round${ROUND}.json"
TMP_LOG="/tmp/eco_check8_${TAG}_${ROUND}.txt"

# Extract touched module names from applied JSON
MODS=$(python3 -c "
import json, sys
d = json.load(open('${APPLIED_JSON}'))
mods = set()
for v in d.values():
    if isinstance(v, list):
        for e in v:
            if e.get('module_name'): mods.add(e['module_name'])
print(' '.join(sorted(mods)))
" 2>/dev/null)

# Build modules array safely
MODS_ARGS=()
if [ -n "$MODS" ]; then
    MODS_ARGS=(--modules)
    for m in $MODS; do
        MODS_ARGS+=("$m")
    done
fi

# Run validator — use -- to separate --modules args from netlist positional args
python3 "${SCRIPT}" \
    --strict \
    "${MODS_ARGS[@]}" \
    -- \
    "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
    "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
    "${REF_DIR}/data/PostEco/Route.v.gz" \
    2>&1 | tee "${TMP_LOG}"
VALIDATOR_EXIT=${PIPESTATUS[0]}

# Parse per-stage results
parse_stage() {
    local stage=$1
    local log=$2
    # Find the section for this stage and check PASS/FAIL
    awk "/Validating:.*${stage}/{found=1} found && /PASS:/{print \"PASS\"; exit} found && /FAIL:/{print \"FAIL\"; exit} found && /Validating:.*[^${stage}]/{exit}" "${log}"
}

SYNTH=$(parse_stage "Synthesize" "${TMP_LOG}")
PPLACE=$(parse_stage "PrePlace" "${TMP_LOG}")
ROUTE=$(parse_stage "Route" "${TMP_LOG}")

# Default to FAIL if parsing failed
[ -z "$SYNTH"  ] && SYNTH="FAIL"
[ -z "$PPLACE" ] && PPLACE="FAIL"
[ -z "$ROUTE"  ] && ROUTE="FAIL"

# Inline fix for SVR4_bare_paren: replace bare ')' with ') ;' in all stages
if grep -q "SVR4_bare_paren" "${TMP_LOG}" 2>/dev/null; then
    for STAGE_GZ in "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
                    "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
                    "${REF_DIR}/data/PostEco/Route.v.gz"; do
        TMP_FIX="/tmp/eco_svr4fix_$(basename ${STAGE_GZ} .v.gz).v"
        # Replace lines that are exactly ')' (bare paren) with ') ;'
        zcat "${STAGE_GZ}" | awk '{if(/^\s*\)\s*$/ && prev_was_port){print ") ;"} else{print} prev_was_port=($0 ~ /\.\w+\s*\(/)}' > "${TMP_FIX}"
        gzip -c "${TMP_FIX}" > "${STAGE_GZ}.fixed" && mv "${STAGE_GZ}.fixed" "${STAGE_GZ}"
        rm -f "${TMP_FIX}"
        echo "SVR4_bare_paren: fixed in $(basename ${STAGE_GZ})"
    done
    # Re-run validator after fix
    python3 "${SCRIPT}" --strict ${MODS_ARGS[@]} -- \
        "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
        "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
        "${REF_DIR}/data/PostEco/Route.v.gz" \
        2>&1 > "${TMP_LOG}"
    SYNTH=$(parse_stage "Synthesize" "${TMP_LOG}")
    PPLACE=$(parse_stage "PrePlace" "${TMP_LOG}")
    ROUTE=$(parse_stage "Route" "${TMP_LOG}")
    [ -z "$SYNTH"  ] && SYNTH="FAIL"
    [ -z "$PPLACE" ] && PPLACE="FAIL"
    [ -z "$ROUTE"  ] && ROUTE="FAIL"
fi

# Collect error lines
ERRORS=$(grep -E "^\s+\[F[0-9]|\[SVR|Error:|SVR-[0-9]" "${TMP_LOG}" 2>/dev/null | head -20 | python3 -c "
import sys, json
lines = [l.rstrip() for l in sys.stdin]
print(json.dumps(lines))
")
[ -z "$ERRORS" ] && ERRORS="[]"

# Write output JSON
python3 -c "
import json
result = {
    'Synthesize': '${SYNTH}',
    'PrePlace':   '${PPLACE}',
    'Route':      '${ROUTE}',
    'errors':     ${ERRORS}
}
print(json.dumps(result, indent=2))
" > "${OUT_JSON}"

# Write launch marker — agent includes this in Step 5 RPT to prove script ran
MARKER="ECO_SCRIPT_LAUNCHED: eco_check8.sh
  Synthesize: ${SYNTH}
  PrePlace:   ${PPLACE}
  Route:      ${ROUTE}
  output:     ${OUT_JSON}"
echo "${MARKER}"
echo "${MARKER}" > "${OUT_JSON%.json}_marker.txt"

# Exit 0 if all PASS, 1 if any FAIL
if [ "$SYNTH" = "PASS" ] && [ "$PPLACE" = "PASS" ] && [ "$ROUTE" = "PASS" ]; then
    exit 0
else
    exit 1
fi
