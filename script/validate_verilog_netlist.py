#!/usr/bin/env python3
"""
validate_verilog_netlist.py — Streaming Verilog gate-level netlist validator.

Catches Verilog syntax errors that cause FM-599 (ABORT_NETLIST) BEFORE FM submission.
Runs in seconds vs 1-2 hours for FM to discover the same errors.

Design: STREAMING — processes one module at a time, never loads full file into memory.
Handles multi-hundred-MB gz netlists without OOM.

Usage:
    python3 validate_verilog_netlist.py <netlist.v.gz> [<netlist2.v.gz> ...]

Exit code: 0 = PASS, 1 = FAIL
"""

import sys
import re
import gzip
import argparse
from collections import defaultdict


def iter_lines(path):
    """Stream lines from .v or .v.gz without loading full file."""
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', errors='replace') as f:
        for line in f:
            yield line


def iter_modules(path):
    """
    Stream modules one at a time from the netlist.
    Yields (module_name, module_lines, start_lineno) without holding full file.
    module_lines is the list of lines for that module only.
    """
    current_name = None
    current_lines = []
    start_lineno = 0
    lineno = 0

    for line in iter_lines(path):
        lineno += 1
        m = re.match(r'^module\s+(\S+)\s*[\(;]', line)
        if m:
            current_name = m.group(1)
            current_lines = [line]
            start_lineno = lineno
        elif re.match(r'^endmodule\b', line.strip()):
            if current_name and current_lines:
                yield (current_name, current_lines, start_lineno)
            current_name = None
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)


def validate_module(mod_name, mod_lines, start_lineno):
    """Run all checks on a single module's lines. Returns list of error dicts."""
    errors = []

    # Build combined text for pattern searches (avoid repeated join)
    # Use a lazy approach: only join when needed
    wire_decls = {}       # wire_name -> first line number
    port_conn_nets = set()
    direction_decls = {}  # name -> (direction, lineno)

    # State for instance tracking
    in_instance = False
    inst_depth = 0
    inst_name = ''
    inst_start = 0
    inst_pins = defaultdict(list)
    inst_has_decl_error = False

    for i, line in enumerate(mod_lines):
        abs_lineno = start_lineno + i

        # --- Collect wire declarations ---
        wm = re.match(r'^\s*wire\s+(?:\[.*?\]\s+)?(\w+)\s*;', line)
        if wm:
            wname = wm.group(1)
            if wname in wire_decls:
                errors.append({
                    'check': 'F1_dup_wire',
                    'module': mod_name,
                    'msg': f"Duplicate 'wire {wname};' — first at line {wire_decls[wname]}, repeated at line {abs_lineno} → FM SVR-9 → FM-599",
                    'line': abs_lineno
                })
            else:
                wire_decls[wname] = abs_lineno

        # --- Collect all port connection net names for F2 check ---
        for net in re.findall(r'\.\s*\w+\s*\(\s*(\w+)\s*\)', line):
            port_conn_nets.add(net)

        # --- F5: Corrupted port value (multiple comma-separated nets in .pin(...)) ---
        if not in_instance or True:  # check everywhere
            for pm in re.finditer(r'\.\w+\s*\(\s*([^)]+)\)', line):
                value = pm.group(1)
                # Remove bus concatenations {a,b,c}
                value_clean = re.sub(r'\{[^}]*\}', '', value)
                if ',' in value_clean:
                    errors.append({
                        'check': 'F5_corrupted_port_value',
                        'module': mod_name,
                        'msg': f"Multiple nets in single port connection (corrupted eco_applier insertion): '{pm.group(0)[:70].strip()}' → FM-599",
                        'line': abs_lineno
                    })

        # --- Instance tracking for F3 (decl inside instance) and F4 (dup pin) ---
        if not in_instance:
            # Detect cell instance start: CellType InstName (
            im = re.match(r'^\s*([A-Za-z]\w*)\s+(\w+)\s*\(', line)
            if im and im.group(1) not in (
                'module', 'input', 'output', 'wire', 'reg', 'inout',
                'integer', 'parameter', 'localparam', 'assign'
            ):
                in_instance = True
                inst_depth = line.count('(') - line.count(')')
                inst_name = im.group(2)
                inst_start = abs_lineno
                inst_pins = defaultdict(list)
                inst_has_decl_error = False
                # Collect pins from start line
                for pin in re.findall(r'\.\s*(\w+)\s*\(', line):
                    inst_pins[pin].append(abs_lineno)
                if inst_depth <= 0:
                    in_instance = False
        else:
            # Inside instance — check for illegal declarations
            dm = re.match(r'^\s*(input|output|wire|inout|reg)\b', line)
            if dm and not inst_has_decl_error:
                inst_has_decl_error = True
                errors.append({
                    'check': 'F3_decl_inside_instance',
                    'module': mod_name,
                    'msg': f"Direction declaration '{line.strip()[:50]}' found INSIDE cell instance '{inst_name}' (started line {inst_start}) → FM-599. eco_applier inserted at wrong location.",
                    'line': abs_lineno
                })

            # Collect pins for F4
            for pin in re.findall(r'\.\s*(\w+)\s*\(', line):
                inst_pins[pin].append(abs_lineno)

            inst_depth += line.count('(') - line.count(')')
            if inst_depth <= 0:
                # Instance closed — check for duplicate pins
                for pin, linenos in inst_pins.items():
                    if len(linenos) > 1:
                        errors.append({
                            'check': 'F4_dup_port_conn',
                            'module': mod_name,
                            'msg': f"Duplicate '.{pin}(...)' in instance '{inst_name}' at lines {linenos[:3]} → FM-599",
                            'line': linenos[0]
                        })
                in_instance = False

    # Check 9: direction declaration not in port list header
    errors.extend(check_declaration_not_in_header(mod_lines, mod_name, start_lineno))

    # F2: wire X conflicts with implicit wire from port connection
    wire_implicit_conflicts = set(wire_decls.keys()) & port_conn_nets
    for net in wire_implicit_conflicts:
        errors.append({
            'check': 'F2_implicit_wire_conflict',
            'module': mod_name,
            'msg': f"'wire {net};' (line {wire_decls[net]}) conflicts with implicit wire from .anypin({net}) port connection → FM SVR-9 → FM-599",
            'line': wire_decls[net]
        })

    return errors


def validate_file(path, quiet=False, max_errors=50, skip_checks=None, target_modules=None):
    """
    Stream through file, validate each module. Returns total error count.
    target_modules: set of module names to check. None = check all (slow for large netlists).
    """
    if not quiet:
        scope = f"modules: {sorted(target_modules)}" if target_modules else "all modules"
        print(f"\n=== Validating: {path} ({scope}) ===")

    total_errors = 0
    modules_checked = 0

    try:
        for mod_name, mod_lines, start_lineno in iter_modules(path):
            # Skip modules not in target set (fast mode)
            if target_modules is not None:
                # Exact match OR with _0/_1 P&R stage suffix (e.g., umcsdpintf_0)
                base_name = re.sub(r'_\d+$', '', mod_name)  # strip trailing _0, _1 etc
                if mod_name not in target_modules and base_name not in target_modules:
                    continue

            modules_checked += 1
            errors = validate_module(mod_name, mod_lines, start_lineno)
            if skip_checks:
                errors = [e for e in errors if e['check'] not in skip_checks]
            for err in errors:
                print(f"  [{err['check']}] {err['module']} | line {err['line']}")
                print(f"    {err['msg']}")
                total_errors += 1
                if total_errors >= max_errors:
                    print(f"  ... (stopped after {max_errors} errors)")
                    return total_errors
    except Exception as e:
        print(f"  ERROR reading {path}: {e}")
        return 1

    if total_errors == 0:
        if not quiet:
            print(f"  PASS: {modules_checked} modules checked, 0 errors")
    else:
        print(f"  FAIL: {total_errors} error(s) in {modules_checked} modules")

    return total_errors



def check_declaration_not_in_header(mod_lines, mod_name, start_lineno):
    """Check 9: Every input/output declaration in body must appear in port list header.
    FM-599 when reading as -r (reference): port declared in body but missing from terminal list."""
    errors = []
    # Build port list header from first ~200 lines
    header_text = "".join(mod_lines[:200])
    port_list_match = re.search(r"\((.*?)\)\s*;", header_text, re.DOTALL)
    if not port_list_match:
        return errors
    header_ports = set(re.findall(r"\b([A-Za-z_]\w*)\b", port_list_match.group(1)))
    header_ports -= {"input","output","inout","wire","reg","integer","parameter",
                     "localparam","genvar","time","real","realtime"}
    # Check all direction declarations in body
    for i, line in enumerate(mod_lines):
        m = re.match(r"^\s*(input|output|inout)\s+(?:\[.*?\]\s+)?(\w+)\s*;", line)
        if m:
            sig = m.group(2)
            if sig not in header_ports:
                errors.append({"check": "check9_decl_not_in_header", "module": mod_name,
                    "msg": (f"'{sig}' has '{m.group(1)} {sig};' declaration in body "
                            f"but NOT in module port list header — FM-599 when file used as REF (-r flag)"),
                    "line": start_lineno + i})
    return errors

def main():
    parser = argparse.ArgumentParser(
        description='Streaming Verilog netlist validator — catches FM-599 errors before FM submission.\n'
                    'FAST MODE: use --modules to validate only specific modules (recommended for large netlists).'
    )
    parser.add_argument('netlists', nargs='+', help='Netlist files (.v or .v.gz)')
    parser.add_argument('--quiet', action='store_true', help='Only print failures')
    parser.add_argument('--max-errors', type=int, default=50,
                        help='Stop after this many errors per file (default: 50)')
    parser.add_argument('--strict', action='store_true',
                        help='Run ALL checks including F1/F2/F4 which may have pre-existing false positives. '
                             'Default: only F3 (decl inside instance) and F5 (corrupted port value) — '
                             'these are ALWAYS eco_applier bugs, never pre-existing.')
    parser.add_argument('--modules', nargs='*',
                        help='Only validate these module names (fast mode). '
                             'Pass the modules eco_applier touched to avoid scanning entire netlist. '
                             'Example: --modules ddrss_umccmd_t_umcsdpintf ddrss_umccmd_t_umcfei')
    args = parser.parse_args()

    target_modules = set(args.modules) if args.modules else None

    # Default: only F3 and F5 (always eco_applier bugs). --strict adds F1/F2/F4.
    skip_checks = set()
    if not args.strict:
        skip_checks = {'F1_dup_wire', 'F2_implicit_wire_conflict', 'F4_dup_port_conn'}

    grand_total = 0
    for path in args.netlists:
        grand_total += validate_file(path, quiet=args.quiet, max_errors=args.max_errors,
                                     skip_checks=skip_checks, target_modules=target_modules)

    status = 'FAIL' if grand_total > 0 else 'PASS'
    print(f"\n=== OVERALL: {status} — {grand_total} total error(s) across {len(args.netlists)} file(s) ===")
    return 1 if grand_total > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
