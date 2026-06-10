set tile = $1
set refDir = $2
set target = $3
set tag = $4
set prevElapsed = $5
set source_dir = `pwd`
set target_run_dir = ":"
set reply = ""
touch $source_dir/data/${tag}_spec

# Maximum total runtime limit (~60 hours)
# After this limit, monitoring will gracefully exit and send email with current status
# This prevents issues with xterm/DISPLAY expiring after ~3 days
set max_total_runtime = 216000

set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}'`
set refdir_name = `echo $refDir | sed 's/:/ /g' | awk '{$1="";print $0}'`
set target_name = `echo $target | sed 's/:/ /g' | awk '{$1="";print $0}'`
set prev_elapsed = `echo $prevElapsed | sed 's/.*://'`

if ("$prev_elapsed" == "") then
    set prev_elapsed = 0
endif

# Mandatory argument validation - exit with error if missing
if ("$tile_name" == "" || "$tile_name" == " ") then
    echo "ERROR: tile_name is REQUIRED but empty or invalid"
    echo "ERROR: tile_name is REQUIRED but empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input tile: $tile" >> $source_dir/data/${tag}_spec
    echo "Usage: monitor_tilebuilder.csh tile:<tilename> refDir:<path> target:<target> <tag>" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "ERROR: refdir_name (directory) is REQUIRED but empty or invalid"
    echo "ERROR: refdir_name (directory) is REQUIRED but empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input refDir: $refDir" >> $source_dir/data/${tag}_spec
    echo "Usage: monitor_tilebuilder.csh tile:<tilename> refDir:<path> target:<target> <tag>" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if ("$target_name" == "" || "$target_name" == " ") then
    echo "ERROR: target_name is REQUIRED but empty or invalid"
    echo "ERROR: target_name is REQUIRED but empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input target: $target" >> $source_dir/data/${tag}_spec
    echo "Usage: monitor_tilebuilder.csh tile:<tilename> refDir:<path> target:<target> <tag>" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -d "$refdir_name") then
    echo "ERROR: Tile directory does not exist: $refdir_name"
    echo "ERROR: Tile directory does not exist: $refdir_name" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -f "${refdir_name}/revrc.main") then
    echo "ERROR: revrc.main not found in $refdir_name"
    echo "ERROR: revrc.main not found in $refdir_name" >> $source_dir/data/${tag}_spec
    echo "This is not a valid TileBuilder directory" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

echo "Tile name: $tile_name"
echo "Tile directory: $refdir_name"
echo "Target name: $target_name"
echo "Previous elapsed time: ${prev_elapsed}s"
echo "Max runtime limit: ${max_total_runtime}s (~60 hours)"

# DEBUG: Session startup diagnostics
echo ""
echo "======================================================================"
echo "DEBUG: Session Startup Diagnostics"
echo "======================================================================"
echo "DEBUG: Session start time: `date`"
echo "DEBUG: Hostname: $HOST"
echo "DEBUG: DISPLAY: $DISPLAY"
echo "DEBUG: USER: $USER"
echo "DEBUG: PID: $$"
set parent_pid = `ps -o ppid= -p $$`
echo "DEBUG: PPID: $parent_pid"
echo "DEBUG: TTY: `tty`"
echo "DEBUG: Source directory: $source_dir"
echo "DEBUG: Tag: $tag"

# DEBUG: Check DISPLAY validity at startup
xset -q >& /dev/null
if ($status == 0) then
    echo "DEBUG: ✓ DISPLAY is VALID at session start"
else
    echo "DEBUG: ✗ WARNING: DISPLAY is INVALID at session start!"
endif

# DEBUG: Check parent process
echo "DEBUG: Parent process: $parent_pid"
echo "======================================================================"
echo ""

# Source LSF environment (TileBuilder-compatible, without cbwa_init.csh)
if (-f "$source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh") then
    source $source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh
endif

set target_log = "${refdir_name}/logs/${target_name}.log.gz"
echo ""
echo "Monitoring target completion: $target_log"

set target_done = 0
set target_elapsed = $prev_elapsed
set last_status = ""
set target_success = 0
set max_runtime_reached = 0
set failed_with_timing = 0

while ($target_done == 0)
    if ($target_elapsed % 300 == 0) then
        set status_check_log = "${refdir_name}/status_check_${tag}_${target_elapsed}.log"

        # DEBUG: Periodic DISPLAY check (every status check)
        if ($target_elapsed % 1800 == 0) then
            echo ""
            echo "DEBUG: Periodic health check at ${target_elapsed}s (`date`)"
            xset -q >& /dev/null
            if ($status == 0) then
                echo "DEBUG: ✓ DISPLAY still valid"
            else
                echo "DEBUG: ✗ WARNING: DISPLAY has become INVALID!"
                echo "DEBUG: This may cause issues with TileBuilderTerm calls"
            endif
        endif

        cd $refdir_name

        # DEBUG: Log TileBuilderTerm invocation
        echo "Running TileBuilderTerm -x TileBuilderShow to $status_check_log"

        TileBuilderTerm -x "TileBuilderShow >& $status_check_log"
        set tb_status = $status

        # DEBUG: Check if TileBuilderTerm succeeded
        if ($tb_status != 0) then
            echo "DEBUG: WARNING - TileBuilderTerm exited with status $tb_status"
        endif

        cd $source_dir

        sleep 5

        if (-f $status_check_log && -s $status_check_log) then
            set target_status = `grep "$target_name" $status_check_log | awk '{print $3}'`

            if ("$target_status" != "") then
                if ("$target_status" != "$last_status") then
                    echo "Target $target_name status: $target_status ($target_elapsed seconds)"
                    set last_status = "$target_status"
                endif

                if ("$target_status" == "FAILED") then
                    # Check timing report FIRST before printing any error
                    set timing_found = 0
                    if ("$target_name" == "FxSynthesize") then
                        foreach pass_num (1 2 3)
                            set qor_file = "${refdir_name}/rpts/${target_name}/${target_name}.pass_${pass_num}.proc_qor.rpt.gz"
                            if (-f "$qor_file") then
                                set timing_found = 1
                                break
                            endif
                        end
                        if (-f $target_log) then
                            set timing_found = 1
                        endif
                    endif

                    if ($timing_found == 1) then
                        echo ""
                        echo "======================================================================"
                        echo "WARNING: Target $target_name FAILED but timing report found"
                        echo "Target time: ${target_elapsed}s"
                        echo "======================================================================"
                        set target_done = 1
                        set target_success = 1
                        set failed_with_timing = 1
                    else
                        echo ""
                        echo "======================================================================"
                        echo "ERROR: Target $target_name FAILED"
                        echo "======================================================================"
                        echo "" >> $source_dir/data/${tag}_spec
                        echo "ERROR: Target $target_name failed at $refdir_name" >> $source_dir/data/${tag}_spec
                        set target_done = 1
                    endif
                    break
                endif

                if ("$target_status" == "WARNING" || "$target_status" == "PASSED") then
                    if (-f $target_log) then
                        echo ""
                        echo "======================================================================"
                        echo "SUCCESS: Target $target_name completed with status $target_status"
                        echo "Target time: ${target_elapsed}s"
                        echo "Completion file found: $target_log"
                        echo "======================================================================"
                        set target_done = 1
                        set target_success = 1
                        break
                    endif
                endif
            else
                echo "Target $target_name still running... ($target_elapsed seconds)"
            endif
        else
            echo "Target $target_name still running... ($target_elapsed seconds)"
        endif
    endif

    if ($target_elapsed % 300 == 0 && $target_elapsed > 0) then
        # Timing pass notifications only for FxSynthesize target on umccmd/umcdat tiles
        if ("$target_name" == "FxSynthesize") then
            set tile_prefix = `echo $tile_name | sed 's/_.*//g'`
            if ("$tile_prefix" == "umccmd" || "$tile_prefix" == "umcdat") then
                set timing_notify_track = "${refdir_name}/timing_pass_notified.tmp"
            if (! -f $timing_notify_track) then
                touch $timing_notify_track
            endif

            foreach pass_num (1 2 3)
                # Check for QoR report (FxSynthesize.pass_N.proc_qor.rpt.gz)
                set qor_file = "${refdir_name}/rpts/${target_name}/${target_name}.pass_${pass_num}.proc_qor.rpt.gz"

                if (-f "$qor_file") then
                    grep "pass_${pass_num}" $timing_notify_track >& /dev/null
                    if ($status != 0) then
                        echo "Found new QoR pass report: pass_${pass_num}"

                        set timing_notify_script = "$source_dir/script/rtg_oss_feint/supra/send_timing_pass_notification.csh"
                        if (-f $timing_notify_script) then
                            $timing_notify_script "$source_dir" "$tag" "$tile_name" "$refdir_name" "$target_name" "$pass_num" "$qor_file" >& /tmp/timing_pass${pass_num}_notify_${tag}_$$.log &
                            echo "pass_${pass_num}" >> $timing_notify_track
                        endif
                    endif
                endif
            end
            endif
        endif
    endif

    if ($target_elapsed % 1800 == 0 && $target_elapsed > 0) then
        echo "Cleaning up old status_check logs (older than 30 minutes)..."
        set cleanup_count = 0
        find ${refdir_name} -name "status_check_${tag}_*.log" -mmin +30 >& /tmp/old_logs_$$.tmp
        if (-f /tmp/old_logs_$$.tmp && -s /tmp/old_logs_$$.tmp) then
            foreach old_log (`cat /tmp/old_logs_$$.tmp`)
                if (-f "$old_log") then
                    rm -f "$old_log"
                    @ cleanup_count += 1
                endif
            end
        endif
        rm -f /tmp/old_logs_$$.tmp
        if ($cleanup_count > 0) then
            echo "Cleaned up $cleanup_count old status_check log files"
        endif
    endif

    # Check if maximum total runtime limit reached (graceful exit with email)
    if ($target_elapsed >= $max_total_runtime) then
        echo ""
        echo "======================================================================"
        echo "MAXIMUM RUNTIME LIMIT REACHED"
        echo "======================================================================"
        echo "Total elapsed time: ${target_elapsed}s (~`expr $target_elapsed / 3600` hours)"
        echo "Maximum allowed: ${max_total_runtime}s (~`expr $max_total_runtime / 3600` hours)"
        echo ""
        echo "Target $target_name is still running but monitoring must stop."
        echo "This is to prevent issues with xterm/DISPLAY expiration."
        echo "======================================================================"

        # Write status to spec file
        echo "" >> $source_dir/data/${tag}_spec
        echo "#text#" >> $source_dir/data/${tag}_spec
        echo "Monitor Runtime Limit Reached" >> $source_dir/data/${tag}_spec
        echo "========================================" >> $source_dir/data/${tag}_spec
        echo "" >> $source_dir/data/${tag}_spec
        echo "#table#" >> $source_dir/data/${tag}_spec
        echo "Tile,Run_Directory,Target,Status,Monitor_Time" >> $source_dir/data/${tag}_spec
        echo "$tile_name,$refdir_name,$target_name,STILL_RUNNING,${target_elapsed}s" >> $source_dir/data/${tag}_spec
        echo "#table end#" >> $source_dir/data/${tag}_spec

        # Exit monitoring loop and proceed to finish (will send email)
        set target_done = 1
        set max_runtime_reached = 1
        break
    endif

    sleep 30
    @ target_elapsed += 30
end

# Exit handling
if ($max_runtime_reached == 1) then
    # Max runtime limit reached - gracefully exit and send email
    echo ""
    echo "Finishing due to max runtime limit..."
    cd $source_dir
    set run_status = "finished"
    if (! $?tasksModelFile) then
        set tasksModelFile = "tasksModel.csv"
    endif
    if (! $?n_instruction) then
        set n_instruction = 0
    endif
    source csh/env.csh
    source csh/updateTask.csh
else
    # Normal completion logic
    if ($target_success == 1) then
        echo "" >> $source_dir/data/${tag}_spec
        if ($failed_with_timing == 1) then
            echo "Target $target_name FAILED but timing/area report is available" >> $source_dir/data/${tag}_spec
        else
            echo "Target $target_name completed successfully" >> $source_dir/data/${tag}_spec
        endif
        echo "" >> $source_dir/data/${tag}_spec
        echo "#table#" >> $source_dir/data/${tag}_spec
        echo "Tile,Run_Directory,Target,Status,Target_Time" >> $source_dir/data/${tag}_spec
        if ($failed_with_timing == 1) then
            echo "$tile_name,$refdir_name,$target_name,FAILED_WITH_TIMING,${target_elapsed}s" >> $source_dir/data/${tag}_spec
        else
            echo "$tile_name,$refdir_name,$target_name,COMPLETED,${target_elapsed}s" >> $source_dir/data/${tag}_spec
        endif
        echo "#table end#" >> $source_dir/data/${tag}_spec

        if ("$target_name" == "FxSynthesize") then
            echo ""
            echo "Extracting timing and area metrics..."
            set date = `date +%d-%b`
            perl $source_dir/script/rtg_oss_feint/supra/synthesis_timing_extract_details.pl $refdir_name "$date" >> $source_dir/data/${tag}_spec
            echo "Timing and area metrics extracted"
        endif

        cd $source_dir
        set run_status = "finished"
        if (! $?tasksModelFile) then
            set tasksModelFile = "tasksModel.csv"
        endif
        if (! $?n_instruction) then
            set n_instruction = 0
        endif
        source csh/env.csh
        source csh/updateTask.csh
    else
        echo "" >> $source_dir/data/${tag}_spec
        echo "ERROR: Target $target_name did not complete at $refdir_name" >> $source_dir/data/${tag}_spec
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
    endif
endif
