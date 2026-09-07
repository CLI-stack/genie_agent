#!/bin/tcsh
# GMC Lint waiver update script
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

# Default tile name if not provided - GMC has two tiles
set tile_count = `echo $tile_name | wc -w`
if ($tile_count == 0) then
    set tile_name = "gmc_gmcctrl_t"
    echo "Using default tile: $tile_name"
endif

# Validate inputs
set ip_count = `echo $ip_name | wc -w`
set refdir_count = `echo $refdir_name | wc -w`

if ($ip_count == 0 || $refdir_count == 0) then
    echo "You didn't specify IP name (gmc13_1a, etc.) and the path to update. Please specify before continuing" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# GMC lint waiver file paths:
# - src/meta/waivers/lint/variant/$ip_name/gmc_gmcch_t_waivers.xml
# - src/meta/waivers/lint/variant/$ip_name/gmc_gmcctrl_t_waivers.xml
# Note: These files may not exist if there are 0 lint waivers - create if needed

set target_file = "src/meta/waivers/lint/variant/${ip_name}/${tile_name}_waivers.xml"
set full_target_path = $refdir_name/$target_file

echo "Updating Lint waivers for $ip_name (tile: $tile_name)..."
echo "Target file: $full_target_path"

# Navigate to workspace
cd $refdir_name

# Check if waiver directory exists, create if not
set waiver_dir = "src/meta/waivers/lint/variant/${ip_name}"
if (! -d $waiver_dir) then
    echo "Creating waiver directory: $waiver_dir"
    mkdir -p $waiver_dir
endif

# Check if file exists - if not, create empty waiver file
if (! -f $target_file) then
    echo "Creating new waiver file: $target_file"
    echo '<?xml version="1.0" encoding="UTF-8"?>' > $target_file
    echo '<waivers>' >> $target_file
    echo '</waivers>' >> $target_file
    p4 add $target_file
else
    # Try to edit file in P4
    p4 edit $target_file |& cat > /dev/null
    if ($status != 0) then
        echo "Note: File not in P4 client, will update locally"
    endif
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

            # Insert before </waivers> closing tag
            set temp_file = "/tmp/lint_waiver_${tag}_$$.tmp"
            grep -v "</waivers>" $full_target_path > $temp_file
            cat $content_file >> $temp_file
            echo "</waivers>" >> $temp_file
            mv $temp_file $full_target_path

            # Report update
            echo "#text#" >> $source_dir/data/${tag}_spec
            echo "Lint Waiver Update Summary" >> $source_dir/data/${tag}_spec
            echo "Status: SUCCESS" >> $source_dir/data/${tag}_spec
            echo "Mode: Direct XML Append" >> $source_dir/data/${tag}_spec
            echo "File Updated: $target_file" >> $source_dir/data/${tag}_spec
            echo "Full Path: $full_target_path" >> $source_dir/data/${tag}_spec
            echo "Waivers Added: $has_xml_waiver XML blocks" >> $source_dir/data/${tag}_spec
            echo "" >> $source_dir/data/${tag}_spec
            echo "XML Waivers Appended:" >> $source_dir/data/${tag}_spec
            echo "----------------------------------------" >> $source_dir/data/${tag}_spec
            cat $content_file >> $source_dir/data/${tag}_spec
            echo "----------------------------------------" >> $source_dir/data/${tag}_spec
            echo "" >> $source_dir/data/${tag}_spec

        else
            # Smart matching mode - user provided code snippets or structured info
            echo "# Detected code snippets - using smart log matching"

            # Find the lint log file for GMC
            # GMC lint path: out/linux_*/$ip_name/config/gmc_leda/pub/sim/publish/tiles/tile/$tile_name/cad/rhea_lint/
            set lint_log = `find $refdir_name/out -name "leda_waiver.log" -path "*/$ip_name/config/gmc_leda/*/tile/$tile_name/cad/rhea_lint/*" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2`

            if ("$lint_log" == "") then
                echo "ERROR: Could not find lint log file" >> $source_dir/data/${tag}_spec
                echo "Expected path pattern: out/linux_*/$ip_name/config/gmc_leda/pub/sim/publish/tiles/tile/$tile_name/cad/rhea_lint/" >> $source_dir/data/${tag}_spec
                set run_status = "failed"
                source $source_dir/script/rtg_oss_feint/finishing_task.csh
                exit 1
            endif

            echo "# Found lint log: $lint_log"
            echo "# Searching log for violations matching your code snippets..."

            # Use smart log-based waiver generator (if available)
            set waiver_log = "$source_dir/data/${tag}_waiver.log"
            if (-f "$source_dir/script/rtg_oss_feint/gmc/generate_waiver_from_log.py") then
                python $source_dir/script/rtg_oss_feint/gmc/generate_waiver_from_log.py $lint_log $full_target_path $content_file agent "reviewed, waived" >& $waiver_log

                if ($status != 0) then
                    echo "ERROR: Failed to generate waivers from log" >> $source_dir/data/${tag}_spec
                    echo "Check log: $waiver_log"
                    set run_status = "failed"
                    source $source_dir/script/rtg_oss_feint/finishing_task.csh
                    exit 1
                else
                    echo "Waivers generated successfully - check $waiver_log for details"
                endif
            else
                # Fallback: just append content
                cat $content_file >> $full_target_path
            endif

            # Report update
            echo "#text#" >> $source_dir/data/${tag}_spec
            echo "Lint Waiver Update Summary" >> $source_dir/data/${tag}_spec
            echo "Status: SUCCESS" >> $source_dir/data/${tag}_spec
            echo "Mode: Smart Log Matching" >> $source_dir/data/${tag}_spec
            echo "File Updated: $target_file" >> $source_dir/data/${tag}_spec
            echo "Full Path: $full_target_path" >> $source_dir/data/${tag}_spec
            echo "" >> $source_dir/data/${tag}_spec
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
source $source_dir/script/rtg_oss_feint/gmc/static_check_command.csh
echo "Lint verification completed"

# Cleanup and finish
cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
