#!/bin/tcsh
# Unified static check analysis script for GMC
# Analyzes results based on check type
# Called by static_check_command.csh after check execution
# Requires: $checktype_name, $refdir_name, $source_dir, $tag, $ip_name

echo "========================================="
echo "Analyzing $checktype_name results for GMC"
echo "IP: $ip_name"
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

# GMC runs both tiles (gmc_gmcctrl_t + gmc_gmcch_t) automatically via DROP_TOPS
# Reports will be generated for both tiles

# Route to appropriate analysis based on check type
if ("$checktype_name" == "cdc_rdc") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "CDC/RDC Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing CDC/RDC results..."

    # CDC/RDC Analysis for GMC
    # GMC uses gmc_cdc dropflow, check for both tiles
    set tiles_to_check = (gmc_gmcctrl_t gmc_gmcch_t)
    set found_any = 0

    foreach tile_check ($tiles_to_check)
        set report_path_cdc = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/$tile_check/cad/rhea_cdc/cdc_*_output/cdc_report.rpt"
        set report_path_rdc = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/$tile_check/cad/rhea_cdc/rdc_*_output/rdc_report.rpt"

        set look_for_rpt_cdc = `ls -t $report_path_cdc |& grep -v "No such file" | grep -v bck | head -1`
        set look_for_rpt_rdc = `ls -t $report_path_rdc |& grep -v "No such file" | grep -v bck | head -1`

        if ("$look_for_rpt_cdc" != "" && "$look_for_rpt_rdc" != "") then
            set found_any = 1
            echo "" >> $source_dir/data/${tag}_spec
            echo "Tile: $tile_check" >> $source_dir/data/${tag}_spec
            echo "----------------------------------------" >> $source_dir/data/${tag}_spec
            set cdc_rpt_path = "$refdir_name/$look_for_rpt_cdc"
            set rdc_rpt_path = "$refdir_name/$look_for_rpt_rdc"
            python $source_dir/script/rtg_oss_feint/gmc/cdc_rdc_extract_violation.py $cdc_rpt_path $rdc_rpt_path $tile_check >> $source_dir/data/${tag}_spec
        endif
    end

    if ($found_any == 0) then
        echo "ERROR: CDC/RDC reports not found for any GMC tiles" >> $source_dir/data/${tag}_spec
        echo "  - Search paths:" >> $source_dir/data/${tag}_spec
        echo "    CDC: out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/rhea_cdc/cdc_*_output/cdc_report.rpt" >> $source_dir/data/${tag}_spec
        echo "    RDC: out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/rhea_cdc/rdc_*_output/rdc_report.rpt" >> $source_dir/data/${tag}_spec
    endif

    echo "CDC/RDC analysis completed"

else if ("$checktype_name" == "lint") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "Lint Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing Lint results..."

    # Lint Analysis for GMC
    # GMC uses gmc_leda dropflow with DROP_TOPS="gmc_gmcctrl_t+gmc_gmcch_t"
    set tiles_to_check = (gmc_gmcctrl_t gmc_gmcch_t)
    set found_any = 0

    foreach tile_check ($tiles_to_check)
        set report_path_lint = "out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/$tile_check/cad/rhea_lint/leda_waiver.log"
        set look_for_rpt_lint = `ls -t $report_path_lint |& grep -v "No such file" | head -1`

        if ("$look_for_rpt_lint" != "") then
            set found_any = 1
            echo "" >> $source_dir/data/${tag}_spec
            echo "Tile: $tile_check" >> $source_dir/data/${tag}_spec
            echo "----------------------------------------" >> $source_dir/data/${tag}_spec
            set lint_report_path = "$refdir_name/$look_for_rpt_lint"
            perl $source_dir/script/rtg_oss_feint/gmc/lint_error_extract.pl $lint_report_path $tile_check >> $source_dir/data/${tag}_spec
        endif
    end

    if ($found_any == 0) then
        echo "ERROR: Lint reports not found for any GMC tiles" >> $source_dir/data/${tag}_spec
        echo "  - Search path: out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/rhea_lint/leda_waiver.log" >> $source_dir/data/${tag}_spec
    endif

    echo "Lint analysis completed"

else if ("$checktype_name" == "spg_dft") then
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "Spyglass DFT Analysis Results" >> $source_dir/data/${tag}_spec
    echo "Tree: $refdir_name" >> $source_dir/data/${tag}_spec
    echo "========================================" >> $source_dir/data/${tag}_spec
    echo "Analyzing Spyglass DFT results..."

    # Spyglass DFT Analysis for GMC
    # GMC uses gmc.rhea_dc_dft.build with single output: gmc_w_phy (not individual tiles)
    set error_extract_pl = "$source_dir/script/rtg_oss_feint/gmc/spg_dft_error_extract.pl"
    set error_filter = "$source_dir/script/rtg_oss_feint/gmc/spg_dft_error_filter.txt"

    # GMC SPG_DFT reports to gmc_w_phy, not individual tiles
    set report_path_spg_dft = "out/$kernel_dir/*/config/gmc_dc_elab/pub/sim/publish/tiles/tile/gmc_w_phy/cad/spg_dft/gmc_w_phy/moresimple.rpt"
    set look_for_rpt_spg_dft = `ls -t $report_path_spg_dft |& grep -v "No such file" | grep -v bck | head -1`

    if ("$look_for_rpt_spg_dft" != "") then
        echo "" >> $source_dir/data/${tag}_spec
        echo "Tile: gmc_w_phy" >> $source_dir/data/${tag}_spec
        echo "----------------------------------------" >> $source_dir/data/${tag}_spec
        set spg_rpt_path = "$refdir_name/$look_for_rpt_spg_dft"
        perl $error_extract_pl $spg_rpt_path $error_filter "gmc_w_phy" $refdir_name $ip_name >> $source_dir/data/${tag}_spec
    else
        echo "ERROR: Spyglass DFT report not found for GMC" >> $source_dir/data/${tag}_spec
        echo "  - Search path: $report_path_spg_dft" >> $source_dir/data/${tag}_spec
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

else
    echo "ERROR: Unknown check type for analysis: $checktype_name"
    exit 1
endif

echo "Analysis completed for $checktype_name"
