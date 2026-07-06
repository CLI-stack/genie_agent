#!/usr/bin/env python3
"""
eco_rtl_synth.py — a bounded, recursive Verilog RTL synthesizer for ECO cone rebuild.

Parses the RTL *control-logic* expression subset into an AST and (in the lowering,
S2) recursively synthesizes gates, grounding at real netlist nets. This is what lets
a combinational-cone ECO (e.g. a priority-chain guard change) be rebuilt fully — the
selector's context signals (themselves computed by procedural blocks) are rebuilt
recursively instead of failing to ground.

S1 (this file, first phase): expression parser + width inference.

Supported expression constructs:
  ternary  c ? a : b
  logical  || &&           (1-bit)
  equality == !=           (1-bit)
  relational < > <= >=     (1-bit)   [lowered later only if needed; parsed here]
  bitwise  | ^ ~^ &        (bus)
  reductions & | ^ ~& ~| ~^ ^~  (unary, 1-bit)
  unary    ~ ! -
  arithmetic + -           (parsed; lowering may fail-closed)
  concat   {a, b, ...}
  replication {N{x}}
  select   x[i] · x[msb:lsb] · x[base +: w] · x[base -: w]
  constants  N'bxxx / N'hxx / decimal · macros `NAME
  signals    bare identifiers

Raises _SErr fail-closed on anything outside this subset.

AST nodes (tuples):
  ('num', width, value|None, raw)      value None if x/z present
  ('id',  name)
  ('bit', node, idx_expr)
  ('part', node, msb_expr, lsb_expr)
  ('idxpart', node, base_expr, width_int, up_bool)
  ('un', op, a)          op in ~ ! -
  ('red', op, a)         op in & | ^ ~& ~| ~^
  ('bin', op, a, b)
  ('tern', c, a, b)
  ('concat', [nodes])
  ('rep', n_int, node)
"""
import re


class _SErr(Exception):
    pass


# ── tokenizer ────────────────────────────────────────────────────────────────
_TOKSPEC = [
    ('WS',   r'\s+'),
    ('NUM',  r"\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+|\d+"),
    ('ID',   r"`?[A-Za-z_]\w*"),
    ('IPU',  r'\+:'),
    ('IPD',  r'-:'),
    ('REL',  r'<=|>=|<|>'),
    ('EQ',   r'==|!='),
    ('LAND', r'&&'),
    ('LOR',  r'\|\|'),
    ('RNAND', r'~&'), ('RNOR', r'~\|'), ('RXNOR', r'~\^|\^~'),
    ('AND',  r'&'), ('OR', r'\|'), ('XOR', r'\^'),
    ('NOT',  r'~'), ('LNOT', r'!'),
    ('PLUS', r'\+'), ('MINUS', r'-'),
    ('Q',    r'\?'), ('COLON', r':'),
    ('LB',   r'\['), ('RB', r'\]'),
    ('LC',   r'\{'), ('RC', r'\}'),
    ('LP',   r'\('), ('RP', r'\)'),
    ('COMMA', r','),
]
_TOKRE = re.compile('|'.join(f'(?P<{n}>{p})' for n, p in _TOKSPEC))


def tokenize(expr):
    out, i = [], 0
    while i < len(expr):
        m = _TOKRE.match(expr, i)
        if not m:
            raise _SErr(f"unlexable at {expr[i:i+24]!r}")
        i = m.end()
        if m.lastgroup == 'WS':
            continue
        out.append((m.lastgroup, m.group()))
    return out


# binary operator precedence (higher binds tighter)
_BINPREC = {
    'LOR': 1, 'LAND': 2, 'OR': 3, 'XOR': 4, 'RXNOR': 4, 'AND': 5,
    'EQ': 6, 'REL': 7, 'PLUS': 8, 'MINUS': 8,
}
_UNARY = {'NOT', 'LNOT', 'MINUS', 'AND', 'OR', 'XOR', 'RNAND', 'RNOR', 'RXNOR'}
_REDMAP = {'AND': '&', 'OR': '|', 'XOR': '^', 'RNAND': '~&', 'RNOR': '~|', 'RXNOR': '~^'}


def parse_expr(expr):
    """Parse a Verilog expression string -> AST. Raises _SErr fail-closed."""
    toks = tokenize(expr)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def eat(kind=None):
        k, v = peek()
        if kind and k != kind:
            raise _SErr(f"expected {kind}, got {k} {v!r}")
        pos[0] += 1
        return v

    def p_ternary():
        c = p_binary(1)
        if peek()[0] == 'Q':
            eat('Q'); a = p_ternary(); eat('COLON'); b = p_ternary()
            return ('tern', c, a, b)
        return c

    def p_binary(minp):
        left = p_unary()
        while True:
            k, v = peek()
            p = _BINPREC.get(k)
            if p is None or p < minp:
                return left
            eat(k)
            right = p_binary(p + 1)
            left = ('bin', v, left, right)

    def p_unary():
        k, v = peek()
        if k in _UNARY:
            eat(k)
            operand = p_unary()
            if k in ('NOT', 'LNOT', 'MINUS'):
                return ('un', v, operand)
            return ('red', _REDMAP[k], operand)   # reduction
        return p_postfix()

    def p_postfix():
        node = p_primary()
        while peek()[0] == 'LB':
            node = p_select(node)
        return node

    def p_select(node):
        eat('LB')
        a = p_ternary()
        k, _ = peek()
        if k == 'RB':
            eat('RB'); return ('bit', node, a)
        if k == 'COLON':
            eat('COLON'); b = p_ternary(); eat('RB'); return ('part', node, a, b)
        if k in ('IPU', 'IPD'):
            up = (k == 'IPU'); eat(k); w = p_ternary(); eat('RB')
            return ('idxpart', node, a, w, up)   # width resolved at lowering (cfg/macros)
        raise _SErr(f"bad select syntax near {k}")

    def p_primary():
        k, v = peek()
        if k == 'NUM':
            eat('NUM'); return _num_node(v)
        if k == 'ID':
            eat('ID'); return ('id', v)
        if k == 'LP':
            eat('LP'); e = p_ternary(); eat('RP'); return e
        if k == 'LC':
            return p_concat()
        raise _SErr(f"unexpected token {k} {v!r}")

    def p_concat():
        eat('LC')
        first = p_ternary()
        k, _ = peek()
        if k == 'LC':          # replication {N{x}}
            n = _const_int(first)
            if n is None:
                raise _SErr("replication count not constant")
            eat('LC'); inner = p_ternary(); eat('RC'); eat('RC')
            return ('rep', n, inner)
        parts = [first]
        while peek()[0] == 'COMMA':
            eat('COMMA'); parts.append(p_ternary())
        eat('RC')
        return ('concat', parts)

    ast = p_ternary()
    if pos[0] != len(toks):
        raise _SErr(f"trailing tokens {toks[pos[0]:]}")
    return ast


def _num_node(raw):
    m = re.match(r"^(\d*)'[sS]?([bBoOdDhH])([0-9a-fA-FxXzZ_]+)$", raw)
    if m:
        width = int(m.group(1)) if m.group(1) else None
        base = m.group(2).lower(); digits = m.group(3).replace('_', '')
        if re.search(r'[xXzZ]', digits):
            return ('num', width, None, raw)
        val = int(digits, {'b': 2, 'o': 8, 'd': 10, 'h': 16}[base])
        if width is None:
            width = max(1, val.bit_length())
        return ('num', width, val, raw)
    if re.match(r'^\d+$', raw):
        val = int(raw)
        return ('num', max(1, val.bit_length()), val, raw)
    raise _SErr(f"bad number {raw!r}")


def _const_int(node):
    """Return the integer value of a constant AST node, or None if not constant."""
    if node[0] == 'num':
        return node[2]
    return None


# ── width inference ──────────────────────────────────────────────────────────
def build_width_map(rtl_text, macros=None):
    """Map signal name -> bit width from module declarations. macros: dict name->int
    to resolve `MACRO bounds in [msb:lsb]."""
    wm = {}
    txt = re.sub(r'//[^\n]*', '', rtl_text)
    txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.DOTALL)
    decl = re.compile(r'\b(?:input|output|inout|wire|reg|logic)\b\s*(?:signed\s*)?'
                      r'(?:\[\s*([^\]:]+?)\s*:\s*([^\]]+?)\s*\]\s*)?'
                      r'([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*[;,)]')
    for m in decl.finditer(txt):
        msb, lsb = m.group(1), m.group(2)
        names = [x.strip() for x in m.group(3).split(',')]
        if msb is None:
            w = 1
        else:
            hi = _eval_bound(msb, macros); lo = _eval_bound(lsb, macros)
            w = (abs(hi - lo) + 1) if (hi is not None and lo is not None) else None
        for nm in names:
            if nm and (nm not in wm or wm[nm] is None):
                wm[nm] = w
    return wm


def _eval_bound(expr, macros):
    e = expr.strip()
    if macros:
        for _ in range(6):
            m = re.search(r'`?(\w+)', e)
            done = True
            for mm in re.finditer(r'`?([A-Za-z_]\w*)', e):
                nm = mm.group(1)
                if nm in macros:
                    e = e[:mm.start()] + str(macros[nm]) + e[mm.end():]
                    done = False
                    break
            if done:
                break
    if re.fullmatch(r'[0-9()+\-*/ ]+', e):
        try:
            return int(eval(e, {'__builtins__': {}}))
        except Exception:
            return None
    m = re.fullmatch(r'\d+', e)
    return int(e) if m else None


def width_of(node, wm, macros=None):
    """Infer the bit width of an AST node. Returns int or None (unknown)."""
    t = node[0]
    if t == 'num':
        return node[1]
    if t == 'id':
        return wm.get(node[1].lstrip('`'), None)
    if t == 'bit':
        return 1
    if t == 'part':
        hi = _const_int(node[2]); lo = _const_int(node[3])
        return abs(hi - lo) + 1 if (hi is not None and lo is not None) else None
    if t == 'idxpart':
        return _const_int(node[3])   # int if width literal; else resolved at lowering
    if t == 'un':
        return 1 if node[1] == '!' else width_of(node[2], wm, macros)
    if t == 'red':
        return 1
    if t == 'bin':
        op = node[1]
        if op in ('&&', '||', '==', '!=', '<', '>', '<=', '>='):
            return 1
        a = width_of(node[2], wm, macros); b = width_of(node[3], wm, macros)
        return max([x for x in (a, b) if x is not None], default=None)
    if t == 'tern':
        a = width_of(node[2], wm, macros); b = width_of(node[3], wm, macros)
        return max([x for x in (a, b) if x is not None], default=None)
    if t == 'concat':
        ws = [width_of(p, wm, macros) for p in node[1]]
        return sum(ws) if all(w is not None for w in ws) else None
    if t == 'rep':
        w = width_of(node[2], wm, macros)
        return node[1] * w if w is not None else None
    return None


if __name__ == '__main__':
    import sys, json
    e = sys.argv[1]
    ast = parse_expr(e)
    print(json.dumps(ast, indent=1, default=str))
