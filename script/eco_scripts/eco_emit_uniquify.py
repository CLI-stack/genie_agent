#!/usr/bin/env python3
"""
eco_emit_uniquify.py — deterministically REPLICATE the canonical ECO unit built
for the _0 copy of a synthesis-uniquified generate array to ALL N copies, so a
per-instance edit is correct BY CONSTRUCTION instead of landing on copy _0 only.

For a family `<base>_0 … <base>_<N-1>` (child instantiated in a generate loop),
the studier builds the unit ONCE on `<base>_0`. Per stage, for every other copy i
this clones:
  * the combinational gates (module -> <base>_i),
  * the consuming D/CP rewire(s) (SI/SE left to eco_emit_rewire_finalize),
  * a per-copy input port_declaration,
with two renames:
  1. every fresh `n_eco_*` net is suffixed `_<i>` so each copy is self-contained
     (kills the driver-emits-<net>_i / load-reads-<net> UNDRIVEN class);
  2. each copy's OWN old net (the per-copy target of the D/CP rewire — a different
     local name per uniquified module) is resolved from THAT copy's flop D-pin in
     the netlist, per stage. Fail-closed: abort (exit 2) if a copy's net or module
     is missing rather than drop it.

Reads `uniquified_family` on the rtl_diff changes to know which study modules are
canonical. N and the copy module names come from the NETLIST (ground truth).

Usage:
    python3 script/eco_scripts/eco_emit_uniquify.py \
        --rtl-diff data/<TAG>_eco_rtl_diff.json --study data/<TAG>_eco_preeco_study.json \
        --jira <JIRA> --ref-dir <REF_DIR> --output data/<TAG>_eco_preeco_study.json
Idempotent (skips copies already present).
"""
import argparse, gzip, json, os, re, sys

STAGES = ('Synthesize', 'PrePlace', 'Route')
_OUT_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN')
_DPIN = re.compile(r'^D\d*$')
_CLKPIN = ('CP', 'CK')
_MOD = re.compile(r'^\s*module\s+(\S+)')
_INST = re.compile(r'^\s*[\w:]+\s+(\w+)\s*\(')
_PIN = re.compile(r'\.(\w+)\s*\(\s*([^)]*?)\s*\)')


def scan_stage(gz):
    """One linear pass -> (inst_pins, modules).
       inst_pins[(module, inst)] = {pin: net}; modules = set of module names."""
    inst_pins, modules = {}, set()
    if not os.path.isfile(gz):
        return inst_pins, modules
    mod, cur, depth = None, None, 0
    with gzip.open(gz, 'rt', errors='replace') as f:
        for ln in f:
            mm = _MOD.match(ln)
            if mm:
                mod = mm.group(1); modules.add(mod); cur, depth = None, 0
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
    return inst_pins, modules


def scan_family_instances(gz, netbase):
    """Map copy index -> parent instance name for a uniquified child family, from the
    netlist instantiation lines `<netbase>_<i>[_0] <inst> (`. Ground truth for
    replicating a per-instance port_connection to every copy's parent instance."""
    out = {}
    if not os.path.isfile(gz):
        return out
    inst_re = re.compile(r'^\s*' + re.escape(netbase) + r'_(\d+)(?:_0)?\s+(\w+)\s*\(')
    with gzip.open(gz, 'rt', errors='replace') as f:
        for ln in f:
            m = inst_re.match(ln)
            if m:
                out.setdefault(int(m.group(1)), m.group(2))
    return out


def _rename_net(net, rmap):
    return rmap.get(net, net) if isinstance(net, str) else net


def _clone_gate(g, i, modname, rmap):
    ng = dict(g)
    ng['module_name'] = modname
    ng['instance_name'] = f"{g.get('instance_name')}_{i}"
    pc = {p: _rename_net(v, rmap) for p, v in (g.get('port_connections') or {}).items()}
    ng['port_connections'] = pc
    ng['port_connections_per_stage'] = {s: dict(pc) for s in STAGES}
    if g.get('output_net') is not None:
        ng['output_net'] = _rename_net(g.get('output_net'), rmap)
    ng['source'] = 'eco_emit_uniquify'
    ng['uniquify_copy'] = i
    return ng


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--study', required=True)
    ap.add_argument('--jira', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--ref-dir', required=True,
                    help='REF_DIR: netlist is the ground truth for N and for each '
                         'copy\'s own old net (fail-closed).')
    args = ap.parse_args()
    rtl_diff = json.loads(open(args.rtl_diff).read())
    study = json.loads(open(args.study).read())

    # families targeted by the ECO (RTL base names)
    fam_bases, new_port_of = set(), {}
    for c in rtl_diff.get('changes', []):
        fb = c.get('uniquified_family')
        if fb:
            fam_bases.add(fb)
            if c.get('change_type') == 'new_port' and c.get('declaration_type') == 'input':
                new_port_of[fb] = {'port_name': c.get('new_token'),
                                   'declaration_type': 'input',
                                   'bus_width': c.get('bus_width'),
                                   'context_line': c.get('context_line')}
    if not fam_bases:
        marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_uniquify.py\n"
                  "  no uniquified_family changes — no-op.\n")
        print(marker)
        open(args.output, 'w').write(json.dumps(study, indent=2))
        open(args.output.replace('.json', '_uniquify_marker.txt'), 'w').write(marker)
        return 0

    scans = {st: scan_stage(os.path.join(args.ref_dir, 'data', 'PreEco', f'{st}.v.gz'))
             for st in STAGES}
    errs, n_clone, n_portdecl, n_pc = [], 0, 0, 0

    for fb in sorted(fam_bases):
        for st in STAGES:
            inst_pins, modules = scans[st]
            entries = study.get(st, [])
            # canonical _0 module for this family+stage (Synth/PP: <base>_0 ; Route: <base>_0_0)
            canon_mod = None
            for e in entries:
                mn = e.get('module_name')
                if isinstance(mn, str) and re.search(re.escape(fb) + r'_0(_0)?$', mn):
                    canon_mod = mn; break
            if not canon_mod:
                continue
            netbase = re.sub(r'_0(_0)?$', '', canon_mod)
            # enumerate copies from the netlist (ground truth)
            copy_mod = {}   # index -> module name in this stage
            crE = re.compile(r'^' + re.escape(netbase) + r'_(\d+)(_0)?$')
            for m in modules:
                cm = crE.match(m)
                if cm:
                    copy_mod[int(cm.group(1))] = m
            if not copy_mod:
                continue
            # C3: normalize every family entry's module_name to the netlist ground
            # truth for THIS stage. The studier emits the Synthesize module name in
            # ALL stages, but P&R re-uniquifies the array with a `_0` suffix at Route
            # (umcrecrcqentry_39 -> umcrecrcqentry_39_0). An entry carrying the wrong
            # per-stage module name lands on no module at Route -> undriven at FM.
            for e in entries:
                mn = e.get('module_name')
                if not isinstance(mn, str):
                    continue
                cm = crE.match(mn)
                if cm:
                    idx = int(cm.group(1))
                    if idx in copy_mod and copy_mod[idx] != mn:
                        e['module_name'] = copy_mod[idx]
            # canon_mod was detected before normalization; realign it to the netlist
            # ground-truth _0 module for this stage so canon_gates/rews match.
            canon_mod = copy_mod.get(0, canon_mod)
            # canonical unit entries (gates + D/CP rewires) on the _0 module
            # compare_fold entries are emitted per-copy by eco_emit_compare_fold itself
            # (it loops the family), so uniquify must NOT clone/replicate them.
            canon_gates = [e for e in entries if e.get('change_type') == 'new_logic_gate'
                           and e.get('module_name') == canon_mod
                           and e.get('source') != 'eco_emit_compare_fold']
            canon_rews = [e for e in entries if e.get('change_type') == 'rewire'
                          and e.get('module_name') == canon_mod
                          and e.get('source') != 'eco_emit_compare_fold'
                          and (_DPIN.match(str(e.get('pin', ''))) or e.get('pin') in _CLKPIN)]
            # fresh ECO nets produced by the canonical gates
            fresh = set()
            for g in canon_gates:
                for p, v in (g.get('port_connections') or {}).items():
                    if p in _OUT_PINS and isinstance(v, str) and v.startswith('n_eco_'):
                        fresh.add(v)
            # per-copy old nets: (flop_cell, pin, old_net_0) from canonical rewires
            flop_pins = [(r.get('cell_name') or r.get('instance_name'), r.get('pin'),
                          r.get('old_net')) for r in canon_rews]

            for i, modname in sorted(copy_mod.items()):
                if i == 0:
                    continue
                # Skip a copy that ALREADY has its functional unit from ANY source —
                # a prior uniquify run OR the studier itself replicating all N copies.
                # Cloning on top double-drives the flop (duplicate D-pin rewires that
                # reference copy _0's nets, undriven in copy i). Detect an existing
                # in-copy gate or D/CP rewire on this module.
                already = any(
                    isinstance(e.get('module_name'), str) and e.get('module_name') == modname
                    and e.get('source') != 'eco_emit_compare_fold'
                    and (e.get('change_type') == 'new_logic_gate'
                         or (e.get('change_type') == 'rewire'
                             and (_DPIN.match(str(e.get('pin', ''))) or e.get('pin') in _CLKPIN)))
                    for e in entries)
                if already:
                    continue
                # resolve this copy's own old net for each rewire pin
                rmap = {net: f'{net}_{i}' for net in fresh}
                copy_ok = True
                per_pin_net = {}
                for cell, pin, old0 in flop_pins:
                    pins = inst_pins.get((modname, cell))
                    if not pins or pin not in pins:
                        errs.append(f"{fb} copy {i} ({modname}) stage {st}: flop {cell!r}.{pin} "
                                    f"not found in netlist — cannot resolve per-copy old net.")
                        copy_ok = False; break
                    per_pin_net[(cell, pin)] = pins[pin]
                    if old0 is not None:
                        rmap[old0] = pins[pin]
                if not copy_ok:
                    continue
                # clone gates
                for g in canon_gates:
                    entries.append(_clone_gate(g, i, modname, rmap)); n_clone += 1
                # clone D/CP rewires with this copy's own old net + suffixed new net
                for r in canon_rews:
                    nr = dict(r)
                    nr['module_name'] = modname
                    cell = r.get('cell_name') or r.get('instance_name')
                    nr['old_net'] = per_pin_net[(cell, r.get('pin'))]
                    nr['new_net'] = _rename_net(r.get('new_net'), rmap)
                    nr.pop('old_net_per_stage', None)
                    nr['source'] = 'eco_emit_uniquify'
                    nr['uniquify_copy'] = i
                    entries.append(nr); n_clone += 1

            # per-copy port_declaration (all copies incl _0). UPSERT, do not blindly add:
            # the studier already emits one correct entry per copy (schema `signal_name`
            # + `bus_width`). Keying a dedup on `port_name` alone missed those and
            # appended 40 wrong-schema duplicates (applier reads `signal_name`, so they
            # were dead weight bloating the study). Instead: for each copy override any
            # existing entry in place to the applier schema, collapse duplicates, and
            # add ONLY where a copy has none.
            npd = new_port_of.get(fb)
            if npd:
                pname = npd['port_name']
                bw = npd.get('bus_width')
                by_mod = {}   # module_name -> existing port_declaration entries for this port
                for e in entries:
                    if e.get('change_type') != 'port_declaration':
                        continue
                    if (e.get('signal_name') or e.get('port_name')) != pname:
                        continue
                    by_mod.setdefault(e.get('module_name'), []).append(e)
                drop = set()
                for i, modname in sorted(copy_mod.items()):
                    ex = by_mod.get(modname) or []
                    # reason/notes are REQUIRED context fields (step-3 validator 0e).
                    _rsn = (f'uniquified per-copy input port {pname} on {modname} (copy {i}) '
                            f'for the compare_fold ECO')
                    _nts = (f'eco_emit_uniquify.py replicated the canonical new-port declaration '
                            f'to uniquified copy {i} ({modname}). source: eco_emit_uniquify')
                    if ex:
                        keep = ex[0]                       # override the first in place
                        keep['signal_name'] = pname
                        keep.pop('port_name', None)
                        keep['declaration_type'] = 'input'
                        if bw is not None and not keep.get('bus_width'):
                            keep['bus_width'] = bw
                        if not keep.get('reason'):
                            keep['reason'] = _rsn
                        if not keep.get('notes'):
                            keep['notes'] = _nts
                        for extra in ex[1:]:               # collapse duplicates
                            drop.add(id(extra))
                    else:
                        pd = {
                            'change_type': 'port_declaration', 'module_name': modname,
                            'signal_name': pname, 'declaration_type': 'input',
                            'context_line': npd.get('context_line'),
                            'source': 'eco_emit_uniquify', 'uniquify_copy': i,
                            'reason': _rsn, 'notes': _nts,
                        }
                        if bw is not None:
                            pd['bus_width'] = bw
                        entries.append(pd)
                        n_portdecl += 1
                if drop:
                    study[st] = [e for e in entries if id(e) not in drop]
                    entries = study[st]

            # per-instance port_connection — the parent instantiates the array N times
            # (RCQ_ENTRIES_<i>__<child>); the ECO must connect the new port on EVERY
            # instance, not just copy _0 (else 39 copies float the port -> FM fail).
            canon_pcs = [c for c in rtl_diff.get('changes', [])
                         if c.get('change_type') == 'port_connection'
                         and c.get('uniquified_family') == fb]
            if canon_pcs:
                gz = os.path.join(args.ref_dir, 'data', 'PreEco', f'{st}.v.gz')
                fam_insts = scan_family_instances(gz, netbase)
                for pc in canon_pcs:
                    pname = pc.get('port_name') or pc.get('new_token')
                    have = {e.get('instance_name') for e in entries
                            if e.get('change_type') == 'port_connection'
                            and (e.get('port_name') or e.get('new_token')) == pname}
                    for i, inst in sorted(fam_insts.items()):
                        if inst in have:
                            continue
                        npc = dict(pc)
                        npc['instance_name'] = inst
                        npc['source'] = 'eco_emit_uniquify'
                        npc['uniquify_copy'] = i
                        entries.append(npc); n_pc += 1
            study[st] = entries

    if errs:
        marker = ("ECO_SCRIPT_LAUNCHED: eco_emit_uniquify.py\n"
                  f"  ABORTED — {len(errs)} copy resolution failure(s):\n"
                  + "".join(f"    - {e}\n" for e in errs[:20])
                  + ("    …\n" if len(errs) > 20 else "")
                  + "  Study UNTOUCHED.\n")
        print(marker)
        open(args.output.replace('.json', '_uniquify_marker.txt'), 'w').write(marker)
        return 2

    open(args.output, 'w').write(json.dumps(study, indent=2))
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_emit_uniquify.py\n"
              f"  families replicated: {sorted(fam_bases)}\n"
              f"  cloned gate+rewire entries (all stages): {n_clone}\n"
              f"  per-copy port_declarations added:        {n_portdecl}\n"
              f"  per-instance port_connections added:     {n_pc}\n")
    print(marker)
    open(args.output.replace('.json', '_uniquify_marker.txt'), 'w').write(marker)
    return 0


if __name__ == '__main__':
    sys.exit(main())
