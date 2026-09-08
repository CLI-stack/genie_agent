#!/bin/tcsh
# Validate ECO TileBuilder directory and emit ECO_ANALYZE_MODE_ENABLED signal
# Parameters: refDir tag tile integer(jira_number)
# Called by genie_cli.py — runs synchronously (thin wrapper, seconds)

set refDir    = $1
set tag       = $2
set tile      = $3
set jira_raw  = $4
set source_dir = `pwd`
set specfile = "$source_dir/data/${tag}_spec"

# Extract JIRA number (strip integer: prefix if present)
set jira_num = `echo $jira_raw | sed 's/integer://' | sed 's/^://g' | xargs`

# Validate JIRA number
if ("$jira_num" == "" || "$jira_num" == " ") then
    echo "#text#" >> $specfile
    echo "ERROR: JIRA ticket number is required. Usage: run eco analysis at <dir> for <tile> <jira_number>" >> $specfile
    echo "Example: run eco analysis at /proj/xxx/tiles/... for umccmd 9874" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif


# Strip prefixes
set refdir_name = `echo $refDir | sed 's/refDir://' | sed 's/^://g'`
set tile_name   = `echo $tile   | sed 's/tile://'   | sed 's/^://g' | xargs`

echo "ECO Analyze: validating $tile_name at $refdir_name (JIRA: $jira_num)"

# --- Validation: refDir ---

if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "#text#" >> $specfile
    echo "ERROR: refDir is empty or invalid" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -d $refdir_name) then
    echo "#text#" >> $specfile
    echo "ERROR: Directory not found: $refdir_name" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -f "$refdir_name/revrc.main") then
    echo "#text#" >> $specfile
    echo "ERROR: Not a TileBuilder directory (revrc.main not found): $refdir_name" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Option 2 single-output: ref dir is a valid TileBuilder dir -> from here every
# artifact (incl. this analyze spec) lands under the tile AI_ECO_FLOW_<TAG> tree.
# Simple mode uses AI_ECO_FLOW_SIMPLE_<TAG>; Complete mode uses AI_ECO_FLOW_<TAG>.
set mode_val = "complete"
if ( "$5" != "" ) then
    set mode_val = `echo "$5" | sed 's/mode://' | sed 's/^://g' | xargs`
endif
if ( "$?ECO_MODE" ) then
    if ( "$ECO_MODE" == "simple" ) set mode_val = "simple"
endif

if ( "$mode_val" == "simple" ) then
    set eco_flow_dir = "$refdir_name/AI_ECO_FLOW_SIMPLE_${tag}"
else
    set eco_flow_dir = "$refdir_name/AI_ECO_FLOW_${tag}"
endif
mkdir -p $eco_flow_dir/data $eco_flow_dir/runs
if ( -f $specfile ) cp $specfile $eco_flow_dir/data/${tag}_spec >& /dev/null
set specfile = "$eco_flow_dir/data/${tag}_spec"

# --- Validation: RTL directories ---

foreach rtl_dir ("data/PreEco/SynRtl" "data/SynRtl")
    if (! -d "$refdir_name/$rtl_dir") then
        echo "#text#" >> $specfile
        echo "ERROR: RTL directory not found: $refdir_name/$rtl_dir" >> $specfile
        echo "#text end#" >> $specfile
        set run_status = "failed"
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
end

# --- Validation: PreEco netlists ---
# Synthesize is MANDATORY. PrePlace/Route are OPTIONAL (a Synthesize-only run is
# allowed, e.g. simple-mode direct inputs). A stage whose PreEco netlist is absent
# is skipped for the rest of the flow — the present stages are collected in
# `stages_present`. (Complete-mode TileBuilder dirs always carry all 3, so this
# relaxation only enables the Synth-only case; it never weakens complete mode.)

if (! -f "$refdir_name/data/PreEco/Synthesize.v.gz") then
    echo "#text#" >> $specfile
    echo "ERROR: Required PreEco netlist not found: $refdir_name/data/PreEco/Synthesize.v.gz" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

set stages_present = ("Synthesize")
foreach stage ("PrePlace" "Route")
    if (-f "$refdir_name/data/PreEco/${stage}.v.gz") then
        set stages_present = ($stages_present $stage)
    else
        echo "#text#" >> $specfile
        echo "INFO: PreEco/${stage}.v.gz not provided — Synthesize-only run, skipping $stage" >> $specfile
        echo "#text end#" >> $specfile
    endif
end

# --- Validation: PostEco netlists (auto-copy from PreEco if missing) ---

if (! -d "$refdir_name/data/PostEco") then
    echo "#text#" >> $specfile
    echo "ERROR: PostEco directory not found: $refdir_name/data/PostEco" >> $specfile
    echo "#text end#" >> $specfile
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

foreach stage ($stages_present)
    if (! -f "$refdir_name/data/PostEco/${stage}.v.gz") then
        echo "#text#" >> $specfile
        echo "INFO: PostEco/${stage}.v.gz not found — copying from PreEco as baseline" >> $specfile
        echo "#text end#" >> $specfile
        cp "$refdir_name/data/PreEco/${stage}.v.gz" "$refdir_name/data/PostEco/${stage}.v.gz"
        if ($status != 0) then
            echo "#text#" >> $specfile
            echo "ERROR: Failed to copy PreEco/${stage}.v.gz to PostEco/" >> $specfile
            echo "#text end#" >> $specfile
            set run_status = "failed"
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        endif
    endif
end

# --- All validation passed — write summary ---

echo "#table#" >> $specfile
echo "Field,Value" >> $specfile
echo "Tile,$tile_name" >> $specfile
echo "JIRA,$jira_num" >> $specfile
echo "TileBuilder Dir,$refdir_name" >> $specfile
echo "PreEco RTL,$refdir_name/data/PreEco/SynRtl" >> $specfile
echo "PostEco RTL,$refdir_name/data/SynRtl" >> $specfile
echo "Stages,$stages_present (Synthesize mandatory; PrePlace/Route optional)" >> $specfile
echo "PreEco Netlists,$stages_present (verified)" >> $specfile
echo "PostEco Netlists,$stages_present (verified or copied from PreEco)" >> $specfile
echo "Status,Validation PASSED — ECO orchestrator launching" >> $specfile
echo "#table end#" >> $specfile

# --- Single output tree (Option 2): all ECO working artifacts (data/<TAG>_*) live
#     under the tile's AI_ECO_FLOW_<TAG>/data. The flow still RUNS from BASE_DIR (the
#     repo user dir — where script/ lives), but every output path in the MDs points at
#     <AI_ECO_FLOW_DIR>/data instead of the repo's data/. Nothing lands in <repo> data.
# --- Emit signal (captured by genie_cli.py) ---

echo ""
echo "========================================================================"
echo "ECO_ANALYZE_MODE_ENABLED"
echo "TAG=$tag"
echo "REF_DIR=$refdir_name"
echo "TILE=$tile_name"
echo "JIRA=$jira_num"
echo "BASE_DIR=$source_dir"
echo "AI_ECO_FLOW_DIR=$eco_flow_dir"
echo "LOG_FILE=$source_dir/runs/${tag}.log"
echo "SPEC_FILE=$eco_flow_dir/data/${tag}_spec"
echo "========================================================================"
echo ""

# Record task as finished (for task tracking)
cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
