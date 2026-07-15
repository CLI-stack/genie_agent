#!/bin/tcsh
# Find equivalent nets using TileBuilderIntFM (Formality ECO)
# Called by agent task execution
# Parameters: refDir, tag, target, netName, tile
# target supports comma-separated list for parallel execution

# netName carries bus-bit-indexed net paths like "sig[3]". tcsh treats
# unquoted "[...]" as filename-glob syntax and aborts with "No match" on
# every unquoted reference to $netName/$net_raw/$4 below (there are several,
# incl. inside backticks) if no file happens to match. This script never
# relies on real globbing, so disable it for the whole script rather than
# relying on quoting every reference correctly.
set noglob

set refDir    = $1
set tag       = $2
set target    = $3
set netName   = $4
set tile      = $5
set source_dir = `pwd`
set target_run_dir = ":"
touch $source_dir/data/${tag}_spec

# Strip "refDir:" prefix and any leading colons
set refdir_name = `echo $refDir | sed 's/refDir://' | sed 's/^://g'`

# Strip "target:" prefix — result may be comma-separated list of targets
# If empty or not specified, auto-detect the tile's 3 PreEco targets.
set target_raw = `echo $target | sed 's/target://'`
if ("$target_raw" == "" || "$target_raw" == "target") then
    # Infix-tolerant detection (handles UPF names like soundwave's
    # FmEqvPwrAllUpfSuppliesOnPreEco*). Falls back to canonical plain names if
    # the tile cannot be scanned. See script/eco_scripts/eco_fm_targets.py.
    set target_raw = `python3 $source_dir/script/eco_scripts/eco_fm_targets.py --detect "$refdir_name" PreEco`
    echo "No target specified — auto-detected PreEco targets: $target_raw"
endif
set target_list = (`echo $target_raw | sed 's/,/ /g'`)
set target_count = $#target_list

# Strip "netName:" prefix — result is comma-separated net names
set net_raw   = `echo $netName | sed 's/netName://'`

# Count nets
set net_count = `echo $net_raw | tr ',' '\n' | wc -l`

# Strip tile prefix
set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}'`

# Build space-separated list for TCL foreach
# Prepend tile_name/ only when the net does NOT already start with it. Run
# 20260511201004 root cause: agents under context pressure pass net paths
# already prefixed with the tile (e.g. "umccmd/ARB/CTRLSW/...") and the
# unconditional prepend produced "umccmd/umccmd/ARB/CTRLSW/..." → FM-036 on
# every query. Idempotent prepend is safe for both shapes.
set net_full_list = ""
foreach _net (`echo $net_raw | sed 's/,/ /g'`)
    set _has_tile_prefix = `echo "$_net" | awk -v t="$tile_name/" 'BEGIN{n=length(t)} {print (substr($0,1,n)==t)?1:0}'`
    if ($_has_tile_prefix == 1) then
        set net_full_list = "$net_full_list $_net"
    else
        set net_full_list = "$net_full_list $tile_name/$_net"
    endif
end
set net_full_list = `echo $net_full_list`

# --- Phase 1: Validate inputs ---

if ("$refdir_name" == "" || "$refdir_name" == " ") then
    echo "ERROR: refDir is empty or invalid" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -d $refdir_name) then
    echo "ERROR: Directory not found: $refdir_name" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if (! -f "$refdir_name/revrc.main") then
    echo "ERROR: Not a TileBuilder directory (revrc.main not found)" >> $source_dir/data/${tag}_spec
    echo "Directory: $refdir_name" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if ($target_count == 0) then
    echo "ERROR: FM target list is empty after parsing" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

if ("$net_raw" == "" || "$net_raw" == "netName") then
    echo "ERROR: NetName not specified. Use format: NetName: <net> or NetName: <net1>, <net2>, ..." >> $source_dir/data/${tag}_spec
    echo "Example: NetName: ARB_BEQ_Cmd1Vld, ARB_BEQ_Cmd1Bank" >> $source_dir/data/${tag}_spec
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

echo "TileBuilder directory: $refdir_name"
echo "FM target(s):          $target_raw  (count: $target_count)"
echo "Net name(s):           $net_raw  (count: $net_count)"

# --- Phase 2: Source LSF environment ---
source $source_dir/script/rtg_oss_feint/lsf_tilebuilder.csh

# --- Phase 2b: Per-tile TileBuilder release + seras env (project-portable) ---
# Selects the tile's OWN release binary ($TBTERM) and VOV/seras binding so this
# works across projects (konark 2026.01 plain targets, soundwave frozen UPF
# targets) without relying on the ambient TileBuilderTerm. See tbterm_env.csh.
set tb_refdir = "$refdir_name"
source $source_dir/script/rtg_oss_feint/supra/tbterm_env.csh

# --- Phase 3: Check ALL FM target statuses via one TileBuilderShow ---
echo "Checking FM target statuses via TileBuilderShow..."
set tb_status_log = "/tmp/tb_fm_status_${tag}.log"

cd $refdir_name
$TBTERM -x "TileBuilderShow >& $tb_status_log"
cd $source_dir
sleep 5

# Validate each target status
foreach tgt ($target_list)
    set fm_status = "UNKNOWN"
    if (-f "$tb_status_log" && -s "$tb_status_log") then
        set fm_status = `grep "$tgt" $tb_status_log | awk '{print $NF}'`
        if ("$fm_status" == "") set fm_status = "NOT_FOUND"
    endif

    echo "FM target '$tgt' status: $fm_status"

    if ("$fm_status" == "NOTRUN" || "$fm_status" == "RUNNING" || "$fm_status" == "UNKNOWN" || "$fm_status" == "NOT_FOUND") then
        echo "#text#" >> $source_dir/data/${tag}_spec
        echo "ERROR: FM target '$tgt' status is '$fm_status'" >> $source_dir/data/${tag}_spec
        echo "Please ensure the Formality run has completed before running this command." >> $source_dir/data/${tag}_spec
        echo "#text end#" >> $source_dir/data/${tag}_spec
        rm -f $tb_status_log
        cd $source_dir
        set run_status = "failed"
        source $source_dir/script/rtg_oss_feint/finishing_task.csh
        exit 1
    endif
end
rm -f $tb_status_log

echo "All $target_count FM targets validated — launching in parallel"

# --- Phase 4: Suppress xterm (once, before parallel launches) ---
set userctl = "$HOME/.TileBuilder.general.controls"
set tb_ctrl_existed = 0
if (-f $userctl) set tb_ctrl_existed = 1
echo "TILEBUILDERINTFM_TERMINAL =" >> $userctl

# --- Phase 5: Generate TCL per target and launch all in parallel ---
set result_files = ()

foreach tgt ($target_list)
    set tcl_script  = "/tmp/fm_find_equiv_${tag}_${tgt}.tcl"
    set output_rpt  = "rpts/${tgt}/find_equivalent_nets_${tag}.txt"
    set result_file = "${refdir_name}/${output_rpt}"

    # Remove stale result file so poll detects only fresh output
    rm -f $result_file

    cat > $tcl_script << TCLEOF
# find_equivalent_nets — auto-generated by find_equivalent_nets.csh
# Tag: ${tag}  Target: ${tgt}

set topModule \$P(TOP_MODULE)
set ref_lib   [string toupper "FMWORK_REF_\${topModule}"]
set ref_top   "\${ref_lib}/\${topModule}"
echo "INFO: ref_top = \$ref_top"

set nets { ${net_full_list} }

redirect ${refdir_name}/${output_rpt} {
    foreach net_suffix \$nets {
        # Guard against a redundant self-prefix: ref_top already ends in
        # \$topModule, so when net_suffix ALSO starts with "\$topModule/"
        # (tile-level FM runs where topModule == tile instance name, e.g.
        # a standalone-tile TileBuilder ECO run) the naive concatenation
        # below would produce ".../topModule/topModule/net" and FM-036
        # "Unknown name" on every query. Strip one redundant leading
        # "topModule/" copy before building the r: path. Root-caused on
        # run 20260713182634 (ddrss_umc_t Synthesize/PrePlace/Route all
        # FM-036'd identically). Safe no-op for tiles where topModule is a
        # true chip-level top and net_suffix references a nested instance
        # that legitimately does not start with topModule/.
        set _self_prefix "\${topModule}/"
        set _self_plen [string length \$_self_prefix]
        if {[string length \$net_suffix] > \$_self_plen && [string equal -length \$_self_plen \$_self_prefix \$net_suffix]} {
            set net_suffix [string range \$net_suffix \$_self_plen end]
        }
        set full_ref_net "r:/\$ref_top/\$net_suffix"
        echo "=========================================="
        echo "Net: \$full_ref_net"
        echo "=========================================="
        find_equivalent_nets \$full_ref_net
        echo ""
    }
    echo "FIND_EQUIVALENT_NETS_COMPLETE"
}
exit
TCLEOF

    # High memory reservation for the heavy Synthesize FM target.
    # NOTE: FM memory demand is an empirical PER-TILE property (driven by the
    # Formality session/compare-point density — the FM session runs to 8-9 GB on
    # these tiles), NOT the gate-level netlist file size. Counter-example: konark
    # umccmd has a LARGER netlist than umcdat yet runs fine on normal memory,
    # while umcdat cannot. So we gate on a tile allowlist, not on file size.
    # Match the Synthesize target by SUFFIX (infix-tolerant — also matches UPF
    # names like FmEqvPwrAllUpfSuppliesOnPreEcoSynthesizeVsPreEcoSynRtl).
    # Add new memory-hog tiles to $_himem_tiles as they are identified.
    set _himem_tiles = (umcdat umc ddrss_umc_t)
    set extra_opts = ""
    set _is_syn = `echo "$tgt" | grep -cE 'SynthesizeVs.*SynRtl$'`
    if ($_is_syn > 0) then
        foreach _ht ($_himem_tiles)
            if ("$tile_name" == "$_ht") then
                # -r 130000: high-memory reservation for the heavy Synthesize FM.
                # -q regr_high: force the regr_high queue instead of the tile
                #   default TILEBUILDER_LSFQUEUE (gb256). gb256 is chronically
                #   saturated and our group fairshare there is near-zero, so
                #   fenets jobs pended ~20h. A command-line -q overrides
                #   TILEBUILDER_LSFQUEUE (TileBuilderIntCommonLSF: the
                #   `if ("x$queue"=="x")` fallback is skipped when queue is set).
                set extra_opts = "-r 130000 -q regr_high"
                echo "${tgt}: using -r 130000 -q regr_high (high-memory tile: $tile_name)"
            endif
        end
    endif

    echo "Launching TileBuilderIntFM for target: $tgt"
    cd $refdir_name
    $TBTERM -x "TileBuilderIntFM --nogui --append $tcl_script $tgt $extra_opts" &
    cd $source_dir

    set result_files = ($result_files $result_file)
end

# --- Phase 6: Poll until ALL result files have sentinel ---
set max_wait = 180
set wait_count = 0

echo "Waiting for all $target_count targets to complete (max ${max_wait} minutes)..."

while ($wait_count < $max_wait)
    sleep 60
    set wait_count = `expr $wait_count + 1`

    set done_count = 0
    foreach rf ($result_files)
        if (-f "$rf") then
            set complete = `grep -c "FIND_EQUIVALENT_NETS_COMPLETE" $rf`
            if ($complete > 0) @ done_count++
        endif
    end

    echo "Waiting... ${done_count}/${target_count} targets done (${wait_count}/${max_wait} min)"

    if ($done_count == $target_count) then
        echo "All targets complete after ${wait_count} minute(s)"
        break
    endif
end

# Check for any incomplete targets
set all_ok = 1
foreach rf ($result_files)
    set sentinel_found = 0
    if (-f "$rf") set sentinel_found = `grep -c "FIND_EQUIVALENT_NETS_COMPLETE" $rf`
    if (! -f "$rf" || $sentinel_found == 0) then
        echo "#text#" >> $source_dir/data/${tag}_spec
        echo "ERROR: find_equivalent_nets timed out for: $rf" >> $source_dir/data/${tag}_spec
        echo "#text end#" >> $source_dir/data/${tag}_spec
        set all_ok = 0
    endif
end

if ($all_ok == 0) then
    foreach tgt ($target_list)
        rm -f /tmp/fm_find_equiv_${tag}_${tgt}.tcl
    end
    cd $source_dir
    set run_status = "failed"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# --- Phase 7: Write results for all targets and finish ---
echo "#table#" >> $source_dir/data/${tag}_spec
echo "Field,Value" >> $source_dir/data/${tag}_spec
set net_value = `echo $net_full_list | sed 's/  */ | /g'`
echo "Net(s),$net_value" >> $source_dir/data/${tag}_spec
echo "Targets Run,$target_raw" >> $source_dir/data/${tag}_spec
echo "#table end#" >> $source_dir/data/${tag}_spec

# Write results per target with clear header
foreach tgt ($target_list)
    set rf = "${refdir_name}/rpts/${tgt}/find_equivalent_nets_${tag}.txt"
    echo "#text#" >> $source_dir/data/${tag}_spec
    echo "===========================================" >> $source_dir/data/${tag}_spec
    echo "TARGET: $tgt" >> $source_dir/data/${tag}_spec
    echo "===========================================" >> $source_dir/data/${tag}_spec
    grep -v "FIND_EQUIVALENT_NETS_COMPLETE" $rf >> $source_dir/data/${tag}_spec
    echo "#text end#" >> $source_dir/data/${tag}_spec
end

# Cleanup TCL scripts
foreach tgt ($target_list)
    rm -f /tmp/fm_find_equiv_${tag}_${tgt}.tcl
end

# Restore ~/.TileBuilder.general.controls
if ($tb_ctrl_existed == 0) then
    rm -f $userctl
else
    grep -v "^TILEBUILDERINTFM_TERMINAL" $userctl >! /tmp/tb_ctrl_restore_$$.tmp
    mv /tmp/tb_ctrl_restore_$$.tmp $userctl
endif

cd $source_dir
set run_status = "finished"
source csh/env.csh
source csh/updateTask.csh
