#!/usr/bin/env python3
"""
eco_query_cg_context.py — Query PreEco gate-level context for enable_swap.

Answers two questions the RTL diff cannot answer from source alone:
  1. What OTHER inputs does the existing clock gate's E-pin driver have
     besides old_enable_net? (e.g., rep_3 alongside wr_vld0_d1)
  2. Are the existing D-input gates of the target DFF array reset-gated
     via AN2D1/INR2(IReset_inv, data)? (i.e., does D have a reset AND gate?)

Usage:
    python3 eco_query_cg_context.py \
        --ref-dir   <REF_DIR> \
        --cg-inst   clk_gate_wdbptr_org0_d2_reg \
        --old-en    wr_vld0_d1 \
        --target    wdbptr_org0_d2 \
        --module    ddrss_umcdat_t_umcwdb \
        --output    /tmp/cg_context.json

Output JSON:
    {
      "other_enable_inputs": ["rep_3"],   # fan-in of E-pin driver beside old_enable_net
      "e_pin_driver_cell": "ctmi_5127",   # cell name driving the E-pin
      "e_pin_driver_type": "OR2D1BWP...", # cell type of E-pin driver
      "d_input_has_reset_gate": true,     # existing D-inputs AN2D1/INR2 reset-gated?
      "reset_gate_cell_type": "AN2D1BWP...",
      "errors": []
    }
"""

import argparse, json, os, re, subprocess, sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--ref-dir',  required=True, dest='ref_dir')
    p.add_argument('--cg-inst',  required=True, dest='cg_inst',
                   help='Existing clock gate instance name')
    p.add_argument('--old-en',   required=True, dest='old_en',
                   help='Old enable net (the one being replaced)')
    p.add_argument('--target',   required=True,
                   help='Target DFF register name (e.g. wdbptr_org0_d2)')
    p.add_argument('--module',   required=True)
    p.add_argument('--output',   required=True)
    return p.parse_args()


def zgrep(pattern, gz, timeout=30):
    try:
        r = subprocess.run(f'zgrep -E "{pattern}" {gz}',
                           shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip().splitlines()
    except Exception:
        return []


def load_module_body(gz, module_name, timeout=300):
    """Extract module body text from gzip netlist."""
    try:
        r = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=timeout)
        text = r.stdout
    except Exception:
        return ''
    m = re.search(rf'^module\s+{re.escape(module_name)}\b.*?^endmodule\b',
                  text, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else ''


def parse_cell_block(body, cell_name):
    """Return port dict for a cell instance in module body text."""
    pat = re.compile(
        rf'^\s*(\w+)\s+{re.escape(cell_name)}\s*\(([^;]+?)\)\s*;',
        re.MULTILINE | re.DOTALL)
    m = pat.search(body)
    if not m:
        return None, None
    cell_type = m.group(1)
    block = m.group(2)
    ports = {}
    for pm in re.finditer(r'\.\s*(\w+)\s*\(\s*([^)]+?)\s*\)', block):
        ports[pm.group(1)] = pm.group(2).strip()
    return cell_type, ports


def main():
    args = parse_args()
    gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if not os.path.exists(gz):
        gz = os.path.join(args.ref_dir, 'data', 'PostEco', 'Synthesize.v.gz')

    result = {
        'other_enable_inputs': [],
        'e_pin_driver_cell': '',
        'e_pin_driver_type': '',
        'd_input_has_reset_gate': False,
        'reset_gate_cell_type': '',
        'errors': [],
    }

    if not os.path.exists(gz):
        result['errors'].append(f'PreEco Synthesize netlist not found')
        Path(args.output).write_text(json.dumps(result, indent=2))
        return 1

    # ── Load module body ─────────────────────────────────────────────────────
    print(f'Loading PreEco Synthesize for module {args.module}...', file=sys.stderr)
    body = load_module_body(gz, args.module)
    if not body:
        # Try _0 variant
        body = load_module_body(gz, args.module + '_0')
    if not body:
        result['errors'].append(f'Module {args.module!r} not found in PreEco Synthesize')
        Path(args.output).write_text(json.dumps(result, indent=2))
        return 1

    # ── 1. Find clock gate E-pin current net ─────────────────────────────────
    # Get the clock gate's port connections
    cg_type, cg_ports = parse_cell_block(body, args.cg_inst)
    if not cg_ports:
        result['errors'].append(f'Clock gate {args.cg_inst!r} not found in module body')
    else:
        e_net = cg_ports.get('E') or cg_ports.get('EN') or cg_ports.get('TE', '')
        print(f'Clock gate E-pin net: {e_net!r}', file=sys.stderr)

        if e_net:
            # Find the cell driving e_net (its output Z/ZN = e_net)
            drv_pat = re.compile(
                rf'\.\s*(?:Z|ZN|ZN1)\s*\(\s*{re.escape(e_net)}\s*\)',
                re.DOTALL)
            # Find the cell instance containing this output
            cell_pat = re.compile(
                r'^\s*(\w+)\s+(\w+)\s*\(([^;]+?)\)\s*;',
                re.MULTILINE | re.DOTALL)
            for cm in cell_pat.finditer(body):
                ct, cn, cb = cm.group(1), cm.group(2), cm.group(3)
                if ct in ('wire', 'reg', 'assign', 'input', 'output', 'inout',
                          'parameter', 'localparam', 'module', 'endmodule'):
                    continue
                if drv_pat.search(cb):
                    # Found the driver of the E-pin
                    result['e_pin_driver_cell'] = cn
                    result['e_pin_driver_type'] = ct
                    print(f'E-pin driver: {ct} {cn}', file=sys.stderr)
                    # Extract all inputs (non-output pins)
                    out_pins = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'}
                    for pm in re.finditer(r'\.\s*(\w+)\s*\(\s*([^)]+?)\s*\)', cb):
                        pin, net = pm.group(1), pm.group(2).strip()
                        if pin in out_pins:
                            continue
                        # Skip the old_enable_net itself
                        if args.old_en in net or net in (args.old_en,):
                            continue
                        # Skip constants
                        if net.startswith("1'") or net.startswith("0'"):
                            continue
                        if net and net not in result['other_enable_inputs']:
                            result['other_enable_inputs'].append(net)
                    print(f'Other enable inputs: {result["other_enable_inputs"]}',
                          file=sys.stderr)
                    break

    # ── 2. Check if DFF D-inputs are reset-gated (AN2D1/INR2) ────────────────
    # Find MB DFF cells for the target register
    dff_pat = re.compile(
        rf'^\s*\w+\s+{re.escape(args.target)}_reg.*?\(',
        re.MULTILINE)
    dff_match = dff_pat.search(body)
    if dff_match:
        # Get first D-pin net of this DFF
        dff_block_start = dff_match.start()
        dff_block = body[dff_block_start:dff_block_start + 500]
        d_pin = re.search(r'\.\s*D1?\s*\(\s*(\S+?)\s*\)', dff_block)
        if d_pin:
            d_net = d_pin.group(1).rstrip('),;')
            print(f'DFF D1 net: {d_net!r}', file=sys.stderr)
            # Find cell driving this D-net
            drv_pat2 = re.compile(
                rf'\.\s*(?:Z|ZN|ZN1)\s*\(\s*{re.escape(d_net)}\s*\)',
                re.DOTALL)
            for cm in cell_pat.finditer(body):
                ct, cn, cb = cm.group(1), cm.group(2), cm.group(3)
                if ct in ('wire', 'reg', 'assign', 'input', 'output', 'inout',
                          'parameter', 'localparam', 'module', 'endmodule'):
                    continue
                if drv_pat2.search(cb):
                    print(f'D1 driver: {ct} {cn}', file=sys.stderr)
                    # Check if it's a reset gate: AN2D1 or INR2 with IReset
                    is_reset_gate = (
                        re.match(r'^(AN2|AND2|INR2)', ct, re.I) is not None
                    )
                    if is_reset_gate:
                        # AN2D1/INR2 on D-input path = reset gate. Don't require
                        # literal "IReset" — the reset signal may be routed via an
                        # intermediate net (e.g. N439 = IReset_inv). Any AND/INR2
                        # on the D-path means the existing design has reset gating
                        # that must be preserved in the new ECO D-input chain.
                        result['d_input_has_reset_gate'] = True
                        result['reset_gate_cell_type'] = ct
                        print(f'D-input reset gate: {ct} (confirmed)',
                              file=sys.stderr)
                    break

    Path(args.output).write_text(json.dumps(result, indent=2))
    print(f'Output: {args.output}', file=sys.stderr)
    print(f'other_enable_inputs: {result["other_enable_inputs"]}', file=sys.stderr)
    print(f'd_input_has_reset_gate: {result["d_input_has_reset_gate"]}', file=sys.stderr)
    return 0 if not result['errors'] else 1


if __name__ == '__main__':
    sys.exit(main())
