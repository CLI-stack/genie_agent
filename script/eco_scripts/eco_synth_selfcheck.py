#!/usr/bin/env python3
"""eco_synth_selfcheck.py — functional equivalence self-check for the combinational
net-force builder (eco_cone_rebuild.emit_comb_net_force).

Proves, on many random input vectors, that the emitted gate cone reproduces the NEW
RTL exactly:

    gate_cone(sig[b])  ==  newRTL(sig[b])   for every bit b, every vector

where the gate cone's `net_orig` leaves (the renamed original combinational driver)
are fed the OLD RTL value of sig — mirroring what net-force does in silicon
(inside the changed region = new value; outside = original/unchanged value).

The reference is an INDEPENDENT integer-domain interpreter of the RTL AST. It shares
only the parser (parse_expr / parse_always, separately validated in S1) with the
synthesizer; the gate-lowering math under test (bit/_and/_or/_mux/_cmp/_eq/_xnor/
comparator/concat) is NOT shared. A logic bug in the lowering shows up as a mismatch.
"""
import os, re, sys, random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eco_cone_rebuild import (parse_always, emit_comb_net_force,          # noqa: E402
                              has_whole_driver, perbit_drivers, _strip_comments)
from eco_extract_pf_condition import resolve_rtl                          # noqa: E402
from eco_rtl_config import RtlConfig                                      # noqa: E402
from eco_rtl_synth import parse_expr, build_width_map, width_of, _eval_bound  # noqa: E402
from eco_emit_priority_force import _module_netlist_body                  # noqa: E402


class _IErr(Exception):
    pass


class _Interp:
    """Integer-domain interpreter of a signal's RTL value. Grounds at the SAME leaves
    as the synthesizer (netlist nets + registers), reading their value from `env`."""
    def __init__(self, cfg, wm, rtl_text, grounds, env, macros=None):
        self.cfg, self.wm, self.rtl = cfg, wm, rtl_text
        self.grounds, self.env = grounds, env
        self.macros = macros if macros is not None else {
            k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None}
        self._cache, self._building = {}, set()
        self._stripped = _strip_comments(rtl_text)

    def _w(self, name):
        return self.wm.get(name) or 1

    def _mask(self, v, w):
        return v & ((1 << w) - 1)

    def sig_val(self, name):
        name = name.lstrip('`')
        if self.grounds(name):
            return self._mask(self.env.get(name, 0), self._w(name))
        if name in self._cache:
            return self._cache[name]
        if name in self._building:
            raise _IErr(f"cycle on {name}")
        self._building.add(name)
        try:
            if not has_whole_driver(self.rtl, name):
                pb = perbit_drivers(self.rtl, name)
                if pb:
                    val = 0
                    for sel, rhs in pb:
                        lo, hi = self._sel_range(sel)
                        if lo is None:
                            raise _IErr(f"{name}[{sel}]: cannot resolve select")
                        rv = self.eval(parse_expr(rhs)) & ((1 << (hi - lo + 1)) - 1)
                        val |= rv << lo
                    val = self._mask(val, self._w(name))
                    self._cache[name] = val
                    return val
            tree = parse_always(self.rtl, name)
            if tree and (tree['default'] is not None or tree['assigns']):
                val = self.eval(parse_expr(tree['default'])) if tree['default'] is not None else 0
                for cond, v in tree['assigns']:
                    if self.path(cond):
                        val = self.eval(parse_expr(v))
            else:
                m = re.search(r'\b' + re.escape(name) + r'\s*=(?!=)\s*(.*?);', self._stripped, re.DOTALL)
                if not m:
                    raise _IErr(f"{name}: not grounded, no always/continuous assign")
                val = self.eval(parse_expr(m.group(1).strip()))
        finally:
            self._building.discard(name)
        val = self._mask(val, self._w(name))
        self._cache[name] = val
        return val

    def _sel_range(self, sel):
        s = sel.strip()
        if ':' in s:
            a, b = s.split(':', 1)
            hi = _eval_bound(a, self.macros); lo = _eval_bound(b, self.macros)
            if hi is None or lo is None:
                return (None, None)
            return (min(hi, lo), max(hi, lo))
        ps = self.cfg.part_select(s.lstrip('`')) if self.cfg else None
        if ps:
            return ps
        v = _eval_bound(s, self.macros)
        return (v, v) if v is not None else (None, None)

    def path(self, cond):
        for e, s in cond:
            v = 1 if self.eval(parse_expr(e)) != 0 else 0
            if bool(v) != bool(s):
                return False
        return True

    def _macro_field(self, idx_node):
        if idx_node[0] == 'id' and self.cfg:
            return self.cfg.part_select(idx_node[1].lstrip('`'))
        return None

    def _const(self, node):
        v = self.eval(node)
        return v

    def _pw(self, node):
        """Part width for concat/rep packing. width_of() reports 1 for EVERY 'bit'
        node, but a bit-select whose index is a range macro (e.g. rcqe_cam[`FLDCSB]
        = cam[26:29]) is 4 bits wide. eval() already returns the full field via
        _macro_field, so packing it with width 1 would truncate it to its LSB and
        corrupt the concat (breaking {..}=={..} field compares). Resolve the macro
        field width here; fall back to width_of for everything else."""
        if node[0] == 'bit':
            ps = self._macro_field(node[2])
            if ps and ps[0] != ps[1]:
                return abs(ps[1] - ps[0]) + 1
        return width_of(node, self.wm, self.macros) or 1

    def eval(self, node):
        t = node[0]
        if t == 'num':
            if node[2] is None:
                raise _IErr(f"x/z constant {node[3]}")
            return node[2]
        if t == 'id':
            nm = node[1]; bare = nm.lstrip('`')
            is_macro = nm.startswith('`') or (bare in self.cfg.defs and bare not in self.wm
                                              and not self.grounds(bare))
            if is_macro:
                raw = self.cfg.defs.get(bare)
                if raw is not None and raw.strip() and ':' not in raw:
                    try:
                        return self.eval(parse_expr(raw))
                    except Exception:
                        pass
                v = self.cfg.value(bare)
                if v is not None:
                    return v
            return self.sig_val(nm)
        if t == 'bit':
            ps = self._macro_field(node[2])
            base = self.sig_val(node[1][1] if node[1][0] == 'id' else self._basename(node[1]))
            if ps and ps[0] != ps[1]:
                return self._mask(base >> ps[0], ps[1] - ps[0] + 1)
            if ps:
                return (base >> ps[0]) & 1
            idx = self._const(node[2])
            return (base >> idx) & 1
        if t == 'part':
            hi = self._const(node[2]); lo = self._const(node[3])
            lo, hi = min(hi, lo), max(hi, lo)
            base = self.sig_val(self._basename(node[1]))
            return self._mask(base >> lo, hi - lo + 1)
        if t == 'idxpart':
            base_i = self._const(node[2]); w = self._const(node[3])
            lo = base_i if node[4] else base_i - w + 1
            base = self.sig_val(self._basename(node[1]))
            return self._mask(base >> lo, w)
        if t == 'un':
            a = self.eval(node[2])
            if node[1] == '~':
                return self._mask(~a, width_of(node[2], self.wm, self.macros) or 1)
            if node[1] == '!':
                return 0 if a else 1
            if node[1] == '-':
                return -a
            raise _IErr(f"unary {node[1]}")
        if t == 'red':
            inner = node[2]; w = width_of(inner, self.wm, self.macros) or 1
            v = self.eval(inner)
            bits = [(v >> k) & 1 for k in range(w)]
            if node[1] in ('&', '~&'):
                r = int(all(bits))
            elif node[1] in ('^', '~^'):
                r = 0
                for b in bits:
                    r ^= b
            else:
                r = int(any(bits))
            return (1 - r) if node[1] in ('~&', '~|', '~^') else r
        if t == 'bin':
            return self._bin(node)
        if t == 'tern':
            return self.eval(node[2]) if self.eval(node[1]) != 0 else self.eval(node[3])
        if t == 'concat':
            val = 0
            for p in node[1]:
                w = self._pw(p)
                val = (val << w) | self._mask(self.eval(p), w)
            return val
        if t == 'rep':
            p = node[2]; w = self._pw(p)
            pv = self._mask(self.eval(p), w); val = 0
            for _ in range(node[1]):
                val = (val << w) | pv
            return val
        raise _IErr(f"node {t}")

    def _bin(self, node):
        op, A, B = node[1], node[2], node[3]
        a = self.eval(A); b = self.eval(B)
        if op == '&':
            return a & b
        if op == '|':
            return a | b
        if op == '^':
            return a ^ b
        if op == '&&':
            return 1 if (a != 0 and b != 0) else 0
        if op == '||':
            return 1 if (a != 0 or b != 0) else 0
        if op == '==':
            return 1 if a == b else 0
        if op == '!=':
            return 1 if a != b else 0
        if op == '<':
            return 1 if a < b else 0
        if op == '>':
            return 1 if a > b else 0
        if op == '<=':
            return 1 if a <= b else 0
        if op == '>=':
            return 1 if a >= b else 0
        if op == '+':
            return a + b
        if op == '-':
            return a - b
        if op == '*':
            return a * b
        if op == '/':
            return a // b if b else 0
        if op == '%':
            return a % b if b else 0
        if op == '<<':
            return a << b
        if op == '>>':
            return a >> b
        raise _IErr(f"bin {op}")

    @staticmethod
    def _basename(node):
        while node[0] in ('bit', 'part', 'idxpart'):
            node = node[1]
        return node[1] if node[0] == 'id' else '0'


def _eval_gates(gates, env):
    """Evaluate the boolean gate netlist recursively (used for small ad-hoc checks).
    env maps leaf nets -> 0/1. Returns a `get(net)` function."""
    drv = {g['output_net']: g for g in gates}
    val = {}

    def get(net):
        if net in ("1'b0", "0'b0"):
            return 0
        if net == "1'b1":
            return 1
        if net in env:
            return env[net]
        if net in val:
            return val[net]
        g = drv.get(net)
        if g is None:
            raise _IErr(f"undriven net {net}")
        val[net] = _apply(g, get)
        return val[net]

    return get


def _apply(g, get):
    fn = g['gate_function']; pc = g['port_connections']
    if fn == 'INV':
        return 1 - get(pc['I'])
    if fn == 'AND2':
        return get(pc['A1']) & get(pc['A2'])
    if fn == 'OR2':
        return get(pc['A1']) | get(pc['A2'])
    if fn == 'INR2':                       # ZN = A1 & ~B1
        return get(pc['A1']) & (1 - get(pc['B1']))
    raise _IErr(f"gate fn {fn}")


def compile_gates(gates):
    """Topologically order the gates once so each vector evals in a single linear pass
    (the synth appends gates bottom-up, so a stable DFS over inputs suffices). Returns
    an ordered list of (out_net, fn, (input_nets...))."""
    drv = {g['output_net']: g for g in gates}
    order, seen, stack = [], set(), set()

    def ins(g):
        pc = g['port_connections']
        return [pc[p] for p in ('I', 'A1', 'A2', 'B1') if p in pc]

    def visit(net):
        if net in seen or net not in drv:
            return
        if net in stack:
            raise _IErr(f"gate cycle at {net}")
        stack.add(net)
        g = drv[net]
        for i in ins(g):
            visit(i)
        stack.discard(net)
        seen.add(net)
        order.append((net, g['gate_function'], tuple(ins(g))))

    sys.setrecursionlimit(100000)
    for g in gates:
        visit(g['output_net'])
    return order


def eval_ordered(order, env):
    """Evaluate a compiled gate order against env (leaf net -> 0/1). Returns net -> 0/1."""
    val = dict(env)

    def v(net):
        if net in val:
            return val[net]
        if net in ("1'b0", "0'b0"):
            return 0
        if net == "1'b1":
            return 1
        raise _IErr(f"undriven leaf {net}")

    for out, fn, inp in order:
        if fn == 'INV':
            val[out] = 1 - v(inp[0])
        elif fn == 'AND2':
            val[out] = v(inp[0]) & v(inp[1])
        elif fn == 'OR2':
            val[out] = v(inp[0]) | v(inp[1])
        elif fn == 'INR2':
            val[out] = v(inp[0]) & (1 - v(inp[1]))
        else:
            raise _IErr(f"gate fn {fn}")
    return val


_SEL_OUT_PINS = ('Z', 'ZN', 'Q', 'QN', 'CO', 'CON', 'S', 'SN', 'SO', 'OUT')


def _selector_independent_of_orig(gates, rewires):
    """Independent structural gate on the emitted force-mux: the region SELECTOR must not be
    a function of the signal's own OLD driver (net_orig). Inspects only the emitted gate
    graph (not the shared RTL lowering), so it catches a selector that aliases to the signal
    — the exact defect that makes the mux collapse to `orig & region`. Returns True if every
    signal's selector is independent of its net_orig, False otherwise (fail-closed)."""
    drv = {g['output_net']: g for g in gates}

    def fanin(net):
        seen, stack = set(), [net]
        while stack:
            nn = stack.pop()
            if nn in seen:
                continue
            seen.add(nn)
            g = drv.get(nn)
            if not g:
                continue
            for k, v in g['port_connections'].items():
                if k in _SEL_OUT_PINS or not isinstance(v, str):
                    continue
                stack.append(v)
        return seen

    for rw in rewires:
        sel = rw.get('region_sel')
        orig = rw.get('new_net')
        if not sel or not orig:
            continue
        if orig in fanin(sel):
            return False
    return True


def run(ref_dir, module, signal, n=5000, seed=1):
    base = re.sub(r'^ddrss_\w+?_t_', '', module)
    new_rtl = open(resolve_rtl(ref_dir=ref_dir, module=base, subdir='SynRtl'), errors='replace').read()
    old_rtl = open(resolve_rtl(ref_dir=ref_dir, module=base, subdir='PreEco/SynRtl'), errors='replace').read()
    cfg = RtlConfig(ref_dir)
    macros = {k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None}
    wm_new = build_width_map(new_rtl, macros)
    wm_old = build_width_map(old_rtl, macros)
    nlbody = _module_netlist_body(ref_dir, module)
    stripped_new = _strip_comments(new_rtl)
    _innl_c, _isreg_c, _grounds_c = {}, {}, {}

    def innl(s):
        r = _innl_c.get(s)
        if r is None:
            r = bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody)); _innl_c[s] = r
        return r

    def is_reg(nm):
        r = _isreg_c.get(nm)
        if r is None:
            r = bool(re.search(r'\b' + re.escape(nm) + r'\s*(?:\[[^\]]*\])?\s*<=', stripped_new)); _isreg_c[nm] = r
        return r

    # The signal-under-test IS present in the netlist (the pre-ECO combinational net), so a
    # plain grounds() would short-circuit it to a netlist leaf instead of computing its RTL
    # value. Exclude it (and only it) from grounding so the reference always evaluates its
    # cone from RTL, grounding at the leaves. (It is not self-referential.)
    def grounds(nm):
        r = _grounds_c.get(nm)
        if r is None:
            r = (nm != signal and (innl(nm) or is_reg(nm))); _grounds_c[nm] = r
        return r

    # tech_map=False: this check validates the PRIMITIVE cone vs RTL with a primitive-only
    # evaluator. Compound-cell mapping is verified separately (fail-closed) inside tech_map
    # (mapped == primitive), so primitive==RTL + mapped==primitive => mapped==RTL.
    out = emit_comb_net_force(ref_dir, module, signal, jira='chk', tech_map=False)
    if out['errors']:
        print("EMIT ERRORS:", out['errors']); return False
    gates = out['gates']
    def _bit_of(rw):
        m = re.search(r'\[(\d+)\]', rw['old_net'])
        return int(m.group(1)) if m else 0        # scalar signal (no [b]) -> bit 0
    width = (max(_bit_of(rw) for rw in out['rewires']) + 1) if out['rewires'] else (wm_new.get(signal) or 1)
    # net_orig leaf per bit (from the driver rewires)
    orig_leaf = {}
    for rw in out['rewires']:
        orig_leaf[_bit_of(rw)] = rw['new_net']

    # ── INDEPENDENT structural gate: region selector must NOT depend on the signal's own
    # old driver (net_orig). A sound region selector is the RTL path guard (a function of
    # control inputs); if it instead aliases to net_orig (e.g. sel = (orig==old_value)),
    # the force-mux degenerates to `orig & region` and silently corrupts the signal OUTSIDE
    # the changed region — a class of bug the random-vector RTL self-check cannot see
    # because it grounds a consistent input space. This check is independent of the shared
    # RTL lowering: it inspects only the emitted gate graph. Fail-closed.
    if not _selector_independent_of_orig(gates, out['rewires']):
        print(f"{signal}: FAIL — region selector depends on net_orig (aliased selector); "
              f"force-mux would corrupt logic outside the changed region.")
        return False

    # leaf base signals the cone reads (strip [i]); net_orig names are computed, not random
    allnets = {g['output_net'] for g in gates}
    orig_names = set(orig_leaf.values())
    leaf_bases = {}
    for g in gates:
        for k, v in g['port_connections'].items():
            if k in ('Z', 'ZN'):
                continue
            if not isinstance(v, str) or v in allnets or v in orig_names:
                continue
            if v.startswith(("1'b", "0'b")):
                continue
            bm = re.match(r'^(.*)\[(\d+)\]$', v)
            base = bm.group(1) if bm else v
            leaf_bases.setdefault(base, wm_new.get(base) or (int(bm.group(2)) + 1 if bm else 1))

    rng = random.Random(seed)
    order = compile_gates(gates)         # topo order once; eval linearly per vector
    is_bus_sig = (wm_new.get(signal) or 1) > 1
    out_nets = [(f'{signal}[{b}]' if is_bus_sig else signal) for b in range(width)]
    mism = 0
    for it in range(n):
        env = {base: rng.getrandbits(w) for base, w in leaf_bases.items()}
        # bit-addressable env for the gate leaves: 'base[i]' -> bit, 'base'(1-bit) -> value
        genv = {}
        for base, w in leaf_bases.items():
            if w == 1:
                genv[base] = env[base] & 1
            for i in range(w):
                genv[f'{base}[{i}]'] = (env[base] >> i) & 1
        # OLD RTL value feeds net_orig leaves
        interp_old = _Interp(cfg, wm_old, old_rtl, grounds, env, macros)
        oldv = interp_old.sig_val(signal)
        for b, nm in orig_leaf.items():
            genv[nm] = (oldv >> b) & 1
        # DUT: gate cone output for each bit (single linear pass)
        val = eval_ordered(order, genv)
        dut = 0
        for b in range(width):
            dut |= (val.get(out_nets[b], 0) & 1) << b
        # REF: NEW RTL value
        interp_new = _Interp(cfg, wm_new, new_rtl, grounds, env, macros)
        ref = interp_new.sig_val(signal)
        if dut != ref:
            mism += 1
            if mism <= 5:
                print(f"  MISMATCH vec#{it}: dut={dut:0{width}b} ref={ref:0{width}b}")
    print(f"{signal}: {n} vectors, {mism} mismatches  ->  {'PASS' if mism == 0 else 'FAIL'}")
    return mism == 0


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit("usage: eco_synth_selfcheck.py <ref_dir> <signal> <module> [n_vectors]")
    ref = sys.argv[1]
    sig = sys.argv[2]
    mod = sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 5000
    ok = run(ref, mod, sig, n=n)
    sys.exit(0 if ok else 1)
