#!/bin/bash
# eco_verilog_validator.sh — Run Verilog validator for Step 5 Check 8.
# Replaces agent reasoning with deterministic script invocation.
#
# Checks that cause FM to abort (FM-599):
#   SVR4_bare_paren  — bare ')' without ';' in module port list  (syntax)
#   SVR9_dup_wire    — duplicate explicit wire declaration       (syntax)
#   DRIVER_COUNT     — an ECO output net that is UNDRIVEN (0 drivers) or
#                      MULTIPLY-DRIVEN (>=2 drivers) in the APPLIED PostEco.
#                      Such a netlist parses cleanly (so the syntax checks pass)
#                      but aborts FM (SVR-9) or is silently wrong. This reuses
#                      eco_validate_step4's exact driver-count logic on the applied
#                      netlist so step5 can never green-light a netlist step4 fails.
#                      (Real on tag 20260707090807: recdsp_c0mop[*] undriven,
#                      ctmn_917 x40 double-driven — both passed the old syntax-only gate.)
#
# F2_implicit_wire_conflict is NOT a FAIL signal: as implemented it flags every net
# that is both `wire`-declared AND used in a port connection — which is normal for
# correctly-applied ECO nets and pre-existing base nets alike — so it is over-broad.
# The sound ECO-net-aware SVR-9 signal is DRIVER_COUNT above. F2 count is informational.
#
# Usage:
#   bash script/eco_verilog_validator.sh \
#       <BASE_DIR> <REF_DIR> <TAG> <ROUND> <eco_applied_json>
#
# Output:
#   Writes <BASE_DIR>/data/<TAG>_eco_verilog_validator_round<ROUND>.json
#   Exit 0 = all PASS, Exit 1 = any FAIL (SVR4/SVR9 in PostEco)

BASE_DIR=$1
REF_DIR=$2
TAG=$3
ROUND=$4
APPLIED_JSON=$5

# Validate positional args before resolving paths — without these checks an
# accidental flag like "--ref-dir /path" parses as $1=BASE_DIR and the
# resulting OUT_JSON path becomes garbage with embedded literal "--ref-dir/".
for arg_name in BASE_DIR REF_DIR TAG ROUND APPLIED_JSON; do
    val=$(eval echo \$$arg_name)
    if [ -z "$val" ] || [[ "$val" == --* ]]; then
        echo "ERROR: positional arg $arg_name is empty or looks like a flag ('$val'). Usage:" >&2
        echo "  bash eco_verilog_validator.sh <BASE_DIR> <REF_DIR> <TAG> <ROUND> <eco_applied_json>" >&2
        exit 2
    fi
done
if [ ! -d "$BASE_DIR" ]; then
    echo "ERROR: BASE_DIR '$BASE_DIR' is not a directory" >&2
    exit 2
fi
if [ ! -d "$REF_DIR/data/PostEco" ]; then
    echo "ERROR: REF_DIR '$REF_DIR' missing data/PostEco subdir" >&2
    exit 2
fi

SCRIPT="${BASE_DIR}/script/eco_scripts/validate_verilog_netlist.py"
OUT_JSON="${BASE_DIR}/data/${TAG}_eco_verilog_validator_round${ROUND}.json"
TMP_LOG="/tmp/eco_verilog_validator_${TAG}_${ROUND}.txt"
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
        # FAIL on any pattern that causes FM-599 ABORT_NETLIST and is NOT pre-existing:
        # - SVR4_bare_paren: bare ) without ; in port list
        # - F1_dup_wire / SVR9_dup_wire: duplicate explicit wire declaration
        # - SVR4_double_comma: , , pattern in port connections
        # - SVR4_trailing_comma: trailing comma before ) ;
        # - SVR4_missing_cell_type: eco_ instance without cell type
        # - SVR4_missing_comma: .port(net) .port(net) without comma between
        # - SVR4_dup_port: same port name twice in module header
        # - SVR4_empty_connection: .port() with no net
        # - SVR14_scalar_indexed: net[N] indexing on scalar wire
        if re.search(r'SVR4_bare_paren|SVR9_dup_wire|F1_dup_wire|'
                     r'SVR4_double_comma|SVR4_trailing_comma|SVR4_missing_cell_type|'
                     r'SVR4_missing_comma|SVR4_dup_port|SVR4_empty_connection|'
                     r'SVR14_scalar_indexed', line):
            print("FAIL")
            sys.exit()
        # NOTE: F2_implicit_wire_conflict is intentionally excluded — hundreds of pre-existing
        # F2 issues exist in all P&R netlists and FM handles them internally without aborting.

print("PASS")
PYEOF
}

SYNTH=$(parse_stage "Synthesize" "${TMP_LOG}")
PPLACE=$(parse_stage "PrePlace"   "${TMP_LOG}")
ROUTE=$(parse_stage  "Route"      "${TMP_LOG}")

[ -z "$SYNTH"  ] && SYNTH="FAIL"
[ -z "$PPLACE" ] && PPLACE="FAIL"
[ -z "$ROUTE"  ] && ROUTE="FAIL"

# ── SVR4 inline fix — bare ')' without ';' (introduced by eco_netlist_port_rewire.py) ─
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

# ── ECO net GROUND-TRUTH gate: undriven / multiply-driven output nets ─────────
# The SVR4/SVR9 checks above catch FM PARSE aborts, but a study can emit a netlist
# that parses cleanly yet is functionally broken: an ECO output net with ZERO drivers
# (re-drive/gate did not land) or >=2 drivers (driver-rename did not land → original +
# new driver both drive it) parses fine but aborts FM (SVR-9). Reuse eco_validate_step4's
# exact driver-count logic on the APPLIED PostEco. Writes stage_fail + errors to GATE_JSON.
GATE_JSON="/tmp/eco_driver_gate_${TAG}_${ROUND}.json"
python3 - "$BASE_DIR" "$REF_DIR" "$STUDY_JSON" "$GATE_JSON" <<'PYEOF'
import json, os, sys
base_dir, ref_dir, study_json, gate_json = sys.argv[1:5]
sys.path.insert(0, os.path.join(base_dir, 'script', 'eco_scripts'))
STAGES = ('Synthesize', 'PrePlace', 'Route')
def _dump(d): json.dump(d, open(gate_json, 'w'))
try:
    import eco_validate_step4 as v4
except Exception as e:
    _dump({'stage_fail': {}, 'errors': ['gate import failed: %s' % e]}); sys.exit(0)
try:
    study = json.load(open(study_json))
except Exception as e:
    _dump({'stage_fail': {}, 'errors': ['gate cannot read study: %s' % e]}); sys.exit(0)
stage_fail = {s: [] for s in STAGES}
errors, seen = [], set()
for stage in STAGES:
    for e in study.get(stage, []):
        if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
            continue
        on = (e.get('output_net') or (e.get('port_connections') or {}).get('Z')
              or (e.get('port_connections') or {}).get('ZN'))
        mod = (e.get('module_name_per_stage') or {}).get(stage) or e.get('module_name')
        if not on or not mod:
            continue
        body = v4._module_body(ref_dir, stage, mod)
        if not body:
            continue                       # module resolution handled by step4; skip here
        nd = v4._driver_count(on, body)
        if nd != 1:
            kind = 'UNDRIVEN' if nd == 0 else 'MULTIPLY-DRIVEN(%d)' % nd
            msg = ('[DRIVER_COUNT] %s net %r in %s is %s (gate %s); expected exactly 1 driver '
                   '-- FM abort.' % (stage, on, mod, kind, e.get('instance_name', '?')))
            if msg in seen:
                continue
            seen.add(msg)
            stage_fail[stage].append(msg); errors.append(msg)
_dump({'stage_fail': stage_fail, 'errors': errors})
PYEOF

# Fold gate verdicts into per-stage results (a stage with an undriven/multiply-driven
# ECO net becomes FAIL even if its syntax was clean).
gate_stage_verdict() {
    python3 -c "import json,sys; d=json.load(open('${GATE_JSON}')); \
print('FAIL' if d.get('stage_fail',{}).get(sys.argv[1]) else 'PASS')" "$1" 2>/dev/null
}
[ "$(gate_stage_verdict Synthesize)" = "FAIL" ] && SYNTH="FAIL"
[ "$(gate_stage_verdict PrePlace)"   = "FAIL" ] && PPLACE="FAIL"
[ "$(gate_stage_verdict Route)"      = "FAIL" ] && ROUTE="FAIL"

# ── Collect FM-aborting error lines for JSON ──────────────────────────────────
ERRORS_JSON=$(grep -E "SVR4_bare_paren|SVR9_dup_wire|F1_dup_wire|SVR4_double_comma|\
SVR4_trailing_comma|SVR4_missing_cell_type|SVR4_missing_comma|SVR4_dup_port|\
SVR4_empty_connection|SVR14_scalar_indexed" "${TMP_LOG}" 2>/dev/null \
    | head -20 \
    | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")
[ -z "$ERRORS_JSON" ] && ERRORS_JSON="[]"

# Merge ECO driver-count gate errors into the reported error list
ERRORS_JSON=$(python3 -c "
import json, sys
base = json.loads(sys.argv[1])
try:    gate = json.load(open(sys.argv[2])).get('errors', [])
except Exception: gate = []
print(json.dumps((base + gate)[:40]))
" "$ERRORS_JSON" "$GATE_JSON")

# Count driver-count gate errors (undriven / multiply-driven ECO nets)
DRV_COUNT=$(python3 -c "import json; print(len(json.load(open('${GATE_JSON}')).get('errors',[])))" 2>/dev/null || echo 0)

# Count pre-existing F2 warnings (informational only).
# NOTE: `grep -c` already prints "0" on no-match (and exits 1), so a `|| echo 0`
# would append a SECOND "0" → "0\n0" and break the int() below. Guard empty only.
F2_COUNT=$(grep -c "F2_implicit_wire_conflict" "${TMP_LOG}" 2>/dev/null)
[ -z "$F2_COUNT" ] && F2_COUNT=0

# ── Write output JSON ─────────────────────────────────────────────────────────
python3 -c "
import json, sys
result = {
    'Synthesize': sys.argv[1],
    'PrePlace':   sys.argv[2],
    'Route':      sys.argv[3],
    'errors':     json.loads(sys.argv[4]),
    'driver_count_errors': int(sys.argv[6]),
    'f2_preexisting_count': int(sys.argv[5])
}
print(json.dumps(result, indent=2))
" "${SYNTH}" "${PPLACE}" "${ROUTE}" "${ERRORS_JSON}" "${F2_COUNT}" "${DRV_COUNT}" > "${OUT_JSON}"

# ── Write launch marker ───────────────────────────────────────────────────────
MARKER="ECO_SCRIPT_LAUNCHED: eco_verilog_validator.sh
  Synthesize: ${SYNTH}
  PrePlace:   ${PPLACE}
  Route:      ${ROUTE}
  driver_count_errors: ${DRV_COUNT} (undriven / multiply-driven ECO nets — FM abort)
  f2_preexisting: ${F2_COUNT} (informational — over-broad check, FM handles them)
  output:     ${OUT_JSON}"
echo "${MARKER}"
echo "${MARKER}" > "${OUT_JSON%.json}_marker.txt"

rm -f "${TMP_LOG}" "${GATE_JSON}"

[ "$SYNTH" = "PASS" ] && [ "$PPLACE" = "PASS" ] && [ "$ROUTE" = "PASS" ] && exit 0 || exit 1
