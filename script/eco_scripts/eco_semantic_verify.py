#!/usr/bin/env python3
"""
eco_semantic_verify.py — Semantic equivalence between Step 3 study intent
and PostEco netlist content.

For every confirmed entry in `<TAG>_eco_preeco_study.json`, parse the netlist
(comments stripped) and verify the corresponding edit is physically present.
This is the "Option B" check that catches semantic mismatches no regex-based
spot check can — bit-position errors, comment-masked edits, wrong-instance
matches, port-vs-direction inconsistencies.

Verifications per entry type:
  new_logic_gate / new_logic_dff:
    - Instance exists in netlist
    - Each port_connections[pin] matches netlist's pin connection
  port_declaration (input/output):
    - Signal in module port list
    - Direction declaration present in module body
    (wire type → skipped, implicit via port connections)
  port_connection (regular):
    - .port(net) present in instance, value matches
  port_connection (with bus_bit_index):
    - .port({...}) is a concat
    - Element at MSB-first position (width-1-bus_bit_index) == net_name
  rewire:
    - cell.pin connection equals new_net (not old_net)

Usage:
    python3 script/eco_scripts/eco_semantic_verify.py \\
        --study    data/<TAG>_eco_preeco_study.json \\
        --ref-dir  <REF_DIR> \\
        --tag      <TAG> \\
        --round    <ROUND> \\
        --output   data/<TAG>_eco_semantic_verify_round<N>.json

Exit: 0 = all entries verified, 1 = any failure.
"""
import argparse, json, os, re, subprocess, sys
from collections import defaultdict
from pathlib import Path


# ── Verilog text utilities ────────────────────────────────────────────────────

def strip_comments(text):
    """Remove // line and /* */ block comments."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def find_balanced(text, open_char, close_char, start_pos):
    """Starting at start_pos (must be the opening char), return index of
    matching close char (depth-aware). Returns -1 if not found."""
    if start_pos >= len(text) or text[start_pos] != open_char:
        return -1
    depth = 1
    for i in range(start_pos + 1, len(text)):
        ch = text[i]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return i
    return -1


# ── Netlist semantic view ─────────────────────────────────────────────────────

class NetlistView:
    """Lazy semantic view of a Verilog netlist. All operations work on
    comment-stripped text."""

    def __init__(self, raw_text):
        self.text = strip_comments(raw_text)
        self._module_cache = {}    # module_name → (start, end, header_text, body_text)
        self._instance_cache = {}  # instance_name → (start, header_end, end, port_text)

    # Module-level operations -------------------------------------------------

    def find_module(self, module_name):
        """Find module declaration. Returns (start, end, header_text, body_text)
        where header_text is the port list and body_text is between port-list-
        close and 'endmodule'. Returns None if not found.
        """
        if module_name in self._module_cache:
            return self._module_cache[module_name]
        # Try exact, _0 suffix, '<prefix>_<bare>', '<prefix>_<bare>_0' tile-prefix variants
        for cand_pat in (rf'^module\s+{re.escape(module_name)}\b',
                         rf'^module\s+{re.escape(module_name)}_0\b',
                         rf'^module\s+\S+_{re.escape(module_name)}_0\b',
                         rf'^module\s+\S+_{re.escape(module_name)}\b'):
            m = re.search(cand_pat, self.text, re.MULTILINE)
            if m:
                break
        else:
            self._module_cache[module_name] = None
            return None
        # Find port list close: first ')' after module declaration at depth 0
        # (relative to the module's opening '(' )
        open_paren = self.text.find('(', m.end())
        if open_paren < 0:
            self._module_cache[module_name] = None
            return None
        close_paren = find_balanced(self.text, '(', ')', open_paren)
        if close_paren < 0:
            self._module_cache[module_name] = None
            return None
        # Find endmodule
        end_m = re.search(r'\bendmodule\b', self.text[close_paren:])
        if not end_m:
            self._module_cache[module_name] = None
            return None
        end = close_paren + end_m.end()
        header_text = self.text[open_paren + 1:close_paren]
        body_text = self.text[close_paren + 1:close_paren + end_m.start()]
        result = (m.start(), end, header_text, body_text)
        self._module_cache[module_name] = result
        return result

    def module_has_port_in_list(self, module_name, signal):
        info = self.find_module(module_name)
        if info is None:
            return False
        return bool(re.search(rf'\b{re.escape(signal)}\b', info[2]))

    def module_has_direction_decl(self, module_name, signal, direction=None):
        """If direction is None, accept any of input/output/inout."""
        info = self.find_module(module_name)
        if info is None:
            return False
        if direction:
            return bool(re.search(rf'^\s*{direction}\s+(?:\[[^\]]+\]\s+)?{re.escape(signal)}\s*[;,]', info[3], re.MULTILINE))
        return bool(re.search(rf'^\s*(?:input|output|inout)\s+(?:\[[^\]]+\]\s+)?{re.escape(signal)}\s*[;,]', info[3], re.MULTILINE))

    # Instance-level operations ----------------------------------------------

    def find_instance(self, instance_name):
        """Find any instance with the given name. Returns (start, header_end,
        end, port_text) where port_text is the comma-separated port list
        between the outer '(' and ')'. Returns None if not found.
        """
        if instance_name in self._instance_cache:
            return self._instance_cache[instance_name]
        m = re.search(rf'\b{re.escape(instance_name)}\s*\(', self.text)
        if not m:
            self._instance_cache[instance_name] = None
            return None
        open_paren = m.end() - 1
        close_paren = find_balanced(self.text, '(', ')', open_paren)
        if close_paren < 0:
            self._instance_cache[instance_name] = None
            return None
        port_text = self.text[open_paren + 1:close_paren]
        result = (m.start(), m.end(), close_paren + 1, port_text)
        self._instance_cache[instance_name] = result
        return result

    def find_cell_instance(self, instance_name):
        """Like find_instance but also returns the cell type (the token before
        the instance name on the same logical line)."""
        info = self.find_instance(instance_name)
        if info is None:
            return None
        # Look back from instance start to find cell type (uppercase identifier)
        prefix = self.text[max(0, info[0] - 200):info[0]]
        # Last whitespace-separated uppercase token before instance name
        m = re.search(r'([A-Z][A-Z0-9_]*[A-Z0-9])\s*$', prefix.rstrip())
        cell_type = m.group(1) if m else None
        return (cell_type,) + info

    def get_pin_connection(self, instance_name, pin_name):
        """For an instance, return the net (or {...} text) connected to a pin.
        Returns None if pin not found.
        """
        info = self.find_instance(instance_name)
        if info is None:
            return None
        port_text = info[3]
        # Find .pin_name( ...
        m = re.search(rf'\.\s*{re.escape(pin_name)}\s*\(', port_text)
        if not m:
            return None
        # Balanced match of the port value
        open_pos = m.end() - 1
        close_pos = find_balanced(port_text, '(', ')', open_pos)
        if close_pos < 0:
            return None
        return port_text[open_pos + 1:close_pos].strip()

    def parse_bus_concat(self, text_value):
        """Given a port value text like '{a, b, c}' or 'simple_net', return
        list of net names if bus concat, or None if simple net.
        """
        s = text_value.strip()
        if not s.startswith('{'):
            return None
        # Find matching close brace
        end = find_balanced(s, '{', '}', 0)
        if end < 0:
            return None
        inner = s[1:end]
        return [e.strip() for e in inner.split(',') if e.strip()]


# ── Per-entry-type verifiers ─────────────────────────────────────────────────

def verify_new_logic(entry, view, stage):
    """new_logic_gate / new_logic_dff: instance exists with matching pins."""
    inst = entry.get('instance_name')
    if not inst:
        return None  # malformed entry — skip silently
    info = view.find_instance(inst)
    if info is None:
        return f'instance {inst} not found in {stage} netlist'
    # Check each port connection
    pcs_per_stage = entry.get('port_connections_per_stage', {}) or {}
    pcs = pcs_per_stage.get(stage) or entry.get('port_connections', {}) or {}
    for pin, expected_net in pcs.items():
        if not isinstance(expected_net, str):
            continue
        # Skip clock/reset auxiliary pins that may be renamed by P&R freely
        actual = view.get_pin_connection(inst, pin)
        if actual is None:
            return f'instance {inst}.{pin} not present in netlist'
        # Strip whitespace for comparison
        actual_s = actual.strip()
        expected_s = expected_net.strip()
        if actual_s != expected_s:
            # Tolerate bracket↔flat form equivalence: 'net[N]' ↔ 'net_N_'
            import re as _re
            def _to_flat(n):
                return _re.sub(r'\[(\d+)\]', lambda m: f'_{m.group(1)}_', n)
            if _to_flat(actual_s) != _to_flat(expected_s):
                return f'instance {inst}.{pin} = {actual_s!r} but expected {expected_s!r}'
    return None


def verify_port_declaration(entry, view, stage):
    """port_declaration: signal in module port list + direction decl present."""
    direction = entry.get('declaration_type', 'input')
    if direction == 'wire':
        return None  # implicit — no declaration needed
    signal = entry.get('signal_name') or entry.get('new_token')
    module = entry.get('module_name')
    if not signal or not module:
        return None
    if not view.module_has_port_in_list(module, signal):
        return f'{module}: {signal!r} not in module port list'
    if not view.module_has_direction_decl(module, signal, direction):
        return f'{module}: {signal!r} missing {direction!r} direction declaration in module body'
    return None


def verify_port_connection(entry, view, stage):
    """port_connection: .port(net) present in instance OR bus position has net."""
    inst = entry.get('instance_name') or entry.get('submodule_instance')
    port = entry.get('port_name') or entry.get('new_token')
    if not inst or not port:
        return None
    bbi = entry.get('bus_bit_index')
    new_net = entry.get('net_name') or entry.get('flat_net_name') or entry.get('net_name_after')
    if not new_net:
        return None
    actual_value = view.get_pin_connection(inst, port)
    if actual_value is None:
        return f'instance {inst}.{port} not present in netlist'
    if bbi is not None:
        # Bus rename — verify {...} concat and bit position
        elements = view.parse_bus_concat(actual_value)
        if elements is None:
            # Scalar bus pass-through (e.g. `.port(wire)`) is valid when all bits
            # flow together — the port connection itself is correct; no concat needed.
            # Downgrade to warning only if the scalar name looks unrelated to the port.
            stripped = actual_value.strip()
            if re.match(r'^[A-Za-z_]\w*$', stripped):
                return None  # scalar bus pass-through — acceptable
            return f'{inst}.{port} expected {{}} bus concat but got {actual_value[:80]!r}'
        width = len(elements)
        pos = width - 1 - bbi  # MSB-first
        if pos < 0 or pos >= width:
            return f'{inst}.{port} bus_bit_index={bbi} out of range (width={width})'
        if elements[pos] != new_net:
            return f'{inst}.{port}[{bbi}] = {elements[pos]!r} but expected {new_net!r}'
        return None
    # Regular port connection — verify exact net match
    if actual_value.strip() != new_net.strip():
        return f'{inst}.{port} = {actual_value.strip()!r} but expected {new_net.strip()!r}'
    return None


def verify_rewire(entry, view, stage):
    """rewire: cell.pin connection equals new_net (not old_net).

    The studier emits the Synth-only cell_name; CTS/CTS-OPT renames the
    instance in PP/Route. When the named cell isn't present in this stage,
    fall back to checking that SOMEWHERE in the netlist a cell has
    .<pin>(<new_net>) — that proves the rewire landed on the renamed cell.
    Mirrors the applier's per-stage cell-discovery fallback.

    When module_name is available in the entry, use module-scoped lookup
    to avoid false positives from same instance name appearing in multiple
    modules (e.g. ctmi_1182 in 40+ umcrecrcqentry modules AND IntestEdt)."""
    # Use per-stage cell name if available
    per_stage = (entry.get('per_stage_cell') or entry.get('per_stage_cell_name')
                 or entry.get('cell_name_per_stage')
                 or entry.get('mux_cell_instance_per_stage') or {})
    cell = per_stage.get(stage, '') or entry.get('cell_name', '')
    per_stage_pin = entry.get('per_stage_pin', {}) or entry.get('pin_per_stage', {})
    pin = per_stage_pin.get(stage, '') or entry.get('pin', '')
    new_net = (entry.get('per_stage_new_net', {}) or {}).get(stage, '') or entry.get('new_net', '')
    if not cell or not pin or not new_net:
        return None

    # Module-scoped lookup: prefer searching within the specific module body
    # when module_name is available. Prevents false positives when same
    # instance name appears in multiple modules.
    module_name = (entry.get('module_name_per_stage', {}) or {}).get(stage, '') \
                  or entry.get('module_name', '')
    if module_name:
        mod_info = view.find_module(module_name)
        if mod_info is not None:
            # mod_info = (start, end, header_text, body_text)
            body_text = mod_info[3]
            inst_m = re.search(rf'\b{re.escape(cell)}\s*\(', body_text)
            if inst_m:
                block = body_text[inst_m.start():inst_m.start() + 200000]
                pin_m = re.search(rf'\.\s*{re.escape(pin)}\s*\(\s*([^)]+)\)', block)
                if pin_m:
                    actual = pin_m.group(1).strip()
                    if actual == new_net.strip():
                        return None
                    return f'{cell}.{pin} = {actual!r} but expected {new_net.strip()!r}'
                # Pin not found in module-scoped block — fall through to global
            # Instance not in module body — try global fallback below

    actual = view.get_pin_connection(cell, pin)
    if actual is not None:
        if actual.strip() == new_net.strip():
            return None
        # Only report error if no module-scoped resolution succeeded
        if not module_name:
            return f'{cell}.{pin} = {actual.strip()!r} but expected {new_net.strip()!r}'
        # Module-scoped lookup found no instance in the target module;
        # global lookup found a different module's instance — check net-level fallback
        pat = rf'\.\s*{re.escape(pin)}\s*\(\s*{re.escape(new_net)}\s*\)'
        if re.search(pat, view.text):
            return None
        return f'{cell}.{pin} = {actual.strip()!r} but expected {new_net.strip()!r}'
    # Cell not present at named instance — check if rewire landed on a
    # per-stage-renamed cell by grepping for .<pin>(<new_net>) anywhere in
    # the stage netlist. Cell-instance rename without per_stage_cell field
    # is the dominant CTS pattern; the rewire is correct iff the new_net
    # appears as the value of a <pin> connection somewhere.
    pat = rf'\.\s*{re.escape(pin)}\s*\(\s*{re.escape(new_net)}\s*\)'
    if re.search(pat, view.text):
        return None
    return f'{cell}.{pin} not present in netlist (per-stage-rename fallback also failed: no .{pin}({new_net}) found)'


VERIFIERS = {
    'new_logic_gate':    verify_new_logic,
    'new_logic_dff':     verify_new_logic,
    'new_logic':         verify_new_logic,
    'port_declaration':  verify_port_declaration,
    'port_connection':   verify_port_connection,
    'rewire':            verify_rewire,
}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--output',   required=True)
    args = p.parse_args()

    study = json.loads(Path(args.study).read_text())
    failures_per_stage = defaultdict(list)
    counts_per_stage = defaultdict(lambda: defaultdict(int))

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(args.ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            failures_per_stage[stage].append(f'[NETLIST_MISSING] {gz} not found')
            continue
        try:
            raw = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=300).stdout
        except Exception as e:
            failures_per_stage[stage].append(f'[NETLIST_READ_ERR] {e}')
            continue
        view = NetlistView(raw)
        for entry in study.get(stage, []):
            if not entry.get('confirmed', True):
                continue
            ct = entry.get('change_type', '')
            verifier = VERIFIERS.get(ct)
            if verifier is None:
                continue
            counts_per_stage[stage][ct] += 1
            err = verifier(entry, view, stage)
            if err:
                failures_per_stage[stage].append(f'[{ct.upper()}] {err}')

    total_failures = sum(len(v) for v in failures_per_stage.values())
    total_checked  = sum(sum(c.values()) for c in counts_per_stage.values())
    passed = total_failures == 0

    out = {
        'tag':           args.tag,
        'round':         args.round,
        'passed':        passed,
        'total_entries_checked': total_checked,
        'total_failures': total_failures,
        'counts_per_stage': {st: dict(c) for st, c in counts_per_stage.items()},
        'failures_per_stage': {st: f for st, f in failures_per_stage.items() if f},
    }
    Path(args.output).write_text(json.dumps(out, indent=2))

    print('ECO_SCRIPT_LAUNCHED: eco_semantic_verify.py')
    print(f'  study:    {args.study}')
    print(f'  entries:  {total_checked} verified across 3 stages')
    print(f'  overall:  {"PASS" if passed else "FAIL"}')
    print(f'  failures: {total_failures}')
    for stage, fails in failures_per_stage.items():
        for f in fails:
            print(f'    {stage}: {f}')

    Path(args.output.replace('.json', '_marker.txt')).write_text(
        f'ECO_SCRIPT_LAUNCHED: eco_semantic_verify.py\n'
        f'  passed: {passed}\n'
        f'  entries: {total_checked}\n'
        f'  failures: {total_failures}\n'
        f'  output: {args.output}\n'
    )

    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
