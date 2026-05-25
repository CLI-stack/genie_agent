#!/usr/bin/env python3
"""
eco_resolve_bus_width.py — Resolve the integer width of a bus register macro or range expr.

Used by eco_emit_dff_entry.py (via --bus-width) and eco_netlist_studier when
is_bus_dff=true to determine how many individual DFF cells need to be emitted.

Two resolution strategies (tried in order):
  1. Grep SynRtl *.v and *.vh for  `define <MACRO> <hi>:<lo>
     or `define <MACRO_WIDTH> <N>  to compute N = hi - lo + 1.
  2. Fallback: count distinct bit indices of <signal> in PreEco Synthesize.v.gz
     (e.g. wdbptr_org0_d1[0..7] → 8 bits).

Usage:
    python3 eco_resolve_bus_width.py \\
        --macro         UMC__WDBPTR_RANGE   # macro name (e.g. from `reg [`MACRO] sig`)
        --signal        wdbptr_org0_d1      # base signal name (for fallback)
        --rtl-dir       <REF_DIR>/data/SynRtl
        --preeco-synth  <REF_DIR>/data/PreEco/Synthesize.v.gz
        --output        data/<TAG>_eco_bus_width_<signal>.json

Exit: 0 = resolved, 1 = could not determine width (outputs width: null)
"""
import argparse
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def resolve_from_defines(macro: str, rtl_dir: str) -> int | None:
    """Grep RTL source files for `define MACRO hi:lo or `define MACRO_WIDTH N."""
    if not macro:
        return None

    # Detect literal range format already resolved by the analyzer: "hi:lo" or "hi : lo"
    # e.g. bus_width_expr="6:0" → N = 6-0+1 = 7.  No file grep needed.
    m_range = re.match(r'^\s*(\d+)\s*:\s*(\d+)\s*$', macro)
    if m_range:
        hi, lo = int(m_range.group(1)), int(m_range.group(2))
        return hi - lo + 1

    if not rtl_dir:
        return None

    rtl_path = Path(rtl_dir)
    if not rtl_path.is_dir():
        return None

    # Patterns to try in order:
    # 1. `define MACRO hi:lo  → width = hi - lo + 1
    # 2. `define MACRO_WIDTH N → width = N
    width_macro = macro.rstrip('_RANGE').rstrip('_range') + '_WIDTH'
    patterns = [
        (macro,       r'`define\s+' + re.escape(macro)       + r'\s+(\d+)\s*:\s*(\d+)', 'range'),
        (width_macro, r'`define\s+' + re.escape(width_macro) + r'\s+(\d+)',              'width'),
    ]

    # Collect all .v and .vh files
    files = list(rtl_path.glob('*.v')) + list(rtl_path.glob('*.vh')) + list(rtl_path.glob('*.h'))

    for _name, pattern, kind in patterns:
        for fpath in files:
            try:
                text = fpath.read_text(errors='replace')
            except Exception:
                continue
            m = re.search(pattern, text)
            if m:
                if kind == 'range':
                    hi, lo = int(m.group(1)), int(m.group(2))
                    return hi - lo + 1
                else:
                    return int(m.group(1))
    return None


def resolve_from_netlist(signal: str, preeco_synth: str) -> int | None:
    """Count distinct bit indices of <signal> in PreEco Synthesize.v.gz."""
    if not signal or not preeco_synth or not Path(preeco_synth).is_file():
        return None
    try:
        # zgrep for all occurrences like signal[N] and collect unique N values
        cmd = (
            f'zcat {preeco_synth} | '
            f'grep -oP \'{re.escape(signal)}\\[\\K\\d+(?=\\])\' | '
            f'sort -nu'
        )
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        bits = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().isdigit()]
        if bits:
            return len(bits)
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--macro',        default='', help='Macro name from `reg [`MACRO] sig`')
    ap.add_argument('--signal',       default='', help='Base signal name for netlist fallback')
    ap.add_argument('--rtl-dir',      default='', help='SynRtl directory containing define files')
    ap.add_argument('--preeco-synth', default='', help='Path to PreEco/Synthesize.v.gz')
    ap.add_argument('--output',       required=True)
    args = ap.parse_args()

    width = None
    method = None

    # Strategy 1: grep defines
    if args.macro and args.rtl_dir:
        w = resolve_from_defines(args.macro, args.rtl_dir)
        if w and w > 0:
            width = w
            method = 'macro_grep'

    # Strategy 2: count bit indices in netlist
    if width is None and args.signal and args.preeco_synth:
        w = resolve_from_netlist(args.signal, args.preeco_synth)
        if w and w > 0:
            width = w
            method = 'sibling_bit_count'

    result = {
        'macro':             args.macro,
        'signal':            args.signal,
        'width':             width,
        'resolution_method': method,
        'resolved':          width is not None and width > 1,
    }

    Path(args.output).write_text(json.dumps(result, indent=2))
    print(f'ECO_SCRIPT_LAUNCHED: eco_resolve_bus_width.py')
    print(f'  macro={args.macro!r}  signal={args.signal!r}  width={width}  method={method}')

    sys.exit(0 if result['resolved'] else 1)


if __name__ == '__main__':
    main()
