#!/usr/bin/env python3
"""
eco_expand_chains.py — Expand d_input_gate_chain entries from RTL diff into
study JSON new_logic_gate entries.

Fixes the agent failure where eco_netlist_studier produces a new_logic_dff entry
with .D referencing n_eco_<jira>_d<N> but forgets to generate the actual gate chain
entries. This script reads the RTL diff JSON, finds all new_logic changes with a
non-empty d_input_gate_chain, and injects the missing gate entries into the study JSON.

Usage:
    python3 script/eco_scripts/eco_expand_chains.py \
        --rtl-diff  data/<TAG>_eco_rtl_diff.json \
        --study     data/<TAG>_eco_preeco_study.json \
        --ref-dir   <REF_DIR> \
        --jira      <JIRA> \
        --output    data/<TAG>_eco_preeco_study.json   (in-place update)

Exit code: 0 = OK (chains expanded or not needed), 1 = error
"""

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path


def find_cell_type_from_preeco(cell_type_hint, stage_gz, timeout=60):
    """
    Find cell type from PreEco netlist by grepping for the hint (generic — no hardcoded prefixes).
    If cell_type_hint is already a valid full cell type, verify it exists in PreEco.
    Otherwise try to find any cell that matches the hint prefix.
    """
    if not cell_type_hint:
        return ''
    try:
        proc = subprocess.run(
            f'zcat {stage_gz} | grep -m1 "^ *{re.escape(cell_type_hint)}" | awk \'{{print $1}}\'',
            shell=True, capture_output=True, text=True, timeout=timeout
        )
        ct = proc.stdout.strip()
        if ct and re.match(r'^[A-Z]', ct):
            return ct
    except Exception:
        pass
    return cell_type_hint  # return as-is if not found


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--rtl-diff', required=True)
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--jira',     required=True)
    p.add_argument('--output',   required=True)
    args = p.parse_args()

    rtl_diff = json.loads(Path(args.rtl_diff).read_text())
    study    = json.loads(Path(args.study).read_text())
    synth_gz = f"{args.ref_dir}/data/PreEco/Synthesize.v.gz"

    changes_added = 0

    for change in rtl_diff.get('changes', []):
        if change.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        chain = change.get('d_input_gate_chain')
        if not chain:
            continue
        target_reg = change.get('target_register', '') or change.get('new_token', '')

        for stage in ['Synthesize', 'PrePlace', 'Route']:
            stage_entries = study.get(stage, [])

            # Find the DFF entry for this target_register
            dff_entry = None
            for e in stage_entries:
                if e.get('change_type') in ('new_logic_dff', 'new_logic') and \
                   e.get('target_register') == target_reg:
                    dff_entry = e
                    break
            if not dff_entry:
                continue

            # Check if gate chain entries already exist
            existing_insts = {e.get('instance_name', '') for e in stage_entries}
            chain_instance_names = [g.get('instance_name', '') for g in chain]
            if all(inst in existing_insts for inst in chain_instance_names if inst):
                continue  # Already expanded

            # Build gate entries from chain
            new_entries = []
            for gate in chain:
                inst = gate.get('instance_name', '')
                if not inst or inst in existing_insts:
                    continue

                fn        = gate.get('gate_function', '')
                out_net   = gate.get('output_net', '')
                scope     = gate.get('instance_scope', '') or dff_entry.get('instance_scope', '')
                mod_name  = gate.get('module_name', '') or dff_entry.get('module_name', '')
                cell_type = find_cell_type_from_preeco(gate.get('cell_type', ''), synth_gz)

                # Use port_connections directly from RTL diff if available — NO hardcoded pin names
                port_connections = gate.get('port_connections') or gate.get('port_connections_per_stage', {}).get('Synthesize')
                if not port_connections:
                    # Fallback: build from inputs list using generic A1/A2/... naming
                    # Pin names will be corrected by eco_netlist_studier from actual PreEco example
                    inputs = gate.get('inputs', [])
                    port_connections = {f'A{i+1}': net for i, net in enumerate(inputs)}
                    if out_net:
                        # Determine output pin from existing port_connections keys in RTL diff
                        # by finding the key whose value matches out_net
                        existing_pc = gate.get('port_connections', {})
                        out_pin = next((k for k, v in existing_pc.items() if v == out_net), None)
                        if out_pin:
                            port_connections[out_pin] = out_net

                entry = {
                    'change_type':    'new_logic_gate',
                    'instance_name':  inst,
                    'output_net':     out_net,
                    'gate_function':  fn,
                    'cell_type':      cell_type,
                    'instance_scope': scope,
                    'module_name':    mod_name,
                    'port_connections': port_connections,
                    'port_connections_per_stage': {stage: port_connections},
                    'needs_explicit_wire_decl': True,
                    'confirmed': True,
                    'source': 'eco_expand_chains'
                }
                new_entries.append(entry)
                existing_insts.add(inst)

            if new_entries:
                # Insert gate chain entries BEFORE the DFF entry
                dff_idx = stage_entries.index(dff_entry)
                for i, ne in enumerate(new_entries):
                    stage_entries.insert(dff_idx + i, ne)
                changes_added += len(new_entries)
                print(f"  Expanded {target_reg} chain: {len(new_entries)} gates added to {stage}")

    # Write updated study JSON
    Path(args.output).write_text(json.dumps(study, indent=2))

    marker = (
        f"ECO_SCRIPT_LAUNCHED: eco_expand_chains.py\n"
        f"  chains_expanded: {changes_added}\n"
        f"  output: {args.output}"
    )
    print(f"\n{marker}")
    # Write marker sidecar
    Path(args.output.replace('_eco_preeco_study.json', '_eco_expand_chains_marker.txt')).write_text(marker + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
