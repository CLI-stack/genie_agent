#!/bin/tcsh
# Unified static check analysis script for OSS
# Analyzes results based on check type
# Called by static_check_command.csh after check execution
# Requires: $checktype_name, $tile_name, $refdir_name, $source_dir, $tag, $ip_name

echo "========================================="
echo "Analyzing $checktype_name results for OSS"
echo "IP: $ip_name"
echo "Tile: $tile_name"
echo "========================================="

# Detect current kernel version to match the correct output directory
# RHEL7: linux_3.10.0_64*, RHEL8: linux_4.18.0_64*
set kernel_version = `uname -r`
if ("$kernel_version" =~ 4.18*) then
    set kernel_dir = "linux_4.18.0_64*"
else if ("$kernel_version" =~ 3.10*) then
    set kernel_dir = "linux_3.10.0_64*"
else
    # Fallback to wildcard if unknown kernel
    set kernel_dir = "linux_*"
endif
echo "Kernel detected: $kernel_version -> using $kernel_dir"

# Route to appropriate analysis based on check type
if ("$checktype_name" == "cdc_rdc") then
    echo "Analyzing CDC/RDC results..."

    # CDC/RDC Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to sort by time (newest first) and head -1 to get most recent
    set report_path_cdc = "out/$kernel_dir/*/config/*_dc_elab/pub/sim/publish/tiles/tile/*/cad/rhea_cdc/cdc_*_output/cdc_report.rpt"
    set report_path_rdc = "out/$kernel_dir/*/config/*_dc_elab/pub/sim/publish/tiles/tile/*/cad/rhea_cdc/rdc_*_output/rdc_report.rpt"
    
    if ($tile_name == all) then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The cdc/rdc run has finished.Please check violation/inferred clock below.
Tree: $refdir_name
========================================
EOF
        
        # Auto-discover tiles from CDC reports
        set tile_list = `ls $report_path_cdc | sed 's/.*\/tile\///' | sed 's/\/cad.*//' | sort -u`
        echo "Auto-discovered tiles: $tile_list"
        
        foreach ip1 ($tile_list)
            # Use ls -t to get most recent when both RHEL versions exist
            set look_for_rpt_cdc = `ls -t $report_path_cdc | grep $ip1 | head -1`
            set look_for_rpt_rdc = `ls -t $report_path_rdc | grep $ip1 | head -1`
            set cdc_rpt_path = "$refdir_name/$look_for_rpt_cdc"
            set rdc_rpt_path = "$refdir_name/$look_for_rpt_rdc"
            python $source_dir/script/rtg_oss_feint/oss/cdc_rdc_extract_violation.py $cdc_rpt_path $rdc_rpt_path $ip1 >> $source_dir/data/${tag}_spec
        end
    else
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The cdc/rdc run has finished.Please check violation/inferred clock below.
Tree: $refdir_name
========================================
EOF

        # Use ls -t to get most recent when both RHEL versions exist
        set look_for_rpt_cdc = `ls -t $report_path_cdc | grep $tile_name | head -1`
        set look_for_rpt_rdc = `ls -t $report_path_rdc | grep $tile_name | head -1`
        set cdc_rpt_path = "$refdir_name/$look_for_rpt_cdc"
        set rdc_rpt_path = "$refdir_name/$look_for_rpt_rdc"
        python $source_dir/script/rtg_oss_feint/oss/cdc_rdc_extract_violation.py $cdc_rpt_path $rdc_rpt_path $tile_name >> $source_dir/data/${tag}_spec
    endif

    echo "CDC/RDC analysis completed"

else if ("$checktype_name" == "lint") then
    echo "Analyzing Lint results..."
    
    # Lint Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to get most recent
    set report_path = "out/$kernel_dir/*/config/*_dc_elab/pub/sim/publish/tiles/tile/*/cad/rhea_lint/report_vc_spyglass_lint.txt"
    
    if ($tile_name == all) then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The lint run has finished.Please check for total error below.
Tree: $refdir_name
========================================
EOF
        
        # Auto-discover tiles from lint reports
        set tile_list = `ls $report_path | awk -F'/tile/' '{print $2}' | awk -F'/cad' '{print $1}' | sort -u`
        echo "Auto-discovered tiles: $tile_list"
        
        foreach ip1 ($tile_list)
            set look_for_rpt_lint = `ls -t $report_path | grep $ip1 | head -1`
            set report_path_full = "$refdir_name/$look_for_rpt_lint"
            perl $source_dir/script/rtg_oss_feint/oss/lint_error_extract.pl $report_path_full $ip1 >> $source_dir/data/${tag}_spec
        end
    else
        set look_for_rpt_lint = `ls -t $report_path | grep $tile_name | head -1`
        set lint_report_path = "$refdir_name/$look_for_rpt_lint"

        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The lint run has finished.Please check for total error below.
Tree: $refdir_name
========================================
EOF
        
        perl $source_dir/script/rtg_oss_feint/oss/lint_error_extract.pl $lint_report_path $tile_name >> $source_dir/data/${tag}_spec
    endif
    
    echo "Lint analysis completed"

else if ("$checktype_name" == "spg_dft") then
    echo "Analyzing Spyglass DFT results..."
    
    # Spyglass DFT Analysis - use kernel-specific directory to avoid picking stale reports from failed runs
    # Use ls -t to get most recent
    set report_path = "out/$kernel_dir/*/config/*_dc_elab/pub/sim/publish/tiles/tile/*/cad/spg_dft/*/moresimple.rpt"
    set error_extract_pl = "$source_dir/script/rtg_oss_feint/oss/spg_dft_error_extract.pl"
    set error_filter = "$source_dir/script/rtg_oss_feint/oss/spg_dft_error_filter.txt"

    if ($tile_name == all) then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The spg_dft run has finished.Please check for error at logfile below.
Tree: $refdir_name
========================================
EOF

        # Auto-discover tiles from DFT reports
        set tile_list = `ls $report_path | awk -F'/tile/' '{print $2}' | awk -F'/cad' '{print $1}' | sort -u`
        echo "Auto-discovered tiles: $tile_list"

        foreach ip1 ($tile_list)
            set look_for_rpt_spg_dft = `ls -t $report_path | grep $ip1 | head -1`
            set spg_rpt_path = "$refdir_name/$look_for_rpt_spg_dft"
            perl $error_extract_pl $spg_rpt_path $error_filter $ip1 $refdir_name >> $source_dir/data/${tag}_spec
        end
    else
        set look_for_rpt_spg_dft = `ls -t $report_path | grep $tile_name | head -1`
        set spg_rpt_path = "$refdir_name/$look_for_rpt_spg_dft"

        cat >> $source_dir/data/${tag}_spec << EOF
#text#
The spg_dft run has finished.Please check for error at logfile below.
Tree: $refdir_name
========================================
EOF

        perl $error_extract_pl $spg_rpt_path $error_filter $tile_name $refdir_name >> $source_dir/data/${tag}_spec
    endif

    # Cleanup: Remove .SG_SaveRestoreDB directories to save disk space
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
    echo "Analyzing RTL build results..."
    
    # RTL Build Analysis (from build_rtl_analysis.csh)
    if ($tile_name == "osssys") then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for $tile_name tiles are done.
Tree: $refdir_name
========================================
EOF
        
        set logfile = `ls osssys_rtl.log 2>/dev/null`
        if ("$logfile" != "") then
            set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
            if ($failpass == "PASSED") then
                echo "The RTL build at $refdir_name is PASSED" >> $source_dir/data/${tag}_spec
            else 
                echo "The RTL build at $refdir_name is FAILED, please debug" >> $source_dir/data/${tag}_spec
            endif
        else
            echo "WARNING: Log file not found for osssys" >> $source_dir/data/${tag}_spec
        endif
        
    else if ($tile_name == "hdp") then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for $tile_name tiles are done.
Tree: $refdir_name
========================================
EOF
        
        set logfile = `ls hdp_rtl.log 2>/dev/null`
        if ("$logfile" != "") then
            set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
            if ($failpass == "PASSED") then
                echo "The RTL build at $refdir_name is PASSED" >> $source_dir/data/${tag}_spec
            else 
                echo "The RTL build at $refdir_name is FAILED, please debug" >> $source_dir/data/${tag}_spec
            endif
        else
            echo "WARNING: Log file not found for hdp" >> $source_dir/data/${tag}_spec
        endif
        
    else if ($tile_name == "sdma0_gc") then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for $tile_name tiles are done.
Tree: $refdir_name
========================================
EOF
        
        set logfile = `ls sdma0_gc_rtl.log 2>/dev/null`
        if ("$logfile" != "") then
            set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
            if ($failpass == "PASSED") then
                echo "The RTL build at $refdir_name is PASSED" >> $source_dir/data/${tag}_spec
            else 
                echo "The RTL build at $refdir_name is FAILED, please debug" >> $source_dir/data/${tag}_spec
            endif
        else
            echo "WARNING: Log file not found for sdma0_gc" >> $source_dir/data/${tag}_spec
        endif
        
    else if ($tile_name == "sdma1_gc") then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for $tile_name tiles are done.
Tree: $refdir_name
========================================
EOF
        
        set logfile = `ls sdma1_gc_rtl.log 2>/dev/null`
        if ("$logfile" != "") then
            set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
            if ($failpass == "PASSED") then
                echo "The RTL build at $refdir_name is PASSED" >> $source_dir/data/${tag}_spec
            else 
                echo "The RTL build at $refdir_name is FAILED, please debug" >> $source_dir/data/${tag}_spec
            endif
        else
            echo "WARNING: Log file not found for sdma1_gc" >> $source_dir/data/${tag}_spec
        endif
        
    else if ($tile_name == "all") then
        cat >> $source_dir/data/${tag}_spec << EOF
#text#
    The rtl run for all tiles are done.
Tree: $refdir_name
========================================
EOF

        # Handle different log naming for Orion vs Arcadia
        if ("$ip_name" == "oss7_2") then
            # Arcadia flow - uses combined all_tiles_rtl.log
            set logfile = `ls all_tiles_rtl.log 2>/dev/null`
            if ("$logfile" != "") then
                set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
                if ($failpass == "PASSED") then
                    echo "  All tiles RTL build: PASSED" >> $source_dir/data/${tag}_spec
                else
                    echo "  All tiles RTL build: FAILED, please debug" >> $source_dir/data/${tag}_spec
                endif
            else
                echo "WARNING: all_tiles_rtl.log not found" >> $source_dir/data/${tag}_spec
            endif
        else
            # Orion flow - separate tile logs (oss8_0 and default)
            # Analyze osssys
            set logfile = `ls osssys_rtl.log 2>/dev/null`
            if ("$logfile" != "") then
                set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
                if ($failpass == "PASSED") then
                    echo "  osssys RTL build: PASSED" >> $source_dir/data/${tag}_spec
                else
                    echo "  osssys RTL build: FAILED" >> $source_dir/data/${tag}_spec
                endif
            endif

            # Analyze hdp
            set logfile = `ls hdp_rtl.log 2>/dev/null`
            if ("$logfile" != "") then
                set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
                if ($failpass == "PASSED") then
                    echo "  hdp RTL build: PASSED" >> $source_dir/data/${tag}_spec
                else
                    echo "  hdp RTL build: FAILED" >> $source_dir/data/${tag}_spec
                endif
            endif

            # Analyze sdma
            set logfile = `ls sdma_rtl.log 2>/dev/null`
            if ("$logfile" != "") then
                set failpass = `grep -A1 "Execution Summary" $logfile | grep -v "Execution Summary" | grep rhea_drop | awk '{print $4}' | sort -u`
                if ($failpass == "PASSED") then
                    echo "  sdma RTL build: PASSED" >> $source_dir/data/${tag}_spec
                else
                    echo "  sdma RTL build: FAILED" >> $source_dir/data/${tag}_spec
                endif
            endif
        endif

        echo "RTL build analysis completed for all tiles"
    endif
    
    echo "RTL build analysis completed"

else
    echo "ERROR: Unknown check type for analysis: $checktype_name"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

echo "Analysis completed for $checktype_name"
