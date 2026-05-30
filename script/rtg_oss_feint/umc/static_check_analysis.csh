#!/bin/tcsh
# Unified static check analysis script for UMC
# Analyzes results based on check type
# Called by static_check_command.csh after check execution
# Requires: $checktype_name, $tile_name, $refdir_name, $source_dir, $tag

echo "========================================="
echo "Analyzing $checktype_name results for UMC"
echo "Tile: $tile_name"
echo "========================================="

# Detect current kernel version to match the correct output directory
# RHEL7: linux_3.10.0_64.VCS, RHEL8: linux_4.18.0_64.VCS
set kernel_version = `uname -r`
if ("$kernel_version" =~ 4.18*) then
    set kernel_dir = "linux_4.18.0_64.VCS"
else if ("$kernel_version" =~ 3.10*) then
    set kernel_dir = "linux_3.10.0_64.VCS"
else
    # Fallback to wildcard if unknown kernel
    set kernel_dir = "linux_*.VCS"
endif
echo "Kernel detected: $kernel_version -> using $kernel_dir"

# For umc17 family: both Grimlock (umc17_0) and Godavari (umc17_x) share umc17_x output paths
if (! $?path_ip_name) then
    set path_ip_name = $ip_name
endif
if ("$ip_name" == "umc17_0") then
    set path_ip_name = "umc17_x"
endif

# Route to appropriate analysis based on check type
if ("$checktype_name" == "cdc_rdc") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "CDC/RDC Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing CDC/RDC results..."
    
    # CDC/RDC Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to sort by time (newest first) and head -1 to get most recent
    set report_path_cdc = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/*/cad/rhea_cdc/cdc_*_output/cdc_report.rpt"
    set report_path_rdc = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/*/cad/rhea_cdc/rdc_*_output/rdc_report.rpt"

    if ($tile_name == umc_top) then
        set look_for_rpt_cdc = `ls -t $report_path_cdc |& grep -v "No such file" | grep $tile_name | grep -v bck | head -1`
        set look_for_rpt_rdc = `ls -t $report_path_rdc |& grep -v "No such file" | grep $tile_name | grep -v bck | head -1`

        if ("$look_for_rpt_cdc" == "" || "$look_for_rpt_rdc" == "") then
            echo "ERROR: CDC/RDC reports not found for $tile_name" >> $source_dir/data/${tag}_spec
            if ("$look_for_rpt_cdc" == "") then
                echo "  - CDC report not found at: $report_path_cdc" >> $source_dir/data/${tag}_spec
            endif
            if ("$look_for_rpt_rdc" == "") then
                echo "  - RDC report not found at: $report_path_rdc" >> $source_dir/data/${tag}_spec
            endif
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        else
            set cdc_rpt_path = "$refdir_name/$look_for_rpt_cdc"
            set rdc_rpt_path = "$refdir_name/$look_for_rpt_rdc"
            python $source_dir/script/rtg_oss_feint/umc/cdc_rdc_extract_violation.py $cdc_rpt_path $rdc_rpt_path $tile_name $path_ip_name >> $source_dir/data/${tag}_spec
        endif
    else
        echo "WARNING: CDC/RDC analysis only supports umc_top tile (current: $tile_name)" >> $source_dir/data/${tag}_spec
    endif
    
    echo "CDC/RDC analysis completed"

else if ("$checktype_name" == "lint") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "Lint Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing Lint results..."
    
    # Lint Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to sort by time (newest first) and head -1 to get most recent
    set report_path_lint = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/*/cad/rhea_lint/leda_waiver.log"
    set look_for_rpt_lint = `ls -t $report_path_lint |& grep -v "No such file" | grep $tile_name | head -1`

    if ("$look_for_rpt_lint" == "") then
        echo "ERROR: Lint report not found for $tile_name" >> $source_dir/data/${tag}_spec
        echo "  - Search path: $report_path_lint" >> $source_dir/data/${tag}_spec
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    else
        set lint_report_path = "$refdir_name/$look_for_rpt_lint"
        perl $source_dir/script/rtg_oss_feint/umc/lint_error_extract.pl $lint_report_path $tile_name >> $source_dir/data/${tag}_spec
    endif
    
    echo "Lint analysis completed"

else if ("$checktype_name" == "spg_dft") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "Spyglass DFT Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing Spyglass DFT results..."

    # Spyglass DFT Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to sort by time (newest first) and head -1 to get most recent
    set report_path_spg_dft = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/*/cad/spg_dft/*/moresimple.rpt"
    set error_extract_pl = "$source_dir/script/rtg_oss_feint/umc/spg_dft_error_extract.pl"
    set error_filter = "$source_dir/script/rtg_oss_feint/umc/spg_dft_error_filter.txt"

    if ($tile_name == umc_top) then
        set look_for_rpt_spg_dft = `ls -t $report_path_spg_dft |& grep -v "No such file" | grep $tile_name | grep -v bck | head -1`

        if ("$look_for_rpt_spg_dft" == "") then
            echo "ERROR: Spyglass DFT report not found for $tile_name" >> $source_dir/data/${tag}_spec
            echo "  - Search path: $report_path_spg_dft" >> $source_dir/data/${tag}_spec
            source $source_dir/script/rtg_oss_feint/finishing_task.csh
            exit 1
        else
            set spg_rpt_path = "$refdir_name/$look_for_rpt_spg_dft"
            perl $error_extract_pl $spg_rpt_path $error_filter $tile_name $refdir_name $path_ip_name >> $source_dir/data/${tag}_spec
        endif
    else
        echo "WARNING: Spyglass DFT analysis only supports umc_top tile (current: $tile_name)" >> $source_dir/data/${tag}_spec
    endif

    # Cleanup: Remove .SG_SaveRestoreDB directories to save disk space
    # Note: These directories can be deeply nested, so search recursively from refdir
    echo "Cleaning up SpyGlass SaveRestoreDB directories..."
    set sg_tmpfile = /tmp/sg_cleanup_$$.txt
    find "$refdir_name" -name ".SG_SaveRestoreDB" -type d > $sg_tmpfile
    set sg_count = `wc -l < $sg_tmpfile`
    if ($sg_count > 0) then
        foreach sg_dir (`cat $sg_tmpfile`)
            if (-d "$sg_dir") then
                set sg_size = `du -sh "$sg_dir" | awk '{print $1}'`
                echo "  Removing: $sg_dir ($sg_size)"
                rm -rf "$sg_dir"
            endif
        end
    else
        echo "  No .SG_SaveRestoreDB directories found"
    endif
    rm -f $sg_tmpfile
    echo "SpyGlass cleanup completed"

    echo "Spyglass DFT analysis completed"

else if ("$checktype_name" == "build_rtl") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "RTL Build Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing RTL build results..."
    
    # RTL Build Analysis
    cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for $tile_name tiles are done.
EOF

    set logfile = `ls ${tile_name}_rtl.log |& grep -v "No such file"`

    if ("$logfile" == "") then
        echo "ERROR: RTL build log file not found: ${tile_name}_rtl.log" >> $source_dir/data/${tag}_spec
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    else
        set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
        if ("$failpass" == "PASSED") then
            echo "The RTL build at $refdir_name is PASSED" >> $source_dir/data/${tag}_spec
        else if ("$failpass" == "FAILED") then
            echo "The RTL build at $refdir_name is FAILED, please debug" >> $source_dir/data/${tag}_spec
        else
            echo "WARNING: Could not determine RTL build status (expected PASSED or FAILED, got: '$failpass')" >> $source_dir/data/${tag}_spec
        endif
    endif
    
    echo "RTL build analysis completed"

else
    echo "ERROR: Unknown check type for analysis: $checktype_name"
    exit 1
endif

echo "Analysis completed for $checktype_name"
