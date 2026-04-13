#!/bin/tcsh
# OSS Lint execution wrapper
# Called by static_check_command.csh
# Requires: $tile_name, $source_dir

echo "========================================="
echo "Running Lint checks for OSS"
echo "Tile: $tile_name"
echo "========================================="

# Get RHEL version for LSF resource selection
set uname_result = `uname -r`
if ("$uname_result" =~ *el8*) then
    set RHEL_TYPE = "RHEL8_64"
else
    set RHEL_TYPE = "RHEL7_64"
endif
echo "Using RHEL type: $RHEL_TYPE"

# Remove stale rhea_lint session.lock before launching LSF to prevent incremental
# analysis from restoring a stale compiled DB (e.g. after fixer-applied RTL edits)
if (-d out) then
    set lock_files = (`find out -name "session.lock" -path "*/rhea_lint/vcst_rtdb*"`)
    if ($#lock_files > 0) then
        foreach lock_file ($lock_files)
            echo "Removing stale rhea_lint session.lock: $lock_file"
            rm -f $lock_file
        end
    endif
endif

# Lint uses different bootenv per tile
if ($tile_name == "ih_top" || $tile_name == "osssys") then
    echo "Running Lint checks for ih_top..."
    bootenv -v osssys_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="ih_top" -l logs/ih_top_lint_agent.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x ih_top

else if ($tile_name == "ih_sem_share") then
    echo "Running Lint checks for ih_sem_share..."
    bootenv -v osssys_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="ih_sem_share" -l sg_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x ih_sem_share

else if ($tile_name == "hdp") then
    echo "Running Lint checks for hdp..."
    bootenv -v hdp_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="hdp_core" -l sg_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x hdp_core

else if ($tile_name == "sdma0_gc") then
    echo "Running Lint checks for sdma0_gc..."
    bootenv -v orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:sdma_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="sdma0_gc" -l sdma_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8

else if ($tile_name == "all") then
    echo "Running Lint checks for all tiles..."
    bootenv -v osssys_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="ih_top" -l sg_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x ih_top
    bootenv -v osssys_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="ih_sem_share" -l sg_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x ih_sem_share
    bootenv -v hdp_orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:oss_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="hdp_core" -l sg_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8 -x hdp_core
    bootenv -v orion
    lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:sdma_dc_elab).build(:rhea_drop, :rhea_lint)' -DDROP_TOPS="sdma0_gc" -l sdma_lint.log -DRHEA_LINT_OPTS='-keep_db -gui -no_swan -tcl_waiver_file -tcl_waiver_path=$STEM/src/meta/waivers/lint/variant/orion' -m 8

else
    echo "ERROR: Unknown tile name: $tile_name"
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

echo "Lint execution completed"
