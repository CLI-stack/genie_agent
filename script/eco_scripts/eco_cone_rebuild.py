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


def _const_bits(v):
    """Parse a constant port value into {bit: 0/1}. Scalar 1'bX -> {0:val}; a concat
    {msb,...,lsb} of 1'bX -> per-bit (leftmost = highest bit). Returns None if any element
    is not a constant (so a partially-driven port is not mis-folded)."""
    v = v.strip()
    ms = re.fullmatch(r"1'b([01])", v)
    if ms:
        return {0: int(ms.group(1))}
    if v.startswith('{') and v.endswith('}'):
        elems = [e.strip() for e in v[1:-1].split(',') if e.strip()]
        vals = []
        for e in elems:
            em = re.fullmatch(r"1'b([01])", e)
            if not em:
                return None
            vals.append(int(em.group(1)))
        n = len(vals)
        return {n - 1 - j: vals[j] for j in range(n)}
    return None


def _parent_const_ports(ref_dir, module, stage='Synthesize'):
    """{port_bare: {bit: 0/1}} for ports of `module`'s instance that the PARENT ties to a
    constant. Synthesis constant-propagates these into the module and drops the now-dead
    logic; a cone re-derived from RTL must fold them too or it references nets synthesis
    removed (the 9666 dsp_postnopdly_cnt / reg_rec_postnopdly NET-ABSENT class). Reads the
    PreEco <stage> netlist and parses the (unique) instantiation of `module`."""
    import os as _os, gzip as _gz
    gz = _os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
    if not _os.path.isfile(gz):
        return {}
    try:
        txt = _gz.open(gz, 'rt', errors='replace').read()
    except Exception:
        return {}
    key = _mod_key(module) if _mod_key else module
    for m in re.finditer(r'\b(\w+)\s+(\w+)\s*\(', txt):
        if (_mod_key(m.group(1)) if _mod_key else m.group(1)) != key:
            continue
        start = m.end() - 1
        depth, i = 0, start
        while i < len(txt):
            c = txt[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inst = txt[start:i + 1]
        out = {}
        for port, val in re.findall(r'\.(\w+)\s*\(\s*([^()]*?)\s*\)', inst):
            bits = _const_bits(val)
            if bits:
                out[port] = bits
        return out
    return {}


_PRUNE_OUT_PINS = ('Z', 'ZN', 'Q', 'QN', 'CO', 'CON', 'S', 'SN', 'SO', 'OUT')


def _prune_to_cone(gates, roots):
    """Keep only gates in the fan-in cone of `roots` (the real output nets). Removes dead
    gates left over when _Synth's eager emit builds a subtree that a later constant-fold
    discards — those danglers still reference synthesis-removed nets and fail step3."""
    drv = {g['output_net']: g for g in gates}
    keep, stack = set(), list(roots)
    while stack:
        n = stack.pop()
        g = drv.get(n)
        if not g or g['output_net'] in keep:
            continue
        keep.add(g['output_net'])
        for pin, v in g['port_connections'].items():
            if pin in _PRUNE_OUT_PINS or not isinstance(v, str):
                continue
            stack.append(v)
    return [g for g in gates if g['output_net'] in keep]


def _bindable_from_rename_map(rename_map):
    """Bare signal names (base + optional [bit]) that FM proved equivalent to a netlist net
    (step2 rename map keys). _Synth grounds these as leaves instead of re-deriving them.

    EXCLUDE FM-036 echo-fallback entries: when FM finds no equivalent net, rename_map.py
    echoes the input name across all stages with no actual_wire. Grounding such a signal as
    a bindable leaf makes the builder WIRE a phantom net that does not exist in the netlist
    (the 9666 recdsp_c0cs NET-ABSENT class). These must instead be REBUILT from real leaves
    (or fold out). A real net whose name happens to be preserved is still grounded — it
    falls through to _sig_bit's in_netlist() check; only genuinely-dissolved echoes are
    excluded here. Matches the passing-run behavior where FM-036 signals were omitted from
    the map entirely and the builder rebuilt them."""
    out = set()
    for k, v in (rename_map or {}).items():
        if k == '_metadata':
            continue
        leaf = k.rsplit('/', 1)[-1]
        if isinstance(v, dict):
            present = [v.get(s) for s in ('Synthesize', 'PrePlace', 'Route') if v.get(s)]
            has_aw = any(v.get(f'actual_wire_{s}') for s in ('Synthesize', 'PrePlace', 'Route'))
            base = re.sub(r'\[\d+\]$', '', leaf)
            # pure echo-fallback: every present stage entry == the input name (leaf or its
            # base), and no actual_wire resolved -> FM-036, not a real binding.
            if present and not has_aw and all(s in (leaf, base) for s in present):
                continue
        out.add(leaf)
        out.add(re.sub(r'\[\d+\]$', '', leaf))   # base name too
    return out


def _synth_setup(ref_dir, module, jira='eco', rename_map=None):
    """Build a shared _Synth (+ mk, rtl texts, width map) for a module. Multiple signals in
    the same module reuse ONE synth so its _sig_cache/_path_cache dedup the shared logic
    (e.g. the WCK-sync guard on both recdsp_c0mop and recdsp_c0vld is built once)."""
    if not (_Synth and RtlConfig):
        raise _CErr("synthesizer unavailable")
    base = _mod_key(module) if _mod_key else re.sub(r'^ddrss_\w+?_t_', '', module)
    from eco_extract_pf_condition import resolve_rtl
    new_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='SynRtl')
    old_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='PreEco/SynRtl')
    if not (new_rtl and old_rtl):
        raise _CErr(f"cannot locate {module} RTL in both trees")
    rtl_text = open(new_rtl, errors='replace').read()
    old_text = open(old_rtl, errors='replace').read()
    cfg = RtlConfig(ref_dir)
    nlbody = _module_netlist_body(ref_dir, module) if _module_netlist_body else ''
    innl = (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody))) if nlbody else (lambda s: True)
    macros = {k: cfg.value(k) for k in cfg.defs if cfg.value(k) is not None} if cfg else {}
    wm = build_width_map(rtl_text, macros)
    seq = [0]
    def mk(t):
        seq[0] += 1
        return f'n_eco_{jira}_cr_{t}_{seq[0]}'
    # Use the FULL netlist module name (from the netlist body's `module` line) for every
    # emitted entry — the studier / eq_decode / uniquify all use the full name (e.g.
    # 'ddrss_umcdat_t_umcrecdsp'), while the rtl_diff carries the short name ('umcrecdsp').
    # Emitting the short name breaks the verifier's exact-match module lookup (CONE_MISS)
    # and the applier's module resolution.
    mm = re.match(r'\s*module\s+(\S+)', nlbody or '')
    full_module = mm.group(1) if mm else module
    const_ports = _parent_const_ports(ref_dir, full_module)
    bindable = _bindable_from_rename_map(rename_map)
    synth = _Synth(cfg, wm, rtl_text, innl, mk, full_module,
                   const_ports=const_ports, bindable=bindable)
    return synth, mk, rtl_text, old_text, wm


_VERILOG_KW = {'begin', 'end', 'if', 'else', 'case', 'casex', 'casez', 'endcase', 'default',
               'for', 'while', 'or', 'and', 'not', 'posedge', 'negedge'}


def selector_folded_conditions(change, ref_dir, jira='chk'):
    """The signals in a comb_net_force change's REGION SELECTOR (the delta-prefix path guard)
    that the step-3 emitter would RE-DERIVE because they are folded out of the netlist and do
    NOT constant-fold away. These are exactly the signals step-2 must resolve via FM
    (find_equivalent_nets) so the emitter can BIND the selector to existing netlist nets
    instead of rebuilding it (the 9666 dsp_cmd_valid / dsp_cnt_end case).

    DETERMINISTIC and SHARED: both the step-2 query deriver (emits these to FM) and the
    step-2 validator (fails if they were not resolved) call this, so they cannot drift.
    Uses the SAME synth (const-port folding, in_netlist, is_reg) the emitter uses, so a
    signal that constant-folds is correctly excluded (no binding needed)."""
    if change.get('change_type') != 'comb_net_force':
        return []
    signal = change.get('signal') or change.get('new_token') or change.get('target')
    module = change.get('module_name') or ''
    if not (signal and module and _Synth and RtlConfig):
        return []
    try:
        synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, jira)
        nt = parse_always(rtl_text, signal)
        ot = parse_always(old_text, signal)
        delta = compute_delta(ot, nt)
    except Exception:
        return []
    if not delta:
        return []
    cfg = synth.cfg
    _SKIP = re.compile(r'_eq_[A-Z0-9_]+$|_orig$|_inv\d*$')
    out, seen = [], set()
    for expr, _sense in (delta.get('prefix') or []):
        for m in re.finditer(r'[A-Za-z_]\w*', expr):
            nm = m.group(0)
            if nm in seen:
                continue
            seen.add(nm)
            if nm in _VERILOG_KW or _SKIP.search(nm):
                continue
            if cfg and nm in cfg.defs:              # macro / opcode constant
                continue
            if synth.in_netlist(nm):                # already a netlist net -> grounds directly
                continue
            if synth._is_reg(nm):                   # flop -> grounds as leaf
                continue
            # Lower it with const-folding: keep ONLY if it re-derives to REAL logic (a signal
            # that folds to a constant, or isn't buildable, needs no FM binding).
            try:
                v = synth.scalar(parse_expr(nm))
            except Exception:
                continue
            if v in ("1'b0", "1'b1"):
                continue
            out.append(nm)
    return out


def _rg_widened_branch(synth, rtl_text, old_text, reg, branch_target):
    """Locate the widened branch of a register guard-change (Intent-A and_term). Returns
    (load_cond, load_val) — the NEW-tree assign whose guard CHANGED vs old (not in the old key
    set) and whose RHS base-name matches `branch_target` (branch_loads/branch_assigns) — or
    (None, None). SHARED by the clock-gate builder, the step-2 query deriver, and the step-3
    validator so all three agree on WHICH branch (and therefore which guard leaves) matter."""
    nt = parse_always(rtl_text, reg)
    ot = parse_always(old_text, reg)
    _base = lambda s: re.sub(r'\s+', '', re.sub(r'\[[^\]]*\]', '', str(s)))
    old_keys = {(_ncond(cc), _nval(vv)) for cc, vv in ot['assigns']}
    for cond, val in nt['assigns']:
        if (_ncond(cond), _nval(val)) in old_keys:
            continue
        if branch_target is None or _base(val) == _base(branch_target):
            return cond, val
    return None, None


def reg_guard_folded_conditions(change, ref_dir, jira='chk'):
    """The signals in a REGISTER guard-change (Intent-A `and_term` on `target_register`)
    WIDENED-BRANCH guard that the step-3 reg_guard_delta builder would RE-DERIVE because they
    are folded out of the netlist. These are exactly the signals step-2 must resolve via FM
    (find_equivalent_nets) so the builder BINDS them to existing nets instead of rebuilding
    (the 9666 WckSyncCtr0 `recdsp_c0cs` case — an internal combinational reg synthesis
    dissolved; rebuilding it costs ~150 gates, binding it costs 0).

    Mirrors selector_folded_conditions (comb_net_force) but keys on the reg-guard widened
    branch. DETERMINISTIC and SHARED: both the step-2 deriver (emits these to FM) and the
    step-2 validator (fails if unresolved) call this, so they cannot drift. Uses the SAME synth
    (const-port folding, in_netlist, is_reg) the builder uses, so a signal that constant-folds
    or is already a netlist net / true flop is correctly excluded (no binding needed)."""
    if change.get('change_type') != 'and_term' or not change.get('target_register'):
        return []
    if change.get('branch_assigns') is None and change.get('branch_loads') is None:
        return []
    reg = change.get('target_register')
    module = change.get('module_name') or ''
    if not (reg and module and _Synth and RtlConfig):
        return []
    try:
        synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, jira)
    except Exception:
        return []
    tgt = change.get('branch_loads') if change.get('branch_loads') is not None else change.get('branch_assigns')
    try:
        load_cond, _load_val = _rg_widened_branch(synth, rtl_text, old_text, reg, tgt)
    except Exception:
        return []
    if load_cond is None:
        return []
    cfg = synth.cfg
    _SKIP = re.compile(r'_eq_[A-Z0-9_]+$|_orig$|_inv\d*$')
    out, seen = [], set()
    for expr, _sense in load_cond:
        for m in re.finditer(r'[A-Za-z_]\w*', expr):
            nm = m.group(0)
            if nm in seen:
                continue
            seen.add(nm)
            if nm in _VERILOG_KW or _SKIP.search(nm):
                continue
            if cfg and nm in cfg.defs:              # macro / opcode constant
                continue
            if synth.in_netlist(nm):                # already a netlist net -> grounds directly
                continue
            if synth._is_reg(nm):                   # true flop -> grounds as leaf
                continue
            try:
                v = synth.scalar(parse_expr(nm))
            except Exception:
                continue
            if v in ("1'b0", "1'b1"):               # constant-folds -> no binding needed
                continue
            out.append(nm)
    return out


def reg_guard_cone_leaves(change, ref_dir, jira='q'):
    """Real netlist-leaf names of a reg-guard-delta (Intent-A `and_term` on a register) clock-gate
    build's GUARD + LOADED-VALUE cone — the nets fenets must resolve per-stage so the re-drive
    applies in P&R (analog of comb_net_force's cone_leaves / priority_force's _pf_cone_leaves).

    Why this exists: the clock-gate builder rebuilds the load guard from RTL (e.g. `recdsp_c0cs`
    is a dissolved internal reg that FM cannot bind, so it is rebuilt), and that rebuild grounds on
    deeper leaves — e.g. `recdsp_c0cs = case(dsp_cmd_msc[7:0])` pulls in `dsp_cmd_msc[*]`. Some of
    those leaves (9666: `dsp_cmd_msc[0]`/`[3]`) are OPTIMIZED AWAY in PrePlace/Route, so without a
    per-stage fenets binding the emitted cone is NET-ABSENT there. Querying every cone leaf (then
    chaining Synth->PP->Route) resolves them. Returns sorted bare/bus-bit leaf names, or []."""
    if change.get('change_type') != 'and_term' or not change.get('target_register'):
        return []
    if change.get('branch_assigns') is None and change.get('branch_loads') is None:
        return []
    reg = change.get('target_register')
    module = change.get('module_name') or ''
    if not (reg and module and _Synth and RtlConfig):
        return []
    try:
        synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, jira)
        tgt = change.get('branch_loads') if change.get('branch_loads') is not None else change.get('branch_assigns')
        load_cond, load_val = _rg_widened_branch(synth, rtl_text, old_text, reg, tgt)
        if load_cond is None:
            return []
        width = wm.get(reg) or 1
        roots = [synth._path_scalar(load_cond)]          # the load-guard cone
        vast = parse_expr(load_val)
        for b in range(width):                           # the loaded value bits (e.g. rdwcksyncclks[b])
            roots.append(synth.bit(vast, b))
    except Exception:
        return []
    driver = {g['output_net']: g for g in synth.gates}
    leaves, seen = set(), set()
    def _walk(net):
        if not isinstance(net, str) or net in seen:
            return
        seen.add(net)
        g = driver.get(net)
        if not g:
            if not net.startswith(('n_eco_', 'eco_')) and not re.match(r"^\d*'[bhdoBHDO]", net):
                leaves.add(net)
            return
        for p, v in (g.get('port_connections') or {}).items():
            if p in ('Z', 'ZN') or not isinstance(v, str):
                continue
            _walk(v)
    for r in roots:
        _walk(r)
    return sorted(leaves)


def _region_of(synth, rtl_text, old_text, signal, wm):
    """Build `signal`'s changed-region logic INTO the given (possibly shared) synth. Returns
    {'region_bits', 'width', 'sel', 'old_region_values'} or None (no delta).

    SURGICAL model:  new_S = (region active) ? fold(subtree, default) : S_old_driver .
    The region selector `sel` is the region's PATH GUARD — the `prefix` path condition that
    encloses every changed entry (e.g. branch3 & dsp_cmd_mop==MRR), lowered from RTL. It is
    NOT `orig == old_value`: that comparison is unsound because a signal takes its old region
    value outside the region too (e.g. a 1-bit valid that is 1 for many commands), which
    aliases the selector to `orig` and collapses the mux to `orig & region` — silently
    corrupting the signal outside the changed region."""
    nt = parse_always(rtl_text, signal)
    ot = parse_always(old_text, signal)
    delta = compute_delta(ot, nt)
    if delta is None:
        return None
    old_region_values = []
    for c, v in ot['assigns']:
        if _starts_with(c, delta['prefix']) and v not in old_region_values:
            old_region_values.append(v)
    try:
        # Region selector = the path guard enclosing the change (empty prefix -> "1'b1",
        # i.e. full-cone rebuild). Sound and independent of the signal's old value.
        sel = synth._path_scalar(delta['prefix'])
        width = wm.get(signal)
        if not width:
            vals = [v for _, v in delta['subtree']] + ([delta['default']] if delta['default'] else [])
            width = max([synth._w(parse_expr(v)) or 1 for v in vals] or [1])
        dflt_ast = parse_expr(delta['default']) if delta['default'] is not None else None
        # branch selectors computed once (hoisted); _path_scalar is memoized so a guard
        # shared with another signal is not rebuilt.
        branch_asts = [(synth._path_scalar(cond), parse_expr(val)) for cond, val in delta['subtree']]
        # BALANCED priority lowering: the subtree folds last-wins (highest priority last),
        # so reverse to highest-first, build one-hot masks ONCE (bit-independent) and per bit
        # OR the masked values. Depth ~log2(N) instead of the nested-mux else-chain ~2N.
        hp = list(reversed(branch_asts))
        sels = [bsel for bsel, _ in hp]
        actives, none = synth._priority_masks(sels)
        region_bits = {}
        for b in range(width):
            terms = [synth._and(actives[k], synth.bit(vast, b))
                     for k, (_, vast) in enumerate(hp)]
            if dflt_ast is not None:
                terms.append(synth._and(none, synth.bit(dflt_ast, b)))
            region_bits[b] = synth._reduce(terms, 'or') if terms else "1'b0"
    except Exception as e:
        raise _CErr(f"delta lowering failed for {signal}: {e}")
    return {'region_bits': region_bits, 'width': width, 'sel': sel,
            'old_region_values': old_region_values, 'summary': delta['summary']}


def lower_delta(ref_dir, module, signal, jira='eco'):
    """Single-signal surgical delta lowering. Returns {'region_bits','width',
    'old_region_values','gates','summary','synth','mk'} or None (no delta)."""
    synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, jira)
    r = _region_of(synth, rtl_text, old_text, signal, wm)
    if r is None:
        return None
    r.update({'gates': synth.gates, 'synth': synth, 'mk': mk})
    return r


def _emit_signal_muxes(synth, mk, r, module, signal, dmaps):
    """Build `signal`'s per-bit force-muxes + driver rewires INTO the given synth (shared
    across signals in a batch). Returns (rewires, errs). The region logic is already in the
    synth (from _region_of); this adds the orig-comparator selector + muxes + rewires."""
    from eco_emit_priority_force import STAGES
    width = r['width']
    old_vals = r.get('old_region_values') or []
    is_bus = width > 1
    errs, rewires = [], []
    _net = lambda b: (f'{signal}[{b}]' if is_bus else signal)
    # pass 1: validate each bit's combinational driver + reserve net_orig
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
        return [], errs
    # SURGICAL selector = the region's path guard (from _region_of). Sound region detection:
    # it is the actual RTL branch condition enclosing the change, NOT `orig == old_value`
    # (which aliases and corrupts the signal outside the region — see _region_of docstring).
    sel = r.get('sel')
    if sel is None:
        return [], [f"{signal}: region selector missing — cannot build the region mux (fail-closed)."]
    nsel = synth._inv(sel)
    # region_get: look up per-bit region value from r['region_bits'] (same lambda as emit_reg_guard_delta_batch)
    region_get = (lambda b, _rb=r['region_bits']: _rb.get(b, "1'b0"))
    # pass 2: per-bit force-mux net[b] = sel ? region[b] : net_orig[b] + driver rewire
    for b in range(width):
        if b not in orig_bit:
            continue
        region = region_get(b)
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
            'old_net': net, 'new_net': net_orig, 'region_sel': sel,
            'confirmed': True, 'force_reapply': True, 'source': 'eco_cone_rebuild',
            'net_force': True, 'driver_side': True,
            'notes': f"comb net-force (surgical): redirect combinational driver of {net} through "
                     f"the region mux (output-pin rename {net}->{net_orig}, per-stage cell).",
        })
    return rewires, errs


def _finalize_study(ref_dir, module, synth, rewires, jira, rename_map, tech_map, summ, prune_roots, errs):
    """Shared tail for the surgical emitters (comb_net_force + register guard-delta): prune
    dead gates to the live cone (`prune_roots`), optional compound-cell tech-map, resolve every
    gate/rewire leaf net per stage (fenets rename-map authoritative, then flat/registered-leaf
    heuristics), default instance names, and fill audit fields. Returns the study-shaped dict.
    Factored out of emit_comb_net_force_batch so the register-D-cone builder reuses the IDENTICAL
    per-stage resolution (no divergence between Intent-A and Intent-B net grounding)."""
    from eco_emit_priority_force import (_stage_net_tokens, _stage_net, _map_stage_net,
                                         _pcstage, STAGES, _module_netlist_body)
    gates = synth.gates
    # PRUNE dead gates: _Synth emits gates eagerly, then _and/_or/_inv fold constants and
    # discard subtrees; the discarded gates linger dangling. Keep ONLY the fan-in cone of the
    # real outputs (prune_roots).
    gates = _prune_to_cone(gates, prune_roots)
    # Depth-reducing compound-cell tech mapping (fail-closed): returns the ORIGINAL gates
    # unchanged if no compound cells resolve or the map isn't provably equivalent.
    if tech_map:
        try:
            import eco_tech_map
            _prot = set()
            for _rw in rewires:            # never fold away a net a rewire still references
                for _k in ('old_net', 'new_net'):
                    if _rw.get(_k):
                        _prot.add(_rw[_k])
            gates = eco_tech_map.tech_map_gates(gates, ref_dir, synth.module, jira, protected_nets=_prot)
        except Exception as _tm_e:
            print(f"  [tech_map] skipped ({type(_tm_e).__name__}: {_tm_e}); using primitive gates")
    # give every cone/mux gate a per-stage view, then resolve leaf nets per stage.
    leaf2key = {}
    for k in (rename_map or {}):
        if k != '_metadata':
            leaf2key.setdefault(k.rsplit('/', 1)[-1], k)
    toks = {st: _stage_net_tokens(ref_dir, module, st) for st in STAGES}
    bodies = {st: _module_netlist_body(ref_dir, module, st) for st in STAGES}
    def _exists(v, st):
        return isinstance(v, str) and (v in toks[st] or v.split('/')[0] in toks[st])
    def _pin_wire_in_cell(cell, pin, st):
        m = re.search(r'\b' + re.escape(cell) + r'\s*\((.*?)\)\s*;', bodies[st], re.S)
        if not m:
            return ''
        pm = re.search(r'\.\s*' + re.escape(pin) + r'\s*\(\s*([A-Za-z_][\w\[\]]*)\s*\)', m.group(1))
        w = pm.group(1) if pm else ''
        return w if (w and _exists(w, st)) else ''
    def _reg_wire_by_constituent(cons, st):
        for cm in re.finditer(r'\b(\w*' + re.escape(cons) + r'\w*)\s*\(', bodies[st]):
            cell = cm.group(1)
            parts = cell.split('_MB_')
            idx = next((j for j, p in enumerate(parts) if p == cons), None)
            if idx is None:
                continue
            pin = 'Q' if len(parts) == 1 else f'Q{idx + 1}'
            w = _pin_wire_in_cell(cell, pin, st)
            if w:
                return w
        return ''
    def _reg_leaf_wire(nn_, st):
        m = re.match(r'^([A-Za-z_]\w*)\[(\d+)\]$', nn_)
        if m:
            cons = f'{m.group(1)}_reg_{m.group(2)}_'
        elif re.match(r'^[A-Za-z_]\w*$', nn_):
            cons = f'{nn_}_reg'
        else:
            return ''
        return _reg_wire_by_constituent(cons, st)
    def _resolve(nn_, st):
        fk = leaf2key.get(nn_)
        if fk:
            mapped = _map_stage_net(nn_, st, fk.rsplit('/', 1)[0], rename_map)
            if (mapped and mapped != nn_ and '/' not in mapped and _exists(mapped, st)):
                return mapped
        flat = _stage_net(nn_, toks[st])
        if _exists(flat, st):
            return flat
        w = _reg_leaf_wire(nn_, st)
        if w:
            return w
        if fk:
            mapped = _map_stage_net(nn_, st, fk.rsplit('/', 1)[0], rename_map)
            if '/' not in (mapped or '/') and _exists(mapped, st):
                return mapped
        return flat
    for g in gates:
        pcs = g.get('port_connections_per_stage') or _pcstage(g['port_connections'])
        for st in STAGES:
            if isinstance(pcs.get(st), dict):
                pcs[st] = {p: _resolve(v, st) for p, v in pcs[st].items()}
        g['port_connections_per_stage'] = pcs
        if not g.get('instance_name'):
            _on = str(g['output_net'])
            if _on.startswith(('n_eco_', 'eco_')):
                g['instance_name'] = _on
            else:
                g['instance_name'] = 'eco_cr_redrive_' + re.sub(r'\W+', '_', _on).strip('_')
    for rw in rewires:
        ops = {st: _resolve(rw['old_net'], st) for st in STAGES}
        rw['old_net_per_stage'] = ops
    for _e in gates + rewires:
        _e.setdefault('source', 'eco_cone_rebuild')
        if not (_e.get('reason') or '').strip():
            _e['reason'] = _e.get('notes') or (
                f"cone-rebuild {_e.get('gate_function') or _e.get('change_type')}")
        if not (_e.get('notes') or '').strip():
            _e['notes'] = _e.get('reason') or "cone-rebuild entry"
    return {'gates': gates, 'rewires': rewires, 'errors': errs, 'summary': summ}


def emit_comb_net_force(ref_dir, module, signal, jira='eco', rename_map=None, tech_map=True):
    """Single-signal comb net-force ECO (wrapper around the batch emitter)."""
    return emit_comb_net_force_batch(ref_dir, module, [signal], jira, rename_map, tech_map=tech_map)


def emit_comb_net_force_batch(ref_dir, module, signals, jira='eco', rename_map=None, tech_map=True):
    """Emit comb net-force for one OR MORE signals in the SAME module through a SHARED synth,
    so common logic (e.g. the WCK-sync guard on recdsp_c0mop AND recdsp_c0vld) is built once
    (synth _sig_cache/_path_cache dedup). Each signal: net[b] = (orig==old_region) ? region
    : net_orig[b], with the original comb driver's output pin renamed per stage. Returns
      {'gates','rewires','errors','summary'} (study-shaped, per-stage resolved once)."""
    from eco_emit_priority_force import (_driver_map, _stage_net_tokens, _stage_net,
                                         _map_stage_net, _pcstage, STAGES,
                                         _module_netlist_body)
    synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, jira, rename_map=rename_map)
    dmaps = {st: _driver_map(ref_dir, module, st) for st in STAGES}
    rewires, errs, summ = [], [], {}
    for signal in signals:
        r = _region_of(synth, rtl_text, old_text, signal, wm)
        if r is None:
            continue
        summ[signal] = r['summary']
        # synth.module is the FULL netlist name (resolved in _synth_setup) — use it for the
        # rewires so they match the studier/other emitters + netlist.
        rw, e = _emit_signal_muxes(synth, mk, r, synth.module, signal, dmaps)
        rewires.extend(rw); errs.extend(e)
    if errs:
        return {'gates': synth.gates, 'rewires': [], 'errors': errs, 'summary': summ}
    # For comb_net_force the live-cone roots are the mux output nets == the rewired signal nets.
    return _finalize_study(ref_dir, module, synth, rewires, jira, rename_map, tech_map, summ,
                           {rw['old_net'] for rw in rewires}, errs)


# ── Intent-A: register guard-change (and_term) deterministic builder ──────────
# An `and_term` that broadens/tightens the guard of an existing branch of a REGISTER's
# always-block (e.g. `else if (... & (mop==RD) & ...) reg <= 1'b0;` widened with `| (mop==MRR)`)
# is NOT a bare `OR2(D_net, new_term)`: whether the netlist combine is OR (broaden a SET branch)
# or AND-NOT (broaden a CLEAR branch) depends on the value the branch ASSIGNS. The old flow let
# the LLM studier hand-build `OR2` regardless → forced the register toward 1 on a clear-branch
# (the JIRA-9666 `postcas`/`WckSyncCtr0` bug). This builder is correct-by-construction: it diffs
# the register's PreEco-vs-new next-state priority tree and re-drives the flop's D via the SAME
# surgical region fold used for comb_net_force (`new_D = sel ? region : old_D`, region built from
# the new tree). The assigned value (0/1/data) is baked into the fold — no operator choice.

def _reg_dpin_per_stage(ref_dir, module, reg, old_tok, rename_map):
    """Per-stage (cell_name, pin, old_net) for the register's `.D` pin. Prefer old_token's fenets
    rename-map '<cell>/<pin>' address (authoritative, carries the Route MB-merge cell + pin, e.g.
    postcas_reg→..._MB_..._0_ , D→D2); else locate the flop in the stage netlist by the D-net, or
    default to '<reg>_reg'/'D'."""
    from eco_emit_priority_force import STAGES, _module_netlist_body
    entry = None
    for k, v in (rename_map or {}).items():
        if k != '_metadata' and isinstance(v, dict) and k.rsplit('/', 1)[-1] == old_tok:
            entry = v
            break
    cellps, pinps, oldps = {}, {}, {}
    for st in STAGES:
        val = (entry or {}).get(st) if isinstance(entry, dict) else None
        aw = (entry or {}).get(f'actual_wire_{st}') if isinstance(entry, dict) else None
        cell = pin = None
        oldnet = aw or (val if (isinstance(val, str) and '/' not in val and val) else old_tok)
        if isinstance(val, str) and '/' in val:
            cell, pin = val.split('/', 1)
        else:
            body = _module_netlist_body(ref_dir, module, st) or ''
            cell, pin = _find_dff_by_dnet(body, oldnet, reg)
        cellps[st] = cell or f'{reg}_reg'
        pinps[st] = pin or 'D'
        oldps[st] = oldnet
    return cellps, pinps, oldps


def _find_dff_by_dnet(body, dnet, reg):
    """Find the flop instance (single or MB-banked) whose D-family pin (.D/.D1../.Dn) connects to
    `dnet`; prefer an instance whose name contains `<reg>_reg`. Returns (cell, pin) or (None,None)."""
    best = (None, None)
    for m in re.finditer(r'([A-Za-z0-9_]+)\s+([A-Za-z0-9_\\]+)\s*\(([^;]*)\)\s*;', body):
        inst, conns = m.group(2), m.group(3)
        pm = re.search(r'\.(D\d*)\s*\(\s*' + re.escape(dnet) + r'\s*\)', conns)
        if not pm:
            continue
        if f'{reg}_reg' in inst:
            return inst, pm.group(1)
        if best == (None, None):
            best = (inst, pm.group(1))
    return best


_CG_CELL_RE = re.compile(r'(ICG|CKLNQ|CKOR|CKAN|CTG|CKGATE|CGL|CKLHQ)', re.I)


def _reg_clockgate(ref_dir, module, reg):
    """If register `reg`'s flop CP is driven by a clock-gate cell, return per-stage
    {stage: (cg_cell_inst, E_pin, E_net)} (read from the PreEco netlist of each stage — the
    clean base the study is applied to); else None. Handles Route MB re-banking (each stage
    parsed independently)."""
    from eco_emit_priority_force import STAGES, _module_netlist_body
    out = {}
    for st in STAGES:
        body = _module_netlist_body(ref_dir, module, st) or ''
        # find reg's flop (first bit) → CP net
        fm = re.search(r'\S*' + re.escape(reg) + r'_reg\S*\s*\(([^;]*)\)\s*;', body)
        if not fm:
            return None
        cp = re.search(r'\.CP\s*\(\s*([^)]*?)\s*\)', fm.group(1))
        if not cp:
            return None
        cpnet = cp.group(1)
        # cell whose Q drives cpnet
        cg = re.search(r'([A-Za-z0-9_]+)\s+(\S+)\s*\(([^;]*\.Q\s*\(\s*' + re.escape(cpnet) + r'\s*\)[^;]*)\)\s*;', body)
        if not cg or not _CG_CELL_RE.search(cg.group(1)):
            return None
        e = re.search(r'\.E\s*\(\s*([^)]*?)\s*\)', cg.group(3))
        if not e:
            return None
        out[st] = (cg.group(2), 'E', e.group(1))
    return out


def _flop_dpins_per_bit(ref_dir, module, reg, width):
    """Per bit b, per stage: (flop_cell_inst, D_pin, D_net) — the register's own flop .D pin,
    read from each stage's PreEco netlist (handles single-bit flops and MB banks incl. Route
    re-banking). Returns {b: {stage: (cell, pin, dnet)}} or None if any bit/stage unresolved."""
    from eco_emit_priority_force import STAGES, _module_netlist_body
    bodies = {st: (_module_netlist_body(ref_dir, module, st) or '') for st in STAGES}
    res = {}
    for b in range(width):
        cons = f'{reg}_reg_{b}_' if width > 1 else f'{reg}_reg'
        res[b] = {}
        for st in STAGES:
            body = bodies[st]
            found = None
            for m in re.finditer(r'([A-Za-z0-9_]+)\s+(\S*' + re.escape(cons) + r'\S*)\s*\(([^;]*)\)\s*;', body):
                inst, conns = m.group(2), m.group(3)
                if 'clk_gate' in inst:
                    continue
                parts = inst.split('_MB_')
                if len(parts) == 1:
                    pin = 'D'
                else:
                    idx = next((j for j, p in enumerate(parts)
                                if re.search(r'(^|/)' + re.escape(reg) + r'_reg_' + str(b) + r'_$', p)), None)
                    if idx is None:
                        continue
                    pin = f'D{idx + 1}'
                dm = re.search(r'\.' + re.escape(pin) + r'\s*\(\s*([^)]*?)\s*\)', conns)
                if dm:
                    found = (inst, pin, dm.group(1))
                    break
            if not found:
                return None
            res[b][st] = found
    return res


def emit_reg_guard_delta_batch(ref_dir, module, changes, jira='eco', rename_map=None, tech_map=True):
    """Emit register guard-change (Intent-A) ECOs for one or more registers in the SAME module
    through a SHARED synth. Each change dict needs {target_register, old_token} (branch_assigns is
    validated upstream). Returns {'gates','rewires','errors','summary'} (study-shaped, per-stage
    resolved) — study UNTOUCHED semantics via the caller on any error."""
    # Distinct net namespace: reg-guard nets become n_eco_<jira>rg_cr_* so they can NEVER collide
    # with the comb_net_force emitter's n_eco_<jira>_cr_* nets — both emitters build through their
    # OWN _synth_setup (independent per-synth `seq` counters), so without a namespace their low-seq
    # 'cr_and_<n>'/'cr_or_<n>' names would overlap when both run in one --emit-into-study.
    synth, mk, rtl_text, old_text, wm = _synth_setup(ref_dir, module, f'{jira}rg', rename_map=rename_map)
    # DRIVER-SIDE net-force (engineer methodology): instead of rewiring the flop's .D pin to a new
    # net (which re-maps the MB-bank D connectivity and breaks FM's multibit/reg register mapping —
    # the JIRA-9666 "logic correct but FM fails" cascade), we KEEP the flop pin on its original .D
    # net and fold the delta UPSTREAM at the net's combinational driver: rename the driver's output
    # <Dnet>-><Dnet>_orig and re-drive <Dnet> through the fold. This is the SAME driver-side pattern
    # comb_net_force (Intent B) + the engineer's Conformal ECO use, and leaves every flop pin intact.
    from eco_emit_priority_force import _driver_map, STAGES
    dmaps = {st: _driver_map(ref_dir, module, st) for st in STAGES}
    def _dnet_driver_ps(net_ps):
        """net_ps: {stage: Dnet}. Return (cps, pps, ok): per-stage combinational driver cell/pin of
        the D-net. ok=False if any stage's D-net is missing or driven by a flop (.Q) — fail-closed."""
        cps, pps = {}, {}
        for st in STAGES:
            dnet = net_ps.get(st)
            sd = dmaps[st].get(dnet) if dnet else None
            if not sd or sd[2]:
                return None, None, False
            cps[st], pps[st] = sd[0], sd[1]
        return cps, pps, True
    rewires, errs, summ = [], [], {}
    roots = set()
    for c in changes:
        reg = c.get('target_register')
        old_tok = c.get('old_token')
        if not reg or not old_tok:
            errs.append(f"reg_guard_delta: change missing target_register/old_token ({c.get('new_token')!r}).")
            continue
        # Rebuild the register's next-state region from the RTL priority tree (correct-by-construction
        # for constant-assign postcas AND data-load counters). _region_of now handles shift/subtract
        # (added to the synth), so a clock-gated counter's full next-state (incl. the decrement) folds
        # correctly. NOTE: a slimmer "surgical loaded-value only" region was tried but proved logically
        # wrong (11/2500 mismatch) for the shallow-prefix counter delta, so the correct full fold is used.
        try:
            r = _region_of(synth, rtl_text, old_text, reg, wm)
        except Exception as e:
            errs.append(f"reg_guard_delta {reg}: region build failed: {e}")
            continue
        if r is None:
            errs.append(f"reg_guard_delta {reg}: no next-state delta between PreEco and new RTL "
                        f"(expected a guard change). Check target_register/RTL.")
            continue
        summ[reg] = r['summary']
        width = r['width']
        sel = r['sel']
        region_get = (lambda b, _rb=r['region_bits']: _rb.get(b, "1'b0"))
        nsel = synth._inv(sel)
        # ── CLOCK-GATED register (e.g. a counter) — needs BOTH .D re-drive AND E widen ──
        # For a simple flop `old_token` is the .D net; for a clock-gated flop it is the clock-gate
        # E net. The old flow OR'd the new term into E ONLY (enable) and left the .D data-select
        # untouched → on the new region the flop enabled but loaded the WRONG value (JIRA-9666
        # WckSyncCtr0: enabled on ==MRR but loaded decrement instead of rdwcksyncclks).
        #
        # SLIM (physical-net hold-mux) fix — do NOT rebuild the whole region from RTL (the earlier
        # full `region_bits` fold pulled in the counter's `cnt-1` ripple subtractor + `==7f` load +
        # hold → ~270 gates, all of which already exist CORRECTLY in silicon). Instead reuse the
        # flop's EXISTING physical .D driver net as the else-leg and only add the widened branch:
        #       new_D[b] = load_active ? load_val[b] : orig-.D[b]
        #       new_E    = old_E | load_active
        # where load_active = _path_scalar(the widened branch's FULL path_cond) — the branch's own
        # priority-correct active mask (path_cond already accumulates higher-priority else-negations,
        # e.g. ~IReset & ~==7f), and load_val = its RHS (e.g. rdwcksyncclks; bit b is just a leaf).
        # This is correct-by-construction: the else-leg IS the FM-passing silicon (already handles the
        # OLD ==RD load + decrement + hold), and where the old narrower guard fired orig-.D already
        # equals load_val, so widening the mux selector never regresses those vectors. (An earlier
        # attempt using the priority-only branch GUARD without the higher-priority negations, or an
        # RTL re-fold of the old state as the else-leg, mismatched 11/2500 — both avoided here.)
        cg = _reg_clockgate(ref_dir, module, reg)
        if cg:
            dbits = _flop_dpins_per_bit(ref_dir, module, reg, width)
            if not dbits:
                errs.append(f"reg_guard_delta {reg}: clock-gated but per-bit .D pins unresolved.")
                continue
            # Locate the widened branch via the SHARED helper (same branch the step-2 deriver +
            # step-3 validator key on — cannot drift).
            tgt = c.get('branch_loads') if c.get('branch_loads') is not None else c.get('branch_assigns')
            load_cond, load_val = _rg_widened_branch(synth, rtl_text, old_text, reg, tgt)
            if load_cond is None:
                errs.append(f"reg_guard_delta {reg}: clock-gated but no widened branch matching "
                            f"branch_loads/branch_assigns={tgt!r} found in new RTL.")
                continue
            try:
                load_active = synth._path_scalar(load_cond)    # full priority-correct active mask
                val_ast = parse_expr(load_val)
            except Exception as e:
                errs.append(f"reg_guard_delta {reg}: widened-branch lowering failed: {e}")
                continue
            nload = synth._inv(load_active)
            bad = False
            for b in range(width):
                inst0, pin0, oldD = dbits[b]['Synthesize']
                dnet_ps = {st: dbits[b][st][2] for st in dbits[b]}
                dcps, dpps, ok = _dnet_driver_ps(dnet_ps)
                if not ok:
                    errs.append(f"reg_guard_delta {reg}[{b}]: D-net {oldD} has no combinational driver "
                                f"in some stage — cannot do driver-side fold (fail-closed).")
                    bad = True; break
                orig = mk(f'{reg}_{b}_orig')
                t_reg = synth._and(load_active, synth.bit(val_ast, b)); t_old = synth._and(nload, orig)
                if t_reg == "1'b0" and t_old == "1'b0":
                    errs.append(f"reg_guard_delta {reg}[{b}]: D folds to constant 0 both legs — refusing.")
                    bad = True; break
                # DRIVER-SIDE: fold OUTPUTS to the original D-net; flop .D pin stays on oldD.
                synth._g(synth.cells['OR2'], 'OR2', {'A1': t_reg, 'A2': t_old, 'Z': oldD})
                roots.add(oldD)
                rewires.append({
                    'change_type': 'rewire', 'instance_name': dcps['Synthesize'], 'cell_name': dcps['Synthesize'],
                    'cell_name_per_stage': dcps, 'module_name': synth.module, 'pin': dpps['Synthesize'],
                    'pin_per_stage': dpps, 'old_net': oldD, 'new_net': orig,
                    'old_net_per_stage': dnet_ps, 'region_sel': load_active,
                    'confirmed': True, 'force_reapply': True, 'source': 'eco_cone_rebuild',
                    'target_register': reg, 'reg_guard_delta': True, 'net_force': True, 'driver_side': True,
                    'notes': f"reg guard-change (clock-gated, DRIVER-SIDE): rename driver of {reg}[{b}] "
                             f".D-net {oldD}->{orig}; re-drive {oldD} = load_active ? load_val : {orig}. "
                             f"Flop .D pin UNCHANGED (engineer methodology; preserves MB-bank mapping).",
                })
            if bad:
                continue
            # widen the clock-gate enable so the flop clocks in the changed region — DRIVER-SIDE:
            # keep the clock-gate .E pin on its original net (old_tok, e.g. N294); rename that net's
            # driver output old_tok->old_tok_orig and re-drive old_tok = old_tok_orig | load_active.
            en_ps = {st: cg[st][2] for st in cg}      # the E net per stage (e.g. N294)
            edcps, edpps, eok = _dnet_driver_ps(en_ps)
            if not eok:
                errs.append(f"reg_guard_delta {reg}: clock-gate E-net {old_tok} has no combinational "
                            f"driver in some stage — cannot do driver-side E widen (fail-closed).")
                continue
            e_orig = mk(f'{reg}_E_orig')
            synth._g(synth.cells['OR2'], 'OR2', {'A1': e_orig, 'A2': load_active, 'Z': old_tok})
            roots.add(old_tok)
            rewires.append({
                'change_type': 'rewire', 'instance_name': edcps['Synthesize'], 'cell_name': edcps['Synthesize'],
                'cell_name_per_stage': edcps, 'module_name': synth.module, 'pin': edpps['Synthesize'],
                'pin_per_stage': edpps, 'old_net': old_tok, 'new_net': e_orig,
                'old_net_per_stage': en_ps, 'region_sel': load_active,
                'confirmed': True, 'force_reapply': True, 'source': 'eco_cone_rebuild',
                'target_register': reg, 'reg_guard_delta': True, 'net_force': True, 'driver_side': True, 'clock_gate_enable_widen': True,
                'notes': f"reg guard-change (clock-gated, DRIVER-SIDE): rename driver of {reg} clock-gate "
                         f"E-net {old_tok}->{e_orig}; re-drive {old_tok} = {e_orig} | load_active. Clock-gate "
                         f".E pin UNCHANGED (engineer methodology).",
            })
            continue
        # ── SIMPLE (non-clock-gated) flop path — DRIVER-SIDE ──
        # Keep the flop .D pin on old_tok (e.g. SEQMAP_NET_1235); rename old_tok's combinational
        # driver output old_tok->old_tok_orig and re-drive old_tok through the region mux. Flop pin
        # unchanged (engineer methodology) — preserves the register's D-connectivity for FM mapping.
        _, _, oldps = _reg_dpin_per_stage(ref_dir, module, reg, old_tok, rename_map)
        is_bus = width > 1
        for b in range(width):
            region = region_get(b)
            old_leaf = (f'{old_tok}[{b}]' if is_bus else old_tok)
            leaf_ps = {st: (f'{oldps.get(st, old_tok)}[{b}]' if is_bus else oldps.get(st, old_tok))
                       for st in STAGES}
            dcps, dpps, ok = _dnet_driver_ps(leaf_ps)
            if not ok:
                errs.append(f"reg_guard_delta {reg}[{b}]: D-net {old_leaf} has no combinational driver "
                            f"in some stage — cannot do driver-side fold (fail-closed).")
                continue
            orig = mk(f'{reg}_{b}_orig') if is_bus else mk(f'{reg}_orig')
            t_reg = synth._and(sel, region)
            t_old = synth._and(nsel, orig)
            if t_reg == "1'b0" and t_old == "1'b0":
                errs.append(f"reg_guard_delta {reg}[{b}]: D folds to constant 0 both legs — refusing.")
                continue
            synth._g(synth.cells['OR2'], 'OR2', {'A1': t_reg, 'A2': t_old, 'Z': old_leaf})
            roots.add(old_leaf)
            rewires.append({
                'change_type': 'rewire', 'instance_name': dcps['Synthesize'],
                'cell_name': dcps['Synthesize'], 'cell_name_per_stage': dcps,
                'module_name': synth.module, 'pin': dpps['Synthesize'], 'pin_per_stage': dpps,
                'old_net': old_leaf, 'new_net': orig, 'old_net_per_stage': leaf_ps, 'region_sel': sel,
                'confirmed': True, 'force_reapply': True, 'source': 'eco_cone_rebuild',
                'target_register': reg, 'reg_guard_delta': True, 'net_force': True, 'driver_side': True,
                'notes': f"reg guard-change (DRIVER-SIDE): rename driver of {reg} .D-net "
                         f"{old_leaf}->{orig}; re-drive {old_leaf} = {sel} ? region : {orig}. Flop .D pin "
                         f"UNCHANGED (engineer methodology; branch-value baked into the fold).",
            })
    if errs:
        return {'gates': synth.gates, 'rewires': [], 'errors': errs, 'summary': summ}
    return _finalize_study(ref_dir, module, synth, rewires, jira, rename_map, tech_map, summ, roots, errs)


def emit_reg_guard_delta_into_study(rtl_diff, study, jira, ref_dir, rename_map=None):
    """Splice every register guard-change `and_term` (change with `target_register`+`branch_assigns`)
    into `study` (all stages) via the deterministic builder. Mirrors emit_into_study; on ANY builder
    error returns (0, errors) with study UNTOUCHED (caller aborts fail-closed)."""
    from eco_emit_priority_force import STAGES
    import collections
    changes = [c for c in rtl_diff.get('changes', [])
               if c.get('change_type') == 'and_term' and c.get('target_register')
               and (c.get('branch_assigns') is not None or c.get('branch_loads') is not None)]
    if not changes:
        return 0, []
    by_mod = collections.OrderedDict()
    for c in changes:
        by_mod.setdefault(c.get('module_name'), []).append(c)
    pending, errs = [], []
    for mod, cs in by_mod.items():
        if not mod:
            errs.append(f"reg_guard_delta: change(s) missing module_name.")
            continue
        try:
            out = emit_reg_guard_delta_batch(ref_dir, mod, cs, jira=jira, rename_map=rename_map)
        except Exception as e:
            errs.append(f"reg_guard_delta {mod}: build failed: {e}")
            continue
        if out['errors']:
            errs.extend(f"reg_guard_delta {mod}: {m}" for m in out['errors'])
            continue
        pending.append(out['gates'] + out['rewires'])
    if errs:
        return 0, errs
    added = 0
    for entries in pending:
        for st in STAGES:
            study.setdefault(st, []).extend(dict(e) for e in entries)
            added += len(entries)
    return added, []


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
    # Group signals by module so all comb_net_force signals in the same module are emitted
    # through ONE shared synth (dedups common logic, e.g. the WCK-sync guard on both
    # recdsp_c0mop and recdsp_c0vld -> ~half the gates).
    import collections
    by_mod = collections.OrderedDict()
    errs = []
    for i, c in enumerate(changes):
        mod = c.get('module_name'); sig = c.get('signal') or c.get('new_token') or c.get('target')
        if not mod or not sig:
            errs.append(f"comb_net_force[{i}]: missing module_name or signal.")
            continue
        by_mod.setdefault(mod, []).append(sig)
    if errs:
        return 0, errs
    pending = []
    for mod, sigs in by_mod.items():
        try:
            out = emit_comb_net_force_batch(ref_dir, mod, sigs, jira=jira, rename_map=rename_map)
        except Exception as e:
            errs.append(f"comb_net_force {mod}/{sigs}: build failed: {e}")
            continue
        if out['errors']:
            errs.extend(f"comb_net_force {mod}: {m}" for m in out['errors'])
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
        # Deterministic surgical emitters, both fail-closed (study UNTOUCHED on any error):
        #  (1) comb_net_force  — combinational net re-drive (Intent-B).
        #  (2) reg_guard_delta — register guard-change .D re-drive (Intent-A). Builds the combine
        #      correct-by-construction (OR vs AND-NOT baked into the region fold) so the studier no
        #      longer hand-builds OR2 into a clear-branch D-net (the JIRA-9666 postcas bug).
        n, errs = emit_into_study(rtl_diff, study, a.jira, a.ref_dir, rename_map=rmap)
        n2, errs2 = ([], []) if errs else emit_reg_guard_delta_into_study(
            rtl_diff, study, a.jira, a.ref_dir, rename_map=rmap)
        allerrs = list(errs) + list(errs2)
        if allerrs:
            marker = ("ECO_SCRIPT_LAUNCHED: eco_cone_rebuild.py --emit-into-study\n"
                      f"  ABORTED — {len(allerrs)} surgical-emitter build error(s):\n"
                      + "".join(f"    - {e}\n" for e in allerrs)
                      + "  Study UNTOUCHED. Fix the comb_net_force / reg_guard_delta change(s) and re-run.\n")
            print(marker)
            open(a.output.replace('.json', '_comb_net_force_marker.txt'), 'w').write(marker)
            sys.exit(2)
        open(a.output, 'w').write(json.dumps(study, indent=2))
        marker = ("ECO_SCRIPT_LAUNCHED: eco_cone_rebuild.py --emit-into-study\n"
                  f"  comb_net_force entries spliced (gates+rewires, all stages): {n}\n"
                  f"  reg_guard_delta entries spliced (gates+rewires, all stages): {n2}\n")
        print(marker)
        open(a.output.replace('.json', '_comb_net_force_marker.txt'), 'w').write(marker)
        sys.exit(0)
    if len(sys.argv) >= 5 and sys.argv[1] == '--emit':
        ref, sig, mod = sys.argv[2], sys.argv[3], sys.argv[4]
        jira = sys.argv[5] if len(sys.argv) > 5 else 'ECO'
        out = emit_comb_net_force(ref, mod, sig, jira=jira)
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
    if len(sys.argv) >= 5 and sys.argv[1] == '--lower':
        ref, sig, mod = sys.argv[2], sys.argv[3], sys.argv[4]
        jira = sys.argv[5] if len(sys.argv) > 5 else 'ECO'
        r = lower_delta(ref, mod, sig, jira=jira)
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
    if len(sys.argv) < 3 or sys.argv[1].startswith('--'):
        sys.exit("usage: eco_cone_rebuild.py <rtl_file> <signal>\n"
                 "   or: eco_cone_rebuild.py --emit  <ref_dir> <signal> <module> [jira]\n"
                 "   or: eco_cone_rebuild.py --lower <ref_dir> <signal> <module> [jira]\n"
                 "   or: eco_cone_rebuild.py --emit-into-study ... (see argparse block)")
    rtl, sig = sys.argv[1], sys.argv[2]
    tree = parse_always(open(rtl, errors='replace').read(), sig)
    print(f"default: {tree['default']}")
    print(f"assigns ({len(tree['assigns'])}), priority = last-wins:")
    for cond, val in tree['assigns']:
        c = ' & '.join((('' if s else '~') + f'({e})') for e, s in cond)
        print(f"  when {c[:120]}  ->  {sig} = {val}")
