#!/usr/bin/env python3
"""
eco_emit_rewire_finalize.py — deterministic Step-3 post-pass that makes DFF-pin
rewire entries correct BY CONSTRUCTION (instead of relying on the catch-and-fix
loop). Module-aware: the SAME flop instance name recurs across many uniquified
generate modules (e.g. rcqe_pgst_reg... in umcrecrcqentry_0.._39), each with its
OWN per-stage SI/SE nets — so everything is keyed by (module, instance).

Two jobs, one linear scan per stage:
  #2 PER-STAGE CELL/PIN: for every D/CP rewire on a pre-existing DFF, if the
     Synthesize cell_name is ABSENT in a later stage (P&R merged the flop into a
     multi-bit bank, e.g. postcas_reg -> <big>_reg_0_ / .D->.D2), resolve
     cell_name_per_stage + pin_per_stage from the cell whose D-pin carries the net.
  #1 SI/SE CONSISTENCY: for every rewired pre-existing DFF (per module), ensure
     SI and SE are 1'b0 in ALL stages (P&R scan-stitches them). Adds SI/SE=1'b0
     rewires with per-stage old_net so FM scan cones match (validate Check 64).

Usage:
    python3 script/eco_scripts/eco_emit_rewire_finalize.py \
        --study data/<TAG>_eco_preeco_study.json --ref-dir <REF_DIR> \
        --output data/<TAG>_eco_preeco_study.json
Idempotent.
"""
import argparse, gzip, json, os, re, sys

STAGES = ('Synthesize', 'PrePlace', 'Route')
_DPIN = re.compile(r'^D\d*$')
_MOD = re.compile(r'^\s*module\s+(\S+)')
_INST = re.compile(r'^\s*[\w:]+\s+(\w+)\s*\(')
_PIN = re.compile(r'\.(\w+)\s*\(\s*([^)]*?)\s*\)')


def scan_stage(gz):
    """One linear pass -> (inst_pins, dpin_owner), both keyed by MODULE:
       inst_pins[(module, inst)] = {pin: net}
       dpin_owner[(module, net)] = (inst, pin)   for register D-pins."""
    inst_pins, dpin_owner = {}, {}
    if not os.path.isfile(gz):
        return inst_pins, dpin_owner
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
                    pin, net = pm.group(1), pm.group(2).strip()
                    inst_pins.setdefault((mod, cur), {})[pin] = net
                    if _DPIN.match(pin):
                        dpin_owner.setdefault((mod, net), (cur, pin))
    return inst_pins, dpin_owner


def _stage_modules(mod):
    """Study module_name -> candidate netlist module names for a given stage
    (Route re-uniquifies with a trailing _0)."""
    return (mod, mod + '_0')


def _find_pins(inst_pins, mod, inst):
    for m in _stage_modules(mod):
        if (m, inst) in inst_pins:
            return inst_pins[(m, inst)]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--study', required=True)
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    study = json.loads(open(args.study).read())
    scans = {st: scan_stage(os.path.join(args.ref_dir, 'data', 'PreEco', f'{st}.v.gz'))
             for st in STAGES}

    n_cell, n_sise = 0, 0
    rewired = {}   # (inst, module) present as a D/CP rewire on a pre-existing DFF

    for st in STAGES:
        for e in study.get(st, []):
            if e.get('change_type') != 'rewire':
                continue
            pin = e.get('pin') or ''
            if not (_DPIN.match(pin) or pin in ('CP', 'CK')):
                continue
            inst = e.get('instance_name') or e.get('cell_name') or ''
            mod = e.get('module_name') or ''
            if not inst:
                continue
            # Canonical rewired-flop set from the Synthesize list only — the SAME flop
            # appears in PP/Route lists with a Route-uniquified module (…_0), which would
            # otherwise double-count. SI/SE (#1) keys off this canonical set.
            if st == 'Synthesize':
                rewired[(inst, mod)] = True
            # #2 per-stage cell/pin (runs for every stage's entry)
            cps = e.get('cell_name_per_stage') or {}
            pps = e.get('pin_per_stage') or {}
            ops = e.get('old_net_per_stage') or {}
            old = e.get('old_net')
            changed = False
            for s2 in STAGES:
                inst_pins, dpin_owner = scans[s2]
                if _find_pins(inst_pins, mod, inst) is not None:
                    cps.setdefault(s2, inst); pps.setdefault(s2, pin); continue
                net_s2 = ops.get(s2) or old
                owner = None
                for m in _stage_modules(mod):
                    owner = dpin_owner.get((m, net_s2))
                    if owner:
                        break
                if owner:
                    cps[s2] = owner[0]; pps[s2] = owner[1]; changed = True
                else:
                    cps.setdefault(s2, inst); pps.setdefault(s2, pin)
            if changed:
                e['cell_name_per_stage'] = cps
                e['pin_per_stage'] = pps
                n_cell += 1

    # #1 SI/SE=1'b0 per (inst, module) — one entry per stage list per flop
    for st in STAGES:
        entries = study.get(st, [])
        have = {(x.get('cell_name') or x.get('instance_name'), x.get('module_name'), x.get('pin'))
                for x in entries if x.get('change_type') == 'rewire' and x.get('pin') in ('SI', 'SE')}
        for (inst, mod) in sorted(rewired):
            for pin in ('SI', 'SE'):
                if (inst, mod, pin) in have:
                    continue
                ops = {}
                for s2 in STAGES:
                    inst_pins, _ = scans[s2]
                    pins = _find_pins(inst_pins, mod, inst) or {}
                    ops[s2] = pins.get(pin, "1'b0")
                if all(v == "1'b0" for v in ops.values()):
                    continue
                entries.append({
                    'change_type': 'rewire', 'instance_name': inst, 'cell_name': inst,
                    'module_name': mod, 'pin': pin, 'old_net': ops[st],
                    'old_net_per_stage': ops, 'new_net': "1'b0",
                    'force_reapply': True, 'confirmed': True,
                    'source': 'eco_emit_rewire_finalize',
                    'reason': "scan-pin isolation: rewired DFF holds SI/SE=1'b0 in all stages (Check 64).",
                    'notes': 'SI/SE consistency emitted by eco_emit_rewire_finalize.',
                })
                have.add((inst, mod, pin)); n_sise += 1

    open(args.output, 'w').write(json.dumps(study, indent=2))
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_emit_rewire_finalize.py\n"
              f"  per-stage cell/pin filled: {n_cell}\n"
              f"  SI/SE=1'b0 rewires added:  {n_sise}\n")
    print(marker)
    open(args.output.replace('.json', '_rewire_finalize_marker.txt'), 'w').write(marker)
    return 0


if __name__ == '__main__':
    sys.exit(main())
