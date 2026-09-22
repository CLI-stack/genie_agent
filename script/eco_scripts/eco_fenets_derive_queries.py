#!/usr/bin/env python3
"""
eco_fenets_derive_queries.py — Deterministic Step 2 query derivation.

Walks the rtl_diff and emits the COMPLETE set of nets to query through FM
find_equivalent_nets. Replaces hand-picked agent reasoning that historically
silently dropped chain leaves (e.g. 9868: agent queried 4 leaves out of ~10,
dropped IReset → studier later used the wrong per-stage net → FM PP/Route
DFF0X failures).

Categories — same as eco_fenets_runner.md STEP A but enforced as code:

  Cat 0: passthrough of the analyzer-provided top-level nets_to_query — already
         fully-qualified per-branch cone leaves. Essential for a comb_net_force
         in a MULTIPLY-INSTANTIATED module (no single scope; the per-change cone
         walk in Cat 4c cannot qualify it, so Step 1 pre-resolves it here).
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
try:
    from eco_rtl_config import RtlConfig
    from eco_extract_pf_condition import extract_added_branch_condition, resolve_rtl
    from eco_emit_priority_force import (synthesize_condition, _PErr, _OUT_PINS_DRV,
                                         _module_netlist_body)
    from eco_cone_rebuild import (cone_leaves, selector_folded_conditions,
                                  reg_guard_folded_conditions, reg_guard_cone_leaves)
    from eco_resolve_bus_width import resolve_from_netlist as _resolve_bus_width
except Exception:
    RtlConfig = extract_added_branch_condition = resolve_rtl = None
    synthesize_condition = _module_netlist_body = cone_leaves = selector_folded_conditions = None
    reg_guard_folded_conditions = reg_guard_cone_leaves = None
    _PErr = Exception
    _OUT_PINS_DRV = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO')
    _resolve_bus_width = None


def _pf_cone_leaves(change, ref_dir):
    """Real-net leaves of a priority_force condition cone — decomposed exactly as the
    Step-3 emitter will build it (diff-aware added-branch condition -> synthesize).
    These are the nets fenets must resolve per-stage so the cone applies in P&R.
    Returns [] when the helpers/ref_dir are unavailable or the condition is unbuildable."""
    if not (ref_dir and RtlConfig and synthesize_condition and extract_added_branch_condition):
        return []
    mod = change.get('module_name') or ''
    base = re.sub(r'^\w+?_t_', '', mod)
    anchor = next((f for f in (change.get('forced_signals') or []) if f.get('const_macro')), None)
    cond_expr = None
    if anchor:
        added = extract_added_branch_condition(ref_dir, base, anchor['signal'], anchor['const_macro'])
        if len(added) == 1:
            cond_expr = added[0].get('condition_expr')
    if not cond_expr:
        cond_expr = change.get('condition_expr')
    if not cond_expr:
        return []
    cfg = RtlConfig(ref_dir)
    rtl = resolve_rtl(ref_dir=ref_dir, module=base) if resolve_rtl else None
    rtl_text = open(rtl, errors='replace').read() if rtl else None
    # REAL netlist predicate so local always@* regs (e.g. WckIsInSync) decompose to
    # their actual netlist leaves (WckSyncCtr*) — exactly as the Step-3 emitter does.
    nlbody = _module_netlist_body(ref_dir, mod) if _module_netlist_body else ''
    innl = (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody))) if nlbody else None
    seq = [0]
    def _mk(t):
        seq[0] += 1
        return f'_q_{t}_{seq[0]}'
    try:
        _c, cone = synthesize_condition(cond_expr, 'q', mod, cfg, _mk,
                                        rtl_text=rtl_text, in_netlist=innl)
    except _PErr:
        return []
    outs = {g['output_net'] for g in cone}
    leaves = set()
    for g in cone:
        for p, v in (g.get('port_connections') or {}).items():
            if p in _OUT_PINS_DRV or not isinstance(v, str):
                continue
            if v in outs or v.startswith('_q_') or re.match(r"^\d*'[bhdo]", v):
                continue
            leaves.add(v)
    return sorted(leaves)


_SKIP_INPUT_PREFIXES = ("n_eco_", "eco_", "1'b", "0'b", "1'h", "0'h")

# Cat 12: schema-independent equality-operand extraction straight from `context_line`
# (the raw Verilog source text the analyzer already captured for reporting). The
# structured `d_input_gate_chain` field the analyzer emits for a bus-equality compare
# has proven UNSTABLE across otherwise-identical re-runs of the SAME ECO — three
# different shapes seen on a single real ECO alone (`inputs`+"EQ_CMP_BUS", `operand_a`/
# `operand_b`+"COMPARE_EQ", `inputs`+"BUS_EQ"+`bus_width_expr`), plus a fourth
# variant at the Cat 0 top-level (`is_bus_variant` flag). Chasing each new shape as
# it appears is a losing game. `context_line` itself is just the literal RTL text —
# its FORMAT never varies, because it isn't the analyzer's own JSON, it's a straight
# copy of the source. Regex-extracting `==`/`!=` operands from it directly makes Cat
# 4 (and Cat 0's bus expansion) redundant-but-harmless belt-and-suspenders rather
# than the only path — this one doesn't care what the analyzer decided to call the
# gate or how it structured the leaf list.
_EQ_OPERAND_RE = re.compile(
    r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:==|!=)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?"
)
_VERILOG_KEYWORDS = {
    'always', 'begin', 'end', 'if', 'else', 'posedge', 'negedge', 'assign',
    'wire', 'reg', 'input', 'output', 'inout',
}


def compute_eco_new_signals(rtl_diff):
    """Signals introduced by this ECO (new ports/wires) — do not exist in PreEco,
    not queryable via find_equivalent_nets. Exposed at module level (not just
    inlined in `derive()`) so other scripts — e.g. `eco_fenets_chain.py`, which
    needs the same exclusion set for its own context_line-based leaf extraction —
    can reuse it instead of recomputing it differently."""
    eco_new_signals = set()
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') in ('new_port', 'port_connection', 'port_promotion'):
            for f in ('new_token', 'signal_name'):
                v = c.get(f)
                if isinstance(v, str) and v:
                    eco_new_signals.add(v)
    return eco_new_signals


def _context_line_eq_operands(context_line, new_token, eco_new_signals):
    """Every bare identifier appearing on either side of a `==`/`!=` in this
    change's raw RTL context line — independent of any analyzer-provided
    structured field. Filters keywords, the change's own new signal, other
    ECO-introduced signals, numeric/bit-literal tokens, and n_eco_*/eco_*
    intermediates."""
    if not isinstance(context_line, str) or not context_line:
        return []
    found = []
    for m in _EQ_OPERAND_RE.finditer(context_line):
        for tok in (m.group(1), m.group(2)):
            if not tok or tok in _VERILOG_KEYWORDS:
                continue
            if tok == new_token or tok in eco_new_signals:
                continue
            if tok.startswith(_SKIP_INPUT_PREFIXES):
                continue
            if re.match(r"^\d", tok):
                continue
            found.append(tok)
    # de-dup, preserve order
    seen = set()
    out = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


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


def derive(rtl_diff, tile='', ref_dir=None):
    out = []
    # Memoize per (base_signal) — the same operand (e.g. a bus signal name) is now checked
    # from up to three independent places (Cat 0's is_bus_variant expansion, Cat 4's
    # chain walk, Cat 12's context_line extraction). `_resolve_bus_width`'s netlist
    # fallback shells out a FRESH `zcat <30-90MB gz> | grep` per call — with dozens
    # of distinct operands that took 35-110s just deriving queries. Decompress the
    # PreEco Synthesize netlist ONCE here and answer every width lookup from the
    # in-process text instead (same "count distinct signal[N] indices" logic
    # `eco_resolve_bus_width.resolve_from_netlist` uses, just without re-shelling).
    _width_cache = {}
    _netlist_text_cache = [None]   # lazy one-shot decompress, list as a mutable cell
    _BIT_IDX_RE_CACHE = {}

    def _cached_bus_width(base):
        if base in _width_cache:
            return _width_cache[base]
        w = None
        if ref_dir:
            if _netlist_text_cache[0] is None:
                preeco_synth = Path(ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
                try:
                    import gzip
                    with gzip.open(preeco_synth, 'rt', errors='replace') as f:
                        _netlist_text_cache[0] = f.read()
                except Exception:
                    _netlist_text_cache[0] = ''
            text = _netlist_text_cache[0]
            if text:
                pat = _BIT_IDX_RE_CACHE.setdefault(
                    base, re.compile(re.escape(base) + r'\[(\d+)\]'))
                bits = {int(m) for m in pat.findall(text)}
                if bits:
                    w = len(bits)
        _width_cache[base] = w
        return w

    # Signals introduced by this ECO (new ports/wires) do not exist in PreEco and
    # are not queryable via find_equivalent_nets — used to skip them below.
    eco_new_signals = compute_eco_new_signals(rtl_diff)

    # Cat 0 (passthrough): consume the analyzer-provided top-level nets_to_query.
    # Step 1 (rtl_diff_analyzer) pre-resolves fully-qualified, tile-relative cone
    # leaves here for cases the per-change cone walk cannot scope on its own — most
    # importantly a comb_net_force whose declaring module is instantiated MORE THAN
    # ONCE (no single `scope`; the module lives under several different parents, so
    # each cone leaf needs its own per-instance hierarchy prefix). Those net_paths
    # already carry the correct per-branch hierarchy, so pass them straight through;
    # the end-of-function
    # dedup absorbs any overlap with the Cat 1..4 derivations below. Harmless for a
    # single-instance run (adds its and_term driver queries, which are legit).
    for q in (rtl_diff.get('nets_to_query') or []):
        np = (q.get('net_path') or '').strip()
        if not np:
            continue
        leaf = np.split('/')[-1]
        if re.sub(r'\[.*$', '', leaf) in eco_new_signals:
            continue
        # This is the THIRD distinct schema variant seen for the same underlying
        # bug class (confirmed live on a real run): the analyzer marks a bus
        # operand with `is_bus_variant: true` right here at the Cat 0 top-level
        # passthrough, instead of via a d_input_gate_chain entry (Cat 4's
        # concern). A bare bus net_path submitted as-is to FM find_equivalent_nets
        # returns FM-036 "Unknown name" (buses only resolve bit-by-bit) — expand
        # it here too, using whichever hierarchy prefix the analyzer already
        # resolved for this net_path (do not re-derive scope).
        if q.get('is_bus_variant') and ref_dir and _resolve_bus_width:
            base = leaf.split('[')[0]
            hier_prefix = np.rsplit('/', 1)[0] if '/' in np else ''
            width = _cached_bus_width(base)
            if width and width > 1:
                for b in range(width):
                    bit_np = f'{hier_prefix}/{base}[{b}]' if hier_prefix else f'{base}[{b}]'
                    out.append({
                        'net_path': bit_np,
                        'signal':   f'{base}[{b}]',
                        'category': 0,
                        'source':   (q.get('source') or 'rtl_diff.nets_to_query') + f'.bit{b}',
                    })
                continue
        out.append({
            'net_path': np,
            'signal':   leaf,
            'category': 0,
            'source':   q.get('source') or 'rtl_diff.nets_to_query',
        })

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
                # Do NOT query a to-be-BUILT new term: an equality-decode match
                # (eco_emit_eq_decode builds it) or a synthetic ECO net (bus OR/AND
                # reduce, n_eco_/eco_). It cannot exist in PreEco, so FM echo-falls-back
                # and Step-2 C6 would flag it as a false positive. old_token (the combine
                # point) is still queried, so the studier loses nothing.
                if tok_field == 'new_token' and (
                        c.get('equality_decode')
                        or t.startswith(_SKIP_INPUT_PREFIXES)
                        or re.search(r'_eq_[A-Z0-9_]+$|_or$|_or_reduce$', t)):
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

            # Cat 1c: equality_decode comparator leaves. eco_emit_eq_decode builds a
            # `signal == CONST` comparator from signal[0..width-1]; that signal is often
            # COMBINATIONAL, so P&R renames its bits (sig[b]->sig_b_ / MB-banked) and the
            # comparator needs per-stage names. Derive each bit as a query so the rename
            # map carries authoritative per-stage names (else PP/Route -> NET-ABSENT).
            ed = c.get('equality_decode')
            if isinstance(ed, dict) and ed.get('signal') and ed.get('width'):
                esig = ed['signal']
                try:
                    ew = int(ed['width'])
                except (TypeError, ValueError):
                    ew = 0
                for b in range(ew):
                    bit = f'{esig}[{b}]'
                    out.append({
                        'net_path': _abs_path(tile, scope, bit),
                        'signal':   bit,
                        'category': 1,
                        'source':   f'changes[{idx}].equality_decode_leaf',
                    })

        # Cat 2 + 3: new_logic_dff-only context (clock/reset pins don't apply to new_logic_gate)
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

        # Cat 4: every chain leaf input that is NOT a `n_eco_*` intermediate or constant.
        # Runs for ALL three new-logic change types, including plain `new_logic_gate` —
        # NOT just new_logic_dff. A `new_logic_gate` entry with a `d_input_gate_chain`
        # (e.g. an EQ_CMP_BUS two-signal bit-equality tree, built by the studier when no
        # deterministic emitter owns it — `eco_emit_eq_decode.py` only handles
        # signal==CONSTANT compares, `eco_cone_rebuild.py` only handles
        # comb_net_force/reg_guard_delta) has leaf operands (e.g. a bare primary-input
        # port) that are JUST as much at risk of a hidden cross-module
        # polarity flip as any new_logic_dff leaf — skipping them here silently drops
        # fenets' only chance to catch it structurally.
        if ct in ('new_logic', 'new_logic_dff', 'new_logic_gate'):
            for g in (c.get('d_input_gate_chain') or []):
                # EQ_CMP_BUS / COMPARE_EQ operands are multi-bit buses being compared
                # bit-for-bit (e.g. `signal_a == signal_b`). FM `find_equivalent_nets`
                # only resolves individual bit-level points, not whole bus names —
                # querying the bare bus name (as the plain per-input walk below would)
                # returns FM-036 "Unknown name" for EVERY such signal (confirmed via
                # manual round-1 query on a real ECO: every Bus-Var=YES signal FM-036'd;
                # only per-bit round-2 queries resolved). Expand to one query per bit
                # here instead.
                #
                # SCHEMA TOLERANCE (this is agent-authored analyzer output, not a fixed
                # format — confirmed to vary run-to-run on the SAME ECO, three ways so
                # far: `inputs: [...]` + gate_function "EQ_CMP_BUS"; scalar
                # `operand_a`/`operand_b` + gate_function "COMPARE_EQ" + explicit
                # `width`; `inputs: [...]` + gate_function "BUS_EQ" + `bus_width_expr`
                # instead of `width`). Matching on gate_function name is a losing game —
                # collect leaves from EVERY shape (inputs list AND operand_a/operand_b),
                # and decide "is this a bus" from the ACTUAL NETLIST via
                # `_resolve_bus_width`, not from whatever the analyzer happened to name
                # the gate this run. Prefer an explicit integer `width` field when
                # present (cheaper, no netlist read), but always fall back to the
                # netlist-driven resolver — it doesn't care what the gate is called.
                leaves = list(g.get('inputs') or [])
                for k in ('operand_a', 'operand_b'):
                    v = g.get(k)
                    if isinstance(v, str):
                        leaves.append(v)
                explicit_width = g.get('width') if isinstance(g.get('width'), int) else None
                for inp in leaves:
                    if not isinstance(inp, str):
                        continue
                    base = inp.split('[')[0]
                    if base.startswith(_SKIP_INPUT_PREFIXES):
                        continue
                    if not base:
                        continue
                    width = explicit_width
                    if width is None:
                        width = _cached_bus_width(base)
                    if width and width > 1:
                        for b in range(width):
                            bit_sig = f'{base}[{b}]'
                            out.append({
                                'net_path': _abs_path(tile, scope, bit_sig),
                                'signal':   bit_sig,
                                'category': 4,
                                'source':   f'changes[{idx}].chain[{g.get("seq", "?")}].bit{b}',
                            })
                    else:
                        out.append({
                            'net_path': _abs_path(tile, scope, base),
                            'signal':   base,
                            'category': 4,
                            'source':   f'changes[{idx}].chain[{g.get("seq", "?")}]',
                        })

        # Cat 12: schema-independent — parse context_line directly (see helper above).
        # Runs unconditionally (any change_type with a context_line), not gated on
        # d_input_gate_chain shape at all.
        for base in _context_line_eq_operands(c.get('context_line'), c.get('new_token'),
                                               eco_new_signals):
            width = _cached_bus_width(base)
            if width and width > 1:
                for b in range(width):
                    out.append({
                        'net_path': _abs_path(tile, scope, f'{base}[{b}]'),
                        'signal':   f'{base}[{b}]',
                        'category': 12,
                        'source':   f'changes[{idx}].context_line_eq.bit{b}',
                    })
            else:
                out.append({
                    'net_path': _abs_path(tile, scope, base),
                    'signal':   base,
                    'category': 12,
                    'source':   f'changes[{idx}].context_line_eq',
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

        # Cat 4b: priority_force condition-cone leaves. The cone is synthesized at
        # Step 3, but its leaves are derivable now from the diff-aware added-branch
        # condition. Query them so fenets resolves each per-stage — the emitter then
        # consumes the rename map (P&R renames these internal nets, e.g. bus-bit
        # flatten + MB-flop banking, which name heuristics cannot resolve reliably).
        if ct == 'priority_force':
            for leaf in _pf_cone_leaves(c, ref_dir):
                out.append({
                    'net_path': _abs_path(tile, scope, leaf),
                    'signal':   leaf,
                    'category': 4,
                    'source':   f'changes[{idx}].priority_force_cone_leaf',
                })

        # Cat 4c: comb_net_force cone leaves. The builder (eco_cone_rebuild) rebuilds the
        # signal's whole changed cone (selector = full priority-prefix), whose leaves
        # include bus bits P&R renames/optimizes (bracket AND flat forms may be absent) —
        # only fenets FM equivalence resolves those. Query them so the rename map covers
        # the cone; the emitter consumes the map (then flat heuristic) per stage.
        if ct == 'comb_net_force' and ref_dir and cone_leaves:
            sig = c.get('signal') or c.get('new_token') or c.get('target')
            # Scope(s) used to qualify the cone leaves. A module instantiated more
            # than once has no single `scope`; the analyzer records the per-instance
            # hierarchy in `instances[]` and ALSO pre-resolves the leaves into
            # nets_to_query (Cat 0 above). With neither a `scope` nor `instances`,
            # _abs_path would strip the hierarchy to a bare leaf name (unscoped →
            # FM-036) — so skip the walk entirely and rely on the Cat 0 passthrough.
            cnf_scopes = [scope] if scope else list(c.get('instances') or [])
            if sig and cnf_scopes:
                leaves = cone_leaves(ref_dir, c.get('module_name') or '', sig)
                for sc in cnf_scopes:
                    for leaf in leaves:
                        out.append({
                            'net_path': _abs_path(tile, sc, leaf),
                            'signal':   leaf,
                            'category': 4,
                            'source':   f'changes[{idx}].comb_net_force_cone_leaf',
                        })

        # Cat 4d: comb_net_force SELECTOR branch-conditions. The region selector (delta-prefix
        # path guard) references folded combinational wires (e.g. dsp_cmd_valid, dsp_cnt_end =
        # counter comparisons) that synthesis deleted from the netlist. If not resolved, step-3
        # REBUILDS them from the counters (591-gate bloat) and references synthesis-removed nets
        # (NET-ABSENT at apply). Query them so FM finds the existing netlist net to BIND to;
        # step-3's _Synth grounds bound signals as leaves instead of re-deriving. selector_folded_
        # conditions() excludes signals that constant-fold or are already in the netlist.
        if ct == 'comb_net_force' and ref_dir and selector_folded_conditions:
            sig = c.get('signal') or c.get('new_token') or c.get('target')
            # Same multi-instance scoping rule as Cat 4c: qualify per instance when
            # the module has no single scope; skip when unscoped (Cat 0 covers it).
            cnf_scopes = [scope] if scope else list(c.get('instances') or [])
            if sig and cnf_scopes:
                conds = selector_folded_conditions(c, ref_dir)
                for sc in cnf_scopes:
                    for cond in conds:
                        out.append({
                            'net_path': _abs_path(tile, sc, cond),
                            'signal':   cond,
                            'category': 4,
                            'source':   f'changes[{idx}].comb_net_force_selector_cond',
                        })

        # Cat 4e: reg_guard_delta (Intent-A and_term on a register) WIDENED-BRANCH guard leaves.
        # The clock-gate builder (eco_cone_rebuild emit_reg_guard_delta_batch) re-derives the load
        # guard from RTL; any guard signal synthesis folded out of the netlist (e.g. 9666
        # WckSyncCtr0 `recdsp_c0cs`, an internal combinational reg) would be REBUILT (~150 gates)
        # unless bound. Query them so FM finds the existing equivalent net; the builder's _Synth
        # grounds bound signals as leaves. reg_guard_folded_conditions() excludes signals that
        # constant-fold, are already in the netlist, or are true flops.
        if ct == 'and_term' and c.get('target_register') and ref_dir and reg_guard_folded_conditions:
            for cond in reg_guard_folded_conditions(c, ref_dir):
                out.append({
                    'net_path': _abs_path(tile, scope, cond),
                    'signal':   cond,
                    'category': 4,
                    'source':   f'changes[{idx}].reg_guard_selector_cond',
                })

        # Cat 4f: reg_guard_delta old_guard_net per-stage resolution.
        # The input-pin-rewire builder needs to know the per-stage equivalent of the old guard
        # netlist signal (e.g. ctmn_1251735). In many designs this net is stable (same name in
        # Synth/PP/Route), but in general P&R may rename it — querying via fenets ensures the
        # per-stage rename_map entry is available so the builder can resolve it reliably.
        # The query is a NOP if the signal is stable (fenets returns it verbatim); it is critical
        # when the signal was optimised/renamed across stages.
        _ogn = c.get('old_guard_net') or ''
        if ct == 'and_term' and c.get('target_register') and _ogn:
            out.append({
                'net_path': _abs_path(tile, scope, _ogn),
                'signal':   _ogn,
                'category': 4,
                'source':   f'changes[{idx}].old_guard_net',
            })

        # Cat 4g: reg_guard_delta BUILD cone leaves. The clock-gate builder rebuilds the load
        # guard from RTL (a dissolved guard signal like `recdsp_c0cs` is rebuilt, not bound); that
        # rebuild grounds on deeper leaves (e.g. `recdsp_c0cs = case(dsp_cmd_msc[7:0])` -> dsp_cmd_msc
        # bits) that P&R may optimize away per stage (9666: dsp_cmd_msc[0]/[3] gone in PP/Route ->
        # NET-ABSENT). Query EVERY cone leaf so fenets resolves each per-stage (then D-CHAIN carries
        # Synth->PP->Route). Dedup drops the many that overlap comb_net_force cone leaves.
        if ct == 'and_term' and c.get('target_register') and ref_dir and reg_guard_cone_leaves:
            for leaf in reg_guard_cone_leaves(c, ref_dir):
                out.append({
                    'net_path': _abs_path(tile, scope, leaf),
                    'signal':   leaf,
                    'category': 4,
                    'source':   f'changes[{idx}].reg_guard_cone_leaf',
                })

        # Cat 11: compare_fold leaf nets. eco_emit_compare_fold self-derives the fold; its
        # LEAF nets (the two compare operands + the stage-stable separating-literal field
        # bit) must be resolved per-stage across ALL uniquified copies so P&R renames land
        # in the map. The reduction-OR fold bits are a NEW port (absent in PreEco) → skipped
        # like other eco_new_signals. The mismatch net itself is found per-stage by the
        # builder from the netlist, so it is NOT queried here.
        if ct == 'compare_fold' and ref_dir:
            try:
                from eco_emit_compare_fold import derive_nets as _cf_nets
                leaves, _ = _cf_nets(ref_dir, c.get('module_name') or '',
                                     c.get('context_line', ''),
                                     c.get('fold_signal') or c.get('new_token'),
                                     c.get('compare_signal') or c.get('target_register'))
            except Exception:
                leaves = []
            cf_scopes = [scope]
            cf_insts = c.get('instances') or []
            if len(cf_insts) > 1:
                parent = '/'.join(scope.split('/')[:-1]) if '/' in scope else ''
                for inst in cf_insts[1:]:
                    cf_scopes.append(f"{parent}/{inst}" if parent else inst)
            for sc in cf_scopes:
                for leaf in leaves:
                    if re.sub(r'\[.*$', '', leaf) in eco_new_signals:
                        continue
                    out.append({
                        'net_path': _abs_path(tile, sc, leaf),
                        'signal':   leaf,
                        'category': 1,
                        'source':   f'changes[{idx}].compare_fold_leaf',
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
    p.add_argument('--ref-dir',  default=None,
                   help='REF_DIR — enables deriving priority_force condition-cone '
                        'leaves (decomposed like Step 3) so fenets resolves them per-stage.')
    args = p.parse_args()

    try:
        rtl = json.loads(Path(args.rtl_diff).read_text())
    except Exception as e:
        print(f'FAIL: cannot read rtl_diff: {e}', file=sys.stderr)
        return 1

    queries = derive(rtl, args.tile, ref_dir=args.ref_dir)
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
