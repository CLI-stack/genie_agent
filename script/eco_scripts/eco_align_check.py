#!/usr/bin/env python3
"""eco_align_check.py — ENGINE-AGREEMENT check for the intent-C candidate-and-verify
oracle. Confirms the two independent evaluation engines agree on the SAME design:

  DUT  = eco_netlist_sim cone-simulation of a registered signal's D-net(s) in the
         PreEco gate netlist (the flop's next-state, combinationally).
  REF  = eco_synth_selfcheck._Interp next-state of that signal from the PreEco RTL
         always-block (registered: HOLD default via the ternary `: sig` feedback).

Because both sides describe the pre-ECO function, a correct pair of engines must
agree on EVERY random vector (0 mismatches). Any mismatch is an engine bug, not an
ECO difference — this is what makes the production emitter's verify step trustworthy.

The D-net<->bit map is auto-discovered from the (possibly multi-bit banked) flop whose
Q pins drive signal[b]. Leaves are shared bit-addressably so the same random vector
feeds both engines (netlist leaf `base[i]` <-> interp bus `base` bit i).

Usage:
  python3 eco_align_check.py --ref-dir <R> --netlist-module <M> --signal <S> \
      [--rtl-module <base>] [--stage Synthesize] [--n 3000] [--seed 1]
"""
import argparse, re, random, sys
import eco_netlist_sim as ns
from eco_cone_rebuild import parse_always
from eco_extract_pf_condition import resolve_rtl
from eco_rtl_config import RtlConfig
from eco_rtl_synth import parse_expr, build_width_map
from eco_synth_selfcheck import _Interp

_OUTQ = re.compile(r'^Q(\d*)$')


def discover_flop(body, signal):
    """Find the flop instance whose Q pins drive signal[b]; return (inst, {bit: dnet}).
    Scans the RAW body: multi-bit banked flops have no truth table so they never appear
    in parse_module's `insts` — but they are exactly the registers we must map."""
    inst_re = re.compile(r'([A-Za-z][\w]*)\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*;', re.S)
    pin_re = re.compile(r'\.\s*([A-Za-z][\w]*)\s*\(\s*([^)]*?)\s*\)')
    for m in inst_re.finditer(body):
        pins = {p: n.strip() for p, n in pin_re.findall(m.group(3))}
        qbits = {}
        for p, net in pins.items():
            if not _OUTQ.match(p):
                continue
            bm = re.match(re.escape(signal) + r'\[(\d+)\]$', net)
            if bm:
                qbits[p] = int(bm.group(1))
            elif net == signal:
                qbits[p] = 0
        if not qbits:
            continue
        out = {}
        for qp, bit in qbits.items():
            dp = 'D' + qp[1:]
            if dp in pins:
                out[bit] = pins[dp]
        if out:
            return m.group(2), out
    return None, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--netlist-module', required=True)
    ap.add_argument('--signal', required=True)
    ap.add_argument('--rtl-module', default=None,
                    help='RTL base module name (default: strip tile prefix + _<i> from netlist module)')
    ap.add_argument('--stage', default='Synthesize')
    ap.add_argument('--n', type=int, default=3000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--preeco', action='store_true', default=True,
                    help='use PreEco RTL (engine-agreement; default). --no-preeco uses PostEco.')
    ap.add_argument('--dump', type=int, default=8, help='max mismatch vectors to print')
    args = ap.parse_args()

    gz = f"{args.ref_dir}/data/PreEco/{args.stage}.v.gz"
    insts, drivers = ns.parse_module(gz, args.netlist_module, ref_dir=args.ref_dir)
    if not insts:
        print(f"FAIL: netlist module {args.netlist_module!r} not found / empty."); return 2
    body = ns._module_body(gz, args.netlist_module)
    flop, dmap = discover_flop(body, args.signal)
    if not dmap:
        print(f"FAIL: no flop driving {args.signal}[b] found in {args.netlist_module!r}."); return 2
    print(f"flop {flop}: D->bit map = {dmap}")

    # netlist cones for each D-net
    cones = {}
    all_leaves = set()
    for bit, dnet in dmap.items():
        order, leaves = ns.cone_of(dnet, insts, drivers)
        cones[bit] = order
        all_leaves |= leaves
    width = max(dmap) + 1

    # RTL side
    base = args.rtl_module or re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', args.netlist_module))
    subdir = 'PreEco/SynRtl' if args.preeco else 'SynRtl'
    rtl = open(resolve_rtl(ref_dir=args.ref_dir, module=base, subdir=subdir), errors='replace').read()
    cfg = RtlConfig(args.ref_dir)
    macros = {k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None}
    wm = build_width_map(rtl, macros)
    tree = parse_always(rtl, args.signal)
    if not tree or not tree['assigns']:
        print(f"FAIL: no always-block for {args.signal} in {base} RTL."); return 2
    print(f"RTL {base}: {len(tree['assigns'])} branch(es), width={width}")

    # Ground BOTH engines at the same leaves = names present in the netlist body OR
    # RTL registers (mirrors eco_synth_selfcheck.run). The netlist D-net cone bottoms
    # out at module PIs/registers; the interp must STOP at those same names (a PI like
    # upd_active is a leaf in the netlist but a *derived* signal in the RTL, so without
    # this the interp recurses past it into signals the netlist never reaches).
    nlbody = body
    stripped = re.sub(r'//[^\n]*', ' ', re.sub(r'/\*.*?\*/', ' ', rtl, flags=re.S))
    _innl_c, _isreg_c = {}, {}

    def innl(s):
        r = _innl_c.get(s)
        if r is None:
            r = bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody)); _innl_c[s] = r
        return r

    def is_reg(nm):
        r = _isreg_c.get(nm)
        if r is None:
            r = bool(re.search(r'\b' + re.escape(nm) + r'\s*(?:\[[^\]]*\])?\s*<=', stripped)); _isreg_c[nm] = r
        return r

    def grounds(nm):
        return innl(nm) or is_reg(nm)

    # bases to randomize each vector: every RTL signal the interp may ground + netlist
    # leaf bases. width from RTL width-map, else widest netlist bit index seen.
    leaf_w = {}
    for net in all_leaves:
        if ns._is_const(net):
            continue
        bm = re.match(r'^(.*)\[(\d+)\]$', net)
        b = bm.group(1) if bm else net
        idx = int(bm.group(2)) if bm else 0
        leaf_w[b] = max(leaf_w.get(b, 0), (wm.get(b) or 0), idx + 1)
    for b, w in wm.items():
        if grounds(b):
            leaf_w[b] = max(leaf_w.get(b, 0), w or 1)
    leaf_w[args.signal] = max(leaf_w.get(args.signal, 0), width, wm.get(args.signal) or 0)

    rng = random.Random(args.seed)
    leaf_items = sorted(leaf_w.items())          # deterministic env order across runs
    mism = 0
    for it in range(args.n):
        env = {b: rng.getrandbits(w) for b, w in leaf_items}
        # netlist genv: bit-addressable
        genv = {}
        for b, w in leaf_w.items():
            for i in range(w):
                genv[f'{b}[{i}]'] = (env[b] >> i) & 1
            if w == 1:
                genv[b] = env[b] & 1
        # DUT: netlist next-state
        dut = 0
        for bit, order in cones.items():
            v = ns.simulate(order, insts, genv).get(dmap[bit], 0)
            dut |= (v & 1) << bit
        # REF: interp next-state (HOLD default, feedback grounded)
        ip = _Interp(cfg, wm, rtl, grounds, env, macros)
        ref = env[args.signal] & ((1 << width) - 1)         # HOLD
        for cond, rhs in tree['assigns']:
            if ip.path(cond):
                ref = ip.eval(parse_expr(rhs)) & ((1 << width) - 1)
        if dut != ref:
            mism += 1
            if mism <= args.dump:
                # which branch fired in interp
                fired = 'HOLD'
                for ci, (cond, rhs) in enumerate(tree['assigns']):
                    if ip.path(cond):
                        fired = f'br{ci}'
                print(f"  MISMATCH #{it}: dut={dut:0{width}b} ref={ref:0{width}b} interp_branch={fired}")
    verdict = 'PASS' if mism == 0 else 'FAIL'
    print(f"\n{args.signal}: {args.n} vectors, {mism} mismatches -> {verdict}")
    return 0 if mism == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
