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

    # F7: Bare ')' without ';' closing the module port list → FM SVR-4 → FM-599
    for i, line in enumerate(mod_lines[:500]):
        stripped = line.strip()
        if stripped == ')':
            next_lines = [l.strip() for l in mod_lines[i+1:i+4] if l.strip()]
            if not next_lines or not next_lines[0].startswith(';'):
                errors.append({
                    'check': 'SVR4_bare_paren',
                    'module': mod_name,
                    'msg': f"Bare ')' without ';' at line {start_lineno+i} → FM SVR-4 → FM-599",
                    'line': start_lineno + i
                })
            break

    # SVR4_trailing_comma: port line ends with ',' then next non-empty line is ') ;'
    # → "mixed ordered and named port connections" in FM
    for i, line in enumerate(mod_lines):
        if line.rstrip().endswith(','):
            # find next non-empty line
            for j in range(i+1, min(i+5, len(mod_lines))):
                nxt = mod_lines[j].strip()
                if nxt:
                    if re.match(r'^\)\s*;', nxt):
                        errors.append({
                            'check': 'SVR4_trailing_comma',
                            'module': mod_name,
                            'msg': f"Trailing comma on line {start_lineno+i} before ') ;' — FM 'mixed ordered/named port connections' → FM-599",
                            'line': start_lineno + i
                        })
                    break

    # SVR4_double_comma: ', ,' pattern in port connections → FM-599
    for i, line in enumerate(mod_lines):
        if re.search(r',\s*,', line):
            errors.append({
                'check': 'SVR4_double_comma',
                'module': mod_name,
                'msg': f"Double comma at line {start_lineno+i}: '{line.strip()[:60]}' → FM SVR-4 → FM-599",
                'line': start_lineno + i
            })

    # SVR4_missing_cell_type: instance line starts with identifier but no cell type prefix
    # Pattern: leading whitespace then instance_name ( .pin — missing CELLTYPE before instance_name
    for i, line in enumerate(mod_lines):
        m = re.match(r'^\s+(\w+)\s*\(', line)
        if m and not re.match(r'^\s*(module|input|output|wire|reg|inout|assign|parameter|localparam|endmodule)', line):
            name = m.group(1)
            # If the identifier looks like an eco instance (eco_<jira>_*) but no cell type before it → error
            if re.match(r'^eco_\w+$', name):
                errors.append({
                    'check': 'SVR4_missing_cell_type',
                    'module': mod_name,
                    'msg': f"Missing cell type before instance '{name}' at line {start_lineno+i} → FM SVR-4 → FM-599. eco_perl_spec generated gate without cell type.",
                    'line': start_lineno + i
                })

    # Check 9: direction declaration not in port list header
    errors.extend(check_declaration_not_in_header(mod_lines, mod_name, start_lineno))

    # F6: invalid net names containing '/' or '\' (always runs — not suppressed by --strict)
    errors.extend(check_invalid_net_names(mod_lines, mod_name, start_lineno))

    # SVR4_missing_comma: two consecutive .port(net) without comma between
    # Caused by eco_passes_2_4 depth tracker finding wrong inst_close
    errors.extend(check_missing_comma(mod_lines, mod_name, start_lineno))

    # SVR4_dup_port: same port name twice in module header port list
    errors.extend(check_dup_port_in_header(mod_lines, mod_name, start_lineno))

    # SVR4_empty_connection: .port() — empty net in port connection
    errors.extend(check_empty_connection(mod_lines, mod_name, start_lineno))

    # SVR-14: net[N] indexing where base not declared as bus in module scope
    errors.extend(check_bus_indexing(mod_lines, mod_name, start_lineno, wire_decls))

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



def check_missing_comma(mod_lines, mod_name, start_lineno):
    """SVR4_missing_comma: two consecutive .port(net) without comma between.
    Pattern: ).port( where ) closes a port arg and . starts next port without comma.
    Caused by eco_passes_2_4 depth tracker finding wrong inst_close → FM-599."""
    errors = []
    # Join adjacent lines for cross-line pattern detection
    text = ''.join(mod_lines)
    for m in re.finditer(r'\)\s{0,10}\.(?: |\t)*[A-Za-z_]\w*\s*\(', text):
        # Position before the '.' — check that no ',' precedes it
        before = text[max(0, m.start()-30):m.start()]
        # The ) should be a port-arg close, not instance/bus close
        # Skip if ) is followed by ; (instance close) or } (bus close)
        suffix = text[m.start():m.start()+3]
        if ';' in suffix or '}' in suffix:
            continue
        # Check no comma between ) and .
        between = m.group(0)  # )\s+.portname(
        if ',' not in between:
            # Find approximate line number
            lineno = start_lineno + text[:m.start()].count('\n')
            errors.append({
                'check': 'SVR4_missing_comma',
                'module': mod_name,
                'msg': f"Missing comma between port connections near line {lineno}: "
                       f"'...){between[1:20].strip()}' — FM 'Expected comma or )' → FM-599",
                'line': lineno
            })
    return errors


def check_dup_port_in_header(mod_lines, mod_name, start_lineno):
    """SVR4_dup_port: same port name appears twice in module header port list.
    Caused by eco_applier applying port_declaration twice with force_reapply → FM-599."""
    errors = []
    # Extract module header port list (between first ( and ) ;)
    header = ''.join(mod_lines[:300])
    m = re.search(r'^module\s+\S+\s*\((.*?)\)\s*;', header, re.DOTALL)
    if not m:
        return errors
    port_list = m.group(1)
    # Extract identifiers (port names) — filter keywords
    KEYWORDS = {'input','output','inout','wire','reg','integer','parameter',
                'localparam','genvar','time','real','realtime'}
    names = re.findall(r'\b([A-Za-z_]\w*)\b', port_list)
    seen = {}
    for name in names:
        if name in KEYWORDS:
            continue
        if name in seen:
            errors.append({
                'check': 'SVR4_dup_port',
                'module': mod_name,
                'msg': f"Duplicate port '{name}' in module header port list → FM-599. "
                       f"eco_applier applied port_declaration twice.",
                'line': start_lineno
            })
        else:
            seen[name] = True
    return errors


def check_empty_connection(mod_lines, mod_name, start_lineno):
    """SVR4_empty_connection: .port() with no net — empty port connection.
    FM treats this as syntax error in some contexts → FM-599."""
    errors = []
    for i, line in enumerate(mod_lines):
        # .portname() — port connected to nothing
        for m in re.finditer(r'\.\s*\w+\s*\(\s*\)', line):
            errors.append({
                'check': 'SVR4_empty_connection',
                'module': mod_name,
                'msg': f"Empty port connection '{m.group(0).strip()}' at line {start_lineno+i} "
                       f"— no net specified → FM-599",
                'line': start_lineno + i
            })
    return errors


def check_bus_indexing(mod_lines, mod_name, start_lineno, wire_decls):
    """SVR-14: net[N] used in port connection where base name is not declared as bus.
    Scalar wires cannot be indexed → FM SVR-14 → FM-599."""
    errors = []
    # Build set of bus-declared names: wire/input/output [W:0] name
    bus_names = set()
    for line in mod_lines[:500]:
        m = re.match(r'^\s*(?:wire|input|output|inout)\s+\[.*?\]\s+(\w+)', line)
        if m:
            bus_names.add(m.group(1))
    # Also from port list header
    header = ''.join(mod_lines[:300])
    for m in re.finditer(r'\[.*?\]\s+(\w+)', header):
        bus_names.add(m.group(1))

    seen_errors = set()
    for i, line in enumerate(mod_lines):
        line_nc = line.split('//')[0]
        for m in re.finditer(r'\b(\w+)\[(\d+)\]', line_nc):
            base = m.group(1)
            idx  = m.group(2)
            if base in bus_names:
                continue  # valid bus indexing
            if base in wire_decls:
                # declared as scalar wire but indexed → SVR-14
                key = (base, idx)
                if key not in seen_errors:
                    seen_errors.add(key)
                    errors.append({
                        'check': 'SVR14_scalar_indexed',
                        'module': mod_name,
                        'msg': f"'{base}[{idx}]' at line {start_lineno+i}: "
                               f"'{base}' declared as scalar wire (not bus) — indexing causes SVR-14 → FM-599",
                        'line': start_lineno + i
                    })
    return errors


def check_invalid_net_names(mod_lines, mod_name, start_lineno):
    """F6: Net names containing '/' or '\\' are invalid Verilog identifiers → FM SVR-4 → FM-599.
    Common cause: FM equivalent-nets cell/pin path (e.g. cell/ZN) stored verbatim as wire name."""
    errors = []
    for i, line in enumerate(mod_lines):
        # Strip trailing comments before scanning
        line_no_comment = line.split('//')[0]
        for m in re.finditer(r'\.\s*\w+\s*\(\s*(\S+?)\s*\)', line_no_comment):
            net = m.group(1).strip(',) ')
            if '/' in net or '\\' in net:
                errors.append({
                    'check': 'F6_invalid_net_name',
                    'module': mod_name,
                    'msg': f"Net name contains invalid character ('/' or '\\'): '{net[:60]}' — FM SVR-4 syntax error → FM-599",
                    'line': start_lineno + i
                })
    return errors


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
                             'these are ALWAYS eco_applier bugs, never pre-existing. '
                             'F6 (invalid net name) always runs regardless of --strict.')
    parser.add_argument('--modules', nargs='*',
                        help='Only validate these module names (fast mode). '
                             'Pass the modules eco_applier touched to avoid scanning entire netlist. '
                             'Example: --modules ddrss_umccmd_t_umcsdpintf ddrss_umccmd_t_umcfei')
    args = parser.parse_args()

    target_modules = set(args.modules) if args.modules else None

    # Default mode: only checks that are NEVER pre-existing (always eco_applier bugs):
    #   F3, F5, F6, SVR4_missing_comma, SVR4_missing_cell_type, SVR4_double_comma,
    #   SVR4_trailing_comma, SVR4_bare_paren, SVR4_empty_connection, SVR4_dup_port,
    #   SVR14_scalar_indexed
    # --strict adds: F1_dup_wire, F2_implicit_wire_conflict, F4_dup_port_conn
    # F6 always runs regardless of mode.
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
