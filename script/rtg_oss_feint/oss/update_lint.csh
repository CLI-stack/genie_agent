#!/bin/tcsh
# Unified Lint waiver update script
# Usage: update_lint.csh <refDir> <ip> <tile> <tag> <updateType>

set refDir = $1
set ip = $2
set tile = $3
set tag = $4
set updateType = $5
set source_dir = `pwd`
touch $source_dir/data/${tag}_spec

# Parse parameters
set refdir_name = `echo $refDir | sed 's/:/ /g' | awk '{$1="";print $0}'`
set ip_name = `echo $ip | sed 's/:/ /g' | awk '{$1="";print $0}'`
set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}'`
set updatetype_name = `echo $updateType | sed 's/:/ /g' | awk '{$1="";print $0}'`

# Default tile name if not provided
set tile_count = `echo $tile_name | wc -w`
if ($tile_count == 0) then
    set tile_name = umc_top
    echo "Using default tile: $tile_name"
endif

# Validate inputs
set ip_count = `echo $ip_name | wc -w`
set refdir_count = `echo $refdir_name | wc -w`

if ($ip_count == 0 || $refdir_count == 0) then
    echo "You didn't specify IP name (oss8_0, oss7_2, etc.) and the path to update. Please specify before continuing" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Set target waiver file path
set target_file = "src/meta/waivers/lint/variant/${ip_name}/umc_waivers.xml"
set full_target_path = $refdir_name/$target_file

echo "Updating Lint waivers for $ip_name..."
echo "Target file: $full_target_path"

# Navigate to workspace
cd $refdir_name

# Try to edit file in P4, but continue even if it fails (file might not be in client)
p4 edit $target_file |& cat > /dev/null
if ($status != 0) then
    echo "Note: File not in P4 client, will update locally"
endif

# Read waiver content from AI-extracted file
set content_file = "$source_dir/data/$tag.lint_waiver"

echo "# Checking content file: $content_file"

if (-f $content_file) then
    set n_items = `cat $content_file | wc -l`
    
    if ($n_items > 0) then
        # Check if content contains XML waiver format (direct append mode)
        set has_xml_waiver = `grep -c "<waive_regexp>" $content_file`
        
        if ($has_xml_waiver > 0) then
            # Direct XML append mode - user provided complete XML waivers
            echo "# Detected XML waiver format - appending directly to waiver file"
            
            # Append XML waivers directly to target file
            cat $content_file >> $full_target_path
            
            # Report update
            cat >> $source_dir/data/${tag}_spec << EOF
#text#
Lint Waiver Update Summary
Status: SUCCESS
Mode: Direct XML Append
File Updated: $target_file
Full Path: $full_target_path
Waivers Added: $has_xml_waiver XML blocks

XML Waivers Appended:
----------------------------------------
EOF
            cat $content_file >> $source_dir/data/${tag}_spec
            cat >> $source_dir/data/${tag}_spec << EOF
----------------------------------------

EOF
            
        else
            # Smart matching mode - user provided code snippets or structured info
            echo "# Detected code snippets - using smart log matching"
            
            # Find the lint log file - use wildcard for kernel version (RHEL7: linux_3.10.0_64.VCS, RHEL8: linux_4.18.0_64.VCS)
            set lint_log_pattern = "out/linux_*/*/config/*/pub/sim/publish/tiles/tile/*/cad/rhea_lint/leda_waiver.log"

            # Use find to locate the log file, sort by time (newest first) to get most recent when both RHEL versions exist
            set lint_log = `find $refdir_name/out -name "leda_waiver.log" -path "*/tile/$tile_name/cad/rhea_lint/*" -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2`
            
            if ("$lint_log" == "") then
                echo "ERROR: Could not find lint log file" >> $source_dir/data/${tag}_spec
                echo "Expected pattern: $lint_log_pattern" >> $source_dir/data/${tag}_spec
                set run_status = "failed"
                source $source_dir/script/rtg_oss_feint/finishing_task.csh
                exit 1
            endif
            
            echo "# Found lint log: $lint_log"
            echo "# Searching log for violations matching your code snippets..."
            
            # Use smart log-based waiver generator (redirect output to file to avoid tcsh parsing)
            set waiver_log = "$source_dir/data/${tag}_waiver.log"
            python $source_dir/script/rtg_oss_feint/umc/generate_waiver_from_log.py $lint_log $full_target_path $content_file agent "reviewed, waived" >& $waiver_log
            
            if ($status != 0) then
                echo "ERROR: Failed to generate waivers from log" >> $source_dir/data/${tag}_spec
                echo "Check log: $waiver_log"
                set run_status = "failed"
                source $source_dir/script/rtg_oss_feint/finishing_task.csh
                exit 1
            else
                echo "Waivers generated successfully - check $waiver_log for details"
            endif
            
            # Report update (don't include content to avoid tcsh parsing issues)
            cat >> $source_dir/data/${tag}_spec << EOF
#text#
Lint Waiver Update Summary
Status: SUCCESS
Mode: Smart Log Matching
File Updated: $target_file
Full Path: $full_target_path

EOF
        endif
        
    else
        echo "Update failed: No waiver content found in email" >> $source_dir/data/${tag}_spec
        set run_status = "failed"
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
else
    echo "Update failed: Waiver content file not found: $content_file" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Rerun Lint checks to verify the waiver update
echo "Rerunning Lint checks to verify waiver updates..."
set checktype_name = lint
set tile_name = umc_top
source $source_dir/script/rtg_oss_feint/umc/static_check_command.csh
echo "Lint verification completed"

# Cleanup and finish
cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
