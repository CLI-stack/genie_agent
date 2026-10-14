#!/usr/bin/env python3
"""
eco_verify_cone_equivalence.py — deterministic, mechanical SAT-based proof that a
PRE-EXISTING real net's entire fan-in cone computes EXACTLY the same Boolean function
as a given RTL-level target expression.

RELATIONSHIP TO eco_verify_boolean_fold.py (this is NOT a replacement for it):
  eco_verify_boolean_fold.py verifies a small, NEWLY-BUILT gate chain (a handful of
  --gates the caller already identified) against a target expressed over operands the
  caller already knows are real -- the normal Method A/B construction-time check. Use
  that tool for that job.

  This tool is for a different, heavier job: proving or refuting a claimed identity
  between a net that ALREADY EXISTS in the real netlist (the caller did not build it)
  and an RTL-level formula, by recursively DISCOVERING and expanding the net's ENTIRE
  real fan-in cone -- potentially hundreds of gates, auto-walked back to true register
  Q-outputs / primary inputs / unsupported-cell boundaries -- not a short caller-supplied
  list. Use this tool when validating whether a candidate net found by structural
  tracing or Formality really is what it's claimed to be (e.g. a Method-B redirect
  boundary, or the module-scope-provenance "touched-but-different-module" exception in
  eco_netlist_verifier.md Check 15).

WHY THIS EXISTS: eco_verify_boolean_fold.py's brute-force enumeration is intractable
past a few dozen free variables -- a real investigation hit exactly this wall (an
18-bit-wide register compare alone is 2^18 combinations before counting anything else)
and, lacking a scalable alternative, fell back to hand-reasoning about what a candidate
net "must" compute instead of a mechanical proof. This tool replaces brute force with a
SAT solver (z3), which scales to hundreds of free variables, so there is never a reason
to fall back to hand-reasoning for being "too big to brute-force."

THE OTHER REAL INCIDENT THIS TOOL IS BUILT FROM: an ad hoc verification attempt needed
to express a claimed target formula that included a sub-term (an internal compare
embedded in a wider shared net) which could not be located anywhere in the candidate
net's own real fan-in cone. Rather than stopping, it was modeled as a fresh,
UNCONSTRAINED free variable with zero connection to anything else -- and a SAT check
against a target expression containing a free variable independent of everything else
is close to a GUARANTEED mismatch by construction, regardless of whether the real
circuit is correct. The "proof" that resulted was meaningless, not a real refutation.
This tool makes that mistake structurally impossible: every identifier referenced in
--target-expr is validated against the real leaf set the cone expansion actually
discovered, and the run HARD-ABORTS (exit 2) before ever reaching the SAT check if any
identifier is not a real, reachable leaf of --target-net's own cone. If an operand you
believe should matter is missing from the leaf list, that is the tool telling you to go
investigate where it really enters (a sub-module boundary the cone extraction didn't
cross, a different net name due to P&R renaming, etc.) -- never a license to invent it.

Scope and limits (by design, not oversight):
  - This does NOT parse Verilog/RTL expressions on its own. --target-expr is a plain
    Python boolean expression (using &, |, ~, and parentheses -- NOT the Python
    keywords and/or/not, which do not work correctly against z3 symbolic values) over
    the SAME real leaf net names the cone expansion discovers. Transcribing an RTL
    clause into that form, with every operand resolved to a real net name, is the
    caller's responsibility.
  - "Module" here means a single already-extracted module body (same convention as
    eco_lol_impact.parse_modules/parse_instances) -- this tool does not itself cross
    into a child instance's own body. If the real structure crosses a sub-module
    boundary, pre-extract that child module and query it separately (same manual
    technique used to resolve the real incident that motivated the Check 15
    module-scope provenance rule).
  - A cell type with no truth table available (eco_cell_truth_tables.py returns None)
    makes that net's output a free leaf, same as a register Q-output or a true primary
    input -- it does not abort the run. Only an --target-expr identifier that is not a
    discovered leaf aborts.

Usage:
    python3 eco_verify_cone_equivalence.py \\
        --ref-dir   <REF_DIR> --stage Synthesize --module <netlist_module_name> \\
        --target-net   <real_net_name_already_in_the_netlist> \\
        --target-expr  "RecCmdVld_d2 & mop_eq_act_expr & ~some_other_real_leaf" \\
        --result    /tmp/<tag>_<entry>_coneequiv.json

One target net per invocation (same granularity as eco_verify_boolean_fold.py's one
--output-net per run) -- checking an Open/Close pair means two invocations.

Exit code: 0 = match (SAT solver proves equivalence for every possible assignment of
the real leaves — UNSAT on the XOR check), 1 = mismatch (SAT on the XOR check — a real,
concrete counterexample exists, captured in --result), 2 = error (module/net not found,
unsupported parse, combinational loop, OR an ungrounded --target-expr identifier — see
--result's `error_kind` field to tell these apart). Callers MUST treat any non-zero
exit as "not verified."

NOTE on counterexamples: unlike eco_verify_boolean_fold.py's brute-force enumeration
(which can list every mismatching row, up to 16), a SAT solver returns exactly ONE
satisfying assignment per check, not an exhaustive list — --result's `counterexample`
field is a single model, not a list.
"""

import argparse
import gzip
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eco_lol_impact as lol
import eco_cell_truth_tables as ett

try:
    import z3
except ImportError:
    z3 = None


def _abort(result_path, msg, error_kind="error"):
    """Write both stderr and a persistent marker file for an aborted run, so the
    flow's audit trail (grep ECO_SCRIPT_LAUNCHED / ls *_marker.txt) shows this
    script ran even when it errored out — same convention as
    eco_verify_boolean_fold.py / eco_emit_uniquify.py."""
    print(f"ERROR [{error_kind}]: {msg}", file=sys.stderr)
    marker = (f"ECO_SCRIPT_LAUNCHED: eco_verify_cone_equivalence.py\n"
              f"  ABORTED [{error_kind}] — {msg}\n")
    try:
        open(result_path.replace('.json', '_marker.txt'), 'w').write(marker)
    except OSError:
        pass
    try:
        import json
        with open(result_path, 'w') as f:
            json.dump({"match": None, "error_kind": error_kind, "error": msg}, f, indent=2)
    except OSError:
        pass
    sys.exit(2)


def _read_text(path):
    if path.endswith('.gz'):
        with gzip.open(path, 'rt', errors='replace') as f:
            return f.read()
    with open(path, 'r', errors='replace') as f:
        return f.read()


def _resolve_netlist_path(ref_dir, stage):
    candidates = [
        os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz'),
        os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v'),
        os.path.join(ref_dir, 'netlist', f'{stage}.v.gz'),
        os.path.join(ref_dir, 'netlist', f'{stage}.v'),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _extract_module_body(full_text, module_name):
    """Extract one module's body (module ... to its matching endmodule), same
    convention as eco_lol_impact.parse_modules but targeted at a single module to
    avoid paying the cost of splitting the whole (often multi-million-line) file."""
    mods = lol.parse_modules(full_text)
    return mods.get(module_name)


# ── Cell-function normalization for Z3 (NOT the same as eco_verify_boolean_fold.py's
# Python-boolean normalizer) ──────────────────────────────────────────────────────
# Cell truth tables use C-style &/|/~ and a `COND ? THEN : ELSE` ternary. z3's BoolRef
# directly overloads &, |, ~ as And/Or/Not (eval()'ing the raw C-style text against z3
# values works as-is) — but Python's `and`/`or`/`not` keywords do NOT work against z3
# symbolic values (they invoke __bool__, which z3 BoolRef intentionally cannot satisfy
# for a symbolic expression). So, unlike eco_verify_boolean_fold.py, do NOT convert
# &/|/~ to and/or/not here. Only the ternary needs rewriting, to z3.If(), since Python's
# own `X if C else Y` ternary syntax would also force C's truthiness.
def _normalize_cell_expr_for_z3(expr):
    if '?' in expr and ':' in expr:
        cond, rest = expr.split('?', 1)
        then_, else_ = rest.split(':', 1)
        return f"If({cond.strip()}, {then_.strip()}, {else_.strip()})"
    return expr.strip()


# Real netlist nets are frequently bus-bit names like "RecCmdCs_d2[0]" — a legitimate,
# common net name, but NOT a valid Python identifier on its own (the brackets break
# Python's tokenizer/eval). --target-expr must still be able to reference these real
# leaf names directly and exactly. _TARGET_IDENT_RE captures the bracketed form as ONE
# token (longest match first) so it is compared/substituted as a single unit, never
# split into a bare prefix that would silently fail to match the real leaf name.
_TARGET_IDENT_RE = re.compile(r'[A-Za-z_]\w*(?:\[\d+\])?')


def _tokenize_target_expr(expr):
    """Return the sorted set of distinct identifier tokens referenced in a
    --target-expr, bracket-aware (see _TARGET_IDENT_RE)."""
    return sorted(set(_TARGET_IDENT_RE.findall(expr)))


def _sanitize_target_expr(expr, tokens):
    """Replace every bracketed token (e.g. 'RecCmdCs_d2[0]') with a safe, Python-
    evaluable placeholder, so the expression can be eval()'d. Plain (non-bracketed)
    tokens are left untouched — they are already valid Python identifiers. Returns
    (sanitized_expr_text, {placeholder_or_token: original_token})."""
    alias_of = {}
    sanitized = expr
    for i, tok in enumerate(tokens):
        if '[' not in tok:
            alias_of[tok] = tok
            continue
        placeholder = f"__LEAF_{i}__"
        alias_of[placeholder] = tok
        # re.sub with a literal pattern (re.escape) — tok is a fixed string, never a
        # regex meta-pattern itself, but its '[' / ']' would otherwise be mis-parsed.
        sanitized = re.sub(re.escape(tok), placeholder, sanitized)
    return sanitized, alias_of


def build_net_driver(insts, ref_dir):
    """net -> (inst, out_pin, truth_table) for every net that is the SOLE real output
    of a combinational cell with a known truth table. Register (DFF/latch) outputs and
    cells with no available truth table are deliberately excluded here -- their nets
    become free z3 leaves during expansion, exactly like a true primary input."""
    net_driver = {}
    multi_drivers = {}
    for inst in insts:
        if lol._is_reg(inst['cell']):
            continue
        tt = ett.truth_table_of(inst['cell'], ref_dir=ref_dir)
        if not tt:
            continue
        for pin, nets in inst['pins'].items():
            if pin in tt and len(nets) == 1:
                net = nets[0]
                if net in net_driver and net_driver[net][0]['inst'] != inst['inst']:
                    multi_drivers.setdefault(net, []).append(inst['inst'])
                net_driver[net] = (inst, pin, tt)
    return net_driver, multi_drivers


def expand_to_z3(target_net, net_driver, result_path):
    """Recursively build a z3 BoolRef for target_net by mechanical substitution over
    real cell truth tables (same technique as eco_verify_boolean_fold.py's
    build_candidate_functions, generalized to auto-discover the whole cone instead of
    a caller-supplied gate list). Returns (expr, leaves) where leaves is the exact set
    of real net names that became free z3 Bool() variables (register Q-outputs, true
    primary inputs, or outputs of cells with no known truth table)."""
    memo = {}
    leaves = set()
    visiting = set()

    def resolve(net):
        if net in memo:
            return memo[net]
        if net == "1'b0":
            memo[net] = z3.BoolVal(False)
            return memo[net]
        if net == "1'b1":
            memo[net] = z3.BoolVal(True)
            return memo[net]
        if net not in net_driver:
            leaves.add(net)
            v = z3.Bool(net)
            memo[net] = v
            return v
        if net in visiting:
            _abort(result_path, f"combinational loop detected at net {net!r}", "loop")
        visiting.add(net)

        inst, out_pin, tt = net_driver[net]
        if 'function' in tt and 'output_pin' in tt:
            out_pin_name = tt['output_pin']
            expr_str = tt['function']
            if tt.get('output_is_inverting'):
                expr_str = f"~({expr_str})"
        else:
            out_pin_name, expr_str = next(iter(tt.items()))
        py_expr = _normalize_cell_expr_for_z3(expr_str)

        pin_names = re.findall(r'\b([A-Za-z_]\w*)\b', expr_str)
        ns = {'If': z3.If}
        for p in set(pin_names):
            if p == 'If' or p not in inst['pins']:
                continue
            pn = inst['pins'][p]
            if not pn:
                continue
            ns[p] = resolve(pn[0])
        try:
            val = eval(py_expr, {"__builtins__": {}}, ns)
        except Exception as ex:
            _abort(result_path,
                   f"cell function eval failed for inst {inst['inst']!r} "
                   f"cell {inst['cell']!r} expr {expr_str!r} (normalized {py_expr!r}): {ex}",
                   "eval_fail")
        visiting.discard(net)
        memo[net] = val
        return val

    expr = resolve(target_net)
    return expr, leaves


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ref-dir', required=True)
    p.add_argument('--stage', default='Synthesize', choices=['Synthesize', 'PrePlace', 'Route'])
    p.add_argument('--module', required=True, help='Real netlist module name (e.g. umccmd_umcstgbuf) — must already be the correct scope; this tool does not cross instance boundaries')
    p.add_argument('--target-net', required=True, help='A real net already present in --module (not a net you are building)')
    p.add_argument('--target-expr', required=True, help='Python boolean expression (&, |, ~ only — NOT and/or/not) over real leaf net names this cone discovers')
    p.add_argument('--netlist', help='Explicit netlist path override; default resolves from --ref-dir/--stage')
    p.add_argument('--result', required=True)
    args = p.parse_args()

    if z3 is None:
        _abort(args.result, "z3-solver is not installed in this environment (pip install z3-solver)", "missing_dependency")

    netlist_path = args.netlist or _resolve_netlist_path(args.ref_dir, args.stage)
    if not netlist_path:
        _abort(args.result, f"could not resolve a {args.stage} netlist under {args.ref_dir!r} "
               f"(checked data/PreEco and netlist/) — pass --netlist explicitly if it lives elsewhere", "not_found")

    full_text = _read_text(netlist_path)
    body = _extract_module_body(full_text, args.module)
    if body is None:
        _abort(args.result, f"module {args.module!r} not found in {netlist_path!r}", "not_found")

    insts = lol.parse_instances(body)
    if not insts:
        _abort(args.result, f"module {args.module!r} parsed with zero instances — check module name/body extraction", "not_found")

    net_driver, multi_drivers = build_net_driver(insts, args.ref_dir)

    target_expr_z3, leaves = expand_to_z3(args.target_net, net_driver, args.result)
    if args.target_net not in net_driver and args.target_net not in leaves:
        # resolve() always registers the net it was asked for, so this should not
        # happen; defensive check only.
        _abort(args.result, f"target net {args.target_net!r} did not resolve to a z3 expression", "not_found")

    # ── THE ITEM-4 GUARD: every --target-expr identifier must be a real leaf of
    # THIS target net's own discovered cone. This is checked BEFORE the SAT solver
    # ever runs — an ungrounded identifier here is exactly the real incident this
    # tool exists to make structurally impossible. Bracket-aware: a bus-bit token
    # like "RecCmdCs_d2[0]" is compared and substituted as ONE unit (see
    # _tokenize_target_expr) — never split into a bare prefix that would silently
    # fail to match the real leaf name. ──
    target_tokens = _tokenize_target_expr(args.target_expr)
    ungrounded = sorted(t for t in target_tokens if t not in leaves)
    if ungrounded:
        _abort(args.result,
               f"--target-expr references {ungrounded!r}, which {'is' if len(ungrounded) == 1 else 'are'} "
               f"NOT a real leaf of {args.target_net!r}'s own discovered fan-in cone "
               f"(real leaves found: {sorted(leaves)}). Do not introduce a fresh/disconnected "
               f"free variable to stand in for an operand you could not locate — that produces a "
               f"near-guaranteed, meaningless SAT mismatch regardless of whether the real circuit is "
               f"correct. Investigate where this operand really enters this cone (a sub-module "
               f"boundary this tool does not cross, a P&R-renamed alias, a different real net "
               f"entirely) before re-running.",
               "ungrounded_target_variable")

    sanitized_expr, alias_of = _sanitize_target_expr(args.target_expr, target_tokens)
    try:
        target_code = compile(sanitized_expr, '<target-expr>', 'eval')
        eval_ns = {placeholder: z3.Bool(real_net) for placeholder, real_net in alias_of.items()}
        target_z3 = eval(target_code, {"__builtins__": {}}, eval_ns)
    except Exception as ex:
        _abort(args.result, f"--target-expr failed to evaluate against z3 leaves "
               f"(sanitized form: {sanitized_expr!r}): {ex}", "target_expr_eval_fail")

    solver = z3.Solver()
    solver.add(z3.Xor(target_expr_z3, target_z3))
    result = solver.check()

    import json
    verdict = {
        "match": result == z3.unsat,
        "module": args.module,
        "netlist": netlist_path,
        "target_net": args.target_net,
        "target_expr": args.target_expr,
        "free_vars": sorted(leaves),
        "free_var_count": len(leaves),
        "multi_driver_nets": multi_drivers,
        "counterexample": None,
    }
    if result == z3.sat:
        model = solver.model()
        verdict["counterexample"] = {str(d): bool(model[d]) for d in model.decls()}

    with open(args.result, 'w') as f:
        json.dump(verdict, f, indent=2)

    marker = (f"ECO_SCRIPT_LAUNCHED: eco_verify_cone_equivalence.py\n"
              f"  module: {args.module}\n"
              f"  target_net: {args.target_net}\n"
              f"  target_expr: {args.target_expr}\n"
              f"  free_vars ({len(leaves)}): {sorted(leaves)}\n"
              f"  MATCH: {verdict['match']}\n"
              f"  result: {args.result}\n")
    if multi_drivers:
        marker += f"  WARNING multi_driver_nets: {multi_drivers}\n"
    print(marker)
    open(args.result.replace('.json', '_marker.txt'), 'w').write(marker)

    sys.exit(0 if verdict["match"] else 1)


if __name__ == '__main__':
    main()
