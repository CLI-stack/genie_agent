set tile = $1
set refDir = $2
set target = $3
set tag = $4
set source_dir = `pwd`
set target_run_dir = ":"
set reply = ""
touch $source_dir/data/${tag}_spec


set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}'`
set refdir_name = `echo $refDir | sed 's/:/ /g' | awk '{$1="";print $0}'`
set target_name = `echo $target | sed 's/:/ /g' | awk '{$1="";print $0}'`

# Validate tile_name is not empty
if ("$tile_name" == "" || "$tile_name" == " ") then
    echo "ERROR: tile_name is empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input tile: $tile" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
endif

# Validate refdir_name is not empty
if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "ERROR: refdir_name is empty or invalid" >> $source_dir/data/${tag}_spec
    echo "Input refDir: $refDir" >> $source_dir/data/${tag}_spec
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
endif

echo "Tile name: $tile_name"
echo "Reference directory: $refdir_name"

# Check if refdir_name ends with "tiles" or a specific tile directory
set basename_refdir = `basename $refdir_name`

# Flag to track if this is a new directory or existing
set is_new_directory = 0

if ("$basename_refdir" == "tiles") then
    echo "Reference directory ends with 'tiles' (tiles directory)"
    # User provided tiles directory - create new tile directory
    set tiles_dir = "$refdir_name"
    set is_new_directory = 1

    # Create new directory name with tile_name and unique timestamp (Malaysia time)
    setenv TZ 'Asia/Kuala_Lumpur'
    set date_stamp = `date +%b%d%H%M%S`
    unsetenv TZ
    set new_tile_dir = "${tiles_dir}/${tile_name}_${date_stamp}"

    # Check if directory already exists
    if (-d $new_tile_dir) then
        echo "WARNING: Directory already exists: $new_tile_dir"
        echo "Removing old directory..."
        rm -rf $new_tile_dir
        echo "Removed old directory"
    endif

    echo "Creating new tile directory: $new_tile_dir"
    mkdir -p $new_tile_dir

    if (! -d $new_tile_dir) then
        echo "ERROR: Failed to create directory: $new_tile_dir"
    endif

    set tile_dir = "$new_tile_dir"
    echo "Created: $tile_dir"
else
    echo "Reference directory ends with '$basename_refdir' (specific tile directory)"
    # User provided specific tile directory
    set tile_dir = "$refdir_name"
    set tiles_dir = `dirname $refdir_name`
    set is_new_directory = 0
endif

echo "Tiles directory: $tiles_dir"
echo "Tile directory: $tile_dir"

# Check if this is an existing ready directory (skip params creation if so)
set skip_params_creation = 0
set tilebuilder_make_log = "${tile_dir}/logs/TileBuilderMake.log.gz"

# First check if user provided new params/controls in this instruction
set tag_params = "${source_dir}/data/${tag}.params"
set tag_controls = "${source_dir}/data/${tag}.controls"
set has_new_params = 0

if (-f $tag_params) then
    set params_size = `wc -c < $tag_params`
    if ($params_size > 0) then
        set has_new_params = 1
        echo ""
        echo "======================================================================"
        echo "New params detected in instruction ($params_size bytes)"
        echo "======================================================================"
    endif
endif

if (-f $tag_controls) then
    set controls_size = `wc -c < $tag_controls`
    if ($controls_size > 0) then
        set has_new_params = 1
        echo ""
        echo "======================================================================"
        echo "New controls detected in instruction ($controls_size bytes)"
        echo "======================================================================"
    endif
endif

if (-d $tile_dir && -f $tilebuilder_make_log) then
    echo ""
    echo "======================================================================"
    echo "Checking if existing directory is ready..."
    echo "======================================================================"
    echo "TileBuilderMake log found: $tilebuilder_make_log"

    # Check if TileBuilderMake completed successfully
    set make_done = `zcat $tilebuilder_make_log | grep -c "TileBuilderMake:INFO: Done" || echo 0`

    if ($make_done > 0) then
        echo "✓ TileBuilderMake:INFO: Done found in log"

        # Only enable fast mode if NO new params were given
        if ($has_new_params == 1) then
            echo "✗ New params/controls detected - NORMAL MODE (need to update params)"
            set skip_params_creation = 0
        else
            echo "✓ No new params detected - FAST MODE enabled"
            set skip_params_creation = 1
        endif
    else
        echo "✗ TileBuilderMake:INFO: Done NOT found in log"
        echo "  Directory exists but not ready - will proceed with params creation"
    endif
else
    if (! -d $tile_dir) then
        echo "Tile directory does not exist yet - will create and setup"
    else if (! -f $tilebuilder_make_log) then
        echo "TileBuilderMake log not found - will proceed with params creation"
    endif
endif

# Extract params_centre path from assignment.csv
set assignment_file = "${source_dir}/assignment.csv"

if (! -f $assignment_file) then
    echo "ERROR: assignment.csv not found: $assignment_file"
endif

set params_centre = `grep "^params," $assignment_file | awk -F',' '{print $2}'`

if ("$params_centre" == "") then
    echo "ERROR: params_centre not found in assignment.csv"
endif

echo "Params centre: $params_centre"

# Skip params creation if directory is already ready
if ($skip_params_creation == 1) then
    echo ""
    echo "======================================================================"
    echo "SKIPPING PARAMS CREATION - Directory already ready"
    echo "======================================================================"
    goto skip_to_gui_check
endif

# Copy override files from params_centre to tile_dir
if ("$params_centre" != "" && "$tile_dir" != "") then
    set params_tile_dir = "${params_centre}/${tile_name}"
    
    if (-d $params_tile_dir) then
        echo "Copying params from: $params_tile_dir"
        
        # Copy override.params if exists
        if (-f "${params_tile_dir}/override.params") then
            cp "${params_tile_dir}/override.params" "$tile_dir/"
            echo "  Copied: override.params"
            
            # Check if tag.params has NICKNAME (will be merged later)
            set tag_has_nickname = 0
            if (-f "${source_dir}/data/${tag}.params") then
                grep -q "^NICKNAME\s*=" "${source_dir}/data/${tag}.params"
                if ($status == 0) then
                    set tag_has_nickname = 1
                    echo "  Tag params has NICKNAME - will use that value"
                endif
            endif
            
            # Update or add NICKNAME only if tag.params doesn't have it
            if ($tag_has_nickname == 0) then
                set new_nickname = `basename $tile_dir`
                
                # Check if NICKNAME exists in override.params
                grep -q "^NICKNAME\s*=" "${tile_dir}/override.params"
                if ($status == 0) then
                    # NICKNAME exists - replace it
                    sed -i "s/^NICKNAME\s*=.*/NICKNAME = $new_nickname/" "${tile_dir}/override.params"
                    echo "  Updated NICKNAME to: $new_nickname"
                else
                    # NICKNAME doesn't exist - add it at the top
                    set temp_file = "${tile_dir}/override.params.tmp"
                    echo "NICKNAME = $new_nickname" > "$temp_file"
                    cat "${tile_dir}/override.params" >> "$temp_file"
                    mv "$temp_file" "${tile_dir}/override.params"
                    echo "  Added NICKNAME at top: $new_nickname"
                endif
            endif
        else
            echo "  WARNING: override.params not found"
        endif
        
        # Copy override.controls if exists
        if (-f "${params_tile_dir}/override.controls") then
            cp "${params_tile_dir}/override.controls" "$tile_dir/"
            echo "  Copied: override.controls"
        else
            echo "  WARNING: override.controls not found"
        endif
    else
        echo "WARNING: Params directory not found: $params_tile_dir"
    endif
endif

# Check if tag-specific params and controls files have content
set tag_params = "${source_dir}/data/${tag}.params"
set tag_controls = "${source_dir}/data/${tag}.controls"

echo "Checking tag-specific files..."

# Check params file
if (-f $tag_params) then
    set params_size = `wc -c < $tag_params`
    if ($params_size > 0) then
        echo "Tag params file has content: $tag_params ($params_size bytes)"
        set has_tag_params = 1
    else
        echo "Tag params file is empty: $tag_params"
        set has_tag_params = 0
    endif
else
    echo "Tag params file not found: $tag_params"
    set has_tag_params = 0
endif

# Check controls file
if (-f $tag_controls) then
    set controls_size = `wc -c < $tag_controls`
    if ($controls_size > 0) then
        echo "Tag controls file has content: $tag_controls ($controls_size bytes)"
        set has_tag_controls = 1
    else
        echo "Tag controls file is empty: $tag_controls"
        set has_tag_controls = 0
    endif
else
    echo "Tag controls file not found: $tag_controls"
    set has_tag_controls = 0
endif

# Merge tag-specific params into override.params if content exists
if ($has_tag_params == 1 && -f "${tile_dir}/override.params") then
    echo "Merging tag params into override.params..."
    
    # Create temporary merged file - remove all comment lines first
    set temp_params = "${tile_dir}/override.params.tmp"
    grep -v "^\s*#" "${tile_dir}/override.params" > "$temp_params"
    echo "  Removed comment lines from override.params"
    
    # Read each line from tag params and update/append
    foreach line ("`cat $tag_params`")
        # Skip comment lines
        if (`echo "$line" | grep -q "^\s*#"`) then
            continue
        endif
        
        set param_name = `echo "$line" | awk -F'=' '{print $1}' | sed 's/ //g'`
        
        if ("$param_name" != "") then
            # Check if parameter exists in override.params
            grep -q "^${param_name}\s*=" "$temp_params"
            if ($status == 0) then
                # Replace existing line (first occurrence)
                sed -i "/^${param_name}\s*=/c\\
$line" "$temp_params"
                echo "  Replaced: $param_name"
            else
                # Append new line
                echo "$line" >> "$temp_params"
                echo "  Added: $param_name"
            endif
        endif
    end
    
    # Replace original with merged
    mv "$temp_params" "${tile_dir}/override.params"
    echo "Merged params complete"
endif

# Merge tag-specific controls into override.controls if content exists
if ($has_tag_controls == 1 && -f "${tile_dir}/override.controls") then
    echo "Merging tag controls into override.controls..."
    
    # Create temporary merged file - remove all comment lines first
    set temp_controls = "${tile_dir}/override.controls.tmp"
    grep -v "^\s*#" "${tile_dir}/override.controls" > "$temp_controls"
    echo "  Removed comment lines from override.controls"
    
    # Read each line from tag controls and update/append
    foreach line ("`cat $tag_controls`")
        # Skip comment lines
        if (`echo "$line" | grep -q "^\s*#"`) then
            continue
        endif
        
        set param_name = `echo "$line" | awk -F'=' '{print $1}' | sed 's/ //g'`
        
        if ("$param_name" != "") then
            # Check if parameter exists in override.controls
            grep -q "^${param_name}\s*=" "$temp_controls"
            if ($status == 0) then
                # Replace existing line (first occurrence)
                sed -i "/^${param_name}\s*=/c\\
$line" "$temp_controls"
                echo "  Replaced: $param_name"
            else
                # Append new line
                echo "$line" >> "$temp_controls"
                echo "  Added: $param_name"
            endif
        endif
    end
    
    # Replace original with merged
    mv "$temp_controls" "${tile_dir}/override.controls"
    echo "Merged controls complete"
endif

# Label for skipping params creation
skip_to_gui_check:

# Check for existing TileBuilder GUI directory (ends with _GUI)
echo "Checking for existing TileBuilder GUI directory..."

set gui_dir = `find $tiles_dir -maxdepth 1 -type d -name "*_GUI" | head -1`

if ("$gui_dir" != "") then
    echo "Found GUI directory: $gui_dir"
    
    # Check if revrc.main exists
    if (-f "${gui_dir}/revrc.main") then
        echo "  revrc.main exists in GUI directory"
        set has_revrc = 1
        set revrc_dir = "$gui_dir"
    else
        echo "  WARNING: revrc.main not found in GUI directory"
        set has_revrc = 0
        set revrc_dir = ""
    endif
else
    echo "No GUI directory found in $tiles_dir"
    set has_revrc = 0
    set revrc_dir = ""
endif

# Finalize - set final_tile_dir for use in subsequent operations
set final_tile_dir = "$tile_dir"
set final_tile_name = `basename $final_tile_dir`

echo ""
echo "======================================================================"
echo "Setup Complete"
echo "======================================================================"
echo "Final tile directory: $final_tile_dir"
if ($has_revrc == 1) then
    echo "TileBuilder GUI directory: $revrc_dir"
endif
echo "======================================================================"

# Run TileBuilder commands if GUI directory exists
if ($has_revrc == 1) then
    echo ""
    echo "Launching TileBuilder commands..."

    cd $revrc_dir
    # Use TileBuilder-compatible LSF (without cbwa_init.csh which conflicts)
    source $source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh
    echo "Sourcing lsf environment"
    # Create log file for TileBuilder output
    set tb_log = "${final_tile_dir}/tilebuilder_run_${tag}.log"

    # Build TileBuilder command based on directory type and params
    # - New directory: just run (no reset needed)
    # - Existing directory: reset all tasks first, then run

    if ($skip_params_creation == 1) then
        # FAST MODE: Directory already ready, no new params - just reset and run target
        echo ""
        echo "======================================================================"
        echo "FAST MODE: Skipping TileBuilderGenParams and TileBuilderMake"
        echo "Existing directory - will reset tasks before running"
        echo "======================================================================"
        set tb_cmd = "cd $final_tile_dir;serascmd -find_jobs 'dir=~$final_tile_name' --action reset;serascmd -find_jobs 'name=~$target_name dir=~$final_tile_name' --action run >& $tb_log"
    else if ($is_new_directory == 1) then
        # NORMAL MODE - New directory: GenParams, Make, then run (no reset needed)
        echo ""
        echo "======================================================================"
        echo "NORMAL MODE: New directory - full setup"
        echo "======================================================================"
        set tb_cmd = "cd $final_tile_dir;TileBuilderGenParams;TileBuilderMake;serascmd -find_jobs 'name=~$target_name dir=~$final_tile_name' --action run >& $tb_log"
    else
        # NORMAL MODE - Existing directory with new params: GenParams, Make, reset, then run
        echo ""
        echo "======================================================================"
        echo "NORMAL MODE: Existing directory with new params"
        echo "Will reset tasks before running"
        echo "======================================================================"
        set tb_cmd = "cd $final_tile_dir;TileBuilderGenParams;TileBuilderMake;serascmd -find_jobs 'dir=~$final_tile_name' --action reset;serascmd -find_jobs 'name=~$target_name dir=~$final_tile_name' --action run >& $tb_log"
    endif

    echo "Command: $tb_cmd"
    echo "Executing from: $revrc_dir"
    echo "Log file: $tb_log"

    # Launch TileBuilderTerm
    TileBuilderTerm -x "$tb_cmd" &

    echo "TileBuilder commands launched"
    echo "Output logged to: $tb_log"

    # Wait for completion in phases
    echo ""
    echo "Waiting for run to complete..."

    # Phase 1: Wait for UpdateTunable.log.gz (indicates setup complete)
    # Skip this phase if we already have a ready directory
    set setup_file = "${final_tile_dir}/logs/UpdateTunable.log.gz"
    set setup_done = 0
    set setup_elapsed = 0

    if ($skip_params_creation == 1) then
        echo "Phase 1: SKIPPED (directory already ready)"
        set setup_done = 1
    else
        echo "Phase 1: Monitoring setup completion: $setup_file"
    endif

    while ($setup_done == 0)
        if (-f $setup_file) then
            echo "✓ Setup completed - UpdateTunable.log.gz found (${setup_elapsed}s)"
            set setup_done = 1
            break
        endif

        sleep 30
        @ setup_elapsed += 30
        
        if ($setup_elapsed % 300 == 0) then
            echo "Waiting for setup... ($setup_elapsed seconds)"
        endif
        
        if ($setup_elapsed >= 1200) then
            echo "ERROR: Setup timeout after 20 minutes"
            echo "ERROR: Setup failed - UpdateTunable.log.gz not found" >> $source_dir/data/${tag}_spec
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        endif
    end
    
    # Phase 2: Copy tune files from tune centre (after setup completes)
    # Skip this phase if we're in fast mode (tune files should already exist)
    echo ""
    if ($skip_params_creation == 1) then
        echo "Phase 2: SKIPPED (directory already ready - tune files should exist)"
    else
        echo "Phase 2: Copying tune files from tune centre..."

        # Get tune centre path from assignment.csv
        set tune_centre = `grep "^tune," $assignment_file | awk -F',' '{print $2}'`

        if ("$tune_centre" != "") then
            # Source: tune_centre/tile/target
            set tune_source_dir = "${tune_centre}/${tile_name}/${target_name}"
            # Destination: final_tile_dir/tune/target
            set dest_tune_dir = "${final_tile_dir}/tune/${target_name}"

            if (-d $tune_source_dir) then
                echo "Tune centre directory: $tune_source_dir"
                echo "Destination: $dest_tune_dir"

                # Create destination tune/target directory if it doesn't exist
                if (! -d $dest_tune_dir) then
                    mkdir -p $dest_tune_dir
                    echo "Created destination tune directory: $dest_tune_dir"
                endif

                # Copy all tune files from target directory (force overwrite)
                cp -rf ${tune_source_dir}/* $dest_tune_dir/

                if ($status == 0) then
                    echo "✓ Successfully copied tune files"
                else
                    echo "WARNING: Failed to copy tune files"
                endif
            else
                echo "WARNING: Tune source directory not found: $tune_source_dir"
            endif
        else
            echo "WARNING: tune_centre not found in assignment.csv"
        endif
    endif

    # Phase 3: Monitor until target is RUNNING
    echo ""
    echo "Phase 3: Monitoring until target is RUNNING"
    echo "Target: $target_name"

    set target_done = 0
    set target_elapsed = 0
    set last_status = ""
    set target_success = 0

    while ($target_done == 0)

        # Check target status every 5 minutes
        if ($target_elapsed % 300 == 0) then
            # Generate full status check log (no filtering)
            set status_check_log = "${final_tile_dir}/status_check_${target_elapsed}.log"
            
            cd $revrc_dir
            TileBuilderTerm -x "cd $final_tile_dir; TileBuilderShow >& $status_check_log"
            cd $source_dir
            
            # Wait for status log to be created
            sleep 5
            
            if (-f $status_check_log && -s $status_check_log) then
                # Check target status from full log
                set target_status = `grep "$target_name" $status_check_log | awk '{print $3}'`
                
                if ("$target_status" != "") then
                    if ("$target_status" != "$last_status") then
                        echo "Target $target_name status: $target_status ($target_elapsed seconds)"
                        set last_status = "$target_status"
                    endif

                    # Check if target is RUNNING - success condition
                    if ("$target_status" == "RUNNING") then
                        echo ""
                        echo "======================================================================"
                        echo "SUCCESS: Target $target_name is now RUNNING"
                        echo "Setup time: ${setup_elapsed}s"
                        echo "Time to RUNNING: ${target_elapsed}s"
                        set total_time = `expr $setup_elapsed + $target_elapsed`
                        echo "Total time: ${total_time}s"
                        echo "Run directory: $final_tile_dir"
                        echo "======================================================================"
                        set target_done = 1
                        set target_success = 1
                        break
                    endif

                    # Check if target failed OR became NOTRUN (dependency failure)
                    if ("$target_status" == "FAILED" || "$target_status" == "NOTRUN") then
                        echo ""
                        echo "======================================================================"
                        if ("$target_status" == "NOTRUN") then
                            echo "ERROR: Target $target_name set to NOTRUN (dependency failure)"
                        else
                            echo "ERROR: Target $target_name FAILED"
                        endif
                        echo "======================================================================"
                        
                        # Get all FAILED tasks and write to temp file
                        echo "DEBUG: Extracting FAILED tasks from status log"
                        grep FAILED $status_check_log > ${final_tile_dir}/failed_tasks.tmp
                        echo "DEBUG: Failed tasks file: ${final_tile_dir}/failed_tasks.tmp"
                        if (-f ${final_tile_dir}/failed_tasks.tmp) then
                            echo "DEBUG: Failed tasks content:"
                            cat ${final_tile_dir}/failed_tasks.tmp
                        else
                            echo "DEBUG: No failed tasks file created"
                        endif

                        # Check waiver file
                        set waiver_file = "${source_dir}/script/rtg_oss_feint/supra/supra_task_skip.txt"
                        set can_skip = 0

                        echo "DEBUG: Waiver file path: $waiver_file"
                        echo "DEBUG: Waiver file exists: "`test -f $waiver_file && echo "YES" || echo "NO"`
                        echo "DEBUG: Failed tasks file exists: "`test -f ${final_tile_dir}/failed_tasks.tmp && echo "YES" || echo "NO"`

                        if (-f $waiver_file && -f ${final_tile_dir}/failed_tasks.tmp) then
                            echo "DEBUG: Entering waiver processing block"
                            echo "Checking waiver file: $waiver_file"
                            echo "DEBUG: Waiver file content:"
                            cat $waiver_file
                            echo "DEBUG: Cat command completed"

                            # Clean waiver file (remove comments)
                            echo "DEBUG: Creating clean waiver list"
                            echo "DEBUG: Running grep command..."
                            # Simple grep to remove comment lines
                            grep -v "^#" $waiver_file > ${final_tile_dir}/waiver_clean.tmp
                            set grep_status = $status
                            if ($grep_status != 0) then
                                echo "DEBUG: grep command failed (status: $grep_status), creating empty file"
                                touch ${final_tile_dir}/waiver_clean.tmp
                            else
                                echo "DEBUG: grep command successful"
                            endif
                            echo "DEBUG: Clean waiver list created"
                            if (-f ${final_tile_dir}/waiver_clean.tmp) then
                                echo "DEBUG: Clean waiver list content:"
                                cat ${final_tile_dir}/waiver_clean.tmp
                            else
                                echo "DEBUG: ERROR - waiver_clean.tmp was not created"
                            endif

                            # Extract just task names (column 2)
                            echo "DEBUG: Extracting task names from failed tasks"
                            awk '{print $2}' ${final_tile_dir}/failed_tasks.tmp > ${final_tile_dir}/failed_task_names.tmp
                            set awk_status = $status
                            if ($awk_status != 0) then
                                echo "DEBUG: awk command failed to extract task names (status: $awk_status)"
                                touch ${final_tile_dir}/failed_task_names.tmp
                            else
                                echo "DEBUG: awk extraction successful"
                            endif
                            echo "DEBUG: Failed task names:"
                            cat ${final_tile_dir}/failed_task_names.tmp

                            # Check if all failed tasks are in waiver file and extract root cause patterns
                            echo "DEBUG: Checking each failed task against waiver list"
                            set all_waived = 1

                            # Create a file to store task -> pattern mapping
                            set WAIVER_PATTERNS = "${final_tile_dir}/waiver_patterns.tmp"
                            rm -f $WAIVER_PATTERNS

                            foreach task_name (`cat ${final_tile_dir}/failed_task_names.tmp`)
                                echo "DEBUG: Checking task: $task_name"
                                # Check if task exists in waiver file
                                grep "^${task_name}:" ${final_tile_dir}/waiver_clean.tmp > /tmp/waiver_line_$$.tmp
                                if ($status != 0) then
                                    echo "Task $task_name is NOT waived"
                                    set all_waived = 0
                                    break
                                else
                                    echo "Task $task_name is waived"
                                    # Extract the root cause pattern (everything after the colon) to a file
                                    sed 's/^[^:]*: *//' /tmp/waiver_line_$$.tmp > /tmp/pattern_$$.tmp
                                    echo -n "  Expected root cause pattern: "
                                    cat /tmp/pattern_$$.tmp
                                    # Store task name and pattern for later use - read pattern from file to avoid glob expansion
                                    echo -n "${task_name}:::" >> $WAIVER_PATTERNS
                                    cat /tmp/pattern_$$.tmp >> $WAIVER_PATTERNS
                                    rm -f /tmp/waiver_line_$$.tmp /tmp/pattern_$$.tmp
                                endif
                            end

                            echo "DEBUG: all_waived = $all_waived"
                            if ($all_waived == 1) then
                                # Check for universal blocking patterns before allowing skip
                                echo ""
                                echo "======================================================================"
                                echo "All failed tasks are in waiver file - checking for blocking patterns..."
                                echo "======================================================================"

                                set blocking_check_passed = 1

                                foreach task_name (`cat ${final_tile_dir}/failed_task_names.tmp`)
                                    echo ""
                                    echo "Checking blocking patterns for task: $task_name"

                                    # Get the expected root cause pattern for this task (store in file to avoid glob expansion)
                                    set EXPECTED_PATTERN_FILE = "/tmp/expected_pattern_$$.tmp"
                                    grep "^${task_name}:::" $WAIVER_PATTERNS | sed 's/^[^:]*::://' > $EXPECTED_PATTERN_FILE
                                    echo -n "  Expected root cause pattern: "
                                    cat $EXPECTED_PATTERN_FILE

                                    set task_log = "${final_tile_dir}/logs/${task_name}.log.gz"

                                    if (! -f $task_log) then
                                        echo "  WARNING: Log file not found: $task_log"
                                        echo "  Cannot verify blocking patterns - denying skip for safety"
                                        set blocking_check_passed = 0
                                        break
                                    endif

                                    echo "  Log file: $task_log"

                                    # Check for universal blocking patterns that prevent skipping
                                    echo "  Checking for universal blocking patterns..."
                                    # Use separate grep commands to avoid regex issues and track which patterns match
                                    set TMPFILE = "/tmp/blocking_check_$$.tmp"
                                    set PATTERNS_MATCHED = "/tmp/blocking_patterns_matched_$$.tmp"
                                    rm -f $TMPFILE $PATTERNS_MATCHED

                                    # Check each pattern and log which ones match
                                    zcat "$task_log" | grep -E "ERROR: Output dependency file.*was not created" > /tmp/check1_$$.tmp
                                    if (-s /tmp/check1_$$.tmp) then
                                        cat /tmp/check1_$$.tmp >> $TMPFILE
                                        echo "Missing output dependency files" >> $PATTERNS_MATCHED
                                    endif

                                    zcat "$task_log" | grep -E "license error|License checkout failed" > /tmp/check2_$$.tmp
                                    if (-s /tmp/check2_$$.tmp) then
                                        cat /tmp/check2_$$.tmp >> $TMPFILE
                                        echo "License checkout failure" >> $PATTERNS_MATCHED
                                    endif

                                    zcat "$task_log" | grep -E "segmentation fault|Segmentation fault|core dumped" > /tmp/check3_$$.tmp
                                    if (-s /tmp/check3_$$.tmp) then
                                        cat /tmp/check3_$$.tmp >> $TMPFILE
                                        echo "Segmentation fault / core dump" >> $PATTERNS_MATCHED
                                    endif

                                    zcat "$task_log" | grep -E "killed by signal|Fatal Error|FATAL:|Abort|ABORT" > /tmp/check4_$$.tmp
                                    if (-s /tmp/check4_$$.tmp) then
                                        cat /tmp/check4_$$.tmp >> $TMPFILE
                                        echo "Fatal error / process killed" >> $PATTERNS_MATCHED
                                    endif

                                    rm -f /tmp/check1_$$.tmp /tmp/check2_$$.tmp /tmp/check3_$$.tmp /tmp/check4_$$.tmp

                                    if (-s $TMPFILE) then
                                        echo "  ✗ BLOCKING PATTERN FOUND - Cannot skip this failure!"
                                        echo "  Matched blocking patterns:"
                                        cat $PATTERNS_MATCHED | sed 's/^/    - /'
                                        echo ""
                                        echo "  Example errors from log:"
                                        head -3 $TMPFILE | sed 's/^/    /'

                                        # Extract root cause errors (all errors EXCEPT blocking patterns)
                                        echo "  Extracting root cause errors from log..."
                                        set ALLERRS = "/tmp/all_errors_$$.tmp"
                                        set ROOTCAUSE = "/tmp/root_cause_errors_$$.tmp"

                                        zcat "$task_log" | grep -E "^ERROR:|ERROR:" >& $ALLERRS

                                        # Filter out blocking pattern errors to get root cause
                                        grep -v -E 'ERROR: Output dependency file.*was not created|ERROR: Hit an error while trying to execute|ERROR: got an exit code not null|ERROR:[ ]*$' $ALLERRS >& $ROOTCAUSE

                                        if (-s $ROOTCAUSE) then
                                            echo "  Root cause errors found:"
                                            head -5 $ROOTCAUSE | sed 's/^/    /'

                                            # Clean and deduplicate root cause errors
                                            set ROOTCAUSE_CLEAN = "/tmp/root_cause_clean_$$.tmp"
                                            set ROOTCAUSE_UNIQUE = "/tmp/root_cause_unique_$$.tmp"

                                            # First clean all errors (remove prefixes and extra spaces)
                                            sed 's/^echo ERROR: //' $ROOTCAUSE | sed 's/^ERROR: //' | sed 's/  */ /g' > $ROOTCAUSE_CLEAN

                                            # Then deduplicate
                                            sort -u $ROOTCAUSE_CLEAN > $ROOTCAUSE_UNIQUE

                                            # Check if ALL root causes match the expected pattern (strict matching)
                                            echo "  Checking if ALL root causes match expected pattern..."
                                            set expected_pattern_text = `cat $EXPECTED_PATTERN_FILE`

                                            # Count total root causes
                                            set total_root_causes = `wc -l < $ROOTCAUSE_UNIQUE`

                                            # Count how many match the pattern
                                            grep -E "$expected_pattern_text" $ROOTCAUSE_UNIQUE > /tmp/matched_root_causes_$$.tmp
                                            set matched_count = `wc -l < /tmp/matched_root_causes_$$.tmp`

                                            echo "  Total root causes: $total_root_causes"
                                            echo "  Matched pattern: $matched_count"

                                            if ($total_root_causes == $matched_count && $matched_count > 0) then
                                                echo "  ✓ ALL ROOT CAUSES MATCH EXPECTED PATTERN - Safe to skip!"
                                                echo -n "  Pattern: "
                                                cat $EXPECTED_PATTERN_FILE
                                                # This task can be skipped - continue to next task
                                                rm -f $TMPFILE $ALLERRS $ROOTCAUSE $ROOTCAUSE_CLEAN $ROOTCAUSE_UNIQUE $PATTERNS_MATCHED $EXPECTED_PATTERN_FILE /tmp/matched_root_causes_$$.tmp
                                                continue
                                            else
                                                echo "  ✗ NOT ALL ROOT CAUSES MATCH EXPECTED PATTERN - Cannot skip!"
                                                echo -n "  Expected pattern: "
                                                cat $EXPECTED_PATTERN_FILE
                                                echo "  This is an unexpected failure"
                                                echo ""
                                                echo "  Root causes that DID NOT match:"
                                                grep -v -E "$expected_pattern_text" $ROOTCAUSE_UNIQUE | sed 's/^/    /'
                                                rm -f /tmp/matched_root_causes_$$.tmp
                                            endif

                                            # Report root cause to spec file in CSV format
                                            echo "" >> $source_dir/data/${tag}_spec
                                            echo "Task $task_name cannot be skipped due to:" >> $source_dir/data/${tag}_spec
                                            echo -n "  Expected pattern: " >> $source_dir/data/${tag}_spec
                                            cat $EXPECTED_PATTERN_FILE >> $source_dir/data/${tag}_spec
                                            echo "  Actual root cause does NOT match expected pattern" >> $source_dir/data/${tag}_spec
                                            echo "" >> $source_dir/data/${tag}_spec

                                            # List the blocking patterns that matched
                                            foreach pattern_match ("`cat $PATTERNS_MATCHED`")
                                                echo "  - $pattern_match" >> $source_dir/data/${tag}_spec
                                            end

                                            echo "" >> $source_dir/data/${tag}_spec
                                            echo "Root cause errors:" >> $source_dir/data/${tag}_spec
                                            echo "#table#" >> $source_dir/data/${tag}_spec
                                            echo "Error_Type,Error_Message" >> $source_dir/data/${tag}_spec

                                            # Format each root cause error as CSV with quotes
                                            set line_num = 0
                                            foreach error_line ("`cat $ROOTCAUSE_UNIQUE`")
                                                @ line_num += 1
                                                if ($line_num > 10) break

                                                # Add quotes around the error message
                                                echo "Root Cause,'$error_line'" >> $source_dir/data/${tag}_spec
                                            end

                                            echo "#table end#" >> $source_dir/data/${tag}_spec
                                            rm -f $ROOTCAUSE_CLEAN $ROOTCAUSE_UNIQUE
                                        else
                                            echo "  WARNING: No root cause errors extracted - only blocking patterns found"
                                            echo "  ✗ Cannot verify expected pattern match - Cannot skip!"
                                            echo -n "  Expected pattern: "
                                            cat $EXPECTED_PATTERN_FILE

                                            # Report blocking patterns if no root cause found
                                            echo "" >> $source_dir/data/${tag}_spec
                                            echo "Task $task_name cannot be skipped due to:" >> $source_dir/data/${tag}_spec
                                            echo -n "  Expected pattern: " >> $source_dir/data/${tag}_spec
                                            cat $EXPECTED_PATTERN_FILE >> $source_dir/data/${tag}_spec
                                            echo "  No root cause found to verify pattern match" >> $source_dir/data/${tag}_spec
                                            echo "" >> $source_dir/data/${tag}_spec

                                            # List the blocking patterns that matched
                                            foreach pattern_match ("`cat $PATTERNS_MATCHED`")
                                                echo "  - $pattern_match" >> $source_dir/data/${tag}_spec
                                            end

                                            # Clean and deduplicate blocking pattern errors
                                            set TMPFILE_CLEAN = "/tmp/blocking_clean_$$.tmp"
                                            set TMPFILE_UNIQUE = "/tmp/blocking_unique_$$.tmp"

                                            # First clean all errors (remove ERROR: prefix and extra spaces)
                                            sed 's/^ERROR: //' $TMPFILE | sed 's/  */ /g' > $TMPFILE_CLEAN

                                            # Then deduplicate
                                            sort -u $TMPFILE_CLEAN > $TMPFILE_UNIQUE

                                            echo "" >> $source_dir/data/${tag}_spec
                                            echo "Blocking pattern errors:" >> $source_dir/data/${tag}_spec
                                            echo "#table#" >> $source_dir/data/${tag}_spec
                                            echo "Error_Type,Error_Message" >> $source_dir/data/${tag}_spec

                                            # Format blocking patterns as CSV with quotes
                                            set line_num = 0
                                            foreach error_line ("`cat $TMPFILE_UNIQUE`")
                                                @ line_num += 1
                                                if ($line_num > 10) break

                                                # Add quotes around the error message
                                                echo "Blocking Pattern,'$error_line'" >> $source_dir/data/${tag}_spec
                                            end

                                            echo "#table end#" >> $source_dir/data/${tag}_spec
                                            rm -f $TMPFILE_CLEAN $TMPFILE_UNIQUE
                                        endif

                                        set blocking_check_passed = 0
                                        rm -f $TMPFILE $ALLERRS $ROOTCAUSE $PATTERNS_MATCHED
                                        break
                                    else
                                        echo "  ✓ No universal blocking patterns found"
                                        rm -f $TMPFILE
                                    endif
                                end

                                echo ""
                                echo "======================================================================"
                                if ($blocking_check_passed == 1) then
                                    echo "✓ Blocking pattern check PASSED - Safe to skip waived tasks"
                                    set can_skip = 1
                                    echo "DEBUG: Setting can_skip = 1"
                                else
                                    echo "✗ Blocking pattern check FAILED - Cannot skip (fundamental failure detected)"
                                    set can_skip = 0
                                    echo "DEBUG: can_skip remains 0"
                                endif
                                echo "======================================================================"
                            else
                                echo "DEBUG: can_skip remains 0"
                            endif

                            rm -f ${final_tile_dir}/failed_task_names.tmp ${final_tile_dir}/waiver_clean.tmp
                        else
                            echo "DEBUG: Skipping waiver check - condition not met"
                        endif

                        echo "DEBUG: Final can_skip value = $can_skip"

                        # If can skip, skip the failed tasks and re-run target
                        if ($can_skip == 1) then
                            echo "======================================================================"
                            echo "All failed tasks are waived - skipping and re-running target"
                            echo "======================================================================"

                            # Extract task names and skip each one
                            echo "DEBUG: Extracting task names for skipping"
                            awk '{print $2}' ${final_tile_dir}/failed_tasks.tmp > ${final_tile_dir}/skip_tasks.tmp
                            set awk_status = $status
                            if ($awk_status != 0) then
                                echo "DEBUG: awk command failed (status: $awk_status)"
                                touch ${final_tile_dir}/skip_tasks.tmp
                            endif
                            echo "DEBUG: Tasks to skip:"
                            cat ${final_tile_dir}/skip_tasks.tmp

                            # Send skip notification email in background (don't block)
                            echo "DEBUG: Sending skip notification email in background..."
                            set skip_notify_script = "$source_dir/script/rtg_oss_feint/supra/send_skip_notification.csh"
                            if (-f $skip_notify_script) then
                                # Copy skip_tasks.tmp to a temp file for email script (will be cleaned up by email script)
                                cp ${final_tile_dir}/skip_tasks.tmp /tmp/skip_tasks_${tag}_$$.tmp
                                $skip_notify_script "$source_dir" "$tag" "$tile_name" "$final_tile_dir" "/tmp/skip_tasks_${tag}_$$.tmp" "$target_name" >& /tmp/skip_notify_${tag}_$$.log &
                                echo "DEBUG: Skip notification started in background (PID: $!)"
                            else
                                echo "DEBUG: Skip notification script not found: $skip_notify_script"
                            endif

                            echo "DEBUG: Starting to skip tasks..."
                            foreach task_name (`cat ${final_tile_dir}/skip_tasks.tmp`)
                                echo "Skipping task: $task_name"
                                echo "DEBUG: Changing to revrc_dir: $revrc_dir"
                                cd $revrc_dir
                                echo "DEBUG: Running TileBuilderTerm skip command"
                                TileBuilderTerm -x "cd $final_tile_dir; serascmd -find_jobs 'name=~${task_name} dir=~${final_tile_name}' --action skip"
                                echo "DEBUG: Skip command completed for $task_name"
                                cd $source_dir
                                sleep 10
                            end
                            echo "DEBUG: All tasks skipped"

                            # Re-run the target
                            echo "======================================================================"
                            echo "Re-running target: $target_name"
                            echo "======================================================================"
                            echo "DEBUG: Changing to revrc_dir: $revrc_dir"
                            cd $revrc_dir
                            echo "DEBUG: Running TileBuilderTerm run command for $target_name"
                            TileBuilderTerm -x "cd $final_tile_dir; serascmd -find_jobs 'name=~${target_name} dir=~${final_tile_name}' --action run"
                            echo "DEBUG: Run command completed"
                            cd $source_dir

                            rm -f ${final_tile_dir}/failed_tasks.tmp ${final_tile_dir}/skip_tasks.tmp
                            echo "Continuing to monitor..."
                            # Don't break - continue monitoring
                        else
                            # Failed and not waived - report and exit
                            echo "======================================================================"
                            echo "DEBUG: Tasks are NOT waived - reporting failure and exiting"
                            echo "======================================================================"
                            echo "" >> $source_dir/data/${tag}_spec
                            echo "#text#" >> $source_dir/data/${tag}_spec
                            echo "Failed tasks:" >> $source_dir/data/${tag}_spec
                            echo "#table#" >> $source_dir/data/${tag}_spec
                            echo "TaskID,Target,Status,Logfile" >> $source_dir/data/${tag}_spec

                            # Report all failed tasks using awk (just paths, no log content)
                            if (-f ${final_tile_dir}/failed_tasks.tmp && -s ${final_tile_dir}/failed_tasks.tmp) then
                                echo "DEBUG: Reporting failed tasks to spec file"
                                awk -v logdir="${final_tile_dir}" '{print $1","$2","$3","logdir"/logs/"$2".log.gz"}' ${final_tile_dir}/failed_tasks.tmp >> $source_dir/data/${tag}_spec
                                rm ${final_tile_dir}/failed_tasks.tmp
                            endif

                            # Also report the target if it's NOTRUN
                            if ("$target_status" == "NOTRUN") then
                                echo "DEBUG: Reporting NOTRUN target to spec file"
                                set target_task_id = `grep "$target_name" $status_check_log | awk '{print $1}'`
                                set target_log_path = "${final_tile_dir}/logs/${target_name}.log.gz"
                                echo "$target_task_id,$target_name,$target_status,$target_log_path" >> $source_dir/data/${tag}_spec
                            endif

                            echo "#table end#" >> $source_dir/data/${tag}_spec

                            echo "DEBUG: Setting target_done = 1 and exiting monitoring loop"
                            set target_done = 1
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

        sleep 30
        @ target_elapsed += 30
    end
    
    if ($target_success == 1) then

        # Log success to spec file with CSV table
        echo "" >> $source_dir/data/${tag}_spec
        echo "Target $target_name is now RUNNING. Please refer to details below:" >> $source_dir/data/${tag}_spec
        echo "" >> $source_dir/data/${tag}_spec
        echo "#table#" >> $source_dir/data/${tag}_spec
        echo "Tile,Run_Directory,Target,Status,Setup_Time,Time_to_Running,Total_Time" >> $source_dir/data/${tag}_spec
        set total_time = `expr $setup_elapsed + $target_elapsed`
        echo "$tile_name,$final_tile_dir,$target_name,RUNNING,${setup_elapsed}s,${target_elapsed}s,${total_time}s" >> $source_dir/data/${tag}_spec
        echo "#table end#" >> $source_dir/data/${tag}_spec

    else
        echo ""
        echo "======================================================================"
        echo "ERROR: Target $target_name failed to start"
        echo "======================================================================"

        # Log failure to spec file
        echo "" >> $source_dir/data/${tag}_spec
        echo "ERROR: Target $target_name failed to start at $final_tile_dir" >> $source_dir/data/${tag}_spec
        echo "Please check the logs and output" >> $source_dir/data/${tag}_spec
        
        # Mark as failed and exit
        cd $source_dir
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
else
    echo ""
    echo "WARNING: Cannot run TileBuilder - no GUI directory found"
    echo "ERROR: No GUI directory found" >> $source_dir/data/${tag}_spec
    cd $source_dir
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
