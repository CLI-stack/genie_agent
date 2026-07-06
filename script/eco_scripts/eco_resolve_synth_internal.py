#!/usr/bin/env python3
"""
eco_resolve_synth_internal.py — Find the P&R stage equivalent of a
synthesis-internal net whose backward driver chain is absent in P&R.

Uses targeted zgrep calls (fast) instead of loading full netlist into memory.

WHEN TO RUN:
  Run this script whenever the verifier or study has any of these placeholders
  in port_connections_per_stage for a P&R stage:
    - UNRESOLVABLE:<signal>
    - MODE_H_ROUTE_SKIP:<signal>
    - NEEDS_NAMED_WIRE:<signal>
  Also run when validator flags CRITICAL/PENDING-UNRESOLVED or
  CRITICAL/NET-ABSENT-IN-STAGE. Run BEFORE attempting manual F1-F3 search.

Strategy (in order):
  1. DIRECT      — net exists in stage by same name → use it
  2. BACKWARD    — find driver cell in Synth, search in stage, read output
                   (recurses up to 3 levels if intermediate cells absent)
  3. FORWARD     — find consumers of net in Synth, find them in stage,
                   read net on same input pin. 2-hop fallback if consumers absent.
  4. UNRESOLVABLE — both failed → fall back to manual F1-F3 in verifier MD

Usage:
    python3 script/eco_scripts/eco_resolve_synth_internal.py \\
        --ref-dir <REF_DIR> \\
        --synth-net <net_name>  e.g. N2408127, N197617, phfnn_2405075 \\
        --stage <PrePlace|Route> \\
        --output /tmp/resolve_<net>.json
    # Use resolved_net from JSON directly if not UNRESOLVABLE
"""
import argparse, json, os, re, shlex, subprocess, sys

_OUT_PINS = ('ZN', 'ZN1', 'Z', 'Q', 'QN', 'CO', 'Y', 'S')
_MAX_BACKWARD = 3
_MAX_CONSUMERS = 15


def zgrep_count(pattern, gz):
    """Count word occurrences of a LITERAL net/cell name in gz. Uses -F (fixed
    string) so bracketed bus bits like `WckSyncCtr0[0]` are matched literally — as a
    regex, `[0]` is a character class and never matches the bracketed net."""
    try:
        r = subprocess.run(['bash', '-c',
                            f'zgrep -cwF -- {shlex.quote(pattern)} {shlex.quote(gz)}'],
                           capture_output=True, text=True, timeout=20)
        return int(r.stdout.strip() or 0)
    except Exception:
        return 0


def zgrep_lines(pattern, gz, max_lines=20):
    """Return lines matching pattern from gz file."""
    try:
        r = subprocess.run(f'zgrep -m {max_lines} "{pattern}" {gz}',
                           shell=True, capture_output=True, text=True, timeout=30)
        return r.stdout.splitlines()
    except Exception:
        return []


def zgrep_context(cell_name, gz, after=5):
    """Return the instantiation block of a cell from gz file."""
    try:
        r = subprocess.run(f'zgrep -A {after} "\\b{cell_name}\\b" {gz}',
                           shell=True, capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:
        return ''


def find_driver(net, synth_gz):
    """
    Find the cell driving `net` on an output pin in Synth.
    Returns (cell_name, out_pin) or (None, None).
    """
    lines = zgrep_lines(f'\\.({"|".join(_OUT_PINS)})\\s*\\(\\s*{re.escape(net)}\\s*\\)', synth_gz)
    for line in lines:
        # Find .ZN ( net ) or .Z ( net ) etc.
        for pin in _OUT_PINS:
            m = re.search(r'\b(\w+)\s*\(', line)  # cell type at start
            inst_m = re.search(r'\b(\w+)\s*\(\s*\.', line)  # instance name
            pin_m = re.search(rf'\.{pin}\s*\(\s*{re.escape(net)}\s*\)', line)
            if pin_m and inst_m:
                # Extract instance name — it's between cell_type and (
                tokens = line.strip().split()
                if len(tokens) >= 2:
                    cell = tokens[1].rstrip('(')
                    if cell and not cell.startswith('.'):
                        return cell, pin
    return None, None


def read_output_net(cell, gz):
    """Read the output net of a cell from gz file."""
    ctx = zgrep_context(cell, gz, after=3)
    if not ctx:
        return None, None
    for pin in _OUT_PINS:
        m = re.search(rf'\.{re.escape(pin)}\s*\(\s*(\w+)\s*\)', ctx)
        if m:
            net = m.group(1)
            if net not in ('0', '1') and not net.startswith("1'"):
                return net, pin
    return None, None


def read_pin_net(cell, pin, gz):
    """Read the net on a specific pin of a cell from gz file."""
    ctx = zgrep_context(cell, gz, after=5)
    if not ctx:
        return None
    m = re.search(rf'\.{re.escape(pin)}\s*\(\s*(\w+)\s*\)', ctx)
    if m:
        net = m.group(1)
        if net not in ('0', '1') and not net.startswith("1'"):
            return net
    return None


def find_consumers(net, synth_gz, max_consumers=_MAX_CONSUMERS):
    """
    Find (cell_name, pin_name) pairs that consume net as an INPUT in Synth.
    Uses -B3 context to find cell instance name from preceding line in
    multi-line Verilog instantiations.
    """
    try:
        r = subprocess.run(
            f'zgrep -m 40 -B3 " {re.escape(net)} " {synth_gz}',
            shell=True, capture_output=True, text=True, timeout=30)
        text = r.stdout
    except Exception:
        return []

    consumers = []
    seen = set()
    blocks = re.split(r'^--$', text, flags=re.MULTILINE)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        # Find input pin in any line of block
        pin = None
        for line in lines:
            m = re.search(rf'\.(\w+)\s*\(\s*{re.escape(net)}\s*\)', line)
            if m and m.group(1) not in _OUT_PINS:
                pin = m.group(1)
                break
        if not pin:
            continue
        # Find cell instance name: line matching CELLTYPE INSTNAME (
        cell = None
        for line in lines:
            m = re.match(r'^\s*\w[\w\[\]]*\s+(\w+)\s*\(', line)
            if m:
                candidate = m.group(1)
                if not candidate.startswith('.') and candidate != net:
                    cell = candidate
                    break
        if cell and (cell, pin) not in seen:
            seen.add((cell, pin))
            consumers.append((cell, pin))
        if len(consumers) >= max_consumers:
            break
    return consumers


def backward_trace(net, synth_gz, stage_gz, level=0):
    """Backward trace: find driver in Synth, locate in stage."""
    if level > _MAX_BACKWARD:
        return None, None
    cell, out_pin = find_driver(net, synth_gz)
    if not cell:
        return None, None
    if zgrep_count(cell, stage_gz) > 0:
        stage_net, stage_pin = read_output_net(cell, stage_gz)
        if stage_net and zgrep_count(stage_net, stage_gz) > 0:
            return stage_net, f'backward L{level}: {cell}.{stage_pin}'
    # Cell absent — try its input net
    ctx = zgrep_context(cell, synth_gz, after=3)
    for pin_m in re.finditer(r'\.(\w+)\s*\(\s*(\w+)\s*\)', ctx):
        pin, input_net = pin_m.group(1), pin_m.group(2)
        if pin in _OUT_PINS: continue
        if input_net in ('0','1') or input_net.startswith("1'"): continue
        result, detail = backward_trace(input_net, synth_gz, stage_gz, level+1)
        if result:
            return result, detail
    return None, None


def forward_consumer(net, synth_gz, stage_gz, hop=1):
    """
    Forward consumer search (up to 2 hops):
    - Hop 1: cells consuming net → find in stage → read same pin
    - Hop 2: if hop1 consumers all absent, try consumers of consumer outputs
    """
    from collections import Counter
    consumers = find_consumers(net, synth_gz)
    candidates = []
    hop1_outputs = []  # collect consumer output nets for hop-2

    for cell, pin in consumers:
        # Collect consumer output for hop-2 even if cell absent in stage
        out_net, _ = read_output_net(cell, synth_gz) if zgrep_count(cell, synth_gz) > 0 else (None, None)
        if out_net:
            hop1_outputs.append(out_net)
        if zgrep_count(cell, stage_gz) == 0:
            continue
        stage_net = read_pin_net(cell, pin, stage_gz)
        if not stage_net or zgrep_count(stage_net, stage_gz) == 0:
            continue
        candidates.append((stage_net, cell, pin))

    if candidates:
        net_votes = Counter(c[0] for c in candidates)
        best_net = net_votes.most_common(1)[0][0]
        best = next(c for c in candidates if c[0] == best_net)
        confidence = 'high' if net_votes[best_net] > 1 else 'medium'
        return best[0], best[1], best[2], confidence

    # Hop 2: try consumers of consumer outputs
    if hop < 2:
        for out_net in hop1_outputs[:5]:  # limit hops
            result = forward_consumer(out_net, synth_gz, stage_gz, hop=2)
            if result[0]:
                return result

    return None, None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref-dir',   required=True)
    ap.add_argument('--synth-net', required=True)
    ap.add_argument('--stage',     required=True, choices=['PrePlace','Route'])
    ap.add_argument('--output',    required=True)
    args = ap.parse_args()

    synth_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    stage_gz = os.path.join(args.ref_dir, 'data', 'PreEco', f'{args.stage}.v.gz')

    def emit(resolved, method, detail, confidence='high', **kw):
        r = {'synth_net': args.synth_net, 'stage': args.stage,
             'resolved_net': resolved, 'method': method,
             'detail': detail, 'confidence': confidence}
        r.update(kw)
        json.dump(r, open(args.output, 'w'), indent=2)
        print(f'{method.upper()}: {resolved}  ({detail})', file=sys.stderr)

    # Verify exists in Synth
    if zgrep_count(args.synth_net, synth_gz) == 0:
        emit('UNRESOLVABLE', 'unresolvable',
             f'{args.synth_net} not in Synth PreEco', 'none')
        return

    # 1. Direct — same name in stage
    if zgrep_count(args.synth_net, stage_gz) > 0:
        emit(args.synth_net, 'direct', 'same name exists in stage')
        return

    # 2. Backward trace
    print('Backward trace...', file=sys.stderr)
    resolved, detail = backward_trace(args.synth_net, synth_gz, stage_gz)
    if resolved:
        emit(resolved, 'backward_trace', detail)
        return

    # 3. Forward consumer search
    print('Forward consumer search...', file=sys.stderr)
    resolved, consumer, pin, confidence = forward_consumer(args.synth_net, synth_gz, stage_gz)
    if resolved:
        emit(resolved, 'forward_consumer',
             f'{consumer}.{pin} → {resolved}', confidence,
             consumer_cell=consumer, consumer_pin=pin)
        return

    # 4. Unresolvable
    emit('UNRESOLVABLE', 'unresolvable',
         'backward and forward search both failed', 'none')


if __name__ == '__main__':
    main()
