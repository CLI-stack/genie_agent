#!/usr/bin/env python3
"""
eco_build_shim_refdir.py — Build a shim TileBuilder-style ref_dir from direct
explicit inputs (RTL before/after + netlist paths), so SIMPLE mode can run the
existing flow unchanged (no TileBuilder run required).

Synthesize is mandatory; PrePlace + Route are OPTIONAL — omit them for a
Synthesize-only run (only the stages provided are materialised in the shim).

Every ECO script/MD reads the fixed layout:
    <ref_dir>/data/PreEco/SynRtl/                          (RTL before)
    <ref_dir>/data/SynRtl/                                 (RTL after)
    <ref_dir>/data/PreEco/{Synthesize,PrePlace,Route}.v.gz (PreEco netlists)
    <ref_dir>/data/PostEco/{...}.v.gz                      (patched here)
    <ref_dir>/revrc.main                                   (TileBuilder marker)

This script materialises that layout by SYMLINKING the user's RTL + netlist paths
into a shim dir and COPYING the netlists into PostEco (the copies get patched;
the user's originals stay read-only). RTL_BEFORE / RTL_AFTER may each be a
directory (symlinked as-is) OR a single .v file (wrapped in a 1-file dir under a
shared basename so `diff -rqw` pairs them).

Usage:
    python3 eco_build_shim_refdir.py \
        --rtl-before      <path|dir> \
        --rtl-after       <path|dir> \
        --netlist-synth   <Synthesize.v.gz> \
        [--netlist-preplace <PrePlace.v.gz>] \   # optional
        [--netlist-route   <Route.v.gz>] \       # optional
        --tag <TAG> --workdir <dir>

Prints exactly one line: `SHIM_REF_DIR=<abspath>` on success. Exit 0 = OK, 2 = bad input.
"""

import argparse
import os
import shutil
import sys


def _abort(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _link_rtl(src, dest_dir, shared_name):
    """Materialise an RTL side (before/after) at dest_dir.
    - src is a directory  -> symlink the directory AS dest_dir.
    - src is a file       -> mkdir dest_dir, symlink the file inside as shared_name.
    """
    src = os.path.abspath(src)
    if os.path.isdir(src):
        os.symlink(src, dest_dir)                       # data/[PreEco/]SynRtl -> <rtl dir>
    elif os.path.isfile(src):
        os.makedirs(dest_dir, exist_ok=True)
        os.symlink(src, os.path.join(dest_dir, shared_name))
    else:
        _abort(f"RTL path is neither file nor dir: {src}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument('--rtl-before',      required=True)
    p.add_argument('--rtl-after',       required=True)
    p.add_argument('--netlist-synth',   required=True)
    p.add_argument('--netlist-preplace', default='',
                   help='optional — omit for a Synthesize-only run')
    p.add_argument('--netlist-route',   default='',
                   help='optional — omit for a Synthesize-only run')
    p.add_argument('--tag',             required=True)
    p.add_argument('--workdir',         required=True,
                   help='parent dir for the shim (usually the dir of the Synthesize netlist)')
    args = p.parse_args()

    # ---- validate inputs exist ----
    for label, path in (('rtl-before', args.rtl_before), ('rtl-after', args.rtl_after)):
        if not os.path.exists(path):
            _abort(f"--{label} not found: {path}")
    # Synthesize is mandatory; PrePlace/Route are optional (Synth-only run).
    netlists = {'Synthesize': args.netlist_synth}
    if args.netlist_preplace:
        netlists['PrePlace'] = args.netlist_preplace
    if args.netlist_route:
        netlists['Route'] = args.netlist_route
    for stage, nl in netlists.items():
        if not os.path.isfile(nl):
            _abort(f"--netlist-{stage.lower()} not a file: {nl}")

    # A shared basename so `diff -rqw` pairs before/after when they are single files.
    # Prefer the AFTER file's basename (the edited module); fall back for dir inputs.
    after_is_file = os.path.isfile(args.rtl_after)
    shared_name = os.path.basename(os.path.abspath(args.rtl_after)) if after_is_file else 'rtl.v'

    # ---- build the shim ----
    shim = os.path.abspath(os.path.join(args.workdir, f'ECO_SIMPLE_{args.tag}'))
    if os.path.exists(shim):
        _abort(f"shim dir already exists (pick a fresh tag/workdir): {shim}")
    os.makedirs(os.path.join(shim, 'data', 'PreEco'), exist_ok=True)
    os.makedirs(os.path.join(shim, 'data', 'PostEco'), exist_ok=True)

    # TileBuilder marker (passes eco_analyze.csh)
    open(os.path.join(shim, 'revrc.main'), 'w').close()

    # RTL before -> data/PreEco/SynRtl ; RTL after -> data/SynRtl
    _link_rtl(args.rtl_before, os.path.join(shim, 'data', 'PreEco', 'SynRtl'), shared_name)
    _link_rtl(args.rtl_after,  os.path.join(shim, 'data', 'SynRtl'),          shared_name)

    # PreEco netlists = symlinks (read-only originals); PostEco = real copies (to be patched)
    for stage, nl in netlists.items():
        nl = os.path.abspath(nl)
        os.symlink(nl, os.path.join(shim, 'data', 'PreEco', f'{stage}.v.gz'))
        shutil.copy(nl, os.path.join(shim, 'data', 'PostEco', f'{stage}.v.gz'))

    print(f"SHIM_REF_DIR={shim}")
    sys.exit(0)


if __name__ == '__main__':
    main()
