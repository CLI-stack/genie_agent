#!/usr/bin/env python3
"""
eco_cone_trace.py — Fenets-free structural cone tracer + polarity resolver.

For SIMPLE mode (no Formality `find_equivalent_nets`), this resolves an RTL
signal to its real gate-level net per stage and decides its POLARITY (does the
net carry the signal or its complement) purely from netlist structure — by
counting inverters along a buffer/inverter chain back to a known-good reference
(the signal's source register Q). If the chain can't reach the reference through
pure buf/inv cells, the result is UNDETERMINED — the caller must NOT guess.

Reuses the battle-tested, complete-gate-boundary parser + graph builder from
eco_lol_impact.py (parse into net->driver / net->loads maps, register detection,
buf/inv detection). This is a general tool — no tile/JIRA/module constants.

Ops:
  resolve  --netlist <v|v.gz> [--module M] --signal <sig>
      -> print `RESOLVED_NET=<net>` or `UNRESOLVED`.
  polarity --netlist <v|v.gz> [--module M] --target <net> --ref <net1[,net2...]>
      -> print `POLARITY=TRUE|INVERTED|UNDETERMINED inv=<n> reached=<net>`.
  cone     --netlist <v|v.gz> [--module M] --net <net> --direction fanin|fanout [--depth N]
      -> print the cone net list.

Optional --output <json> writes a machine-readable result. Exit 0 = OK,
2 = UNDETERMINED/UNRESOLVED (caller should stop, not guess), 1 = error.
"""

import argparse
import gzip
import json
import os
import sys
from collections import deque
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eco_lol_impact as L   # parse_modules, parse_instances, build_graph, is_reg_inst, _is_bufinv, _norm, _OUT_PIN_RE

# Inverting subset of the buf/inv family (the rest are non-inverting buffers).
_INV_RE = re.compile(r'^(INV|CKN|CKND|CKNBD|CKINV|CLKINV)', re.I)


def _read(path):
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt', errors='replace') as f:
        return f.read()


def load(netlist, module=None):
    """Return (driver, consumers, reg_dpins, reg_out_nets, inst_outputs).
    reg_out_nets[net] = inst  (register Q/QN outputs = known startpoints)
    inst_outputs[inst] = [output nets]  (for fanout walking)."""
    text = _read(netlist)
    if module:
        mods = L.parse_modules(text)
        body = mods.get(module)
        if body is None:
            # tolerate tile-prefixed / uniquified names
            cand = [m for m in mods if m == module or m.endswith('_' + module) or module in m]
            if not cand:
                sys.exit(f"ERROR: module {module!r} not found in {netlist}")
            body = mods[sorted(cand, key=len)[0]]
        text = body
    insts = L.parse_instances(text)
    driver, consumers, reg_dpins = L.build_graph(insts)
    reg_out_nets, inst_outputs = {}, {}
    for it in insts:
        ct, inst, pins = it['cell'], it['inst'], it['pins']
        outs = []
        reg = L.is_reg_inst(ct, pins)
        for pin, nets in pins.items():
            if L._OUT_PIN_RE.match(pin):
                for n in nets:
                    outs.append(L._norm(n))
                    if reg and pin.startswith('Q'):
                        reg_out_nets[L._norm(n)] = inst
        inst_outputs[inst] = outs
    return driver, consumers, reg_dpins, reg_out_nets, inst_outputs


def _is_inverter(cell):
    return bool(cell) and bool(_INV_RE.match(cell))


def resolve_signal(sig, driver, reg_out_nets, insts_by_name):
    """RTL signal -> real gate-level net. Anchor on the source register instance
    (survives P&R renaming) when the bare name isn't a live net."""
    s = L._norm(sig)
    if s in driver or s in reg_out_nets:
        return s
    for cand in (sig, sig + '_reg'):
        pins = insts_by_name.get(cand)
        if pins:
            for pin, nets in pins.items():
                if L._OUT_PIN_RE.match(pin) and pin.startswith('Q') and nets:
                    return L._norm(nets[0])
    return None


def trace_polarity(target, refs, driver, max_hops=400):
    """Walk the buf/inv chain backward from `target`, counting inverters, until a
    net in `refs` is reached. even inversions -> TRUE, odd -> INVERTED.
    Any real (multi-input / non-buf-inv) gate, or a startpoint not in refs,
    -> UNDETERMINED (caller must not guess)."""
    net = L._norm(target)
    refset = {L._norm(r) for r in refs if r}
    inv, hops, seen = 0, 0, set()
    while hops < max_hops:
        if net in refset:
            return ('TRUE' if inv % 2 == 0 else 'INVERTED', inv, net)
        if net in seen:
            break
        seen.add(net)
        d = driver.get(net)
        if not d or not d['bufinv'] or len(d['inputs']) != 1:
            break                      # real logic / primary input / reg output -> can't chain-decide
        if _is_inverter(d['cell']):
            inv += 1
        net = L._norm(d['inputs'][0])
        hops += 1
    return ('UNDETERMINED', inv, net)


def cone(start, direction, depth, driver, consumers, inst_outputs, reg_out_nets):
    start = L._norm(start)
    seen, q = {start}, deque([(start, 0)])
    while q:
        n, dd = q.popleft()
        if depth and dd >= depth:
            continue
        if direction == 'fanin':
            d = driver.get(n)
            if not d:
                continue               # reg output / primary input -> stop
            nxt = [L._norm(i) for i in d['inputs']]
        else:  # fanout
            nxt = []
            for (inst, is_reg, _pin) in consumers.get(n, []):
                if is_reg:
                    continue           # stop at register data pins
                nxt.extend(inst_outputs.get(inst, []))
        for m in nxt:
            if m not in seen:
                seen.add(m)
                q.append((m, dd + 1))
    return sorted(seen)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('op', choices=['resolve', 'polarity', 'cone'])
    ap.add_argument('--netlist', required=True)
    ap.add_argument('--module', default=None)
    ap.add_argument('--signal')
    ap.add_argument('--target')
    ap.add_argument('--ref')
    ap.add_argument('--net')
    ap.add_argument('--direction', choices=['fanin', 'fanout'], default='fanin')
    ap.add_argument('--depth', type=int, default=0)
    ap.add_argument('--output')
    args = ap.parse_args()

    driver, consumers, reg_dpins, reg_out_nets, inst_outputs = load(args.netlist, args.module)
    # instance-name -> pins map (for register-anchored resolve)
    text = _read(args.netlist)
    if args.module:
        mods = L.parse_modules(text)
        body = mods.get(args.module) or next((mods[m] for m in mods if args.module in m), text)
        text = body
    insts_by_name = {it['inst']: it['pins'] for it in L.parse_instances(text)}

    res, code = {}, 0
    if args.op == 'resolve':
        if not args.signal:
            sys.exit("ERROR: resolve needs --signal")
        net = resolve_signal(args.signal, driver, reg_out_nets, insts_by_name)
        res = {'op': 'resolve', 'signal': args.signal, 'net': net}
        if net:
            print(f"RESOLVED_NET={net}")
        else:
            print("UNRESOLVED"); code = 2
    elif args.op == 'polarity':
        if not (args.target and args.ref):
            sys.exit("ERROR: polarity needs --target and --ref")
        refs = [r.strip() for r in args.ref.split(',') if r.strip()]
        verdict, inv, reached = trace_polarity(args.target, refs, driver)
        res = {'op': 'polarity', 'target': args.target, 'refs': refs,
               'polarity': verdict, 'inversions': inv, 'reached': reached}
        print(f"POLARITY={verdict} inv={inv} reached={reached}")
        if verdict == 'UNDETERMINED':
            code = 2
    else:  # cone
        if not args.net:
            sys.exit("ERROR: cone needs --net")
        nets = cone(args.net, args.direction, args.depth, driver, consumers, inst_outputs, reg_out_nets)
        res = {'op': 'cone', 'net': args.net, 'direction': args.direction,
               'depth': args.depth, 'cone_size': len(nets), 'cone': nets}
        print(f"CONE_SIZE={len(nets)} ({args.direction})")
        for n in nets[:200]:
            print(f"  {n}")

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(res, f, indent=2)
    sys.exit(code)


if __name__ == '__main__':
    main()
