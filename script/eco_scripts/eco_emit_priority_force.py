#!/usr/bin/env python3
"""
eco_emit_priority_force.py — deterministically BUILD the gate logic for every
`priority_force` change (Intent-B: "under a new condition, force sig=CONST"),
so it is correct by construction instead of left to LLM free-form or a PENDING
placeholder. Splices force-mux gates + DFF-pin rewires into the study.

Per priority_force change:
  1. Condition cone: the change's `condition_gate_chain` gates are emitted verbatim;
     the last gate's output is the condition net `cond`. One shared INV(cond)->cond_n.
  2. Per forced signal, per bit b (const bit value decides the gate):
        const bit == 1  ->  OR2 (cond, old_bit)         -> force to 1 when cond
        const bit == 0  ->  INR2(A1=old_bit, B1=cond)    -> old & ~cond = force to 0
     output is a fresh n_eco_<jira>_pf_<sig>_<b>, then a DFF-pin rewire repoints
     that bit's flop .D from old_bit to the fresh net.

Required schema on each priority_force change (Step 1 / rtl_diff_analyzer.md #1):
  module_name, condition_gate_chain:[{instance_name,gate_function,cell_type,
      port_connections}], forced_signals:[{signal, const, bits:[{bit, old_net,
      dff_cell, dff_pin}]}].  (bits[].dff_cell/dff_pin identify the flop to rewire;
  old_net is that bit's pre-ECO D-input net.)

Fail-closed grounding: pass --ref-dir to verify every bit's (module, dff_cell).dff_pin
actually carries old_net in the PreEco Synthesize netlist. Any mismatch ABORTS the build
(exit 2, study untouched) instead of rewiring the wrong pin on a hallucinated net.

Usage:
    python3 script/eco_scripts/eco_emit_priority_force.py \
        --rtl-diff data/<TAG>_eco_rtl_diff.json --study data/<TAG>_eco_preeco_study.json \
        --jira <JIRA> --ref-dir <REF_DIR> --output data/<TAG>_eco_preeco_study.json
"""
import argparse, gzip, json, os, re, sys

STAGES = ('Synthesize', 'PrePlace', 'Route')
_INV_CELL = 'INVD1BWP136P5M156H3P48CPDLVT'
_OR2_CELL = 'OR2D1BWP136P5M156H3P48CPDLVT'
_INR2_CELL = 'INR2D1BWP136P5M156H3P48CPDLVT'

_MOD = re.compile(r'^\s*module\s+(\S+)')
_INST = re.compile(r'^\s*[\w:]+\s+(\w+)\s*\(')
_PIN = re.compile(r'\.(\w+)\s*\(\s*([^)]*?)\s*\)')


def _scan_pins(gz):
    """One linear pass over a netlist -> inst_pins[(module, inst)] = {pin: net}."""
    inst_pins = {}
    if not os.path.isfile(gz):
        return inst_pins
    mod, cur, depth = None, None, 0
    with gzip.open(gz, 'rt', errors='replace') as f:
        for ln in f:
            mm = _MOD.match(ln)
            if mm:
                mod, cur, depth = mm.group(1), None, 0
                continue
            im = _INST.match(ln)
            if im and depth == 0:
                cur = im.group(1)
            depth += ln.count('(') - ln.count(')')
            if depth <= 0:
                depth = 0
            if cur and mod:
                for pm in _PIN.finditer(ln):
                    inst_pins.setdefault((mod, cur), {})[pm.group(1)] = pm.group(2).strip()
    return inst_pins


def _find_pins(inst_pins, mod, inst):
    """Route re-uniquifies the study module with a trailing _0."""
    for m in (mod, mod + '_0'):
        if (m, inst) in inst_pins:
            return inst_pins[(m, inst)]
    return None


def ground_bits(rtl_diff, ref_dir):
    """Fail-closed: verify EVERY priority_force bit's (module, dff_cell).dff_pin actually
    carries old_net in the PreEco Synthesize netlist. Returns a list of error strings;
    empty means all bits are netlist-grounded and it is safe to build."""
    gz = os.path.join(ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    inst_pins = _scan_pins(gz)
    errs = []
    if not inst_pins:
        return [f"cannot ground priority_force: PreEco netlist not readable at {gz}"]
    for ci, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'priority_force':
            continue
        mod = c.get('module_name') or ''
        for f in c.get('forced_signals') or []:
            sig = f.get('signal')
            for bspec in f.get('bits') or []:
                b = bspec.get('bit')
                old = bspec.get('old_net')
                cell = bspec.get('dff_cell')
                pin = bspec.get('dff_pin', 'D')
                pins = _find_pins(inst_pins, mod, cell) if cell else None
                if pins is None:
                    errs.append(f"changes[{ci}] {sig}[{b}]: dff_cell {cell!r} not found in "
                                f"module {mod!r} of PreEco Synthesize netlist.")
                    continue
                actual = pins.get(pin)
                if actual != old:
                    errs.append(f"changes[{ci}] {sig}[{b}]: {cell}.{pin} carries {actual!r} in "
                                f"PreEco netlist but bits[].old_net says {old!r} — net mismatch, "
                                f"force would rewire the WRONG pin.")
    return errs


def _const_bits(const):
    """'5'b01011' -> ['0','1','0','1','1'] (MSB..LSB); '1'b1' -> ['1']. None if unparsable."""
    m = re.match(r"^(\d+)'b([01]+)$", str(const).strip())
    if m:
        w, bits = int(m.group(1)), m.group(2)
        return list(bits.zfill(w))
    m = re.match(r"^1'b([01])$", str(const).strip())
    return [m.group(1)] if m else None


def _pcstage(pc):
    return {s: dict(pc) for s in STAGES}


def emit(rtl_diff, study, jira):
    seq = [0]
    def nn(tag):
        seq[0] += 1
        return f"n_eco_{jira}_pf_{tag}_{seq[0]}"
    added = 0
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'priority_force':
            continue
        mod = c.get('module_name') or ''
        new_gates, new_rewires = [], []
        # 1. condition cone (emit verbatim) -> cond net = last gate output
        chain = c.get('condition_gate_chain') or []
        cond = None
        for g in chain:
            pc = g.get('port_connections') or {}
            new_gates.append({
                'change_type': 'new_logic_gate', 'instance_name': g['instance_name'],
                'cell_type': g.get('cell_type', ''), 'gate_function': g.get('gate_function', ''),
                'output_net': g.get('output_net') or _out_of(pc),
                'module_name': mod, 'port_connections': pc,
                'port_connections_per_stage': g.get('port_connections_per_stage') or _pcstage(pc),
                'confirmed': True, 'source': 'eco_emit_priority_force',
            })
            cond = g.get('output_net') or _out_of(pc)
        if not cond:
            continue
        cond_n = nn('condn')
        new_gates.append({
            'change_type': 'new_logic_gate', 'instance_name': f'eco_{jira}_pf_inv_{seq[0]}',
            'cell_type': _INV_CELL, 'gate_function': 'INV', 'output_net': cond_n,
            'module_name': mod, 'port_connections': {'I': cond, 'ZN': cond_n},
            'port_connections_per_stage': _pcstage({'I': cond, 'ZN': cond_n}),
            'confirmed': True, 'source': 'eco_emit_priority_force',
        })
        # 2. per forced signal, per bit force-mux + DFF-pin rewire
        for f in c.get('forced_signals') or []:
            cbits = _const_bits(f.get('const'))
            bits = f.get('bits') or []
            if cbits is None or not bits:
                continue
            for bspec in bits:
                b = bspec.get('bit')
                old = bspec.get('old_net')
                dff_cell = bspec.get('dff_cell'); dff_pin = bspec.get('dff_pin', 'D')
                if b is None or old is None or not dff_cell:
                    continue
                # const bit for this position (bits given MSB..LSB; index by width-1-b)
                cval = cbits[len(cbits) - 1 - b] if b < len(cbits) else '0'
                fresh = nn(f"{f.get('signal','sig')}_{b}")
                if cval == '1':
                    pc = {'A1': cond, 'A2': old, 'Z': fresh}
                    cell, fn = _OR2_CELL, 'OR2'
                else:
                    pc = {'A1': old, 'B1': cond, 'ZN': fresh}
                    cell, fn = _INR2_CELL, 'INR2'
                new_gates.append({
                    'change_type': 'new_logic_gate',
                    'instance_name': f"eco_{jira}_pf_mux_{f.get('signal','sig')}_{b}",
                    'cell_type': cell, 'gate_function': fn, 'output_net': fresh,
                    'module_name': mod, 'port_connections': pc,
                    'port_connections_per_stage': bspec.get('port_connections_per_stage') or _pcstage(pc),
                    'confirmed': True, 'source': 'eco_emit_priority_force',
                    'notes': f"priority_force: force {f.get('signal')}[{b}]={cval} when condition, else hold.",
                })
                rw = {
                    'change_type': 'rewire', 'instance_name': dff_cell, 'cell_name': dff_cell,
                    'module_name': mod, 'pin': dff_pin, 'old_net': old, 'new_net': fresh,
                    'confirmed': True, 'force_reapply': True, 'source': 'eco_emit_priority_force',
                    'notes': f"priority_force DFF-pin rewire for {f.get('signal')}[{b}].",
                }
                if bspec.get('cell_name_per_stage'):
                    rw['cell_name_per_stage'] = bspec['cell_name_per_stage']
                if bspec.get('pin_per_stage'):
                    rw['pin_per_stage'] = bspec['pin_per_stage']
                if bspec.get('old_net_per_stage'):
                    rw['old_net_per_stage'] = bspec['old_net_per_stage']
                new_rewires.append(rw)
        for st in STAGES:
            study.setdefault(st, []).extend([dict(g) for g in new_gates] + [dict(r) for r in new_rewires])
            added += len(new_gates) + len(new_rewires)
    return added


def _out_of(pc):
    for p in ('Z', 'ZN', 'CO', 'S'):
        if p in pc:
            return pc[p]
    return ''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--study', required=True)
    ap.add_argument('--jira', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--ref-dir', help='REF_DIR: when given, every priority_force bit is '
                    'grounded against PreEco Synthesize netlist and the build ABORTS '
                    '(nonzero exit, study untouched) on any dff_cell/old_net mismatch.')
    args = ap.parse_args()
    rtl_diff = json.loads(open(args.rtl_diff).read())
    study = json.loads(open(args.study).read())

    # Fail-closed grounding: refuse to build on hallucinated/wrong nets.
    if args.ref_dir:
        errs = ground_bits(rtl_diff, args.ref_dir)
        if errs:
            marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_priority_force.py\n"
                      f"  ABORTED — {len(errs)} priority_force bit(s) not netlist-grounded:\n"
                      + "".join(f"    - {e}\n" for e in errs)
                      + "  Study UNTOUCHED. Fix bits[].dff_cell/old_net in the RTL diff and re-run.\n")
            print(marker)
            open(args.output.replace('.json', '_priority_force_marker.txt'), 'w').write(marker)
            return 2

    n = emit(rtl_diff, study, args.jira)
    open(args.output, 'w').write(json.dumps(study, indent=2))
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_emit_priority_force.py\n"
              f"  priority_force entries spliced (gates+rewires, all stages): {n}\n"
              f"  netlist-grounded: {'yes' if args.ref_dir else 'NO (no --ref-dir)'}\n")
    print(marker)
    open(args.output.replace('.json', '_priority_force_marker.txt'), 'w').write(marker)
    return 0


if __name__ == '__main__':
    sys.exit(main())
