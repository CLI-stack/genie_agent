#!/usr/bin/env python3
"""
eco_emit_loopback_bridge.py — deterministically emit the CSR register read-back
(oQ/iQ) loopback rename entries for a Mode-I spare-bit bridge.

When a spare CSR bit `REG_<F>[b]` is bridged out, the register-file wrapper's
output port `oQ_<F>_<F>` is left undriven: its register-block (`oQ_<F>_*`) and
read-mux (`iQ_<F>_*`) sub-instances connect to an INTERNAL net `oQ_<F>_<F>_0`
instead of the wrapper output port. The engineer fix renames those connections
`oQ_<F>_<F>_0 -> oQ_<F>_<F>` on BOTH ports so the register value reaches the
wrapper output. This is mechanical — do NOT leave it to the LLM studier (which
hallucinates wrong-register ports like oQ_UMC_CONFIG_DDR_TYPE).

Emits `port_connection` study entries (per-stage net_name_before) the studier
splices verbatim into the Mode-I bridge.

Usage:
  eco_emit_loopback_bridge.py --ref-dir R --family UmcCfgEco \
      --regfile-module ddrss_umccmd_t_umccmdregs --output out.json
"""
import argparse
import gzip
import json
import os
import re
import sys

_STAGES = ('Synthesize', 'PrePlace', 'Route')


def _load(ref, stage):
    gz = os.path.join(ref, 'data', 'PreEco', f'{stage}.v.gz')
    if not os.path.exists(gz):
        return ''
    with gzip.open(gz, 'rt') as f:
        return f.read()


def _mod_body(text, modbase):
    # match a module whose name contains modbase (handles _0 uniquification in Route)
    m = re.search(r'(?m)^module\s+(\w*' + re.escape(modbase) + r'\w*)\b.*?^endmodule',
                  text, re.DOTALL)
    return (m.group(1), m.group(0)) if m else (None, '')


def derive(ref, family, regfile_base):
    # per (instance, port) -> {child, net_after, net_before:{stage:...}, module:{stage:...}}
    found = {}
    for stage in _STAGES:
        text = _load(ref, stage)
        if not text:
            continue
        modname, body = _mod_body(text, regfile_base)
        if not body:
            continue
        pm = re.search(r'(?m)^\s*output\s*\[\d+:\d+\]\s*(oQ_' + re.escape(family) + r'\w*)\s*;', body)
        if not pm:
            continue
        out_port = pm.group(1)          # e.g. oQ_UmcCfgEco_UmcCfgEco
        internal = out_port + '_0'      # the undriven internal net
        pat = re.compile(r'\.((?:oQ|iQ)_' + re.escape(family) + r'\w*)\s*\(\s*'
                         + re.escape(internal) + r'\s*\)')
        for cm in pat.finditer(body):
            port = cm.group(1)
            last = None
            for im in re.finditer(r'(?m)^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(', body[:cm.start()]):
                last = im
            inst = last.group(2) if last else None
            child = last.group(1) if last else None
            key = (inst, port)
            rec = found.setdefault(key, {'child': child, 'net_after': out_port,
                                         'net_before': {}, 'module': {}})
            rec['net_before'][stage] = internal
            rec['module'][stage] = modname
    entries = []
    for (inst, port), rec in found.items():
        entries.append({
            'change_type': 'port_connection',
            'module_name': rec['module'].get('Synthesize') or next(iter(rec['module'].values()), ''),
            'module_name_per_stage': rec['module'],
            'instance_name': inst,
            'child_module_name': rec['child'],
            'port_name': port,
            'net_name': rec['net_after'],          # full-bus rename to the wrapper output port
            'net_name_before': rec['net_before'],   # per-stage internal net
            'source': 'eco_emit_loopback_bridge',
            'reason': f'CSR {port} read-back loopback: drive wrapper output {rec["net_after"]} '
                      f'from register block (was internal {rec["net_after"]}_0)',
        })
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--ref-dir', required=True)
    ap.add_argument('--family', required=True, help='CSR family base, e.g. UmcCfgEco')
    ap.add_argument('--regfile-module', required=True,
                    help='register-file wrapper module base, e.g. ddrss_umccmd_t_umccmdregs')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    entries = derive(args.ref_dir, args.family, args.regfile_module)
    with open(args.output, 'w') as f:
        json.dump(entries, f, indent=2)

    print('ECO_SCRIPT_LAUNCHED: eco_emit_loopback_bridge.py')
    print(f'  family={args.family}  regfile={args.regfile_module}')
    print(f'  emitted {len(entries)} loopback port_connection entries -> {args.output}')
    for e in entries:
        print(f"    {e['instance_name']}.{e['port_name']}: "
              f"{e['net_name_before']} -> {e['net_name']}")
    if not entries:
        print('  WARNING: no oQ/iQ loopback connections found — check family/regfile module.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
