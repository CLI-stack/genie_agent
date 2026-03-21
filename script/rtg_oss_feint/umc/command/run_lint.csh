#!/bin/tcsh
# Lint static check command

# Get RHEL version for LSF resource selection
source $source_dir/script/rtg_oss_feint/get_rhel_version.csh

# Temporary workaround for umc9_3 - create waiver directory and copy default waivers
#if ("$ip_name" == "umc9_3") then
#    set waiver_file = "src/meta/waivers/lint/variant/umc9_3/umc_waivers.xml"
#
#    if (-f $waiver_file) then
#        echo "umc9_3 lint waiver already exists, skipping copy: $waiver_file"
#    else
#        echo "Applying umc9_3 lint workaround - creating waiver directory..."
#        mkdir -p src/meta/waivers/lint/variant/umc9_3
#        cp $source_dir/script/rtg_oss_feint/umc/umc_waivers.xml src/meta/waivers/lint/variant/umc9_3/
#        echo "Waiver file copied to src/meta/waivers/lint/variant/umc9_3/"
#    endif
#endif

lsf_bsub -P rtg-mcip-ver -R "select[type==${RHEL_TYPE}] rusage[mem=50000]" -q normal -I dj -c -v -e 'releaseflow::dropflow(:umc_top_drop2cad).build(:rhea_drop,:rhea_lint)' -l lint.log
