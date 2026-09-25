#!/usr/bin/env python3
"""
eco_structural_polarity.py — shared structural (no-Formality/no-fenets) cross-module
polarity tracer.

Determines whether a bare-named leaf operand used by an ECO gate carries the same
logical polarity as its true RTL-level source, by walking the already-decompressed
gate-level netlist text: counting inverter hops through standard-cell buffer/inverter
chains, and crossing module boundaries in BOTH directions:

  UP   — a bare net that is itself a primary INPUT port of its own module hops into
         the parent instantiation's connection expression (found in the SAME netlist
         text via `_find_parent_instantiation`).
  DOWN — a bare net driven by a child RTL submodule instance's own OUTPUT port (a
         named pin that is not a recognized standard-cell pin, e.g. `addr_rm`)
         descends into that child module's own body and keeps walking there.

Neither hop needs Formality/fenets — everything is derived from the PreEco netlist
text already on disk. One implementation, used by:
  - eco_validate_step3.py's Check 68 (complete mode)
  - eco_check_cross_module_polarity.py (simple mode's standalone structural check)
so a fix (or a bug) only exists in one place.
"""
import re
from pathlib import Path

from eco_validate_step3 import _plain_netlist, _nl_text, _nl_module_map

_INV_RE = re.compile(r'^(INV|INVD|INVSKR|INVLLKG|INVTX|INVSK|INVFE)', re.IGNORECASE)
_OUT_PIN_RE = re.compile(r'\.\s*(Z|ZN|ZN1|Q|QN|CO|S)\s*\(\s*(\w+(?:\[\d+\])?)\s*\)')
_IN_PIN_RE = re.compile(r'\.\s*I\s*\(\s*(\w+(?:\[\d+\])?)\s*\)')
_INST_HEAD_RE = re.compile(r'^([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
# NOTE: unlike a standard-cell-only scan, this must also match lowercase RTL
# module instantiations (e.g. `some_child_module some_inst (`) — a child module
# type is just as valid an identifier as a standard-cell type, and DOWN-hop resolution
# (child_inst_map, below) depends on seeing those instances at all. The trailing
# keyword skip-list in _index_module_body_uncached filters out the few
# non-instance Verilog statements this broader match would otherwise catch.
_INPUT_PORT_DECL_RE = re.compile(r'^\s*input\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]', re.MULTILINE)
_OUTPUT_PORT_DECL_RE = re.compile(r'^\s*output\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]', re.MULTILINE)
_PORT_WIDTH_DECL_RE = re.compile(r'^\s*input\s+\[(\d+):(\d+)\]\s+(\w+)\s*[;,]', re.MULTILINE)
_BIT_RE = re.compile(r'^([A-Za-z_]\w*)(?:\[(\d+)\])?$')

_MODULE_INDEX_CACHE = {}
_INDEXING_STACK = set()   # cycle guard — real HW hierarchy is a DAG; defensive only


def _parse_port_connections(block):
    """Parse a `.port(expr), .port2(expr2), ...` instantiation block into
    {port_name: expr_text}, balanced-paren aware. None on any parse failure
    (caller must fail closed to UNDETERMINED/unresolved, never guess)."""
    conns = {}
    i, n = 0, len(block)
    port_re = re.compile(r'\.\s*([A-Za-z_]\w*)\s*\(')
    while i < n:
        m = port_re.search(block, i)
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


def _bare_ident(expr):
    """A parent-side port-connection expression we can confidently follow for a DOWN
    hop: a plain identifier, or a plain identifier's bit-select — NOT a concatenation/
    constant/expression (those are rare for a straight structural passthrough; fail
    closed rather than guess at which sub-piece corresponds to which child bit)."""
    m = _BIT_RE.match((expr or '').strip())
    return m.group(1) if m else None


def _index_module_body(host_module, ref_dir, stage):
    """Parse <host>'s PreEco body once (cached). Returns
    (driver_map, primary_inputs, output_ports, child_inst_map), or all-None on failure.
      driver_map      : {net: (cell_type, inst_name, passthrough_input|None, terminal_kind, is_inv)}
                        standard-cell outputs only (Z/ZN/ZN1/Q/QN/CO/S).
      primary_inputs  : {port_name, ...} bare names from this module's own `input` decls.
      output_ports    : {port_name, ...} bare names from this module's own `output` decls.
      child_inst_map  : {parent_net_base_name: (child_module_type, child_port_name, None)}
                        one entry per net wired to a locally-defined child RTL module's
                        OWN declared output port — this is what makes the DOWN hop
                        possible: a net that a standard-cell driver-map lookup misses
                        might still have a real driver one level deeper, inside that
                        child instance's own body.
    """
    key = (stage, host_module)
    if key in _MODULE_INDEX_CACHE:
        return _MODULE_INDEX_CACHE[key]
    if key in _INDEXING_STACK:
        return (None, None, None, None)
    _INDEXING_STACK.add(key)
    try:
        result = _index_module_body_uncached(host_module, ref_dir, stage)
    finally:
        _INDEXING_STACK.discard(key)
    _MODULE_INDEX_CACHE[key] = result
    return result


def _index_module_body_uncached(host_module, ref_dir, stage):
    gz = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
    if not gz.is_file():
        return (None, None, None, None)
    try:
        plain = _plain_netlist(str(gz))
        full = _nl_text(plain)
        modmap = _nl_module_map(plain)
    except Exception:
        full = ''; modmap = {}
    if not full:
        return (None, None, None, None)
    parts = []
    for _mod in (host_module, f'{host_module}_0'):
        span = modmap.get(_mod)
        if span:
            parts.append(full[span[0]:span[1]])
    txt = '\n'.join(parts)
    if not txt:
        return (None, None, None, None)
    txt = re.sub(r'//[^\n]*', '', txt)
    txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.DOTALL)

    primary_inputs = set(_INPUT_PORT_DECL_RE.findall(txt))
    output_ports = set(_OUTPUT_PORT_DECL_RE.findall(txt))

    driver_map = {}
    child_inst_map = {}
    inst_iter = list(_INST_HEAD_RE.finditer(txt))
    for idx, m in enumerate(inst_iter):
        cell_type, inst_name = m.group(1), m.group(2)
        if cell_type in ('module', 'endmodule', 'input', 'output', 'inout',
                          'wire', 'reg', 'tri', 'wand', 'wor', 'assign', 'always',
                          'initial', 'parameter', 'localparam', 'function', 'task',
                          'generate', 'endgenerate'):
            continue
        open_pos = m.end() - 1
        depth = 0; close_pos = -1
        scan_end = inst_iter[idx + 1].start() if idx + 1 < len(inst_iter) else min(open_pos + 20000, len(txt))
        for j in range(open_pos, scan_end):
            c = txt[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    close_pos = j; break
        if close_pos < 0:
            continue
        block = txt[open_pos + 1:close_pos]

        if cell_type in modmap:
            # A real, locally-defined child RTL module instance (not a standard
            # library cell — those never appear as `module...endmodule` in the
            # design's own netlist text). Index its OWN output ports (recursively
            # cached) and register every net it connects to one of those pins —
            # this is the DOWN-hop table.
            _, _, child_outputs, _ = _index_module_body(cell_type, ref_dir, stage)
            if child_outputs:
                conns = _parse_port_connections(block)
                if conns:
                    for pin, expr in conns.items():
                        if pin in child_outputs:
                            base = _bare_ident(expr)
                            if base is not None and base not in child_inst_map:
                                child_inst_map[base] = (cell_type, pin, None)
            continue   # never treat a submodule instance as a standard-cell driver

        passthrough_input = None
        is_inv = bool(_INV_RE.match(cell_type))
        im = _IN_PIN_RE.search(block)
        if im:
            passthrough_input = im.group(1)
        for op_m in _OUT_PIN_RE.finditer(block):
            out_pin, out_net = op_m.group(1), op_m.group(2)
            terminal_kind = ('dff_q' if out_pin == 'Q' else
                              'dff_qn' if out_pin == 'QN' else
                              'dff_co' if out_pin == 'CO' else
                              'dff_s' if out_pin == 'S' else
                              'comb')
            driver_map.setdefault(out_net, (cell_type, inst_name, passthrough_input, terminal_kind, is_inv))

    return (driver_map, primary_inputs, output_ports, child_inst_map)


def net_parity_in_stage(net, host_module, ref_dir, stage, max_hops=8, _depth=0):
    """Parity 0/1 + terminal descriptor via the indexed driver_map/child_inst_map,
    extended with a DOWN hop into a child RTL submodule's own output port when the
    standard-cell lookup misses. Terminal string encodes what's needed by the
    hierarchical (UP-hop) caller:
      'primary_input:<module>:<net>' — bare module input; module is the one the
                                        DOWN-hop walk actually stopped in (may differ
                                        from `host_module` if we descended first).
      'dff_<inst>' / 'comb_<celltype>' — a real, trustworthy driver.
      'unresolved' / 'max_hops'        — could not be determined.
    """
    if _depth > 6:
        return 0, 'max_hops'
    driver_map, primary_inputs, output_ports, child_inst_map = _index_module_body(host_module, ref_dir, stage)
    if driver_map is None:
        return None
    cur = (net or '').strip()
    parity = 0
    for _hop in range(max_hops):
        if cur in primary_inputs:
            return parity, f'primary_input:{host_module}:{cur}'
        _bit_m = _BIT_RE.match(cur)
        base = _bit_m.group(1) if _bit_m else cur
        if _bit_m and base in primary_inputs:
            return parity, f'primary_input:{host_module}:{cur}'
        d = driver_map.get(cur)
        if d is not None:
            cell_type, inst_name, passthrough_input, terminal_kind, is_inv = d
            if terminal_kind == 'dff_qn':
                parity ^= 1
                return parity, f'dff_{inst_name}'
            if terminal_kind in ('dff_q', 'dff_co', 'dff_s'):
                return parity, f'dff_{inst_name}'
            if passthrough_input is not None:
                if is_inv:
                    parity ^= 1
                cur = passthrough_input
                continue
            return parity, f'comb_{cell_type[:8]}'
        ci = child_inst_map.get(base)
        if ci is not None:
            child_module, child_pin, _unused = ci
            bit_suffix = _bit_m.group(2) if _bit_m else None
            child_net = f'{child_pin}[{bit_suffix}]' if bit_suffix is not None else child_pin
            sub = net_parity_in_stage(child_net, child_module, ref_dir, stage,
                                       max_hops=max_hops, _depth=_depth + 1)
            if sub is None:
                return parity, 'unresolved'
            sub_parity, sub_terminal = sub
            return parity ^ sub_parity, sub_terminal
        return parity, 'unresolved'
    return parity, 'max_hops'


def _port_width_in_module(module, ref_dir, stage, base_name):
    """Return the bit-width of input port `base_name` in `module` (from its own
    `input [MSB:LSB] name;` declaration), or None if not found/not a vector."""
    gz = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
    if not gz.is_file():
        return None
    try:
        plain = _plain_netlist(str(gz))
        full = _nl_text(plain)
        modmap = _nl_module_map(plain)
    except Exception:
        return None
    for _mod in (module, f'{module}_0'):
        span = modmap.get(_mod)
        if not span:
            continue
        body = full[span[0]:span[1]]
        for wm in _PORT_WIDTH_DECL_RE.finditer(body):
            msb, lsb, name = int(wm.group(1)), int(wm.group(2)), wm.group(3)
            if name == base_name:
                return abs(msb - lsb) + 1
    return None


def _find_parent_instantiation(host_module, ref_dir, stage, expected_inst_name=None):
    """Find the instantiation of `host_module` (or its Route-uniquified `_0` variant)
    as a child inside some OTHER module in the same stage netlist. Returns
    (parent_module, {port: expr}) or None if zero, ambiguous, or unparseable — never
    guess. `expected_inst_name` (from the study entry's own `instance_scope`) picks the
    specific instantiation when a module type is instantiated more than once."""
    gz = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
    if not gz.is_file():
        return None
    try:
        plain = _plain_netlist(str(gz))
        full = _nl_text(plain)
        modmap = _nl_module_map(plain)
    except Exception:
        return None
    if not full or not modmap:
        return None
    candidates = [host_module, f'{host_module}_0']
    pat = re.compile(r'(?m)^\s*(' + '|'.join(re.escape(c) for c in candidates) +
                      r')\s+([A-Za-z_]\w*)\s*\(')
    matches = list(pat.finditer(full))
    if not matches:
        return None
    results = []
    for m in matches:
        inst_name = m.group(2)
        parent = None
        for mod_name, (s, e) in modmap.items():
            if s <= m.start() < e:
                parent = mod_name; break
        if parent is None:
            continue
        parent_end = modmap[parent][1]
        open_pos = m.end() - 1
        depth = 0; close_pos = -1
        scan_end = min(parent_end, len(full))
        for j in range(open_pos, scan_end):
            c = full[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    close_pos = j; break
        if close_pos < 0:
            continue
        block = full[open_pos + 1:close_pos]
        results.append((parent, inst_name, block))
    if not results:
        return None

    def _pick(candidates_list):
        first_parent, _first_inst, first_block = candidates_list[0]
        if len(candidates_list) > 1 and ({b for _, _, b in candidates_list[1:]} - {first_block}):
            return None   # ambiguous: multiple instantiations with different wiring
        conns = _parse_port_connections(first_block)
        if conns is None:
            return None
        return (first_parent, conns)

    if expected_inst_name:
        by_name = [r for r in results if r[1] == expected_inst_name]
        if by_name:
            picked = _pick(by_name)
            if picked is not None:
                return picked
            return None
    return _pick(results)


def _resolve_bit_in_expr(expr, bit_index, total_width):
    """Given a parent-scope port-connection expression (a bare identifier, or a
    `{a,b,c}` concatenation, MSB-first per Verilog convention) and the bit index
    (0=LSB) we need out of a `total_width`-wide port, return the single-bit net name
    at that position, or None if ambiguous."""
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


def net_parity_hierarchical(net, host_module, ref_dir, stage, instance_scope=None,
                             max_module_hops=5):
    """Like net_parity_in_stage, but when the walk dead-ends at a primary input of
    `host_module` (or of a module reached via a DOWN hop), hop into the ACTUAL PARENT
    instantiation and continue the identical buf/inv parity walk there — across as
    many module boundaries, in either direction, as needed — until reaching a real
    register terminal, or a genuine, structurally-undecidable dead end. Returns
    (verdict, parity, terminal_desc); verdict in TRUE|INVERTED|UNDETERMINED. No
    Formality/fenets used — purely structural, from the already-parsed netlist text.

    `instance_scope`: the study entry's own hierarchy path (e.g. "PARENT/CHILD" —
    slash-separated, root-to-leaf, LAST segment is `host_module`'s own instance
    name). Consumed one segment per UP hop so `_find_parent_instantiation` can pick
    the SPECIFIC instantiation this leaf belongs to. Once segments run out, later
    hops fall back to the name-agnostic scan.
    """
    cur_net, cur_module = net, host_module
    total_parity = 0
    hops_used = 0
    visited = set()
    scope_segs = [s for s in (instance_scope or '').split('/') if s]
    while True:
        p = net_parity_in_stage(cur_net, cur_module, ref_dir, stage)
        if p is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (no-index)')
        local_parity, terminal = p
        total_parity ^= local_parity
        if terminal.startswith('primary_input:'):
            # The DOWN-hop walk inside net_parity_in_stage may have moved `cur`
            # forward into a DIFFERENT (child) module before landing on a primary
            # input there — use that real module+net for the next UP hop, not the
            # stale values from before this call, or the parent lookup below finds
            # the wrong port and false-fires "port-not-connected".
            _, term_module, term_net = terminal.split(':', 2)
            cur_module, cur_net = term_module, term_net
            terminal = 'primary_input'
        if terminal in ('unresolved', 'max_hops'):
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} ({terminal})')
        if terminal.startswith('comb_') and hops_used > 0:
            # A plain combinational terminal reached AFTER already crossing at least
            # one module boundary UP is not trustworthy enough to claim TRUE/INVERTED
            # — P&R can restructure the local driver differently per stage. Only a
            # genuine register terminal, or a comb terminal found WITHOUT ever
            # escalating UP past the original module, is confident enough to report.
            # (A DOWN hop does not weaken confidence — it fully resolves to the real
            # physical driver rather than stopping at an unexplored boundary — so
            # this caution only applies to UP-hop-reached comb terminals.)
            return ('UNDETERMINED', total_parity,
                    f'{cur_module}.{terminal} (cross-module comb terminal, unverifiable)')
        if terminal != 'primary_input':
            return ('TRUE' if total_parity == 0 else 'INVERTED',
                    total_parity, f'{cur_module}.{terminal}')
        key = (cur_module, cur_net)
        if key in visited or hops_used >= max_module_hops:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (hop-limit/loop)')
        visited.add(key)
        base_m = _BIT_RE.match(cur_net)
        if not base_m:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (unparseable-net)')
        base_name, bit_str = base_m.group(1), base_m.group(2)
        expected_inst = scope_segs.pop() if scope_segs else None
        found = _find_parent_instantiation(cur_module, ref_dir, stage, expected_inst)
        if found is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (no-unique-parent)')
        parent_module, conns = found
        expr = conns.get(base_name)
        if expr is None:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (port-not-connected)')
        if bit_str is not None:
            total_width = _port_width_in_module(cur_module, ref_dir, stage, base_name)
            if not total_width or total_width < 2:
                return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (width-unresolved)')
            upstream = _resolve_bit_in_expr(expr, int(bit_str), total_width)
        else:
            upstream = expr
        if not upstream:
            return ('UNDETERMINED', total_parity, f'{cur_module}:{cur_net} (bit-resolve-failed)')
        cur_net, cur_module = re.sub(r'\s+', '', upstream), parent_module
        hops_used += 1


_NO_CHECK_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CP', 'CK', 'CLK', 'CLOCK')
_ENTRY_TYPES = ('new_logic_gate', 'new_logic_dff')
_PLACEHOLDER_PREFIXES = ("MODE_H_ROUTE_SKIP", "UNRESOLVABLE",
                         "PENDING_FM_RESOLUTION", "NEEDS_NAMED_WIRE")


def check_cross_module_polarity(study, ref_dir):
    """Run the '68. CROSS-MODULE PRIMARY-INPUT POLARITY CHECK' over a study JSON's
    Synthesize/PrePlace/Route entries and return the same issue strings Check 68
    produces in eco_validate_step3.py — the single shared implementation both
    complete mode (via that validator) and simple mode (via
    eco_check_cross_module_polarity.py) call."""
    issues = []
    for stage in [s for s in ('Synthesize', 'PrePlace', 'Route') if study.get(s)]:
        for e in study.get(stage, []):
            if e.get('change_type') not in _ENTRY_TYPES:
                continue
            if not e.get('confirmed', True):
                continue
            host = e.get('module_name_per_stage', {}).get(stage) or e.get('module_name', '')
            inst = e.get('instance_name', '?')
            pcs_synth = e.get('port_connections') or {}
            pcs_ps = (e.get('port_connections_per_stage') or {}).get(stage) or {}
            for pin, synth_val in pcs_synth.items():
                if pin in _NO_CHECK_PINS or not isinstance(synth_val, str):
                    continue
                v = (pcs_ps.get(pin) or synth_val).strip()
                if v.startswith(("1'b", "0'b", "1'h", "0'h", "n_eco_")):
                    continue
                if any(v.startswith(p) for p in _PLACEHOLDER_PREFIXES):
                    continue
                _dm, primary_inputs, _op, _ci = _index_module_body(host, ref_dir, stage)
                if primary_inputs is None:
                    continue
                base_m = _BIT_RE.match(v)
                base_name = base_m.group(1) if base_m else v
                if base_name not in primary_inputs:
                    continue   # not a primary input of this module — Check 38's territory
                verdict, _par, term = net_parity_hierarchical(
                    v, host, ref_dir, stage, instance_scope=e.get('instance_scope'))
                if verdict == 'INVERTED':
                    issues.append(
                        f"CRITICAL/68-CROSS-MODULE-PRIMARY-INPUT-INVERTED: "
                        f"{e.get('change_type')} {inst}.{pin} = {v!r} ({stage}) is a "
                        f"primary input of module {host!r} whose TRUE origin (traced "
                        f"hierarchically to {term}) is INVERTED relative to this bare port "
                        f"name — {inst}.{pin} silently carries the LOGICAL COMPLEMENT of "
                        f"{v!r}. A gate assuming direct/non-inverted value here (e.g. XNOR2 "
                        f"for an equality compare) computes the WRONG function for this "
                        f"operand. Fix: swap the gate function for this leaf (e.g. XNOR2->XOR2 "
                        f"if this is the only inverted operand), insert a compensating INV, or "
                        f"bind a non-inverted equivalent net if one exists in this module scope.")
                elif verdict == 'UNDETERMINED':
                    issues.append(
                        f"MEDIUM/68-CROSS-MODULE-PRIMARY-INPUT-UNVERIFIED: "
                        f"{e.get('change_type')} {inst}.{pin} = {v!r} ({stage}) is a "
                        f"primary input of module {host!r} whose true origin could NOT be "
                        f"structurally traced across the hierarchy (reason: {term}) — "
                        f"polarity relative to its RTL name is UNVERIFIED, not confirmed "
                        f"correct. Verify via Formality find_equivalent_nets before trusting "
                        f"this gate, or route this change through complete mode.")
    return issues
