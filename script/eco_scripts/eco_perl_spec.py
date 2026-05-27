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
import os
import re
import subprocess
import sys
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

# Bus-bit form (e.g. `X[1]`) is illegal in a wire declaration. Studier may
# legitimately think of it as "bit 1 of bus X", but `wire X[1] ;` is invalid
# Verilog. The convention used everywhere else in the netlist is the flat-net
# form `X_1_` (underscore-escape). The applier auto-converts on consumption
# so studier output gets emitted as legal Verilog without losing semantic intent.
#
# Run 20260512070625 root cause #2: studier's `named_net: "REG_UmcCfgEco[1]"`
# was passed verbatim to wire_decls → `wire REG_UmcCfgEco[1] ;` → SVR-4/SVR-64
# → FM-599 ABORT → 5 hours of misdiagnosis. With this sanitizer, the same
# input becomes `wire REG_UmcCfgEco_1_ ;` (valid).
_BRACKET_BIT_RE = re.compile(r'\[(\d+)\]')


def _sanitize_named_net(named):
    """Convert bus-bit form to flat-net form: 'X[1]' → 'X_1_'.
    Idempotent. Pass through anything that's already a clean identifier.
    Returns (sanitized_name, was_transformed) so the caller can log."""
    if not named:
        return named, False
    if '[' not in named:
        return named, False
    sanitized = _BRACKET_BIT_RE.sub(lambda m: f'_{m.group(1)}_', named)
    return sanitized, (sanitized != named)


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
    P&R stages uniquify modules with _0/_1 suffixes — try those variants too.
    """
    mod = e.get('module_name', '')
    if mod:
        # In P&R stages, module may be uniquified as mod_0, mod_1, etc.
        # Verify the module exists in PostEco; if not, try _0 suffix.
        for candidate in [mod, mod + '_0', mod + '_1', mod + '_0_0']:
            try:
                proc = subprocess.run(
                    f'zcat {gz_path} | grep -c "^module {re.escape(candidate)}\\b"',
                    shell=True, capture_output=True, text=True, timeout=10
                )
                if int(proc.stdout.strip() or '0') > 0:
                    return candidate
            except Exception:
                pass
        return mod  # fallback: return original even if not found

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
    p.add_argument('--apply', action='store_true',
                   help='After generating the Perl script, EXECUTE it against the PostEco netlist '
                        '(zcat <stage>.v.gz | perl <script> | gzip > <stage>.v.gz). Without this '
                        'flag the script only generates the .pl file — cells will NOT be inserted '
                        'into the netlist, and Step 5 will catch the gap. Recommended: always pass '
                        '--apply unless you intentionally want to inspect the .pl before running.')
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

    # Build set of nets that will be implicitly declared by other passes in this
    # batch — do NOT add explicit wire_decl for these (prevents FM-599 duplicate).
    # Sources of implicit wire creation:
    #   - Pass 4 rewires: .PORT(new_net) hookup creates the wire implicitly
    #   - Pass 3 port_connections: .PORT(net) connection in parent module
    # GAP-4 fix: also collect port_connection nets — a port connection in the
    # same batch creates an implicit wire before the explicit wire_decl can land,
    # causing FM-599 'Duplicate wire declaration' (observed in 9899 Route CMDARB
    # with n_eco_9899_new_pri_chain: rewire + port_connection both in same batch).
    rewire_new_nets = set()
    for e in entries:
        if e.get('change_type') == 'rewire':
            new_net = e.get('new_net', '')
            if new_net:
                rewire_new_nets.add(new_net)
        elif e.get('change_type') in ('port_connection', 'port_promotion'):
            # Port connections create implicit wires for the connected net
            net = e.get('net_name') or e.get('flat_net_name') or e.get('new_token', '')
            if net and not net.startswith("1'b"):
                rewire_new_nets.add(net)

    # Build set of all nets that will have implicit wires created in this batch
    # by port_connection targets or by gate output pins (.Z/.ZN/.Q).
    # When any of these appear as an explicit `wire X ;` declaration → SVR-9.
    port_conn_nets = set()
    for e in entries:
        # port_connection target net → implicit wire from .PORT(net) hookup
        if e.get('change_type') == 'port_connection':
            net = e.get('net_name') or e.get('flat_net_name') or ''
            if net and not net.startswith("1'b"):
                port_conn_nets.add(net)
        # new_logic_gate being INSERTED → gate line has .Z(out_net) which
        # auto-creates an implicit wire — explicit `wire out_net ;` = SVR-9
        if e.get('change_type') in ('new_logic_gate', 'and_term'):
            for pin, net in (e.get('port_connections') or {}).items():
                if pin in ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S') and isinstance(net, str):
                    if net and not net.startswith("1'b") and not net.startswith("n_eco_") is False:
                        port_conn_nets.add(net)
            out = e.get('output_net', '')
            if out and not out.startswith("1'b"):
                port_conn_nets.add(out)

    # Track instance names already queued in this Perl batch (dedup guard for RISK 1.1).
    queued_instances = set()

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
    # Bus DFF groups: (mod, target_reg) → set of bit indices.
    # After the main loop we emit ONE  wire [N-1:0] target_reg;  per group so
    # the new bus signal is properly declared in the module body.  Individual
    # per-bit DFF cells produce only bit-slice implicit wires (e.g. sig[3]);
    # the full bus declaration is required for consumers that reference the
    # whole bus (e.g. port connections using the signal as a bus operand).
    bus_dff_groups = {}   # (mod, target_reg) → set of int bit indices

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
        # Guard: if module still unresolved, skip to avoid invalid Perl key ''
        if not mod:
            statuses.append({'name': inst, 'status':'SKIPPED',
                             'reason': f'module unresolvable for {inst} — no module_name, scope, or tile-root'})
            continue
        force = e.get('force_reapply', False)

        # ── new_logic_gate / new_logic_dff ───────────────────────────────────
        if ct in ('new_logic_gate', 'new_logic_dff', 'new_logic'):
            # Bus vector tracking — covers both bus DFFs (is_bus_dff_bit) and bus
            # combinational gates (is_bus_gate_bit).  After the loop we emit ONE
            # wire [N-1:0] sig;  per group so consumers that reference the whole
            # bus (e.g. port connections, downstream gate inputs) see a declared net.
            if e.get('is_bus_dff_bit') and mod:
                bit_idx = e.get('bus_bit_index')
                target_reg = re.sub(r'_reg_\d+_$', '', inst)
                if bit_idx is not None and target_reg:
                    key = (mod, target_reg)
                    bus_dff_groups.setdefault(key, set()).add(int(bit_idx))
            if e.get('is_bus_gate_bit') and mod:
                bit_idx = e.get('bus_bit_index')
                # output_net for a bus gate bit: e.g. "wdbptr_org0_d2_nxt[3]"
                out_net = e.get('output_net', '')
                base_net = re.sub(r'\[\d+\]$', '', out_net)
                if bit_idx is not None and base_net:
                    key = (mod, base_net)
                    bus_dff_groups.setdefault(key, set()).add(int(bit_idx))

            # GAP-7: existing-signal reuse — skip cell insertion entirely when
            # the studier marked this gate as reusing an existing wire. The
            # downstream gate that consumed this entry's output should already
            # have its input substituted in port_connections_per_stage.
            #
            # Two equivalent flags trigger skip (studier MD §0a — both should be
            # set on reuse entries; either alone is sufficient for backward compat):
            #   reuse_existing_wire: true        — legacy / semantic flag
            #   skip_cell_instantiate: true      — explicit applier directive
            if e.get('reuse_existing_wire') or e.get('skip_cell_instantiate'):
                ips = (e.get('inputs_per_stage') or {}).get(args.stage)
                flags = []
                if e.get('reuse_existing_wire'):    flags.append('reuse_existing_wire')
                if e.get('skip_cell_instantiate'): flags.append('skip_cell_instantiate')
                statuses.append({'name': inst, 'status': 'SKIPPED',
                                 'reason': f'GAP-7: cell skip ({"+".join(flags)}) — '
                                           f'{args.stage} uses {ips!r} (cell not instantiated; '
                                           f'output_net aliased to per-stage input)'})
                continue

            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}

            prev_st = prev_applied.get(inst, '')
            if already_applied(inst, posteco, prev_st, force):
                statuses.append({'name': inst, 'status':'ALREADY_APPLIED',
                                 'reason': f'grep found {inst} in PostEco {args.stage}'})
                # CRITICAL: still process unconnected_rewires wire_decls even for
                # ALREADY_APPLIED gates. The gate may be in PostEco but its associated
                # wire declaration (from UNCONNECTED rename) might be missing if it was
                # added in a prior round and not re-verified. Without the explicit wire
                # FM cannot trace the REGCMD bus bit → DFF0X / globally unmatched.
                # Auto-sanitize bracket form + apply 5-layer dedup so we never emit
                # a duplicate or invalid wire decl on this path either.
                for ur in e.get('unconnected_rewires', []):
                    named_raw = ur.get('named_net', '')
                    if not named_raw:
                        continue
                    named, was_san = _sanitize_named_net(named_raw)
                    if was_san:
                        statuses.append({'name': named_raw, 'status': 'AUTO_SANITIZED',
                                         'reason': f'(ALREADY_APPLIED path) named_net "{named_raw}" '
                                                   f'used bus-bit form; converted to "{named}"'})
                    # Skip if any of: rewire-implicit, PostEco existing, PreEco
                    # existing, intra-batch, or PostEco port-use already present.
                    if named in rewire_new_nets:
                        continue
                    if named in changes[mod]['wire_decls']:
                        continue
                    if zgrep_count(named, posteco) > 0:
                        continue
                    if zgrep_count(named, preeco) > 0:
                        continue
                    has_port_use = False
                    try:
                        import subprocess as _sp
                        _g = _sp.run(['zgrep', '-c', f'\\.[A-Za-z0-9_]\\+ *( *{named} *)', posteco],
                                     capture_output=True, text=True, timeout=60)
                        has_port_use = int((_g.stdout or '0').strip() or '0') > 0
                    except Exception:
                        pass
                    if has_port_use:
                        continue
                    changes[mod]['wire_decls'].append(named)
                    statuses.append({'name': inst, 'status': 'INFO',
                                     'reason': f'unconnected_rewires wire_decl re-added for {named} (ALREADY_APPLIED gate, dedup-passed)'})
                continue

            # Per-stage port connections
            pcs = dict(e.get('port_connections_per_stage', {}).get(args.stage)
                       or e.get('port_connections', {}))
            # Apply net_per_stage overrides (Gap A: P&R driver alias renames)
            for pin, stage_map in e.get('net_per_stage', {}).items():
                if args.stage in stage_map:
                    pcs[pin] = stage_map[args.stage]

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
                if str(net).startswith('UNRESOLVABLE_IN_') or str(net).startswith('UNRESOLVABLE:'):
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
                # Use IGNORECASE to handle both eco_9899_*_orig and ECO_9899_*_orig naming
                if re.match(r'^eco_\d+_\w+_orig$', str(net), re.IGNORECASE):
                    continue
                # Skip existence check for nets that are new ports (added by Pass 2),
                # output nets of other gates in this same Perl batch (forward reference),
                # explicitly flagged via input_from_new_port field in study JSON,
                # OR flagged via input_from_unconnected_rewire (the net is created by
                # passes 2-4 unconnected_rewires renaming UNCONNECTED→<flat-net>;
                # net doesn't exist in PreEco — chicken-and-egg with pre-existence check)
                if (net in new_port_nets
                    or net == e.get('input_from_new_port', '')
                    or net == e.get('input_from_unconnected_rewire', '')):
                    continue
                if not net_exists_in_posteco(net, posteco):
                    skip_reason = f"input net '{net}' absent in {args.stage}"
                    break

            if skip_reason:
                statuses.append({'name': inst, 'status':'SKIPPED', 'reason': skip_reason})
                continue

            # Dedup guard: skip if same instance already queued in this Perl batch (RISK 1.1)
            if inst in queued_instances:
                statuses.append({'name': inst, 'status':'ALREADY_APPLIED',
                                 'reason': f'{inst} already queued in this Perl batch — dedup guard'})
                continue
            queued_instances.add(inst)

            # wire_decls: output net only, NOT if already in PostEco or referenced by rewire (RISK 1.3)
            # Multi-layer defensive dedup against FM-599 'Duplicate wire declaration':
            #   1. rewire_new_nets — Pass 4 will create the wire implicitly via .PORT(net) hookup
            #   2. PreEco existing — net already exists in pre-applied netlist
            #   3. PostEco existing — net already exists in post-applied netlist (round 2+)
            #   4. Already queued in this batch
            # Run 20260511201004 root cause: dedup #1 didn't fire (reason TBD), wire decl
            # added on top of Pass 4 rewire's implicit wire → FM-599 ABORT. Layers
            # below catch the same condition through orthogonal evidence.
            out_net_raw = pcs.get(out_pin, '') if out_pin else ''
            # Bug fix: for bus-gate-bit entries (is_bus_gate_bit: true), the
            # output net is a bus-bit access like RowUpperMask[0]. When the bus
            # port is declared as 'input [7:0] RowUpperMask', bracket-indexing
            # IS valid Verilog — do NOT sanitize. Sanitize only for genuinely
            # new standalone wire declarations (not bus-port bit accesses).
            # For is_bus_gate_bit entries the verifier resolves per-stage output
            # ZN to flat form (e.g. RowUpperMask_0_ in PP/Route) but downstream
            # AND2 consumers use bracket form (RowUpperMask[0]) via _san_net fix.
            # These must be consistent — always use entry.output_net (bracket) for
            # the gate line so INV ZN and AND2 A2 both reference RowUpperMask[0].
            canonical_out = e.get('output_net', '')
            if e.get('is_bus_gate_bit') and canonical_out and '[' in canonical_out:
                out_net = canonical_out   # bracket form, consistent with AND2 A2
                _ow_san = False
            else:
                out_net, _ow_san = _sanitize_named_net(out_net_raw)
            if _ow_san and out_net_raw:
                statuses.append({'name': inst, 'status': 'AUTO_SANITIZED',
                                 'reason': f'output wire_decl "{out_net_raw}" used bus-bit form; '
                                           f'auto-converted to flat-net "{out_net}". '
                                           f'Studier should emit flat-net form directly.'})
            if e.get('needs_explicit_wire_decl') and out_net:
                # Layer 1: rewire-new-nets (Pass 4 will create implicit wire)
                if out_net in rewire_new_nets:
                    statuses.append({'name': inst, 'status':'INFO',
                                     'reason': f'wire_decl SKIPPED for {out_net}: referenced by Pass 4 rewire → implicit decl (SVR-9 prevention)'})
                else:
                    # Layer 2: existing reference in PostEco (any role)
                    existing_post = zgrep_count(out_net, posteco)
                    # Layer 3: existing reference in PreEco (race conditions where the
                    # entry might be ALREADY_APPLIED before this round but its rewire
                    # entry was deferred and now the dedup fires after-the-fact)
                    existing_pre  = zgrep_count(out_net, preeco)
                    # Layer 4: already queued in this batch (intra-batch dedup)
                    already_queued = out_net in changes[mod]['wire_decls']
                    # Layer 5: scan PostEco for `.PORT(<out_net>)` port connections.
                    # If found, the net already has implicit-wire creation and an
                    # explicit wire decl is a duplicate (SVR-9).
                    has_port_use_in_post = False
                    try:
                        import subprocess as _sp
                        _grep = _sp.run(['zgrep', '-c', f'\\.[A-Za-z0-9_]\\+ *( *{re.escape(out_net)} *)', posteco],
                                        capture_output=True, text=True, timeout=60)
                        has_port_use_in_post = int((_grep.stdout or '0').strip() or '0') > 0
                    except Exception:
                        pass
                    # Layer 6: intra-batch port_connection entries — when another
                    # entry in this same run uses out_net as a port_connection net,
                    # that will auto-create an implicit wire; explicit decl = SVR-9.
                    in_batch_port_conn = out_net in port_conn_nets
                    if existing_post == 0 and existing_pre == 0 and not already_queued and not has_port_use_in_post and not in_batch_port_conn:
                        changes[mod]['wire_decls'].append(out_net)
                    else:
                        why = []
                        if existing_post > 0: why.append(f'PostEco refs={existing_post}')
                        if existing_pre  > 0: why.append(f'PreEco refs={existing_pre}')
                        if already_queued:    why.append('already queued in batch')
                        if has_port_use_in_post: why.append('used as .PORT(net) in PostEco')
                        statuses.append({'name': inst, 'status':'INFO',
                                         'reason': f'wire_decl SKIPPED for {out_net}: ' + ', '.join(why) + ' — SVR-9 prevention'})

            # Build gate line — cell_type must not be empty (SVR-4: missing cell type = invalid Verilog)
            cell_type = e.get('cell_type','')
            if not cell_type:
                # Fallback: try other stage entries for the same instance_name
                for fb_stage in ['Synthesize','PrePlace','Route']:
                    if fb_stage == args.stage: continue
                    for fb_e in study.get(fb_stage,[]):
                        if fb_e.get('instance_name') == inst and fb_e.get('cell_type'):
                            cell_type = fb_e['cell_type']
                            break
                    if cell_type: break
            if not cell_type:
                statuses.append({'name': inst, 'status':'SKIPPED',
                                 'reason': f'cell_type empty for {inst} in {args.stage} — cannot insert without cell type (SVR-4 risk)'})
                continue
            # For gate pin net connections: prefer the bracket form when valid.
            # Bracket form X[N] is legal in .PIN(X[N]) pin connections (unlike
            # wire declarations). Only fall back to underscore form when the net
            # truly uses flat-net convention in the netlist.
            # Important: do NOT check PostEco existence for nets that will be
            # CREATED by this batch (e.g. RowUpperMask[N] from INV gate output,
            # or bus-port accesses after `input [W:0] X` is declared) — they
            # don't exist in PostEco yet but will be valid after insertion.
            def _san_net(n):
                if not isinstance(n, str):
                    return n
                if '[' not in n:
                    return n
                base = n.split('[')[0]
                # If base is an output_net prefix of any entry in this batch,
                # the bracket form will be valid after insertion — keep it.
                for _e in entries:
                    _on = (_e.get('output_net') or '')
                    if _on.startswith(base) or _on == base:
                        return n  # bracket form valid — new bus gate output
                # If base appears in a port_declaration (bus port being added),
                # bracket access will be valid after port is declared — keep it.
                for _e in entries:
                    if (_e.get('change_type') in ('port_declaration','port_promotion')
                            and _e.get('signal_name','') == base):
                        return n  # bracket form valid — bus port being declared
                # Check if bracket form exists in PostEco netlist already
                bracket_count = zgrep_count(n, posteco, timeout=30)
                if bracket_count > 0:
                    return n   # bracket form is the real net — use as-is
                # Bracket form absent and not being created — use underscore form
                flat, _ = _sanitize_named_net(n)
                return flat
            pins_str  = ', '.join(f'.{pin}({_san_net(net)})' for pin, net in pcs.items())
            gate_line = f'  // ECO {args.jira} TAG={args.tag} Round={args.round}'
            changes[mod]['gates'].append(gate_line)
            changes[mod]['gates'].append(f'  {cell_type} {inst} ( {pins_str} ) ;')
            statuses.append({'name': inst, 'status':'INSERTED',
                             'reason': f'Added to Perl spec for module {mod}'})

        # ── Synthesize unconnected_rewires from a1_unconnected_rename if present ─
        # New studier schema (eco_emit_dff_entry.py modei wrapper) sometimes
        # emits `a1_unconnected_rename` on gate entries instead of the
        # canonical `unconnected_rewires` list. Treat the embedded field as
        # an equivalent unconnected_rewires entry so the existing handler
        # below performs the bus rename. Without this, gate input nets stay
        # UNCONNECTED → undriven → FM fails.
        a1_ren = e.get('a1_unconnected_rename')
        if a1_ren and not e.get('unconnected_rewires'):
            synth_ur = {
                'named_net':                  a1_ren.get('named_net', ''),
                'original':                   (a1_ren.get('original_per_stage') or {}).get(args.stage, ''),
                'original_per_stage':         a1_ren.get('original_per_stage', {}),
                'port_bus_instance':          a1_ren.get('instance_name', ''),
                'port_bus_instance_per_stage': {s: a1_ren.get('instance_name','')
                                                for s in ('Synthesize','PrePlace','Route')},
                'port_bus_name':              a1_ren.get('port_name', ''),
                'port_bus_bit':               a1_ren.get('bus_bit_index'),
                'needs_explicit_wire_decl':   e.get('needs_explicit_wire_decl', True),
            }
            e.setdefault('unconnected_rewires', []).append(synth_ur)
            statuses.append({'name': e.get('instance_name','?'),
                             'status': 'INFO',
                             'reason': f'synthesized unconnected_rewires from a1_unconnected_rename '
                                       f'(bus={a1_ren.get("port_name","")}[{a1_ren.get("bus_bit_index")}] '
                                       f'→ {a1_ren.get("named_net","")}) — studier should emit a separate '
                                       f'port_connection entry per studier MD'})

        # ── unconnected_rewires — applies to ANY change type carrying this field ─
        # Gap B: rename UNCONNECTED_* → named wire + rewire port bus bit.
        # Processed once per entry regardless of change_type.
        for ur in e.get('unconnected_rewires', []):
            named_raw = ur.get('named_net', '')
            # Bug fix: also try 'original' field (studier may use 'original' not
            # 'original_unconnected') and fall back to entry's port_connections.I
            # value (the UNCONNECTED net the gate reads directly).
            orig = (ur.get('original_unconnected', '')
                    or ur.get('original', '')
                    or e.get('port_connections', {}).get('I', '')
                    or '')
            if not named_raw or not orig:
                continue
            # Auto-sanitize bus-bit form to flat-net form. Studier may emit
            # `REG_UmcCfgEco[1]` thinking "bit 1 of bus X"; that's illegal as
            # a wire decl. Convert to `REG_UmcCfgEco_1_` (the netlist's
            # standard underscore-escape). The same sanitized name is used for
            # BOTH the wire_decl AND the consumer rewrite below — keeps the
            # connection intact.
            named, was_sanitized = _sanitize_named_net(named_raw)
            if was_sanitized:
                statuses.append({
                    'name': named_raw, 'status': 'AUTO_SANITIZED',
                    'reason': f'named_net "{named_raw}" used bus-bit form (illegal '
                              f'in wire decl). Auto-converted to flat-net "{named}". '
                              f'Wire decl + consumer rewrite both use the sanitized '
                              f'form so connection stays intact. Studier should emit '
                              f'flat-net form directly to avoid this auto-fix.'
                })
            # Wire declaration with FULL 5-layer defensive dedup (same as
            # new_logic_gate path at line ~378-422). Run history shows this
            # path was the recurring source of wire-decl bugs:
            #   - run 20260511083831: duplicate UNCONNECTED_19090 → FM-599 SVR-9
            #   - run 20260511201004: implicit wire conflict on n_eco_9868_mux_sel
            #   - run 20260512070625: bracket-form wire decl REG_UmcCfgEco[1]
            # Earlier comment "ALWAYS declare the named wire explicitly" was
            # wrong — it bypassed dedup and produced duplicates. The correct
            # behavior: declare ONLY when not already present in any form.
            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}
            # Layer 1: rewire-new-nets (Pass 4 creates implicit wire via .PORT)
            if named in rewire_new_nets:
                statuses.append({'name': named, 'status': 'INFO',
                                 'reason': f'wire_decl SKIPPED for {named}: '
                                           f'referenced by Pass 4 rewire → implicit decl (SVR-9 prevention)'})
            else:
                # Layer 2: existing reference in PostEco (any role)
                existing_post = zgrep_count(named, posteco)
                # Layer 3: existing reference in PreEco
                existing_pre  = zgrep_count(named, preeco)
                # Layer 4: intra-batch dedup
                already_queued = named in changes[mod]['wire_decls']
                # Layer 5: PostEco `.PORT(<named>)` use → implicit wire already exists
                has_port_use_in_post = False
                try:
                    import subprocess as _sp
                    _grep = _sp.run(['zgrep', '-c', f'\\.[A-Za-z0-9_]\\+ *( *{named} *)', posteco],
                                    capture_output=True, text=True, timeout=60)
                    has_port_use_in_post = int((_grep.stdout or '0').strip() or '0') > 0
                except Exception:
                    pass
                if existing_post == 0 and existing_pre == 0 and not already_queued and not has_port_use_in_post:
                    changes[mod]['wire_decls'].append(named)
                else:
                    why = []
                    if existing_post > 0:    why.append(f'PostEco refs={existing_post}')
                    if existing_pre  > 0:    why.append(f'PreEco refs={existing_pre}')
                    if already_queued:       why.append('already queued in batch')
                    if has_port_use_in_post: why.append('used as .PORT(net) in PostEco → implicit wire exists')
                    statuses.append({'name': named, 'status': 'INFO',
                                     'reason': f'unconnected_rewires wire_decl SKIPPED for {named}: ' +
                                               ', '.join(why) + ' — SVR-9 prevention'})
            # Port bus bit replacement via Pass 4 rewire (word-boundary replace in bus { })
            # per_stage_bus_instance supports stage-specific renamed instances (e.g., REGCMD_0 in Route)
            # Use per-stage original (different UNCONNECTED_N names per stage)
            orig_this_stage = ur.get('original_per_stage', {}).get(args.stage, orig)
            per_stage_bi = ur.get('port_bus_instance_per_stage', {})
            bus_inst = per_stage_bi.get(args.stage, '') or ur.get('port_bus_instance', '')
            if bus_inst and orig_this_stage:
                if bus_inst not in rewires:
                    rewires[bus_inst] = []
                rewires[bus_inst].append({
                    'pin':                ur.get('port_bus_name', ''),
                    'old':                orig_this_stage,
                    'new':                named,             # ← sanitized form
                    'bus_element':        True,
                    'per_stage_cell_name': per_stage_bi,
                })
                statuses.append({'name': bus_inst, 'status':'QUEUED',
                                 'reason': f'bus_bit_replace {orig}→{named} on {bus_inst}.{ur.get("port_bus_name","")}'})
            else:
                # G4 — HARD ERROR instead of silent skip. Per-stage edit dispatch
                # for unconnected_rewires MUST resolve to a bus_inst + original
                # net for every stage. A missing per-stage value silently
                # produces a stage-divergent netlist (one stage rewired, the
                # others not) — FM sees cone divergence on apparently-unrelated
                # DFFs because cone walk reaches the now-connected bit only in
                # the rewired stage.
                missing = []
                if not bus_inst:        missing.append('port_bus_instance')
                if not orig_this_stage: missing.append('original_unconnected')
                statuses.append({
                    'name':   ur.get('port_bus_instance','?') + '.' + ur.get('port_bus_name',''),
                    'status': 'VERIFY_FAILED',
                    'reason': f'unconnected_rewires: missing per-stage value(s) '
                              f'{missing} for stage {args.stage} (named={named!r}, '
                              f'orig={orig!r}). Studier MUST emit '
                              f'original_per_stage and port_bus_instance_per_stage '
                              f'for ALL 3 stages — silent skip produces stage-'
                              f'divergent netlist that breaks FM. Step 4 validator '
                              f'Check 10 catches this; pre-FM check structurally '
                              f'verifies the rewire applied identically per stage.',
                })

        # ── undo_instance — remove previously-inserted gate from PostEco ────────
        # Used when eco_fm_analyzer replaces a gate strategy (e.g., MUX2→OA12).
        # Removes the named instance block AND its associated wire declarations
        # from the PostEco netlist. Must be paired with new gate insertion entries.
        if ct == 'undo_instance':
            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': [],
                                'undo_instances': []}
            if 'undo_instances' not in changes[mod]:
                changes[mod]['undo_instances'] = []
            # Remove the instance itself
            changes[mod]['undo_instances'].append(inst)
            # Also remove associated output wire declaration if specified
            output_net = e.get('output_net', '')
            if output_net and output_net not in changes[mod]['wire_removes']:
                changes[mod]['wire_removes'].append(output_net)
            statuses.append({'name': inst, 'status': 'UNDO_QUEUED',
                             'reason': f'undo_instance: remove {inst} from {mod} in {args.stage}'})

        # ── remove_wire_decl ─────────────────────────────────────────────────
        elif ct == 'remove_wire_decl':
            sig = e.get('signal_name','')
            if mod not in changes:
                changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}
            changes[mod]['wire_removes'].append(sig)
            statuses.append({'name': sig, 'status':'APPLIED',
                             'reason': f'remove_wire_decl added to Perl wire_removes'})

        # ── wire_declaration ─────────────────────────────────────────────────
        # Used by Mode-S bridge plumbing (parent_wire role) for the parent-scope
        # bridge wires (e.g. `eco<jira>_si_bridge` declared inside the parent
        # module that instantiates host + sibling). Same 5-layer dedup as the
        # unconnected_rewires wire-decl path to prevent SVR-9 duplicate.
        # `bridge_port_role`-tagged entries are auto-skipped in Synth (Synth
        # has no scan plumbing — see eco_netlist_port_rewire.py).
        elif ct == 'wire_declaration':
            sig = e.get('signal_name','') or e.get('net_name','')
            # Synth-skip for bridge plumbing per the auto-skip contract
            if e.get('bridge_port_role') and args.stage == 'Synthesize':
                statuses.append({'name': sig, 'status': 'SKIPPED',
                                 'reason': f"GAP-3: bridge_port_role={e.get('bridge_port_role')} "
                                           f"wire_decl skipped in Synth (no scan plumbing)"})
            elif sig and mod:
                if mod not in changes:
                    changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}
                # Same 5-layer dedup as unconnected_rewires wire_decl path
                if sig in rewire_new_nets:
                    statuses.append({'name': sig, 'status': 'INFO',
                                     'reason': f'wire_decl SKIPPED for {sig}: referenced by Pass 4 '
                                               f'rewire → implicit decl (SVR-9 prevention)'})
                else:
                    existing_post = zgrep_count(sig, posteco)
                    existing_pre  = zgrep_count(sig, preeco)
                    already_queued = sig in changes[mod]['wire_decls']
                    has_port_use_in_post = False
                    try:
                        import subprocess as _sp
                        _grep = _sp.run(['zgrep', '-c', f'\\.[A-Za-z0-9_]\\+ *( *{sig} *)', posteco],
                                        capture_output=True, text=True, timeout=60)
                        has_port_use_in_post = int((_grep.stdout or '0').strip() or '0') > 0
                    except Exception:
                        pass
                    if existing_post == 0 and existing_pre == 0 and not already_queued and not has_port_use_in_post:
                        changes[mod]['wire_decls'].append(sig)
                        statuses.append({'name': sig, 'status': 'QUEUED',
                                         'reason': f'wire_decl queued for Perl Pass 1 in {mod} '
                                                   f'(role={e.get("bridge_port_role","?")})'})
                    else:
                        why = []
                        if existing_post > 0:    why.append(f'PostEco refs={existing_post}')
                        if existing_pre  > 0:    why.append(f'PreEco refs={existing_pre}')
                        if already_queued:       why.append('already queued in batch')
                        if has_port_use_in_post: why.append('used as .PORT(net) in PostEco → implicit wire exists')
                        statuses.append({'name': sig, 'status': 'INFO',
                                         'reason': f'wire_decl SKIPPED for {sig}: ' +
                                                   ', '.join(why) + ' — SVR-9 prevention'})
            else:
                statuses.append({'name': sig, 'status': 'SKIPPED',
                                 'reason': f'wire_declaration missing signal_name or module_name'})

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

        # Types that another script handles intentionally — silently skip
        # the catch-all UNHANDLED entry. new_logic_gate / new_logic_dff are
        # already INSERTED/SKIPPED above; assign is handled by eco_netlist_port_rewire
        # Pass 5; rewire alone (without per_stage_cell_name) is handled by
        # eco_netlist_port_rewire Pass 4. Without this filter, every cell appears in
        # status as both INSERTED and UNHANDLED — pollutes the merged
        # applied JSON and trips Step 5 `no_unhandled` check.
        elif ct in ('new_logic_gate', 'new_logic_dff', 'new_logic',
                    'assign', 'rewire', 'remove_wire_decl', 'wire_declaration',
                    'port_declaration', 'port_promotion'):
            pass
        else:
            statuses.append({'name': inst, 'status':'UNHANDLED',
                             'reason': f'{ct} — not handled by eco_perl_spec'})

    # ── Bus vector wire declarations (bus DFFs + bus combinational gates) ────────
    # Each bus DFF/gate was expanded into N per-bit entries (is_bus_dff_bit /
    # is_bus_gate_bit=true).  Both share the same bus_dff_groups dict and the
    # Individual Q-pin connections create implicit bit-slice wires (e.g. sig[3])
    # but NOT an explicit bus declaration.  Consumers that reference the whole
    # bus need  wire [N-1:0] sig;  in the module body.  We emit it here as a
    # wire_decl entry — the Perl template writes  wire [N-1:0] sig ;  and the
    # already_declared dedup in the Perl prevents double-declaration on re-runs.
    for (mod, target_reg), bits in bus_dff_groups.items():
        if not bits:
            continue
        max_bit = max(bits)
        decl    = f'[{max_bit}:0] {target_reg}'   # → "wire [7:0] sig ;"
        if mod not in changes:
            changes[mod] = {'wire_decls': [], 'wire_removes': [], 'gates': []}
        # Dedup: skip if already queued or already exists in PostEco (round 2+)
        if decl in changes[mod]['wire_decls']:
            continue
        if zgrep_count(target_reg, posteco) > 0:
            statuses.append({'name': target_reg, 'status': 'INFO',
                             'reason': f'bus wire_decl SKIPPED for {decl}: signal already in PostEco'})
            continue
        changes[mod]['wire_decls'].append(decl)
        statuses.append({'name': target_reg, 'status': 'INFO',
                         'reason': f'bus wire_decl queued: wire {decl} ; in {mod} ({len(bits)} bit entries)'})

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
        undo_str = ', '.join(f"'{u}'" for u in spec.get('undo_instances', []))
        perl_lines.append(f"  '{mod}' => {{")
        perl_lines.append(f"    wire_decls    => [{wd_str}],")
        perl_lines.append(f"    wire_removes  => [{wrm_str}],")
        perl_lines.append(f"    undo_instances=> [{undo_str}],")
        perl_lines.append(f"    gates         => [")
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
        "            my %rm   = map { $_ => 1 } @{ $spec->{wire_removes} };",
        "            my %undo = map { $_ => 1 } @{ $spec->{undo_instances} };",
        "            my @filtered; my $undo_depth = 0; my $undo_inst = '';",
        "            for my $bl (@buf) {",
        "                # wire_removes: remove scalar wire declarations",
        "                if (!$undo_depth && %rm && $bl =~ /^\\s*wire\\s+(\\w+)\\s*;/ && $rm{$1})",
        "                    { print STDERR \"REMOVED wire $1\\n\"; next; }",
        "                # undo_instances: depth-track and skip entire instance block",
        "                if (!$undo_depth && %undo) {",
        "                    for my $ui (keys %undo) {",
        "                        if ($bl =~ /\\b\\Q$ui\\E\\b/) { $undo_inst=$ui; $undo_depth=0; last; }",
        "                    }",
        "                }",
        "                if ($undo_inst) {",
        "                    $undo_depth += ($bl =~ tr/(//) - ($bl =~ tr/\\)//) ;",
        "                    if ($undo_depth <= 0 && $bl =~ /\\)\\s*;/) {",
        "                        print STDERR \"UNDO_REMOVED $undo_inst\\n\";",
        "                        $undo_inst=''; $undo_depth=0; next;",
        "                    }",
        "                    next;",
        "                }",
        "                push @filtered, $bl;",
        "            }",
        "            # Add wire decls — skip if net already declared in module body",
        "            # (any decl form: bare, bus, multi-name, bit-indexed). Robust",
        "            # against FM-599 'Duplicate wire' ABORT — see run 20260511083831.",
        "            my $buf_text = join('', @filtered);",
        "            my %already_declared = ();",
        "            while ($buf_text =~ /^\\s*(?:wire|tri|wand|wor|reg)\\s+(?:\\[[^\\]]+\\]\\s+)?([^;]+);/mg) {",
        "                for my $n (split /,/, $1) {",
        "                    $n =~ s/^\\s+|\\s+$//g;",
        "                    my $base = $n; $base =~ s/\\[[^\\]]+\\]//g;",
        "                    $already_declared{$n} = 1; $already_declared{$base} = 1;",
        "                }",
        "            }",
        "            for my $net (@{ $spec->{wire_decls} }) {",
        "                my $base = $net; $base =~ s/\\[[^\\]]+\\]//g;",
        "                if ($already_declared{$net} || $already_declared{$base})",
        "                    { print STDERR \"DUP_WIRE_PREVENT: SKIP wire $net (already declared)\\n\"; }",
        "                else { push @filtered, \"  wire $net ;\\n\"; $already_declared{$net} = 1; $already_declared{$base} = 1; }",
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

    # ── Execute the Perl pipe against PostEco when --apply is set ──────────
    # Closes the historical execution gap: without this, generating the .pl
    # script and reporting INSERTED was insufficient — cells were never
    # actually written to the netlist. Step 5 caught it via cells_in_netlist
    # / semantic_verify, but only after wasting the rest of Step 4.
    if args.apply and changes:
        try:
            tmp_out = posteco + '.new'
            cmd = f"zcat {posteco} | perl {args.output} 2>/tmp/eco_perl_apply_{args.tag}_{args.stage}.err | gzip > {tmp_out}"
            r = subprocess.run(cmd, shell=True, timeout=600)
            if r.returncode == 0 and os.path.getsize(tmp_out) > 0:
                os.replace(tmp_out, posteco)
                applied_note = 'PERL_APPLIED'
            else:
                applied_note = f'PERL_APPLY_FAILED rc={r.returncode}'
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
        except Exception as e:
            applied_note = f'PERL_APPLY_EXC {e}'
        statuses.append({'name': '__perl_apply', 'status': applied_note,
                         'reason': f'Perl pipe execution against {posteco}'})

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
