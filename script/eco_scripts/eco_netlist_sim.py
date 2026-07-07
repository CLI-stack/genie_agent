"""eco_netlist_sim.py — extract and simulate the combinational cone of a net in a
gate-level netlist module, using eco_cell_truth_tables for per-cell boolean functions.

Purpose: VERIFY that a surgical ECO fold reproduces the intended function
(candidate-and-verify). We can:
  - extract the cone of a net back to leaves (primary inputs / register Q / constants),
  - simulate it over random leaf-value vectors,
  - compare a candidate net's function to an independent oracle (an RTL expression or
    another net), and to detect the polarity relationship (equal vs inverted).

This is intentionally combinational-only: the cone stops at any net whose driver is a
sequential cell (has a CP/CK clock pin) — that Q output is treated as a leaf.
"""
import re
import gzip
import random
import eco_cell_truth_tables as _ett


_CLK_PINS = ('CP', 'CPN', 'CK', 'CKB', 'CLK')


def _module_body(gz_path, module):
    """Return the text of `module`'s body from a (possibly gz) netlist file."""
    op = gzip.open if gz_path.endswith('.gz') else open
    body, grab = [], False
    with op(gz_path, 'rt', errors='replace') as f:
        for ln in f:
            m = re.match(r'^\s*module\s+(\S+)', ln)
            if m:
                grab = (m.group(1) == module)
            if grab:
                body.append(ln)
                if ln.lstrip().startswith('endmodule'):
                    break
    return ''.join(body)


def _strip_comments(txt):
    txt = re.sub(r'/\*.*?\*/', ' ', txt, flags=re.S)
    txt = re.sub(r'//[^\n]*', ' ', txt)
    return txt


# instance:  CELLTYPE inst ( .pin(net) , .pin(net) ... ) ;
_INST_RE = re.compile(r'([A-Za-z][\w]*)\s+([A-Za-z_][\w]*)\s*\((.*?)\)\s*;', re.S)
_PIN_RE = re.compile(r'\.\s*([A-Za-z][\w]*)\s*\(\s*([^)]*?)\s*\)')


def parse_module(gz_path, module, ref_dir=None):
    """Parse a module into {inst: {'cell': type, 'pins': {pin: net}}} plus a driver map
    net -> (inst, out_pin). Only real library cells (recognised by eco_cell_truth_tables)
    are kept; wire/assign/module-header lines are skipped. `ref_dir` is required to
    resolve vendor-specific cells (e.g. *AMD* OAOI/AOI variants) via liberty extraction."""
    body = _strip_comments(_module_body(gz_path, module))
    insts, drivers = {}, {}
    for m in _INST_RE.finditer(body):
        cell, inst, pinstr = m.group(1), m.group(2), m.group(3)
        if cell in ('module', 'wire', 'input', 'output', 'inout', 'reg', 'assign', 'endmodule'):
            continue
        tt = _ett.truth_table_of(cell, ref_dir=ref_dir)
        if not tt:
            continue  # unknown cell — cannot simulate; caller must treat as leaf
        pins = {p: _norm_net(n) for p, n in _PIN_RE.findall(pinstr)}
        if not pins:
            continue
        insts[inst] = {'cell': cell, 'pins': pins, 'tt': tt}
        is_seq = any(cp in pins for cp in _CLK_PINS)
        for out_pin in tt:                       # tt keys are the output pins
            net = pins.get(out_pin)
            if net:
                drivers[net] = (inst, out_pin, is_seq)
    return insts, drivers


def _norm_net(n):
    n = n.strip()
    return n


def cone_of(target, insts, drivers):
    """Collect the combinational cone driving `target`: ordered gate list (each an
    (inst, out_pin) whose value we compute) + the set of LEAF nets (primary inputs,
    register Q outputs, constants) the cone bottoms out at. Sequential drivers are
    leaves (the cone does not cross a flop)."""
    order, seen, leaves = [], set(), set()

    def visit(net):
        if net in seen:
            return
        seen.add(net)
        if _is_const(net):
            return
        drv = drivers.get(net)
        if drv is None:
            leaves.add(net); return
        inst, out_pin, is_seq = drv
        if is_seq:
            leaves.add(net); return          # flop Q — treat as leaf
        # recurse into this gate's input nets first
        pins = insts[inst]['pins']
        outs = set(insts[inst]['tt'].keys())
        for p, n in pins.items():
            if p in outs or p in _CLK_PINS:
                continue
            visit(n)
        order.append((inst, out_pin, net))
    visit(target)
    return order, leaves


def _is_const(n):
    return bool(re.match(r"^\d*'[bBhHdDoO]", n)) or n in ('1', '0')


def _const_val(n):
    m = re.match(r"^\d*'[bB]([01])", n)
    if m:
        return int(m.group(1))
    if n in ('0', '1'):
        return int(n)
    return 0


def simulate(order, insts, env):
    """Evaluate the cone `order` (from cone_of) with leaf net values in `env` (net->0/1).
    Returns a dict net->0/1 including the target. Each gate's output pin value is the
    truth-table expression evaluated over its input-pin values."""
    val = dict(env)

    def get(net):
        if net in val:
            return val[net]
        if _is_const(net):
            val[net] = _const_val(net); return val[net]
        val[net] = 0  # undriven leaf not in env -> 0 (caller controls all real leaves)
        return 0

    for inst, out_pin, net in order:
        info = insts[inst]
        expr = info['tt'][out_pin]
        pins = info['pins']
        local = {}
        for p, n in pins.items():
            if p == out_pin or p in _CLK_PINS:
                continue
            local[p] = get(n)
        try:
            v = 1 if (eval(expr, {"__builtins__": {}}, local) & 1) else 0
        except Exception:
            v = 0
        val[net] = v
    return val


def net_function(gz_path, module, target, n=2000, seed=1, ref_dir=None):
    """Sample `target`'s function: return (leaves_sorted, [(env, value)...]) over n random
    vectors. Used to compare two nets / a net vs an oracle."""
    insts, drivers = parse_module(gz_path, module, ref_dir=ref_dir)
    order, leaves = cone_of(target, insts, drivers)
    leaves = sorted(leaves)
    rnd = random.Random(seed)
    samples = []
    for _ in range(n):
        env = {lf: rnd.randint(0, 1) for lf in leaves}
        v = simulate(order, insts, env).get(target, 0)
        samples.append((env, v))
    return leaves, samples, order, leaves


if __name__ == '__main__':
    import sys
    gz, mod, net = sys.argv[1], sys.argv[2], sys.argv[3]
    ref = sys.argv[4] if len(sys.argv) > 4 else None
    insts, drivers = parse_module(gz, mod, ref_dir=ref)
    order, leaves = cone_of(net, insts, drivers)
    print(f"net {net}: cone gates={len(order)}  leaves={len(leaves)}")
    print("  leaves:", sorted(leaves)[:20])
    print("  driver:", drivers.get(net))
