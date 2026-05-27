#!/usr/bin/env python3
"""
eco_modei_chain_input_check.py — Deterministic Mode-I detector for chain leaves.

For a single chain leaf input that resolves to a bus-bit `<bus>[<N>]`, scan
the host module's gate-level body in each stage to determine whether that
bit lands on `UNCONNECTED_*` at a child instance's port-bus connection.
If yes, walk into the child submodule body to locate the inner sub-instance
whose port-bus output bit is also UNCONNECTED, and emit JSON snippets that
the studier can splice into the study verbatim:

  - `unconnected_rewires` entry (for the parent module, renames per-stage
    UNCONNECTED at the child instance's port-bus bit position to a flat name)
  - `port_connection` entry (inside the child module, wires the inner
    sub-instance's port-bus bit to the child's own output port self-loop)

Usage:
    python3 eco_modei_chain_input_check.py \\
        --ref-dir <REF_DIR> \\
        --host-module <gate-level module name, e.g. ddrss_umccmd_t_umccmd> \\
        --chain-input <bus[bit] form, e.g. REG_UmcCfgEco[1]> \\
        --output <data/<TAG>_eco_modei_<leaf>.json>

Algorithm (fully deterministic — no LLM reasoning required):
  1. Parse chain_input as `<bus>[<bit>]` (skip if not bus-bit form).
  2. For each PreEco stage:
     a. Locate host_module body (handles `<host>` and `<host>_0` variants).
     b. Find ALL child instances `<child_module> <inst> ( ... )` whose port
        list contains `.<bus>(...)`. If multiple, prefer ones whose `.<bus>`
        connects via a `{...}` concat (multi-bit busses).
     c. Parse the `.<bus>({elem_MSB, ..., elem_LSB})` concat and extract the
        element at MSB-first position (width-1-bit) — that is bit[bit].
     d. If element matches `^(SYNOPSYS_)?UNCONNECTED_\\d+$`, record:
        - parent_unc_per_stage[stage] = element name
        - parent_inst (the child instance name)
        - child_module (the submodule type)
  3. If parent UNCONNECTED found in ANY stage, walk into child_module body:
     a. For each sub-instance `<sub_module> <sub_inst> ( ... )`, look for
        `.<sub_port>({...})` connections where bit[bit] is also UNCONNECTED.
        The sub_port name may differ from the outer bus name; identify by
        bit-width matching first, then UNCONNECTED-at-same-bit pattern.
     b. Record inner_unc_per_stage + sub_inst + sub_port.
  4. Emit JSON: status, parent/inner findings, suggested unconnected_rewires
     + child port_connection snippets.

Exit codes:
  0  — Mode-I detected and JSON emitted (caller should splice)
  1  — chain input is not bus-bit form, OR not UNCONNECTED at any parent
       slot, OR parent UNCONNECTED found but no inner driver to wire up
"""
import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path


UNC_RE = re.compile(r'^(?:SYNOPSYS_)?UNCONNECTED_\d+$')


def _strip_verilog_comments(text):
    """Strip `//` line comments and `/* ... */` block comments. Critical
    before any depth-counting on `(`, `)`, `{`, `}` because comments may
    contain unbalanced punctuation (e.g. `// old line had ) ;`)."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def _read_stage(ref_dir, stage):
    """Return PreEco stage netlist text (decompressed, comments stripped)."""
    p = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
    if not p.is_file():
        return None
    try:
        with gzip.open(p, 'rt') as f:
            return _strip_verilog_comments(f.read())
    except Exception:
        return None


def _find_module_body(text, module_name):
    """Locate `module <name> ... endmodule` window in text. Tries `<name>`
    then `<name>_0` (Route stage uniquification). Returns (start, end, body)
    or (None, None, '')."""
    for cand in (module_name, f'{module_name}_0'):
        m = re.search(rf'^module\s+{re.escape(cand)}\b.*?^endmodule\b',
                      text, re.MULTILINE | re.DOTALL)
        if m:
            return m.start(), m.end(), m.group(0)
    return None, None, ''


def _find_child_instances_with_bus(body, bus_name):
    """Within a module body, find every child-instance block that wires
    `.<bus_name>(...)`. Returns list of dicts:
      {sub_module_type, inst_name, port_value (raw text after `.bus(` up to
       its matching `)`), inst_block_start_offset_in_body}.
    """
    hits = []
    # Cell instance pattern: <UPPER_TYPE> <lower_inst> ( ... ) ;
    # We need to walk instance by instance; use a simple state machine.
    pat_inst_start = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',
                                re.MULTILINE)
    for m in pat_inst_start.finditer(body):
        sub_type, inst_name = m.group(1), m.group(2)
        # Skip Verilog keywords that aren't instance types
        if sub_type in ('module', 'endmodule', 'input', 'output', 'inout',
                        'wire', 'reg', 'tri', 'wand', 'wor', 'assign',
                        'always', 'initial', 'parameter', 'localparam',
                        'function', 'task', 'generate', 'endgenerate'):
            continue
        # Find balanced () for the instance block
        open_pos = m.end() - 1
        depth = 0
        end_pos = None
        for i in range(open_pos, len(body)):
            c = body[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break
        if end_pos is None:
            continue
        port_block = body[open_pos + 1:end_pos]
        # Look for `.<bus_name>(<value>)` within the port block
        pmatch = re.search(rf'\.\s*{re.escape(bus_name)}\s*\(', port_block)
        if not pmatch:
            continue
        # Balanced match for the port value
        v_open = pmatch.end() - 1
        v_depth = 0
        v_end = None
        for j in range(v_open, len(port_block)):
            c = port_block[j]
            if c == '(':
                v_depth += 1
            elif c == ')':
                v_depth -= 1
                if v_depth == 0:
                    v_end = j
                    break
        if v_end is None:
            continue
        port_value = port_block[v_open + 1:v_end].strip()
        hits.append({
            'sub_module_type': sub_type,
            'inst_name':       inst_name,
            'port_value':      port_value,
            'inst_block':      body[m.start():end_pos + 1],
        })
    return hits


def _parse_concat(port_value):
    """Parse `{elem0, elem1, ..., elemN}` → list (MSB-first). Returns None
    if not a concat. Strips comments + whitespace."""
    # Strip Verilog comments
    cleaned = re.sub(r'//[^\n]*', '', port_value)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()
    if not cleaned.startswith('{'):
        return None
    # Find balanced `{...}`
    depth = 0
    end = None
    for i, c in enumerate(cleaned):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    inner = cleaned[1:end]
    # Split at top-level commas (depth-aware to handle nested concats)
    elems = []
    cur = ''
    d = 0
    for c in inner:
        if c == '{':
            d += 1
        elif c == '}':
            d -= 1
        if c == ',' and d == 0:
            elems.append(cur.strip())
            cur = ''
        else:
            cur += c
    if cur.strip():
        elems.append(cur.strip())
    return elems


def _find_unc_at_bit(elems, bit):
    """elems is MSB-first list. For bit position N (LSB=0), the index from
    the left is len(elems) - 1 - N. Returns the element name if
    UNCONNECTED, else None."""
    if elems is None:
        return None
    n = len(elems)
    idx = n - 1 - bit
    if idx < 0 or idx >= n:
        return None
    el = elems[idx].strip()
    return el if UNC_RE.match(el) else None


def _scan_inner(child_module, ref_dir, stage, bit, parent_bus):
    """Walk into child_module's body in <stage> netlist and find the
    sub-instance whose port-bus bit[bit] is UNCONNECTED AND whose concat
    contains a self-reference to parent_bus (e.g. `REG_UmcCfgEco[0]`).

    The self-reference discriminator is critical — many child sub-instances
    have UNCONNECTED at bit[N] for unrelated busses; the ONE we want is
    the sub-port that DRIVES the parent module's output bus (so its concat
    contains at least one `<parent_bus>[<x>]` element somewhere).

    Returns {sub_inst, sub_module_type, sub_port, unc_name} or None.
    """
    text = _read_stage(ref_dir, stage)
    if text is None:
        return None
    _, _, body = _find_module_body(text, child_module)
    if not body:
        return None
    pat_inst_start = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',
                                re.MULTILINE)
    self_ref_re = re.compile(rf'\b{re.escape(parent_bus)}\s*\[\s*\d+\s*\]')
    matches_with_selfref = []  # preferred — concat has parent_bus self-reference
    matches_no_selfref   = []  # fallback — UNCONNECTED at bit but no self-ref
    for m in pat_inst_start.finditer(body):
        sub_type, inst_name = m.group(1), m.group(2)
        if sub_type in ('module', 'endmodule', 'input', 'output', 'inout',
                        'wire', 'reg', 'tri', 'wand', 'wor', 'assign',
                        'always', 'initial', 'parameter', 'localparam',
                        'function', 'task', 'generate', 'endgenerate'):
            continue
        open_pos = m.end() - 1
        depth = 0
        end_pos = None
        for i in range(open_pos, len(body)):
            c = body[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break
        if end_pos is None:
            continue
        port_block = body[open_pos + 1:end_pos]
        for pmatch in re.finditer(r'\.\s*([A-Za-z_]\w*)\s*\(', port_block):
            sub_port = pmatch.group(1)
            v_open = pmatch.end() - 1
            v_depth = 0
            v_end = None
            for j in range(v_open, len(port_block)):
                c = port_block[j]
                if c == '(':
                    v_depth += 1
                elif c == ')':
                    v_depth -= 1
                    if v_depth == 0:
                        v_end = j
                        break
            if v_end is None:
                continue
            port_value = port_block[v_open + 1:v_end].strip()
            elems = _parse_concat(port_value)
            if elems is None:
                continue
            if bit >= len(elems):
                continue
            unc = _find_unc_at_bit(elems, bit)
            if not unc:
                continue
            hit = {
                'sub_inst': inst_name,
                'sub_module_type': sub_type,
                'sub_port': sub_port,
                'unc_name': unc,
            }
            if self_ref_re.search(port_value):
                matches_with_selfref.append(hit)
            else:
                matches_no_selfref.append(hit)
    if matches_with_selfref:
        return matches_with_selfref[0]
    if matches_no_selfref:
        return matches_no_selfref[0]
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ref-dir',     required=True,
                   help='REF_DIR containing data/PreEco/<stage>.v.gz')
    p.add_argument('--host-module', required=True,
                   help='Gate-level module name where the new chain lives '
                        '(e.g. ddrss_umccmd_t_umccmd)')
    p.add_argument('--chain-input', required=True,
                   help='Chain leaf input signal as <bus>[<bit>] '
                        '(e.g. REG_UmcCfgEco[1])')
    p.add_argument('--jira',        required=False, default='',
                   help='Optional JIRA — used in named_net suggestion')
    p.add_argument('--output',      required=True,
                   help='Output JSON path')
    args = p.parse_args()

    # Parse chain_input as <bus>[<bit>]
    m = re.match(r'^([A-Za-z_]\w*)\[(\d+)\]$', args.chain_input.strip())
    if not m:
        out = {
            'status': 'NOT_BUS_BIT',
            'reason': f'chain_input {args.chain_input!r} is not <bus>[<bit>] form '
                      f'— Mode-I check only applies to bus-bit references',
            'chain_input': args.chain_input,
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f'NOT_BUS_BIT: {args.chain_input}', file=sys.stderr)
        sys.exit(1)
    bus, bit = m.group(1), int(m.group(2))

    # Per-stage scan of the host module for `.<bus>(...)` connections
    parent_findings = {}   # stage → {parent_inst, child_module_type, parent_unc, parent_inst_per_stage_actual}
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        text = _read_stage(args.ref_dir, stage)
        if text is None:
            continue
        _, _, body = _find_module_body(text, args.host_module)
        if not body:
            continue
        candidates = _find_child_instances_with_bus(body, bus)
        if not candidates:
            continue
        # Prefer the candidate whose .<bus>(...) is a {...} concat AND
        # has bit[bit] as UNCONNECTED; fall back to other UNCONNECTED hits.
        chosen = None
        for c in candidates:
            elems = _parse_concat(c['port_value'])
            unc = _find_unc_at_bit(elems, bit) if elems else None
            if unc:
                chosen = (c, unc)
                break
        if chosen is None:
            continue
        c, unc = chosen
        parent_findings[stage] = {
            'parent_inst':      c['inst_name'],
            'child_module':     c['sub_module_type'],
            'parent_unc':       unc,
            'bus':              bus,
            'bit':              bit,
        }

    if not parent_findings:
        out = {
            'status': 'NO_PARENT_UNC',
            'reason': f'host_module {args.host_module!r} has no child instance '
                      f'with .<{bus}>({{...}}) where bit[{bit}] is UNCONNECTED '
                      f'in any stage — Mode-I rewire not needed',
            'chain_input': args.chain_input,
            'host_module': args.host_module,
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f'NO_PARENT_UNC: {args.chain_input} in {args.host_module}', file=sys.stderr)
        sys.exit(1)

    # Determine canonical parent_inst + child_module (use the first stage's findings;
    # all stages should agree on these — only the UNCONNECTED literal varies)
    ref_stage = next(iter(parent_findings))
    parent_inst = parent_findings[ref_stage]['parent_inst']
    child_module_base = parent_findings[ref_stage]['child_module']
    # Strip Route's `_0` uniquification suffix to get the canonical type
    child_module_canon = re.sub(r'_\d+$', '', child_module_base) if child_module_base.endswith('_0') else child_module_base

    parent_unc_per_stage = {s: f['parent_unc'] for s, f in parent_findings.items()}

    # Walk into child for each stage. Pass parent_bus so inner scan can prefer
    # the sub-port whose concat self-references the parent bus (= the actual
    # driver of the parent's output port bus, vs. unrelated UNCONNECTED hits).
    inner_findings = {}   # stage → {sub_inst, sub_module_type, sub_port, unc_name}
    for stage, f in parent_findings.items():
        inner = _scan_inner(f['child_module'], args.ref_dir, stage, bit, bus)
        if inner:
            inner_findings[stage] = inner

    if not inner_findings:
        out = {
            'status': 'NO_INNER_DRIVER',
            'reason': f'parent UNCONNECTED detected for {args.chain_input} but '
                      f'no sub-instance inside {child_module_canon!r} has the '
                      f'matching bit[{bit}] as UNCONNECTED — engineer review needed',
            'chain_input': args.chain_input,
            'host_module': args.host_module,
            'parent_findings': parent_findings,
        }
        Path(args.output).write_text(json.dumps(out, indent=2))
        print(f'NO_INNER_DRIVER: {args.chain_input}', file=sys.stderr)
        sys.exit(1)

    # All ingredients found — emit suggested study entries
    flat_net = f'{bus}_{bit}_'
    suggested_unconnected_rewires = {
        'original':                 parent_unc_per_stage.get(ref_stage, ''),
        'original_per_stage':       parent_unc_per_stage,
        'named_net':                flat_net,
        'needs_explicit_wire_decl': True,
        'port_bus_instance':        parent_inst,
        'port_bus_instance_per_stage': {s: parent_inst for s in parent_unc_per_stage},
        'port_bus_name':            bus,
        'port_bus_bit':             bit,
        'reason':                   f'Mode I — chain leaf {args.chain_input} is '
                                    f'UNCONNECTED at parent {parent_inst}.{bus} bit[{bit}] '
                                    f'(detected by eco_modei_chain_input_check.py)',
    }
    # Per-stage sub-instance + sub-port (Route may have _0 suffix on sub_inst type)
    sub_inst_canon = inner_findings[ref_stage]['sub_inst']
    sub_port = inner_findings[ref_stage]['sub_port']
    sub_module_per_stage = {s: f['sub_module_type'] for s, f in inner_findings.items()}
    sub_inst_per_stage = {s: f['sub_inst'] for s, f in inner_findings.items()}
    inner_unc_per_stage = {s: f['unc_name'] for s, f in inner_findings.items()}

    suggested_child_port_conn = {
        'change_type':         'port_connection',
        'module_name':         child_module_canon,
        'parent_module':       child_module_canon,
        'instance_name':       sub_inst_canon,
        'instance_name_per_stage': sub_inst_per_stage,
        'child_module_name':   sub_module_per_stage.get(ref_stage, ''),
        'child_module_name_per_stage': sub_module_per_stage,
        'port_name':           sub_port,
        'bus_bit_index':       bit,
        'net_name':            f'{bus}[{bit}]',
        'net_name_after':      f'{bus}[{bit}]',
        'net_name_before':     inner_unc_per_stage,
        'force_reapply':       True,
        'confirmed':           True,
        'reason':              f'Mode I exception — sub-instance {sub_inst_canon}.{sub_port}[{bit}] '
                               f'was UNCONNECTED inside {child_module_canon!r}; wire to OWN output '
                               f'port {bus}[{bit}] (self-loop, legal in port_connections)',
        'source':              'eco_modei_chain_input_check.py',
    }

    # PARENT-side port_connection — wires the WRAPPER instance (parent_inst,
    # e.g. REGCMD) bit at the host module's level, replacing the UNCONNECTED_*
    # slot in the bus concat with the flat consumer net (e.g. REG_UmcCfgEco_12_).
    #
    # Without this entry, the wrapper's output bit reaches the host module
    # bus concat at position [bit] as `UNCONNECTED_*` (or never reaches),
    # leaving the consumer wire `<bus>_<bit>_` declared but undriven in the
    # host module — guaranteed FM Mode A on the new ECO gate's input cone.
    suggested_parent_port_conn = {
        'change_type':         'port_connection',
        'module_name':         args.host_module,
        'parent_module':       args.host_module,
        'instance_name':       parent_inst,
        'instance_name_per_stage': {s: parent_inst for s in parent_unc_per_stage},
        'child_module_name':   child_module_canon,
        'child_module_name_per_stage': {s: child_module_canon
                                        for s in parent_unc_per_stage},
        'port_name':           bus,
        'bus_bit_index':       bit,
        'net_name':            flat_net,
        'net_name_after':      flat_net,
        'net_name_before':     parent_unc_per_stage,
        'force_reapply':       True,
        'confirmed':           True,
        'reason':              f'Mode I parent-bridge — wrapper instance '
                               f'{parent_inst}.{bus} bit[{bit}] was UNCONNECTED at '
                               f'host module {args.host_module!r}; wire to consumer net '
                               f'{flat_net} so the wrapper\'s exported bit lands on the '
                               f'flat wire the new ECO cell consumes (closes the cross-'
                               f'module driver chain — without this, the consumer is undriven '
                               f'and FM fails Mode A).',
        'source':              'eco_modei_chain_input_check.py',
    }

    out = {
        'status':       'MODEI_DETECTED',
        'chain_input':  args.chain_input,
        'host_module':  args.host_module,
        'bus':          bus,
        'bit':          bit,
        'parent_inst':  parent_inst,
        'child_module': child_module_canon,
        'parent_unc_per_stage':  parent_unc_per_stage,
        'sub_inst':     sub_inst_canon,
        'sub_port':     sub_port,
        'inner_unc_per_stage':   inner_unc_per_stage,
        'sub_module_per_stage':  sub_module_per_stage,
        'sub_inst_per_stage':    sub_inst_per_stage,
        'suggested_chain_input_replacement': flat_net,
        'suggested_unconnected_rewires_entry': suggested_unconnected_rewires,
        'suggested_child_port_connection_entry':  suggested_child_port_conn,
        'suggested_parent_port_connection_entry': suggested_parent_port_conn,
        'note': (
            f'Splice suggested_unconnected_rewires_entry into the new_logic_dff '
            f'entry whose chain references {args.chain_input}. Splice BOTH '
            f'suggested_child_port_connection_entry AND '
            f'suggested_parent_port_connection_entry into ALL three stage arrays '
            f'(Synthesize, PrePlace, Route) — the child entry wires the inner '
            f'sub-instance bit to the wrapper\'s internal bus wire, and the '
            f'parent entry wires the wrapper instance\'s output bit at the host '
            f'module level to {flat_net!r} so the consumer is actually driven. '
            f'Omitting the parent entry leaves the consumer wire declared but '
            f'undriven → guaranteed FM Mode A fail. Update the chain leaf input '
            f'from {args.chain_input!r} to {flat_net!r} on all relevant '
            f'new_logic_gate entries (port_connections + port_connections_per_stage).'
        ),
    }
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f'MODEI_DETECTED: {args.chain_input} → unconnected_rewires + child port_connection emitted',
          file=sys.stderr)
    print(f'  output: {args.output}', file=sys.stderr)
    sys.exit(0)


if __name__ == '__main__':
    main()
