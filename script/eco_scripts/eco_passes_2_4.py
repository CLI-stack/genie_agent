#!/usr/bin/env python3
"""
eco_passes_2_4.py — Apply Passes 2-4 ECO changes to PostEco netlists.

Pass 2: port_declaration  — add signals to module port lists + direction declarations
Pass 3: port_connection   — add .port(net) to submodule instance blocks
Pass 4: rewire            — change pin connections in existing cell instance blocks

Replaces eco_applier Passes 2-4 agent reasoning with deterministic code.

Usage:
    python3 script/eco_scripts/eco_passes_2_4.py \
        --study    data/<TAG>_eco_preeco_study.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --stage    Synthesize|PrePlace|Route \
        --round    <ROUND> \
        --status   data/<TAG>_eco_passes_2_4_<Stage>.json

Exit code: 0 = OK, 1 = any VERIFY_FAILED
"""

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path


# ── File I/O ──────────────────────────────────────────────────────────────────

def read_gz(path):
    with gzip.open(path, 'rt', errors='replace') as f:
        return f.readlines()

def write_gz(path, lines):
    with gzip.open(path, 'wt') as f:
        f.writelines(lines)

def grep_count(pattern, lines):
    return sum(1 for l in lines if re.search(pattern, l))


# ── Port list close finder (parenthesis depth tracking) ──────────────────────

def find_port_list_close(lines, mod_start):
    """Find the closing ')' of the module port list. Returns line index or -1."""
    depth = 0
    for i in range(mod_start, min(mod_start + 5000, len(lines))):
        clean = lines[i].split('//')[0]
        for ch in clean:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Validate: should not contain .pin( patterns (would be a cell port list)
                    if '.pin(' not in lines[i] and re.search(r'\)', lines[i]):
                        return i
    return -1


# ── Pass 2: port_declaration ─────────────────────────────────────────────────

def apply_port_declaration(lines, entry):
    """Add signal to module port list + direction declaration. Returns (lines, status, reason)."""
    mod_name = entry.get('module_name', '')
    signal   = entry.get('signal_name', '')
    direction = entry.get('declaration_type', 'input')  # 'input' or 'output'

    if direction == 'wire':
        return lines, 'SKIPPED', 'wire type — implicitly declared by port connections'

    # Find module start
    mod_start = -1
    for i, line in enumerate(lines):
        if re.match(rf'^module\s+{re.escape(mod_name)}\b', line):
            mod_start = i
            break
    if mod_start < 0:
        # Try with _0 suffix (P&R stage rename)
        for i, line in enumerate(lines):
            if re.match(rf'^module\s+{re.escape(mod_name)}_0\b', line):
                mod_start = i
                mod_name = mod_name + '_0'
                break
    if mod_start < 0:
        return lines, 'SKIPPED', f'module {mod_name} not found in stage'

    # Check already applied
    port_close = find_port_list_close(lines, mod_start)
    if port_close < 0:
        return lines, 'SKIPPED', f'cannot find port list close for {mod_name}'
    port_region = ''.join(lines[mod_start:port_close+1])
    if re.search(rf'\b{re.escape(signal)}\b', port_region):
        return lines, 'ALREADY_APPLIED', f'{signal} already in port list of {mod_name}'

    # Insert signal into port list: add ", signal" before last ")"
    close_line = lines[port_close]
    last_paren = close_line.rfind(')')
    if last_paren < 0:
        return lines, 'SKIPPED', f'no ) on port close line {port_close}'
    lines[port_close] = close_line[:last_paren] + f' , {signal}\n)\n'

    # Insert direction declaration after port list close
    decl_line = f'  {direction} {signal} ;\n'
    lines.insert(port_close + 1, decl_line)

    return lines, 'APPLIED', f'added {signal} to port list and {direction} decl in {mod_name}'


# ── Pass 3: port_connection ───────────────────────────────────────────────────

def apply_port_connection(lines, entry):
    """Add .port(net) to submodule instance block. Returns (lines, status, reason)."""
    parent_mod  = entry.get('module_name', '') or entry.get('parent_module', '')
    inst_name   = entry.get('instance_name', '')
    port_name   = entry.get('port_name', '')
    net_name    = entry.get('net_name', '')

    if not all([inst_name, port_name, net_name]):
        return lines, 'SKIPPED', 'missing instance_name/port_name/net_name'

    # Find instance block
    inst_start = -1
    for i, line in enumerate(lines):
        if re.search(rf'\b{re.escape(inst_name)}\s*\(', line):
            inst_start = i
            break
    if inst_start < 0:
        return lines, 'SKIPPED', f'instance {inst_name} not found'

    # Find instance close
    depth = 0
    inst_close = -1
    for i in range(inst_start, min(inst_start + 500, len(lines))):
        clean = lines[i].split('//')[0]
        for ch in clean:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    inst_close = i
                    break
        if inst_close >= 0:
            break
    if inst_close < 0:
        return lines, 'SKIPPED', f'cannot find instance close for {inst_name}'

    inst_block = ''.join(lines[inst_start:inst_close+1])

    # Already applied?
    if re.search(rf'\.\s*{re.escape(port_name)}\s*\(', inst_block):
        if re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', inst_block):
            return lines, 'ALREADY_APPLIED', f'.{port_name}({net_name}) already in {inst_name}'
        else:
            # Port exists but different net — rewire it
            lines[inst_close] = re.sub(
                rf'\.\s*{re.escape(port_name)}\s*\([^)]*\)',
                f'.{port_name}( {net_name} )',
                lines[inst_close]
            )
            return lines, 'APPLIED', f'rewired existing .{port_name} to ({net_name}) in {inst_name}'

    # Insert before close paren
    close_line = lines[inst_close]
    last_paren = close_line.rfind(')')
    if last_paren < 0:
        return lines, 'SKIPPED', f'no ) on instance close line {inst_close}'
    lines[inst_close] = (close_line[:last_paren] +
                         f' , .{port_name}( {net_name} )\n) ;\n')
    return lines, 'APPLIED', f'added .{port_name}({net_name}) to {inst_name}'


# ── Pass 4: rewire ────────────────────────────────────────────────────────────

def apply_rewire(lines, entry):
    """Change pin connection in cell instance block. Returns (lines, status, reason)."""
    cell_name = entry.get('cell_name', '')
    pin_name  = entry.get('pin', '')
    old_net   = entry.get('old_net', '')
    new_net   = entry.get('new_net', '')

    if not all([cell_name, pin_name, new_net]):
        return lines, 'SKIPPED', 'missing cell_name/pin/new_net'

    # Find cell instance
    cell_start = -1
    for i, line in enumerate(lines):
        if re.search(rf'\b{re.escape(cell_name)}\b', line):
            cell_start = i
            break
    if cell_start < 0:
        return lines, 'SKIPPED', f'cell {cell_name} not found'

    # Find cell block end
    depth = 0
    cell_close = -1
    for i in range(cell_start, min(cell_start + 50, len(lines))):
        for ch in lines[i].split('//')[0]:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    cell_close = i
                    break
        if cell_close >= 0:
            break
    if cell_close < 0:
        cell_close = cell_start + 10  # fallback

    cell_block = ''.join(lines[cell_start:cell_close+1])

    # Already applied?
    if re.search(rf'\.\s*{re.escape(pin_name)}\s*\(\s*{re.escape(new_net)}\s*\)', cell_block):
        return lines, 'ALREADY_APPLIED', f'.{pin_name}({new_net}) already in {cell_name}'

    # Check old_net on pin
    pat = rf'(\.\s*{re.escape(pin_name)}\s*\()\s*{re.escape(old_net)}\s*(\))'
    found = False
    for i in range(cell_start, cell_close+1):
        if re.search(pat, lines[i]):
            lines[i] = re.sub(pat, rf'\g<1>{new_net}\g<2>', lines[i], count=1)
            found = True
            break
    if not found:
        # Try without old_net constraint (ambiguous — find pin and replace)
        pat2 = rf'(\.\s*{re.escape(pin_name)}\s*\()([^)]+)(\))'
        for i in range(cell_start, cell_close+1):
            if re.search(pat2, lines[i]):
                lines[i] = re.sub(pat2, rf'\g<1>{new_net}\g<3>', lines[i], count=1)
                found = True
                break
    if not found:
        return lines, 'SKIPPED', f'pin .{pin_name}({old_net}) not found in {cell_name} block'

    return lines, 'APPLIED', f'{cell_name}.{pin_name}: {old_net} → {new_net}'


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--stage',    required=True, choices=['Synthesize','PrePlace','Route'])
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--status',   required=True)
    args = p.parse_args()

    study   = json.loads(Path(args.study).read_text())
    posteco = f"{args.ref_dir}/data/PostEco/{args.stage}.v.gz"
    entries = study.get(args.stage, [])

    lines    = read_gz(posteco)
    statuses = []
    verify_failed = 0

    for e in entries:
        if not e.get('confirmed', True):
            continue
        ct = e.get('change_type', '')

        if ct in ('port_declaration', 'port_promotion'):
            lines, st, reason = apply_port_declaration(lines, e)
        elif ct == 'port_connection':
            lines, st, reason = apply_port_connection(lines, e)
        elif ct == 'rewire':
            lines, st, reason = apply_rewire(lines, e)
        else:
            continue  # Handled by eco_perl_spec.py (Pass 1) or other pass

        inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name','?')
        statuses.append({'name': inst, 'ct': ct, 'status': st, 'reason': reason})
        if st == 'VERIFY_FAILED':
            verify_failed += 1
        print(f"  {st:15} {inst:35} {ct} — {reason[:60]}")

    # Write back if any changes were made
    applied = sum(1 for s in statuses if s['status'] == 'APPLIED')
    if applied > 0:
        write_gz(posteco, lines)
        print(f"\nRecompressed {args.stage}: {applied} changes applied.")
    else:
        print(f"\nNo changes written for {args.stage}.")

    # Write status JSON
    Path(args.status).write_text(json.dumps({
        'tag': args.tag, 'stage': args.stage, 'round': args.round,
        'entries': statuses,
        'summary': {
            'applied':       sum(1 for s in statuses if s['status']=='APPLIED'),
            'already':       sum(1 for s in statuses if s['status']=='ALREADY_APPLIED'),
            'skipped':       sum(1 for s in statuses if s['status']=='SKIPPED'),
            'verify_failed': verify_failed,
        }
    }, indent=2))

    # Write marker
    marker = (
        f"ECO_SCRIPT_LAUNCHED: eco_passes_2_4.py\n"
        f"  stage:   {args.stage}\n"
        f"  applied: {applied}\n"
        f"  status:  {args.status}"
    )
    print(f"\n{marker}")
    Path(args.status.replace('.json','_marker.txt')).write_text(marker + '\n')

    return 1 if verify_failed else 0


if __name__ == '__main__':
    sys.exit(main())
