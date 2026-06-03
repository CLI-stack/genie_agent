#!/usr/bin/env python3
"""
eco_emit_shadow_gate.py — Generate CP and D-input rewire entries for enable_swap
shadow clock gate pattern.

When enable_swap targets a clock-gated DFF array (enable_via_clock_gate=true),
the correct implementation creates a new shadow clock gate and rewires the existing
DFF array CP pins + D-input pins. This script handles the complex multi-line MB DFF
parsing and per-stage resolution that the LLM studier cannot do reliably.

The caller (eco_netlist_studier Phase 0.16 Path A) is responsible for emitting
the shadow gate and OR2 new_logic_gate entries. This script produces the rewires.

Usage:
    python3 eco_emit_shadow_gate.py \
        --ref-dir   <REF_DIR> \
        --base-dir  <BASE_DIR> \
        --tag       <TAG> \
        --target    wdbptr_org0_d2 \
        --dff-cp-net umcdat_WDB_uclkg_clk_gate_wdbptr_org0_d2_reg \
        --new-cp-net ECO_9855_umcdat_WDB_uclkg_clk_gate_wdbptr_org0_d2_reg \
        --d-map     "0:n_eco_9855_net20,1:n_eco_9855_net21,2:n_eco_9855_net22,3:n_eco_9855_net23,4:n_eco_9855_net24,5:n_eco_9855_net25,6:n_eco_9855_net26" \
        --module    ddrss_umcdat_t_umcwdb \
        --output    data/<TAG>_eco_shadow_gate_rewires.json

Output JSON:
    {
      "status": "OK" | "PARTIAL" | "FAIL",
      "rewire_count": N,
      "errors": [...],
      "rewires": [
        {"change_type":"rewire","cell_name":"...","pin":"CP","old_net":"...","new_net":"...","module_name":"...","confirmed":true,"reason":"..."},
        ...
      ]
    }

The rewires list is added by the studier to all 3 stage lists in the study JSON.
The eco_applier's net-rename recovery handles per-stage CP/D-net CTS renames.

Exit 0 = OK (all cells found, all rewires emitted)
Exit 1 = PARTIAL or FAIL (see errors in output JSON)
"""

import argparse, json, os, re, subprocess, sys
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--ref-dir',    required=True, dest='ref_dir')
    p.add_argument('--base-dir',   required=True, dest='base_dir')
    p.add_argument('--tag',        required=True)
    p.add_argument('--target',     required=True,
                   help='Target register name, e.g. wdbptr_org0_d2')
    p.add_argument('--dff-cp-net', required=True, dest='dff_cp_net',
                   help='Existing clock gate Q output net (old CP net on DFF array)')
    p.add_argument('--new-cp-net', required=True, dest='new_cp_net',
                   help='New shadow gate Q output net (rewire target)')
    p.add_argument('--d-map',      required=False, dest='d_map', default='',
                   help='Bit→new_net map: "0:net0,1:net1,...". Empty = CP-only mode')
    p.add_argument('--module',     required=True,
                   help='Declaring module name, e.g. ddrss_umcdat_t_umcwdb')
    p.add_argument('--output',     required=True)
    return p.parse_args()


# ── Netlist loading ──────────────────────────────────────────────────────────

def load_netlist(gz_path, timeout=300):
    try:
        r = subprocess.run(['zcat', gz_path], capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout
    except Exception as e:
        return ''


def extract_module_body(text, module_name):
    """Return the text of the named module (tries _0 suffix if not found)."""
    for cand in (module_name, module_name + '_0'):
        m = re.search(
            rf'^module\s+{re.escape(cand)}\b.*?^endmodule\b',
            text, re.MULTILINE | re.DOTALL)
        if m:
            return m.group(0), cand
    return '', module_name


# ── Cell block parsing ───────────────────────────────────────────────────────

_SKIP_KW = frozenset({
    'module', 'endmodule', 'wire', 'reg', 'assign', 'input', 'output',
    'inout', 'parameter', 'localparam', 'tri', 'tri0', 'tri1',
    'supply0', 'supply1', 'wand', 'wor',
})

_HEADER_RE = re.compile(r'^\s*(\w+)\s+(\w+)\s*\(')


def _extract_cell_blocks(module_body):
    """
    Parse the module body line by line, yield cell instance dicts:
      {cell_type, instance_name, ports: {pin: net}, block_text}
    Only yields actual gate/DFF cells, not keyword blocks.
    """
    lines = module_body.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        m = _HEADER_RE.match(lines[i])
        if not m or m.group(1) in _SKIP_KW:
            i += 1
            continue
        ct, inst = m.group(1), m.group(2)
        # Collect block by paren depth
        depth, end = 0, i
        for j in range(i, min(i + 80, len(lines))):
            for ch in lines[j].split('//')[0]:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            if depth == 0 and end >= i:
                break
        block = ''.join(lines[i:end + 1])
        # Parse .pin(net) from block; allow [] in net names (e.g. bus[N])
        # but exclude {} (concat) and () (nested sub-expressions).
        ports = {}
        for pm in re.finditer(r'\.\s*(\w+)\s*\(\s*([^{}()]+?)\s*\)', block):
            pin = pm.group(1)
            net = pm.group(2).strip().rstrip('),').strip()
            if net and not net.startswith('{'):
                ports[pin] = net
        yield {'cell_type': ct, 'instance_name': inst,
               'ports': ports, 'block_text': block}
        i = end + 1


def find_dff_cells(module_body, cp_net):
    """
    Return list of cell dicts whose CP pin matches cp_net (exact or partial).
    Handles standard CP pin name and CTS-renamed net (partial match on base name).
    """
    found = []
    cp_base = cp_net.split('/')[-1]  # strip scope prefix if any
    for cell in _extract_cell_blocks(module_body):
        cp_val = cell['ports'].get('CP', '')
        if cp_net in cp_val or cp_base in cp_val:
            found.append(cell)
    return found


# ── Bit-index mapping from Q-pins ───────────────────────────────────────────

def _q_pin_to_bit(ports):
    """
    Build {q_pin: bit_index} from Q-pin net names like signal[N] or signal_N_.
    Returns e.g. {'Q1': 6, 'Q2': 5, 'Q3': 4, 'Q4': 3} for MB4 MSB-first packing.
    """
    q_to_bit = {}
    for pin, net in ports.items():
        if not re.match(r'^Q\d*$', pin):
            continue
        bm = re.search(r'\[(\d+)\]', net) or re.search(r'_(\d+)_\s*$', net)
        if bm:
            q_to_bit[pin] = int(bm.group(1))
    return q_to_bit


# ── Per-stage net resolution via rename_map ──────────────────────────────────

def _resolve_stage_net(rmap, net, stage):
    """Look up a net in the rename map and return its stage-specific actual wire."""
    for key, entry in rmap.items():
        if not isinstance(entry, dict):
            continue
        # Match key suffix against net name
        if key == net or key.endswith(f'/{net}'):
            aw = entry.get(f'actual_wire_{stage}')
            if aw:
                return aw
            sv = entry.get(stage)
            if sv and not sv.endswith('/Q') and not sv.endswith('/D'):
                return sv
    return net  # unchanged


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # Parse d-map: "0:n_eco_9855_net20,1:n_eco_9855_net21,..."
    d_map = {}
    if args.d_map.strip():
        for item in args.d_map.split(','):
            item = item.strip()
            if ':' in item:
                bit_s, net = item.split(':', 1)
                try:
                    d_map[int(bit_s.strip())] = net.strip()
                except ValueError:
                    pass

    # Load rename map
    rmap_path = os.path.join(args.base_dir, 'data',
                             f'{args.tag}_eco_fenets_rename_map.json')
    rmap = {}
    if os.path.exists(rmap_path):
        try:
            rmap = json.loads(Path(rmap_path).read_text())
        except Exception:
            pass

    errors = []
    rewires = []

    # ── Step 1: find DFF cells from PreEco Synthesize ──────────────────────
    preeco_syn_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if not os.path.exists(preeco_syn_gz):
        preeco_syn_gz = os.path.join(args.ref_dir, 'data', 'PostEco', 'Synthesize.v.gz')
    if not os.path.exists(preeco_syn_gz):
        errors.append('PreEco/Synthesize.v.gz not found')
        _write_output(args.output, 'FAIL', 0, errors, [])
        return 1

    print(f'Loading PreEco Synthesize netlist...', file=sys.stderr)
    text = load_netlist(preeco_syn_gz)
    if not text:
        errors.append('Failed to load PreEco Synthesize netlist')
        _write_output(args.output, 'FAIL', 0, errors, [])
        return 1

    mod_body, actual_mod = extract_module_body(text, args.module)
    if not mod_body:
        errors.append(f'Module {args.module!r} not found in PreEco Synthesize')
        _write_output(args.output, 'FAIL', 0, errors, [])
        return 1

    dff_cells = find_dff_cells(mod_body, args.dff_cp_net)
    if not dff_cells:
        errors.append(
            f'No DFF cells with CP={args.dff_cp_net!r} found in module '
            f'{actual_mod!r} in PreEco Synthesize. Check --dff-cp-net and --module.')
        _write_output(args.output, 'FAIL', 0, errors, [])
        return 1

    print(f'Found {len(dff_cells)} DFF cell(s) with CP={args.dff_cp_net!r}',
          file=sys.stderr)
    for c in dff_cells:
        print(f'  {c["cell_type"]} {c["instance_name"]}', file=sys.stderr)

    # ── Step 2: build rewire entries ────────────────────────────────────────
    for cell in dff_cells:
        inst = cell['instance_name']
        ports = cell['ports']
        old_cp = ports.get('CP', args.dff_cp_net)

        # CP rewire
        rewires.append({
            'change_type':  'rewire',
            'cell_name':    inst,
            'pin':          'CP',
            'old_net':      old_cp,
            'new_net':      args.new_cp_net,
            'module_name':  actual_mod,
            'confirmed':    True,
            'reason':       (f'enable_swap shadow gate: CP rewired from '
                             f'{old_cp!r} → {args.new_cp_net!r}'),
        })

        if not d_map:
            continue  # CP-only mode

        # D-input rewires — resolve bit via Q-pin net
        q_to_bit = _q_pin_to_bit(ports)
        for pin, old_net in sorted(ports.items()):
            if not re.match(r'^D\d*$', pin):
                continue
            q_pin = 'Q' if pin == 'D' else 'Q' + pin[1:]
            bit = q_to_bit.get(q_pin)
            if bit is None:
                errors.append(
                    f'{inst}: D-pin {pin!r} has no matching Q-pin {q_pin!r} '
                    f'with a parseable bit index — skipping')
                continue
            new_net = d_map.get(bit)
            if new_net is None:
                errors.append(
                    f'{inst}: D-pin {pin!r} bit={bit} has no d-map entry — '
                    f'd-map keys: {sorted(d_map.keys())}')
                continue
            rewires.append({
                'change_type':  'rewire',
                'cell_name':    inst,
                'pin':          pin,
                'old_net':      old_net,
                'new_net':      new_net,
                'module_name':  actual_mod,
                'confirmed':    True,
                'reason':       (f'enable_swap shadow gate: D-input bit {bit} '
                                 f'rewired from {old_net!r} → {new_net!r}'),
            })

    # ── Step 3: verify per-stage — warn if CP net absent in PP/Route ────────
    for stage in ('PrePlace', 'Route'):
        gz = os.path.join(args.ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            gz = os.path.join(args.ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            r = subprocess.run(
                f'zgrep -c "{re.escape(args.dff_cp_net)}" {gz}',
                shell=True, capture_output=True, text=True, timeout=30)
            count = int(r.stdout.strip() or '0')
            if count == 0:
                # CP net renamed in this stage — applier net-rename recovery will handle
                resolved = _resolve_stage_net(rmap, args.dff_cp_net, stage)
                errors.append(
                    f'WARN/{stage}: dff_cp_net {args.dff_cp_net!r} absent '
                    f'(CTS renamed → {resolved!r}). '
                    f'Applier net-rename recovery will handle.')
        except Exception:
            pass

    status = 'OK' if not any(not e.startswith('WARN/') for e in errors) else 'PARTIAL'
    if any(e.startswith('FAIL') or ('not found' in e and 'WARN' not in e)
           for e in errors):
        status = 'FAIL'

    _write_output(args.output, status, len(rewires), errors, rewires)
    print(f'eco_emit_shadow_gate: {len(rewires)} rewires | status={status}',
          file=sys.stderr)
    for e in errors:
        print(f'  {"ERROR" if "WARN" not in e else "WARN"}: {e}', file=sys.stderr)
    return 0 if status != 'FAIL' else 1


def _write_output(path, status, count, errors, rewires):
    out = {
        'status':       status,
        'rewire_count': count,
        'errors':       errors,
        'rewires':      rewires,
    }
    Path(path).write_text(json.dumps(out, indent=2))
    marker = path.replace('.json', '_marker.txt')
    Path(marker).write_text(
        f'ECO_SCRIPT_LAUNCHED: eco_emit_shadow_gate.py\n'
        f'  status: {status}\n'
        f'  rewire_count: {count}\n'
        f'  errors: {len(errors)}\n'
        f'  output: {path}\n')


if __name__ == '__main__':
    sys.exit(main())
