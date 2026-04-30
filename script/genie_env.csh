#!/bin/tcsh
# Genie environment setup for csh

# Initialize module system if not already loaded
if (! $?MODULESHOME) then
    source /tool/pandora/etc/profile.d/modules.csh
endif

# Load genie module
module use /proj/verif_release_ro
#module load genie/current
module load genie/2.1.4

