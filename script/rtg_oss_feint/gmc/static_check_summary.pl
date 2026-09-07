#!/usr/bin/perl
# GMC Master Check Summary with Auto-Discovery
# Usage: perl static_check_summary.pl <base_dir> <tile_name> <error_filter> [output_dir] [ip_name]
#
# tile_name can be:
#   - "all" : Run for both gmc_gmcctrl_t and gmc_gmcch_t
#   - "gmc_gmcctrl_t" : Run for single tile
#   - "gmc_gmcch_t" : Run for single tile
#   - "gmc_gmcctrl_t+gmc_gmcch_t" : Run for both tiles (+ separated)
#   - "gmc_gmcctrl_t gmc_gmcch_t" : Run for both tiles (space separated)

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

# Build tiles array from tile_name parameter
my @gmc_tiles;
if ($tile_name eq "all") {
    # Default: both tiles
    @gmc_tiles = ("gmc_gmcctrl_t", "gmc_gmcch_t");
} elsif ($tile_name =~ /\+/) {
    # Plus-separated: "gmc_gmcctrl_t+gmc_gmcch_t"
    @gmc_tiles = split(/\+/, $tile_name);
} elsif ($tile_name =~ /\s/) {
    # Space-separated: "gmc_gmcctrl_t gmc_gmcch_t"
    @gmc_tiles = split(/\s+/, $tile_name);
} else {
    # Single tile: "gmc_gmcctrl_t" or "gmc_gmcch_t"
    @gmc_tiles = ($tile_name);
}

# Trim whitespace from each tile name
@gmc_tiles = map { s/^\s+|\s+$//gr } @gmc_tiles;

print "#text#\n";
print "Changelist: $changelist\n";
print "Tree Path: $base_dir\n";
print "GMC Full Static Check Summary\n";
print "=" x 70 . "\n";

# Lint Table
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Errors,Warnings,Waived,Unresolved_Modules,Logfile\n";

foreach my $tile (@gmc_tiles) {
    my @lint_paths = (
        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile/cad/rhea_lint/leda_waiver.log",
        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile/cad/rhea_lint/report_vc_spyglass_lint.txt"
    );
    my $lint_file = find_first_match(@lint_paths);
    my ($lint_status, $lint_errors, $lint_warnings, $lint_waivers) = check_lint($lint_file);
    my $lint_dir = $lint_file; $lint_dir =~ s/\/[^\/]+$// if $lint_dir;
    my $lint_unresolved = count_unresolved_modules($lint_dir);
    print "Lint,$tile,$lint_status,$lint_errors,$lint_warnings,$lint_waivers,$lint_unresolved,$lint_file\n";
}

print "#table end#\n";

# CDC/RDC Table
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Errors,Inferred,Warnings,Waived,Filtered_rsmu_dft,Unfiltered_rsmu_dft,Blackboxes,Unresolved,Logfile\n";

foreach my $tile (@gmc_tiles) {
    my @cdc_paths = (
        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile/cad/rhea_cdc/cdc_${tile}_output/cdc_report.rpt"
    );
    my @rdc_paths = (
        "$base_dir/out/$kernel_dir_pattern/*/config/*/pub/sim/publish/tiles/tile/$tile/cad/rhea_cdc/rdc_${tile}_output/rdc_report.rpt"
    );

    my $cdc_file = find_first_match(@cdc_paths);
    my $rdc_file = find_first_match(@rdc_paths);

    my ($cdc_status, $cdc_errors, $cdc_warnings, $cdc_waivers, $cdc_filtered, $cdc_unfiltered) = check_cdc($cdc_file);
    my ($cdc_blackboxes, $cdc_unresolved, $cdc_bb_list, $cdc_unres_list) = check_blackbox_unresolved($cdc_file);
    my $cdc_inferred_clocks = count_inferred_clocks($cdc_file);

    my ($rdc_status, $rdc_errors, $rdc_warnings, $rdc_waivers, $rdc_filtered, $rdc_unfiltered) = check_rdc($rdc_file);
    my $rdc_inferred_resets = count_inferred_resets($rdc_file);

    print "CDC,$tile,$cdc_status,$cdc_errors,$cdc_inferred_clocks,$cdc_warnings,$cdc_waivers,$cdc_filtered,$cdc_unfiltered,$cdc_blackboxes,$cdc_unresolved,$cdc_file\n";
    print "RDC,$tile,$rdc_status,$rdc_errors,$rdc_inferred_resets,$rdc_warnings,$rdc_waivers,$rdc_filtered,$rdc_unfiltered,N/A,N/A,$rdc_file\n";
}

print "#table end#\n";

# SpgDFT Table
# GMC SPG_DFT reports to gmc_w_phy, not individual tiles
print "\n#table#\n";
print "Static_Check,Tile,Run_Status,Total_Errors,Filtered_rsmu_dft,Unfiltered_rsmu_dft,Filtered_List,Logfile\n";

my @dft_paths = (
    "$base_dir/out/$kernel_dir_pattern/*/config/gmc_dc_elab/pub/sim/publish/tiles/tile/gmc_w_phy/cad/spg_dft/gmc_w_phy/moresimple.rpt"
);
my $dft_file = find_first_match(@dft_paths);
my ($dft_status, $dft_errors, $dft_warnings, $dft_waivers, $dft_filtered, $dft_unfiltered, $dft_filtered_file) = check_dft($dft_file, $error_filter, "gmc_w_phy", $output_dir, $ip_name);
print "SpgDFT,gmc_w_phy,$dft_status,$dft_errors,$dft_filtered,$dft_unfiltered,$dft_filtered_file,$dft_file\n";

print "#table end#\n";

sub find_first_match {
    my (@patterns) = @_;
    my @all_files;

    foreach my $pattern (@patterns) {
        my @files = glob($pattern);
        @files = grep { !/bck/ } @files;
        push @all_files, @files;
    }

    return "" unless @all_files;

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

    my %counted_lines;
    my $header_line = '';
    my $header_has_filter = 0;
    my @children = ();

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

        if ($line =~ /^\S.*: start :/ && $line !~ /^\s/) {
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
        elsif ($line =~ /^\S.*: end :/ && $line !~ /^\s/ && $line !~ /\(ID:/) {
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
        elsif ($line =~ /^\s+.*: (start|end) :.*\(ID:/) {
            push @children, [$i, $line];
        }
        elsif ($line =~ /^\s+.*: (start|end) :.*\(Synchronizer ID:/) {
            push @children, [$i, $line];
        }
    }

    if ($header_line && @children) {
        foreach my $child_info (@children) {
            my ($idx, $child_line) = @$child_info;
            if ($header_has_filter || $child_line =~ /rsmu|rdft|dft|tdr/i) {
                $filtered_count++;
                $counted_lines{$idx} = 1;
            }
        }
    }

    # Second pass: handle simple list violations
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
        next if $counted_lines{$i};
        next if $line =~ /: start :|: end :/;

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

    my @patterns;
    my @pattern_texts;
    if ($filter_file && -e $filter_file) {
        open(my $ffh, '<', $filter_file) or return ("Not_Complete", 0, 0, 0, 0, 0, "");
        my $current_section = "general";

        while (my $line = <$ffh>) {
            chomp $line;
            next if $line =~ /^\s*$/;
            next if $line =~ /^\s*#/;

            if ($line =~ /^\s*\[(\w+)\]\s*$/) {
                $current_section = lc($1);
                next;
            }

            my $ip_lower = lc($ip_name || "");
            if ($current_section eq "general" || $current_section eq $ip_lower) {
                push @patterns, qr/$line/;
                push @pattern_texts, "[$current_section] $line";
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

        if ($line =~ /^\[[\dA-F]+\]/ && $line =~ /\s+(Warning|WARNING)\s+/) {
            $warnings++;
        }

        if ($line =~ /\s+(Error|ERROR)\s+/) {
            $total_errors++;

            for (my $i = 0; $i < scalar(@patterns); $i++) {
                if ($line =~ $patterns[$i]) {
                    $filtered_errors++;
                    push @filtered_lines, $line;
                    push @filtered_patterns, $pattern_texts[$i];
                    next LINE;
                }
            }

            $unfiltered_errors++;
        }
    }
    close($fh);

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
            <$fh>; <$fh>;
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
