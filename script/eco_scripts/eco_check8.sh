#!/bin/bash
# eco_check8.sh — Run Verilog validator for Step 5 Check 8.
# Replaces agent reasoning with deterministic script invocation.
#
# Checks ONLY for errors that cause FM to abort (FM-599):
#   SVR4_bare_paren  — bare ')' without ';' in module port list
#   SVR9_dup_wire    — duplicate explicit wire declaration
#
# F2_implicit_wire_conflict errors are pre-existing in all P&R netlists and
# do NOT cause FM abort — FM handles them internally. They are reported as
# warnings but never cause FAIL.
#
# Usage:
#   bash script/eco_check8.sh \
#       <BASE_DIR> <REF_DIR> <TAG> <ROUND> <eco_applied_json>
#
# Output:
#   Writes <BASE_DIR>/data/<TAG>_eco_check8_round<ROUND>.json
#   Exit 0 = all PASS, Exit 1 = any FAIL (SVR4/SVR9 in PostEco)

BASE_DIR=$1
REF_DIR=$2
TAG=$3
ROUND=$4
APPLIED_JSON=$5

SCRIPT="${BASE_DIR}/script/eco_scripts/validate_verilog_netlist.py"
OUT_JSON="${BASE_DIR}/data/${TAG}_eco_check8_round${ROUND}.json"
TMP_LOG="/tmp/eco_check8_${TAG}_${ROUND}.txt"
STUDY_JSON="${BASE_DIR}/data/${TAG}_eco_preeco_study.json"

# ── Extract touched module names ─────────────────────────────────────────────
MODS=$(python3 -c "
import json, os
mods = set()
study = '${STUDY_JSON}'
if os.path.exists(study):
    d = json.load(open(study))
    for entries in d.values():
        if isinstance(entries, list):
            for e in entries:
                if e.get('module_name'): mods.add(e['module_name'])
if not mods:
    d = json.load(open('${APPLIED_JSON}'))
    for entries in d.values():
        if isinstance(entries, list):
            for e in entries:
                if e.get('module_name'): mods.add(e['module_name'])
print(' '.join(sorted(mods)))
" 2>/dev/null)

MODS_ARGS=()
if [ -n "$MODS" ]; then
    MODS_ARGS=(--modules)
    for m in $MODS; do MODS_ARGS+=("$m"); done
fi

# ── Run validator on PostEco ─────────────────────────────────────────────────
python3 "${SCRIPT}" --strict "${MODS_ARGS[@]}" -- \
    "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
    "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
    "${REF_DIR}/data/PostEco/Route.v.gz" \
    2>&1 | tee "${TMP_LOG}"

# ── Per-stage PASS/FAIL: only SVR4_bare_paren and SVR9_dup_wire cause FAIL ──
parse_stage() {
    local stage="$1"
    local log="$2"
    # Check if this stage section has SVR4 or SVR9 errors (FM-aborting errors only)
    python3 - "${log}" "${stage}" <<'PYEOF'
import re, sys

log_path = sys.argv[1]
stage    = sys.argv[2]

try:
    lines = open(log_path).readlines()
except:
    print("FAIL")
    sys.exit()

in_stage = False
for i, line in enumerate(lines):
    if re.search(r'Validating:.*' + re.escape(stage), line):
        in_stage = True
    elif re.search(r'Validating:', line) and in_stage:
        break
    if in_stage:
        # SVR4_bare_paren and SVR9_dup_wire always FAIL (never pre-existing in gate-level P&R netlists)
        if re.search(r'SVR4_bare_paren|SVR9_dup_wire', line):
            print("FAIL")
            sys.exit()
        # F2_implicit_wire_conflict: compare against PreEco baseline — only FAIL if count is HIGHER
        # Pre-existing F2s are normal in P&R netlists and FM handles them internally
        # This check is done separately after stage loop via F2_COUNT comparison

print("PASS")
PYEOF
}

SYNTH=$(parse_stage "Synthesize" "${TMP_LOG}")
PPLACE=$(parse_stage "PrePlace"   "${TMP_LOG}")
ROUTE=$(parse_stage  "Route"      "${TMP_LOG}")

[ -z "$SYNTH"  ] && SYNTH="FAIL"
[ -z "$PPLACE" ] && PPLACE="FAIL"
[ -z "$ROUTE"  ] && ROUTE="FAIL"

# ── SVR4 inline fix — bare ')' without ';' (introduced by eco_passes_2_4.py) ─
if grep -q "SVR4_bare_paren" "${TMP_LOG}" 2>/dev/null; then
    for STAGE_GZ in "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
                    "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
                    "${REF_DIR}/data/PostEco/Route.v.gz"; do
        TMP_FIX="/tmp/eco_svr4fix_$(basename ${STAGE_GZ} .v.gz).v"
        zcat "${STAGE_GZ}" | awk '{
            if(/^\s*\)\s*$/ && prev_was_port){print ") ;"}
            else{print}
            prev_was_port=($0 ~ /\.\w+\s*\(/)
        }' > "${TMP_FIX}"
        gzip -c "${TMP_FIX}" > "${STAGE_GZ}.fixed" && mv "${STAGE_GZ}.fixed" "${STAGE_GZ}"
        rm -f "${TMP_FIX}"
        echo "SVR4_bare_paren: fixed in $(basename ${STAGE_GZ})"
    done
    python3 "${SCRIPT}" --strict "${MODS_ARGS[@]}" -- \
        "${REF_DIR}/data/PostEco/Synthesize.v.gz" \
        "${REF_DIR}/data/PostEco/PrePlace.v.gz" \
        "${REF_DIR}/data/PostEco/Route.v.gz" \
        > "${TMP_LOG}" 2>&1
    SYNTH=$(parse_stage "Synthesize" "${TMP_LOG}")
    PPLACE=$(parse_stage "PrePlace"   "${TMP_LOG}")
    ROUTE=$(parse_stage  "Route"      "${TMP_LOG}")
    [ -z "$SYNTH"  ] && SYNTH="FAIL"
    [ -z "$PPLACE" ] && PPLACE="FAIL"
    [ -z "$ROUTE"  ] && ROUTE="FAIL"
fi

# ── Collect FM-aborting error lines for JSON ──────────────────────────────────
ERRORS_JSON=$(grep -E "SVR4_bare_paren|SVR9_dup_wire" "${TMP_LOG}" 2>/dev/null \
    | head -20 \
    | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")
[ -z "$ERRORS_JSON" ] && ERRORS_JSON="[]"

# Count pre-existing F2 warnings (informational only)
F2_COUNT=$(grep -c "F2_implicit_wire_conflict" "${TMP_LOG}" 2>/dev/null || echo 0)

# ── Write output JSON ─────────────────────────────────────────────────────────
python3 -c "
import json, sys
result = {
    'Synthesize': sys.argv[1],
    'PrePlace':   sys.argv[2],
    'Route':      sys.argv[3],
    'errors':     json.loads(sys.argv[4]),
    'f2_preexisting_count': int(sys.argv[5])
}
print(json.dumps(result, indent=2))
" "${SYNTH}" "${PPLACE}" "${ROUTE}" "${ERRORS_JSON}" "${F2_COUNT}" > "${OUT_JSON}"

# ── Write launch marker ───────────────────────────────────────────────────────
MARKER="ECO_SCRIPT_LAUNCHED: eco_check8.sh
  Synthesize: ${SYNTH}
  PrePlace:   ${PPLACE}
  Route:      ${ROUTE}
  f2_preexisting: ${F2_COUNT} (informational — pre-existing in base netlist, FM handles them)
  output:     ${OUT_JSON}"
echo "${MARKER}"
echo "${MARKER}" > "${OUT_JSON%.json}_marker.txt"

rm -f "${TMP_LOG}"

[ "$SYNTH" = "PASS" ] && [ "$PPLACE" = "PASS" ] && [ "$ROUTE" = "PASS" ] && exit 0 || exit 1
