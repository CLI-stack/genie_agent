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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rtl')
    ap.add_argument('--ref-dir')
    ap.add_argument('--module')
    ap.add_argument('--signal', required=True)
    ap.add_argument('--value', required=True, help='macro (UMC_MOP_CAS), literal, or token')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    rtl = args.rtl
    if not rtl and args.ref_dir and args.module:
        rtl = _find_module_rtl(args.ref_dir, args.module)
    if not rtl or not os.path.isfile(rtl):
        print(f"ECO_SCRIPT_LAUNCHED: eco_extract_pf_condition.py\n  ERROR: RTL not found "
              f"(rtl={args.rtl} ref-dir={args.ref_dir} module={args.module})")
        json.dump({'error': 'rtl_not_found', 'matches': []}, open(args.output, 'w'), indent=2)
        return 2

    text = _strip_comments(open(rtl, errors='replace').read())
    lines = text.split('\n')
    val = args.value.lstrip('`')
    # match `<signal> = <value>` or `<signal> <= <value>`; value may carry a backtick
    assign_re = re.compile(r'\b' + re.escape(args.signal) + r'\s*(?:<=|=)\s*`?' + re.escape(val) + r'\b')
    matches = []
    for i, ln in enumerate(lines):
        if assign_re.search(ln):
            cond = _guard_condition(lines, i)
            matches.append({
                'assignment_line': i + 1,
                'condition_expr': cond,
                'leaves': _leaves(cond) if cond else [],
            })

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
