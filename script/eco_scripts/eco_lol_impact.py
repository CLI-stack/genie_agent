#!/usr/bin/env python3
"""
eco_lol_impact.py — ADVISORY Step-3 check: Levels-of-Logic (LOL) impact of an ECO.

For every register endpoint the ECO touches, it measures the combinational depth
(Levels of Logic) feeding that register's data pin BEFORE the ECO vs AFTER (with the
study's planned gate insertions + rewires spliced in), on the PreEco *Synthesize*
netlist only, and estimates the added delay.

Levels of Logic = number of combinational cells in series on the worst (longest) path
from a startpoint (register Q, primary input, or constant) to the endpoint.
  - INVERTERS and BUFFERS are EXCLUDED from the count (they pass through as 0 levels).
  - Registers' Q outputs, module input ports, and 1'b0/1'b1 are startpoints (LOL 0).

This check is ADVISORY: it always writes a report and never fails the flow. ECOs
legitimately add logic depth; the point is visibility into timing risk.

Usage:
    python3 script/eco_scripts/eco_lol_impact.py \
        --study    data/<TAG>_eco_preeco_study.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --output   data/<TAG>_eco_lol_impact.json \
        [--ns-per-level 0.08]

Exit: always 0 (advisory). Writes <output> + <output:_marker.txt>.
"""
import argparse, gzip, json, os, re, sys
from pathlib import Path

# ── Cell classification ──────────────────────────────────────────────────────
# Register (sequential) cell families — their Q/QN outputs are path startpoints.
# Includes MB* multi-bit banked flops (e.g. MB8SRLSDFQTX...).
_REG_RE = re.compile(r'^(MB\d|SDFF|SDFQ|SDFR|SDFS|SDF|DFQ|DFF|DFCN|DFCNQ|LAT|LHQ|LHR|SAFF|SEDF)', re.I)
# Inverter / buffer families — EXCLUDED from the level count (pass-through).
_BUFINV_RE = re.compile(r'^(INV|BUF|BUFF|BUFT|CKBD|CKBUF|CKND|CKNBD|CKINV|CKLN|DEL|DLY|CLKBUF|CLKINV|HOLD|LVLL|LVLH)', re.I)
# Output pin names (everything else on a cell is treated as an input).
# Q\d?/QN\d? covers MB flop banked outputs Q1..Q8.
_OUT_PIN_RE = re.compile(r'^(ZN?\d?|CON?|S|SO|Q\d?|QN\d?|QB)$')
# Register data-pin names (endpoints) — D, D1..D8 for MB flops.
_DPIN_RE = re.compile(r'^D\d*$')
# Clock pin names — used to identify a sequential cell by signature.
_CLK_PIN_RE = re.compile(r'^(CP|CK|CLK|CPN)$')


def is_reg_inst(cell_type, pins):
    """A cell is sequential if its family matches OR it has a clock pin AND a D pin
    (catches MB banked flops and odd library names; excludes clock gates/latches w/o D)."""
    if _REG_RE.match(cell_type or ''):
        return True
    has_clk = any(_CLK_PIN_RE.match(p) for p in pins)
    has_d = any(_DPIN_RE.match(p) for p in pins)
    return has_clk and has_d

_CONST_RE = re.compile(r"^\d*'b[01xXzZ]+$|^1'b[01]$")

_LOOP_CAP = 500  # safety cap on recursion depth (combinational loops / pathological)


def _is_reg(ct):      return bool(ct) and bool(_REG_RE.match(ct))
def _is_bufinv(ct):   return bool(ct) and bool(_BUFINV_RE.match(ct))
def _is_const(net):   return bool(net) and bool(_CONST_RE.match(net.strip()))


def _norm(net):
    """Normalize a net token for matching (strip spaces). Keeps bracket form."""
    return net.strip() if net else net


def _flat_bracket_variants(net):
    """Yield the net plus its flat<->bracket variants so study `X_3_` matches netlist `X[3]`."""
    if not net:
        return []
    out = {net}
    m = re.match(r'^(.*)\[(\d+)\]$', net)
    if m:
        out.add(f"{m.group(1)}_{m.group(2)}_")
    m2 = re.match(r'^(.*)_(\d+)_$', net)
    if m2:
        out.add(f"{m2.group(1)}[{m2.group(2)}]")
    return list(out)


# ── Netlist parsing ──────────────────────────────────────────────────────────
def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', ' ', text)
    return text


def parse_modules(text):
    """Return {module_name: body_text (from 'module' to 'endmodule')}."""
    mods = {}
    for m in re.finditer(r'(?ms)^module\s+(\S+).*?^endmodule', text):
        mods[m.group(1)] = m.group(0)
    return mods


_INST_START_RE = re.compile(r'(?m)^\s*([A-Za-z_]\w*)\s+([A-Za-z_][\w\\]*)\s*\(')


def parse_instances(body):
    """Parse a module body into instances: list of {cell,inst,pins:{pin:[nets]}}.
    Skips module header line and non-instance statements (wire/input/assign/...)."""
    body = _strip_comments(body)
    insts = []
    KW = {'module', 'input', 'output', 'inout', 'wire', 'tri', 'reg', 'assign',
          'endmodule', 'parameter', 'localparam', 'generate', 'endgenerate',
          'supply0', 'supply1'}
    for m in _INST_START_RE.finditer(body):
        cell, inst = m.group(1), m.group(2)
        if cell in KW:
            continue
        # balance parens from the '(' at m.end()-1
        i = m.end() - 1
        depth = 0
        j = i
        n = len(body)
        while j < n:
            c = body[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        port_txt = body[i + 1:j]
        pins = {}
        for pm in re.finditer(r'\.\s*(\w+)\s*\(\s*([^)]*?)\s*\)', port_txt):
            pin = pm.group(1)
            val = pm.group(2).strip()
            if not val:
                nets = []
            elif val.startswith('{'):
                nets = [x.strip() for x in val.strip('{}').split(',') if x.strip()]
            else:
                nets = [val]
            pins[pin] = [_norm(x) for x in nets]
        insts.append({'cell': cell, 'inst': inst, 'pins': pins})
    return insts


def build_graph(insts):
    """From instances build:
       driver[net]   = {'inst','cell','bufinv','inputs':[nets]}  (combinational only)
       consumers[net]= [(inst, is_reg, pin)]
       reg_dpins     = {inst: {pin: net}}  for register data pins
    Register outputs are intentionally NOT in driver (=> they are startpoints)."""
    driver, consumers, reg_dpins = {}, {}, {}
    for it in insts:
        ct, inst, pins = it['cell'], it['inst'], it['pins']
        reg = is_reg_inst(ct, pins)
        out_nets, in_nets = [], []
        for pin, nets in pins.items():
            if _OUT_PIN_RE.match(pin) and not (reg and pin in ('D',)):
                # Q/QN of a reg are outputs (startpoints); Z/ZN/CO/S of comb are outputs
                out_nets.extend([(pin, nn) for nn in nets])
            else:
                in_nets.extend(nets)
                if reg and _DPIN_RE.match(pin):
                    for nn in nets:
                        reg_dpins.setdefault(inst, {})[pin] = nn
        for nn in set(in_nets):
            for pin, nets in pins.items():
                if nn in nets:
                    consumers.setdefault(nn, []).append((inst, reg, pin))
        if not reg:
            for (_pin, onet) in out_nets:
                driver[onet] = {'inst': inst, 'cell': ct,
                                'bufinv': _is_bufinv(ct), 'inputs': list(set(in_nets))}
    return driver, consumers, reg_dpins


def lol(net, driver, memo, stack=None):
    """Longest combinational depth to `net`. Inverters/buffers count 0."""
    if net is None:
        return 0
    net = _norm(net)
    if net in memo:
        return memo[net]
    if _is_const(net) or net not in driver:
        memo[net] = 0
        return 0
    if stack is None:
        stack = set()
    if net in stack or len(stack) > _LOOP_CAP:
        return 0  # loop guard
    stack.add(net)
    d = driver[net]
    ins = d['inputs']
    if not ins:
        val = 0
    else:
        sub = max((lol(i, driver, memo, stack) for i in ins), default=0)
        val = sub if d['bufinv'] else sub + 1
    stack.discard(net)
    memo[net] = val
    return val


def worst_startpoint(net, driver, memo):
    """Trace the longest path back to name its startpoint (best-effort)."""
    cur = _norm(net)
    guard = 0
    while cur in driver and guard < _LOOP_CAP:
        d = driver[cur]
        if not d['inputs']:
            return cur
        cur = max(d['inputs'], key=lambda i: lol(i, driver, memo))
        guard += 1
    return cur


# ── AFTER-graph construction from the study ──────────────────────────────────
def _syn_pc(entry):
    """Synthesize-stage pin->net map for a gate entry."""
    pcs = entry.get('port_connections_per_stage') or {}
    return pcs.get('Synthesize') or entry.get('port_connections') or {}


def apply_study_to_module(insts, mod_entries):
    """Return a NEW instance list = insts + ECO gates, with rewires applied.
    mod_entries = study entries whose Synthesize module is this module."""
    after = [dict(it, pins=dict(it['pins'])) for it in insts]
    by_inst = {it['inst']: it for it in after}
    # 1) add new_logic_gate / new_logic_dff cells
    for e in mod_entries:
        ct = e.get('change_type')
        if ct in ('new_logic_gate', 'new_logic_dff'):
            pc = _syn_pc(e)
            if not pc:
                continue
            pins = {p: ([v] if not str(v).startswith('{')
                        else [x.strip() for x in str(v).strip('{}').split(',')])
                    for p, v in pc.items()}
            after.append({'cell': e.get('cell_type') or e.get('dff_cell_type') or '',
                          'inst': e.get('instance_name', ''), 'pins': pins})
    # 2) apply rewires: re-point cell.pin old->new
    for e in mod_entries:
        if e.get('change_type') != 'rewire':
            continue
        cps = e.get('cell_name_per_stage') or {}
        cell = cps.get('Synthesize') or e.get('cell_name')
        pin = (e.get('pin_per_stage') or {}).get('Synthesize') or e.get('pin')
        new = (e.get('new_net_per_stage') or {}).get('Synthesize') or e.get('new_net')
        if not (cell and pin and new is not None):
            continue
        tgt = by_inst.get(cell)
        if tgt and pin in tgt['pins']:
            tgt['pins'][pin] = [_norm(new)]
    return after


def module_of(entry):
    mps = entry.get('module_name_per_stage') or {}
    return mps.get('Synthesize') or entry.get('module_name') or ''


def find_reg_dpin_endpoints(mod_entries, before_regd, after_insts):
    """Determine (reg_inst, pin) endpoints affected by the ECO in this module.
    Direct: rewire on a D-pin, or a new_logic_dff. Implicit: forward-trace ECO nets."""
    after_regd = {}
    for it in after_insts:
        if is_reg_inst(it['cell'], it['pins']):
            for pin, nets in it['pins'].items():
                if _DPIN_RE.match(pin):
                    for nn in nets:
                        after_regd.setdefault(it['inst'], {})[pin] = nn
    # consumers in AFTER for forward tracing
    after_cons = {}
    for it in after_insts:
        rg = is_reg_inst(it['cell'], it['pins'])
        for pin, nets in it['pins'].items():
            for nn in nets:
                after_cons.setdefault(nn, []).append((it['inst'], rg, pin))
    endpoints = {}   # (inst,pin) -> {'new': net}
    eco_nets = set()
    for e in mod_entries:
        ct = e.get('change_type')
        if ct == 'rewire':
            pin = (e.get('pin_per_stage') or {}).get('Synthesize') or e.get('pin') or ''
            cps = e.get('cell_name_per_stage') or {}
            cell = cps.get('Synthesize') or e.get('cell_name') or ''
            new = (e.get('new_net_per_stage') or {}).get('Synthesize') or e.get('new_net')
            if _DPIN_RE.match(pin) and cell in after_regd:
                endpoints[(cell, pin)] = {'new': after_regd[cell].get(pin, new)}
            elif new:
                eco_nets.add(_norm(new))
        elif ct in ('new_logic_gate',):
            on = e.get('output_net')
            if on:
                eco_nets.add(_norm(on))
        elif ct == 'new_logic_dff':
            pc = _syn_pc(e)
            dnet = pc.get('D')
            endpoints[(e.get('instance_name', ''), 'D')] = {'new': dnet, 'is_new_dff': True}
    # forward-trace eco_nets to nearest register D-pins (bounded BFS)
    for start in eco_nets:
        seen, frontier, hops = set(), [start], 0
        while frontier and hops < 80:
            nxt = []
            for nn in frontier:
                for (inst, is_reg, pin) in after_cons.get(nn, []):
                    if is_reg and _DPIN_RE.match(pin):
                        endpoints.setdefault((inst, pin),
                                             {'new': after_regd.get(inst, {}).get(pin)})
                    key = (inst, pin)
                    if key in seen:
                        continue
                    seen.add(key)
                    # walk forward through this cell's outputs
                    for it in after_insts:
                        if it['inst'] == inst and not is_reg:
                            for p2, nets2 in it['pins'].items():
                                if _OUT_PIN_RE.match(p2):
                                    nxt.extend(nets2)
            frontier = nxt
            hops += 1
    return endpoints, after_regd


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--study', required=True)
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--ns-per-level', type=float, default=0.08,
                    help='estimated delay per logic level in ns (default 0.08)')
    args = ap.parse_args()

    try:
        study = json.loads(Path(args.study).read_text())
    except Exception as e:
        _emit(args, {'error': f'cannot read study: {e}'}, [])
        return 0

    gz = Path(args.ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
    if not gz.is_file():
        _emit(args, {'error': f'PreEco Synthesize not found: {gz}'}, [])
        return 0
    try:
        with gzip.open(gz, 'rt', errors='replace') as f:
            text = f.read()
    except Exception as e:
        _emit(args, {'error': f'cannot read netlist: {e}'}, [])
        return 0

    modules = parse_modules(text)
    syn_entries = study.get('Synthesize', []) if isinstance(study, dict) else []
    # group entries by their Synthesize module
    by_mod = {}
    for e in syn_entries:
        if e.get('change_type') not in ('new_logic_gate', 'new_logic_dff', 'rewire'):
            continue
        by_mod.setdefault(module_of(e), []).append(e)

    endpoints_out = []
    _mod_cache = {}

    def _resolve_body(modname):
        if modname in modules:
            return modules[modname]
        # try _0 / tile-prefixed / suffix variants
        for k in modules:
            if k == modname or k.endswith('_' + modname) or k == modname + '_0' \
               or re.sub(r'_\d+$', '', k) == modname:
                return modules[k]
        return None

    for modname, mod_entries in by_mod.items():
        body = _resolve_body(modname)
        if body is None:
            for e in mod_entries:
                endpoints_out.append({'register': None, 'module': modname,
                                      'note': f'module {modname!r} not found in Synthesize netlist',
                                      'instance': e.get('instance_name') or e.get('cell_name')})
            continue
        if modname not in _mod_cache:
            insts = parse_instances(body)
            _mod_cache[modname] = (insts, build_graph(insts))
        insts, (driver_b, cons_b, regd_b) = _mod_cache[modname]
        after_insts = apply_study_to_module(insts, mod_entries)
        driver_a, _, _ = build_graph(after_insts)
        memo_b, memo_a = {}, {}
        endpoints, after_regd = find_reg_dpin_endpoints(mod_entries, regd_b, after_insts)
        for (inst, pin), info in sorted(endpoints.items()):
            new_net = info.get('new') or (after_regd.get(inst, {}) or {}).get(pin)
            if info.get('is_new_dff'):
                before_net, b_lol = None, 0
            else:
                before_net = (regd_b.get(inst, {}) or {}).get(pin)
                if before_net is None:
                    # try flat/bracket variants against before reg map
                    before_net = new_net
                b_lol = _lol_multi(before_net, driver_b, memo_b)
            a_lol = _lol_multi(new_net, driver_a, memo_a)
            delta = a_lol - b_lol
            endpoints_out.append({
                'module': modname,
                'register': inst,
                'pin': pin,
                'net_before': before_net,
                'net_after': new_net,
                'before_lol': b_lol,
                'after_lol': a_lol,
                'delta_lol': delta,
                'est_added_delay_ns': round(delta * args.ns_per_level, 4),
                'new_register': bool(info.get('is_new_dff')),
                'worst_startpoint_after': worst_startpoint(new_net, driver_a, memo_a) if new_net else None,
            })

    deltas = [e['delta_lol'] for e in endpoints_out if 'delta_lol' in e]
    summary = {
        'tag': args.tag,
        'stage': 'Synthesize',
        'ns_per_level': args.ns_per_level,
        'inverters_buffers_excluded': True,
        'endpoints': len([e for e in endpoints_out if 'delta_lol' in e]),
        'max_delta_lol': max(deltas) if deltas else 0,
        'total_est_added_delay_ns': round(sum(max(0, d) for d in deltas) * args.ns_per_level, 4),
    }
    if deltas:
        worst = max((e for e in endpoints_out if 'delta_lol' in e), key=lambda e: e['delta_lol'])
        summary['worst_endpoint'] = f"{worst.get('register')}/{worst.get('pin')}"
        summary['worst_delta_lol'] = worst['delta_lol']
    _emit(args, summary, endpoints_out)
    return 0


def _lol_multi(net, driver, memo):
    """LOL of a net, trying flat/bracket variants for robust matching."""
    if net is None:
        return 0
    best = 0
    for v in _flat_bracket_variants(_norm(net)):
        best = max(best, lol(v, driver, memo))
    return best


def _emit(args, summary, endpoints_out):
    out = {'summary': summary, 'endpoints': endpoints_out}
    Path(args.output).write_text(json.dumps(out, indent=2))
    lines = [
        "ECO_SCRIPT_LAUNCHED: eco_lol_impact.py (ADVISORY)",
        f"  output: {args.output}",
        f"  stage: Synthesize | ns/level: {summary.get('ns_per_level')} | inv/buf excluded: yes",
        f"  endpoints: {summary.get('endpoints', 0)} | max +LOL: {summary.get('max_delta_lol', 0)} "
        f"| est added delay (sum): {summary.get('total_est_added_delay_ns', 0)} ns",
    ]
    if summary.get('worst_endpoint'):
        lines.append(f"  worst: {summary['worst_endpoint']}  (+{summary.get('worst_delta_lol')} levels)")
    for e in endpoints_out:
        if 'delta_lol' in e and e['delta_lol'] != 0:
            tag = ' NEW-DFF' if e.get('new_register') else ''
            lines.append(f"    {e['register']}/{e['pin']}: {e['before_lol']} -> {e['after_lol']} "
                         f"(+{e['delta_lol']}, ~{e['est_added_delay_ns']} ns){tag}")
    marker = '\n'.join(lines)
    print(marker)
    Path(args.output.replace('.json', '_marker.txt')).write_text(marker + '\n')


if __name__ == '__main__':
    sys.exit(main())
