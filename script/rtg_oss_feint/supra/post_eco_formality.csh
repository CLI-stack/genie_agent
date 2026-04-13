#!/bin/tcsh
# eco_formality.csh - Reset, run and report PostEco Formality verification
# Usage: eco_formality.csh <tile> <refDir> <tag>
#
# Runs all 3 PostEco FM targets:
#   FmEqvEcoSynthesizeVsSynRtl
#   FmEqvEcoPrePlaceVsEcoSynthesize
#   FmEqvEcoRouteVsEcoPrePlace
#
# For each target extracts:
#   - Overall PASS/FAIL
#   - Failing points count + detail
#   - Non-equivalent points from .dat

set tile   = $1
set refDir = $2
set tag    = $3
set source_dir = `pwd`
touch $source_dir/data/${tag}_spec

# Parse tile (format: tile:umccmd or tile:umcdat)
set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}' | sed 's/^ //'`

# Parse refDir (format: refDir:/path/to/tile_dir)
set refdir_name = `echo $refDir | sed 's/:/ /g' | awk '{$1="";print $0}' | sed 's/^ //'`

# Validate tile_name
if ("$tile_name" == "" || "$tile_name" == " ") then
    echo "ERROR: tile_name is empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input tile: $tile" >> $source_dir/data/${tag}_spec
    echo "Usage: $0 tile:<tile_name> refDir:/path/to/tile_dir <tag>"
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Validate refdir_name
if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "ERROR: refdir_name is empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input refDir: $refDir" >> $source_dir/data/${tag}_spec
    echo "Usage: $0 tile:<tile_name> refDir:/path/to/tile_dir <tag>"
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -d "$refdir_name") then
    echo "ERROR: Directory not found: $refdir_name" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -f "$refdir_name/revrc.main") then
    echo "ERROR: Not a TileBuilder directory (revrc.main not found): $refdir_name" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

set tile_dir      = "$refdir_name"
set tile_dir_name = `basename $tile_dir`
set out           = "$source_dir/data/${tag}_spec"

# ECO FM targets (3 PostEco targets)
set eco_targets = (FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace)

#------------------------------------------------------------------------------
# SOURCE LSF/TILEBUILDER ENVIRONMENT
#------------------------------------------------------------------------------
source $source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh

#------------------------------------------------------------------------------
# PHASE 1: RESET AND RUN ALL 3 ECO FM TARGETS
#------------------------------------------------------------------------------
echo "#text#" >> $out
echo "ECO FORMALITY: Resetting and launching all 3 PostEco FM targets..." >> $out
echo "#text end#" >> $out

cd $tile_dir

foreach tgt ($eco_targets)
    echo "Resetting $tgt ..."
    TileBuilderTerm -x "serascmd -find_jobs 'name=~${tgt} dir=~${tile_dir_name}' --action reset"
    sleep 20
    echo "Running $tgt ..."
    TileBuilderTerm -x "serascmd -find_jobs 'name=~${tgt} dir=~${tile_dir_name}' --action run"
end

cd $source_dir

#------------------------------------------------------------------------------
# PHASE 2: POLL UNTIL ALL 3 TARGETS COMPLETE (180 min timeout, 15 min intervals)
#------------------------------------------------------------------------------
echo "Monitoring ECO FM targets (max 180 min, checking every 15 min)..."

set tb_status_log = "/tmp/tb_eco_fm_status_${tag}.log"
set elapsed       = 0
set max_elapsed   = 10800   # 180 minutes in seconds
set poll_interval = 900     # 15 minutes in seconds
set all_done      = 0

while ($all_done == 0)
    sleep $poll_interval
    @ elapsed += $poll_interval

    # Check TileBuilderShow
    cd $tile_dir
    TileBuilderTerm -x "TileBuilderShow >& $tb_status_log"
    cd $source_dir
    sleep 5

    # Count how many ECO targets are done
    set done_count = 0
    foreach tgt ($eco_targets)
        set tgt_status = "UNKNOWN"
        if (-f "$tb_status_log" && -s "$tb_status_log") then
            set tgt_status = `grep "$tgt" $tb_status_log | awk '{print $NF}'`
            if ("$tgt_status" == "") set tgt_status = "UNKNOWN"
        endif

        if ("$tgt_status" == "PASSED" || "$tgt_status" == "WARNING" || \
            "$tgt_status" == "FAILED" || "$tgt_status" == "DONE") then
            @ done_count++
        endif
    end

    echo "ECO FM: ${done_count}/3 targets complete (${elapsed}s elapsed)"

    if ($done_count == 3) then
        echo "All 3 ECO FM targets complete after ${elapsed}s"
        set all_done = 1
    else if ($elapsed >= $max_elapsed) then
        echo "ERROR: ECO FM timeout after 180 min — only ${done_count}/3 targets complete" >> $out
        rm -f $tb_status_log
        set run_status = "failed"
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
end

rm -f $tb_status_log

#------------------------------------------------------------------------------
# PHASE 3: EXTRACT AND REPORT RESULTS PER TARGET
#------------------------------------------------------------------------------
echo "#text#" >> $out
echo "ECO FORMALITY REPORT: $tile_dir_name" >> $out
echo "#text end#" >> $out

set overall_pass = 1

foreach tgt ($eco_targets)

    set fm_dir  = "${tile_dir}/rpts/${tgt}"
    set fm_dat  = "${fm_dir}/${tgt}.dat"
    set fp_rpt  = "${fm_dir}/${tgt}__failing_points.rpt.gz"

    # Read .dat file
    set lec_result  = "N/A"
    set exit_val    = "N/A"
    set num_noneq   = "N/A"
    set num_eq      = "N/A"
    set tgt_status  = "UNKNOWN"

    if (-f "$fm_dat") then
        set lec_result = `grep "^lecResult:"           "$fm_dat" | awk '{print $2}'`
        set exit_val   = `grep "^exitVal:"             "$fm_dat" | awk '{print $2}'`
        set num_noneq  = `grep "^numberOfNonEqPoints:" "$fm_dat" | awk '{print $2}'`
        set num_eq     = `grep "^numberOfEqPoints:"    "$fm_dat" | awk '{print $2}'`

        if ("$lec_result" == "SUCCEEDED" && "$exit_val" == "0") then
            set tgt_status = "PASS"
        else
            set tgt_status = "FAIL"
            set overall_pass = 0
        endif
    else
        set tgt_status = "FAIL - .dat not found"
        set overall_pass = 0
    endif

    # Failing points
    set failing_count  = "N/A"
    set failing_status = "N/A"

    if (-f "$fp_rpt") then
        set clean_check = `zcat "$fp_rpt" | grep -c "No failing compare points"`
        if ($status != 0) set clean_check = 0
        if ($clean_check > 0) then
            set failing_count  = 0
            set failing_status = "CLEAN"
        else
            set failing_count = `zcat "$fp_rpt" | grep -E "^[0-9]+ Failing" | awk '{print $1}'`
            if ("$failing_count" == "") then
                set failing_count  = 0
                set failing_status = "CLEAN"
            else
                set failing_status = "FAILED"
            endif
        endif
    endif

    # Write per-target table
    echo "#text#" >> $out
    echo "--- $tgt ---" >> $out
    echo "#table#" >> $out
    echo "Item,Value" >> $out
    echo "Target,$tgt" >> $out
    echo "Status,$tgt_status" >> $out
    if ("$lec_result" != "N/A") echo "LEC Result,$lec_result" >> $out
    if ("$num_eq"     != "N/A") echo "Equivalent Points,$num_eq" >> $out
    if ("$num_noneq"  != "N/A") echo "Non-Equivalent Points,$num_noneq" >> $out
    echo "Failing Points,$failing_count ($failing_status)" >> $out
    echo "Failing Points Report,$fp_rpt" >> $out
    echo "#table end#" >> $out
    echo "" >> $out

    # Failing point details (if any)
    if (-f "$fp_rpt" && "$failing_count" != "N/A" && "$failing_count" != "0") then
        echo "#text#" >> $out
        echo "FAILING POINTS ($failing_count) for ${tgt}:" >> $out
        echo "#table#" >> $out
        echo "Type,Path" >> $out
        zcat "$fp_rpt" | grep -E "^\s+Ref\s+" | \
            awk -v tile="$tile_name" '{type=$2; path=$3; gsub("r:/[^/]+/" tile "/", "", path); printf "%s,%s\n", type, path}' >> $out
        echo "#table end#" >> $out
        echo "" >> $out
    endif

end

#------------------------------------------------------------------------------
# OVERALL SUMMARY
#------------------------------------------------------------------------------
if ($overall_pass == 1) then
    set overall_result = "PASS"
else
    set overall_result = "FAIL"
endif

echo "#text#" >> $out
echo "OVERALL ECO FM RESULT: $overall_result" >> $out
echo "#table#" >> $out
echo "Target,Status" >> $out

foreach tgt ($eco_targets)
    set fm_dat = "${tile_dir}/rpts/${tgt}/${tgt}.dat"
    set s = "N/A"
    if (-f "$fm_dat") then
        set lr = `grep "^lecResult:" "$fm_dat" | awk '{print $2}'`
        set ev = `grep "^exitVal:"   "$fm_dat" | awk '{print $2}'`
        if ("$lr" == "SUCCEEDED" && "$ev" == "0") then
            set s = "PASS"
        else
            set s = "FAIL"
        endif
    endif
    echo "$tgt,$s" >> $out
end

echo "#table end#" >> $out

cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
