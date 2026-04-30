#!/usr/bin/env python3
"""
eco_perl_spec.py — Generate Perl ECO application script from study JSON.

Replaces eco_applier Phase A (agent reasoning) with deterministic code.
Handles: ALREADY_APPLIED detection, SKIPPED checks, wire_decls exclusion (SVR-9 prevention),
wire_removes, gate line building, and Perl script output.

Usage:
    python3 script/eco_perl_spec.py \
        --study    data/<TAG>_eco_preeco_study.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --jira     <JIRA> \
        --stage    Synthesize|PrePlace|Route \
        --round    <ROUND> \
        [--prev-applied data/<TAG>_eco_applied_round<N-1>.json] \
        --output   runs/eco_apply_<TAG>_<Stage>.pl \
        --status   data/<TAG>_eco_perl_spec_<Stage>.json

Exit code: 0 = OK, 1 = error
"""

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def zgrep_count(pattern, gz_path, timeout=60):
    """grep -cF (fixed string) pattern in gzipped file. Returns int.
    Uses -F to treat pattern as literal string — prevents brackets like [2]
    from being interpreted as character classes by grep."""
    try:
        proc = subprocess.run(
            f'zcat {gz_path} | grep -cF {repr(pattern)}',
            shell=True, capture_output=True, text=True, timeout=timeout
        )
        return int(proc.stdout.strip() or '0')
    except Exception:
        return 0


def net_exists_in_posteco(net, gz_path, timeout=60):
    """True if net is referenced anywhere in PostEco stage file."""
    return zgrep_count(net, gz_path, timeout) > 0


def build_scope_to_module_map(gz_path, timeout=120):
    """
    Build a mapping from instance path last segment → gate-level module name.
    e.g.  'ARB/DCQARB' → 'ddrss_umccmd_t_umcdcqarb_0'
    Scans module declarations in the gate-level netlist.
    Returns dict: {scope_key: module_name}
    """
    try:
        proc = subprocess.run(
            f'zcat {gz_path} | grep "^module "',
            shell=True, capture_output=True, text=True, timeout=timeout
        )
        modules = []
        for line in proc.stdout.splitlines():
            m = re.match(r'^module\s+(\S+)', line)
            if m:
                modules.append(m.group(1))
    except Exception:
        return {}

    scope_map = {}
    for mod in modules:
        # Last segment of module name after final underscore group gives a hint
        # e.g. ddrss_umccmd_t_umcdcqarb_0 → last meaningful part = umcdcqarb
        parts = mod.split('_')
        for i, part in enumerate(parts):
            if part.startswith('umc') or part.startswith('ati') or part.startswith('gmc'):
                suffix = '_'.join(parts[i:])
                scope_map[suffix] = mod
                break
    return scope_map


def resolve_module_name(e, scope_to_mod, gz_path):
    """
    Resolve gate-level module_name for a study entry.
    Priority: explicit module_name > scope-based lookup > posteco grep.
    """
    mod = e.get('module_name', '')
    if mod:
        return mod

    scope = e.get('instance_scope', '')
    if not scope:
        return ''

    # Try last segment of scope path
    last = scope.split('/')[-1].lower()
    for suffix, modname in scope_to_mod.items():
        if last in suffix.lower() or suffix.lower().endswith(last):
            return modname

    # Fallback: grep PostEco for module containing the instance
    inst = e.get('instance_name', '')
    if inst:
        try:
            proc = subprocess.run(
                f'zcat {gz_path} | grep -B500 " {inst} " | grep "^module " | tail -1',
                shell=True, capture_output=True, text=True, timeout=30
            )
            m = re.match(r'^module\s+(\S+)', proc.stdout.strip())
            if m:
                return m.group(1)
        except Exception:
            pass

    return scope  # last resort: use scope as key


def already_applied(inst_name, gz_path, prev_status=None, force_reapply=False):
    """Check if instance already inserted in PostEco. Returns True/False."""
    if force_reapply:
        return False
    if prev_status and prev_status == 'SKIPPED':
        return False  # SKIPPED in prior round → not applied
    count = zgrep_count(inst_name, gz_path)
    return count > 0


def output_pin_key(port_connections):
    """Find the output pin (Z, ZN, Q, QN) from port_connections dict."""
    for pin in ('ZN', 'Z', 'Q', 'QN', 'CO', 'S'):
        if pin in port_connections:
            return pin
    # Fallback: last key is usually output
    return list(port_connections.keys())[-1] if port_connections else None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study',        required=True)
    p.add_argument('--ref-dir',      required=True)
    p.add_argument('--tag',          required=True)
    p.add_argument('--jira',         required=True)
    p.add_argument('--stage',        required=True, choices=['Synthesize','PrePlace','Route'])
    p.add_argument('--round',        required=True, type=int)
    p.add_argument('--prev-applied', default=None)
    p.add_argument('--output',       required=True)
    p.add_argument('--status',       required=True)
    p.add_argument('--tile',         default='', help='Tile name for tile-root module resolution')
    args = p.parse_args()

    study    = json.loads(Path(args.study).read_text())
    posteco  = f"{args.ref_dir}/data/PostEco/{args.stage}.v.gz"
    preeco   = f"{args.ref_dir}/data/PreEco/{args.stage}.v.gz"

    prev_applied = {}
    if args.prev_applied and Path(args.prev_applied).exists():
        pa = json.loads(Path(args.prev_applied).read_text())
        for e in pa.get(args.stage, []):
            inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name','')
            if inst:
                prev_applied[inst] = e.get('status','')

    entries  = study.get(args.stage, [])

    # Build set of nets that are NEW PORTS being added by Pass 2 (port_declaration entries).
    # These nets won't exist in PostEco yet at Pass 1 time — Pass 2 adds them.
    # Gates whose inputs reference these nets MUST NOT be skipped by Pass 1.
    new_port_nets = set()
    for e in entries:
        if e.get('change_type') in ('port_declaration', 'port_promotion', 'new_port'):
            sig = e.get('signal_name') or e.get('new_token') or ''
            if sig:
                new_port_nets.add(sig)
        # Also include output nets of gates that will be inserted by this same Perl batch
        if e.get('change_type') in ('new_logic_gate', 'new_logic_dff', 'new_logic'):
            out = e.get('output_net', '')
            if out:
                new_port_nets.add(out)

    # Build scope → module_name map from PostEco for module resolution
    scope_to_mod = build_scope_to_module_map(posteco)

    # Find the tile-root module name (for entries with empty instance_scope)
    # Tile-root module follows pattern: ddrss_<tile>_t_<tile> (or with _0 suffix in Route)
    tile_root_module = ''
    if args.tile:
        # Tile-root module is ddrss_<tile>_t (or ddrss_<tile>_t_0 in Route)
        # It does NOT have a submodule suffix after _t
        for pat in [f'ddrss_{args.tile}_t ', f'ddrss_{args.tile}_t_0 ']:
            try:
                proc = subprocess.run(
                    f'zcat {posteco} | grep -m1 "^module {pat}" | awk \'{{print $2}}\'',
                    shell=True, capture_output=True, text=True, timeout=60
                )
                candidate = proc.stdout.strip().rstrip('(').rstrip()
                if candidate:
                    tile_root_module = candidate
                    break
            except Exception:
                pass
    # {module_name: {wire_decls, wire_removes, gates}}
    changes    = {}
    port_decls = {}   # Pass 2: {module_name: [{signal, direction}]}
    port_conns = {}   # Pass 3: {instance_name: [{port, net}]}
    rewires    = {}   # Pass 4: {cell_name: [{pin, old, new}]}
    statuses   = []   # list of {instance_name, status, reason}

    for e in entries:
        if not e.get('confirmed', True):
            statuses.append({'name': e.get('instance_name','?'), 'status':'EXCLUDED',
                             'reason': e.get('reason','unconfirmed')})
            continue

        ct   = e.get('change_type','')
        inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name','')
        # Resolve gate-level module name (generic — works for any tile)
        mod   = resolve_module_name(e, scope_to_mod, posteco)
        # For tile-root entries (empty scope, scope_is_tile_root=True), use tile root module
        if not mod and (e.get('scope_is_tile_root') or not e.get('instance_scope','')):
            mod = tile_root_module
        force = e.get('force_reapply', False)

        # ── new_logic_gate / new_logic_dff ───────────────────────────────────
        if ct in ('new_logic_gate', 'new_logic_dff', 'new_logic'):
            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}

            prev_st = prev_applied.get(inst, '')
            if already_applied(inst, posteco, prev_st, force):
                statuses.append({'name': inst, 'status':'ALREADY_APPLIED',
                                 'reason': f'grep found {inst} in PostEco {args.stage}'})
                continue

            # Per-stage port connections
            pcs = e.get('port_connections_per_stage', {}).get(args.stage) \
                  or e.get('port_connections', {})

            # Check all input nets exist in PostEco
            out_pin = output_pin_key(pcs)
            skip_reason = None
            # skip_input_net_check: gate inputs depend on new ports (Pass 2) or renamed
            # driver nets (Pass 4) — they will exist after those passes run.
            # Verilog allows forward reference; insert the gate unconditionally.
            if not e.get('skip_input_net_check'):
              for pin, net in pcs.items():
                if pin == out_pin:
                    continue
                if net in ("1'b0", "1'b1") or str(net).startswith('NEEDS_NAMED_WIRE:'):
                    continue
                if str(net).startswith('UNRESOLVABLE_IN_'):
                    continue
                # n_eco_* nets are intermediate batch nets — they don't exist in PostEco
                # yet but will be created by other gates in the same Perl batch insertion.
                # Skip the PostEco check for these — they are always valid within a batch.
                if re.match(r'^n_eco_\d+_', str(net)):
                    continue
                # SEQMAP_NET_*_orig is a driver-rename intermediate net — also valid in batch
                if re.match(r'^SEQMAP_NET_\d+_orig$', str(net)):
                    continue
                # eco_<jira>_*_orig nets are renamed driver outputs — valid after Pass 4 rewire
                if re.match(r'^eco_\d+_\w+_orig$', str(net)):
                    continue
                # Skip existence check for nets that are new ports (added by Pass 2)
                # or output nets of other gates in this same Perl batch (forward reference)
                if net in new_port_nets:
                    continue
                if not net_exists_in_posteco(net, posteco):
                    skip_reason = f"input net '{net}' absent in {args.stage}"
                    break

            if skip_reason:
                statuses.append({'name': inst, 'status':'SKIPPED', 'reason': skip_reason})
                continue

            # wire_decls: output net only, and only if NOT already in PostEco
            out_net = pcs.get(out_pin, '') if out_pin else ''
            if e.get('needs_explicit_wire_decl') and out_net:
                existing = zgrep_count(out_net, posteco)
                if existing == 0:
                    changes[mod]['wire_decls'].append(out_net)
                else:
                    statuses.append({'name': inst, 'status':'INFO',
                                     'reason': f'wire_decl SKIPPED for {out_net}: already referenced ({existing}x) — SVR-9 prevention'})

            # Build gate line — cell_type must not be empty (SVR-4: missing cell type = invalid Verilog)
            cell_type = e.get('cell_type','')
            if not cell_type:
                # Fallback: try other stage entries for the same instance_name
                for fb_stage in ['Synthesize','PrePlace','Route']:
                    if fb_stage == stage: continue
                    for fb_e in study.get(fb_stage,[]):
                        if fb_e.get('instance_name') == inst and fb_e.get('cell_type'):
                            cell_type = fb_e['cell_type']
                            break
                    if cell_type: break
            if not cell_type:
                statuses.append({'name': inst, 'status':'SKIPPED',
                                 'reason': f'cell_type empty for {inst} in {stage} — cannot insert without cell type (SVR-4 risk)'})
                continue
            pins_str  = ', '.join(f'.{pin}({net})' for pin, net in pcs.items())
            gate_line = f'  // ECO {args.jira} TAG={args.tag} Round={args.round}'
            changes[mod]['gates'].append(gate_line)
            changes[mod]['gates'].append(f'  {cell_type} {inst} ( {pins_str} ) ;')
            statuses.append({'name': inst, 'status':'INSERTED',
                             'reason': f'Added to Perl spec for module {mod}'})

        # ── remove_wire_decl ─────────────────────────────────────────────────
        elif ct == 'remove_wire_decl':
            sig = e.get('signal_name','')
            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}
            changes[mod]['wire_removes'].append(sig)
            statuses.append({'name': sig, 'status':'APPLIED',
                             'reason': f'remove_wire_decl added to Perl wire_removes'})

        # ── port_declaration / port_promotion (Pass 2) ───────────────────────────
        elif ct in ('port_declaration', 'port_promotion'):
            sig = e.get('signal_name', '')
            direction = e.get('declaration_type', 'input')
            if direction == 'wire':
                statuses.append({'name': sig, 'status':'SKIPPED',
                                 'reason':'wire — implicitly declared by port connections'})
            elif sig and mod:
                if mod not in port_decls:
                    port_decls[mod] = []
                port_decls[mod].append({'signal': sig, 'direction': direction})
                statuses.append({'name': sig, 'status':'QUEUED',
                                 'reason': f'port_declaration queued for Perl Pass 2 in {mod}'})

        # ── port_connection (Pass 3) ─────────────────────────────────────────────
        elif ct == 'port_connection':
            inst_n   = e.get('instance_name', '')
            port_n   = e.get('port_name', '')
            net_n    = e.get('net_name', '')
            if inst_n and port_n and net_n:
                key = inst_n
                if key not in port_conns:
                    port_conns[key] = []
                port_conns[key].append({'port': port_n, 'net': net_n})
                statuses.append({'name': inst_n, 'status':'QUEUED',
                                 'reason': f'.{port_n}({net_n}) queued for Perl Pass 3 on {inst_n}'})

        # ── rewire (Pass 4) ──────────────────────────────────────────────────────
        elif ct == 'rewire':
            per_stage_cn = e.get('per_stage_cell_name', {})
            cell_n = per_stage_cn.get(args.stage, '') or e.get('cell_name', '')
            pin_n  = e.get('pin', '')
            old_n  = e.get('old_net', '')
            new_n  = e.get('new_net', '')
            if cell_n and pin_n and new_n:
                if cell_n not in rewires:
                    rewires[cell_n] = []
                rewires[cell_n].append({'pin': pin_n, 'old': old_n, 'new': new_n})
                statuses.append({'name': cell_n, 'status':'QUEUED',
                                 'reason': f'.{pin_n}({old_n}→{new_n}) queued for Perl Pass 4 on {cell_n}'})

        else:
            statuses.append({'name': inst, 'status':'UNHANDLED',
                             'reason': f'{ct} — not handled by eco_perl_spec'})

    # ── Write Perl script ─────────────────────────────────────────────────────
    perl_lines = [
        '#!/usr/bin/perl',
        f'# ECO Apply — JIRA {args.jira} — {args.stage} stage',
        f'# TAG={args.tag}  Round={args.round}',
        '# Auto-generated by eco_perl_spec.py — do NOT edit manually',
        'use strict; use warnings;',
        '',
        'my %changes = (',
    ]

    for mod, spec in changes.items():
        wd_str   = ', '.join(f"'{w}'" for w in spec['wire_decls'])
        wrm_str  = ', '.join(f"'{w}'" for w in spec['wire_removes'])
        gate_str = '\n'.join(f"    {repr(g)}," for g in spec['gates'])
        perl_lines.append(f"  '{mod}' => {{")
        perl_lines.append(f"    wire_decls   => [{wd_str}],")
        perl_lines.append(f"    wire_removes => [{wrm_str}],")
        perl_lines.append(f"    gates        => [")
        perl_lines.append(gate_str)
        perl_lines.append(f"    ],")
        perl_lines.append(f"  }},")

    perl_lines += [
        ');',
        '',
        "my $in_module = ''; my @buf; my %processed;",
        "while (my $line = <STDIN>) {",
        "    if ($line =~ /^module\\s+(\\S+?)[\\s(;]/) {",
        "        my $mod = $1;",
        "        if (exists $changes{$mod}) { $in_module=$mod; @buf=($line); next; }",
        "    }",
        "    if ($in_module) {",
        "        if ($line =~ /^endmodule\\b/) {",
        "            my $spec = $changes{$in_module};",
        "            my %rm = map { $_ => 1 } @{ $spec->{wire_removes} };",
        "            my @filtered;",
        "            for my $bl (@buf) {",
        "                if (%rm && $bl =~ /^\\s*wire\\s+(\\w+)\\s*;/ && $rm{$1})",
        "                    { print STDERR \"REMOVED wire $1\\n\"; next; }",
        "                push @filtered, $bl;",
        "            }",
        "            # Add wire decls — skip if net already in buffer (SVR-9 prevention)",
        "            my $buf_text = join('', @filtered);",
        "            for my $net (@{ $spec->{wire_decls} }) {",
        "                if ($buf_text =~ /\\b\\Q$net\\E\\b/)",
        "                    { print STDERR \"SVR9_PREVENT: SKIP wire $net\\n\"; }",
        "                else { push @filtered, \"  wire $net ;\\n\"; }",
        "            }",
        "            push @filtered, \"$_\\n\" for @{ $spec->{gates} };",
        "            print join('', @filtered); print $line;",
        "            $processed{$in_module}++;",
        "            $in_module=''; @buf=();",
        "        } else { push @buf, $line; }",
        "    } else { print $line; }",
        "}",
        "print STDERR \"\\n=== ECO PERL SPEC SUMMARY: " + args.tag + " " + args.stage + " ===\\n\";",
        "for my $mod (sort keys %changes) {",
        "    if ($processed{$mod}) { print STDERR \"OK  $mod\\n\"; }",
        "    else { print STDERR \"MISSING  $mod\\n\"; }",
        "}",
        "print STDERR \"=== DONE ===\\n\";",
    ]

    Path(args.output).write_text('\n'.join(perl_lines) + '\n')
    Path(args.output).chmod(0o755)

    # Write status JSON
    Path(args.status).write_text(json.dumps({
        'tag': args.tag, 'stage': args.stage, 'round': args.round,
        'modules': list(changes.keys()),
        'entries': statuses
    }, indent=2))

    inserted = sum(1 for s in statuses if s['status'] == 'INSERTED')
    skipped  = sum(1 for s in statuses if s['status'] == 'SKIPPED')
    already  = sum(1 for s in statuses if s['status'] == 'ALREADY_APPLIED')

    # Write launch marker — agent includes this in Step 4 RPT to prove script ran
    marker = (
        f"ECO_SCRIPT_LAUNCHED: eco_perl_spec.py\n"
        f"  stage:    {args.stage}\n"
        f"  round:    {args.round}\n"
        f"  INSERTED: {inserted}\n"
        f"  SKIPPED:  {skipped}\n"
        f"  ALREADY:  {already}\n"
        f"  perl:     {args.output}\n"
        f"  status:   {args.status}"
    )
    print(marker)
    Path(args.status.replace('.json', '_marker.txt')).write_text(marker + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
