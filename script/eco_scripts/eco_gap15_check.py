#!/usr/bin/env python3
"""
eco_gap15_check.py — Pre-check script for eco_netlist_studier Step 3.

Determines is_output_port for every and_term change in the RTL diff JSON.
Outputs a JSON file so eco_netlist_studier gets the answer directly,
without relying on agent reasoning.

Usage:
    python3 script/eco_gap15_check.py \
        --rtl-diff  data/<TAG>_eco_rtl_diff.json \
        --ref-dir   <REF_DIR> \
        --output    data/<TAG>_eco_gap15_check.json

Output JSON format:
    {
      "<old_token>": {
        "is_output_port": true|false,
        "rtl_check": <N>,
        "gatelvl_check": <N>,
        "strategy": "module_port_direct_gating" | "proceed_to_selection",
        "declaring_module": "<module_name>"
      }, ...
    }
"""

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path


def grep_count(pattern, path, decompress=False):
    """Count lines matching pattern in file. Returns 0 on error."""
    try:
        if decompress or str(path).endswith('.gz'):
            proc = subprocess.run(
                ['zcat', str(path)],
                capture_output=True, timeout=60
            )
            text = proc.stdout.decode('utf-8', errors='replace')
        else:
            text = Path(path).read_text(errors='replace')
        return sum(1 for line in text.splitlines() if re.search(pattern, line))
    except Exception:
        return 0


def find_rtl_file(ref_dir, module_name):
    """
    Find the RTL source file for a module — generic, works for any tile/project.
    Searches multiple candidate directories in priority order.
    """
    ref = Path(ref_dir)
    # Priority search paths (handles different tile structures)
    candidate_dirs = [
        ref / 'data' / 'SynRtl',
        ref / 'data' / 'PreEco' / 'SynRtl',
        ref / 'data' / 'SynRtl' / 'rtl',
    ]
    # Also check parent directories (some tiles share RTL)
    for parent in [ref.parent, ref.parent.parent]:
        candidate_dirs.append(parent / 'data' / 'SynRtl')

    # Build short module name variants to match against
    # e.g. "ddrss_umccmd_t_umcdcqarb_0" → try "umcdcqarb", "umcdcqarb_0", full name
    variants = [module_name]
    if '_t_' in module_name:
        short = module_name.split('_t_')[-1]          # e.g. "umcdcqarb_0"
        variants.append(short)
        variants.append(re.sub(r'_\d+$', '', short))  # strip trailing _0 suffix

    for d in candidate_dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob('**/*.v')):
            try:
                text = f.read_text(errors='replace')
                if any(f'module {v}' in text or f'module {v} ' in text for v in variants):
                    return f
            except Exception:
                pass
    return None


def check_gatelvl_output_port(old_token, module_name, ref_dir):
    """Check if old_token is declared as output in PreEco gate-level module header."""
    synth_gz = Path(ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
    if not synth_gz.exists():
        return 0
    try:
        proc = subprocess.run(['zcat', str(synth_gz)], capture_output=True, timeout=120)
        text = proc.stdout.decode('utf-8', errors='replace')
    except Exception:
        return 0

    # Find the module block and check its header for output <old_token>
    in_module = False
    header_done = False
    paren_depth = 0
    count = 0

    for line in text.splitlines():
        # Detect module start — try exact name and _0 suffix variant
        if re.match(rf'^module\s+{re.escape(module_name)}[\s(;]', line) or \
           re.match(rf'^module\s+{re.escape(module_name)}_0[\s(;]', line):
            in_module = True
            paren_depth = 0
            header_done = False

        if in_module and not header_done:
            # Track parenthesis depth for port list
            for ch in line.split('//')[0]:
                if ch == '(': paren_depth += 1
                elif ch == ')': paren_depth -= 1
            # Check for output declaration of old_token
            if re.search(rf'\boutput\b.*\b{re.escape(old_token)}\b', line):
                count += 1
            # Port list closed when depth returns to 0 after opening
            if paren_depth == 0 and in_module:
                header_done = True

        if in_module and re.match(r'^endmodule\b', line.strip()):
            in_module = False
            if count:
                break  # Found — stop scanning

    return count


def main():
    parser = argparse.ArgumentParser(description='GAP-15 is_output_port pre-check')
    parser.add_argument('--rtl-diff', required=True, help='eco_rtl_diff.json path')
    parser.add_argument('--ref-dir',  required=True, help='TileBuilder REF_DIR')
    parser.add_argument('--output',   required=True, help='Output JSON path')
    args = parser.parse_args()

    rtl_diff = json.loads(Path(args.rtl_diff).read_text())
    results = {}

    for change in rtl_diff.get('changes', []):
        if change.get('change_type') not in ('and_term', 'new_logic', 'wire_swap'):
            continue

        old_token = change.get('old_token', '')
        if not old_token:
            continue

        module_name = change.get('module_name', '')
        # Try RTL source check
        rtl_check = 0
        rtl_file = find_rtl_file(args.ref_dir, module_name)
        if rtl_file:
            rtl_check = grep_count(
                rf'^\s*output\b.*\b{re.escape(old_token)}\b',
                rtl_file, decompress=False
            )

        # Gate-level PreEco check
        gatelvl_check = check_gatelvl_output_port(old_token, module_name, args.ref_dir)

        is_output_port = (rtl_check >= 1) or (gatelvl_check >= 1)
        strategy = 'module_port_direct_gating' if is_output_port else 'proceed_to_selection'

        results[old_token] = {
            'is_output_port':   is_output_port,
            'rtl_check':        rtl_check,
            'gatelvl_check':    gatelvl_check,
            'strategy':         strategy,
            'declaring_module': module_name,
            'change_type':      change.get('change_type', '')
        }

        print(f"GAP15: {old_token:40s}  rtl={rtl_check}  gatelvl={gatelvl_check}"
              f"  → {strategy}")

    Path(args.output).write_text(json.dumps(results, indent=2))

    # Write launch marker — agent includes this in Step 3 RPT to prove script ran
    marker_lines = [
        f"ECO_SCRIPT_LAUNCHED: eco_gap15_check.py",
        f"  output:   {args.output}",
        f"  changes:  {len(results)}",
    ]
    for tok, r in results.items():
        marker_lines.append(
            f"  {tok}: is_output_port={r['is_output_port']}  rtl={r['rtl_check']}  "
            f"gatelvl={r['gatelvl_check']}  → {r['strategy']}"
        )
    marker = '\n'.join(marker_lines)
    print(marker)

    # Also write to sidecar marker file for RPT to include
    Path(args.output.replace('.json', '_marker.txt')).write_text(marker + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
