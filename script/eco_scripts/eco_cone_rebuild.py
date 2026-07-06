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


def _strip_comments(text):
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


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


def parse_always(rtl_text, signal):
    """Priority-value tree for `signal` from its `always` block in rtl_text.
    Returns {'default': value|None, 'assigns': [(path_cond, value), ...]} where
    path_cond is a list of (expr, sense) AND-terms. Raises _CErr fail-closed."""
    text = _strip_comments(rtl_text)
    body = _find_always_block(text, signal)
    p = _Parser(signal)
    p.parse(body, [])
    return {'default': p.default, 'assigns': p.assigns}


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


# ── LOWER: delta -> gates (constant-value subtree => per-bit boolean) ─────────
try:
    from eco_emit_priority_force import (synthesize_condition, _PErr as _PF,
                                         _const_bits, _module_netlist_body, _mod_key)
    from eco_rtl_config import RtlConfig
except Exception:
    synthesize_condition = _const_bits = _module_netlist_body = _mod_key = RtlConfig = None
    _PF = Exception


def _cond_to_expr(cond):
    """(expr,sense) AND-terms -> a single condition string for synthesize_condition."""
    if not cond:
        return "1'b1"
    return ' & '.join((f'({e})' if s else f'~({e})') for e, s in cond)


def lower_delta(ref_dir, module, signal, jira='eco'):
    """Rebuild the DELTA of `signal`'s cone (NEW vs OLD RTL) as gates. Only supports a
    CONSTANT-valued subtree (opcodes/enums) — the per-bit value is then pure boolean:
      region_bit[b] = OR over subtree branches whose value has bit b set (branches are
      mutually exclusive via the parser's negations) | default_bit[b].
    Returns {'selector', 'region_bits':{b:net}, 'width', 'gates', 'orig'} or None
    (no delta). Raises _CErr fail-closed on anything unsupported (non-const value, etc.)."""
    if not (synthesize_condition and RtlConfig):
        raise _CErr("synthesizer unavailable")
    base = _mod_key(module) if _mod_key else re.sub(r'^ddrss_\w+?_t_', '', module)
    from eco_extract_pf_condition import resolve_rtl
    new_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='SynRtl')
    old_rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='PreEco/SynRtl')
    if not (new_rtl and old_rtl):
        raise _CErr(f"cannot locate {signal} RTL in both trees")
    nt = parse_always(open(new_rtl, errors='replace').read(), signal)
    ot = parse_always(open(old_rtl, errors='replace').read(), signal)
    delta = compute_delta(ot, nt)
    if delta is None:
        return None
    cfg = RtlConfig(ref_dir)
    nlbody = _module_netlist_body(ref_dir, module) if _module_netlist_body else ''
    innl = (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', nlbody))) if nlbody else (lambda s: True)
    rtl_text = open(new_rtl, errors='replace').read()
    seq = [0]
    def mk(t):
        seq[0] += 1
        return f'n_eco_{jira}_cr_{t}_{seq[0]}'
    gates = []
    def synth(expr):
        cn, gg = synthesize_condition(expr, jira, module, cfg, mk, rtl_text=rtl_text, in_netlist=innl)
        gates.extend(gg)
        return cn
    # selector = the region prefix
    selector = synth(_cond_to_expr(delta['prefix']))
    # subtree branches -> (cond_net, const_bits) ; require constant values
    branches = []
    for suffix, value in delta['subtree']:
        cb = _const_bits(value) or (_const_bits(cfg.defs.get(str(value).lstrip('`'))) if cfg else None)
        if not cb:
            raise _CErr(f"non-constant subtree value {value!r} (delta rebuild supports "
                        f"constant/enum branches only)")
        branches.append((synth(_cond_to_expr(suffix)), cb))
    dfl = _const_bits(delta['default']) or (_const_bits(cfg.defs.get(str(delta['default']).lstrip('`'))) if cfg and delta['default'] else None)
    width = max([len(cb) for _, cb in branches] + ([len(dfl)] if dfl else []) or [1])
    # per bit: OR of branch conds where that opcode bit == 1 (branches mutually exclusive)
    region_bits = {}
    for b in range(width):
        ones = []
        for cn, cb in branches:
            bit = cb[len(cb) - 1 - b] if b < len(cb) else '0'
            if bit == '1':
                ones.append(cn)
        dbit = (dfl[len(dfl) - 1 - b] if dfl and b < len(dfl) else '0')
        if dbit == '1':
            # default contributes when NO branch matches: default & ~OR(all branch conds)
            allc = [cn for cn, _ in branches]
            nm = _or_nets(allc, gates, mk, cfg) if allc else None
            term = _and2(_inv_net(nm, gates, mk, cfg), None, gates, mk, cfg) if nm else "1'b1"
            ones = ones + ([term] if term != "1'b1" else [])
        region_bits[b] = _or_nets(ones, gates, mk, cfg) if ones else "1'b0"
    return {'selector': selector, 'region_bits': region_bits, 'width': width,
            'gates': gates, 'summary': delta['summary']}


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


if __name__ == '__main__':
    import sys, json
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
        print(f"width={r['width']} gates={len(r['gates'])} selector={r['selector']}")
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
