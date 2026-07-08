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

    # ECO_TARGETS — explicit list (takes priority)
    set cfg_targets = `grep "^ECO_TARGETS=" "$config_file" | sed 's/ECO_TARGETS=//'`
    if ("$cfg_targets" != "") then
        set eco_targets = ($cfg_targets)
        echo "  ECO_TARGETS: $eco_targets"

    # SMART_TARGETS=1 — auto-select based on changed stages + prior FM verdicts
    # Agent writes: SMART_TARGETS=1, APPLIED_JSON=<path>, PREV_VERIFY_JSON=<path>
    # Script reads eco_applied JSON (which stages changed) and prev eco_fm_verify
    # (which targets passed) and picks only the necessary targets.
    # Rule: only run a target if its reference stage changed OR it never passed before.
    else if (`grep -c "^SMART_TARGETS=1" "$config_file"` > 0) then
        set smart_applied = `grep "^APPLIED_JSON=" "$config_file" | sed 's/APPLIED_JSON=//'`
        set smart_prev    = `grep "^PREV_VERIFY_JSON=" "$config_file" | sed 's/PREV_VERIFY_JSON=//'`
        echo "  SMART_TARGETS=1 — computing optimal targets..."
        set eco_targets = (`python3 -c "
import json, sys
T = ['FmEqvEcoSynthesizeVsSynRtl','FmEqvEcoPrePlaceVsEcoSynthesize','FmEqvEcoRouteVsEcoPrePlace']
changed = {'Synthesize':0,'PrePlace':0,'Route':0}
try:
    d=json.load(open('$smart_applied'))
    for s in changed:
        changed[s]=sum(1 for e in d.get(s,[]) if e.get('status') in ('APPLIED','INSERTED'))
except: pass
prev={}
try:
    pt=json.load(open('$smart_prev')).get('per_target',{})
    prev={t:(v.get('verdict')=='PASS') for t,v in pt.items() if isinstance(v,dict)}
except: pass
targets=[]
if changed['Synthesize']>0 or not prev.get(T[0],False): targets.append(T[0])
if changed['PrePlace']>0 or changed['Synthesize']>0 or not prev.get(T[1],False): targets.append(T[1])
if changed['Route']>0 or changed['PrePlace']>0: targets.append(T[2])
print(' '.join(targets) if targets else ' '.join(T))
"`)
        echo "  SMART_TARGETS selected: $eco_targets"
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

#------------------------------------------------------------------------------
# CLEANUP STALE RPTS FOR TARGETS BEING RUN (avoid confusion with prior rounds)
# Only cleans rpts for targets in $eco_targets — skipped targets are untouched
# so their PASS results remain readable for carry-forward and HTML reports.
#------------------------------------------------------------------------------
echo "Cleaning stale rpts for targets being re-run..." >> $out
foreach tgt ($eco_targets)
    set rpt_dir = "$tile_dir/rpts/$tgt"
    if (-d "$rpt_dir") then
        echo "  Removing all stale rpts: $rpt_dir" >> $out
        rm -rf "$rpt_dir"
    endif
end
echo "Stale rpt cleanup done." >> $out

#-- submit each target ONCE (reset+run). Do NOT re-submit later: a job that is
#-- legitimately queued in LSF just loses its queue slot on re-submit and waits
#-- even LONGER, so a pending target is WAITED OUT, never kicked. --
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
# POLL UNTIL ALL TARGETS COMPLETE
#
# Timeout policy (this script is the authority; the agent poll is only a backstop):
#   * runtime budget (max_elapsed, 12h) accrues ONLY while every unfinished target
#     is actively RUNNING — it RESETS whenever any target is still pending/queued,
#     so LSF queue-wait never burns the FM runtime budget.
#   * a pending/queued target is simply WAITED OUT (never re-submitted) up to
#     max_pending_wait (12h) of cumulative queue time. If it still never starts,
#     we give up: it is reported NOT_RUN downstream. This is an FM-scheduling
#     problem, NOT a netlist defect — it must NOT trigger a re-study round
#     (the collector yields NOT_RUN/PARTIAL -> APPLY_ORCHESTRATOR STOP).
#   Worst case ~ max_pending_wait (12h) + max_elapsed (12h) = 24h, under the
#   agent's 30h poll backstop.
#------------------------------------------------------------------------------
echo "Monitoring ECO FM targets (runtime countdown accrues only while all RUNNING; a pending target is waited out, never re-submitted)..."

set tb_status_log    = "/tmp/tb_eco_fm_status_${tag}.log"
set elapsed          = 0
set max_elapsed      = 43200
set pending_elapsed  = 0
set max_pending_wait = 43200
set poll_interval    = 300
set all_done         = 0

while ($all_done == 0)
    sleep $poll_interval

    cd $tile_dir
    TileBuilderTerm -x "TileBuilderShow >& $tb_status_log"
    cd $source_dir
    sleep 5

    set done_count    = 0
    set running_count = 0
    set total_count   = 0
    foreach tgt ($eco_targets)
        @ total_count++
        set tgt_status = "UNKNOWN"
        if (-f "$tb_status_log" && -s "$tb_status_log") then
            set tgt_status = `grep "$tgt" $tb_status_log | awk '{print $NF}'`
            if ("$tgt_status" == "") set tgt_status = "UNKNOWN"
        endif

        # Terminal (finished): PASSED/WARNING/FAILED/DONE/ABORTED
        if ("$tgt_status" == "PASSED" || "$tgt_status" == "WARNING" || \
            "$tgt_status" == "FAILED" || "$tgt_status" == "DONE"   || \
            "$tgt_status" == "ABORTED") then
            @ done_count++
        else if ("$tgt_status" == "RUNNING") then
            @ running_count++
        endif
    end

    # Pending = not finished AND not RUNNING (NOTRUN, PENDING, WAITING, HOLD,
    # BLOCKED, QUEUED, UNKNOWN, ...). i.e. still waiting in the LSF queue.
    @ pending_count = $total_count - $done_count - $running_count

    if ($done_count == $total_count) then
        echo "All ${total_count} ECO FM targets complete after ${elapsed}s of all-running time"
        set all_done = 1
    else if ($pending_count > 0) then
        # Queued — do NOT burn the runtime budget and do NOT re-submit (re-queuing a
        # waiting job only makes it wait longer). Reset the runtime countdown; just
        # accrue a bounded pending wait so a job stuck in the queue forever bails out.
        set elapsed = 0
        @ pending_elapsed += $poll_interval
        echo "ECO FM: ${done_count} done, ${running_count} running, ${pending_count} pending — runtime RESET, waiting out queue (${pending_elapsed}s / ${max_pending_wait}s)"
        if ($pending_elapsed >= $max_pending_wait) then
            echo "ERROR: ECO FM target(s) stuck pending in queue >12h — ${done_count}/${total_count} complete, ${pending_count} never started running" >> $out
            echo "  (reported NOT_RUN downstream; FM-scheduling issue, NOT escalated to a re-study round)" >> $out
            set all_done = 1
        endif
    else
        # All remaining targets are actively RUNNING — accrue the runtime budget.
        @ elapsed += $poll_interval
        echo "ECO FM: ${done_count}/${total_count} complete, ${running_count} running (runtime ${elapsed}s / ${max_elapsed}s)"
        if ($elapsed >= $max_elapsed) then
            echo "ERROR: ECO FM timeout after 12 hours of all-running — only ${done_count}/${total_count} targets complete" >> $out
            rm -f $tb_status_log
            set run_status = "failed"
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        endif
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

# AUTO-INVOKE FM STATUS COLLECTOR (deterministic, single source of truth — removes
# orchestrator-context-pressure failure mode where ABORT verdicts got silently dropped
# AND removes the older two-script split where verdict + classification were written
# to separate JSON files. Runs unconditionally after every FM submission.
#
# Inputs read by status_collector:
#   <REF_DIR>/rpts/<target>/<target>__runtime.rpt.gz   — phase status table
#   <REF_DIR>/rpts/<target>/<target>.dat               — runStatus metadata
#   <REF_DIR>/rpts/<target>/<target>__failing_points.rpt.gz — failing compare points
#   <REF_DIR>/logs/<target>.log[.gz|.bz2]              — for ABORT classification
#                                                        (uses eco_fm_abort_patterns.yaml
#                                                         via eco_extract_fm_abort_cause
#                                                         imported as a library)
#
# Output: <BASE_DIR>/data/<TAG>_eco_fm_verify.json (canonical v1 schema with `verdict`)
# eco_fm_runner reads ONLY this file to decide PASS/FAIL/ABORT branching.
#
# History:
#   - Run 20260511201004 + 20260511083831: orchestrator wrote round_handoff but didn't
#     classify ABORT → flow stopped silently. Fixed by adding csh-side auto-invoke.
#   - Run 20260512070625: prior split (eco_extract_fm_abort_cause.py CLI) had a status-
#     field blind spot — wrote "No ABORT detected" when overall_status was "FM_FAILED"
#     but per-target was ABORT → 3 rounds of wrong fixes (5 hours wasted). Now fixed
#     by replacing the old classifier CLI with eco_fm_status_collector.py which owns
#     the canonical verdict end-to-end.
set fm_verify_path = "$source_dir/data/${tag}_eco_fm_verify.json"
set logs_dir       = "$refdir_name/logs"
# Read PREV_VERIFY_JSON from config for carry-forward of skipped targets
set prev_verify_arg = ""
set cfg_prev_verify = `grep "^PREV_VERIFY_JSON=" "$config_file" | sed 's/PREV_VERIFY_JSON=//'`
if ("$cfg_prev_verify" != "" && -f "$cfg_prev_verify") then
    set prev_verify_arg = "--prev-verify $cfg_prev_verify"
    echo "  PREV_VERIFY_JSON: $cfg_prev_verify (carry forward PASS results)" >> $out
endif
if (-d "$refdir_name/rpts" && -d "$logs_dir") then
    echo "" >> $out
    echo "=== Auto-invoking eco_fm_status_collector.py ===" >> $out
    python3 $source_dir/script/eco_scripts/eco_fm_status_collector.py \
        --ref-dir $refdir_name \
        --tag     $tag \
        --round   1 \
        --targets `echo "$eco_targets" | tr ' ' ','` \
        $prev_verify_arg \
        --output  $fm_verify_path >> $out 2>&1
    echo "Canonical verdict: $fm_verify_path" >> $out
endif

cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
