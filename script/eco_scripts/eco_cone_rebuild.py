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


if __name__ == '__main__':
    import sys, json
    rtl, sig = sys.argv[1], sys.argv[2]
    tree = parse_always(open(rtl, errors='replace').read(), sig)
    print(f"default: {tree['default']}")
    print(f"assigns ({len(tree['assigns'])}), priority = last-wins:")
    for cond, val in tree['assigns']:
        c = ' & '.join((('' if s else '~') + f'({e})') for e, s in cond)
        print(f"  when {c[:120]}  ->  {sig} = {val}")
