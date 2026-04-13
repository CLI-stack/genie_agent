#!/bin/tcsh
# GMC Lint static check command
# Command: be_dj --bootenv_v gmc13_1a -J lsf -e 'releaseflow::dropflow(:gmc_leda).build(:rhea_lint, :rhea_drop)' -DDROP_TOPS="gmc_gmcctrl_t+gmc_gmcch_t" -DLINT_TILE -l sg_lint_tile_run.log -DRHEA_LINT_OPTS='-keep_db -gui'

# Get RHEL version for LSF resource selection
set uname_result = `uname -r`
if ("$uname_result" =~ *el8*) then
    set RHEL_TYPE = "RHEL8_64"
else
    set RHEL_TYPE = "RHEL7_64"
endif

echo "GMC Lint execution started at `date`"
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

# GMC uses be_dj with bootenv_v and gmc_leda dropflow
# -J lsf handles LSF submission internally
# Tiles: gmc_gmcctrl_t, gmc_gmcch_t
be_dj --bootenv_v gmc13_1a -J lsf -e 'releaseflow::dropflow(:gmc_leda).build(:rhea_lint, :rhea_drop)' -DDROP_TOPS="gmc_gmcctrl_t+gmc_gmcch_t" -DLINT_TILE -l logs/sg_lint_tile_run.log -DRHEA_LINT_OPTS='-keep_db'

echo "GMC Lint execution completed at `date`"
