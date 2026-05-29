#!/bin/tcsh
# Unified static check script for CDC/RDC, Lint, and Spyglass DFT
# Called by static_check_command.csh after workspace setup
# Requires: $checktype_name, $tile_name, $ip_name, $source_dir

source /tool/pandora/etc/lsf/cshrc.lsf
source /tool/site-config/cshrc
source /proj/verif_release_ro/cbwa_initscript/current/cbwa_init.csh

if ($tile_name == umc_top) then
    bootenv -v $ip_name

    # Run appropriate static check based on checktype_name
    if ("$checktype_name" == "cdc_rdc") then
        echo "Running CDC/RDC checks..."
        source $source_dir/script/rtg_oss_feint/umc/command/run_cdc_rdc.csh
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        
    else if ("$checktype_name" == "lint") then
        echo "Running Lint checks..."
        source $source_dir/script/rtg_oss_feint/umc/command/run_lint.csh
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        
    else if ("$checktype_name" == "spg_dft") then
        echo "Running Spyglass DFT checks..."
        source $source_dir/script/rtg_oss_feint/umc/command/run_spg_dft.csh
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        
    else if ("$checktype_name" == "build_rtl") then
        echo "Running RTL build..."
        source $source_dir/script/rtg_oss_feint/umc/command/run_build_rtl.csh
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        
    else if ("$checktype_name" == "full_static_check") then
        echo "Running full static check suite..."

        # Save refdir for summary script (extract from current working directory or environment)
        set refdir_name = `pwd`

        # Read email override if exists (from --to flag in genie_cli.py)
        set email_override = ""
        if (-f "$source_dir/data/${tag}_email") then
            set email_content = `cat $source_dir/data/${tag}_email | head -1`
            if ("$email_content" != "" && "$email_content" != "default") then
                set email_override = "$email_content"
                echo "Email override detected: $email_override"
            endif
        endif

        # Step 1: Run Lint first
        echo "Step 1/2: Running Lint checks..."
        source $source_dir/script/rtg_oss_feint/umc/command/run_lint.csh

        # Analyze Lint and send notification
        echo "Analyzing Lint results and sending notification..."
        set checktype_name = "lint"

        # Backup current data spec and create temp spec for lint analysis
        set backup_spec = "$source_dir/data/${tag}_spec.backup"
        set temp_lint_spec = "/tmp/${tag}_lint_analysis.tmp"
        cp $source_dir/data/${tag}_spec $backup_spec
        rm -f $temp_lint_spec

        # Temporarily redirect tag_spec to capture lint analysis output
        rm -f $source_dir/data/${tag}_spec
        touch $source_dir/data/${tag}_spec
        cd $refdir_name
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        cd $source_dir

        # Save analysis output to temp file for email
        mv $source_dir/data/${tag}_spec $temp_lint_spec

        # Restore original spec
        mv $backup_spec $source_dir/data/${tag}_spec

        # Send notification in background
        set notify_script = "$source_dir/script/rtg_oss_feint/umc/send_static_check_notification.csh"
        if (-f $notify_script) then
            $notify_script "$source_dir" "$tag" "$tile_name" "lint" "$temp_lint_spec" "$ip_name" "$email_override" >& /tmp/lint_notify_${tag}_$$.log &
            echo "Lint notification sent in background (PID: $!)"
        else
            echo "WARNING: Notification script not found: $notify_script"
            rm -f $temp_lint_spec
        endif

        set checktype_name = "full_static_check"
        echo "Lint completed"

        # Step 2: Run CDC/RDC and Spyglass DFT in parallel using xterm with PID tracking
        echo "Step 2/2: Launching CDC/RDC and Spyglass DFT in separate xterm windows..."

        set current_dir = $refdir_name

        # Launch CDC/RDC in xterm (xterm itself runs in background)
        xterm -T "CDC/RDC Check" -e /bin/tcsh -c "cd $current_dir && bootenv -v $ip_name && source $source_dir/script/rtg_oss_feint/umc/command/run_cdc_rdc.csh" &
        set cdc_xterm_pid = "$!"
        echo "CDC/RDC xterm launched (PID: $cdc_xterm_pid)"

        # Launch Spyglass DFT in xterm (xterm itself runs in background)
        xterm -T "Spyglass DFT Check" -e /bin/tcsh -c "cd $current_dir && set ip_name = $ip_name && bootenv -v $ip_name && source $source_dir/script/rtg_oss_feint/umc/command/run_spg_dft.csh" &
        set spg_xterm_pid = "$!"
        echo "Spyglass DFT xterm launched (PID: $spg_xterm_pid)"

        # Wait for both xterm windows to close
        echo "Waiting for both xterm windows to complete..."
        echo "Note: Windows will close automatically when jobs finish"

        # IMPORTANT: Track "done" state to avoid PID reuse race condition
        # Once a process is detected as finished, mark it done permanently
        # Use while loop to check if processes are still running
        set cdc_done = 0
        set spg_done = 0

        while (1)
            # Only check CDC if not already marked done (prevents PID reuse false positive)
            if ($cdc_done == 0) then
                set cdc_running = `ps -p $cdc_xterm_pid | grep -c $cdc_xterm_pid`
                if ($cdc_running == 0) then
                    set cdc_done = 1
                    echo "CDC/RDC xterm completed (PID: $cdc_xterm_pid)"
                endif
            endif

            # Only check SPG if not already marked done (prevents PID reuse false positive)
            if ($spg_done == 0) then
                set spg_running = `ps -p $spg_xterm_pid | grep -c $spg_xterm_pid`
                if ($spg_running == 0) then
                    set spg_done = 1
                    echo "Spyglass DFT xterm completed (PID: $spg_xterm_pid)"
                endif
            endif

            # Break if both are done
            if ($cdc_done == 1 && $spg_done == 1) then
                echo "Both xterm windows completed"
                break
            endif

            # Progress update (only print periodically, not every iteration)
            if ($cdc_done == 1 && $spg_done == 0) then
                echo "CDC/RDC completed, waiting for Spyglass DFT..."
            else if ($cdc_done == 0 && $spg_done == 1) then
                echo "Spyglass DFT completed, waiting for CDC/RDC..."
            else if ($cdc_done == 0 && $spg_done == 0) then
                echo "Waiting for both CDC/RDC and Spyglass DFT..."
            endif

            sleep 10
        end

        # Analyze CDC/RDC and send notification
        echo "Analyzing CDC/RDC results and sending notification..."
        set checktype_name = "cdc_rdc"

        # Create temp spec for CDC/RDC analysis
        set temp_cdc_spec = "/tmp/${tag}_cdc_rdc_analysis.tmp"
        set backup_spec = "$source_dir/data/${tag}_spec.backup"
        cp $source_dir/data/${tag}_spec $backup_spec
        rm -f $temp_cdc_spec

        # Temporarily redirect tag_spec to capture CDC/RDC analysis output
        rm -f $source_dir/data/${tag}_spec
        touch $source_dir/data/${tag}_spec
        cd $refdir_name
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        cd $source_dir

        # Save analysis output to temp file for email
        mv $source_dir/data/${tag}_spec $temp_cdc_spec

        # Restore original spec
        mv $backup_spec $source_dir/data/${tag}_spec

        # Send notification in background
        if (-f $notify_script) then
            $notify_script "$source_dir" "$tag" "$tile_name" "cdc_rdc" "$temp_cdc_spec" "$ip_name" "$email_override" >& /tmp/cdc_rdc_notify_${tag}_$$.log &
            echo "CDC/RDC notification sent in background (PID: $!)"
        else
            echo "WARNING: Notification script not found: $notify_script"
            rm -f $temp_cdc_spec
        endif

        # Analyze Spyglass DFT and send notification
        echo "Analyzing Spyglass DFT results and sending notification..."
        set checktype_name = "spg_dft"

        # Create temp spec for Spyglass DFT analysis
        set temp_spg_spec = "/tmp/${tag}_spg_dft_analysis.tmp"
        set backup_spec = "$source_dir/data/${tag}_spec.backup"
        cp $source_dir/data/${tag}_spec $backup_spec
        rm -f $temp_spg_spec

        # Temporarily redirect tag_spec to capture Spyglass DFT analysis output
        rm -f $source_dir/data/${tag}_spec
        touch $source_dir/data/${tag}_spec
        cd $refdir_name
        source $source_dir/script/rtg_oss_feint/umc/static_check_analysis.csh
        cd $source_dir

        # Save analysis output to temp file for email
        mv $source_dir/data/${tag}_spec $temp_spg_spec

        # Restore original spec
        mv $backup_spec $source_dir/data/${tag}_spec

        # Send notification in background
        if (-f $notify_script) then
            $notify_script "$source_dir" "$tag" "$tile_name" "spg_dft" "$temp_spg_spec" "$ip_name" "$email_override" >& /tmp/spg_dft_notify_${tag}_$$.log &
            echo "Spyglass DFT notification sent in background (PID: $!)"
        else
            echo "WARNING: Notification script not found: $notify_script"
            rm -f $temp_spg_spec
        endif

        set checktype_name = "full_static_check"

        # Generate final summary and write to data spec
        echo "Generating final summary..."
        set error_filter = "$source_dir/script/rtg_oss_feint/umc/spg_dft_error_filter.txt"
        perl $source_dir/script/rtg_oss_feint/umc/static_check_summary.pl $refdir_name $tile_name $error_filter $refdir_name $ip_name >> $source_dir/data/${tag}_spec

        echo "Full static check suite completed successfully"
        
    else
        echo "ERROR: Unknown check type: $checktype_name"
    endif
    
else
    echo "ERROR: tile_name must be umc_top, got: $tile_name"
endif
