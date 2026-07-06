#!/usr/bin/env python3
"""
eco_cone_rebuild.py — GENERAL combinational-cone rebuilder for a signal computed by
a procedural `always` block (if/else-if/else + case + blocking assigns).

This generalizes priority_force / and_term-guard / enable-change: an ECO that changes
the logic computing a signal S is handled by REBUILDING S's cone from the NEW RTL as
a priority mux (conditions -> boolean cones via synthesize_condition; values -> const/
net muxes), then net-forcing S so all fanout sees it.

Pipeline:
  1. parse_always(rtl_text, signal) -> priority-value tree:
        {'default': <value|None>, 'assigns': [(path_cond_expr, value_expr), ...]}
     in SOURCE order. Verilog blocking semantics = LAST matching assignment wins, so
     the lowering folds from last to first (last = highest priority).
  2. (lowering + net-force live in the emitter that imports this.)

FAIL-CLOSED: parse_always raises _CErr on any construct it cannot model
(unbalanced blocks, unsupported statement) — the caller aborts rather than build a
partial/wrong cone.

Importable:
    from eco_cone_rebuild import parse_always, _CErr
    tree = parse_always(open(rtl).read(), 'recdsp_c0mop')
"""
import re


class _CErr(Exception):
    pass


_STRIP_CACHE = {}


def _strip_comments(text):
    key = id(text)
    hit = _STRIP_CACHE.get(key)
    if hit is not None and hit[0] is text:      # guard against id reuse
        return hit[1]
    out = re.sub(r'/\*.*?\*/', '', re.sub(r'//[^\n]*', '', text), flags=re.DOTALL)
    _STRIP_CACHE[key] = (text, out)
    return out


_WHOLE_CACHE = {}
_PERBIT_CACHE = {}


def has_whole_driver(rtl_text, signal):
    """True if `signal` has an unselected (whole-signal) blocking/continuous assignment
    (`signal = ...`, no bit/part select). Distinguishes a signal driven as a whole (case/
    if chain, e.g. recdsp_c0mop) from one driven bit-by-bit (WckIsInSync[k])."""
    key = (id(rtl_text), signal)
    hit = _WHOLE_CACHE.get(key)
    if hit is not None:
        return hit
    r = bool(re.search(r'\b' + re.escape(signal) + r'\s*=(?!=)', _strip_comments(rtl_text)))
    _WHOLE_CACHE[key] = r
    return r


def perbit_drivers(rtl_text, signal):
    """All simple bit/part-select assignments to `signal`: returns [(sel_str, rhs_str)]
    for each `signal[SEL] = RHS;` (blocking or continuous, NOT `<=`). Handles the common
    per-bit combinational fan (`always @* signal[k] = expr;`). Conditional per-bit assigns
    (inside if/case) are not modeled here — the caller lowers RHS via parse_expr and fails
    closed if RHS is not a plain expression."""
    key = (id(rtl_text), signal)
    hit = _PERBIT_CACHE.get(key)
    if hit is not None:
        return hit
    txt = _strip_comments(rtl_text)
    out = []
    for m in re.finditer(r'\b' + re.escape(signal) + r'\s*\[([^\]]+)\]\s*=(?!=)\s*', txt):
        end = txt.find(';', m.end())
        if end < 0:
            continue
        out.append((m.group(1).strip(), txt[m.end():end].strip()))
    _PERBIT_CACHE[key] = out
    return out


def _find_always_block(text, signal):
    """Return the body text (inside the outer begin..end) of the `always` block that
    contains a blocking/nonblocking assignment to `signal`. Picks the block whose
    span encloses the first `signal =`/`signal <=` assignment."""
    # locate an assignment to signal (bare or bit/part select)
    asg = re.search(r'\b' + re.escape(signal) + r'\s*(?:\[[^\]]*\])?\s*(?:<=|=)(?!=)', text)
    if not asg:
        raise _CErr(f"no assignment to {signal!r} found")
    apos = asg.start()
    # find the nearest `always` keyword before apos
    always_iter = [m for m in re.finditer(r'\balways\b', text) if m.start() < apos]
    if not always_iter:
        raise _CErr(f"no `always` before assignment to {signal!r}")
    astart = always_iter[-1].start()
    # after `always @(...)` find the opening `begin` and its matching `end`
    m = re.compile(r'\balways\b\s*(@\s*\([^)]*\)|@\s*\*|)\s*', re.DOTALL).match(text, astart)
    if not m:
        raise _CErr("cannot parse always header")
    p = m.end()
    if not text[p:].lstrip().startswith('begin'):
        # single-statement always (no begin) — take up to the next ';'
        stmt_end = text.find(';', p)
        if stmt_end < 0:
            raise _CErr("single-statement always not terminated")
        return text[p:stmt_end + 1]
    bstart = text.index('begin', p) + len('begin')
    depth = 1
    tok = re.compile(r'\b(begin|end|case|casex|casez|endcase)\b')
    i = bstart
    for mm in tok.finditer(text, bstart):
        kw = mm.group(1)
        if kw in ('begin',):
            depth += 1
        elif kw in ('case', 'casex', 'casez'):
            depth += 1
        elif kw == 'endcase':
            depth -= 1
        elif kw == 'end':
            depth -= 1
        if depth == 0:
            return text[bstart:mm.start()]
    raise _CErr("unbalanced always block (no matching end)")


# ── statement-list parser ────────────────────────────────────────────────────
# Produces, for the target signal, an ordered list of (path_condition, value) plus
# the default (an unconditional assignment). path_condition is a list of (expr, sense)
# terms that are AND-ed; sense False means the term is negated (else-branch).

def _balanced_parens(s, start):
    """s[start]=='(' -> return index just after the matching ')'."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '(':
            depth += 1
        elif s[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    raise _CErr("unbalanced parentheses in condition")


def _matching_end(s, start, openers, closer):
    """s has an opener at/after start; return index of the matching closer keyword's
    match object end. Used for begin..end and case..endcase."""
    tok = re.compile(r'\b(begin|end|case|casex|casez|endcase)\b')
    depth = 0
    started = False
    for mm in tok.finditer(s, start):
        kw = mm.group(1)
        if kw in ('begin', 'case', 'casex', 'casez'):
            depth += 1; started = True
        elif kw in ('end', 'endcase'):
            depth -= 1
            if started and depth == 0:
                return mm.start(), mm.end()
    raise _CErr("unbalanced begin/case block")


class _Parser:
    def __init__(self, signal):
        self.signal = signal
        self.assigns = []      # (path_cond, value) in source order
        self.default = None

    def _record(self, path_cond, value):
        if not path_cond:
            self.default = value          # unconditional assignment = default
        else:
            self.assigns.append((list(path_cond), value))

    def parse(self, s, path_cond):
        """Parse a statement list `s` under the given path condition (list of
        (expr, sense) AND terms)."""
        i, n = 0, len(s)
        while i < n:
            # skip whitespace / stray semicolons / begin-end wrappers handled below
            m = re.compile(r'\S').search(s, i)
            if not m:
                break
            i = m.start()
            # begin ... end wrapper
            if re.match(r'\bbegin\b', s[i:]):
                bstart = i + len('begin')
                cstart, cend = _matching_end(s, i, ('begin',), 'end')
                self.parse(s[bstart:cstart], path_cond)
                i = cend
                continue
            # if ( cond ) stmt [else stmt]
            mif = re.match(r'\bif\b\s*', s[i:])
            if mif:
                po = s.index('(', i)
                pe = _balanced_parens(s, po)
                cond = s[po + 1:pe - 1].strip()
                then_s, next_i = self._one_statement(s, pe)
                # optional else
                mel = re.match(r'\s*\belse\b', s[next_i:])
                self.parse(then_s, path_cond + [(cond, True)])
                if mel:
                    else_start = next_i + mel.end()
                    else_s, after_else = self._one_statement(s, else_start)
                    self.parse(else_s, path_cond + [(cond, False)])
                    i = after_else
                else:
                    i = next_i
                continue
            # case ( sel ) items endcase
            mcase = re.match(r'\b(case|casex|casez)\b\s*', s[i:])
            if mcase:
                po = s.index('(', i)
                pe = _balanced_parens(s, po)
                sel = s[po + 1:pe - 1].strip()
                cstart, cend = _matching_end(s, i, ('case',), 'endcase')
                self._parse_case(sel, s[pe:cstart], path_cond)
                i = cend
                continue
            # assignment: LHS (<=|=) value ;
            masg = re.match(r'([A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s*(<=|=)(?!=)\s*', s[i:])
            if masg:
                lhs = re.sub(r'\s+', '', masg.group(1))
                vstart = i + masg.end()
                vend = s.index(';', vstart)
                value = s[vstart:vend].strip()
                base = lhs.split('[')[0]
                if base == self.signal:
                    self._record(path_cond, value)
                i = vend + 1
                continue
            raise _CErr(f"unsupported statement near: {s[i:i+40]!r}")

    def _one_statement(self, s, i):
        """Return (statement_text, index_after) for the single statement starting at
        the first non-space at/after i (a begin..end block or a single ...;)."""
        m = re.compile(r'\S').search(s, i)
        if not m:
            raise _CErr("expected statement, got end")
        j = m.start()
        if re.match(r'\bbegin\b', s[j:]):
            bstart = j + len('begin')
            cstart, cend = _matching_end(s, j, ('begin',), 'end')
            return s[bstart:cstart], cend
        if re.match(r'\b(if|case|casex|casez)\b', s[j:]):
            # a bare if/case as the statement — return up to its end, let parse() recurse
            if re.match(r'\bif\b', s[j:]):
                po = s.index('(', j); pe = _balanced_parens(s, po)
                inner, after = self._one_statement(s, pe)
                mel = re.match(r'\s*\belse\b', s[after:])
                if mel:
                    _, after = self._one_statement(s, after + mel.end())
                return s[j:after], after
            cstart, cend = _matching_end(s, j, ('case',), 'endcase')
            return s[j:cend], cend
        # single assignment
        k = s.index(';', j)
        return s[j:k + 1], k + 1

    def _parse_case(self, sel, body, path_cond):
        """Parse case items. Each `label[,label]: stmt` becomes path term
        (sel == label). `default:` becomes the negation of all labels."""
        # split into items by scanning labels `... :` at top level
        items = []       # (labels_list_or_None, stmt_text)
        i, n = 0, len(body)
        all_labels = []
        while i < n:
            m = re.compile(r'\S').search(body, i)
            if not m:
                break
            i = m.start()
            mdef = re.match(r'\bdefault\b\s*:', body[i:])
            if mdef:
                stmt_start = i + mdef.end()
                stmt, after = self._one_statement(body, stmt_start)
                items.append((None, stmt)); i = after; continue
            # labels: one or more `expr` separated by ',' then ':'
            colon = self._label_colon(body, i)
            labels = [x.strip() for x in body[i:colon].split(',') if x.strip()]
            stmt_start = colon + 1
            stmt, after = self._one_statement(body, stmt_start)
            items.append((labels, stmt)); all_labels += labels; i = after
        for labels, stmt in items:
            if labels is None:
                terms = [(f'({sel})==({lb})', False) for lb in all_labels]  # default = none matched
                self.parse(stmt, path_cond + terms)
            else:
                # item matches if sel == any label; multiple labels share the stmt
                if len(labels) == 1:
                    self.parse(stmt, path_cond + [(f'({sel})==({labels[0]})', True)])
                else:
                    # OR of equalities — represent as a single condition expr
                    ored = ' | '.join(f'({sel})==({lb})' for lb in labels)
                    self.parse(stmt, path_cond + [(ored, True)])

    def _label_colon(self, body, i):
        """Find the `:` ending a case label, skipping colons inside (...) or [...]."""
        depth = 0
        for j in range(i, len(body)):
            ch = body[j]
            if ch in '([':
                depth += 1
            elif ch in ')]':
                depth -= 1
            elif ch == ':' and depth == 0:
                return j
        raise _CErr("case label missing ':'")


_ALWAYS_CACHE = {}


def parse_always(rtl_text, signal):
    """Priority-value tree for `signal` from its `always` block in rtl_text (memoized per
    (rtl_text identity, signal)). Returns {'default': value|None, 'assigns': [(path_cond,
    value), ...]} where path_cond is a list of (expr, sense) AND-terms. Fail-closed."""
    key = (id(rtl_text), signal)
    cached = _ALWAYS_CACHE.get(key)
    if cached is not None:
        return cached
    text = _strip_comments(rtl_text)
    body = _find_always_block(text, signal)
    p = _Parser(signal)
    p.parse(body, [])
    tree = {'default': p.default, 'assigns': p.assigns}
    _ALWAYS_CACHE[key] = tree
    return tree


# ── DELTA: diff OLD vs NEW priority-value trees ──────────────────────────────

def _nterm(expr):
    return re.sub(r'\s+', '', expr)


def _ncond(cond):
    return tuple((_nterm(e), s) for e, s in cond)


def _nval(v):
    return re.sub(r'\s+', '', str(v))


def _starts_with(cond, prefix):
    if len(cond) < len(prefix):
        return False
    return _ncond(cond[:len(prefix)]) == _ncond(prefix)


def _common_prefix(conds):
    """Longest common leading (expr,sense) prefix across a list of path conditions."""
    if not conds:
        return []
    pref = list(conds[0])
    for c in conds[1:]:
        k = 0
        while k < len(pref) and k < len(c) and _nterm(pref[k][0]) == _nterm(c[k][0]) and pref[k][1] == c[k][1]:
            k += 1
        pref = pref[:k]
        if not pref:
            break
    return pref


def compute_delta(old_tree, new_tree):
    """Diff two priority-value trees for the SAME signal. Returns the minimal changed
    region as {prefix, subtree, default, summary} or None when the trees are identical.

      prefix   : list of (expr, sense) — the path selector common to every
                 changed/removed entry (e.g. the enclosing `case`-item + outer negations).
      subtree  : [(suffix_cond, value)] — the NEW entries under `prefix`, conditions
                 relative to the prefix; folded (last-wins) they give the new value in
                 the region. `default` is the tree default (value when no branch matches).

    The patch is then:  S = (prefix active) ? fold(subtree, default) : S_old_driver .
    This rebuilds ONLY the changed region, gated by its selector — proportional to the
    ECO, not the whole cone."""
    old_keys = {(_ncond(c), _nval(v)) for c, v in old_tree['assigns']}
    new_keys = {(_ncond(c), _nval(v)) for c, v in new_tree['assigns']}
    added   = [(c, v) for c, v in new_tree['assigns'] if (_ncond(c), _nval(v)) not in old_keys]
    removed = [(c, v) for c, v in old_tree['assigns'] if (_ncond(c), _nval(v)) not in new_keys]
    default_changed = _nval(old_tree.get('default')) != _nval(new_tree.get('default'))
    if not added and not removed and not default_changed:
        return None
    region = [c for c, _ in added] + [c for c, _ in removed]
    prefix = _common_prefix(region) if region else []
    subtree = [(c[len(prefix):], v) for c, v in new_tree['assigns'] if _starts_with(c, prefix)]
    return {
        'prefix': prefix,
        'subtree': subtree,
        'default': new_tree.get('default'),
        'summary': {'added': len(added), 'removed': len(removed),
                    'default_changed': default_changed, 'subtree_branches': len(subtree)},
    }


# ── LOWER: delta -> gates (recursive _Synth grounds selector + values) ────────
try:
    from eco_emit_priority_force import _module_netlist_body, _mod_key
    from eco_rtl_config import RtlConfig
    from eco_rtl_synth import _Synth, parse_expr, build_width_map
except Exception:
    _module_netlist_body = _mod_key = RtlConfig = None
    _Synth = parse_expr = build_width_map = None


def lower_delta(ref_dir, module, signal, jira='eco'):
    """Rebuild the DELTA of `signal`'s cone (NEW vs OLD RTL) as gates using the recursive
    RTL synthesizer (_Synth). Fully general: the changed subtree is lowered as a priority
    mux tree (default, then each branch overrides in order) where BOTH the branch
    conditions AND the branch values are grounded recursively to real netlist nets /
    registers — no constant-only restriction. Returns
      {'selector', 'region_bits':{b:net}, 'width', 'gates', 'summary'} or None (no delta).
    Raises _CErr fail-closed on anything unsupported."""
    if not (_Synth and RtlConfig):
        raise _CErr("synthesizer unavailable")
    base = _mod_key(module) if _mod_key else re.sub(r'^ddrss_\w+?_t_', '', module)
    from eco_extract_pf_condition import resolve_rtl
    new_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='SynRtl')
    old_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='PreEco/SynRtl')
    if not (new_rtl and old_rtl):
        raise _CErr(f"cannot locate {signal} RTL in both trees")
    rtl_text = open(new_rtl, errors='replace').read()
    nt = parse_always(rtl_text, signal)
    ot = parse_always(open(old_rtl, errors='replace').read(), signal)
    delta = compute_delta(ot, nt)
    if delta is None:
        return None
    cfg = RtlConfig(ref_dir)
    nlbody = _module_netlist_body(ref_dir, module) if _module_netlist_body else ''
    innl = (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody))) if nlbody else (lambda s: True)
    macros = {k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None} if cfg else {}
    wm = build_width_map(rtl_text, macros)
    seq = [0]
    def mk(t):
        seq[0] += 1
        return f'n_eco_{jira}_cr_{t}_{seq[0]}'
    synth = _Synth(cfg, wm, rtl_text, innl, mk, module)
    # SURGICAL selector (engineer's trick): instead of rebuilding the whole priority
    # PREFIX (which drags in dsp_cnt_end / dsp_cmd_valid / timers -> thousands of gates),
    # reuse the ORIGINAL signal value as the region detector. `orig == <old region value>`
    # is equivalent to "the old chain reached & fired the changed branch" (the entire
    # prefix), because orig already encodes the full chain. The subtree conditions are
    # LOCAL (prefix stripped), so region logic stays tiny. The comparator itself is built
    # in emit_comb_net_force (it needs the per-bit net_orig nets); here we return the OLD
    # region value(s) it must match.
    old_region_values = []
    for c, v in ot['assigns']:
        if _starts_with(c, delta['prefix']) and v not in old_region_values:
            old_region_values.append(v)
    try:
        # width: signal decl, else widest branch/default value
        width = wm.get(signal)
        if not width:
            vals = [v for _, v in delta['subtree']] + ([delta['default']] if delta['default'] else [])
            width = max([synth._w(parse_expr(v)) or 1 for v in vals] or [1])
        # subtree -> priority mux tree per bit, LOCAL conditions only (prefix stripped).
        # Compute each branch's selector ONCE (hoisted out of the bit loop) — otherwise the
        # WCK-sync guard logic is rebuilt per bit (width x duplication). Per-bit work is then
        # just cheap constant muxes.
        dflt_ast = parse_expr(delta['default']) if delta['default'] is not None else None
        branch_asts = [(synth._path_scalar(cond), parse_expr(val)) for cond, val in delta['subtree']]
        region_bits = {}
        for b in range(width):
            cur = synth.bit(dflt_ast, b) if dflt_ast is not None else "1'b0"
            for bsel, vast in branch_asts:
                cur = synth._mux(bsel, synth.bit(vast, b), cur)
            region_bits[b] = cur
    except Exception as e:
        raise _CErr(f"delta lowering failed for {signal}: {e}")
    return {'region_bits': region_bits, 'width': width, 'old_region_values': old_region_values,
            'gates': synth.gates, 'summary': delta['summary'], 'synth': synth, 'mk': mk}


def emit_comb_net_force(ref_dir, module, signal, jira='eco', rename_map=None):
    """Emit the ECO for a COMBINATIONAL net delta (e.g. B2 recdsp_c0mop): re-drive the
    net so ALL fanout sees the new value inside the changed region and the original value
    elsewhere. Per bit b:
        net[b] = selector ? region_bits[b] : net[b]_orig
    where the original combinational driver's output pin is renamed net -> net_orig (a
    per-stage driver-side rewire), so the mux feeds every consumer. Returns
      {'gates':[...], 'rewires':[...], 'errors':[...], 'summary':str} (study-shaped,
    per-stage-resolved). Raises _CErr fail-closed on structural problems."""
    from eco_emit_priority_force import (_driver_map, _stage_net_tokens, _stage_net,
                                         _map_stage_net, _pcstage, STAGES)
    r = lower_delta(ref_dir, module, signal, jira)
    if r is None:
        return {'gates': [], 'rewires': [], 'errors': [], 'summary': 'no delta'}
    synth, width = r['synth'], r['width']
    mk = r['mk']
    old_vals = r.get('old_region_values') or []
    dmaps = {st: _driver_map(ref_dir, module, st) for st in STAGES}
    is_bus = width > 1
    errs, rewires = [], []

    def _net(b):
        return f'{signal}[{b}]' if is_bus else signal

    # ── pass 1: validate each bit's combinational driver + reserve net_orig ──
    orig_bit, drv_ps = {}, {}
    for b in range(width):
        net = _net(b)
        cps, pps, ok = {}, {}, True
        for st in STAGES:
            sd = dmaps[st].get(net)
            if not sd:
                errs.append(f"{net}: no driver in {st} netlist — cannot re-drive."); ok = False; break
            if sd[2]:
                errs.append(f"{net}: driven by a flop (.Q) in {st}; expected a combinational driver."); ok = False; break
            cps[st], pps[st] = sd[0], sd[1]
        if not ok:
            continue
        orig_bit[b] = mk(f'{signal}_{b}_orig')
        drv_ps[b] = (cps, pps)
    if errs:
        return {'gates': synth.gates, 'rewires': [], 'errors': errs, 'summary': r['summary']}
    if not old_vals:
        return {'gates': synth.gates, 'rewires': [],
                'errors': [f"{signal}: no OLD region value — cannot build the orig-based region detector."],
                'summary': r['summary']}

    # ── SURGICAL selector: sel = OR over old region values v of (orig == v). This reuses
    # the existing signal (net_orig bits) as the region detector — replacing the entire
    # rebuilt priority prefix with a small equality comparator (engineer's trick). ──
    sel = "1'b0"
    for v in old_vals:
        vast = parse_expr(v)
        eq_terms = [synth._xnor(orig_bit[b], synth.bit(vast, b)) for b in range(width) if b in orig_bit]
        sel = synth._or(sel, synth._reduce(eq_terms, 'and'))
    nsel = synth._inv(sel)

    # ── pass 2: per-bit force-mux net[b] = sel ? region[b] : net_orig[b] + driver rewire ──
    for b in range(width):
        if b not in orig_bit:
            continue
        region = r['region_bits'].get(b, "1'b0")
        net, net_orig = _net(b), orig_bit[b]
        t_reg = synth._and(sel, region)
        t_old = synth._and(nsel, net_orig)
        if t_reg == "1'b0" and t_old == "1'b0":
            errs.append(f"{net}: mux folds to constant 0 for both legs — refusing (fail-closed).")
            continue
        synth._g(synth.cells['OR2'], 'OR2', {'A1': t_reg, 'A2': t_old, 'Z': net})
        cps, pps = drv_ps[b]
        rewires.append({
            'change_type': 'rewire', 'instance_name': cps['Synthesize'],
            'cell_name': cps['Synthesize'], 'cell_name_per_stage': cps,
            'module_name': module, 'pin': pps['Synthesize'], 'pin_per_stage': pps,
            'old_net': net, 'new_net': net_orig,
            'confirmed': True, 'force_reapply': True, 'source': 'eco_cone_rebuild',
            'net_force': True, 'driver_side': True,
            'notes': f"comb net-force (surgical): redirect combinational driver of {net} through "
                     f"the region mux (output-pin rename {net}->{net_orig}, per-stage cell).",
        })
    gates = synth.gates
    # give every cone/mux gate a per-stage view, then resolve leaf nets per stage
    # (fenets rename map authoritative, then flat-name heuristic; internal n_eco_ names
    # are absent from both and pass through unchanged).
    # The rename map keys are scoped ('<tile-relative-scope>/<leaf>', e.g.
    # 'rec/recdsp/WckSyncCtr0[0]'); a comb_net_force change carries no scope, so index the
    # map by bare leaf name and use each key's own scope. Cone leaves are single-module
    # (unique bare names), so this is unambiguous.
    leaf2key = {}
    for k in (rename_map or {}):
        if k != '_metadata':
            leaf2key.setdefault(k.rsplit('/', 1)[-1], k)
    toks = {st: _stage_net_tokens(ref_dir, module, st) for st in STAGES}
    def _exists(v, st):
        return isinstance(v, str) and (v in toks[st] or v.split('/')[0] in toks[st])
    def _resolve(nn_, st):
        # Prefer the fenets map, but ONLY when it gives a REAL renamed net (exists in the
        # stage netlist and is not just an echo of the query). A bracket echo / FM-036
        # entry must NOT override the flat-name heuristic (which flattens sig[b]->sig_b_).
        fk = leaf2key.get(nn_)
        if fk:
            mapped = _map_stage_net(nn_, st, fk.rsplit('/', 1)[0], rename_map)
            if mapped and mapped != nn_ and _exists(mapped, st):
                return mapped
        flat = _stage_net(nn_, toks[st])
        if _exists(flat, st):
            return flat
        # last resort: a real map value even if it equals the bare name
        if fk:
            mapped = _map_stage_net(nn_, st, fk.rsplit('/', 1)[0], rename_map)
            if _exists(mapped, st):
                return mapped
        return flat
    for g in gates:
        pcs = g.get('port_connections_per_stage') or _pcstage(g['port_connections'])
        for st in STAGES:
            if isinstance(pcs.get(st), dict):
                pcs[st] = {p: _resolve(v, st) for p, v in pcs[st].items()}
        g['port_connections_per_stage'] = pcs
        g.setdefault('instance_name', g['output_net'])
    for rw in rewires:
        ops = {st: _resolve(rw['old_net'], st) for st in STAGES}
        rw['old_net_per_stage'] = ops
    return {'gates': gates, 'rewires': rewires, 'errors': errs, 'summary': r['summary']}


# small gate helpers reusing the resolved library cells
def _lib():
    from eco_emit_priority_force import _DEFAULT_CELLS
    return _DEFAULT_CELLS


def _g(gates, mk, cell, fn, pc):
    out = pc.get('Z') or pc.get('ZN')
    gates.append({'change_type': 'new_logic_gate', 'instance_name': f'eco_cr_{len(gates)}',
                  'cell_type': cell, 'gate_function': fn, 'output_net': out, 'module_name': '',
                  'port_connections': pc, 'confirmed': True, 'source': 'eco_cone_rebuild',
                  'reason': f'cone-rebuild {fn}'})
    return out


def _inv_net(a, gates, mk, cfg):
    return _g(gates, mk, _lib()['INV'], 'INV', {'I': a, 'ZN': mk('inv')})


def _and2(a, b, gates, mk, cfg):
    if b is None:
        return a
    return _g(gates, mk, _lib()['AND2'], 'AND2', {'A1': a, 'A2': b, 'Z': mk('and')})


def _or_nets(nets, gates, mk, cfg):
    cur = list(nets)
    while len(cur) > 1:
        a, b = cur.pop(0), cur.pop(0)
        cur.append(_g(gates, mk, _lib()['OR2'], 'OR2', {'A1': a, 'A2': b, 'Z': mk('or')}))
    return cur[0]


def cone_leaves(ref_dir, module, signal, jira='q'):
    """Real netlist-leaf names of a comb_net_force signal's rebuilt cone — the nets fenets
    must resolve per-stage so the re-drive applies in P&R (analog of priority_force's
    _pf_cone_leaves). Returns a sorted list of bare/bus-bit leaf names, or [] if there is
    no delta or the cone can't be built."""
    try:
        r = lower_delta(ref_dir, module, signal, jira=jira)
    except Exception:
        return []
    if not r:
        return []
    outs = {g['output_net'] for g in r['gates']}
    leaves = set()
    for g in r['gates']:
        for p, v in (g.get('port_connections') or {}).items():
            if p in ('Z', 'ZN') or not isinstance(v, str):
                continue
            if v in outs or v.startswith(('n_eco_', 'eco_')) or re.match(r"^\d*'[bhdo]", v):
                continue
            leaves.add(v)
    return sorted(leaves)


def emit_into_study(rtl_diff, study, jira, ref_dir, rename_map=None):
    """Splice every `comb_net_force` change in rtl_diff into `study` (all stages), using
    the deterministic emit_comb_net_force builder. Mirrors eco_emit_priority_force.emit:
    returns (added, errors). On ANY builder error the study is left UNTOUCHED (caller
    aborts fail-closed). Each comb_net_force change needs only {module_name, signal} — the
    delta, region, selector, gates and per-stage driver rewires are all derived from RTL +
    netlist deterministically."""
    from eco_emit_priority_force import STAGES
    changes = [c for c in rtl_diff.get('changes', []) if c.get('change_type') == 'comb_net_force']
    if not changes:
        return 0, []
    pending, errs = [], []
    for i, c in enumerate(changes):
        mod = c.get('module_name'); sig = c.get('signal') or c.get('new_token') or c.get('target')
        if not mod or not sig:
            errs.append(f"comb_net_force[{i}]: missing module_name or signal.")
            continue
        try:
            out = emit_comb_net_force(ref_dir, mod, sig, jira=jira, rename_map=rename_map)
        except Exception as e:
            errs.append(f"comb_net_force[{i}] {mod}/{sig}: build failed: {e}")
            continue
        if out['errors']:
            errs.extend(f"comb_net_force[{i}] {mod}/{sig}: {m}" for m in out['errors'])
            continue
        pending.append(out['gates'] + out['rewires'])
    if errs:
        return 0, errs                              # fail-closed: study untouched
    added = 0
    for entries in pending:
        for st in STAGES:
            study.setdefault(st, []).extend(dict(e) for e in entries)
            added += len(entries)
    return added, []


if __name__ == '__main__':
    import sys, json
    if len(sys.argv) >= 2 and sys.argv[1] == '--emit-into-study':
        import argparse, os
        ap = argparse.ArgumentParser()
        ap.add_argument('--emit-into-study', action='store_true')
        ap.add_argument('--rtl-diff', required=True)
        ap.add_argument('--study', required=True)
        ap.add_argument('--jira', required=True)
        ap.add_argument('--ref-dir', required=True)
        ap.add_argument('--output', required=True)
        ap.add_argument('--rename-map', default=None)
        a = ap.parse_args()
        rtl_diff = json.loads(open(a.rtl_diff).read())
        study = json.loads(open(a.study).read())
        rmap = None
        if a.rename_map and os.path.isfile(a.rename_map):
            try:
                rmap = json.loads(open(a.rename_map).read())
            except Exception:
                rmap = None
        n, errs = emit_into_study(rtl_diff, study, a.jira, a.ref_dir, rename_map=rmap)
        if errs:
            marker = ("ECO_SCRIPT_LAUNCHED: eco_cone_rebuild.py --emit-into-study\n"
                      f"  ABORTED — {len(errs)} comb_net_force build error(s):\n"
                      + "".join(f"    - {e}\n" for e in errs)
                      + "  Study UNTOUCHED. Fix the comb_net_force change(s) and re-run.\n")
            print(marker)
            open(a.output.replace('.json', '_comb_net_force_marker.txt'), 'w').write(marker)
            sys.exit(2)
        open(a.output, 'w').write(json.dumps(study, indent=2))
        marker = ("ECO_SCRIPT_LAUNCHED: eco_cone_rebuild.py --emit-into-study\n"
                  f"  comb_net_force entries spliced (gates+rewires, all stages): {n}\n")
        print(marker)
        open(a.output.replace('.json', '_comb_net_force_marker.txt'), 'w').write(marker)
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == '--emit':
        ref, sig = sys.argv[2], sys.argv[3]
        mod = sys.argv[4] if len(sys.argv) > 4 else 'ddrss_umcdat_t_umcrecdsp'
        out = emit_comb_net_force(ref, mod, sig, jira='9666')
        allnets = {g['output_net'] for g in out['gates']}
        leaves = sorted({v for g in out['gates'] for k, v in g['port_connections'].items()
                         if k not in ('Z', 'ZN') and isinstance(v, str) and v not in allnets
                         and not v.startswith(("1'b", "0'b"))})
        print(f"gates={len(out['gates'])} rewires={len(out['rewires'])} errors={len(out['errors'])}")
        print(f"summary={out['summary']}")
        print(f"leaves ({len(leaves)}): {leaves}")
        for e in out['errors']:
            print('  ERR:', e)
        for rw in out['rewires']:
            print(f"  rewire {rw['old_net']}->{rw['new_net']} pin_per_stage={rw['pin_per_stage']}")
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == '--lower':
        ref, sig = sys.argv[2], sys.argv[3]
        mod = sys.argv[4] if len(sys.argv) > 4 else 'ddrss_umcdat_t_umcrecdsp'
        r = lower_delta(ref, mod, sig, jira='9666')
        if r is None:
            print('no delta'); sys.exit(0)
        outs = {g['output_net'] for g in r['gates']}
        leaves = sorted({v for g in r['gates'] for k, v in g['port_connections'].items()
                         if k not in ('Z', 'ZN') and isinstance(v, str) and v not in outs
                         and not v.startswith(("1'b", "0'b"))})
        print(f"width={r['width']} gates={len(r['gates'])} old_region_values={r['old_region_values']}")
        print(f"region_bits={r['region_bits']}")
        print(f"leaves ({len(leaves)}): {leaves}")
        sys.exit(0)
    if len(sys.argv) >= 5 and sys.argv[1] == '--delta':
        old_rtl, new_rtl, sig = sys.argv[2], sys.argv[3], sys.argv[4]
        ot = parse_always(open(old_rtl, errors='replace').read(), sig)
        nt = parse_always(open(new_rtl, errors='replace').read(), sig)
        d = compute_delta(ot, nt)
        if d is None:
            print(f"{sig}: no delta (trees identical)"); sys.exit(0)
        print(f"{sig}: summary={d['summary']}")
        sel = ' & '.join((('' if s else '~') + f'({e})') for e, s in d['prefix'])
        print(f"region selector (prefix): {sel[:200]}")
        print(f"new sub-tree ({len(d['subtree'])} branches), default={d['default']}:")
        for c, v in d['subtree']:
            cc = ' & '.join((('' if s else '~') + f'({e})') for e, s in c)
            print(f"    when {cc[:140] or '(always)'}  ->  {v}")
        sys.exit(0)
    rtl, sig = sys.argv[1], sys.argv[2]
    tree = parse_always(open(rtl, errors='replace').read(), sig)
    print(f"default: {tree['default']}")
    print(f"assigns ({len(tree['assigns'])}), priority = last-wins:")
    for cond, val in tree['assigns']:
        c = ' & '.join((('' if s else '~') + f'({e})') for e, s in cond)
        print(f"  when {c[:120]}  ->  {sig} = {val}")
