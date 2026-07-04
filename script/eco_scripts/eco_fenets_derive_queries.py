#!/usr/bin/env python3
"""
eco_fenets_derive_queries.py — Deterministic Step 2 query derivation.

Walks the rtl_diff and emits the COMPLETE set of nets to query through FM
find_equivalent_nets. Replaces hand-picked agent reasoning that historically
silently dropped chain leaves (e.g. 9868: agent queried 4 leaves out of ~10,
dropped IReset → studier later used the wrong per-stage net → FM PP/Route
DFF0X failures).

Categories — same as eco_fenets_runner.md STEP A but enforced as code:

  Cat 1: wire_swap / and_term — both old_token and new_token.
  Cat 2: new_logic_dff dff_clock.
  Cat 3: new_logic_dff reset_signal.
  Cat 4: every chain leaf input that is NOT a `n_eco_*` intermediate or constant.
  Cat 5: port_promotion signal name.
  Cat 6: Mode I UNCONNECTED rename targets (submodule_instance/port_name[bit]).
  Cat 7: explicit hookup hints from rtl_diff (when present).
  Cat 8: Mode-S anchor pins — for any new_logic_dff that is a potential Mode-S
         target, query the SI/SE/Q paths of an anchor DFF in the chosen sibling
         module. Lets the studier pick a stage-stable bridge source/consumer
         using FM equivalence data instead of guessing.
         Trigger: rtl_diff change with `potential_mode_s_targets` list, OR
         any new_logic_dff with requires_scan_stitching=true that names a
         `mode_s_anchor` field.

Output JSON: a list of {net_path, signal, source, ...} entries, deduplicated
by net_path. The fenets agent receives this list as input and may ADD entries
but must NEVER silently DROP any.

Usage:
    python3 eco_fenets_derive_queries.py \\
        --rtl-diff data/<TAG>_eco_rtl_diff.json \\
        --output   data/<TAG>_eco_fenets_queries_raw.json
"""
import argparse, json, re, sys
from pathlib import Path


_SKIP_INPUT_PREFIXES = ("n_eco_", "eco_", "1'b", "0'b", "1'h", "0'h")


def _scope_of(c):
    return c.get('scope') or c.get('instance_scope') or ''


def _abs_path(tile, scope, signal):
    """Build the tile-RELATIVE net path FM expects.

    FM session is rooted at `r:/FMWORK_REF_<TILE_T>/ddrss_<tile>_t/` — i.e.
    one level INSIDE the tile module. If we prepend `<tile>/` the path
    resolves as `r:/.../ddrss_<tile>_t/<tile>/<rest>` → duplicate `umccmd/umccmd/`
    → FM-036 on every query.

    Rules:
      1. NEVER prepend `<tile>/` (FM is already at tile depth).
      2. If `scope` itself starts with `<tile>/` or equals `<tile>`, strip it.
      3. Emit `<scope>/<signal>` (tile-relative). Top-scope DFFs (host module
         is the tile itself, scope=tile) just emit `<signal>`.
    """
    parts = []
    if scope:
        if tile and (scope == tile or scope.startswith(tile + '/')):
            scope = scope[len(tile):].lstrip('/')
        if scope:
            parts.append(scope)
    parts.append(signal)
    return '/'.join(p.strip('/') for p in parts if p)


def derive(rtl_diff, tile=''):
    out = []
    # Signals introduced by this ECO (new ports/wires) do not exist in PreEco and
    # are not queryable via find_equivalent_nets — used to skip them below.
    eco_new_signals = set()
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') in ('new_port', 'port_connection', 'port_promotion'):
            for f in ('new_token', 'signal_name'):
                v = c.get(f)
                if isinstance(v, str) and v:
                    eco_new_signals.add(v)
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        ct = c.get('change_type', '')
        scope = _scope_of(c)

        # Cat 1: wire_swap / and_term tokens + the rewired target_register
        if ct in ('wire_swap', 'and_term'):
            # When instances[] lists multiple instance names (e.g. DCQARB + DCQARB1),
            # generate one query per instance so FM resolves the signal in each
            # module separately. Without this, only the primary scope is queried
            # and the second instance's gate-level net name is never resolved.
            instances = c.get('instances') or []
            for tok_field in ('old_token', 'new_token', 'target_register'):
                t = c.get(tok_field)
                if not t:
                    continue
                s = c.get('target_scope') if tok_field == 'target_register' else scope
                base_scope = s or scope
                # Build list of scopes to query: primary scope + any extra instances
                scopes_to_query = [base_scope]
                if instances and len(instances) > 1 and tok_field != 'target_register':
                    # Add sibling instance scopes by replacing the last path component
                    parent = '/'.join(base_scope.split('/')[:-1]) if '/' in base_scope else ''
                    for inst in instances[1:]:
                        sibling_scope = f"{parent}/{inst}" if parent else inst
                        scopes_to_query.append(sibling_scope)
                for sc in scopes_to_query:
                    out.append({
                        'net_path': _abs_path(tile, sc, t),
                        'signal':   t,
                        'category': 1,
                        'source':   f'changes[{idx}].{tok_field}',
                    })

            # Cat 1b: and_term gate-chain condition inputs (e.g. the OR's other input).
            # Previously only old_token/new_token were queried, dropping additional
            # condition inputs (e.g. UclkMult2PhaseEn) -> their per-stage rename was
            # never resolved (latent Mode-H miss when CTS-renamed in P&R).
            if ct == 'and_term':
                cond_inputs = []
                for g in (c.get('and_term_gate_chain_design') or []):
                    for inp in (g.get('inputs') or []):
                        if isinstance(inp, str):
                            cond_inputs.append(inp)
                add_in = c.get('and_term_additional_input')
                if isinstance(add_in, str) and add_in:
                    cond_inputs.append(add_in)
                seen_ci = set()
                for inp in cond_inputs:
                    base = inp.split('[')[0].strip()
                    # only plain net identifiers are queryable — skip placeholders
                    # (PENDING_FM_RESOLUTION:*), negations (~X), constants, etc.
                    # ALSO skip SYNTHETIC/derived tokens the flow will BUILD as gates
                    # (equality decodes <sig>_eq_<CONST>, renamed drivers *_orig,
                    # computed *_inv nets) — these are not existing wires and must NOT
                    # be sent to find_equivalent_nets (they echo-fall-back → Step 2 C6).
                    if (not base or base in seen_ci
                            or not re.match(r'^[A-Za-z_]\w*$', base)
                            or base.startswith(_SKIP_INPUT_PREFIXES)
                            or re.search(r'_eq_[A-Z0-9_]+$|_orig$|_inv\d*$', base)
                            or base == c.get('old_token') or base == c.get('new_token')
                            or base in eco_new_signals):
                        continue
                    seen_ci.add(base)
                    out.append({
                        'net_path': _abs_path(tile, scope, base),
                        'signal':   base,
                        'category': 1,
                        'source':   f'changes[{idx}].and_term_cond_input',
                    })

        # Cat 2 + 3 + 4: new_logic_dff context
        if ct in ('new_logic', 'new_logic_dff'):
            if c.get('dff_clock'):
                out.append({
                    'net_path': _abs_path(tile, scope, c['dff_clock']),
                    'signal':   c['dff_clock'],
                    'category': 2,
                    'source':   f'changes[{idx}].dff_clock',
                })
            if c.get('reset_signal'):
                out.append({
                    'net_path': _abs_path(tile, scope, c['reset_signal']),
                    'signal':   c['reset_signal'],
                    'category': 3,
                    'source':   f'changes[{idx}].reset_signal',
                })
            for g in (c.get('d_input_gate_chain') or []):
                for inp in (g.get('inputs') or []):
                    if not isinstance(inp, str):
                        continue
                    base = inp.split('[')[0]
                    if base.startswith(_SKIP_INPUT_PREFIXES):
                        continue
                    if not base:
                        continue
                    out.append({
                        'net_path': _abs_path(tile, scope, base),
                        'signal':   base,
                        'category': 4,
                        'source':   f'changes[{idx}].chain[{g.get("seq", "?")}]',
                    })

        # Cat 5: port_promotion — one query per instance when instances[] present
        if ct == 'port_promotion':
            s = c.get('signal_name') or c.get('new_token')
            if s:
                pp_instances = c.get('instances') or []
                pp_scopes = [scope]
                if len(pp_instances) > 1:
                    parent = '/'.join(scope.split('/')[:-1]) if '/' in scope else ''
                    for inst in pp_instances[1:]:
                        pp_scopes.append(f"{parent}/{inst}" if parent else inst)
                for sc in pp_scopes:
                    out.append({
                        'net_path': _abs_path(tile, sc, s),
                        'signal':   s,
                        'category': 5,
                        'source':   f'changes[{idx}].port_promotion',
                    })

        # Cat 6: Mode I — UNCONNECTED rename target
        unc = c.get('original_unconnected_net') or c.get('d_input_net') or ''
        if unc.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_')):
            sm = c.get('submodule_instance') or c.get('instance_name', '')
            port = c.get('port_name', '')
            bbi = c.get('bus_bit_index')
            if sm and port and bbi is not None:
                out.append({
                    'net_path':         _abs_path(tile, scope, f'{sm}/{port}[{bbi}]'),
                    'signal':           f'{port}[{bbi}]',
                    'category':         6,
                    'mode_I_candidate': True,
                    'source':           f'changes[{idx}].mode_I_candidate',
                })

        # Cat 7: explicit hookup hints (when rtl_diff_analyzer emits them)
        for h in (c.get('hookup_hints') or []):
            np = h.get('net_path')
            if np:
                out.append({
                    'net_path': np,
                    'signal':   h.get('signal') or np.rsplit('/', 1)[-1],
                    'category': 7,
                    'source':   f'changes[{idx}].hookup_hint',
                })

        # Cat 8: Mode-S anchor WIRES (NOT pin paths).
        # FM `find_equivalent_nets` accepts wires/output-pin nets — querying
        # input pin paths like <DFF>/SI returns FM-036 (Unknown name). The
        # picker (eco_pick_sibling.py) resolves the anchor DFF's actual wire
        # names from the netlist and emits them on the mode_s_anchor as
        # anchor_si_wire / anchor_se_wire / anchor_q_wire — query those.
        anchor = c.get('mode_s_anchor') or {}
        sib   = anchor.get('sibling_module', '')
        adff  = anchor.get('anchor_dff', '')
        if sib and adff:
            # Path priority (instance-name aware):
            #   1. fm_scope (computed by eco_pick_sibling.py — instance hierarchy
            #      from tile-internal root to sibling, e.g. "ARB/DCQARB"). FM
            #      resolves only via INSTANCE names; module-type fall-backs
            #      always return FM-036 and silently break Cat 8 queries.
            #   2. anchor_scope (legacy hand-written field).
            #   3. sibling_module (module TYPE — last-resort fallback; will
            #      almost certainly FM-036 → Step 2 C7 catches it).
            anchor_scope = (anchor.get('fm_scope')
                            or anchor.get('anchor_scope')
                            or sib)
            for role, wire_field in (('SI', 'anchor_si_wire'),
                                     ('SE', 'anchor_se_wire'),
                                     ('Q',  'anchor_q_wire')):
                wire = anchor.get(wire_field)
                if not wire:
                    # Skip when picker didn't resolve this wire (e.g. DFF has
                    # no .SI hookup or wire is a constant). Better to skip than
                    # to emit a guess that returns FM-036.
                    continue
                # Skip constants
                if str(wire).startswith(("1'b", "0'b", "1'h", "0'h")):
                    continue
                out.append({
                    'net_path':       _abs_path(tile, anchor_scope, wire),
                    'signal':         wire,
                    'category':       8,
                    'mode_s_anchor':  True,
                    'anchor_pin':     role,        # SI/SE/Q role label (the pin this wire connects to)
                    'anchor_dff':     adff,
                    'anchor_wire':    wire,
                    'sibling_module': sib,
                    'source':         f'changes[{idx}].mode_s_anchor.{wire_field}',
                })

        # Cat 9: condition_inputs_to_query — signals in condition gate chains that
        # the rtl_diff_analyzer couldn't resolve to gate-level names (marked as
        # PENDING_FM_RESOLUTION). FM find_equivalent_nets resolves them per stage.
        # Without Cat 9, the studier uses wrong fallback signals (GAP-2 in 9899).
        for ci in (c.get('condition_inputs_to_query') or []):
            sig   = ci.get('signal', '')
            cscope = ci.get('scope', '') or scope
            if not sig or sig.startswith(_SKIP_INPUT_PREFIXES):
                continue
            out.append({
                'net_path':                  _abs_path(tile, cscope, sig),
                'signal':                    sig,
                'category':                  9,
                'condition_input_resolution': True,
                'source':                    f'changes[{idx}].condition_inputs_to_query',
            })

        # Cat 10: enable_swap — query old_enable_net so the studier can locate
        # the CE/WE/EN pin on the existing DFF cell to rewire, and query chain
        # leaf inputs from new_enable_gate_chain for per-stage rename resolution.
        if ct == 'enable_swap':
            oen = c.get('old_enable_net')
            if oen:
                out.append({
                    'net_path': _abs_path(tile, scope, oen),
                    'signal':   oen,
                    'category': 10,
                    'source':   f'changes[{idx}].old_enable_net',
                })
            for g in (c.get('new_enable_gate_chain') or []):
                for inp in (g.get('inputs') or []):
                    if not isinstance(inp, str):
                        continue
                    base = inp.split('[')[0]
                    if base.startswith(_SKIP_INPUT_PREFIXES) or not base:
                        continue
                    out.append({
                        'net_path': _abs_path(tile, scope, base),
                        'signal':   base,
                        'category': 10,
                        'source':   f'changes[{idx}].enable_chain[{g.get("seq","?")}]',
                    })

    # Deduplicate by net_path (preserve first source)
    seen, unique = set(), []
    for q in out:
        if q['net_path'] in seen:
            continue
        seen.add(q['net_path'])
        unique.append(q)
    return unique


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--rtl-diff', required=True)
    p.add_argument('--tile',     default='',
                   help='Tile name (e.g. umccmd) — prepended to net_path when '
                        'rtl_diff scope is relative. Without this, FM queries '
                        'will miss the tile-root level.')
    p.add_argument('--output',   required=True)
    args = p.parse_args()

    try:
        rtl = json.loads(Path(args.rtl_diff).read_text())
    except Exception as e:
        print(f'FAIL: cannot read rtl_diff: {e}', file=sys.stderr)
        return 1

    queries = derive(rtl, args.tile)
    Path(args.output).write_text(json.dumps(queries, indent=2))

    by_cat = {}
    for q in queries:
        c = q.get('category', '?')
        by_cat[c] = by_cat.get(c, 0) + 1
    print(f'ECO_RPT_GENERATED: queries → {args.output}')
    print(f'  total:    {len(queries)}')
    print(f'  per_cat:  {dict(sorted(by_cat.items()))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
