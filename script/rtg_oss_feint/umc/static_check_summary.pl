#!/usr/bin/perl
# Master Check Summary with Auto-Discovery
# Usage: perl static_check_summary.pl <base_dir> <tile_name> <error_filter> [output_dir]
#
# Outputs:
#   - Summary to STDOUT (for spec file)
#   - Filtered SPG_DFT violations to <output_dir>/spg_dft_filtered_<tile_name>.txt (if output_dir provided)

use strict;
use warnings;

my $base_dir = $ARGV[0] || die "Usage: $0 <base_dir> <tile_name> <error_filter> [output_dir] [ip_name]\n";
my $tile_name = $ARGV[1] || die "Usage: $0 <base_dir> <tile_name> <error_filter> [output_dir] [ip_name]\n";
my $error_filter = $ARGV[2] || "";
my $output_dir = $ARGV[3] || "";
my $ip_name = $ARGV[4] || "";

# Detect current kernel version to match the correct output directory
# RHEL7: linux_3.10.0_64*, RHEL8: linux_4.18.0_64*
my $kernel_version = `uname -r`;
chomp($kernel_version);
my $kernel_dir_pattern;
if ($kernel_version =~ /^4\.18/) {
    $kernel_dir_pattern = "linux_4.18.0_64*";
} elsif ($kernel_version =~ /^3\.10/) {
    $kernel_dir_pattern = "linux_3.10.0_64*";
} else {
    # Fallback to wildcard if unknown kernel
    $kernel_dir_pattern = "linux_*";
}

# Extract changelist from configuration_id
my $changelist = "Unknown";
my $config_file = "$base_dir/configuration_id";
if (-e $config_file) {
    open(my $cfg_fh, '<', $config_file);
    my @config_lines = <$cfg_fh>;
    close($cfg_fh);
    if (@config_lines) {
        chomp @config_lines;
        $changelist = join("\n", @config_lines);
    }
}

# Construct expected paths
my @lint_tile_variants = ($tile_name);
if ($tile_name =~ /^sdma\d+_gc$/) {
    push @lint_tile_variants, "dma_body_gc";
}
if ($tile_name =~ /^osssys$/i) {
    push @lint_tile_variants, "ih_sem_share", "ih_top";
}
if ($tile_name =~ /^hdp$/i) {
    push @lint_tile_variants, "hdp_core";
}
if ($tile_name =~ /^lsdma0$/i) {
    push @lint_tile_variants, "lsdma0_body";
}

my @lint_paths = ();
foreach my $variant (@lint_tile_variants) {
    push @lint_paths, "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$variant/cad/rhea_lint/leda_waiver.log";
    push @lint_paths, "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$variant/cad/rhea_lint/report_vc_spyglass_lint.txt";
}

my @cdc_paths = (
    "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile_name/cad/rhea_cdc/cdc_${tile_name}_output/cdc_report.rpt"
);

my @rdc_paths = (
    "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile_name/cad/rhea_cdc/rdc_${tile_name}_output/rdc_report.rpt"
);

my @dft_paths = (
    "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile_name/cad/spg_dft/$tile_name/moresimple.rpt"
);

my $lint_file = find_first_match(@lint_paths);
my $cdc_file = find_first_match(@cdc_paths);
my $rdc_file = find_first_match(@rdc_paths);
my $dft_file = find_first_match(@dft_paths);

my ($cdc_status, $cdc_errors, $cdc_warnings, $cdc_waivers, $cdc_filtered, $cdc_unfiltered) = check_cdc($cdc_file);
my ($cdc_blackboxes, $cdc_unresolved, $cdc_bb_list, $cdc_unres_list) = check_blackbox_unresolved($cdc_file);
my $cdc_inferred_clocks = count_inferred_clocks($cdc_file);
my ($rdc_status, $rdc_errors, $rdc_warnings, $rdc_waivers, $rdc_filtered, $rdc_unfiltered) = check_rdc($rdc_file);
my $rdc_inferred_resets = count_inferred_resets($rdc_file);
my ($dft_status, $dft_errors, $dft_warnings, $dft_waivers, $dft_filtered, $dft_unfiltered, $dft_filtered_file) = check_dft($dft_file, $error_filter, $tile_name, $output_dir, $ip_name);

print "#text#\n";
print "Changelist: $changelist\n";
print "Tree Path: $base_dir\n";

# Lint Table
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Errors,Warnings,Waived,Unresolved_Modules,Logfile\n";

if ($tile_name =~ /^osssys$/i) {
    my @ih_sem_paths = ("$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/ih_sem_share/cad/rhea_lint/leda_waiver.log",
                        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/ih_sem_share/cad/rhea_lint/report_vc_spyglass_lint.txt");
    my $ih_sem_file = find_first_match(@ih_sem_paths);
    my ($ih_sem_status, $ih_sem_errors, $ih_sem_warnings, $ih_sem_waivers) = check_lint($ih_sem_file);
    my $ih_sem_dir = $ih_sem_file; $ih_sem_dir =~ s/\/[^\/]+$//;
    my $ih_sem_unresolved = count_unresolved_modules($ih_sem_dir);

    my @ih_top_paths = ("$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/ih_top/cad/rhea_lint/leda_waiver.log",
                        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/ih_top/cad/rhea_lint/report_vc_spyglass_lint.txt");
    my $ih_top_file = find_first_match(@ih_top_paths);
    my ($ih_top_status, $ih_top_errors, $ih_top_warnings, $ih_top_waivers) = check_lint($ih_top_file);
    my $ih_top_dir = $ih_top_file; $ih_top_dir =~ s/\/[^\/]+$//;
    my $ih_top_unresolved = count_unresolved_modules($ih_top_dir);
    
    print "Lint,ih_sem_share,$ih_sem_status,$ih_sem_errors,$ih_sem_warnings,$ih_sem_waivers,$ih_sem_unresolved,$ih_sem_file\n";
    print "Lint,ih_top,$ih_top_status,$ih_top_errors,$ih_top_warnings,$ih_top_waivers,$ih_top_unresolved,$ih_top_file\n";
} elsif ($tile_name =~ /^sdma\d+_gc$/) {
    my ($lint_status, $lint_errors, $lint_warnings, $lint_waivers) = check_lint($lint_file);
    my $lint_dir = $lint_file; $lint_dir =~ s/\/[^\/]+$//;
    my $lint_unresolved = count_unresolved_modules($lint_dir);
    print "Lint,dma_body_gc,$lint_status,$lint_errors,$lint_warnings,$lint_waivers,$lint_unresolved,$lint_file\n";
} elsif ($tile_name =~ /^hdp$/i) {
    my ($lint_status, $lint_errors, $lint_warnings, $lint_waivers) = check_lint($lint_file);
    my $lint_dir = $lint_file; $lint_dir =~ s/\/[^\/]+$//;
    my $lint_unresolved = count_unresolved_modules($lint_dir);
    print "Lint,hdp_core,$lint_status,$lint_errors,$lint_warnings,$lint_waivers,$lint_unresolved,$lint_file\n";
} elsif ($tile_name =~ /^lsdma0$/i) {
    my ($lint_status, $lint_errors, $lint_warnings, $lint_waivers) = check_lint($lint_file);
    my $lint_dir = $lint_file; $lint_dir =~ s/\/[^\/]+$//;
    my $lint_unresolved = count_unresolved_modules($lint_dir);
    print "Lint,lsdma0_body,$lint_status,$lint_errors,$lint_warnings,$lint_waivers,$lint_unresolved,$lint_file\n";
} else {
    my ($lint_status, $lint_errors, $lint_warnings, $lint_waivers) = check_lint($lint_file);
    my $lint_dir = $lint_file; $lint_dir =~ s/\/[^\/]+$//;
    my $lint_unresolved = count_unresolved_modules($lint_dir);
    print "Lint,$tile_name,$lint_status,$lint_errors,$lint_warnings,$lint_waivers,$lint_unresolved,$lint_file\n";
}

print "#table end#\n";

# Lint Unresolved Details
my $lint_dir = $lint_file;
$lint_dir =~ s/\/[^\/]+$// if $lint_dir;
extract_lint_unresolved_details($lint_dir, $tile_name);

# CDC/RDC Table
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Errors,Inferred,Warnings,Waived,Filtered_rsmu_dft,Unfiltered_rsmu_dft,Blackboxes,Unresolved,Logfile\n";
print "CDC,$tile_name,$cdc_status,$cdc_errors,$cdc_inferred_clocks,$cdc_warnings,$cdc_waivers,$cdc_filtered,$cdc_unfiltered,$cdc_blackboxes,$cdc_unresolved,$cdc_file\n";
print "RDC,$tile_name,$rdc_status,$rdc_errors,$rdc_inferred_resets,$rdc_warnings,$rdc_waivers,$rdc_filtered,$rdc_unfiltered,N/A,N/A,$rdc_file\n";
print "#table end#\n";

# Blackbox/Unresolved Details
if ($cdc_bb_list ne "None" || $cdc_unres_list ne "None") {
    print "\n#text#\n";
    print "=" x 70 . "\n";
    print "CDC/RDC Blackboxes/Unresolved Modules:\n";
    print "=" x 70 . "\n";
    print "Blackbox Modules: $cdc_bb_list\n";
    print "Unresolved Modules: $cdc_unres_list\n";
}

# SpgDFT Table
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Total_Errors,Filtered_rsmu_dft,Unfiltered_rsmu_dft,Filtered_List,Logfile\n";
print "SpgDFT,$tile_name,$dft_status,$dft_errors,$dft_filtered,$dft_unfiltered,$dft_filtered_file,$dft_file\n";
print "#table end#\n";

sub find_first_match {
    # Find the newest file by modification time (matching static_check_analysis.csh behavior)
    # Only looks in current kernel directory to avoid false reporting from stale runs
    my (@patterns) = @_;
    my @all_files;

    foreach my $pattern (@patterns) {
        my @files = glob($pattern);
        # Filter out backup files (matching grep -v bck in analysis script)
        @files = grep { !/bck/ } @files;
        push @all_files, @files;
    }

    return "" unless @all_files;

    # Sort by modification time (newest first) - equivalent to ls -t | head -1
    my @sorted = sort { (stat($b))[9] <=> (stat($a))[9] } @all_files;
    return $sorted[0];
}

sub check_lint {
    my ($file) = @_;
    return ("Not_Complete", 0, 0, 0) unless ($file && -e $file);
    
    my $errors = 0;
    my $waivers = 0;
    open(my $fh, '<', $file) or return ("Not_Complete", 0, 0, 0);
    
    if ($file =~ /leda_waiver/i) {
        my ($in_unwaived, $in_waived) = (0, 0);
        while (my $line = <$fh>) {
            if ($line =~ /^Unwaived\s*$/) { $in_unwaived = 1; $in_waived = 0; }
            elsif ($line =~ /^Waived\s*$/) { $in_unwaived = 0; $in_waived = 1; }
            elsif ($line =~ /^Unused Waivers\s*$/) { $in_unwaived = 0; $in_waived = 0; }
            elsif ($line =~ /\s+\|\s+.*\|\s+.*\|\s+.*\|\s+\d+\s+\|/) {
                $errors++ if $in_unwaived;
                $waivers++ if $in_waived;
            }
        }
    } else {
        while (my $line = <$fh>) {
            if ($line =~ /^\s*Total\s+\d+\s+(\d+)\s+\d+\s+\d+\s+(\d+)/) {
                $errors = $1; $waivers = $2; last;
            }
        }
    }
    close($fh);
    return ("Complete", $errors, 0, $waivers);
}

sub check_cdc {
    my ($file) = @_;
    return ("Not_Complete", 0, 0, 0, 0, 0) unless ($file && -e $file);
    
    my ($violations, $cautions, $waived, $filtered) = (0, 0, 0, 0);
    open(my $fh, '<', $file) or return ("Not_Complete", 0, 0, 0, 0, 0);
    while (my $line = <$fh>) {
        $violations = $1 if $line =~ /^Violations?\s*\((\d+)\)/;
        $cautions = $1 if $line =~ /^Cautions?\s*\((\d+)\)/;
        $waived = $1 if $line =~ /Resolved - Waived.*\((\d+)\)/;
    }
    close($fh);
    
    # Count filtered violations
    $filtered = count_filtered_violations($file);
    my $unfiltered = $violations - $filtered;
    
    return ("Complete", $violations, $cautions, $waived, $filtered, $unfiltered);
}

sub check_rdc {
    my ($file) = @_;
    return ("Not_Complete", 0, 0, 0, 0, 0) unless ($file && -e $file);
    
    my ($violations, $cautions, $waived, $filtered) = (0, 0, 0, 0);
    open(my $fh, '<', $file) or return ("Not_Complete", 0, 0, 0, 0, 0);
    while (my $line = <$fh>) {
        $violations = $1 if $line =~ /^Violations?\s*\((\d+)\)/;
        $cautions = $1 if $line =~ /^Cautions?\s*\((\d+)\)/;
        $waived = $1 if $line =~ /Resolved - Waived.*\((\d+)\)/;
    }
    close($fh);
    
    # Count filtered violations
    $filtered = count_filtered_violations($file);
    my $unfiltered = $violations - $filtered;
    
    return ("Complete", $violations, $cautions, $waived, $filtered, $unfiltered);
}

sub count_filtered_violations {
    my ($file) = @_;
    return 0 unless ($file && -e $file);

    open(my $fh, '<', $file) or return 0;
    my @all_lines = <$fh>;
    close($fh);

    my $filtered_count = 0;
    my $violation_count = 0;
    my $caution_count = 0;
    my $recording = 0;

    # Track which lines have been counted (to avoid double counting)
    my %counted_lines;

    # Flexible format detection based on line structure:
    # - Header line: non-indented (starts with non-whitespace)
    # - Child line: indented (starts with tab/spaces)
    # Format A: Header has START (with or without ID), children have END with ID
    # Format B: Header has END without ID, children have START with ID

    my $header_line = '';
    my $header_has_filter = 0;
    my @children = ();  # array of [line_index, line_content]

    for (my $i = 0; $i < scalar(@all_lines); $i++) {
        my $line = $all_lines[$i];

        if ($line =~ /Violation/) {
            $violation_count++;
            if ($violation_count == 2) {
                $recording = 1;
                next;
            }
        }

        if ($line =~ /Caution/) {
            $caution_count++;
            if ($caution_count == 2 && $recording) {
                last;
            }
        }

        next unless $recording;

        # Detect header lines (non-indented lines with : start : or : end :)
        # Header with START (non-indented) - can be with or without ID
        if ($line =~ /^\S.*: start :/ && $line !~ /^\s/) {
            # Process previous violation group
            if ($header_line && @children) {
                foreach my $child_info (@children) {
                    my ($idx, $child_line) = @$child_info;
                    if ($header_has_filter || $child_line =~ /rsmu|rdft|dft|tdr/i) {
                        $filtered_count++;
                        $counted_lines{$idx} = 1;
                    }
                }
            }
            $header_line = $line;
            $header_has_filter = ($line =~ /rsmu|rdft|dft|tdr/i) ? 1 : 0;
            @children = ();
        }
        # Header with END but NO ID (non-indented)
        elsif ($line =~ /^\S.*: end :/ && $line !~ /^\s/ && $line !~ /\(ID:/) {
            # Process previous violation group
            if ($header_line && @children) {
                foreach my $child_info (@children) {
                    my ($idx, $child_line) = @$child_info;
                    if ($header_has_filter || $child_line =~ /rsmu|rdft|dft|tdr/i) {
                        $filtered_count++;
                        $counted_lines{$idx} = 1;
                    }
                }
            }
            $header_line = $line;
            $header_has_filter = ($line =~ /rsmu|rdft|dft|tdr/i) ? 1 : 0;
            @children = ();
        }
        # Child lines (indented) with ID - captures both formats
        elsif ($line =~ /^\s+.*: (start|end) :.*\(ID:/) {
            push @children, [$i, $line];
        }
        # Child lines with Synchronizer ID (alternate ID format)
        elsif ($line =~ /^\s+.*: (start|end) :.*\(Synchronizer ID:/) {
            push @children, [$i, $line];
        }
    }

    # Process last violation group
    if ($header_line && @children) {
        foreach my $child_info (@children) {
            my ($idx, $child_line) = @$child_info;
            if ($header_has_filter || $child_line =~ /rsmu|rdft|dft|tdr/i) {
                $filtered_count++;
                $counted_lines{$idx} = 1;
            }
        }
    }

    # Second pass: handle simple list violations (RDC style)
    # Only count lines not already processed by header/child logic
    $violation_count = 0;
    $caution_count = 0;
    $recording = 0;

    for (my $i = 0; $i < scalar(@all_lines); $i++) {
        my $line = $all_lines[$i];

        if ($line =~ /Violation/) {
            $violation_count++;
            if ($violation_count == 2) {
                $recording = 1;
                next;
            }
        }

        if ($line =~ /Caution/) {
            $caution_count++;
            if ($caution_count == 2 && $recording) {
                last;
            }
        }

        next unless $recording;
        next if $counted_lines{$i};  # Skip already counted lines
        next if $line =~ /: start :|: end :/;  # Skip start/end lines

        # Match simple list violations with IDs (not start/end format)
        if ($line =~ /\(ID:\s*\w+\)/) {
            if ($line =~ /rsmu|rdft|dft|tdr/i) {
                $filtered_count++;
            }
        }
    }

    return $filtered_count;
}

sub check_dft {
    my ($file, $filter_file, $tile_name, $out_dir, $ip_name) = @_;
    return ("Not_Complete", 0, 0, 0, 0, 0, "") unless ($file && -e $file);

    # Load filter patterns with section support (same logic as spg_dft_error_extract.pl)
    # Sections: [general] applies to all, [ip_name] applies only to matching IP
    my @patterns;
    my @pattern_texts;
    if ($filter_file && -e $filter_file) {
        open(my $ffh, '<', $filter_file) or return ("Not_Complete", 0, 0, 0, 0, 0, "");
        my $current_section = "general";  # Default section

        while (my $line = <$ffh>) {
            chomp $line;
            next if $line =~ /^\s*$/;     # Skip empty lines
            next if $line =~ /^\s*#/;     # Skip comments

            # Check for section header [section_name]
            if ($line =~ /^\s*\[(\w+)\]\s*$/) {
                $current_section = lc($1);
                next;
            }

            # Only include pattern if:
            # 1. It's in [general] section, OR
            # 2. It's in a section matching the ip_name
            my $ip_lower = lc($ip_name || "");
            if ($current_section eq "general" || $current_section eq $ip_lower) {
                push @patterns, qr/$line/;    # Compile as regex
                push @pattern_texts, "[$current_section] $line";  # Store with section info
            }
        }
        close($ffh);
    }

    my ($total_errors, $filtered_errors, $unfiltered_errors) = (0, 0, 0);
    my ($warnings, $waived) = (0, 0);
    my @filtered_lines = ();
    my @filtered_patterns = ();

    open(my $fh, '<', $file) or return ("Not_Complete", 0, 0, 0, 0, 0, "");

    LINE: while (my $line = <$fh>) {
        $waived = $1 if $line =~ /Number of Waived Messages\s*:\s*(\d+)/;

        # Count warnings
        if ($line =~ /^\[[\dA-F]+\]/ && $line =~ /\s+(Warning|WARNING)\s+/) {
            $warnings++;
        }

        # Count and filter errors (same logic as spg_dft_error_extract.pl)
        if ($line =~ /\s+(Error|ERROR)\s+/) {
            $total_errors++;

            # Check if line matches any filter pattern
            for (my $i = 0; $i < scalar(@patterns); $i++) {
                if ($line =~ $patterns[$i]) {
                    $filtered_errors++;
                    push @filtered_lines, $line;
                    push @filtered_patterns, $pattern_texts[$i];
                    next LINE;  # Skip line if it matches any pattern
                }
            }

            # If we get here, error was not filtered
            $unfiltered_errors++;
        }
    }
    close($fh);

    # Write filtered violations to file if output_dir is provided
    my $filtered_file = "";
    if ($out_dir && @filtered_lines > 0) {
        $filtered_file = "$out_dir/spg_dft_filtered_$tile_name.txt";
        if (open(my $ofh, '>', $filtered_file)) {
            print $ofh "=" x 70 . "\n";
            print $ofh "SpyGlass DFT Filtered Violations Report\n";
            print $ofh "=" x 70 . "\n";
            print $ofh "Tile: $tile_name\n";
            print $ofh "Report: $file\n";
            print $ofh "Filter: $filter_file\n";
            print $ofh "Total Filtered: $filtered_errors\n";
            print $ofh "=" x 70 . "\n\n";

            for (my $i = 0; $i < scalar(@filtered_lines); $i++) {
                my $num = $i + 1;
                print $ofh "[$num] Pattern: $filtered_patterns[$i]\n";
                print $ofh "    $filtered_lines[$i]";
                print $ofh "\n";
            }
            close($ofh);
        }
    }

    return ("Complete", $total_errors, $warnings, $waived, $filtered_errors, $unfiltered_errors, $filtered_file);
}

sub check_blackbox_unresolved {
    my ($file) = @_;
    return (0, 0, "None", "None") unless ($file && -e $file);
    
    my ($num_blackboxes, $num_unresolved) = (0, 0);
    my (@blackbox_modules, @unresolved_modules);
    my ($in_blackbox, $in_unresolved) = (0, 0);
    
    open(my $fh, '<', $file) or return (0, 0, "None", "None");
    while (my $line = <$fh>) {
        $num_blackboxes = $1 if $line =~ /Number\s+of\s+blackboxes\s*=\s*(\d+)/;
        $num_unresolved = $1 if $line =~ /Number\s+of\s+Unresolved\s+Modules\s*=\s*(\d+)/;
        
        if ($line =~ /^Empty Black Boxes:\s*$/) {
            <$fh>; <$fh>; # Skip separator and header
            $in_blackbox = 1;
            next;
        }
        
        if ($in_blackbox && $line =~ /^(\S+)\s+\d+\s+\//) {
            push @blackbox_modules, $1;
        }
        
        if ($in_blackbox && ($line =~ /^\s*$/ || $line =~ /^Detail Design Information/)) {
            $in_blackbox = 0;
        }
        
        if ($line =~ /^Unresolved Modules:\s*$/) {
            $in_unresolved = 1;
            next;
        }
        
        if ($in_unresolved && $line =~ /^(\S+)\s+\d+\s+Unresolved Module/) {
            push @unresolved_modules, $1;
        }
        
        if ($in_unresolved && $line =~ /^\s*Definition\s*:/) {
            $in_unresolved = 0;
        }
    }
    close($fh);
    
    my $bb_list = (@blackbox_modules > 0) ? join(" ", @blackbox_modules) : "None";
    my $unres_list = (@unresolved_modules > 0) ? join(" ", @unresolved_modules) : "None";
    
    return ($num_blackboxes, $num_unresolved, $bb_list, $unres_list);
}

sub count_unresolved_modules {
    my ($lint_dir) = @_;
    return 0 unless $lint_dir;
    
    my $unresolved_file = "$lint_dir/List_unresolved_refs.txt";
    return 0 unless (-e $unresolved_file);
    
    open(my $fh, '<', $unresolved_file) or return 0;
    my %modules;
    while (my $line = <$fh>) {
        $modules{$1}++ if $line =~ /BB Module:\s*(\S+)/;
    }
    close($fh);
    
    my (@std_cells, @memory, @rtl);
    foreach my $mod (keys %modules) {
        if ($mod =~ /^trfp/i) { push @memory, $mod; }
        elsif ($mod =~ /BWP|AMDBWP|LVT$/i) { push @std_cells, $mod; }
        else { push @rtl, $mod; }
    }
    
    return scalar(@std_cells) + scalar(@rtl) + (@memory > 0 ? 1 : 0);
}

sub extract_lint_unresolved_details {
    my ($lint_dir, $tile) = @_;
    return unless $lint_dir;
    
    my $unresolved_file = "$lint_dir/List_unresolved_refs.txt";
    return unless (-e $unresolved_file);
    
    open(my $fh, '<', $unresolved_file) or return;
    my %modules;
    while (my $line = <$fh>) {
        $modules{$1}++ if $line =~ /BB Module:\s*(\S+)/;
    }
    close($fh);
    return unless %modules;
    
    my (@std_cells, @memory, @rtl);
    foreach my $mod (sort keys %modules) {
        if ($mod =~ /^trfp/i) { push @memory, $mod; }
        elsif ($mod =~ /BWP|AMDBWP|LVT$/i) { push @std_cells, $mod; }
        else { push @rtl, $mod; }
    }
    
    my $memory_str = "";
    if (@memory > 0) {
        my $prefix = $memory[0] =~ /^(trfp[^0-9]+)/ ? $1 : "trfp";
        $memory_str = $prefix . "*(" . scalar(@memory) . ")";
    }
    
    my @module_list = (@std_cells, @rtl);
    push @module_list, $memory_str if $memory_str;
    
    print "\n#text#\n";
    print "=" x 70 . "\n";
    print "Lint Unresolved Modules for $tile:\n";
    print "=" x 70 . "\n";
    print "Unresolved Modules: " . join(" ", @module_list) . "\n";
}

sub count_inferred_clocks {
    my ($file) = @_;
    return 0 unless ($file && -e $file);
    
    open(my $fh, '<', $file) or return 0;
    my $inferred = 0;
    while (my $line = <$fh>) {
        if ($line =~ /Clock Group Summary/) {
            while (my $summary_line = <$fh>) {
                last if $summary_line =~ /^\s*$/;
                if ($summary_line =~ /Inferred\s*:\s*\((\d+)\)/) {
                    $inferred = $1;
                    last;
                }
            }
            last;
        }
    }
    close($fh);
    return $inferred;
}

sub count_inferred_resets {
    my ($file) = @_;
    return 0 unless ($file && -e $file);
    
    open(my $fh, '<', $file) or return 0;
    my $inferred = 0;
    while (my $line = <$fh>) {
        if ($line =~ /Reset Tree Summary/) {
            while (my $summary_line = <$fh>) {
                last if $summary_line =~ /^\s*$/;
                if ($summary_line =~ /Inferred\s*:\s*\((\d+)\)/) {
                    $inferred = $1;
                    last;
                }
            }
            last;
        }
    }
    close($fh);
    return $inferred;
}
