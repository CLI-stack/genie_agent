#!/bin/tcsh
# Spyglass DFT static check command

# Get RHEL version for LSF resource selection (inline)
set uname_result = `uname -r`
if ("$uname_result" =~ *el8*) then
    set RHEL_TYPE = "RHEL8_64"
    set out_linux_dir = "out/linux_4.18.0_64*"
else
    set RHEL_TYPE = "RHEL7_64"
    set out_linux_dir = "out/linux_3.10.0_64*"
endif

echo "Spyglass DFT execution started at `date`"
echo "Using RHEL type: $RHEL_TYPE"
setenv SPGDFT_CONFIG $STEM/src/meta/tools/spgdft/variant/$ip_name

if (! -d "$out_linux_dir") then
    echo "ERROR: Cannot find $out_linux_dir directory"
    exit 1
endif
cd $out_linux_dir/$ip_name/config/umc_top_drop2cad/pub/sim/
lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I compile_sglib.pl
lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I run_spg_dft.pl -l logs/spg_dft.log
cd $STEM
echo "Spyglass DFT execution completed at `date`"
