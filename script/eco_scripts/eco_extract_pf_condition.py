#!/usr/bin/env python3
"""
eco_extract_pf_condition.py — deterministically ANCHOR a priority_force's condition
to the RTL. Given the force target (signal + forced value/macro), it finds the
`<signal> = <value>` assignment in the RTL and extracts the EXACT guarding
`if(...)` / `else if(...)` condition that governs it — so Step 1 captures the
RIGHT branch instead of a look-alike (9666: it grabbed line 1535's FLDPM/mrsmodok
branch instead of the real CAS force at line 1514).

Why deterministic: the CAS force sits in a deeply-nested priority if/else chain of
near-identical branches; an LLM conflated them. Anchoring to the assignment line
removes the guess.

Output JSON per matched assignment:
  {assignment_line, condition_expr (verbatim, comments stripped, whitespace-normalized),
   leaves (sorted unique identifiers referenced in the condition)}

Usage:
    python3 script/eco_scripts/eco_extract_pf_condition.py \
        --rtl <path/to/rtl_umcrecdsp.v>  (or --ref-dir <REF> --module umcrecdsp) \
        --signal recdsp_c0mop --value UMC_MOP_CAS \
        --output data/<TAG>_pf_condition.json
`--value` may be a macro (UMC_MOP_CAS), a literal (5'b01011), or a plain token.
"""
import argparse, json, os, re, sys

_KEYWORDS = {'if', 'else', 'begin', 'end', 'case', 'endcase', 'default', 'or',
             'and', 'posedge', 'negedge', 'assign', 'always', 'wire', 'reg'}


def _strip_comments(text):
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def _find_module_rtl(ref_dir, module):
    """Locate the RTL source file for a module under <ref>/data/PreEco/SynRtl."""
    root = os.path.join(ref_dir, 'data', 'PreEco', 'SynRtl')
    base = re.sub(r'^ddrss_\w+?_t_', '', module)  # strip tile prefix if present
    for cand in (f'rtl_{base}.v', f'{base}.v', f'rtl_{module}.v'):
        p = os.path.join(root, cand)
        if os.path.isfile(p):
            return p
    # fallback: grep for a `module <base>` declaration
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith('.v'):
                p = os.path.join(dirpath, fn)
                try:
                    if re.search(r'^\s*module\s+' + re.escape(base) + r'\b',
                                 open(p, errors='replace').read(), re.MULTILINE):
                        return p
                except Exception:
                    pass
    return None


def _guard_condition(lines, assign_idx):
    """Walk up from the assignment line to the `begin` that opens its block, then
    extract the paren-balanced if/else-if condition preceding that begin.
    Returns the verbatim condition string, or None."""
    # 1. find the 'begin' opening our block: scan upward, tokens right-to-left,
    #    balance += for 'end', -= for 'begin'; first time balance goes < 0 that
    #    'begin' opens the block containing the assignment.
    balance = 0
    tok_re = re.compile(r'\b(begin|end)\b')
    begin_line = begin_pos = None
    for j in range(assign_idx, -1, -1):
        toks = list(tok_re.finditer(lines[j]))
        # on the assignment line itself, only consider tokens BEFORE the assignment?
        # keep it simple: consider all tokens on lines above; on assign line, tokens
        # before are rare — process all right-to-left.
        for m in reversed(toks):
            if m.group(1) == 'end':
                balance += 1
            else:  # begin
                balance -= 1
                if balance < 0:
                    begin_line, begin_pos = j, m.start()
                    break
        if begin_line is not None:
            break
    if begin_line is None:
        return None
    # 2. build a backward text window up to the begin, find the guard if/else-if.
    #    Concatenate from a few lines above the begin_line through the begin.
    win_start = max(0, begin_line - 40)
    # text from win_start..begin_line, cutting the begin line at begin_pos
    parts = lines[win_start:begin_line] + [lines[begin_line][:begin_pos]]
    blob = '\n'.join(parts)
    # find the LAST 'if' / 'else if' whose '(' ... ')' closes right before begin.
    # Strategy: locate all 'if' keywords, for each take the paren group; the guard
    # is the one whose matching ')' is closest to the end of blob.
    best = None
    for m in re.finditer(r'\bif\b', blob):
        # the '(' after this if
        k = m.end()
        while k < len(blob) and blob[k] in ' \t\n':
            k += 1
        if k >= len(blob) or blob[k] != '(':
            continue
        depth = 0; end = None
        for p in range(k, len(blob)):
            if blob[p] == '(':
                depth += 1
            elif blob[p] == ')':
                depth -= 1
                if depth == 0:
                    end = p; break
        if end is None:
            continue
        cond = blob[k + 1:end]
        # the guard is the if-condition closest to (ending nearest) the begin
        if best is None or end > best[0]:
            best = (end, cond)
    if not best:
        return None
    return re.sub(r'\s+', ' ', best[1]).strip()


def _leaves(cond):
    """Unique identifiers in the condition (signals + macros), minus keywords."""
    ids = set()
    # backtick macros
    for m in re.findall(r'`(\w+)', cond):
        ids.add('`' + m)
    # plain identifiers (not numbers, not inside `...` already captured)
    tmp = re.sub(r'`\w+', ' ', cond)
    for m in re.findall(r'[A-Za-z_]\w*', tmp):
        if m not in _KEYWORDS:
            ids.add(m)
    return sorted(ids)


def resolve_rtl(rtl=None, ref_dir=None, module=None):
    """Return the RTL file path from an explicit --rtl or (ref_dir, module)."""
    if rtl and os.path.isfile(rtl):
        return rtl
    if ref_dir and module:
        return _find_module_rtl(ref_dir, module)
    return None


def extract_condition(rtl_path, signal, value):
    """IMPORTABLE. Given an RTL file, the forced signal, and its value/macro, find
    every `<signal> = <value>` assignment and return the guarding condition of each:
      [{assignment_line, condition_expr, leaves}]
    Reused by eco_emit_priority_force (to build the cone) and by the validators
    (completeness check). Returns [] if the file is unreadable or nothing matches."""
    if not rtl_path or not os.path.isfile(rtl_path):
        return []
    lines = _strip_comments(open(rtl_path, errors='replace').read()).split('\n')
    val = str(value).lstrip('`')
    assign_re = re.compile(r'\b' + re.escape(signal) + r'\s*(?:<=|=)\s*`?' + re.escape(val) + r'\b')
    out = []
    for i, ln in enumerate(lines):
        if assign_re.search(ln):
            cond = _guard_condition(lines, i)
            out.append({'assignment_line': i + 1, 'condition_expr': cond,
                        'leaves': _leaves(cond) if cond else []})
    return out


def branch_assignments(rtl_path, signal, value):
    """IMPORTABLE. Return the sorted set of LHS signal names assigned in the SAME
    begin/end block as the `<signal> = <value>` anchor — i.e. every signal the
    forced branch drives. Used to verify forced_signals completeness (a dropped
    driven signal is a silent-wrong ECO). Returns [] if unreadable / no match."""
    if not rtl_path or not os.path.isfile(rtl_path):
        return []
    lines = _strip_comments(open(rtl_path, errors='replace').read()).split('\n')
    val = str(value).lstrip('`')
    assign_re = re.compile(r'\b' + re.escape(signal) + r'\s*(?:<=|=)\s*`?' + re.escape(val) + r'\b')
    tok_re = re.compile(r'\b(begin|end)\b')
    asg_re = re.compile(r'(\w+)\s*(?:\[[^\]]*\])?\s*(?:<=|=)(?!=)')
    names = set()
    for i, ln in enumerate(lines):
        if not assign_re.search(ln):
            continue
        # opening begin of the anchor's block (same balance walk as _guard_condition)
        balance = 0; begin_line = begin_pos = None
        for j in range(i, -1, -1):
            for m in reversed(list(tok_re.finditer(lines[j]))):
                if m.group(1) == 'end':
                    balance += 1
                else:
                    balance -= 1
                    if balance < 0:
                        begin_line, begin_pos = j, m.start(); break
            if begin_line is not None:
                break
        if begin_line is None:
            continue
        # forward to the matching end
        depth = 0; started = False; end_line = None
        for j in range(begin_line, len(lines)):
            seg = lines[j][begin_pos:] if j == begin_line else lines[j]
            for m in tok_re.finditer(seg):
                if m.group(1) == 'begin':
                    depth += 1; started = True
                else:
                    depth -= 1
                    if started and depth == 0:
                        end_line = j; break
            if end_line is not None:
                break
        if end_line is None:
            end_line = len(lines) - 1
        # collect LHS names of blocking/nonblocking assigns in [begin_line, end_line]
        for j in range(begin_line, end_line + 1):
            seg = lines[j][begin_pos:] if j == begin_line else lines[j]
            for m in asg_re.finditer(seg):
                nm = m.group(1)
                if nm not in _KEYWORDS:
                    names.add(nm)
    return sorted(names)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl')
    ap.add_argument('--ref-dir')
    ap.add_argument('--module')
    ap.add_argument('--signal', required=True)
    ap.add_argument('--value', required=True, help='macro (UMC_MOP_CAS), literal, or token')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    rtl = resolve_rtl(args.rtl, args.ref_dir, args.module)
    if not rtl or not os.path.isfile(rtl):
        print(f"ECO_SCRIPT_LAUNCHED: eco_extract_pf_condition.py\n  ERROR: RTL not found "
              f"(rtl={args.rtl} ref-dir={args.ref_dir} module={args.module})")
        json.dump({'error': 'rtl_not_found', 'matches': []}, open(args.output, 'w'), indent=2)
        return 2

    matches = extract_condition(rtl, args.signal, args.value)

    out = {'rtl': rtl, 'signal': args.signal, 'value': args.value,
           'match_count': len(matches), 'matches': matches}
    json.dump(out, open(args.output, 'w'), indent=2)
    print("ECO_SCRIPT_LAUNCHED: eco_extract_pf_condition.py")
    print(f"  rtl: {rtl}")
    print(f"  {args.signal} = {args.value}: {len(matches)} assignment(s)")
    for m in matches:
        print(f"    line {m['assignment_line']}: {m['condition_expr']}")
    return 0 if matches else 1


if __name__ == '__main__':
    sys.exit(main())
