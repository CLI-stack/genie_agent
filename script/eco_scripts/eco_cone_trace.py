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
      -> same-module mode: print `POLARITY=TRUE|INVERTED|UNDETERMINED inv=<n> reached=<net>`.
  polarity --netlist <v|v.gz> --module M --target <net> [--instance-scope "A/B"]  (no --ref)
      -> cross-module mode: auto-hops into the parent instantiation whenever the
         walk dead-ends at a bare primary-input port of M, instead of requiring
         the caller to already know the true reference net. --instance-scope
         (root-to-leaf, last segment = M's own instance name) lets it pick the
         SPECIFIC instantiation when M's type is instantiated more than once.
         A same-module real-gate terminal is trusted; one reached AFTER
         crossing >=1 module boundary is NOT (reports UNDETERMINED) — a plain
         buffer can precede a further inversion this bounded walk can't rule out.
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


_INPUT_PORT_DECL_RE = re.compile(r'^\s*input\s+(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*[;,]', re.MULTILINE)
_XMOD_PORT_RE = re.compile(r'\.\s*([A-Za-z_]\w*)\s*\(')


def module_primary_inputs(body):
    """{port_name: bit_width} from this module's own `input [MSB:LSB] name;`
    declarations (width=1 for scalar ports)."""
    ins = {}
    for m in _INPUT_PORT_DECL_RE.finditer(body):
        msb, lsb, name = m.group(1), m.group(2), m.group(3)
        ins[name] = (abs(int(msb) - int(lsb)) + 1) if msb is not None else 1
    return ins


def _parse_port_connections_xmod(block):
    """`.port(expr), ...` -> {port: expr}, balanced-paren aware. None on any
    parse failure (caller must fail closed to UNDETERMINED, never guess)."""
    conns = {}
    i, n = 0, len(block)
    while i < n:
        m = _XMOD_PORT_RE.search(block, i)
        if not m:
            break
        port = m.group(1)
        j = m.end()
        depth = 1
        start = j
        while j < n and depth > 0:
            if block[j] == '(':
                depth += 1
            elif block[j] == ')':
                depth -= 1
            j += 1
        if depth != 0:
            return None
        conns[port] = block[start:j - 1].strip()
        i = j
    return conns if conns else None


def _resolve_bit_in_expr_xmod(expr, bit_index, total_width):
    """Resolve a single bit out of a parent-scope port-connection expression
    (bare identifier, or a `{a,b,c}` MSB-first concatenation). None if
    ambiguous/width-mismatched — never guess."""
    expr = expr.strip()
    if total_width <= 1:
        return expr or None
    if expr.startswith('{') and expr.endswith('}'):
        inner = expr[1:-1]
        items, depth, cur = [], 0, ''
        for ch in inner:
            if ch in '{(':
                depth += 1; cur += ch
            elif ch in '})':
                depth -= 1; cur += ch
            elif ch == ',' and depth == 0:
                items.append(cur.strip()); cur = ''
            else:
                cur += ch
        if cur.strip():
            items.append(cur.strip())
        if len(items) != total_width:
            return None
        idx_from_left = (total_width - 1) - bit_index
        return items[idx_from_left] if 0 <= idx_from_left < len(items) else None
    if re.match(r'^[A-Za-z_]\w*$', expr):
        return f'{expr}[{bit_index}]'
    return None


def find_parent_instantiation_xmod(mods, host_module, expected_inst_name=None):
    """Find the instantiation of `host_module` (or its `_0` Route-uniquified
    variant) as a child inside some OTHER already-parsed module body. Returns
    (parent_module, {port: expr}) or None if zero, ambiguous, or unparseable
    — never guess. `expected_inst_name`: prefer the occurrence whose OWN
    instance name matches (from the caller's instance_scope hierarchy path) —
    a module type can legitimately be instantiated more than once with
    genuinely different wiring at each site; blind scanning can't tell those
    apart, but the caller usually knows which specific instance it means."""
    candidates = [host_module, f'{host_module}_0']
    pat = re.compile(r'(?m)^\s*(' + '|'.join(re.escape(c) for c in candidates) +
                      r')\s+([A-Za-z_]\w*)\s*\(')
    results = []
    for parent_name, body in mods.items():
        for m in pat.finditer(body):
            inst_name = m.group(2)
            open_pos = m.end() - 1
            depth = 0; close_pos = -1
            for j in range(open_pos, len(body)):
                c = body[j]
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        close_pos = j; break
            if close_pos < 0:
                continue
            block = body[open_pos + 1:close_pos]
            results.append((parent_name, inst_name, block))
    if not results:
        return None

    def _pick(cands):
        first_parent, _first_inst, first_block = cands[0]
        if len(cands) > 1 and ({b for _, _, b in cands[1:]} - {first_block}):
            return None   # ambiguous: multiple instantiations with different wiring
        conns = _parse_port_connections_xmod(first_block)
        if conns is None:
            return None
        return (first_parent, conns)

    if expected_inst_name:
        by_name = [r for r in results if r[1] == expected_inst_name]
        if by_name:
            return _pick(by_name)
        # No occurrence carries the expected instance name (naming drift) —
        # fall back to the name-agnostic scan rather than failing immediately.
    return _pick(results)


def trace_polarity_xmod(net, host_module, mods, instance_scope=None,
                         max_hops=400, max_module_hops=5):
    """Cross-module polarity trace with NO explicit --ref needed: walks the
    buf/inv chain, and when it dead-ends at a bare PRIMARY INPUT of the
    current module, hops into the ACTUAL PARENT instantiation (found in the
    same netlist text) and continues — across as many module boundaries as
    needed — until reaching a real register terminal, or a genuine,
    structurally-undecidable dead end. `instance_scope`: the study entry's own
    hierarchy path (e.g. "ARB/STGBUF", root-to-leaf, last segment = this
    module's own instance name) — consumed one segment per hop so the parent
    search can pick the SPECIFIC instantiation this leaf belongs to, instead
    of guessing when the module type is instantiated more than once.

    Returns (verdict, parity, terminal_desc); verdict in
    TRUE|INVERTED|UNDETERMINED. A real (non-buf/inv) gate terminal is only
    trustworthy if reached WITHOUT ever crossing a module boundary — reaching
    one AFTER at least one hop is reported UNDETERMINED (confirmed on real
    silicon data: a non-inverting buffer can sit in front of a further,
    hidden inversion this bounded walk cannot structurally rule out).
    """
    cur_net, cur_module = L._norm(net), host_module
    total_parity = 0
    module_hops = 0
    visited = set()
    scope_segs = [s for s in (instance_scope or '').split('/') if s]
    while True:
        body = mods.get(cur_module) or mods.get(f'{cur_module}_0')
        if body is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (module-not-found)')
        insts = L.parse_instances(body)
        driver, _consumers, _reg_dpins = L.build_graph(insts)
        reg_out_nets = {}
        for it in insts:
            if L.is_reg_inst(it['cell'], it['pins']):
                for pin, nets in it['pins'].items():
                    if L._OUT_PIN_RE.match(pin) and pin.startswith('Q'):
                        for n in nets:
                            reg_out_nets[L._norm(n)] = it['inst']
        primary_inputs = module_primary_inputs(body)

        n = cur_net
        local_parity = 0
        kind, kind_extra = None, None
        for _ in range(max_hops):
            base_m = re.match(r'^([A-Za-z_]\w*)(?:\[(\d+)\])?$', n)
            base_name = base_m.group(1) if base_m else n
            bit_str = base_m.group(2) if base_m else None
            if n in reg_out_nets:
                kind, kind_extra = 'dff', reg_out_nets[n]; break
            if n in primary_inputs or base_name in primary_inputs:
                kind, kind_extra = 'primary_input', (base_name, bit_str); break
            d = driver.get(n)
            if not d:
                kind = 'unresolved'; break
            # Only continue through a recognized INVERTER, not any
            # `d['bufinv']` cell — that flag also covers non-inverting
            # buffers (BUFF*/CKBUF*/etc, per eco_lol_impact._BUFINV_RE).
            # Empirically (real JIRA-11233 data) continuing through a plain
            # buffer can walk onto an unrelated local inverter that isn't
            # actually in this net's true causal path, producing a WRONG
            # verdict. Stopping at the first non-inverter cell is the
            # conservative, correct choice — see the cross-module
            # comb-terminal handling below for how that's still made safe.
            if not (_is_inverter(d['cell']) and len(d['inputs']) == 1):
                kind, kind_extra = 'comb', d['cell']; break
            local_parity ^= 1
            n = L._norm(d['inputs'][0])
        else:
            kind = 'max_hops'
        total_parity ^= local_parity

        if kind == 'dff':
            return ('TRUE' if total_parity == 0 else 'INVERTED',
                    total_parity, f'{cur_module}.dff_{kind_extra}')
        if kind in ('unresolved', 'max_hops'):
            return ('UNDETERMINED', total_parity, f'{cur_module}:{n} ({kind})')
        if kind == 'comb':
            if module_hops > 0:
                return ('UNDETERMINED', total_parity,
                        f'{cur_module}.comb_{kind_extra} (cross-module comb terminal, unverifiable)')
            return ('TRUE' if total_parity == 0 else 'INVERTED',
                    total_parity, f'{cur_module}.comb_{kind_extra}')
        # primary_input -> hop into the parent instantiation
        base_name, bit_str = kind_extra
        key = (cur_module, n)
        if key in visited or module_hops >= max_module_hops:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{n} (hop-limit/loop)')
        visited.add(key)
        expected_inst = scope_segs.pop() if scope_segs else None
        found = find_parent_instantiation_xmod(mods, cur_module, expected_inst)
        if found is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{n} (no-unique-parent)')
        parent_module, conns = found
        expr = conns.get(base_name)
        if expr is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{n} (port-not-connected)')
        if bit_str is not None:
            total_width = primary_inputs.get(base_name, 1)
            if total_width < 2:
                return ('UNDETERMINED', total_parity, f'{cur_module}:{n} (width-unresolved)')
            upstream = _resolve_bit_in_expr_xmod(expr, int(bit_str), total_width)
        else:
            upstream = expr
        if not upstream:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{n} (bit-resolve-failed)')
        cur_net, cur_module = re.sub(r'\s+', '', upstream), parent_module
        module_hops += 1


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
    ap.add_argument('--instance-scope', default=None,
                     help='Slash-separated hierarchy path (root-to-leaf, last segment = '
                          '--module\'s own instance name), e.g. "ARB/STGBUF". Only used for '
                          '`polarity` when --ref is omitted (cross-module mode) — lets the '
                          'walk pick the SPECIFIC parent instantiation instead of failing '
                          'closed whenever --module is instantiated more than once elsewhere.')
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
        if not args.target:
            sys.exit("ERROR: polarity needs --target")
        if args.ref:
            # Same-module mode (unchanged): explicit known-good reference net(s).
            refs = [r.strip() for r in args.ref.split(',') if r.strip()]
            verdict, inv, reached = trace_polarity(args.target, refs, driver)
            res = {'op': 'polarity', 'target': args.target, 'refs': refs,
                   'polarity': verdict, 'inversions': inv, 'reached': reached}
        else:
            # Cross-module mode: no explicit ref needed — auto-hops across
            # module boundaries (via --module + --instance-scope) whenever the
            # walk dead-ends at a bare primary-input port, discovering its own
            # terminal (register, or an in-module-only real gate) instead of
            # requiring the caller to already know where the true source is.
            if not args.module:
                sys.exit("ERROR: polarity without --ref (cross-module mode) needs --module")
            full_text = _read(args.netlist)
            all_mods = L.parse_modules(full_text)
            verdict, inv, reached = trace_polarity_xmod(
                args.target, args.module, all_mods, instance_scope=args.instance_scope)
            res = {'op': 'polarity', 'target': args.target, 'module': args.module,
                   'instance_scope': args.instance_scope,
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
