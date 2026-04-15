#!/bin/tcsh
# post_eco_formality.csh - Reset, run and report PostEco Formality verification
# Usage: post_eco_formality.csh <tile> <refDir> <tag>
#
# Reads optional config file: data/<tag>_eco_fm_config
#   ECO_TARGETS=<space-separated list>   (default: all 3)
#   RUN_SVF_GEN=0|1                      (default: 0)
#   ECO_SVF_ENTRIES=<path to tcl file>   (default: none)
#
# Flow:
#   Phase A (if RUN_SVF_GEN=1):
#     1. Reset + run FmEcoSvfGen
#     2. Poll until FmEcoSvfGen complete
#     3. Append ECO_SVF_ENTRIES to data/svf/EcoChange.svf
#   Phase B:
#     4. Reset + run only specified ECO_TARGETS
#     5. Poll until all complete
#     6. Extract and report results

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
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Validate refdir_name
if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "ERROR: refdir_name is empty or invalid" >> $source_dir/data/${tag}_spec
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

#------------------------------------------------------------------------------
# READ CONFIG FILE (if exists)
# Config is written by ORCHESTRATOR at <refDir>/data/eco_fm_config
# (fixed name per refDir — not tag-based, since post_eco_formality gets its own tag from genie_cli)
#------------------------------------------------------------------------------
set config_file = "$refdir_name/data/eco_fm_config"

# Defaults
set all_eco_targets = (FmEqvEcoSynthesizeVsSynRtl FmEqvEcoPrePlaceVsEcoSynthesize FmEqvEcoRouteVsEcoPrePlace)
set eco_targets     = ($all_eco_targets)
set run_svf_gen     = 0
set eco_svf_entries = ""

if (-f "$config_file") then
    echo "Reading ECO FM config: $config_file"

    # ECO_TARGETS
    set cfg_targets = `grep "^ECO_TARGETS=" "$config_file" | sed 's/ECO_TARGETS=//'`
    if ("$cfg_targets" != "") then
        set eco_targets = ($cfg_targets)
        echo "  ECO_TARGETS: $eco_targets"
    endif

    # RUN_SVF_GEN
    set cfg_svfgen = `grep "^RUN_SVF_GEN=" "$config_file" | sed 's/RUN_SVF_GEN=//'`
    if ("$cfg_svfgen" == "1") then
        set run_svf_gen = 1
        echo "  RUN_SVF_GEN: 1 (FmEcoSvfGen will run first)"
    endif

    # ECO_SVF_ENTRIES
    set eco_svf_entries = `grep "^ECO_SVF_ENTRIES=" "$config_file" | sed 's/ECO_SVF_ENTRIES=//'`
    if ("$eco_svf_entries" != "") then
        echo "  ECO_SVF_ENTRIES: $eco_svf_entries"
    endif
endif

#------------------------------------------------------------------------------
# SOURCE LSF/TILEBUILDER ENVIRONMENT
#------------------------------------------------------------------------------
source $source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh

#------------------------------------------------------------------------------
# PHASE A: RUN FmEcoSvfGen (if needed, as dependency for FmEqvEcoSynthesizeVsSynRtl)
#------------------------------------------------------------------------------
set synth_in_targets = 0
foreach tgt ($eco_targets)
    if ("$tgt" == "FmEqvEcoSynthesizeVsSynRtl") set synth_in_targets = 1
end

if ($run_svf_gen == 1 && $synth_in_targets == 1) then

    echo "#text#" >> $out
    echo "PHASE A: Running FmEcoSvfGen (SVF dependency for FmEqvEcoSynthesizeVsSynRtl)..." >> $out
    echo "#text end#" >> $out

    cd $tile_dir
    echo "Resetting FmEcoSvfGen ..."
    TileBuilderTerm -x "serascmd -find_jobs 'name=~FmEcoSvfGen dir=~${tile_dir_name}' --action reset"
    sleep 20
    echo "Running FmEcoSvfGen ..."
    TileBuilderTerm -x "serascmd -find_jobs 'name=~FmEcoSvfGen dir=~${tile_dir_name}' --action run"
    cd $source_dir

    # Poll until FmEcoSvfGen complete (60 min timeout, 5 min intervals)
    set svfgen_log = "/tmp/tb_svfgen_status_${tag}.log"
    set elapsed    = 0
    set max_elapsed = 3600
    set poll_interval = 300
    set svfgen_done = 0

    while ($svfgen_done == 0)
        sleep $poll_interval
        @ elapsed += $poll_interval

        cd $tile_dir
        TileBuilderTerm -x "TileBuilderShow >& $svfgen_log"
        cd $source_dir
        sleep 5

        set svfgen_status = "UNKNOWN"
        if (-f "$svfgen_log" && -s "$svfgen_log") then
            set svfgen_status = `grep "FmEcoSvfGen" $svfgen_log | awk '{print $NF}'`
        endif

        echo "FmEcoSvfGen status: $svfgen_status (${elapsed}s elapsed)"

        if ("$svfgen_status" == "PASSED" || "$svfgen_status" == "WARNING" || "$svfgen_status" == "DONE") then
            set svfgen_done = 1
            echo "#text#" >> $out
            echo "FmEcoSvfGen completed: $svfgen_status" >> $out
            echo "#text end#" >> $out
        else if ("$svfgen_status" == "FAILED") then
            echo "#text#" >> $out
            echo "ERROR: FmEcoSvfGen FAILED — aborting ECO FM run. EcoChange.svf may be incomplete." >> $out
            echo "#text end#" >> $out
            echo "OVERALL ECO FM RESULT: FAIL" >> $out
            echo "#table#" >> $out
            echo "Target,Status" >> $out
            foreach tgt ($eco_targets)
                echo "$tgt,ABORTED (FmEcoSvfGen failed)" >> $out
            end
            echo "#table end#" >> $out
            rm -f $svfgen_log
            set run_status = "failed"
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        else if ($elapsed >= $max_elapsed) then
            echo "#text#" >> $out
            echo "ERROR: FmEcoSvfGen timeout after 60 min" >> $out
            echo "#text end#" >> $out
            rm -f $svfgen_log
            set run_status = "failed"
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        endif
    end
    rm -f $svfgen_log

    # Append ECO SVF entries to EcoChange.svf AFTER FmEcoSvfGen regenerated it
    if ("$eco_svf_entries" != "" && -f "$eco_svf_entries") then
        echo "Appending ECO SVF entries to data/svf/EcoChange.svf ..."
        cat "$eco_svf_entries" >> "$tile_dir/data/svf/EcoChange.svf"
        echo "#text#" >> $out
        echo "ECO SVF entries appended to data/svf/EcoChange.svf" >> $out
        echo "#text end#" >> $out
    endif

endif

#------------------------------------------------------------------------------
# PHASE B: RESET AND RUN SPECIFIED ECO FM TARGETS
#------------------------------------------------------------------------------
echo "#text#" >> $out
echo "PHASE B: Resetting and launching ECO FM targets: $eco_targets" >> $out
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
# POLL UNTIL ALL SPECIFIED TARGETS COMPLETE (180 min timeout, 15 min intervals)
#------------------------------------------------------------------------------
echo "Monitoring ECO FM targets (max 180 min, checking every 15 min)..."

set tb_status_log = "/tmp/tb_eco_fm_status_${tag}.log"
set elapsed       = 0
set max_elapsed   = 10800
set poll_interval = 900
set all_done      = 0

while ($all_done == 0)
    sleep $poll_interval
    @ elapsed += $poll_interval

    cd $tile_dir
    TileBuilderTerm -x "TileBuilderShow >& $tb_status_log"
    cd $source_dir
    sleep 5

    set done_count  = 0
    set total_count = 0
    foreach tgt ($eco_targets)
        @ total_count++
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

    echo "ECO FM: ${done_count}/${total_count} targets complete (${elapsed}s elapsed)"

    if ($done_count == $total_count) then
        echo "All ${total_count} ECO FM targets complete after ${elapsed}s"
        set all_done = 1
    else if ($elapsed >= $max_elapsed) then
        echo "ERROR: ECO FM timeout after 180 min — only ${done_count}/${total_count} targets complete" >> $out
        rm -f $tb_status_log
        set run_status = "failed"
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
end

rm -f $tb_status_log

#------------------------------------------------------------------------------
# EXTRACT AND REPORT RESULTS PER TARGET
#------------------------------------------------------------------------------
echo "#text#" >> $out
echo "ECO FORMALITY REPORT: $tile_dir_name" >> $out
echo "#text end#" >> $out

set overall_pass = 1

foreach tgt ($eco_targets)

    set fm_dir  = "${tile_dir}/rpts/${tgt}"
    set fm_dat  = "${fm_dir}/${tgt}.dat"
    set fp_rpt  = "${fm_dir}/${tgt}__failing_points.rpt.gz"

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
