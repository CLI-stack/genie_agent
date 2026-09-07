#!/bin/tcsh
# Clock and Reset Analyzer wrapper script
# Usage: clock_reset_analyzer.csh <file> <ip> <tile> <refDir> <tag>
# Analyzes RTL clock and reset structure from .vf file
#
# Priority: Uses $file if provided, otherwise searches in $refDir

set file = $1
set ip = $2
set tile = $3
set refDir = $4
set tag = $5
set source_dir = `pwd`
touch $source_dir/data/${tag}_spec

# Parse parameters
set file_name = `echo $file | sed 's/:/ /g' | awk '{$1="";print $0}'`
set ip_name = `echo $ip | sed 's/:/ /g' | awk '{$1="";print $0}'`
set tile_name = `echo $tile | sed 's/:/ /g' | awk '{$1="";print $0}'`
set refdir_name = `echo $refDir | sed 's/:/ /g' | awk '{$1="";print $0}'`

# Validate tile is provided (compulsory)
set tile_count = `echo $tile_name | wc -w`
if ($tile_count == 0) then
    echo "ERROR: Tile name is required"
    cat >> $source_dir/data/${tag}_spec << EOF
#text#
ERROR: Tile name is required
Please specify tile name in the instruction (e.g., umc_top)
EOF
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Determine the .vf file to use
set vf_file = ""

# Check if file is directly provided
set file_count = `echo $file_name | wc -w`
if ($file_count > 0) then
    if (-f "$file_name") then
        set vf_file = "$file_name"
        echo "Using provided .vf file: $vf_file"
        # Extract refdir from vf file path (everything before /out/)
        set refdir_count = `echo $refdir_name | wc -w`
        if ($refdir_count == 0) then
            set refdir_name = `echo $vf_file | sed 's|/out/.*||'`
            echo "Extracted refDir from file path: $refdir_name"
        endif
    else
        echo "WARNING: Provided file not found: $file_name"
    endif
endif

# If no file provided or file not found, search in refDir
if ("$vf_file" == "") then
    set refdir_count = `echo $refdir_name | wc -w`
    if ($refdir_count > 0) then
        if (-d "$refdir_name") then
            echo "Searching for .vf file in: $refdir_name"

            # Detect current kernel version
            set kernel_version = `uname -r`
            if ("$kernel_version" =~ 4.18*) then
                set kernel_dir = "linux_4.18.0_64*"
            else if ("$kernel_version" =~ 3.10*) then
                set kernel_dir = "linux_3.10.0_64*"
            else
                set kernel_dir = "linux_*"
            endif

            # Search pattern for .vf file using tile name (flexible for UMC/OSS)
            set vf_pattern = "$refdir_name/out/$kernel_dir/*/config/*/pub/sim/publish/tiles/tile/$tile_name/publish_rtl/$tile_name.vf"
            set vf_found = `sh -c "ls -t $vf_pattern 2>/dev/null | head -1"`

            if ("$vf_found" != "" && -f "$vf_found") then
                set vf_file = "$vf_found"
                echo "Found .vf file: $vf_file"
            endif
        else
            echo "WARNING: refDir not found: $refdir_name"
        endif
    endif
endif

# Validate we have a .vf file
if ("$vf_file" == "") then
    echo "ERROR: Could not find .vf file"
    cat >> $source_dir/data/${tag}_spec << EOF
#text#
ERROR: Could not find .vf file
Provided file: $file_name
Searched refDir: $refdir_name
Tile: $tile_name
EOF
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

echo "========================================="
echo "Clock and Reset Analyzer"
echo "========================================="
echo "VF File: $vf_file"
echo "IP: $ip_name"
echo "Tile: $tile_name"
echo "========================================="

# Run the analyzer - output to refDir
set output_file = "$refdir_name/clock_reset_report.rpt"
set html_file = "$refdir_name/clock_reset_report.html"
set dot_prefix = "$refdir_name/clock_reset"
set clock_dot = "${dot_prefix}_clock.dot"
set reset_dot = "${dot_prefix}_reset.dot"
set clock_png = "${dot_prefix}_clock.png"
set reset_png = "${dot_prefix}_reset.png"

python3 $source_dir/script/rtg_oss_feint/clock_reset_analyzer.py "$vf_file" --top $tile_name --output "$output_file" --html "$html_file" --dot "$dot_prefix"

if ($status != 0) then
    echo "ERROR: Clock/Reset analyzer failed"
    cat >> $source_dir/data/${tag}_spec << EOF
#text#
ERROR: Clock/Reset analyzer failed
VF File: $vf_file
EOF
    source $source_dir/script/rtg_oss_feint/finishing_task.csh
    exit 1
endif

# Convert DOT files to PNG using Graphviz
echo "Converting DOT files to PNG..."

# Convert clock DOT to PNG
if (-f "$clock_dot") then
    dot -Tpng "$clock_dot" -o "$clock_png"
    if ($status == 0 && -f "$clock_png") then
        echo "Generated Clock PNG: $clock_png"
    else
        echo "WARNING: Failed to convert clock DOT to PNG"
        set clock_png = ""
    endif
else
    set clock_png = ""
endif

# Convert reset DOT to PNG
if (-f "$reset_dot") then
    dot -Tpng "$reset_dot" -o "$reset_png"
    if ($status == 0 && -f "$reset_png") then
        echo "Generated Reset PNG: $reset_png"
    else
        echo "WARNING: Failed to convert reset DOT to PNG"
        set reset_png = ""
    endif
else
    set reset_png = ""
endif

# Append HTML content and report path to spec file
cat >> $source_dir/data/${tag}_spec << EOF
#text#
Clock and Reset Analysis Report
Report Details: $output_file
EOF

# Add PNG attachments (attached as files, not embedded)
if ("$clock_png" != "" && -f "$clock_png") then
    cat >> $source_dir/data/${tag}_spec << EOF
#attachment#
$clock_png
EOF
endif

if ("$reset_png" != "" && -f "$reset_png") then
    cat >> $source_dir/data/${tag}_spec << EOF
#attachment#
$reset_png
EOF
endif

cat >> $source_dir/data/${tag}_spec << EOF

#html#
EOF

cat "$html_file" >> $source_dir/data/${tag}_spec

echo ""
echo "Analysis complete!"
echo "Text report: $output_file"
echo "HTML report: $html_file"
if ("$clock_png" != "" && -f "$clock_png") then
    echo "Clock structure diagram: $clock_png"
endif
if ("$reset_png" != "" && -f "$reset_png") then
    echo "Reset structure diagram: $reset_png"
endif

# Set target_run_dir for finishing_task.csh
set target_run_dir = "$refdir_name"
cd $source_dir
source $source_dir/script/rtg_oss_feint/finishing_task.csh
