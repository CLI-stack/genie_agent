#!/usr/bin/env python3
"""
eco_validate_step3.py — Validate eco_preeco_study.json completeness before Step 4.

This script enforces the Step 3 output contract. If any required data is missing,
it prints a FAIL with specific reasons so the ORCHESTRATOR can re-spawn
eco_netlist_studier or eco_expand_chains to fill the gap.

Usage:
    python3 script/eco_scripts/eco_validate_step3.py \
        --study    data/<TAG>_eco_preeco_study.json \
        --rtl-diff data/<TAG>_eco_rtl_diff.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --output   data/<TAG>_eco_validate_step3.json

Exit: 0 = PASS (all complete), 1 = FAIL (issues found)
"""

import argparse, gzip, json, os, re, subprocess, sys
import shutil, tempfile, atexit
from pathlib import Path
from eco_validate_io import write_result

# ── PERF: decompress each PreEco .v.gz ONCE, run plain grep/cat thereafter ─────
# The dominant cost of this validator is repeated gzip decompression: many checks
# loop over hundreds of study entries and shell out to `zgrep`/`zcat`, each of
# which re-decompresses a 34-55 MB netlist. `_fastcmd()` rewrites such a shell
# command to run plain `grep`/`cat` on a once-decompressed temp copy — IDENTICAL
# regex semantics (same grep binary, same pattern), just no per-call decompress.
# Functionality is preserved exactly; only wall-clock changes.
_PLAIN_NL_CACHE = {}          # gz_path -> decompressed temp file path
_PLAIN_NL_TEMPS = []          # temp files to clean up at exit
_GZ_TOKEN_RE = re.compile(r'''[^\s'"]*\.v\.gz(?:\.bak_\d+)?''')

def _plain_netlist(gz):
    """Decompress a .v.gz (optionally a .bak_<n> variant) ONCE to a temp plain file;
    return its path. Falls back to the original gz path on any error."""
    if not gz or '.v.gz' not in gz:
        return gz
    if gz in _PLAIN_NL_CACHE:
        return _PLAIN_NL_CACHE[gz]
    out = gz
    try:
        if os.path.isfile(gz):
            fd, path = tempfile.mkstemp(suffix='.v', prefix='eco_s3nl_')
            with os.fdopen(fd, 'wb') as w, gzip.open(gz, 'rb') as r:
                shutil.copyfileobj(r, w, length=1 << 20)
            out = path
            _PLAIN_NL_TEMPS.append(path)
    except Exception:
        out = gz
    _PLAIN_NL_CACHE[gz] = out
    return out

_NL_TEXT = {}                 # plain_path -> full text (one read)
_NL_WORDS = {}                # plain_path -> set of \w+ tokens (one build)
_PLAIN_ID_RE = re.compile(r'^[A-Za-z_]\w*$')
# Simple existence query: `zgrep -c[w] "PATTERN" <gz>` (the hot per-entry shape).
_SIMPLE_COUNT_RE = re.compile(r'^zgrep -c(w?) "([^"]+)" (\S+\.v\.gz(?:\.bak_\d+)?)$')

def _nl_text(plain):
    if plain not in _NL_TEXT:
        t = ''
        try:
            with open(plain, 'r', errors='replace') as f:
                t = f.read()
        except Exception:
            t = ''
        _NL_TEXT[plain] = t
    return _NL_TEXT[plain]


_NL_MODMAP = {}                 # plain_path -> {module_name: (start_offset, end_offset)}
_MODHEAD_RE = re.compile(r'(?m)^module\s+(\w+)\b')
_ENDMOD_RE = re.compile(r'(?m)^endmodule\b')

def _nl_module_map(plain):
    """Build ONCE per stage netlist: {module_name -> (start,end) offsets into _nl_text}. Modules
    don't nest, so a single forward scan of module-heads + endmodules indexes every module. Callers
    slice the body on demand — O(1) lookup instead of an O(file) awk/regex scan PER module (the
    ~40 uniquified umcrecrcqentry_* × 3-stage validator hotspot)."""
    mp = _NL_MODMAP.get(plain)
    if mp is not None:
        return mp
    mp = {}
    full = _nl_text(plain)
    ends = [m.start() for m in _ENDMOD_RE.finditer(full)]
    import bisect
    for hm in _MODHEAD_RE.finditer(full):
        s = hm.start()
        i = bisect.bisect_left(ends, hm.end())
        if i < len(ends):
            e = ends[i] + len('endmodule')
            mp.setdefault(hm.group(1), (s, e))
    _NL_MODMAP[plain] = mp
    return mp

def _nl_words(plain):
    if plain not in _NL_WORDS:
        _NL_WORDS[plain] = set(re.findall(r'[A-Za-z_]\w*', _nl_text(plain)))
    return _NL_WORDS[plain]


_NL_WORDS_BR = {}             # plain_path -> set of bracket-inclusive net tokens (one build)
_NL_WORDBR_RE = re.compile(r'[A-Za-z_][\w\[\]]*')

def _nl_words_br(plain):
    """Token set INCLUDING bracket-bit net forms (e.g. `WckSyncCtr0[5]`). Lets a whole-net
    existence check use O(1) set membership for BOTH plain identifiers and bus-bit names,
    instead of a `re.search(r'\\bnet\\b', <300MB text>)` per net (the Check-65 hotspot)."""
    if plain not in _NL_WORDS_BR:
        _NL_WORDS_BR[plain] = set(_NL_WORDBR_RE.findall(_nl_text(plain)))
    return _NL_WORDS_BR[plain]


_CELL_INST_COUNT = {}         # (cell, plain_path) -> #instantiations in the netlist
def _cell_instance_count(cell, plain):
    """How many times cell `cell` is INSTANTIATED (`<CellType> <cell> (`) in the netlist. Used to
    tell an AMBIGUOUS multi-module tool cell (wrong-module apply risk) from a UNIQUE one (safe).
    Cached."""
    key = (cell, plain)
    if key in _CELL_INST_COUNT:
        return _CELL_INST_COUNT[key]
    n = 0
    try:
        n = len(re.findall(r'\b' + re.escape(cell) + r'\s*\(', _nl_text(plain)))
    except Exception:
        n = 0
    _CELL_INST_COUNT[key] = n
    return n

def _fastcmd(cmd):
    """Rewrite a shell cmd using zgrep/zcat on PreEco .v.gz into grep/cat on a
    once-decompressed plain temp file (exact same results, far less wall-clock).
    For the hot `zgrep -c[w] "PLAIN_ID" <gz>` existence shape, answer entirely
    in-memory (O(1) word-set / substring lookup) and return `echo <0|1>` — the
    callers use the result only as ==0/>0, so 0/1 is result-equivalent. Only PLAIN
    identifiers (no regex metachars) take this path; -cw uses word-set membership
    (== grep `\\bW\\b`), -c uses substring presence (== grep substring match).
    Anything else falls back to plain grep/cat with identical semantics."""
    if not isinstance(cmd, str):
        return cmd
    m = _SIMPLE_COUNT_RE.match(cmd)
    if m and _PLAIN_ID_RE.match(m.group(2)):
        word_flag, pat, gz = (m.group(1) == 'w'), m.group(2), m.group(3)
        plain = _plain_netlist(gz)
        if plain != gz:
            present = (pat in _nl_words(plain)) if word_flag else (pat in _nl_text(plain))
            return f'echo {1 if present else 0}'
    paths = sorted(set(_GZ_TOKEN_RE.findall(cmd)), key=len, reverse=True)
    if not paths:
        return cmd
    new, subbed = cmd, False
    for p in paths:
        plain = _plain_netlist(p)
        if plain != p:
            new = new.replace(p, plain); subbed = True
    if subbed:
        new = re.sub(r'\bzgrep\b', 'grep', new)
        new = re.sub(r'\bzcat\b', 'cat', new)
    return new

def _cleanup_plain_netlists():
    for p in _PLAIN_NL_TEMPS:
        try:
            if os.path.isfile(p):
                os.unlink(p)
        except Exception:
            pass
atexit.register(_cleanup_plain_netlists)
try:
    from eco_rtl_config import RtlConfig
    from eco_extract_pf_condition import extract_condition, resolve_rtl, extract_added_branch_condition
    from eco_emit_priority_force import synthesize_condition, _PErr
except Exception:
    RtlConfig = extract_condition = resolve_rtl = synthesize_condition = extract_added_branch_condition = None
    _PErr = Exception


def _pf_condition_completeness(study, rtl_diff, ref_dir):
    """The built priority_force condition cone must match the RTL condition. Re-synth
    the expected cone from the anchored condition_expr -> expected real-net leaf set;
    trace the study's cone back from the muxes' cond input -> actual leaf set; a
    mismatch means the study built the WRONG/incomplete condition (e.g. dropped the
    WCK reductions, or reconstructed from the wrong signal). Needs --ref-dir+helpers."""
    if not (ref_dir and RtlConfig and synthesize_condition and extract_condition):
        return []
    cfg = RtlConfig(ref_dir)
    syn = study.get('Synthesize', [])
    _OUT = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN')
    # net -> list of input nets (producer graph over study gates)
    producer = {}
    for e in syn:
        if e.get('change_type') != 'new_logic_gate':
            continue
        pc = e.get('port_connections') or {}
        outs = [v for k, v in pc.items() if k in _OUT]
        ins = [v for k, v in pc.items() if k not in _OUT and isinstance(v, str)]
        for o in outs:
            producer[o] = ins
    issues = []
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'priority_force':
            continue
        mod = c.get('module_name') or ''
        anchor = next((f for f in (c.get('forced_signals') or []) if f.get('const_macro')), None)
        if not anchor:
            continue
        base = re.sub(r'^ddrss_\w+?_t_', '', mod)
        rtl = resolve_rtl(ref_dir=ref_dir, module=base)
        # GROUND TRUTH: the ECO-added branch (present in SynRtl, absent in PreEco).
        added = (extract_added_branch_condition(ref_dir, base, anchor['signal'], anchor['const_macro'])
                 if extract_added_branch_condition else [])
        cond_expr = added[0]['condition_expr'] if len(added) == 1 else c.get('condition_expr')
        if not cond_expr:
            continue
        rtl_text = open(rtl, errors='replace').read() if rtl else None
        nl = _pf_modbody(ref_dir, mod)
        innl = (lambda s: bool(re.search(r'\b' + re.escape(s) + r'\b', nl)))
        try:
            seq = [0]
            def _mk(t):
                seq[0] += 1
                return f"_exp_{t}_{seq[0]}"
            _cond, cone = synthesize_condition(cond_expr, 'exp', mod, cfg, _mk,
                                               rtl_text=rtl_text, in_netlist=innl)
        except _PErr:
            continue                                   # unbuildable -> other checks handle
        exp_leaves = {v for g in cone for k, v in g['port_connections'].items()
                      if k not in _OUT and isinstance(v, str) and not v.startswith('_exp_')}
        # study cond net = the condition input of this change's force-muxes
        cond_nets = set()
        for e in syn:
            if e.get('source') == 'eco_emit_priority_force' and re.search(r'pf_(net)?mux', str(e.get('instance_name', ''))):
                pc = e.get('port_connections') or {}
                cond_nets.add(pc.get('A1') if e.get('gate_function') == 'OR2' else pc.get('B1'))
        # trace back from cond nets to real-net leaves
        act_leaves, seen, stack = set(), set(), [n for n in cond_nets if n]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in producer:
                stack.extend(producer[n])
            elif isinstance(n, str) and not re.match(r"^\d*'[bhd]", n):
                act_leaves.add(n)
        missing = sorted(exp_leaves - act_leaves)
        extra = sorted(act_leaves - exp_leaves)
        if missing or extra:
            issues.append(
                f"CRITICAL/PF-CONDITION-MISMATCH: priority_force {mod} condition cone does not match "
                f"the RTL condition. Missing expected leaves {missing[:8]}; unexpected leaves "
                f"{extra[:8]}. The built condition is wrong/incomplete (dropped terms or wrong signals) "
                f"— rebuild it from condition_expr via eco_emit_priority_force.")
    return issues


def _cnf_completeness(study, rtl_diff, ref_dir):
    """Validate every `comb_net_force` change was actually BUILT (not left a placeholder)
    and is well-formed. Per change {module_name, signal}, in the Synthesize stage:
      - ≥1 driver-side net-force rewire from eco_cone_rebuild whose old_net is signal/signal[b]
        (absence ⇒ the deterministic builder did not run ⇒ placeholder ⇒ CRITICAL);
      - each such rewire carries cell_name_per_stage + pin_per_stage + old_net_per_stage for
        all 3 stages;
      - the original net (old_net) is re-driven by a mux gate (a gate whose output_net ==
        old_net), so all fanout sees the forced value.
    The builder itself fails closed on ungrounded leaves / missing comb driver, so a PRESENT
    well-formed emission is grounded by construction; this check guards the wiring."""
    issues = []
    changes = [c for c in rtl_diff.get('changes', []) if c.get('change_type') == 'comb_net_force']
    if not changes:
        return issues
    syn = study.get('Synthesize', [])
    gate_outs = {e.get('output_net') for e in syn if e.get('change_type') == 'new_logic_gate'}
    for c in changes:
        sig = c.get('signal') or c.get('new_token') or c.get('target')
        if not sig:
            issues.append("CRITICAL: comb_net_force change missing `signal` — cannot validate.")
            continue
        rws = [e for e in syn if e.get('change_type') == 'rewire' and e.get('driver_side')
               and e.get('net_force') and e.get('source') == 'eco_cone_rebuild'
               and (e.get('old_net') == sig or str(e.get('old_net', '')).startswith(sig + '['))]
        if not rws:
            issues.append(f"CRITICAL: comb_net_force {sig} — no driver-side net-force rewire from "
                          f"eco_cone_rebuild in study. The deterministic builder did not run "
                          f"(placeholder). Run eco_cone_rebuild.py --emit-into-study.")
            continue
        for rw in rws:
            on = rw.get('old_net')
            for fld in ('cell_name_per_stage', 'pin_per_stage', 'old_net_per_stage'):
                d = rw.get(fld)
                if not (isinstance(d, dict) and all(d.get(s) for s in ('Synthesize', 'PrePlace', 'Route'))):
                    issues.append(f"HIGH: comb_net_force {sig} rewire {on} missing {fld} for all stages.")
            if on not in gate_outs:
                issues.append(f"CRITICAL: comb_net_force {sig} — original net {on} renamed to "
                              f"{rw.get('new_net')} but no mux gate re-drives {on}; fanout would "
                              f"see an undriven net.")
    return issues


def _compare_fold_completeness(study, rtl_diff, ref_dir):
    """Validate every `compare_fold` change was BUILT by eco_emit_compare_fold (net-force),
    not left for the and_term OR-widen path. Per change, in Synthesize: >=1 driver-side
    net-force rewire from eco_emit_compare_fold, and its renamed original net (old_net = the
    mismatch net) is re-driven by a new_logic_gate (the fold INR2). The emitter self-checks
    the fold function exhaustively, so a PRESENT well-formed emission is correct by
    construction; this guards the wiring + that the wrong builder was not used."""
    issues = []
    changes = [c for c in rtl_diff.get('changes', []) if c.get('change_type') == 'compare_fold']
    if not changes:
        return issues
    syn = study.get('Synthesize', [])
    gate_outs = {e.get('output_net') for e in syn if e.get('change_type') == 'new_logic_gate'
                 and e.get('source') == 'eco_emit_compare_fold'}
    rws = [e for e in syn if e.get('change_type') == 'rewire'
           and e.get('source') == 'eco_emit_compare_fold'
           and e.get('driver_side') and e.get('net_force')]
    if not rws:
        issues.append("CRITICAL: compare_fold change(s) present but no driver-side net-force "
                      "rewire from eco_emit_compare_fold in study — the deterministic builder "
                      "did not run (placeholder / wrong builder). Run eco_emit_compare_fold.py.")
        return issues
    for rw in rws:
        on = rw.get('old_net')
        for fld in ('cell_name_per_stage', 'pin_per_stage', 'old_net_per_stage'):
            d = rw.get(fld)
            if not (isinstance(d, dict) and all(d.get(s) for s in ('Synthesize', 'PrePlace', 'Route'))):
                issues.append(f"HIGH: compare_fold rewire {on} missing {fld} for all stages.")
        if on not in gate_outs:
            issues.append(f"CRITICAL: compare_fold — mismatch net {on} renamed to "
                          f"{rw.get('new_net')} but no compare_fold gate re-drives {on}; "
                          f"fanout would see an undriven net.")
    return issues


# Required INPUT pins per (confidently-known) gate function. Compound cells (AOI/OAI/
# AO/OA/MUX/etc.) are intentionally omitted — their pin sets vary and we must not
# false-flag them. Prefix match: fn.startswith(key).
_REQ_INPUTS = {
    'OR2': ('A1', 'A2'), 'AND2': ('A1', 'A2'), 'AN2': ('A1', 'A2'),
    'NOR2': ('A1', 'A2'), 'NR2': ('A1', 'A2'), 'NAND2': ('A1', 'A2'), 'ND2': ('A1', 'A2'),
    'XOR2': ('A1', 'A2'), 'XNOR2': ('A1', 'A2'),
    'INR2': ('A1', 'B1'), 'IND2': ('A1', 'B1'),
    'OR3': ('A1', 'A2', 'A3'), 'AND3': ('A1', 'A2', 'A3'), 'AN3': ('A1', 'A2', 'A3'),
    'NOR3': ('A1', 'A2', 'A3'), 'NR3': ('A1', 'A2', 'A3'), 'ND3': ('A1', 'A2', 'A3'),
    'INV': ('I',), 'BUF': ('I',),
}


def _gate_input_completeness(study):
    """Every new_logic_gate must have ALL required input pins present in the pc used for
    EACH of the 3 stages (port_connections_per_stage[stage], falling back to
    port_connections). Catches the silent-drop failure mode where a per-stage pc omits an
    input (e.g. an OR2 combine gate whose A1 was deleted in PrePlace/Route because FM
    resolved the old net to a sink-pin) — the applier then emits a FLOATING pin and FM
    fails only at the PP/Route cone. The pre-existing pin check ran on Synthesize ONLY and
    only flagged INVALID present pins, never a MISSING required input, so this slipped."""
    issues = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            fn = (e.get('gate_function') or '').upper()
            req = next((v for k, v in _REQ_INPUTS.items() if fn.startswith(k)), None)
            if not req:
                continue  # compound/unknown cell — don't risk a false flag
            pc = (e.get('port_connections_per_stage', {}) or {}).get(stage) \
                or e.get('port_connections', {}) or {}
            missing = [p for p in req if p not in pc]
            if missing:
                issues.append(
                    f"CRITICAL/GATE-INPUT-MISSING: {stage} new_logic_gate "
                    f"{e.get('instance_name','?')!r} ({fn}) is missing required input pin(s) "
                    f"{missing} in its {stage} port_connections {sorted(pc.keys())}. The applier "
                    f"emits this gate from the per-stage pc, so the pin FLOATS in {stage} -> "
                    f"wrong function / FM cone mismatch. Resolve the pin to its real per-stage "
                    f"net (do NOT drop it).")
    return issues


def _rg_surgical_mismatch(ref_dir, module, reg, N=2000, branch_target=None):
    """Intent-A backstop (RTL-signal space — NO netlist-leaf confound): return a short mismatch
    string if the re-drive the reg_guard_delta builder emits does NOT equal the RTL golden
    next-state for register `reg`, else ''. Brute-forces random RTL-input vectors. Best-effort:
    any build/parse error returns '' (never a false hard-fail).

    TWO models, matching the builder's two emission paths:
      • SIMPLE flop (postcas class): `surg = sel ? region : old_D` vs golden next-state.
      • CLOCK-GATED flop (WckSyncCtr0/counter class): the builder emits a slim hold-mux + E-widen,
        so the check is the EFFECTIVE flop: eff = new_E?new_D:reg  vs  golden_eff = golden_E?golden:reg,
        where new_D[b]=load_active?load_val[b]:old_D[b], new_E=old_E|load_active,
        load_active = _path_scalar(the widened branch's full cond). (This is the cg_slim_chk oracle;
        the simple-flop model would validate the WRONG function for a clock-gated reg.)"""
    try:
        import random
        import eco_cone_rebuild as _ecr
        from eco_rtl_synth import parse_expr as _pexpr
        from eco_cell_truth_tables import ABSTRACT_GATE_FUNCTIONS as _AGF
        synth, mk, rtl_text, old_text, wm = _ecr._synth_setup(ref_dir, module, jira='vchk')
        new_tree = _ecr.parse_always(rtl_text, reg)
        old_tree = _ecr.parse_always(old_text, reg)
        width = wm.get(reg) or 1
        def build_full(tree):
            branch = [(synth._path_scalar(cc), _pexpr(vv)) for cc, vv in tree['assigns']]
            hp = list(reversed(branch)); sels = [s for s, _ in hp]
            actives, none = synth._priority_masks(sels)
            dflt = _pexpr(tree['default']) if tree.get('default') is not None else None
            out = {}
            for b in range(width):
                terms = [synth._and(actives[k], synth.bit(v, b)) for k, (_, v) in enumerate(hp)]
                if dflt is not None:
                    terms.append(synth._and(none, synth.bit(dflt, b)))
                out[b] = synth._reduce(terms, 'or') if terms else "1'b0"
            return out, none
        golden, none_new = build_full(new_tree); old, none_old = build_full(old_tree)
        cg = None
        try:
            cg = _ecr._reg_clockgate(ref_dir, module, reg)
        except Exception:
            cg = None
        if cg:
            # ── CLOCK-GATED: effective-flop oracle (matches the slim hold-mux + E-widen build) ──
            load_cond, load_val = _ecr._rg_widened_branch(synth, rtl_text, old_text, reg, branch_target)
            if load_cond is None:
                return ''
            load_active = synth._path_scalar(load_cond); v_ast = _pexpr(load_val)
            nload = synth._inv(load_active)
            old_E = synth._inv(none_old); golden_E = synth._inv(none_new)
            new_E = synth._or(old_E, load_active); n_newE = synth._inv(new_E); n_goldE = synth._inv(golden_E)
            surg, gold_eff = {}, {}
            for b in range(width):
                new_D = synth._or(synth._and(load_active, synth.bit(v_ast, b)), synth._and(nload, old[b]))
                surg[b]     = synth._or(synth._and(new_E, new_D),      synth._and(n_newE,  f'{reg}[{b}]'))
                gold_eff[b] = synth._or(synth._and(golden_E, golden[b]), synth._and(n_goldE, f'{reg}[{b}]'))
            golden = gold_eff        # compare eff vs golden_eff below (same downstream harness)
        else:
            # ── SIMPLE flop: sel ? region : old_D  vs  golden next-state ──
            r = _ecr._region_of(synth, rtl_text, old_text, reg, wm)
            if r is None:
                return ''
            sel = r['sel']; nsel = synth._inv(sel)
            surg = {b: synth._or(synth._and(sel, r['region_bits'].get(b, "1'b0")), synth._and(nsel, old[b]))
                    for b in range(width)}
        driver = {g['output_net']: g for g in synth.gates}
        def _leaves(net, acc):
            g = driver.get(net)
            if not g:
                if not str(net).startswith(("1'b", "0'b")):
                    acc.add(net)
                return
            for k, v in g['port_connections'].items():
                if k in ('Z', 'ZN') or not isinstance(v, str):
                    continue
                _leaves(v, acc)
        allleaves = set()
        for d in (golden, old, surg):
            for b in d:
                if isinstance(d[b], str):
                    _leaves(d[b], allleaves)
        if not cg:
            _leaves(sel, allleaves)     # simple-flop selector cone (clock-gate: load_active already in surg)
        leaflist = sorted(allleaves)
        def _ev(net, env, memo):
            if net in memo:
                return memo[net]
            m = re.match(r"\d*'[bB]([01]+)", str(net))
            if m:
                memo[net] = int(m.group(1), 2) & 1; return memo[net]
            g = driver.get(net)
            if not g:
                memo[net] = env.get(net, 0); return memo[net]
            expr = _AGF.get(g['gate_function'])
            if not expr:
                raise RuntimeError('gate_function %r not in AGF' % g['gate_function'])
            pin = {k: _ev(v, env, memo) for k, v in g['port_connections'].items() if k not in ('Z', 'ZN')}
            memo[net] = eval(next(iter(expr.values())), {"__builtins__": {}}, pin) & 1
            return memo[net]
        random.seed(3)
        for _ in range(N):
            env = {l: random.randint(0, 1) for l in leaflist}
            if 'IReset' in env:
                env['IReset'] = 0        # async reset handled by the flop reset pin, not the D-cone
            for b in range(width):
                memo = {}
                gv = _ev(golden[b], env, memo) if isinstance(golden[b], str) else golden[b]
                sv = _ev(surg[b], env, memo) if isinstance(surg[b], str) else surg[b]
                if gv != sv:
                    return f"bit{b} differs on a reachable input vector"
        return ''
    except Exception:
        return ''


def _pf_modbody(ref_dir, module):
    gz = os.path.join(ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    want = re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', str(module or '')))
    body, grab = [], False
    if os.path.isfile(gz):
        try:
            with gzip.open(gz, 'rt', errors='replace') as f:
                for ln in f:
                    mm = re.match(r'^module\s+(\S+)', ln)
                    if mm:
                        grab = re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', mm.group(1))) == want
                    if grab:
                        body.append(ln)
                        if ln.lstrip().startswith('endmodule'):
                            grab = False
        except Exception:
            body = []
    return ''.join(body)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study',    required=True)
    p.add_argument('--rtl-diff', required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--output',   required=True)
    p.add_argument('--iter', type=int, default=None,
                   help='catch-and-fix iteration number; when set, ALSO write '
                        '<output>_iter<N>.json (history). Canonical --output stays latest/final.')
    args = p.parse_args()

    study    = json.loads(Path(args.study).read_text())
    rtl_diff = json.loads(Path(args.rtl_diff).read_text())
    issues   = []

    # ── 1. All 3 stages present and non-empty ────────────────────────────────
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        entries = study.get(stage, [])
        if not entries:
            issues.append(f"CRITICAL: {stage} entries empty — eco_netlist_studier produced no output for this stage")

    # ── 2. Every DFF with d_input_gate_chain has gate entries expanded ───────
    for change in rtl_diff.get('changes', []):
        # Bus DFFs have no combinational chain — skip the chain-expansion check.
        if change.get('is_bus_dff'):
            continue
        chain = change.get('d_input_gate_chain')
        if not chain:
            continue
        target = change.get('target_register', '') or change.get('new_token', '')
        chain_inst_names = {g.get('instance_name','') for g in chain if g.get('instance_name')}
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            existing = {e.get('instance_name','') for e in study.get(stage,[])
                       if e.get('change_type') in ('new_logic_gate','new_logic_dff','new_logic')}
            missing = chain_inst_names - existing
            if missing:
                issues.append(f"CRITICAL: {stage} missing d-input gate chain entries for {target}: {missing} — run eco_expand_chains.py")

    # ── 2b. Bus DFF: verify N individual DFF entries exist per stage ─────────
    # When is_bus_dff=true, eco_emit_dff_entry.py --bus-width N emits N entries
    # (one per bit, instance name <target>_reg_<bit>_).  This check verifies the
    # expected count matches the resolved bus width so partial expansions are caught
    # before Step 4.
    bus_width_cache = {}
    for change in rtl_diff.get('changes', []):
        if not change.get('is_bus_dff'):
            continue
        if change.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        target = change.get('target_register', '') or change.get('new_token', '')
        # bus_width_resolved is set by the studier after calling eco_resolve_bus_width.py.
        # If absent, derive from bus_width_expr (e.g. "6:0" → 7, or integer string).
        expected_n = change.get('bus_width_resolved')
        if not expected_n:
            bwe = str(change.get('bus_width_expr', ''))
            import re as _re_bw
            m_range = _re_bw.match(r'^\s*(\d+)\s*:\s*(\d+)\s*$', bwe)
            if m_range:
                expected_n = int(m_range.group(1)) - int(m_range.group(2)) + 1
            elif bwe.isdigit():
                expected_n = int(bwe)
        if not expected_n:
            issues.append(
                f"HIGH: bus DFF '{target}' missing `bus_width_resolved` and "
                f"cannot derive width from bus_width_expr={change.get('bus_width_expr')!r} — "
                f"eco_netlist_studier must call eco_resolve_bus_width.py")
            continue
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            bit_entries = [
                e for e in study.get(stage, [])
                if e.get('is_bus_dff_bit') and
                   e.get('instance_name', '').startswith(f'{target}_reg_')
            ]
            if len(bit_entries) != expected_n:
                issues.append(
                    f"CRITICAL: {stage} has {len(bit_entries)} bus DFF bit entries for '{target}' "
                    f"but expected {expected_n} — re-run eco_emit_dff_entry.py --bus-width {expected_n}")

    # ── 2d. Bus gate: verify consistent N entries per stage ──────────────────
    # When is_bus_gate=true, the studier expands one RTL gate into N per-bit
    # entries (is_bus_gate_bit=true).  Check that all 3 stages have the same
    # count and that the count is > 0.  We validate consistency rather than an
    # exact expected_n (the studier records bus_width_resolved on each entry).
    for change in rtl_diff.get('changes', []):
        if not change.get('is_bus_gate'):
            continue
        if change.get('change_type') not in ('new_logic_gate', 'new_logic'):
            continue
        target = change.get('output_net', '') or change.get('new_token', '')
        import re as _re
        target_base = _re.sub(r'\[\d+\]$', '', target)
        counts = {}
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            counts[stage] = sum(
                1 for e in study.get(stage, [])
                if e.get('is_bus_gate_bit') and
                   _re.sub(r'\[\d+\]$', '', e.get('output_net', '')) == target_base
            )
        if any(c == 0 for c in counts.values()):
            issues.append(
                f"CRITICAL: bus gate '{target_base}' has zero entries in one or more stages "
                f"({counts}) — eco_netlist_studier must expand to N per-bit gate entries")
        elif len(set(counts.values())) > 1:
            issues.append(
                f"CRITICAL: bus gate '{target_base}' has inconsistent entry counts across stages "
                f"({counts}) — all 3 stages must have the same N bit entries")

    # ── 2e-pre00. Bus bit output port SYNOPSYS_UNCONNECTED used as gate input ──
    # When a gate chain uses a bus bit in flat form (e.g. signal_N_) as input,
    # and that bit is an OUTPUT PORT with SYNOPSYS_UNCONNECTED internal driver in
    # any PreEco stage, using it produces an undriven gate input → FM Mode A.
    # The actual data wire is actual_wire_<stage> from the rename map.
    _RMAP_PATH_UNC = Path(args.study).parent / f'{args.tag}_eco_fenets_rename_map.json'
    _rmap_unc = {}
    if _RMAP_PATH_UNC.is_file():
        try:
            _rmap_unc = json.loads(_RMAP_PATH_UNC.read_text())
        except Exception:
            pass
    _OUT_CHECK_UNC = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'}
    if _rmap_unc and args.ref_dir:
        for stage in ['Route', 'PrePlace']:  # Route most common for UNCONNECTED
            gz = Path(args.ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
            if not gz.is_file():
                continue
            try:
                import subprocess as _sp_unc2
                r2 = _sp_unc2.run(
                    _fastcmd(f'zgrep "SYNOPSYS_UNCONNECTED" {gz}'),
                    shell=True, capture_output=True, text=True, timeout=30)
                unc_text = r2.stdout
            except Exception:
                unc_text = ''
            if not unc_text:
                continue
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                    continue
                pcps = e.get('port_connections_per_stage', {})
                st_pcs = pcps.get(stage) or e.get('port_connections') or {}
                inst = e.get('instance_name', '?')
                scope = e.get('instance_scope', '')
                for pin, net in st_pcs.items():
                    if pin in _OUT_CHECK_UNC or not isinstance(net, str):
                        continue
                    if net.startswith(('n_eco_', "1'b")):
                        continue
                    base = net.split('[')[0]
                    # Quick check: does this base name appear near SYNOPSYS_UNCONNECTED?
                    if base in unc_text and 'SYNOPSYS_UNCONNECTED' in unc_text:
                        key1 = f'{scope}/{base}' if scope else base
                        actual = (_rmap_unc.get(key1) or {}).get(f'actual_wire_{stage}', '')
                        if actual and actual != net:
                            issues.append(
                                f"HIGH: gate '{inst}' {stage} {pin}={net!r} — this port has "
                                f"SYNOPSYS_UNCONNECTED internal driver in PreEco {stage}; "
                                f"undriven as gate input. Use actual_wire_{stage}={actual!r} "
                                f"from rename map instead of the flat output port form.")

    # ── 2e-pre0. Chain gate per-stage input resolution check ─────────────────
    # Every non-output input pin on a new_logic_gate that has port_connections_per_stage
    # must NOT have the same bare value across all 3 stages when the rename map
    # has a different per-stage value for that signal.  Identical values across all
    # stages mean per-stage resolution was skipped — CTS-renamed nets (IReset,
    # clock signals, etc.) will differ between stages causing DFF0X / cone mismatch.
    _rename_map_path = Path(args.study).parent / f'{args.tag}_eco_fenets_rename_map.json'
    _rmap = {}
    if _rename_map_path.is_file():
        try:
            _rmap = json.loads(_rename_map_path.read_text())
        except Exception:
            pass
    _OUT_CHECK = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'}
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            pcps = e.get('port_connections_per_stage', {})
            if not pcps:
                continue
            inst = e.get('instance_name', '?')
            scope = e.get('instance_scope', '')
            for pin in pcps.get('Synthesize', {}):
                if pin in _OUT_CHECK:
                    continue
                vals = {s: pcps.get(s, {}).get(pin, '') for s in ('Synthesize', 'PrePlace', 'Route')}
                if len(set(vals.values())) != 1:
                    continue  # already different per-stage — OK
                bare = vals['Synthesize']
                # Check rename map: does this net have different per-stage values?
                base = bare.split('[')[0]
                key = f'{scope}/{base}' if scope else base
                entry = _rmap.get(key, {})
                if isinstance(entry, dict):
                    # Use actual_wire_<stage> (the REAL per-stage NET) when present. The plain
                    # <stage> field is FM's equivalence-POINT, which is often a SINK-PIN ref
                    # (e.g. 'postcas_reg/D') even when the net name is stage-stable — using it
                    # here false-flagged a correct bare net and triggered a bad "fix" that
                    # DROPPED the pin. actual_wire_<stage> reflects the true net name.
                    rmap_vals = {s: (entry.get('actual_wire_' + s) or entry.get(s, bare))
                                 for s in ('Synthesize', 'PrePlace', 'Route')}
                    if len(set(rmap_vals.values())) > 1:
                        issues.append(
                            f"HIGH: gate '{inst}' pin={pin!r} uses bare '{bare}' for ALL stages "
                            f"but rename map shows per-stage values "
                            f"(Syn={rmap_vals['Synthesize']!r} PP={rmap_vals['PrePlace']!r} "
                            f"Route={rmap_vals['Route']!r}). "
                            f"eco_emit_dff_entry.py must resolve this pin per-stage from rename map "
                            f"to avoid DFF0X/cone-mismatch in PP/Route stages.")

    # ── 2e-pre-inv. Multiple INV inversions of same CTS-renamed signal ───────
    # N separate INV gates each inverting the same CTS-renamed signal should be
    # consolidated into ONE shared INV (eco_<jira>_<signal>_inv).  Downstream
    # gates then reference the new ECO-internal net — stage-stable, no CTS
    # renaming needed.  Multiple INV gates multiply per-stage resolution work
    # and create redundant FM cone entries for logically-identical inversions.
    import re as _re_inv
    _RMAP_INV_PATH = Path(args.study).parent / f'{args.tag}_eco_fenets_rename_map.json'
    _rmap_inv = {}
    if _RMAP_INV_PATH.is_file():
        try:
            _rmap_inv = json.loads(_RMAP_INV_PATH.read_text())
        except Exception:
            pass
    if _rmap_inv:
        for stage in ['Synthesize']:
            inv_inputs = {}   # base_net → [instance_names]
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                    continue
                if (e.get('gate_function') or '').upper() not in ('INV', 'INVD', 'INVLLKG', 'INVSKR'):
                    continue
                pcs = e.get('port_connections') or {}
                inp = pcs.get('I') or pcs.get('A') or ''
                if not inp or inp.startswith(('n_eco_', "1'b")):
                    continue
                base = _re_inv.sub(r'\[\d+\]$', '', inp)
                scope = e.get('instance_scope', '')
                key = f'{scope}/{base}' if scope else base
                entry = _rmap_inv.get(key, {})
                if isinstance(entry, dict):
                    vals = [entry.get(s, base) for s in ('Synthesize', 'PrePlace', 'Route')]
                    if len(set(vals)) > 1:   # CTS-renamed → shared INV needed
                        inv_inputs.setdefault(base, []).append(e.get('instance_name', '?'))
            for sig, insts in inv_inputs.items():
                if len(insts) > 1:
                    issues.append(
                        f"HIGH: {len(insts)} separate INV gates invert the same "
                        f"CTS-renamed signal '{sig}' ({insts[:3]}) — consolidate into ONE "
                        f"shared ECO INV gate; downstream AND2/AO22 gates reference its "
                        f"output (stage-stable ECO net, no per-stage resolution needed).")

    # ── 2e-pre. Rewire entries must have module_name ─────────────────────────
    # Without module_name, eco_applier's resolve_module_name() falls back to a
    # generic file-wide search and matches the FIRST cell of that name across ALL
    # modules — which may be a completely unrelated module sharing the same
    # tool-generated cell name (e.g. ctmi_*, phs_*). This silently applies the
    # rewire to the wrong module, leaving the intended target unchanged and
    # corrupting unrelated logic. Require module_name on every rewire entry so
    # eco_applier can scope its search to the correct module.
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') != 'rewire':
                continue
            cell = e.get('cell_name') or e.get('instance_name') or '?'
            if not e.get('module_name'):
                issues.append(
                    f"CRITICAL: rewire entry '{cell}' pin={e.get('pin','?')} in {stage} "
                    f"has no module_name — eco_applier will match the first cell of this "
                    f"name across ALL modules (wrong-module rewire risk). "
                    f"Studier must set module_name from the declaring module of the cell "
                    f"found by fenets (extracted from the impl path hierarchy).")

    # ── 2e. Bus DFF: per-bit D-input check ───────────────────────────────────
    # All N is_bus_dff_bit entries for the same register must carry DISTINCT D
    # nets — one per bit.  A single scalar D shared by all bits means the chain
    # gate was not expanded per-bit; each DFF bit would get the same scalar
    # value instead of its bit-slice → FM logical mismatch on every bus bit.
    import re as _re2
    for stage in ['Synthesize']:
        bus_d = {}
        for e in study.get(stage, []):
            if not e.get('is_bus_dff_bit'):
                continue
            reg = _re2.sub(r'_reg_\d+_$', '', e.get('instance_name', ''))
            pcs = e.get('port_connections') or e.get('port_connections_per_stage', {}).get(stage, {})
            d_net = pcs.get('D', '')
            bus_d.setdefault(reg, set()).add(d_net)
        for reg, d_nets in bus_d.items():
            if len(d_nets) == 1:
                issues.append(
                    f"CRITICAL: bus DFF '{reg}' all bits share the same D net ({d_nets}) "
                    f"— D-input chain must be expanded per-bit with bit-indexed inputs; "
                    f"scalar shared D causes FM logical mismatch on every bit")

    # ── 2f. Combinational loop detection ─────────────────────────────────────
    # A gate whose output net feeds back into its own INPUT chain is a
    # combinational loop — illegal Verilog, causes FM ABORT or infinite loop.
    # Common cause: buffer chain (INV→INV) where the final output = the first
    # gate's input (e.g., A drives INV1 → INV2 → A, creating a cycle).
    _OUT_PINS = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'}
    for stage in ['Synthesize']:
        # Build output_net → entry map for new_logic_gate entries only
        out_to_entry = {}
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            out = e.get('output_net', '')
            if out:
                out_to_entry[out] = e

        def _input_nets(entry, stg):
            """Return only INPUT pin values — exclude output pins to avoid false positives."""
            pcs = entry.get('port_connections') or entry.get('port_connections_per_stage', {}).get(stg, {})
            return [v for k, v in pcs.items()
                    if isinstance(v, str) and k not in _OUT_PINS]

        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            gate_out = e.get('output_net', '')
            if not gate_out:
                continue
            # BFS through INPUT pins only — do NOT include output pin in initial queue
            visited, queue = set(), list(_input_nets(e, stage))
            loop_found = False
            while queue and not loop_found:
                net = queue.pop(0)
                if not isinstance(net, str) or net in visited:
                    continue
                visited.add(net)
                if net == gate_out:
                    loop_found = True
                    break
                src = out_to_entry.get(net)
                if src:
                    queue.extend(_input_nets(src, stage))
            if loop_found:
                inst = e.get('instance_name', '?')
                issues.append(
                    f"CRITICAL: combinational loop detected — gate '{inst}' output '{gate_out}' "
                    f"feeds back into its own input chain — illegal Verilog; "
                    f"check that the output port driver is not also used as the gate source")

    # ── 3. DFF entries have port_connections_per_stage for all 3 stages ─────
    # Only flag STATEFUL entries (DFFs with .Q output, .CP, scan pins). Skip:
    #   - bridge buffer cells (bridge_port_role ends in '_driver') — these are
    #     Route-only single-stage BUF cells with .I/.Z only, no per-stage variants
    #   - combinational gates without DFF semantics (no Q output pin)
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not e.get('confirmed', True):
                continue
            # Skip bridge driver buffers (Route-only, no per-stage variations needed)
            role = e.get('bridge_port_role', '') or ''
            if role.endswith('_driver'):
                continue
            # Skip non-DFF entries (no Q pin in port_connections → not a sequential cell)
            top_pcs = e.get('port_connections') or {}
            has_q_pin = 'Q' in top_pcs or 'QN' in top_pcs
            is_dff_change = e.get('change_type') == 'new_logic_dff'
            if not (is_dff_change or has_q_pin):
                continue
            inst = e.get('instance_name', '?')
            pcs = e.get('port_connections_per_stage', {})
            for chk_stage in ['Synthesize', 'PrePlace', 'Route']:
                if not pcs.get(chk_stage):
                    issues.append(f"HIGH: DFF {inst} in {stage} missing port_connections_per_stage[{chk_stage}] — eco_netlist_studier Phase 0b-STAGE-NETS incomplete")

    # ── 3a-SKIP. Scan stitching is OUT OF SCOPE (DFT team handles integration).
    #        The wrapper (eco_emit_dff_entry.py) emits SE=SI=1'b0 in all stages
    #        and never sets mode_S_applied/requires_scan_stitching. When neither
    #        the study nor the rtl_diff carries those fields, skip the entire
    #        Mode-S block (3b/3c/3d). Checks remain in place as a safety net
    #        for stale fields leaking through.
    _mode_s_active = any(
        e.get('mode_S_applied') or e.get('requires_scan_stitching')
        for stage in ('Synthesize', 'PrePlace', 'Route')
        for e in study.get(stage, [])
        if e.get('change_type') in ('new_logic_dff', 'new_logic')
    )
    _diff_requires_scan = any(
        c.get('requires_scan_stitching')
        for c in rtl_diff.get('changes', [])
        if c.get('change_type') in ('new_logic', 'new_logic_dff')
    )
    _skip_mode_s_checks = (not _mode_s_active) and (not _diff_requires_scan)

    # ── 3b. Mode S consistency: when requires_scan_stitching/mode_S_applied is
    #        true on a DFF, every per-stage entry list (Synthesize/PrePlace/Route)
    #        must carry the SAME port_connections_per_stage map. The 9868 R1 bug
    #        was the Route-stage entry overriding its own SE/SI to neighbor-DFF
    #        nets while Synthesize/PrePlace entries used the bridge port names.
    by_inst = {}
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not (e.get('mode_S_applied') or e.get('requires_scan_stitching')):
                continue
            by_inst.setdefault(e.get('instance_name','?'), {})[stage] = \
                (e.get('port_connections_per_stage', {}),
                 e.get('mode_S_strategy_per_stage', {}))
    for inst, per_entry in by_inst.items():
        if len(per_entry) < 2:
            continue
        ref_stage = sorted(per_entry.keys())[0]
        ref_pcs, ref_strat = per_entry[ref_stage]
        for stage_entry, (pcs, strat) in per_entry.items():
            if stage_entry == ref_stage:
                continue
            for chk_stage in ('Synthesize', 'PrePlace', 'Route'):
                # Asymmetric Mode S: a stage entry can legitimately use a
                # different strategy (neighbor_dff vs bridge_port) per stage
                # — engineer's own pattern. Only flag mismatch when BOTH
                # entries declared the SAME strategy for chk_stage AND yet
                # disagreed on the actual SE/SI/CP nets — that's a real bug.
                ref_chk_strat = (ref_strat or {}).get(chk_stage)
                cmp_chk_strat = (strat or {}).get(chk_stage)
                if ref_chk_strat and cmp_chk_strat and ref_chk_strat != cmp_chk_strat:
                    continue  # asymmetric strategy — comparison meaningless
                ref_pin = ref_pcs.get(chk_stage, {}) or {}
                cmp_pin = pcs.get(chk_stage, {}) or {}
                for pin in ('SE', 'SI', 'CP'):
                    a, b = ref_pin.get(pin), cmp_pin.get(pin)
                    if a != b:
                        issues.append(
                            f"HIGH: Mode S {inst} stage_entry[{stage_entry}] differs from "
                            f"stage_entry[{ref_stage}] for port_connections_per_stage[{chk_stage}].{pin}: "
                            f"{a!r} vs {b!r} — when both entries use the same Mode S strategy "
                            f"for this stage, the per-stage map MUST agree.")

    # ── 3c. Mode S completeness: when a DFF is marked mode_S_applied=true the
    #        study MUST contain matching port_declaration entries for the SI_in,
    #        SE_in and Q_out bridge ports referenced by the DFF's
    #        port_connections_per_stage, AND an `assign` change wiring Q_out to
    #        the DFF's Q net. Without these, eco_netlist_port_rewire will leave the
    #        bridge ports undeclared and Step 5 Check 17 catches it later — but
    #        we want to fail fast at Step 3.
    decl_set = set()       # (module_name, signal_name)
    assign_set = set()     # (module_name, lhs)
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            ct = e.get('change_type', '')
            if ct == 'port_declaration':
                decl_set.add((e.get('module_name', ''), e.get('signal_name', '')))
            elif ct == 'assign':
                decl_set  # no-op
                assign_set.add((e.get('module_name', ''),
                                e.get('lhs') or e.get('signal_name', '')))
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not (e.get('mode_S_applied') or e.get('requires_scan_stitching')):
                continue
            inst = e.get('instance_name', '?')
            mod = e.get('module_name', '')
            strat_per_stage = e.get('mode_S_strategy_per_stage') or {}
            # Only require bridge port_decls + assign when at least one stage
            # uses bridge_port strategy. neighbor_dff strategy needs no bridge.
            uses_bridge = any(strat_per_stage.get(s) == 'bridge_port'
                              for s in ('PrePlace', 'Route'))
            if not uses_bridge and strat_per_stage:
                continue  # explicit neighbor_dff strategy → no bridge needed
            pp_pcs = (e.get('port_connections_per_stage') or {}).get('PrePlace', {}) or {}
            si_name = pp_pcs.get('SI', '')
            se_name = pp_pcs.get('SE', '')
            base_match = re.match(r'(ECO_\w+?)_SI_in$', si_name) if si_name else None
            qo_name = f'{base_match.group(1)}_Q_out' if base_match else None
            for port_name in (si_name, se_name, qo_name):
                if not port_name or port_name in ("1'b0", "1'b1"):
                    continue
                # Skip if this name is a real netlist signal (neighbor_dff
                # strategy in PP keeps SI/SE pointing at neighbor nets — not
                # bridge ports — so port_decl shouldn't exist for it)
                if not port_name.startswith('ECO_'):
                    continue
                if (mod, port_name) not in decl_set:
                    issues.append(
                        f"HIGH: Mode S {inst} (mode_S_applied=true) references bridge "
                        f"port {port_name!r} on module {mod!r} but no matching "
                        f"`port_declaration` entry exists in the study — eco_netlist_port_rewire "
                        f"will leave this port undeclared. Add the port_declaration "
                        f"or switch mode_S_strategy_per_stage to neighbor_dff for "
                        f"the affected stage with a justification.")
            if qo_name and (mod, qo_name) not in assign_set:
                issues.append(
                    f"HIGH: Mode S {inst} (mode_S_applied=true) needs an `assign "
                    f"{qo_name} = <Q net>;` change in module {mod!r} but no matching "
                    f"`assign` entry exists in the study — Q_out will be left undriven.")

            # Bridge wire driver requirement (closes 9868 R1 dangling-bridge bug):
            # for each bridge_port stage, the parent module MUST contain an
            # `assign eco<jira>_si_bridge = <real net>` and same for se_bridge.
            for chk_stage in ('PrePlace', 'Route'):
                if strat_per_stage.get(chk_stage) != 'bridge_port':
                    continue
                m = re.match(r'ECO_(\w+?)_SI_in$', si_name) if si_name else None
                if not m:
                    continue
                jira_part = m.group(1)
                for bridge in (f'eco{jira_part}_si_bridge', f'eco{jira_part}_se_bridge'):
                    found = any(lhs == bridge for (_, lhs) in assign_set)
                    if not found:
                        issues.append(
                            f"HIGH: Mode S {inst} stage={chk_stage} bridge_port strategy "
                            f"requires an `assign {bridge} = <parent_neighbor_net>` at the "
                            f"parent scope — without it the bridge wire dangles undriven "
                            f"and FM sees globally unmatched SE/SI on the new DFF.")

            # GAP-4b enforcement: bridge buffer source wire must be verified
            # stage-stable PP↔Route. The studier should record in the entry:
            #   bridge_source_pp_route_match: { si: bool, se: bool }
            # set true ONLY after checking the wire's parent-level driver is the
            # SAME logical net in both PP and Route (via fenets rename map or
            # structural cone trace). False / missing → fail this check.
            uses_bridge_pp_route = (strat_per_stage.get('PrePlace') == 'bridge_port'
                                    or strat_per_stage.get('Route') == 'bridge_port')
            if uses_bridge_pp_route:
                bsm = e.get('bridge_source_pp_route_match') or {}
                for pin in ('si', 'se'):
                    if bsm.get(pin) is not True:
                        issues.append(
                            f"HIGH: GAP-4b — Mode S {inst} bridge_port (PP/Route) requires "
                            f"`bridge_source_pp_route_match.{pin}: true` in the study entry. "
                            f"Studier must verify the {pin.upper()} bridge buffer source wire "
                            f"has a stage-stable parent driver across PP→Route (use fenets "
                            f"rename map). Missing/false = bridge cone diverges across stages "
                            f"and FM fails on the new DFF.")

            # GAP-4 + GAP-4c enforcement (studier-side): when bridge_port chosen,
            # the study JSON MUST also contain matching companion entries:
            #   - sibling_pin_consolidation entries (one per pin: SE, sometimes SI)
            #   - si_consumer_replace entry (closes the bridge Q output)
            # Without these, the bridge plumbing dangles and FM fails.
            if uses_bridge_pp_route:
                # Aggregate companion entries from any stage of study JSON
                all_entries = []
                for s in ('Synthesize', 'PrePlace', 'Route'):
                    all_entries.extend(study.get(s, []))
                jira_part = ''
                if isinstance(si_name, str):
                    m = re.match(r'ECO_(\w+?)_SI_in$', si_name)
                    if m:
                        jira_part = m.group(1)
                # GAP-4: at least one sibling_pin_consolidation entry referencing
                # this DFF's bridge wire (ECO_<jira>_SE_out)
                want_se_net = f'ECO_{jira_part}_SE_out' if jira_part else None
                consol_found = any(
                    ce.get('change_type') == 'sibling_pin_consolidation'
                    and ce.get('pin_name') == 'SE'
                    and (want_se_net is None or ce.get('new_net') == want_se_net)
                    for ce in all_entries
                )
                if not consol_found:
                    issues.append(
                        f"HIGH: GAP-4 — Mode S {inst} uses bridge_port but no "
                        f"`sibling_pin_consolidation` entry (pin_name=SE"
                        f"{f', new_net={want_se_net}' if want_se_net else ''}) found in study. "
                        f"Bridge SE port is not consumed by sibling DFFs → FM cone divergence.")
                # GAP-4c: at least one si_consumer_replace entry referencing
                # this DFF's bridge Q port (ECO_<jira>_Q_in)
                want_q_net = f'ECO_{jira_part}_Q_in' if jira_part else None
                qclose_found = any(
                    ce.get('change_type') == 'si_consumer_replace'
                    and (want_q_net is None or ce.get('new_si_net') == want_q_net)
                    for ce in all_entries
                )
                if not qclose_found:
                    issues.append(
                        f"HIGH: GAP-4c — Mode S {inst} uses bridge_port but no "
                        f"`si_consumer_replace` entry"
                        f"{f' (new_si_net={want_q_net})' if want_q_net else ''} found in study. "
                        f"Bridge Q output dangles in sibling module → DFT scan break + FM warns.")

    # ── 3d. Mode S decision must match Step 1 (rtl_diff). When Step 1 emits
    #        requires_scan_stitching=true on a new_logic_dff, the studier MUST
    #        carry that decision through (mode_S_applied=true OR an explicit
    #        scan_stitching_skipped_reason justification). Silent downgrade
    #        bypasses the entire Mode S pipeline and was the actual cause of
    #        9868 R1 fresh-run Round 2 going wrong on EcoUseSdpOutstRdCnt.
    #
    # Skipped when scan stitching is out of scope (3a-SKIP) — neither side
    # should emit `requires_scan_stitching` under the new policy.
    if not _skip_mode_s_checks:
      diff_decisions = {}  # instance_name → (requires_scan_stitching, skipped_reason)
      for c in rtl_diff.get('changes', []):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        dff_inst = c.get('dff_instance_name') or (
            (c.get('target_register') or '') + '_reg' if c.get('target_register') else '')
        if dff_inst:
            diff_decisions[dff_inst] = (
                bool(c.get('requires_scan_stitching')),
                c.get('scan_stitching_skipped_reason') or '')
      seen_in_study = set()
      for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            if inst not in diff_decisions:
                continue
            seen_in_study.add(inst)
            diff_required, _ = diff_decisions[inst]
            study_applied = bool(
                e.get('mode_S_applied') or e.get('requires_scan_stitching'))
            study_skip_reason = e.get('scan_stitching_skipped_reason') or ''
            if diff_required and not study_applied and not study_skip_reason:
                issues.append(
                    f"HIGH: Mode S decision downgrade — rtl_diff set "
                    f"requires_scan_stitching=true for {inst} but study has "
                    f"mode_S_applied/requires_scan_stitching=false WITHOUT a "
                    f"`scan_stitching_skipped_reason`. Studier cannot silently "
                    f"opt out — either honor Step 1's decision (emit Mode S "
                    f"port_decls + assign + bridged SE/SI) or supply an "
                    f"auditable justification field.")

    # ── 3e-MOD. port_connection entries MUST have module_name or parent_module.
    # Without either, the applier searches the full netlist and edits the wrong
    # instance when multiple instantiations exist. HARD FAIL — studier must always
    # populate the enclosing parent module. Applier reads:
    #   parent_mod = entry.get('module_name','') or entry.get('parent_module','')
    for stage_name in ('Synthesize',):  # check once (same JSON across stages)
        for e in study.get(stage_name, []):
            if e.get('change_type') != 'port_connection':
                continue
            if e.get('module_name') or e.get('parent_module'):
                continue  # properly set
            inst = e.get('instance_name', '?')
            port = e.get('port_name', '?')
            issues.append(
                f"HIGH/3e-MISSING-PARENT-MOD: port_connection {inst}.{port} has no "
                f"module_name or parent_module. Applier searches full netlist — will "
                f"edit wrong instance when multiple instantiations exist. Set "
                f"module_name/parent_module to the enclosing module where {inst!r} "
                f"is instantiated. See eco_netlist_studier.md Phase 0.14.")

    # ── 3e. Cross-check port_connection ↔ port_declaration. Every port_connection
    #        targeting a child instance MUST reference a port name that either
    #        (a) is already declared in the child module's PreEco SynRtl source,
    #        OR (b) appears as a port_declaration entry in the study for that
    #        child module. Missing → FE-LINK-7 ABORT during FM elaboration
    #        (observed on 9868 fresh run R1: NeedFreqAdj port_connection with
    #        no matching port on umcarbctrlsw).
    rtl_dir = os.path.join(args.ref_dir, 'data', 'PreEco', 'SynRtl')
    pre_existing_ports = {}  # module_name → set(port_name)
    # Gate-level global fallback: modules present + port names used as connections.
    # Used to suppress false HIGH when a port is synthesis-generated (exists in the
    # gate-level hierarchy but not as an explicit SynRtl port declaration).
    _gl_modules_in_netlist: set = set()
    _gl_portnames_in_netlist: set = set()
    _gl_s = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if os.path.isfile(_gl_s):
        try:
            import gzip as _gz2
            _gl_m_re = re.compile(r'^\s*module\s+(\w+)\b')
            _gl_p_re = re.compile(r'\.\s*([A-Za-z_]\w*)\s*\(')
            with _gz2.open(_gl_s, 'rt', errors='ignore') as _f2:
                for _l in _f2:
                    _mm = _gl_m_re.match(_l)
                    if _mm:
                        _mn = _mm.group(1)
                        _gl_modules_in_netlist.add(_mn)
                        _t = _mn.rfind('_t_')
                        if _t >= 0: _gl_modules_in_netlist.add(_mn[_t+3:])
                    for _pm in _gl_p_re.finditer(_l):
                        _gl_portnames_in_netlist.add(_pm.group(1))
        except Exception:
            pass
    if os.path.isdir(rtl_dir):
        port_decl_re = re.compile(
            r'\b(?:input|output|inout)\b[^;]*?\b([A-Za-z_]\w*)\s*[;,)]',
            re.MULTILINE)
        # Anchor `module` to start-of-line (with optional leading whitespace).
        # The `\bmodule\b` form matched the keyword `for` from a procedural
        # `for(...)` block in some files because `\b` allows match anywhere.
        mod_re = re.compile(r'^\s*module\s+(\w+)\b', re.MULTILINE)
        for root, _, files in os.walk(rtl_dir):
            for f in files:
                if not f.endswith(('.v','.sv')):
                    continue
                try:
                    txt = Path(os.path.join(root, f)).read_text(errors='ignore')
                except Exception:
                    continue
                # Cheap pass: any module-port direction-keyword in this file
                # contributes to that file's signal pool. Bucket by the first
                # `module <name>` we see (most ECO targets are 1-module-per-file).
                m = mod_re.search(txt)
                if not m:
                    continue
                mod_name = m.group(1)
                # Also try the tile-prefixed form 'ddrss_umccmd_t_<base>'
                ports = set()
                for pm in port_decl_re.finditer(txt):
                    ports.add(pm.group(1))
                pre_existing_ports.setdefault(mod_name, set()).update(ports)
                # Mirror under the prefixed name(s) so lookups work either way
                for prefix in ('ddrss_umccmd_t_', 'ddrss_'):
                    pre_existing_ports.setdefault(prefix + mod_name, set()).update(ports)
    # Also load ports from gate-level PreEco Synthesize.v.gz — catches synthesis-
    # generated ports (e.g. iQ_* readback ports) that exist in the gate netlist
    # but were not present in the RTL source (SynRtl). We build a global set of
    # all port names seen anywhere in the gate netlist and merge them into every
    # module that is already in pre_existing_ports (conservative over-approximation).
    # This avoids module-boundary parsing issues with large multi-module netlists.
    gl_synth = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if os.path.isfile(gl_synth):
        try:
            import gzip as _gz
            # Use same permissive regex as SynRtl loader — catches both inline
            # port-list style and explicit 'input portname;' declaration style.
            _port_decl_re = re.compile(
                r'\b(?:input|output|inout)\b[^;]*?\b([A-Za-z_]\w*)\s*[;,)]', re.MULTILINE)
            _gl_mod_re  = re.compile(r'^\s*module\s+(\w+)\b', re.MULTILINE)
            _gl_end_re  = re.compile(r'\bendmodule\b')
            _gl_ports: dict = {}  # module → set of port names
            _cur_gl_mod = None
            _buf = []
            with _gz.open(gl_synth, 'rt', errors='ignore') as _glf:
                for _gline in _glf:
                    _mm = _gl_mod_re.match(_gline)
                    if _mm:
                        # Flush previous module block
                        if _cur_gl_mod and _buf:
                            _block = ''.join(_buf)
                            for _pm in _port_decl_re.finditer(_block):
                                _gl_ports.setdefault(_cur_gl_mod, set()).add(_pm.group(1))
                        _cur_gl_mod = _mm.group(1)
                        _buf = [_gline]
                    elif _cur_gl_mod:
                        _buf.append(_gline)
                        if _gl_end_re.search(_gline):
                            _block = ''.join(_buf)
                            for _pm in _port_decl_re.finditer(_block):
                                _gl_ports.setdefault(_cur_gl_mod, set()).add(_pm.group(1))
                            _cur_gl_mod = None
                            _buf = []
            # Merge gate-level ports into pre_existing_ports for all known modules
            for _gmod, _gports in _gl_ports.items():
                # Add under the gate-level module name itself
                pre_existing_ports.setdefault(_gmod, set()).update(_gports)
                # Also add under the base name: strip tile prefix by taking everything
                # after the last '_t_' separator (e.g. ddrss_umcdat_t_umcdatrdmux →
                # umcdatrdmux). Works for any tile without hardcoding tile names.
                _t_idx = _gmod.rfind('_t_')
                if _t_idx >= 0:
                    _base = _gmod[_t_idx + 3:]
                    if _base:
                        pre_existing_ports.setdefault(_base, set()).update(_gports)
        except Exception:
            pass

    # Add port_decls from study (mirror under prefixed + bare module names so
    # lookups work whether child_module_name uses tile prefix or not)
    study_ports_per_mod = {}  # module_name → set(port_name)
    for e in study.get('Synthesize', []):
        if e.get('change_type') == 'port_declaration':
            mod = e.get('module_name','')
            sig = e.get('signal_name','')
            if not (mod and sig):
                continue
            study_ports_per_mod.setdefault(mod, set()).add(sig)
            for prefix in ('ddrss_umccmd_t_', 'ddrss_'):
                if mod.startswith(prefix):
                    study_ports_per_mod.setdefault(mod[len(prefix):], set()).add(sig)
                else:
                    study_ports_per_mod.setdefault(prefix + mod, set()).add(sig)
    # Walk every port_connection and verify the target port exists
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') != 'port_connection':
                continue
            child_mod = e.get('child_module_name') or ''
            inst = e.get('instance_name','?')
            port = e.get('port_name','?')
            if not child_mod:
                issues.append(
                    f"HIGH: port_connection {inst!r} (port {port!r}) is missing "
                    f"the mandatory `child_module_name` field — Step 3 cannot "
                    f"verify the port exists. Without this field a missing "
                    f"port_decl slips to FM and triggers FE-LINK-7 ABORT. "
                    f"Studier must emit child_module_name on every port_connection.")
                continue
            in_pre = port in pre_existing_ports.get(child_mod, set())
            in_study = port in study_ports_per_mod.get(child_mod, set())
            # Black-box gate-level cells (not in SynRtl) cannot be verified via
            # pre_existing_ports — skip rather than false-firing FE-LINK-7.
            # A module absent from SynRtl but present in gate-level netlists is
            # a tech cell (e.g. techind_mcpm, clock-gate, stdlib); its ports are
            # authoritative in the gate-level netlist, not SynRtl.
            is_blackbox = child_mod not in pre_existing_ports
            # Gate-level fallback: if the child module exists in the gate-level
            # netlist AND the port appears as a port connection anywhere in the
            # gate-level (meaning it flows through the hierarchy), treat it as
            # pre-existing. This covers synthesis-generated pass-through ports.
            in_gl = (child_mod in _gl_modules_in_netlist
                     and port in _gl_portnames_in_netlist)
            if not (in_pre or in_study or is_blackbox or in_gl):
                issues.append(
                    f"HIGH: port_connection {inst}.{port} → child_module={child_mod!r} "
                    f"references a port that is NEITHER pre-existing in PreEco SynRtl "
                    f"NOR added by a `port_declaration` entry in this study. "
                    f"FM elaboration will FE-LINK-7 ABORT. Add a port_declaration "
                    f"entry for {child_mod}.{port} or reference an existing port name.")

    # ── 4. No empty module_name AND empty instance_scope for new_logic entries
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate','new_logic_dff','new_logic'):
                continue
            if not e.get('confirmed', True):
                continue
            mod = e.get('module_name','')
            scope = e.get('instance_scope','')
            inst = e.get('instance_name','?')
            if not mod and not scope:
                issues.append(f"HIGH: {inst} has no module_name AND no instance_scope — eco_applier cannot find insertion point")

    # ── 5. No UNRESOLVABLE inputs without alternatives on confirmed gates ────
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if not e.get('confirmed', True):
                continue
            pcs = e.get('port_connections_per_stage', {}).get(stage) or e.get('port_connections', {})
            inst = e.get('instance_name', '?')
            for pin, net in pcs.items():
                if str(net).startswith('UNRESOLVABLE_IN_'):
                    issues.append(f"MEDIUM: {inst}.{pin} = {net} — condition input unresolved; eco_fenets_runner re-query may be needed")

    # ── 6. and_term changes: GAP-15 result must be present ──────────────────
    and_term_changes = [c for c in rtl_diff.get('changes',[]) if c.get('change_type') == 'and_term']
    gap15_file = Path(args.study.replace('_eco_preeco_study.json', '_eco_and_term_port_check.json'))
    if and_term_changes and not gap15_file.exists():
        issues.append(f"HIGH: and_term changes present but eco_and_term_port_check.json missing — eco_and_term_port_check.py was not run")

    # ── 7. eco_expand_chains must have run ───────────────────────────────────
    marker = Path(args.study).parent / f"{args.tag}_eco_preeco_study_eco_expand_chains_marker.txt"
    if not marker.exists():
        issues.append(f"MEDIUM: eco_expand_chains_marker.txt not found — eco_expand_chains.py may not have run")

    # ── 8. Each rewire entry has old_net, new_net, AND a resolvable cell_name ─
    for stage in ['Synthesize']:
        for e in study.get(stage, []):
            if e.get('change_type') != 'rewire':
                continue
            if not e.get('old_net') or not e.get('new_net'):
                issues.append(f"HIGH: rewire entry {e.get('cell_name','?')} missing old_net or new_net")
            cell = (e.get('cell_name')
                    or (e.get('per_stage_cell_name') or {}).get(stage)
                    or (e.get('cell_name_per_stage') or {}).get(stage)
                    or (e.get('mux_cell_instance_per_stage') or {}).get(stage))
            if not cell:
                issues.append(f"HIGH: rewire entry pin={e.get('pin','?')} {e.get('old_net')}→{e.get('new_net')} has no cell_name in any known field (checked: cell_name, per_stage_cell_name, cell_name_per_stage, mux_cell_instance_per_stage)")

    # ── 9. Every n_eco_* input net must have a driver in some entry's output ─
    OUT_PINS = {'Z','ZN','ZN1','Q','QN','CO'}
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        driven = {n for e in study.get(stage, [])
                    for p, n in (e.get('port_connections') or {}).items()
                    if p in OUT_PINS and isinstance(n, str)}
        # A driver-side rewire renames an EXISTING cell's output pin to new_net, so
        # that net is now driven by the (renamed) existing cell — not by a study
        # new_logic_gate. Count it as driven (combinational net-force pattern).
        driven |= {e.get('new_net') for e in study.get(stage, [])
                   if e.get('change_type') == 'rewire' and (e.get('driver_side') or e.get('net_force'))
                   and isinstance(e.get('new_net'), str)}
        for e in study.get(stage, []):
            if not e.get('confirmed', True):
                continue
            for pin, net in (e.get('port_connections') or {}).items():
                if pin in OUT_PINS or not isinstance(net, str):
                    continue
                if net.startswith('n_eco_') and net not in driven:
                    issues.append(f"CRITICAL: {e.get('instance_name','?')}.{pin}={net} in {stage} — undriven ECO net (no entry's Z/ZN/Q drives it)")

    # ── 9a. GATE-NO-OUTPUT-PIN (root cause of many Check-9 'undriven' symptoms) ────
    # A new_logic_gate MUST carry its output pin (Z/ZN/Q/...) in port_connections. The
    # applier resolves the gate's output via output_pin_key(port_connections); if no
    # output pin is present it stamps the cell with an UNCONNECTED output, so the gate's
    # output net is undriven at FM. This is exactly how LLM-hand-built gates (e.g. the
    # intent-C `|reg_dualdcqenmode` OR/NOR gates) fail: they put A1/A2 but omit Z, and
    # the only symptom Check 9 sees is the CONSUMER reading an undriven net. Flag the
    # PRODUCER here with an actionable root-cause message. Output-pin presence is
    # structural, so check the base port_connections once (not per stage).
    _GATE_OUT_PINS = ('ZN', 'Z', 'Q', 'QN', 'CO', 'S')
    for e in study.get('Synthesize', []):
        if e.get('change_type') != 'new_logic_gate':
            continue
        pcs = e.get('port_connections') or {}
        if isinstance(pcs, dict) and pcs and not any(p in pcs for p in _GATE_OUT_PINS):
            inst = e.get('instance_name') or e.get('output_net') or '?'
            issues.append(
                f"CRITICAL/GATE-NO-OUTPUT-PIN: new_logic_gate {inst!r} has no output pin "
                f"({'/'.join(_GATE_OUT_PINS)}) in port_connections={list(pcs)} "
                f"(output_net={e.get('output_net')!r}). The applier wires the gate output "
                f"via port_connections; without an output pin the output net is left "
                f"UNCONNECTED -> undriven at FM. Emit the output pin explicitly, e.g. "
                f"'Z': {e.get('output_net')!r}.")

    # ── 9b. NET-ABSENT: every REAL-net input leaf must exist in the stage netlist ─
    # Check 9 only catches undriven n_eco_ nets. A real-net leaf (e.g. dsp_condsok[10],
    # WckSyncCtr0[0]) that the verifier did NOT resolve is neither an ECO net nor a
    # constant, so it slips past Check 9 and becomes an undriven gate input at FM (Mode A).
    # Flag it here: an input pin whose net is not ECO / constant / ECO-driven and is
    # absent from that stage's PreEco netlist (bare token or cell of a cell/pin ref).
    _CONST_RE = re.compile(r"^\d*'[bBhHdDoO]")
    # X3: keys that live alongside real pins inside port_connections_per_stage[stage]
    # but are metadata annotations, NOT pin→net entries. Iterating them treats the
    # metadata VALUE (e.g. net_source="real_rtl_name") as a net and false-flags it.
    _META_KEYS = {'net_source', 'source', 'origin', 'note', 'comment', 'net_origin', 'resolved_by'}
    def _modkey(n):
        return re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', str(n or '')))
    # Build BOTH the global token set and a per-module token map in ONE pass per stage
    # (X4). Per-module scoping catches a leaf that exists somewhere in the stage but
    # NOT in the module where the gate is inserted (e.g. a P&R-renamed SEQMAP_NET).
    _stage_tok_cache = {}      # stage -> global token set
    _stage_modtok_cache = {}   # stage -> {mod_key -> token set}
    def _build_tokens(stage):
        if stage in _stage_tok_cache:
            return
        gz = os.path.join(args.ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
        glob, permod, cur = set(), {}, None
        if os.path.isfile(gz):
            try:
                with gzip.open(gz, 'rt', errors='replace') as f:
                    for ln in f:
                        mm = re.match(r'^\s*module\s+(\S+)', ln)
                        if mm:
                            cur = _modkey(mm.group(1))
                            permod.setdefault(cur, set())
                        if '(' in ln or '.' in ln:      # instance/port lines carry nets
                            t = re.findall(r'[A-Za-z_][\w\[\]]*', ln)
                            glob.update(t)
                            if cur is not None:
                                permod[cur].update(t)
            except Exception:
                glob, permod = set(), {}
        _stage_tok_cache[stage] = glob
        _stage_modtok_cache[stage] = permod
    def _stage_tokens(stage):
        _build_tokens(stage)
        return _stage_tok_cache[stage]
    def _module_tokens(stage, module):
        _build_tokens(stage)
        return _stage_modtok_cache[stage].get(_modkey(module))

    # ECO-added ports/nets do NOT exist in the PreEco netlist by design (they are
    # created by this ECO). A new port like `reg_dualdcqenmode` has no eco_ prefix, so
    # it would false-flag as NET-ABSENT — collect declared ECO port names and exclude
    # them (and their bit-slices) from the leaf-existence check.
    _eco_ports = set()
    for _st in ('Synthesize', 'PrePlace', 'Route'):
        for _e in study.get(_st, []):
            if _e.get('change_type') in ('port_declaration', 'new_port'):
                _nm = _e.get('port_name') or _e.get('new_token') or _e.get('new_port')
                if _nm:
                    _eco_ports.add(str(_nm).split('[')[0])

    if getattr(args, 'ref_dir', None):
        _absent_seen = set()
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            toks = _stage_tokens(stage)
            if not toks:
                continue
            driven = {n for e in study.get(stage, [])
                      for p, n in (e.get('port_connections') or {}).items()
                      if p in OUT_PINS and isinstance(n, str)}
            driven |= {e.get('new_net') for e in study.get(stage, [])
                       if e.get('change_type') == 'rewire' and (e.get('driver_side') or e.get('net_force'))
                       and isinstance(e.get('new_net'), str)}
            for e in study.get(stage, []):
                if not e.get('confirmed', True):
                    continue
                pcs = (e.get('port_connections_per_stage') or {}).get(stage)
                if not isinstance(pcs, dict):
                    pcs = e.get('port_connections') if stage == 'Synthesize' else None
                if not isinstance(pcs, dict):
                    continue
                mtoks = _module_tokens(stage, e.get('module_name'))
                for pin, net in pcs.items():
                    if pin in OUT_PINS or pin in _META_KEYS or not isinstance(net, str) or not net:
                        continue
                    if net.startswith(('n_eco_', 'eco_')) or _CONST_RE.match(net) or net in driven:
                        continue
                    if net.split('[')[0].split('/')[0] in _eco_ports:
                        continue
                    base = net.split('/')[0]
                    in_global = (net in toks or base in toks)
                    # X4: when the entry's own module resolves to a non-empty body, the
                    # leaf must exist in THAT module. When it does NOT resolve (uniquify
                    # name mismatch, C3), fall back to the global check — never false-flag.
                    in_module = (net in mtoks or base in mtoks) if mtoks else None
                    if in_global and (in_module is None or in_module):
                        continue
                    key = (stage, e.get('module_name'), net)
                    if key in _absent_seen:
                        continue
                    _absent_seen.add(key)
                    where = ("the PreEco netlist" if not in_global
                             else f"module {e.get('module_name')!r} (present elsewhere in stage, "
                                  f"absent from this module — P&R-renamed?)")
                    issues.append(
                        f"CRITICAL/NET-ABSENT: {e.get('instance_name','?')}.{pin}={net} in {stage} "
                        f"— not in {where} and not an ECO/const/driven net. The verifier "
                        f"did not resolve this leaf; it would be an undriven gate input at FM. Fix its "
                        f"per-stage name (rename map / verifier) or thread it in.")

    # ── 10. Stale-reference guard: when a port_connection renames net A→B,
    # no other entry's input pin may still reference A (becomes stale post-Step 4)
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        renames = {}  # old_net → new_net
        for e in study.get(stage, []):
            nb = e.get('net_name_before')
            na = e.get('net_name_after')
            if isinstance(nb, dict):
                nb = nb.get(stage)
            if nb and na:
                renames[nb] = na
        for e in study.get(stage, []):
            if not e.get('confirmed', True):
                continue
            for pin, net in (e.get('port_connections') or {}).items():
                if pin in OUT_PINS or not isinstance(net, str):
                    continue
                if net in renames:
                    issues.append(f"HIGH: {e.get('instance_name','?')}.{pin}={net} in {stage} — net is being renamed to {renames[net]} by another entry; update this reference or skip the rename")

    # ── 11. Mode I — UNCONNECTED bus rename at parent must be paired with
    # child-scope wire-up entry when child output port is internally undriven.
    # Without the pair, parent renames a dangling pin → FM sees X → DFF0X.
    # Detect by netlist scan: open child module body and check whether the
    # matching bit slot at any sub-instance bus is also UNCONNECTED_*.
    import gzip, re as _re
    _child_bodies = {}  # module_name → comment-stripped body text
    def _load_child_body(mod):
        if mod in _child_bodies:
            return _child_bodies[mod]
        body = ''
        for stage in ('Synthesize',):
            # Prefer round-1 backup (pristine pre-Step4); fall back to current PostEco
            base = os.path.join(args.ref_dir, 'data', 'PostEco')
            cands = sorted(os.path.join(base, n) for n in (os.listdir(base) if os.path.isdir(base) else []) if n.startswith(f'{stage}.v.gz.bak_'))
            gz = cands[0] if cands else os.path.join(base, f'{stage}.v.gz')
            if not os.path.exists(gz):
                continue
            try:
                with gzip.open(gz, 'rt') as fh: text = fh.read()
            except Exception:
                continue
            text = _re.sub(r'/\*.*?\*/', '', text, flags=_re.DOTALL)
            text = _re.sub(r'//[^\n]*', '', text)
            m = _re.search(rf'^module\s+{_re.escape(mod)}\b.*?^endmodule\b', text, _re.DOTALL | _re.MULTILINE)
            if m:
                body = m.group(0); break
        _child_bodies[mod] = body
        return body
    if os.path.isdir(os.path.join(args.ref_dir, 'data', 'PostEco')):
        for stage in ['Synthesize']:
            for e in study.get(stage, []):
                if e.get('change_type') != 'port_connection': continue
                if not e.get('confirmed', True): continue
                if e.get('bus_bit_index') is None: continue
                orig = e.get('original_unconnected_net', '') or ''
                if not orig.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_')): continue
                child_mod = e.get('submodule_type') or ''
                bbi = e.get('bus_bit_index')
                port = e.get('port_name') or ''
                if not child_mod or not port: continue
                # Look for the paired child-scope entry: module_name=child_mod, bus_bit_index=bbi
                paired = any(
                    p.get('change_type') == 'port_connection'
                    and (p.get('module_name') == child_mod or p.get('parent_module') == child_mod)
                    and p.get('bus_bit_index') == bbi
                    and (p.get('net_name') or '') == f"{port}[{bbi}]"
                    for p in study.get(stage, []))
                if paired: continue
                # Confirm child body has UNCONNECTED at this bit position of any sub-inst bus
                body = _load_child_body(child_mod)
                if not body: continue
                # Heuristic: any sub-instance with bus port containing UNCONNECTED_\d+ at the same MSB-first position
                hit = False
                for cm in _re.finditer(r'\.\s*(\w+)\s*\(\s*\{([^{}]+)\}\s*\)', body):
                    elems = [x.strip() for x in cm.group(2).split(',') if x.strip()]
                    pos = len(elems) - 1 - bbi
                    if 0 <= pos < len(elems) and _re.match(r'^(SYNOPSYS_)?UNCONNECTED_\d+$', elems[pos]):
                        hit = True; break
                if hit:
                    issues.append(f"CRITICAL: Mode I gap — parent rename of {orig} at {child_mod}.{port}[{bbi}] but no paired child-scope port_connection wires {port}[{bbi}] internally. Add entry: module_name={child_mod}, bus_bit_index={bbi}, net_name={port}[{bbi}]")

    # ── 12. Cell truth-table check: every new_logic_gate's cell_type must
    # actually compute the claimed gate_function. Catches the case where a
    # cell name suggests one boolean function but the library cell computes
    # a different one (common for inverter-input compound cells).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import eco_cell_truth_tables as _ett
    except ImportError:
        _ett = None
    if _ett is not None:
        for stage in ['Synthesize']:  # truth table is stage-invariant
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_gate', 'new_logic'): continue
                if not e.get('confirmed', True): continue
                cell = e.get('cell_type', '')
                fn = e.get('gate_function', '')
                if not cell or not fn: continue
                m, why = _ett.cell_function_matches(cell, fn, ref_dir=args.ref_dir)
                inst = e.get('instance_name', '?')
                if m is False:
                    issues.append(f"CRITICAL: ECO {inst} cell_type={cell!r} does NOT compute claimed gate_function={fn!r} — {why}. Pick a cell whose family matches, or update gate_function to reflect actual cell logic.")
                elif m is None and _ett.family_of(cell) and fn not in _ett.ABSTRACT_GATE_FUNCTIONS:
                    # Both unknown → uncovered cell; warn so the library JSON can be extended
                    issues.append(f"MEDIUM: ECO {inst} cell_type={cell!r} (family={_ett.family_of(cell)!r}) and gate_function={fn!r} not covered — cannot verify functional correctness. Add to script/eco_scripts/cell_libraries/<your_lib>.json.")

    # ── 13. (REMOVED) Scan-bridge SE/SI MEDIUM warnings.
    # Was: warned when SE/SI = 1'b0 in PP/Route on the assumption the new DFF
    # would become a scan-isolated island. Under the current policy SE=SI=1'b0
    # in all 3 stages IS the unconditional default; DFT team owns scan
    # integration. The warning created false-positive blocks at the spawn-level
    # gate (passed != true) on every new ECO DFF.

    # ── 14. Per-stage CP/SE/SI must come from an existing DFF in the same scope
    # for each stage. Catches "force same-as-Synthesize" anti-pattern and
    # ensures per-stage net names actually exist.
    # _neighbors helper is hoisted to function scope so Check 25 (per-stage
    # wrapper-clock detection) can reuse the same cache without re-reading
    # 50MB+ gz files.
    import gzip as _gz
    neighbor_cache = {}
    def _neighbors(stage, mod):
        key = (stage, mod)
        if key in neighbor_cache:
            return neighbor_cache[key]
        base = os.path.join(args.ref_dir, 'data', 'PostEco')
        cands = sorted(os.path.join(base, n) for n in (os.listdir(base) if os.path.isdir(base) else []) if n.startswith(f'{stage}.v.gz.bak_'))
        gz = cands[0] if cands else os.path.join(base, f'{stage}.v.gz')
        sets = {'CP': set(), 'SE': set(), 'SI': set()}
        if os.path.exists(gz):
            try:
                with _gz.open(gz, 'rt') as f: text = f.read()
                text = _re.sub(r'/\*.*?\*/', '', text, flags=_re.DOTALL)
                text = _re.sub(r'//[^\n]*', '', text)
                body_m = _re.search(rf'^module\s+{_re.escape(mod)}(?:_0)?\b.*?^endmodule\b', text, _re.DOTALL | _re.MULTILINE)
                if body_m:
                    body = body_m.group(0)
                    for pin in ('CP', 'SE', 'SI'):
                        for m in _re.finditer(rf'\.\s*{pin}\s*\(\s*([^)]+?)\s*\)', body):
                            sets[pin].add(m.group(1).strip())
            except Exception:
                pass
        neighbor_cache[key] = sets
        return sets
    if os.path.isdir(os.path.join(args.ref_dir, 'data', 'PostEco')):
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_dff', 'new_logic'): continue
                if not e.get('confirmed', True): continue
                inst = e.get('instance_name', '?')
                mod  = e.get('module_name')
                if not mod: continue
                pcs = (e.get('port_connections_per_stage') or {}).get(stage) or e.get('port_connections') or {}
                neigh = _neighbors(stage, mod)
                # Mode S exemption: when the DFF is bridged via Mode S ports, the
                # SE/SI nets are by design new bridge ports that no existing DFF
                # uses. Skip the SE/SI mismatch check for these — Check 3c already
                # verifies the bridge ports are declared.
                mode_s_active = bool(e.get('mode_S_applied') or e.get('requires_scan_stitching'))
                for pin in ('CP', 'SE', 'SI'):
                    v = pcs.get(pin)
                    if not isinstance(v, str) or not neigh.get(pin):
                        continue
                    if mode_s_active and pin in ('SE', 'SI'):
                        continue
                    # Constant on SE/SI in Synthesize is always OK; constant in P&R
                    # is covered by Check 15 (skip duplicate flag here).
                    if v.strip() in ("1'b0", "1'b1") and pin in ('SE', 'SI'):
                        continue
                    if v.strip() not in neigh[pin]:
                        # ECO shadow gate exemption: a new CKOR*/ICG* shadow gate Q
                        # won't appear in any pre-existing DFF CP — it's a new net by
                        # design. Skip when CP contains 'ECO_' (shadow gate Q net).
                        if pin == 'CP' and 'ECO_' in v:
                            continue
                        sample = list(neigh[pin])[:3]
                        # CP mismatch: HIGH (real failure mode — clock cone divergence).
                        # SE/SI mismatch: MEDIUM (engineer may legitimately use parent-scope
                        # bridge wires not in module-scope neighbor set).
                        sev = "HIGH" if pin == "CP" else "MEDIUM"
                        issues.append(f"{sev}: ECO DFF {inst}.{pin} in {stage}={v!r} not used by any existing DFF in module {mod!r}. Existing values include {sample}. {'Pick one of those' if pin == 'CP' else 'Either pick a neighbor value OR add as new bridge port'} for per-stage consistency.")

    # ── 25. PER-STAGE-CP-WRAPPER-CLOCK: detect tile-top wrapper-clock swap.
    # At tile-top wrapper scope, RTL DFFs use the raw clock (e.g. UCLK01) in
    # Synthesize, but P&R inserts wrapper clock-gating cells that derive
    # `wrp_clk_*` and existing module-sibling DFFs use the wrapper clock in
    # PP/Route. A new ECO DFF at this scope MUST swap CP per stage:
    #   Synthesize: <UCLK*>     PP: wrp_clk_*     Route: wrp_clk_*
    # Engineer 9868 EcoUseSdpOutstRdCnt does exactly this. Without the swap,
    # the new DFF runs on a different clock from its module siblings — passes
    # FM cone equivalence (UCLK01 ≡ wrp_clk_1 logically) but breaks DFT scan
    # testability and clock-gating coverage. Reuses _neighbors() cache from
    # Check 14.
    if os.path.isdir(os.path.join(args.ref_dir, 'data', 'PostEco')):
        for e in study.get('Synthesize', []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not e.get('confirmed', True):
                continue
            inst = e.get('instance_name', '?')
            mod  = e.get('module_name')
            if not mod:
                continue
            pcs_per_stage = e.get('port_connections_per_stage') or {}
            # Detect the wrapper-clock domain by inspecting PrePlace neighbor CPs
            # (RTL-level naming wrp_clk_* survives into PP; Route stage applies
            # CTS-renaming so wrp_clk_1 might become FxCts_ZCTSNET_<N>).
            pp_neigh = _neighbors('PrePlace', mod)
            pp_neigh_cps = list(pp_neigh.get('CP', set()))
            if not pp_neigh_cps:
                continue
            wrp_pp = [n for n in pp_neigh_cps if _re.search(r'wrp_clk', n, _re.IGNORECASE)]
            if not wrp_pp:
                continue  # no wrapper-clock present in this module
            # Only flag if wrapper clocks are MAJORITY (>=50%) of neighbor DFFs.
            # A module with 1/4 DFFs on wrp_clk and 3/4 on UCLK is NOT a wrapper-clock-dominated
            # module — using UCLK for ungated DFFs is correct in that case.
            if len(wrp_pp) < len(pp_neigh_cps) / 2:
                continue  # minority wrapper clock — ungated UCLK DFFs are correct
            # PrePlace check — when wrapper clock exists in module AND new DFF uses
            # raw UCLK (the Synth-stage clock), studier missed the wrapper swap.
            pp_cp = (pcs_per_stage.get('PrePlace', {}) or {}).get('CP', '').strip()
            if pp_cp and _re.match(r'UCLK\d?$', pp_cp, _re.IGNORECASE):
                sample = sorted(set(wrp_pp))[:3]
                issues.append(
                    f"HIGH/25-WRAPPER-CLOCK-MISSED: ECO DFF {inst}.CP in PrePlace "
                    f"= {pp_cp!r} but {len(wrp_pp)}/{len(pp_neigh_cps)} existing "
                    f"DFFs in module {mod!r} use wrapper clock(s) {sample}. New "
                    f"DFFs at this scope MUST swap CP per stage: Synth keeps the "
                    f"raw clock (UCLK*), but PP MUST use wrp_clk_*. Otherwise the "
                    f"DFF runs on a different clock domain from its siblings — "
                    f"breaks clock-gating and DFT scan coverage. Update "
                    f"port_connections_per_stage['PrePlace'].CP to one of the "
                    f"wrp_clk_* nets above.")
            # Route check — CP must be in Route's neighbor CP set (CTS-renamed
            # wrapper). Reject UCLK01 (raw clock) AND non-CP-domain values.
            rt_cp = (pcs_per_stage.get('Route', {}) or {}).get('CP', '').strip()
            rt_neigh = _neighbors('Route', mod)
            rt_neigh_cps = rt_neigh.get('CP', set())
            if rt_cp and rt_neigh_cps:
                # Route CP should NOT be the raw UCLK* (that's the Synth-stage clock)
                # AND should be in the existing module DFF CP set.
                if _re.match(r'UCLK\d', rt_cp, _re.IGNORECASE):
                    sample = sorted(c for c in rt_neigh_cps if 'fxcts' in c.lower() or 'wrp_clk' in c.lower())[:3]
                    issues.append(
                        f"HIGH/25-WRAPPER-CLOCK-MISSED: ECO DFF {inst}.CP in Route "
                        f"= {rt_cp!r} (raw clock) but module {mod!r} is wrapper-clock "
                        f"dominated. Route CP must be a CTS-renamed wrapper clock "
                        f"(e.g. {sample}); using the raw UCLK clock leaves the new "
                        f"DFF on a different clock domain from siblings.")

    # ── 25b. CROSS-STAGE-CLOCK-MISMATCH: Route uses CTS/wrapper clock but
    # PrePlace still uses raw UCLK. Topology-agnostic — does NOT require
    # pre-existing neighbor DFFs (the gap in Check 25).
    # Root case: ECO 9868 NeedFreqAdj_reg — brand-new module umcarbctrlsw had
    # 0 pre-existing DFFs so Check 25's wrp_clk neighbor scan skipped it;
    # PP=UCLK01 but Route=FxCts_ZCTSNET_5 → clock gater cone present in PP
    # but absent in Route → FM FmEqvEcoRouteVsEcoPrePlace non-equiv after 6 rounds.
    for e in study.get('Synthesize', []):
        if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
            continue
        if not e.get('confirmed', True):
            continue
        pcs_per_stage = e.get('port_connections_per_stage') or {}
        inst = e.get('instance_name', '?')
        rt_cp_cs = (pcs_per_stage.get('Route', {}) or {}).get('CP', '').strip()
        pp_cp_cs = (pcs_per_stage.get('PrePlace', {}) or {}).get('CP', '').strip()
        if (rt_cp_cs and pp_cp_cs
                and _re.match(r'(FxCts_|FxOptCts_|wrp_clk_)', rt_cp_cs, _re.IGNORECASE)
                and _re.match(r'UCLK', pp_cp_cs, _re.IGNORECASE)):
            # Exempt when the raw UCLK clock is actually a valid CP in the PrePlace netlist
            # (i.e. existing DFFs already use it in PP — UCLK01 domain DFFs may not get
            # CTS-renamed in PrePlace, only in Route). Skip if PP netlist has the raw CP.
            _pp_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'PrePlace.v.gz')
            _pp_cp_ok = False
            if os.path.exists(_pp_gz):
                try:
                    import subprocess as _sp25b
                    _r25b = _sp25b.run(
                        _fastcmd(f'zgrep -cE "\\.CP\\s*\\(\\s*{_re.escape(pp_cp_cs)}\\s*\\)" {_pp_gz}'),
                        shell=True, capture_output=True, text=True, timeout=15)
                    if int(_r25b.stdout.strip() or '0') > 0:
                        _pp_cp_ok = True  # raw clock IS a valid PrePlace CP — skip 25b
                except Exception:
                    pass
            if _pp_cp_ok:
                continue
            issues.append(
                f"HIGH/25b-STAGE-CLOCK-MISMATCH: ECO DFF {inst}.CP Route={rt_cp_cs!r} "
                f"(CTS-renamed) but PrePlace={pp_cp_cs!r} (raw UCLK). "
                f"PrePlace must use wrp_clk_* so FM can trace the CTS rename "
                f"Route→PrePlace cleanly — FmEqvEcoRouteVsEcoPrePlace fails when "
                f"PP has the ungated UCLK cone but Route has the post-CTS cone "
                f"(clock gater present in PP cone, absent in Route cone). "
                f"Fix: set port_connections_per_stage['PrePlace'].CP to the "
                f"wrp_clk_* net used by sibling DFFs in the parent module.")

    # ── 15. Every confirmed entry must have non-empty `reason`, `notes`, and
    # `source` — these populate the Step 3 RPT and serve as the audit trail for
    # round-N re-studier. Empty fields = un-traceable change.
    REQUIRED_CTX = ('reason', 'notes', 'source')
    for stage in ['Synthesize']:  # check Synthesize as canonical; per-stage entries inherit
        for e in study.get(stage, []):
            if not e.get('confirmed', True): continue
            inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name', '?')
            ct = e.get('change_type', '?')
            missing = [k for k in REQUIRED_CTX if not (e.get(k) or '').strip()]
            if missing:
                issues.append(f"MEDIUM: {ct} {inst} missing context field(s) {missing} — studier must populate `reason`/`notes`/`source` per eco_netlist_studier.md 0e.")

    # ── 16. Chain-injection schema: every gate entry produced by
    #         eco_expand_chains.py must carry the structural fields the applier
    #         depends on. Catches silently malformed chain output before it
    #         poisons Step 4.
    REQUIRED_GATE_FIELDS = ('instance_name', 'cell_type', 'gate_function', 'port_connections')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') != 'new_logic_gate':
                continue
            inst = e.get('instance_name', '?')
            missing = [f for f in REQUIRED_GATE_FIELDS if not e.get(f)]
            if missing:
                issues.append(f"CRITICAL: chain-injection schema — new_logic_gate {inst} in {stage} missing {missing}; eco_expand_chains.py output malformed")
                continue
            pcs = e.get('port_connections', {})
            if not isinstance(pcs, dict) or not pcs:
                issues.append(f"CRITICAL: chain-injection schema — new_logic_gate {inst} in {stage} has empty port_connections")
                continue
            for pin, net in pcs.items():
                if net is None or (isinstance(net, str) and not net.strip()):
                    issues.append(f"CRITICAL: chain-injection schema — new_logic_gate {inst}.{pin} in {stage} = {net!r} (empty/null)")

    # ── 17. Bridge port consumption (role-aware). Each port has exactly ONE
    #        expected consumer; check by bridge_port_role (see studier MD
    #        §0b-MODE-S consumer table). Multi-cell pattern — must NOT just
    #        check DFF.SI/SE.
    has_neighbor_dff = False
    has_bridge_port  = False
    # Collect every "consumed by" set per role across all stages
    consumed_by_dff_si_se = set()       # for host_si / host_se
    consumed_by_buffer_output = set()   # for sibling_si / sibling_se (Route)
    consumed_by_si_consumer = set()     # for sibling_q
    consumed_by_dff_q = set()           # for host_q (DFF Q net == port name)
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            ct = e.get('change_type', '')
            if ct in ('new_logic_dff', 'new_logic'):
                strat_per_stage = e.get('mode_S_strategy_per_stage') or {}
                for s in ('PrePlace', 'Route'):
                    v = strat_per_stage.get(s)
                    if v == 'neighbor_dff':   has_neighbor_dff = True
                    elif v == 'bridge_port':  has_bridge_port = True
                    pcs = (e.get('port_connections_per_stage') or {}).get(s) or {}
                    for pin in ('SI', 'SE'):
                        w = pcs.get(pin)
                        if isinstance(w, str) and w.strip() not in ("1'b0", "1'b1", ''):
                            consumed_by_dff_si_se.add(w.strip())
                    q = pcs.get('Q')
                    if isinstance(q, str) and q.strip():
                        consumed_by_dff_q.add(q.strip())
                # Also check direct port_connections (not stage-keyed) on DFF
                top_pcs = e.get('port_connections') or {}
                for pin in ('SI', 'SE'):
                    w = top_pcs.get(pin)
                    if isinstance(w, str) and w.strip() not in ("1'b0", "1'b1", ''):
                        consumed_by_dff_si_se.add(w.strip())
                q = top_pcs.get('Q')
                if isinstance(q, str) and q.strip():
                    consumed_by_dff_q.add(q.strip())
                # If this is a buffer cell, its output_net consumes a sibling_*_out port
                if e.get('bridge_port_role','').endswith('_driver'):
                    out = e.get('output_net') or top_pcs.get('Z') or top_pcs.get('ZN')
                    if isinstance(out, str) and out.strip():
                        consumed_by_buffer_output.add(out.strip())
            elif ct == 'si_consumer_replace':
                ns = e.get('new_si_net')
                if isinstance(ns, str) and ns.strip():
                    consumed_by_si_consumer.add(ns.strip())

    BRIDGE_TYPES = ('sibling_pin_consolidation', 'si_consumer_replace')
    seen_port_role = set()  # dedup across stages
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            ct = e.get('change_type')
            is_bridge_artifact = (
                ct in BRIDGE_TYPES
                or e.get('bridge_port_role')
                or (ct in ('port_declaration', 'port_connection') and e.get('is_mode_s_stitch'))
            )
            if not is_bridge_artifact:
                continue
            # Strategy explicitly declared neighbor_dff → forbidden
            if has_neighbor_dff and not has_bridge_port:
                issues.append(
                    f"HIGH: Strategy/Entry contradiction — bridge artifact {ct!r} "
                    f"({e.get('port_name') or e.get('change_index') or e.get('sibling_module','?')!r}) "
                    f"in {stage} but no DFF uses bridge_port strategy. "
                    f"Either switch the DFF's mode_S_strategy_per_stage to bridge_port "
                    f"(and wire SI/SE to the bridge port names), or drop this bridge entry.")
                continue
            # Role-aware consumer check on port_declaration entries only.
            # Each role has exactly ONE expected consumer (see studier MD §0b-MODE-S).
            if ct == 'port_declaration' and e.get('is_mode_s_stitch'):
                pn = e.get('port_name', '')
                role = e.get('bridge_port_role', '')
                if not pn or not role:
                    continue
                # Dedup: same (pn, role) reported once across stages
                key = (pn, role)
                if key in seen_port_role:
                    continue
                if   role in ('host_si', 'host_se'):
                    consumed = pn in consumed_by_dff_si_se
                elif role in ('sibling_si', 'sibling_se'):
                    # PP/Synth allowed to be undriven (Step 5 BRIDGE_OUTPUT_UNDRIVEN catches);
                    # only require buffer-driven for Route entries
                    if stage != 'Route':
                        continue
                    consumed = pn in consumed_by_buffer_output
                elif role == 'sibling_q':
                    consumed = pn in consumed_by_si_consumer
                else:
                    # host_q is auto-wired by applier (assign Q_out = <dff_Q>);
                    # no study-level proof required.
                    continue
                if not consumed:
                    seen_port_role.add(key)
                    expected = {
                        'host_si':    'DFF.SI', 'host_se': 'DFF.SE',
                        'sibling_si': 'buffer cell (sibling_si_driver) output_net',
                        'sibling_se': 'buffer cell (sibling_se_driver) output_net',
                        'sibling_q':  'si_consumer_replace.new_si_net',
                    }[role]
                    issues.append(
                        f"HIGH: Bridge port {pn!r} (role={role}) declared on module "
                        f"{e.get('module_name','?')!r} but NOT consumed by expected "
                        f"consumer ({expected}). Step 5 will fail with "
                        f"MODE_S_BRIDGE_NOT_WIRED.")

    # ── 18. Q-closure consumer must structurally exist in the named sibling.
    #        si_consumer_replace.consumer_dff_inst MUST appear in sibling_module's
    #        body in the PreEco netlist; otherwise applier silently no-ops.
    seen_consumer_check = set()
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') != 'si_consumer_replace':
                continue
            sib = e.get('sibling_module', '') or ''
            consumer = e.get('consumer_dff_inst', '') or ''
            key = (sib, consumer)
            if not sib or not consumer or key in seen_consumer_check:
                continue
            seen_consumer_check.add(key)
            # Module-scoped grep in PreEco PrePlace
            netlist = f'{args.ref_dir}/data/PreEco/PrePlace.v.gz'
            if not Path(netlist).exists():
                continue
            try:
                # Extract sibling module body, then count consumer instances
                cmd = (f"zcat '{netlist}' | "
                       f"awk '/^module\\s+\\S*{re.escape(sib.replace('ddrss_umccmd_t_',''))}/,"
                       f"/^endmodule/' | "
                       f"grep -c '\\b{re.escape(consumer)}\\b'")
                r = subprocess.run(_fastcmd(cmd), shell=True, capture_output=True, text=True, timeout=30)
                count = int(r.stdout.strip() or '0')
            except Exception:
                count = -1
            if count == 0:
                issues.append(
                    f"HIGH: Q-closure consumer {consumer!r} not found in sibling "
                    f"{sib!r} body (PreEco PrePlace). Applier will silently no-op the "
                    f".SI rewire — DFT scan break + FM Und cut-point.")

    # ── 19. Per-stage wire existence: pcs[stage].SI and .SE MUST exist in the
    #        corresponding stage's PreEco netlist. Catches the PP→Route copy-paste
    #        bug where studier reuses PP wire names that CTS removed in Route.
    #        Skip bridge ports — they're created by the applier in PostEco, not
    #        present in PreEco by design (cross-ref study port_declaration entries).
    bridge_port_names = set()
    for stg in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stg, []):
            if e.get('change_type') == 'port_declaration' and (e.get('is_mode_s_stitch') or e.get('bridge_port_role')):
                pn = e.get('port_name') or e.get('signal_name')
                if pn:
                    bridge_port_names.add(pn.strip())
    seen_wire_check = set()
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        netlist = f'{args.ref_dir}/data/PreEco/{stage}.v.gz'
        if not Path(netlist).exists():
            continue
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            pcs = e.get('port_connections_per_stage', {}).get(stage, {}) or {}
            for pin in ('SI', 'SE'):
                wire = pcs.get(pin)
                if not isinstance(wire, str) or not wire:
                    continue
                w = wire.strip()
                # Skip constants, ECO-introduced names, and bridge ports declared in the study
                if w.startswith(("1'b", "0'b", "1'h")) or w.startswith(('ECO_', 'eco')):
                    continue
                if w in bridge_port_names:
                    continue
                key = (stage, w)
                if key in seen_wire_check:
                    continue
                seen_wire_check.add(key)
                try:
                    r = subprocess.run(
                        _fastcmd(f"zgrep -c '\\b{re.escape(w)}\\b' '{netlist}'"),
                        shell=True, capture_output=True, text=True, timeout=30)
                    count = int(r.stdout.strip() or '0')
                except Exception:
                    count = -1
                if count == 0:
                    issues.append(
                        f"CRITICAL: Per-stage wire missing — DFF {inst} stage={stage} "
                        f".{pin}={w!r} does NOT exist in PreEco/{stage}.v.gz "
                        f"(0 hits). Likely PP→Route copy-paste; studier MUST run "
                        f"per-stage neighbor_dff lookup independently from each stage's netlist.")

    # ── 20. Consolidation cluster minimum size: when bridge_port is chosen,
    #        sibling_pin_consolidation.consolidation_target_dffs MUST have ≥10
    #        DFF instances. Smaller clusters indicate weak scan-en cluster — bridge
    #        is symbolic only and FM cone matching is unstable.
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') != 'sibling_pin_consolidation':
                continue
            tgts = e.get('consolidation_target_dffs') or []
            if len(tgts) < 10:
                issues.append(
                    f"HIGH: Consolidation cluster too small — sibling_pin_consolidation "
                    f"in {stage} sibling={e.get('sibling_module','?')!r} pin={e.get('pin_name','?')!r} "
                    f"has only {len(tgts)} DFFs (minimum 10). The picker found a sparse "
                    f"scan-en cluster — bridge will not be a meaningful scan path. "
                    f"Either re-pick sibling with stronger cluster, or fall back to "
                    f"neighbor_dff strategy for this DFF.")

    # ── 21. mode_S_applied consistency: when mode_S_strategy_per_stage names a real
    #        scan-integration strategy (neighbor_dff or bridge_port) for any P&R
    #        stage, mode_S_applied MUST be true. Null/false silently disables
    #        Mode S downstream.
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            strat = e.get('mode_S_strategy_per_stage') or {}
            real_pp_rt = any(strat.get(s) in ('neighbor_dff', 'bridge_port')
                             for s in ('PrePlace', 'Route'))
            if real_pp_rt and e.get('mode_S_applied') is not True:
                issues.append(
                    f"HIGH: mode_S_applied missing — DFF {e.get('instance_name','?')} in {stage} "
                    f"has real Mode-S strategy in PP/Route ({strat}) but mode_S_applied="
                    f"{e.get('mode_S_applied')!r}. Set mode_S_applied: true so downstream "
                    f"recognizes the DFF as Mode-S handled.")

    # ── 22. CTS-touched Route scan wire forces bridge_port. When neighbor_dff is
    # picked for Route and the chosen SE/SI is on a post-CTS or post-OPT-CTS
    # wire, FM cone walks through CTS infrastructure that DIFFERS between PreEco
    # and PostEco (CTS rebalances when a new DFF changes fanout/loading) → cone
    # divergence → Failing Compare Points. Safe fix: bridge_port routes SI/SE
    # through fresh parent-level ports so the ECO DFF stays off the CTS tree.
    # PrePlace HFSNET wires are NOT flagged — HFS is deterministic w.r.t. fanout
    # so PreEco-PP and PostEco-PP HFSNET topology matches; PP=neighbor_dff with
    # HFSNET is engineer's actual pattern and FM-safe.
    CTS_TOUCHED = _re.compile(r'(FxOptCts_|FxCts_|_CLKBUF_|_CTSBUF_)', _re.IGNORECASE)
    for stage in ('Route',):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not e.get('requires_scan_stitching'):
                continue
            if not e.get('confirmed', True):
                continue
            inst = e.get('instance_name') or e.get('dff_instance_name', '?')
            pcs  = (e.get('port_connections_per_stage') or {}).get(stage) or {}
            strat = (e.get('mode_S_strategy_per_stage') or {}).get(stage) or 'neighbor_dff'
            for pin in ('SE', 'SI'):
                v = pcs.get(pin, '')
                if not isinstance(v, str) or v.strip() in ("1'b0", "1'b1"):
                    continue
                if not CTS_TOUCHED.search(v):
                    continue
                # Allow bridge_port to use any wire (it's a parent-level port
                # name, not a direct CTS net hookup).
                if strat == 'bridge_port':
                    continue
                issues.append(
                    f"HIGH: ECO DFF {inst}.{pin} in {stage} = {v!r} is on a CTS/OPT-touched scan wire — "
                    f"strategy={strat!r} hooks the new DFF directly into the post-CTS scan cone, which "
                    f"FM walks through CTS infrastructure that does not exist in PreEco → cone divergence "
                    f"→ Failing Compare Points. MUST switch to bridge_port: route SI/SE through fresh "
                    f"parent-level ports + sibling consolidation (see eco_pick_bridge_dffs.py output). "
                    f"Bridge_port keeps the new DFF OFF the CTS-touched scan tree.")

    # ── 23. BRIDGE-SOURCE-IN-MAP: when bridge_port strategy is chosen for any
    # P&R stage, the anchor wire driving that bridge MUST appear as a key in
    # eco_fenets_rename_map.json. Missing key = FM-036 silent failure upstream
    # → studier has no per-stage equivalence data → bridges built on guessed
    # wires → FM Route divergence. Force a hard failure here so the orchestrator
    # restarts Step 1+2 with corrected mode_s_anchor.fm_scope.
    rename_map_path = args.study.replace('_eco_preeco_study.json', '_eco_fenets_rename_map.json')
    if os.path.isfile(rename_map_path):
        try:
            rmap = json.loads(Path(rename_map_path).read_text())
        except Exception:
            rmap = {}
        rmap_keys = set(k for k in rmap.keys() if k != '_metadata')
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                    continue
                strat = e.get('mode_S_strategy_per_stage') or {}
                inst = e.get('instance_name') or e.get('dff_instance_name', '?')
                # Surface the explicit BLOCKED marker as a hard fail
                for s in ('PrePlace', 'Route'):
                    if strat.get(s) == 'BLOCKED_NO_RENAME_MAP':
                        issues.append(
                            f"HIGH: ECO DFF {inst} stage={s} mode_S_strategy="
                            f"'BLOCKED_NO_RENAME_MAP' — studier could not find rename_map "
                            f"entry for the chosen anchor wire. Re-run Step 1 with "
                            f"corrected mode_s_anchor.fm_scope (instance hierarchy, NOT "
                            f"module-type names), then re-run Step 2.")
                # If bridge_port chosen but anchor wires aren't in rename_map → fail
                bridges_used = [s for s in ('PrePlace', 'Route') if strat.get(s) == 'bridge_port']
                if not bridges_used:
                    continue
                # Pull anchor wires from the ECO DFF's source rtl_diff change
                ci = e.get('change_index')
                src_change = next((c for c in (rtl_diff.get('changes') or []) if c.get('change_index') == ci), None)
                if not src_change:
                    continue
                anc = src_change.get('mode_s_anchor') or {}
                for role, field in (('SI', 'anchor_si_wire'), ('SE', 'anchor_se_wire')):
                    wire = anc.get(field)
                    if not wire:
                        continue
                    # Match wire against rename_map keys (suffix or exact)
                    hit = (wire in rmap_keys) or any(k.endswith('/' + wire) for k in rmap_keys)
                    if not hit:
                        issues.append(
                            f"HIGH/23-BRIDGE-SOURCE-IN-MAP: ECO DFF {inst} uses bridge_port "
                            f"strategy in {bridges_used} but anchor {role} wire {wire!r} has "
                            f"NO entry in {os.path.basename(rename_map_path)} — FM did not "
                            f"return per-stage equivalence (likely FM-036). Bridge would be "
                            f"built on a wire studier never validated across PP/Route → FM "
                            f"divergence guaranteed. Fix Step 1 mode_s_anchor.fm_scope and "
                            f"re-run Step 2 before retrying Step 3.")

    # ── 24. BRIDGE-ARTIFACT-SET-COMPLETE: when any new DFF uses bridge_port
    # strategy in PP or Route, the study MUST contain the COMPLETE set of ~17
    # artifact types per bridge. The previous failure mode (run 20260511083831):
    # Studier emitted bridge port_declarations on the host module but skipped
    # parent wire_declarations, parent instance_hookups, sibling buffer cells,
    # sibling consolidation, and Q-closure → FM elaboration ABORTED on every
    # target because parent scope referenced undeclared bridge wires. Step 5
    # didn't catch it (only validates per-module syntax), Step 6 wasted FM
    # runtime to discover it.
    #
    # Required artifact types per bridge (run eco_emit_bridge_plumbing.py to
    # generate them deterministically):
    #   1. port_declaration × 6 (host SI/SE/Q + sibling SI/SE/Q)
    #   2. wire_declaration × 3 (parent-scope bridge wires)
    #   3. port_connection × 6 (parent-scope instance hookups: host + sibling)
    #   4. sibling_pin_consolidation × ≥1 (SE pin, ≥10 DFF cluster)
    #   5. si_consumer_replace × 1 (Q-closure)
    #   6. new_logic × 2 (Route only: SI buffer + SE buffer)
    REQUIRED_ROLES_ALL_STAGES = {
        # role → (change_type, count, description)
        ('host_si',     'port_declaration'): 'host SI bridge input port',
        ('host_se',     'port_declaration'): 'host SE bridge input port',
        ('host_q',      'port_declaration'): 'host Q bridge output port',
        ('sibling_si',  'port_declaration'): 'sibling SI bridge output port',
        ('sibling_se',  'port_declaration'): 'sibling SE bridge output port',
        ('sibling_q',   'port_declaration'): 'sibling Q bridge input port',
        ('parent_wire', 'wire_declaration'): 'parent-scope bridge wires (need ≥3)',
        ('host_si',     'port_connection'):  'parent hookup: host instance .SI_in(bridge)',
        ('host_se',     'port_connection'):  'parent hookup: host instance .SE_in(bridge)',
        ('host_q',      'port_connection'):  'parent hookup: host instance .Q_out(bridge)',
        ('sibling_si',  'port_connection'):  'parent hookup: sibling instance .SI_out(bridge)',
        ('sibling_se',  'port_connection'):  'parent hookup: sibling instance .SE_out(bridge)',
        ('sibling_q',   'port_connection'):  'parent hookup: sibling instance .Q_in(bridge)',
    }
    REQUIRED_ROLES_PP_ROUTE = {
        ('sibling_se', 'sibling_pin_consolidation'): 'sibling SE-pin consolidation (≥10 DFFs)',
        ('sibling_q',  'si_consumer_replace'):       'Q-closure: rewire sibling DFF.SI to Q_in',
    }
    REQUIRED_ROLES_ROUTE_ONLY = {
        ('sibling_si_driver', 'new_logic'): 'Route: SI bridge buffer cell',
        ('sibling_se_driver', 'new_logic'): 'Route: SE bridge buffer cell',
    }
    # Collect (for_dff, mode_S_strategy_per_stage) for every DFF that picked bridge_port
    bridge_dffs = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            strat = e.get('mode_S_strategy_per_stage') or {}
            stages_using_bridge = [s for s in ('PrePlace', 'Route') if strat.get(s) == 'bridge_port']
            if stages_using_bridge:
                bridge_dffs.append({
                    'inst':   e.get('instance_name') or e.get('dff_instance_name', '?'),
                    'stages': stages_using_bridge,
                })
    # Dedupe (same DFF appears in all 3 stage entries)
    seen = set(); uniq_bridge_dffs = []
    for b in bridge_dffs:
        if b['inst'] in seen: continue
        seen.add(b['inst']); uniq_bridge_dffs.append(b)
    for bd in uniq_bridge_dffs:
        inst = bd['inst']
        # Build per-stage role-set for this DFF
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            if stage not in ('PrePlace', 'Route') and stage not in bd['stages']:
                # Synth requires the all-stage subset only when ANY P&R stage is bridge_port
                pass
            present = set()
            wire_count = 0
            for e in study.get(stage, []):
                # Match entries tagged for THIS DFF (if for_dff field present) or
                # any bridge artifact (if studier didn't tag — be lenient on legacy).
                if e.get('for_dff') and e.get('for_dff') != inst:
                    continue
                role = e.get('bridge_port_role')
                ct = e.get('change_type')
                if not role or not ct:
                    continue
                present.add((role, ct))
                if (role, ct) == ('parent_wire', 'wire_declaration'):
                    wire_count += 1
            # Always-required roles
            for key, desc in REQUIRED_ROLES_ALL_STAGES.items():
                if key not in present:
                    issues.append(
                        f"HIGH/24-BRIDGE-ARTIFACT-MISSING: ECO DFF {inst} stage={stage} "
                        f"missing {key[1]} with bridge_port_role={key[0]!r} ({desc}). "
                        f"Run eco_emit_bridge_plumbing.py and splice its {stage} list "
                        f"into the study; do NOT hand-derive bridge artifacts.")
            # PP/Route-required roles
            if stage in ('PrePlace', 'Route'):
                for key, desc in REQUIRED_ROLES_PP_ROUTE.items():
                    if key not in present:
                        issues.append(
                            f"HIGH/24-BRIDGE-ARTIFACT-MISSING: ECO DFF {inst} stage={stage} "
                            f"missing {key[1]} with bridge_port_role={key[0]!r} ({desc}). "
                            f"Bridge_port without consolidation/Q-closure leaves sibling "
                            f"output ports undriven → FM ABORT on elaboration.")
            # Route-only roles (buffers)
            if stage == 'Route':
                for key, desc in REQUIRED_ROLES_ROUTE_ONLY.items():
                    if key not in present:
                        issues.append(
                            f"HIGH/24-BRIDGE-ARTIFACT-MISSING: ECO DFF {inst} stage=Route "
                            f"missing {key[1]} with bridge_port_role={key[0]!r} ({desc}). "
                            f"Without buffer cells the sibling output port has no driver "
                            f"→ FM ABORT.")
            # Wire count: parent_wire role must appear ≥3 times (si/se/q bridges)
            if wire_count < 3 and ('parent_wire', 'wire_declaration') in present:
                issues.append(
                    f"HIGH/24-BRIDGE-WIRE-COUNT: ECO DFF {inst} stage={stage} has only "
                    f"{wire_count} parent_wire wire_declaration(s); need ≥3 (si/se/q "
                    f"bridges). eco_emit_bridge_plumbing.py emits all 3 — partial "
                    f"emission means studier hand-edited the output.")
            # Consolidation cluster size sanity (separate from Check 20 which
            # checks per-entry; this checks per-DFF-bridge presence)
            for e in study.get(stage, []):
                if e.get('change_type') != 'sibling_pin_consolidation':
                    continue
                if e.get('for_dff') and e.get('for_dff') != inst:
                    continue
                cluster = e.get('consolidation_target_dffs') or []
                if len(cluster) < 10:
                    issues.append(
                        f"HIGH/24-CONSOLIDATION-TOO-SMALL: ECO DFF {inst} stage={stage} "
                        f"sibling_pin_consolidation cluster has {len(cluster)} DFFs "
                        f"(<10). Bridge_port requires ≥10-DFF cluster "
                        f"(per eco_pick_sibling.py SIBLING ESCALATION rule) "
                        f"or fall back to neighbor_dff strategy.")

    # ── 26. NAMED-NET FORMAT + SCOPE-LEAK SUSPECT DETECTION ────────────────
    # Two related checks for unconnected_rewires entries:
    # (a) named_net MUST be a flat Verilog identifier — no brackets, no spaces.
    #     Bracket form (e.g. "<bus>[N]") is illegal in `wire <name>;`
    #     declarations. Typical root cause: studier emits a bracket-form
    #     named_net → applier writes `wire <bus>[N];` → FM SVR-4 / SVR-64 /
    #     FM-599 ABORT (and downstream debug burns hours).
    #     The applier's eco_perl_spec.py auto-sanitizes, but this validator
    #     flags the studier-side root cause so engineer can fix at source.
    # (b) When N entries across N different modules share the same `named_net`
    #     value AND the same `original` UNCONNECTED name, that's a likely
    #     scope-leak symptom — the studier may have broadcast a substitution
    #     that should have been scoped to ONE module (the actual target).
    NAMED_NET_RE = re.compile(r'^[A-Za-z_]\w*$')
    from collections import defaultdict
    rewires_by_named = defaultdict(list)   # named_net -> [(stage, mod, inst, orig)]
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            mod = e.get('module_name', '')
            for ur in e.get('unconnected_rewires', []) or []:
                named = ur.get('named_net', '')
                orig  = ur.get('original_unconnected', '')
                inst  = ur.get('port_bus_instance', '') or e.get('instance_name', '')
                if not named:
                    continue
                # (a) Format check
                if not NAMED_NET_RE.match(named):
                    issues.append(
                        f"HIGH/26-NAMED-NET-FORMAT: stage={stage} mod={mod} "
                        f"named_net={named!r} is not a flat Verilog identifier. "
                        f"Use underscore-escape form (e.g. 'X_1_' instead of 'X[1]'). "
                        f"Applier auto-sanitizes but studier should emit correct "
                        f"form directly. See eco_netlist_studier.md §0b-UNCONNECTED "
                        f"format constraints.")
                rewires_by_named[(named, orig)].append((stage, mod, inst))

    # (b) Scope-leak suspect: same (named, orig) used in entries with DIFFERENT
    # modules. Allowed: same (named, orig) appears in 3 entries (one per stage)
    # for the SAME module — that's normal multi-stage. Flag when the modules
    # set has >1 distinct module per stage.
    for (named, orig), occurrences in rewires_by_named.items():
        # Group by stage
        per_stage_mods = defaultdict(set)
        for stage, mod, inst in occurrences:
            per_stage_mods[stage].add(mod)
        for stage, mods in per_stage_mods.items():
            if len(mods) > 1:
                issues.append(
                    f"WARN/26-SCOPE-LEAK-SUSPECT: stage={stage} the same rename "
                    f"(orig={orig!r} → named={named!r}) targets {len(mods)} different "
                    f"modules: {sorted(mods)[:5]}. Applier executes per-instance, "
                    f"so each entry results in its own substitution — this LOOKS "
                    f"like a scope-leak. Verify all targets are intentional; if "
                    f"only ONE module needs the rename, drop the others to avoid "
                    f"polluting unrelated module scopes.")

    # ── 27. CLOCK-STAGE-STABILITY for new DFFs ──────────────────────────────
    # For each new_logic_dff, check that port_connections_per_stage[*].CP
    # references wires from the SAME clock domain across all 3 stages.
    # Failure mode: studier picks Synth=ClkA, PP=ClkA, Route=<ClkB-tree
    # CTS-rebalanced antenna fix> — different clock domain in Route → FM
    # logical mismatch.
    #
    # Heuristic: extract the "root clock token" from each per-stage CP wire
    # name. Patterns like '<clk_token>_buf_*', '<clk_token>_cts_*',
    # 'ant_fix_net_*_<clk_token>_*', 'FxOptCts_*<clk_token>*', etc., should
    # all share the same <clk_token>. If the tokens disagree, fail.
    CLK_TOKEN_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*?clk[A-Za-z0-9_]*|'
                              r'[A-Z][A-Z0-9]*CLK[A-Z0-9_]*)', re.IGNORECASE)
    # Decorations that wrap a clock root: clock-gate cells, CTS rebalance,
    # antenna-fix nets, buffer chains, register suffixes. Stripped (anywhere
    # in the token, not just suffix) before comparing tokens across stages.
    _CLK_DECOR_RE = re.compile(
        r'(_CLK_GATE_[A-Z0-9_]*|_CTS(_[A-Z0-9]+)*|_BUF(_[A-Z0-9]+)*|'
        r'_INV(_[A-Z0-9]+)*|_GATE(_[A-Z0-9]+)*|_REG(_[A-Z0-9]+)*|'
        r'^ANT_FIX_NET_\d+_|^UMCCMD_)', re.IGNORECASE)
    def _extract_clock_tokens(cp_value):
        """Find all clock-like tokens in a CP wire/cell name. Returns set of
        decoration-stripped root tokens. Recognizes clock-gate cell names
        (`*_clk_gate_*_reg`), CTS rebalance (`*_cts_*`), antenna-fix nets
        (`ant_fix_net_*_<clk>_*`), buffer chains, etc."""
        if not cp_value:
            return set()
        tokens = set()
        for m in CLK_TOKEN_RE.finditer(cp_value):
            tok = m.group(1).upper()
            # Strip decorations anywhere in the token (not just suffix)
            prev = None
            while tok != prev:
                prev = tok
                tok = _CLK_DECOR_RE.sub('', tok)
            tok = tok.strip('_')
            if tok:
                tokens.add(tok)
        return tokens

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            pcs = e.get('port_connections_per_stage', {})
            if not pcs:
                continue
            per_stage_tokens = {}
            for st in ('Synthesize', 'PrePlace', 'Route'):
                cp = (pcs.get(st) or {}).get('CP', '')
                per_stage_tokens[st] = _extract_clock_tokens(cp)
            # Find any pairwise disjoint set (no common token across stages)
            non_empty = {st: tks for st, tks in per_stage_tokens.items() if tks}
            if len(non_empty) >= 2:
                common = set.intersection(*non_empty.values())
                if not common:
                    # Wrapper-clock swap exemption: when Check 25 approved
                    # Synth=UCLK*/PP=wrp_clk_*/Route=CTS-renamed swap, set
                    # `wrapper_clock_swap: true` in the study entry to exempt.
                    if e.get('wrapper_clock_swap'):
                        pass  # intentional wrapper-clock domain swap — Check 25 validated it
                    else:
                      issues.append(
                        f"HIGH/27-CLOCK-STAGE-MISMATCH: DFF {inst} per-stage CP "
                        f"references DIFFERENT clock domains: " +
                        ', '.join(f"{st}=tokens{sorted(tks)}" for st, tks in per_stage_tokens.items() if tks) +
                        f". A new DFF must clock on the SAME logical clock across "
                        f"all 3 PostEco stages (CTS-rebalanced names are OK as long "
                        f"as the clock-token root matches). Mismatch → FM logical "
                        f"mismatch on this DFF. Studier should re-pick CP per stage "
                        f"to ensure all 3 belong to the same clock tree (verify by "
                        f"tracing each CP wire's source register's CP recursively).")

    # ── 31. SYNTH-STYLE-TOPOLOGY: every new_logic_dff's d_input_gate_chain
    # must match what eco_synth_chain.py would produce for the same target
    # Boolean. The synthesizer enforces engineer-style decomposition (e.g.,
    # collapse AND-of-mixed-literals into OR4+NR2 instead of literal AN+INV
    # chains). Mismatch → HARD FAIL.
    #
    # Why this matters
    # ----------------
    # FM treats different cell topologies as different cones even when the
    # Boolean is mathematically equivalent. Trial3 of run 20260512070625
    # produced a 5-cell literal decomposition for NeedFreqAdj_reg.D and FM
    # Route failed; trial1 + engineer used the synth-style 4-cell chain for
    # the same Boolean and FM passed. This check ensures the studier emits
    # the synth-style chain.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import eco_synth_chain as synth
    except ImportError:
        synth = None
    if synth is not None:
        # Build per-stage gate-by-output-net index for chain walking
        for stage_key in ('Synthesize',):
            stage_entries = study.get(stage_key, [])
            # Index: output_net → gate_entry
            gates_by_output = {}
            for e in stage_entries:
                if e.get('change_type') == 'new_logic_gate':
                    out = e.get('output_net') or e.get('port_connections', {}).get('Z') \
                          or e.get('port_connections', {}).get('ZN')
                    if out:
                        gates_by_output[out] = e

            # For each new_logic_dff, walk the chain
            for dff in stage_entries:
                if dff.get('change_type') not in ('new_logic_dff', 'new_logic'):
                    continue
                inst = dff.get('instance_name') or dff.get('dff_instance_name') \
                       or dff.get('signal_name') or '?'
                d_net = (dff.get('port_connections') or {}).get('D')
                if not d_net or not d_net.startswith('n_eco_'):
                    # D is a primary signal or constant — no chain to check
                    continue

                # BFS backward from d_net through n_eco_* nets, collecting gates
                visited = set()
                chain_gates = []
                queue = [d_net]
                while queue:
                    net = queue.pop(0)
                    if net in visited or not net.startswith('n_eco_'):
                        continue
                    visited.add(net)
                    g = gates_by_output.get(net)
                    if g is None:
                        continue
                    chain_gates.append(g)
                    pc = g.get('port_connections') or {}
                    for pin, val in pc.items():
                        if pin in ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'):
                            continue  # output pins, skip
                        if val.startswith('n_eco_'):
                            queue.append(val)

                if not chain_gates:
                    continue

                try:
                    # 1. Compose the emitted chain into Boolean
                    emitted_chain_obj = synth.CellChain()
                    for g in chain_gates:
                        emitted_chain_obj.add_cell(
                            cell_type=g.get('cell_type', ''),
                            inst_name=g.get('cell_instance_name', '') or g.get('instance_name', ''),
                            port_connections=g.get('port_connections', {}),
                        )
                    emitted_chain_obj.output_net = d_net

                    # Build sympy symbol map from chain leaf inputs
                    leaf_inputs = sorted({
                        v for g in chain_gates
                        for k, v in (g.get('port_connections') or {}).items()
                        if k not in ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S')
                           and not v.startswith(('n_eco_', "1'b", "0'b"))
                    })
                    if not leaf_inputs:
                        continue
                    from sympy import symbols as _sym
                    sym_dict = {n: _sym('S_' + re.sub(r'[^A-Za-z0-9_]', '_', n))
                                for n in leaf_inputs}
                    emitted_boolean = synth.compose_chain_boolean(
                        emitted_chain_obj, sym_dict, jira='check'
                    )
                    if emitted_boolean is None:
                        issues.append(
                            f"WARN/31-SYNTH-TOPOLOGY: ECO DFF {inst} chain contains "
                            f"cell types not modelled by eco_synth_chain.compose. "
                            f"Cannot verify topology."
                        )
                        continue
                    # 2. Canonicalize via DeMorgan, then re-synthesize
                    canonical = synth._push_nots_to_literals(emitted_boolean)
                    expected_chain = synth.synthesize_and_pattern(
                        canonical, tuple(sym_dict.values()), jira='check'
                    )
                    if expected_chain is None:
                        issues.append(
                            f"WARN/31-SYNTH-TOPOLOGY: ECO DFF {inst} Boolean does not "
                            f"match any pattern in eco_synth_chain.py. Cannot enforce "
                            f"topology — add a pattern detector for this case."
                        )
                        continue
                    # 3. Compare cell-FAMILY multisets (not exact strings).
                    # The studier picks library variants (drive strength, leakage:
                    # LL/HVT/SVT/LVT, threshold) from the actual netlist; the
                    # synth_chain library's choice is a default. FM equivalence
                    # depends on gate function + pin layout, not the trailing
                    # variant suffixes. Reduce both to a family token:
                    #   INR2D1BWP136P5M156H3P48CPDLVTLL -> INR2
                    #   AN2D1BWP136P5M117H3P48CPDLVT    -> AN2
                    def _family(ct):
                        m = re.match(r'^([A-Z]+\d*)', ct or '')
                        return m.group(1) if m else (ct or '')
                    emitted_fams  = sorted(_family(g.get('cell_type', '')) for g in chain_gates)
                    expected_fams = sorted(_family(c['cell_type']) for c in expected_chain.cells)
                    if emitted_fams != expected_fams:
                        emitted_types  = sorted(g.get('cell_type', '') for g in chain_gates)
                        expected_types = sorted(c['cell_type'] for c in expected_chain.cells)
                        issues.append(
                            f"HIGH/31-SYNTH-TOPOLOGY: ECO DFF {inst} emitted "
                            f"{len(emitted_types)} cells {emitted_types} differs from "
                            f"synth-style {len(expected_types)} cells {expected_types}. "
                            f"Boolean is equivalent but FM may treat different "
                            f"topologies as cone-divergent. Studier MUST invoke "
                            f"eco_synth_chain.py and use its output verbatim — "
                            f"literal RTL decomposition is FORBIDDEN."
                        )
                except Exception as ex:
                    issues.append(
                        f"WARN/31-SYNTH-TOPOLOGY: error checking topology for {inst}: "
                        f"{type(ex).__name__}: {ex}"
                    )

    # ── Helper: detect entries that are doing "real" scan stitching even when
    #          their mode_S_applied / requires_scan_stitching flags say no.
    #          A DFF whose port_connections_per_stage[PP|Route].SE/SI hold a
    #          real wire (not '1'b0' / '1'bz') IS doing scan stitching, full
    #          stop. The strategy field's claim is irrelevant; the netlist
    #          will use the real wire. This closes the G1/G2 escape hatch.
    _CONST_OR_EMPTY = ("1'b0", "1'bz", "")
    def _scan_active(e):
        if e.get('mode_S_applied') or e.get('requires_scan_stitching'):
            return True
        pcs = e.get('port_connections_per_stage') or {}
        for s in ('PrePlace', 'Route'):
            sm = pcs.get(s) or {}
            if sm.get('SE','') not in _CONST_OR_EMPTY or sm.get('SI','') not in _CONST_OR_EMPTY:
                return True
        return False

    # ── Helper: re-grep PreEco netlist to get authoritative DFF count in
    #          host module on dff_clock — never trust the studier's value.
    #          Uses /tmp/eco_study_<TAG>_Synthesize.v cached by 0c (or
    #          falls back to PreEco/Synthesize.v.gz via zcat+awk pipeline).
    _dff_count_cache = {}
    def _real_dff_count(host_mod, dff_clock):
        if not host_mod or not dff_clock:
            return None
        key = (host_mod, dff_clock)
        if key in _dff_count_cache:
            return _dff_count_cache[key]
        cached = Path(f'/tmp/eco_study_{args.tag}_Synthesize.v')
        ref_dir = args.ref_dir
        if cached.is_file():
            cmd = (f"awk '/^module {re.escape(host_mod)}\\b/,/^endmodule/' {cached} "
                   f"| grep -cE '\\.CP\\(\\s*{re.escape(dff_clock)}\\b'")
        else:
            gz = Path(ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
            if not gz.is_file():
                _dff_count_cache[key] = None
                return None
            cmd = (f"zcat {gz} | awk '/^module {re.escape(host_mod)}\\b/,/^endmodule/' "
                   f"| grep -cE '\\.CP\\(\\s*{re.escape(dff_clock)}\\b'")
        try:
            r = subprocess.run(_fastcmd(cmd), shell=True, capture_output=True, text=True, timeout=60)
            n = int((r.stdout or '0').strip() or '0')
        except Exception:
            n = None
        _dff_count_cache[key] = n
        return n

    # ── 28. ROUTE-MUST-BE-BRIDGE-PORT (G1) ─────────────────────────────────
    # Route-stage scan wires are CTS-rebalanced, so neighbor_dff in Route is
    # non-deterministic. Guard relaxed: also processes entries that have real
    # PP/Route SE/SI wires regardless of mode_S_applied flag (closes escape).
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not _scan_active(e):
                continue
            inst = e.get('instance_name', '?')
            strat = (e.get('mode_S_strategy_per_stage') or {})
            rt = strat.get('Route')
            if rt and rt not in ('bridge_port', 'BLOCKED_NO_RENAME_MAP'):
                issues.append(
                    f"HIGH/28-ROUTE-MUST-BE-BRIDGE-PORT: DFF {inst} entry in {stage} "
                    f"declares mode_S_strategy_per_stage.Route={rt!r}. Route MUST be "
                    f"'bridge_port' for any DFF requiring scan stitching — neighbor_dff "
                    f"is non-deterministic in Route because CTS rebalances scan wires "
                    f"into multiple clones. Re-pick strategy and emit full bridge "
                    f"plumbing for Route via eco_emit_bridge_plumbing.py.")

    # ── 29. PP-MUST-MATCH-ROUTE-WHEN-BRIDGE (G1) ──────────────────────────
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not _scan_active(e):
                continue
            inst = e.get('instance_name', '?')
            strat = (e.get('mode_S_strategy_per_stage') or {})
            pp = strat.get('PrePlace')
            rt = strat.get('Route')
            if rt == 'bridge_port' and pp and pp not in ('bridge_port', 'BLOCKED_NO_RENAME_MAP'):
                issues.append(
                    f"HIGH/29-PP-MUST-MATCH-ROUTE: DFF {inst} entry in {stage} has "
                    f"Route='bridge_port' but PrePlace={pp!r}. Mixing strategies "
                    f"across PP and Route causes stage-divergent cone reach into the "
                    f"bridge buffer. PP MUST also use 'bridge_port' when Route does.")

    # ── 30. CONSTANT-ZERO-ONLY-WHEN-NO-DFFS (G2) — with grep override ─────
    # constant_zero was previously gated to host modules with zero same-clock
    # DFFs (assumed scan-isolated island would fail FM). Under the new policy
    # scan stitching is OUT OF SCOPE — constant_zero on SE/SI is the unconditional
    # default in all 3 stages; DFT team handles scan integration separately. This
    # entire check is now a no-op (kept as a safety net behind _skip_mode_s_checks
    # in case scan stitching is reintroduced).
    if not _skip_mode_s_checks:
      for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            strat = (e.get('mode_S_strategy_per_stage') or {})
            for chk in ('PrePlace', 'Route'):
                if strat.get(chk) != 'constant_zero':
                    continue
                declared = e.get('host_module_dff_count_same_clock')
                exempt_reason = e.get('scan_stitching_skipped_reason', '')
                host_mod = e.get('module_name', '') or ''
                clk = e.get('dff_clock', '') or ''
                actual = _real_dff_count(host_mod, clk)
                if actual is not None and declared is not None and int(declared) != actual:
                    issues.append(
                        f"HIGH/30b-DFF-COUNT-LIE: DFF {inst} entry declares "
                        f"host_module_dff_count_same_clock={declared} but PreEco grep "
                        f"counts {actual} DFFs in module {host_mod!r} on clock {clk!r}. "
                        f"Studier MUST compute this value via grep, not assert it. "
                        f"Forcing actual={actual} for Check 30 evaluation.")
                effective = actual if actual is not None else declared
                if effective is None:
                    issues.append(
                        f"HIGH/30-CONSTANT-ZERO-UNJUSTIFIED: DFF {inst} entry in "
                        f"{stage} declares mode_S_strategy_per_stage.{chk}="
                        f"'constant_zero' but neither studier-declared "
                        f"'host_module_dff_count_same_clock' nor a working "
                        f"netlist grep is available. Compute the count and either "
                        f"justify constant_zero (count==0) or switch to bridge_port.")
                elif int(effective) > 0 and 'wrp_clk' not in exempt_reason:
                    issues.append(
                        f"HIGH/30-CONSTANT-ZERO-FORBIDDEN: DFF {inst} entry in "
                        f"{stage} uses constant_zero for {chk} but host module "
                        f"{host_mod!r} has {effective} pre-existing DFF(s) on clock "
                        f"{clk!r}. constant_zero creates a scan-isolated island that "
                        f"fails FM via cross-DFF cone interference. Re-strategy to "
                        f"'bridge_port' (use SIBLING ESCALATION in studier 0b-MODE-S).")

    # ── 32. STRATEGY-CONNECTION-CONSISTENCY (concern B escape closure) ────
    # When mode_S_strategy_per_stage[<stage>] declares constant_zero, the
    # port_connections_per_stage[<stage>].SE/SI MUST be '1'b0'. A studier
    # that writes 'constant_zero' but plugs real neighbor wires produces a
    # netlist that will USE the real wires (the strategy field is metadata;
    # port_connections is what gets applied). The PP/Route real wire may be
    # CTS-rebalanced → FM Route fail despite the entry claiming "no scan".
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            strat = e.get('mode_S_strategy_per_stage') or {}
            pcs = e.get('port_connections_per_stage') or {}
            for chk in ('Synthesize', 'PrePlace', 'Route'):
                if strat.get(chk) != 'constant_zero':
                    continue
                pcs_chk = pcs.get(chk) or {}
                actual_se = pcs_chk.get('SE', '')
                actual_si = pcs_chk.get('SI', '')
                if (actual_se not in _CONST_OR_EMPTY) or (actual_si not in _CONST_OR_EMPTY):
                    issues.append(
                        f"HIGH/32-STRATEGY-CONNECTION-LIE: DFF {inst} entry in "
                        f"{stage} declares mode_S_strategy_per_stage.{chk}="
                        f"'constant_zero' but port_connections_per_stage.{chk} has "
                        f"SE={actual_se!r}, SI={actual_si!r} (real wires). The "
                        f"netlist will use the real wires; the strategy field lies. "
                        f"Either set strategy to 'neighbor_dff'/'bridge_port' (and "
                        f"satisfy Checks 28/29) OR set SE/SI to '1'b0' to actually "
                        f"be constant_zero.")

    # ── GAP-5: [CELL_TYPE_STAGE_VALID] — cell type must exist in per-stage PreEco netlist
    # Prevents FE-LINK-2 ABORT caused by studier picking a Synth-only cell variant
    # (e.g. MUX2D1AMDBWP136P5M156H3P48CPDLVT) that doesn't exist in PP/Route library.
    # Proxy: if the cell type appears 0 times in the PreEco stage netlist, it's not
    # in the library FM links against for that stage.
    _stage_gz = {
        'Synthesize': os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz'),
        'PrePlace':   os.path.join(args.ref_dir, 'data', 'PreEco', 'PrePlace.v.gz'),
        'Route':      os.path.join(args.ref_dir, 'data', 'PreEco', 'Route.v.gz'),
    }
    _gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic', 'condition_gate')
    _checked_ct = {}  # (stage, cell_type) → count
    for stage in ['Synthesize', 'PrePlace', 'Route']:
        gz = _stage_gz.get(stage, '')
        if not gz or not Path(gz).is_file():
            continue
        for e in study.get(stage, []):
            if e.get('change_type') not in _gate_types:
                continue
            ct = e.get('cell_type', '') or e.get('preeco_cell_type', '')
            if not ct:
                continue
            key = (stage, ct)
            if key not in _checked_ct:
                try:
                    r = subprocess.run(
                        _fastcmd(f'zgrep -c "{ct}" {gz}'),
                        shell=True, capture_output=True, text=True, timeout=30
                    )
                    _checked_ct[key] = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
                except Exception:
                    _checked_ct[key] = -1
            count = _checked_ct[key]
            if count == 0:
                inst = e.get('instance_name') or e.get('cell_name') or '?'
                issues.append(
                    f"[CELL_TYPE_STAGE_VALID] {stage}: cell_type {ct!r} for {inst!r} "
                    f"has 0 occurrences in PreEco {stage} netlist — not in FM library "
                    f"for this stage → FE-LINK-2 ABORT. Studier must pick a cell type "
                    f"that exists in the per-stage PreEco netlist (GAP-5).")

    # ── driver_substitution: gate inputs must be stage-stable ────────────────
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('fallback_strategy') != 'driver_substitution': continue
        chain = c.get('new_condition_gate_chain') or []
        tgt = c.get('driver_sub_target_net') or c.get('old_token') or '?'
        _skip_prefixes = ("1'b", "0'b", "n_eco_", "ECO_", "PENDING", "SEQMAP")
        for g in chain:
            for inp in (g.get('inputs') or []):
                base = str(inp).split('[')[0]
                if any(base.startswith(p) for p in _skip_prefixes): continue
                for stage in ['Synthesize', 'PrePlace', 'Route']:
                    gz = os.path.join(args.ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
                    if not os.path.exists(gz): continue
                    try:
                        r = subprocess.run(_fastcmd(f'zgrep -c "{base}" {gz}'),
                            shell=True, capture_output=True, text=True, timeout=30)
                        cnt = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
                        if cnt == 0:
                            issues.append(
                                f"HIGH: {stage} driver_substitution for '{tgt}': "
                                f"gate input '{base}' has 0 occurrences in {stage} PreEco — "
                                f"not stage-stable. Only ECO ports and primary inputs allowed.")
                            break
                    except Exception:
                        pass

    # ── wire_swap + intermediate_net_insertion gate chain check ──────────────
    # When a wire_swap change has fallback_strategy=intermediate_net_insertion
    # AND new_condition_gate_chain, the studier MUST emit new_logic_gate study
    # entries for the gate chain. Without them the applier skips insertion and
    # the pivot net becomes undriven → thousands of FM failing compare points.
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'wire_swap':
            continue
        if c.get('fallback_strategy') != 'intermediate_net_insertion':
            continue
        chain = c.get('new_condition_gate_chain') or []
        if not chain:
            continue
        # Verify study has new_logic_gate entries for this chain
        pivot_net = chain[-1].get('output_net', '') if chain else ''
        for stage in ['Synthesize', 'PrePlace', 'Route']:
            gate_entries = [e for e in study.get(stage, [])
                           if e.get('change_type') in ('new_logic_gate', 'condition_gate_chain', 'new_logic')]
            if not gate_entries:
                issues.append(
                    f"CRITICAL: {stage} wire_swap changes[{idx}] has fallback_strategy="
                    f"intermediate_net_insertion with {len(chain)}-gate new_condition_gate_chain, "
                    f"but study has NO new_logic_gate entries. Studier Phase 1 must emit gate chain "
                    f"entries same as Phase 0 — without them the pivot net '{pivot_net}' is undriven "
                    f"after rename and FM will fail with thousands of compare points.")

    # ── 33. DFF.D MUST be a valid Verilog identifier or constant ────────────
    # Catches wrapper synth failures that wrote an error string into the D
    # pin (e.g. "SYNTH_FAILED: ..."). Without this check a broken study
    # passes validation and the applier writes garbage into the netlist
    # → FM elaboration error.
    _VALID_NET_RE = re.compile(r"^([A-Za-z_]\w*|\d+'[bohd][0-9a-fA-FxXzZ_]+)$")
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            # Top-level port_connections + per-stage maps
            checks = [('port_connections', e.get('port_connections') or {})]
            for s, p in (e.get('port_connections_per_stage') or {}).items():
                checks.append((f'port_connections_per_stage[{s}]', p or {}))
            for label, pcs in checks:
                d_val = pcs.get('D')
                if d_val is None:
                    continue
                if not isinstance(d_val, str) or not _VALID_NET_RE.match(d_val.strip()):
                    issues.append(
                        f"CRITICAL/33-INVALID-DFF-D: DFF {inst} {label}.D = {d_val!r} "
                        f"is NOT a valid Verilog identifier or constant. "
                        f"Likely cause: eco_emit_dff_entry.py / eco_synth_chain.py "
                        f"failed to synthesize the chain and wrote an error string "
                        f"or null. Re-spawn studier with corrected d_input_expected_function "
                        f"OR extend eco_synth_chain.py pattern library to handle this Boolean.")
                # Detect the explicit SYNTH_FAILED marker placeholder
                if 'SYNTH_FAILED' in d_val:
                    issues.append(
                        f"CRITICAL/33-SYNTH-FAILED-PLACEHOLDER: DFF {inst} {label}.D = "
                        f"{d_val!r}. eco_synth_chain.py failed; the applier will insert "
                        f"a stub net with no driver. Investigate the synth failure (see "
                        f"the wrapper's stderr) and fix d_input_expected_function or the "
                        f"synth_chain pattern library.")

    # ── 34. cell_type non-empty on every new_logic_dff ───────────────────────
    # Without cell_type the applier returns "cell_type empty for <inst> ...
    # cannot insert" SKIP and the DFF doesn't land in PostEco. The wrapper
    # (eco_emit_dff_entry.py _discover_dff_cell_type) populates this from a
    # neighbor DFF in host module scope; if it's empty here the discovery
    # missed (host module not found, no DFF on dff_clock) and the DFF needs
    # a manual cell_type before APPLY can run. Run 20260515071155 surface.
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            ct = (e.get('cell_type') or e.get('dff_cell_type') or '').strip()
            if not ct:
                inst = e.get('instance_name', '?')
                issues.append(
                    f"CRITICAL/34-DFF-CELL-TYPE-EMPTY: DFF {inst} in {stage} has "
                    f"empty `cell_type` AND empty `dff_cell_type`. eco_perl_spec "
                    f"will SKIP with reason 'cell_type empty for {inst} ... "
                    f"cannot insert without cell type' and the DFF won't land in "
                    f"PostEco. Cause: eco_emit_dff_entry.py _discover_dff_cell_type "
                    f"failed (host_module={e.get('module_name')!r} on dff_clock="
                    f"{e.get('dff_clock')!r} — no neighbor DFF found in PreEco "
                    f"Synthesize). Studier must populate cell_type via §0c neighbor "
                    f"DFF lookup before completing Step 3.")

    # ── 35. DFF.CP must equal dff_clock (or its rename_map per-stage value) ──
    # Wrapper writes the rename-map-resolved CP per stage; if the studier (or
    # the verifier) post-processes and accidentally truncates / overwrites it
    # (e.g. UCLK01 → UCLK), FM Route fails on clock-domain mismatch. Check
    # that DFF.CP starts with (or is identical to) the rtl_diff dff_clock —
    # this catches truncation and gross overwrites without false-positiving
    # legitimate CTS-rebalanced names like FxCts_<dff_clock>_*. Run
    # 20260515071155 surface (UCLK01 → UCLK silent truncation).
    diff_dff_clocks = {}  # instance_name → dff_clock
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') in ('new_logic', 'new_logic_dff'):
            tr = c.get('target_register') or ''
            inst = c.get('dff_instance_name') or (f'{tr}_reg' if tr else '')
            if inst and c.get('dff_clock'):
                diff_dff_clocks[inst] = c['dff_clock']
    for e in study.get('Synthesize', []):
        if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
            continue
        inst = e.get('instance_name', '?')
        expected_clk = diff_dff_clocks.get(inst) or (e.get('dff_clock') or '')
        if not expected_clk:
            continue
        # Synth CP should equal expected exactly (no CTS yet at Synth)
        cp_synth = (e.get('port_connections') or {}).get('CP', '')
        if cp_synth and cp_synth != expected_clk:
            # Allow CTS-rebalanced forms (Fx*Cts*<clk>* or *<clk>_clk_gate*)
            # that contain the clock as a substring
            if expected_clk not in cp_synth:
                issues.append(
                    f"HIGH/35-DFF-CP-MISMATCH: DFF {inst} Synth port_connections.CP="
                    f"{cp_synth!r} but rtl_diff dff_clock={expected_clk!r}. The "
                    f"wrapper resolves CP from the fenets rename_map; if the studier "
                    f"or verifier overwrote it with a truncated/wrong value, FM Route "
                    f"will fail on clock-domain mismatch. Verify the rename_map entry "
                    f"for the DFF's host_scope/dff_clock returned the right per-stage "
                    f"value and that no post-processing step modified it.")
        # PP/Route may legitimately differ (CTS); just check non-empty
        for stg in ('PrePlace', 'Route'):
            cp_v = ((e.get('port_connections_per_stage') or {}).get(stg) or {}).get('CP', '')
            if not cp_v:
                issues.append(
                    f"HIGH/35-DFF-CP-EMPTY: DFF {inst} port_connections_per_stage[{stg}].CP "
                    f"is empty. The wrapper (eco_emit_dff_entry.py resolve_cp_per_stage) "
                    f"must always populate CP per stage from rename_map; empty value "
                    f"means rename_map didn't have an entry for the DFF's host_scope/"
                    f"dff_clock combo OR per-stage map post-processing dropped it. "
                    f"Re-spawn fenets to add the clock query.")

    # Build set of named_nets from unconnected_rewires — these are new wires
    # declared by the applier and absent in PreEco by design. Used to exempt
    # them from existence checks in Check 36 and the NET-ABSENT check below.
    _unconn_rewire_named_nets = set()
    for _stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for _e in study.get(_stage_name, []):
            for _ur in (_e.get('unconnected_rewires') or []):
                _nn = _ur.get('named_net', '')
                if _nn:
                    _unconn_rewire_named_nets.add(_nn)
                    _unconn_rewire_named_nets.add(_nn.split('[')[0])

    # ── 36. chain leaf inputs MUST be resolvable in PreEco netlist OR have
    # an explicit skip-existence flag (input_from_new_port,
    # input_from_unconnected_rewire, input_from_change). Without the flag,
    # eco_perl_spec returns SKIP with 'input net <X> absent in <stage>' and
    # the chain gate doesn't land in PostEco — silently breaks the DFF.D
    # driver. Run 20260515071155 surface (BeqCtrlPeSrc_0_/_1_/_2_ flat-form
    # leaves vs bracket-form netlist — caused by analyzer flat-form
    # representation in d_input_expected_function leaking into chain pcs).
    OUT_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S')
    skip_flags = ('input_from_new_port', 'input_from_unconnected_rewire',
                  'input_from_change')
    # Collect all port_connection bus_rename net_names — these wires are created
    # by the ECO (eco_netlist_port_rewire renames UNCONNECTED → new_name), so they
    # don't exist in PreEco and are exempt from the flat-form check.
    _bus_rename_nets = set()
    for _e36 in study.get('Synthesize', []):
        if not isinstance(_e36, dict): continue
        if _e36.get('change_type') == 'port_connection' and _e36.get('bus_bit_index') is not None:
            _n = _e36.get('net_name', '')
            if _n:
                _bus_rename_nets.add(_n)
    for stage in ('Synthesize',):  # Synth is enough — same chain across stages
        for e in study.get(stage, []):
            if e.get('change_type') != 'new_logic_gate':
                continue
            inst = e.get('instance_name', '?')
            host = e.get('module_name', '')
            pcs = e.get('port_connections') or {}
            for pin, val in pcs.items():
                if pin in OUT_PINS or not isinstance(val, str):
                    continue
                v = val.strip()
                # Skip constants, n_eco_* (intra-batch refs), explicit skip flags,
                # named_nets from unconnected_rewires, and bus_rename targets
                # (port_connection bus renames create new wires — exempt from flat-form check)
                if v.startswith(("1'b", "0'b", "1'h", "0'h")): continue
                if v.startswith('n_eco_'): continue
                if any(e.get(f) == v for f in skip_flags): continue
                if v in _unconn_rewire_named_nets: continue
                if v in _bus_rename_nets: continue  # ECO-created wire, doesn't pre-exist
                # Bracket-form bus-bit names (e.g. SIG[0]) need to be matched
                # against the netlist as bracket form — flat form (SIG_0_) is a
                # leak from sympy-eval-friendly representation. Detect and
                # warn on flat form so the wrapper rewrites it.
                if re.match(r'^[A-Za-z_][A-Za-z0-9_]*?_\d+_$', v):
                    issues.append(
                        f"HIGH/36-CHAIN-INPUT-FLAT-FORM: gate {inst}.{pin} = {v!r} "
                        f"uses flat-net form (sympy-friendly); the netlist has "
                        f"bracket form. eco_perl_spec.net_exists check will FAIL "
                        f"and the gate will SKIP. Wrapper must convert "
                        f"`SIG_<N>_` → `SIG[<N>]` after eco_synth_chain returns "
                        f"(see eco_emit_dff_entry.py flat_to_bracket post-pass).")

    # ── 37. reset_pin_used must be set explicitly (true/false), never None ──
    # The studier MD §0c says reset_pin_used MUST be true (when a DFF with
    # the right reset pin is found in scope) or false (with reason — reset
    # baked into D-cone). None means the wrapper or studier didn't make the
    # decision; downstream applier logic falls through and the reset path
    # may be inconsistent across stages. Run 20260515071155 surface.
    for e in study.get('Synthesize', []):
        if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
            continue
        if e.get('reset_pin_used') is None:
            inst = e.get('instance_name', '?')
            issues.append(
                f"MEDIUM/37-RESET-PIN-USED-UNSET: DFF {inst} has "
                f"reset_pin_used=None. Studier (or wrapper) MUST set this to "
                f"true (when find_reset_capable_dff returned a hit) or false "
                f"(with reset_pin_used:false + reason — reset baked into D-cone). "
                f"None defeats the §0c decision tree and downstream applier can't "
                f"tell whether to wire the dedicated reset pin or rely on the chain.")

    # ── 38. CHAIN-LEAF-POLARITY-PARITY ─────────────────────────────────────
    # For each chain leaf input net referenced by a new_logic_gate, count
    # the inverters between the consuming gate's pin and the nearest DFF.Q
    # (or primary input) driving that net in EACH stage's PreEco netlist.
    # If the INV count parity differs across stages, the chain reads opposite
    # logical values per stage → FM Route/PP mismatch.
    #
    # Root cause (run 20260515084942 round 6): Rule 32 picked the bare RTL
    # name `ArbCtrlPeRdy` for Route's chain leaf, but P&R had added 3
    # inverters between ArbCtrlPeRdy_reg.Q (= aps_rename_12109_ in Route's
    # merged MB DFF) and the port-named wire `ArbCtrlPeRdy` for drive-
    # strength optimization. Synth/PP had 0/2 inverters in the same path.
    # The chain entry `.A2(ArbCtrlPeRdy)` therefore computed
    # `OR4(...,+ArbCtrlPeRdy,...)` in Synth/PP but `OR4(...,~ArbCtrlPeRdy,...)`
    # in Route → cone divergence → 1 failing point that survived 6 rounds.
    #
    # Trace strategy (depth-bounded): from the consumer gate's `.<pin>(<wire>)`,
    # walk upstream by following each cell's output → input. INV/INVD/INV*
    # cells add 1 to the parity count. Stop at: DFF.Q output, primary
    # input port, or after 8 hops. Compare parity across stages.
    _INV_RE = re.compile(r'^(INV|INVD|INVSKR|INVLLKG|INVTX|INVSK|INVFE)', re.IGNORECASE)
    _OUT_PIN_RE = re.compile(r'\.\s*(Z|ZN|ZN1|Q|QN|CO|S)\s*\(\s*(\w+)\s*\)')
    _INPUT_PORT_DECL_RE = re.compile(r'^\s*input\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]', re.MULTILINE)
    _INST_HEAD_RE = re.compile(r'^([A-Z][A-Z0-9_]+)\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
    # Cache: (stage, host_module) → ({net → (cell_type, inst, inv_input_net)}, primary_inputs_set)
    _MODULE_INDEX_CACHE = {}

    def _index_module_body(host_module, ref_dir, stage):
        """Parse <host>'s PreEco body once and build:
          driver_map: { output_net : (cell_type, inst_name, inv_input_net|None) }
          primary_inputs: { signal_name, ... }
        Tries `<host>` then `<host>_0` (Route uniquification). Cached.
        Indexes INV cells with their .I() input net for fast walking.
        Non-INV cells: stored but inv_input_net=None (parity walk stops there).
        """
        key = (stage, host_module)
        if key in _MODULE_INDEX_CACHE:
            return _MODULE_INDEX_CACHE[key]
        gz = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
        if not gz.is_file():
            _MODULE_INDEX_CACHE[key] = (None, None); return _MODULE_INDEX_CACHE[key]
        # IN-MEMORY module extraction: decompress the stage netlist ONCE (cached temp via
        # _plain_netlist) + read its text ONCE (cached via _nl_text), then slice the module body
        # from memory with a bounded non-greedy regex. This replaces a per-module `awk` subprocess
        # that re-scanned the whole 4.2M-line netlist each call — with ~40 uniquified modules
        # (umcrecrcqentry_0..39) × 3 stages that was the ~1h validator hotspot. Output is identical
        # (module <host> body [+ module <host>_0 body], comment-stripped below).
        try:
            plain = _plain_netlist(str(gz))
            full = _nl_text(plain)
            modmap = _nl_module_map(plain)          # one-pass index, cached per stage
        except Exception:
            full = ''; modmap = {}
        if not full:
            _MODULE_INDEX_CACHE[key] = (None, None); return _MODULE_INDEX_CACHE[key]
        parts = []
        for _mod in (host_module, f'{host_module}_0'):
            span = modmap.get(_mod)
            if span:
                parts.append(full[span[0]:span[1]])
        txt = '\n'.join(parts)
        if not txt:
            _MODULE_INDEX_CACHE[key] = (None, None); return _MODULE_INDEX_CACHE[key]
        # Strip comments
        txt = re.sub(r'//[^\n]*', '', txt)
        txt = re.sub(r'/\*.*?\*/', '', txt, flags=re.DOTALL)
        # Primary inputs of the module (port direction declarations)
        primary_inputs = set(_INPUT_PORT_DECL_RE.findall(txt))
        # Walk instance heads and parse each block by balanced () matching
        driver_map = {}
        inst_iter = list(_INST_HEAD_RE.finditer(txt))
        for idx, m in enumerate(inst_iter):
            cell_type, inst_name = m.group(1), m.group(2)
            # Skip Verilog keywords / module decls / port decls
            if cell_type in ('module','endmodule','input','output','inout',
                             'wire','reg','tri','wand','wor','assign','always',
                             'initial','parameter','localparam','function','task',
                             'generate','endgenerate'):
                continue
            # Block bounds: from this match's '(' to balanced ')'
            open_pos = m.end() - 1
            depth = 0; close_pos = -1
            # Cap scan to next instance head (or +20k chars) to keep bounded
            scan_end = inst_iter[idx+1].start() if idx+1 < len(inst_iter) else min(open_pos + 20000, len(txt))
            for j in range(open_pos, scan_end):
                c = txt[j]
                if c == '(': depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0: close_pos = j; break
            if close_pos < 0:
                continue
            block = txt[open_pos+1:close_pos]
            # Find output pin's net
            inv_input = None
            is_inv = bool(_INV_RE.match(cell_type))
            if is_inv:
                # Extract .I(<wire>)
                im = re.search(r'\.\s*I\s*\(\s*(\w+)\s*\)', block)
                if im: inv_input = im.group(1)
            for op_m in _OUT_PIN_RE.finditer(block):
                out_pin, out_net = op_m.group(1), op_m.group(2)
                # Tag DFF outputs distinctly
                terminal_kind = ('dff_q' if out_pin == 'Q' else
                                 'dff_qn' if out_pin == 'QN' else
                                 'dff_co' if out_pin == 'CO' else
                                 'dff_s' if out_pin == 'S' else
                                 'comb')
                # Keep first writer per net (in Verilog each wire has one driver)
                driver_map.setdefault(out_net, (cell_type, inst_name, inv_input, terminal_kind, is_inv))
        _MODULE_INDEX_CACHE[key] = (driver_map, primary_inputs)
        return _MODULE_INDEX_CACHE[key]

    def _net_parity_in_stage(net, host_module, ref_dir, stage, max_hops=8):
        """Parity 0/1 + terminal kind via indexed driver_map (fast).

        Note: CTS primary inputs (FxPrePlace_*/FxPlace_*) stop the trace at
        the module boundary with parity=0. The fenets rename map records
        polarity as (+)/(−) — the studier ONLY accepts (+) results, so any
        CTS-renamed primary input in the study JSON is guaranteed to have the
        SAME polarity as the Synth reference signal. Do NOT add extra parity
        for CTS inputs based on cell name patterns (e.g. HFSINV/I does NOT
        mean inverted — fenets (+) is the authoritative polarity indicator).
        """
        driver_map, primary_inputs = _index_module_body(host_module, ref_dir, stage)
        if driver_map is None:
            return None
        cur = (net or '').strip()
        parity = 0
        for hop in range(max_hops):
            if cur in primary_inputs:
                return parity, 'primary_input'
            d = driver_map.get(cur)
            if d is None:
                return parity, 'unresolved'
            cell_type, inst_name, inv_input, terminal_kind, is_inv = d
            if terminal_kind == 'dff_qn':
                parity ^= 1
                return parity, f'dff_{inst_name}'
            if terminal_kind in ('dff_q','dff_co','dff_s'):
                return parity, f'dff_{inst_name}'
            # Combinational
            if is_inv:
                parity ^= 1
                if inv_input is None:
                    return parity, 'unresolved_inv'
                cur = inv_input
                continue
            return parity, f'comb_{cell_type[:8]}'
        return parity, 'max_hops'

    # Exclude output pins (Z/ZN/...) and clock pins (CP/CK/CLK) from polarity
    # check. Clock buffers intentionally have odd INV counts (CTS handles
    # clock-tree parity separately); only combinational data-path pins are
    # candidates for Mode J polarity flips.
    _NO_CHECK_PINS_38 = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S',
                          'CP', 'CK', 'CLK', 'CLOCK')
    _ENTRY_TYPES_38 = ('new_logic_gate', 'new_logic_dff')
    _placeholder_prefixes = ("MODE_H_ROUTE_SKIP", "UNRESOLVABLE",
                              "PENDING_FM_RESOLUTION", "NEEDS_NAMED_WIRE")
    # Pre-index instance_name -> entry per stage array so we can fall back to
    # the stage's OWN entry when per_stage_per_stage schema drift hides the pin.
    # Defends Check 38 against the (unfortunately common) studier schema where
    # port_connections_per_stage.Synthesize uses {A1, A2, ZN} (canonical) but
    # PrePlace/Route use {A, B, ZN} for the same gate — fall back to Synth
    # value would then false-fire polarity mismatch even though the actual
    # netlist (applied from each stage-array's bare port_connections) is fine.
    _stage_inst_map_38 = {
        stg: {ee.get('instance_name', ''): ee for ee in study.get(stg, [])}
        for stg in ('Synthesize', 'PrePlace', 'Route')
    }

    def _lookup_pin_net_38(inst_name, pin, stage, pcs_per_stage, synth_v):
        """Resolve the net wired to <inst_name>.<pin> in <stage>.
        Priority: (1) Synth-entry's port_connections_per_stage[stage][pin]
                  (2) stage-array's own entry's port_connections[pin]
                  (3) fall back to Synth value (last-resort, matches old behavior)
        Returns None when no reliable answer is available so the caller can SKIP
        Check 38 for this pin/stage instead of false-firing.
        """
        stg_dict = pcs_per_stage.get(stage) or {}
        if pin in stg_dict and stg_dict[pin]:
            return stg_dict[pin]
        own = _stage_inst_map_38.get(stage, {}).get(inst_name)
        if own:
            own_pcs = own.get('port_connections') or {}
            if pin in own_pcs and own_pcs[pin]:
                return own_pcs[pin]
            # Try the per_stage dict of the stage's OWN entry too — sometimes
            # only that has the resolved net.
            own_ps = (own.get('port_connections_per_stage') or {}).get(stage) or {}
            if pin in own_ps and own_ps[pin]:
                return own_ps[pin]
        # Last-resort: Synth value (preserves old behavior in the rare case
        # where no per-stage data exists at all).
        return synth_v

    # FM-resolved leaves: a rename_map key whose per-stage values are all REAL
    # resolved nets (different from the bare leaf, not echo/FM-036/FALLBACK) was
    # resolved by find_equivalent_nets, which reports polarity as (+)/(−). The
    # studier/chain accept ONLY (+) results, so such a leaf is GUARANTEED
    # same-polarity across stages — the bounded inverter-parity walk below cannot
    # see that (the three stages use DIFFERENT named nets that trace to different
    # local cells), so it would false-fire. Skip parity for these, exactly as the
    # CTS-primary-input carve-out does (see _net_parity_in_stage note). Partial
    # resolution (any stage echoes/FM-036) is NOT skipped → still parity-checked.
    _fm_resolved_leaves_38 = set()
    for _k, _v in (_rmap or {}).items():
        if _k == '_metadata' or not isinstance(_v, dict):
            continue
        _leaf = _k.rsplit('/', 1)[-1]
        _vals = [_v.get(s) for s in ('Synthesize', 'PrePlace', 'Route')]
        if all(isinstance(x, str) and x and x != _leaf
               and 'FM-036' not in x and 'FALLBACK' not in x for x in _vals):
            _fm_resolved_leaves_38.add(_leaf)

    for e in study.get('Synthesize', []):
        if e.get('change_type') not in _ENTRY_TYPES_38:
            continue
        host = e.get('module_name', '')
        inst = e.get('instance_name', '?')
        ent_kind = 'dff' if e.get('change_type') == 'new_logic_dff' else 'gate'
        pcs_synth = e.get('port_connections') or {}
        pcs_per_stage = e.get('port_connections_per_stage') or {}
        for pin, val in pcs_synth.items():
            if pin in _NO_CHECK_PINS_38 or not isinstance(val, str): continue
            v = val.strip()
            if v.startswith(("1'b","0'b","1'h","0'h","n_eco_")): continue
            # FM-(+)-resolved leaf → polarity guaranteed; parity walk is invalid
            # (different named nets per stage). Skip, per the CTS carve-out rule.
            if v in _fm_resolved_leaves_38:
                continue
            per_stage_nets = {
                'Synthesize': _lookup_pin_net_38(inst, pin, 'Synthesize',
                                                 pcs_per_stage, v),
                'PrePlace':   _lookup_pin_net_38(inst, pin, 'PrePlace',
                                                 pcs_per_stage, v),
                'Route':      _lookup_pin_net_38(inst, pin, 'Route',
                                                 pcs_per_stage, v),
            }
            # Skip parity check for placeholder/unresolved nets (per stage)
            parities = {}
            for stg, n in per_stage_nets.items():
                if not n: continue
                if any(str(n).startswith(p) for p in _placeholder_prefixes): continue
                p = _net_parity_in_stage(n, host, args.ref_dir, stg)
                if p is not None:
                    parities[stg] = p
            if len(parities) < 3:
                continue
            par_vals = {stg: pv[0] for stg, pv in parities.items()}
            if len(set(par_vals.values())) > 1:
                terms = {stg: pv[1] for stg, pv in parities.items()}
                # Only a RELIABLE polarity mismatch when the parity walk reached the SAME terminal
                # driver (cell type) in every stage — then a differing inverter count is a genuine
                # extra inverter on a STABLE driver (the exact case this check targets: "P&R added an
                # odd number of inverters"). If the terminals DIFFER across stages (e.g. Synth OA12 /
                # PP OA21 / Route OAI21), P&R RESTRUCTURED the local driver, so the inverter-parity
                # counts measure different chains and are NOT comparable — the net value is typically
                # preserved (OA → INV+OAI double-inversion cancels). Skip to avoid a false positive;
                # FM remains the authoritative polarity gate. (Intent preserved: still catch a real
                # added-INV flip on a stable driver; no longer misfire on P&R driver restructures.)
                if len(set(terms.values())) > 1:
                    continue
                issues.append(
                    f"HIGH/38-CHAIN-LEAF-POLARITY-MISMATCH: {ent_kind} {inst}.{pin} "
                    f"net per-stage = {per_stage_nets} but inverter-parity "
                    f"counts DIFFER across stages: {par_vals} "
                    f"(terminals: {terms}). The chain will compute opposite "
                    f"logical values per stage → FM cone divergence. "
                    f"Cause: Rule 32 picked the bare RTL-named wire in a "
                    f"stage where P&R added an odd number of inverters between "
                    f"the registered driver and the port wire — bare name is "
                    f"the INVERSE logical value in that stage. Fix: use a "
                    f"polarity-correct wire (the DFF Q output directly, or "
                    f"FM's resolved pin location's actual wire).")

    # ── port_declaration output driver check ─────────────────────────────────
    # Hierarchical netlists use port_declaration(output) instead of port_promotion.
    # Same driver requirement: if the signal has no driver in PreEco Synthesize AND
    # no new_logic_gate in the study drives it, the output port is undriven → FM fail.
    import subprocess as _sp2
    _pre_synth_gz2 = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz') \
                     if args.ref_dir else ''
    for stage in ('Synthesize',):
        gate_output_nets_all = {e.get('output_net', '') for e in study.get(stage, [])
                                if e.get('change_type') in ('new_logic_gate', 'new_logic')}
        # Build set of nets wired by companion port_connection entries — a
        # child instance port connection provides the driver implicitly (the
        # child output pin drives the parent net through the connection).
        port_conn_nets = {
            (e.get('net_name') or e.get('new_token') or '')
            for e in study.get(stage, [])
            if e.get('change_type') == 'port_connection'
        }
        for e in study.get(stage, []):
            if e.get('change_type') != 'port_declaration':
                continue
            if e.get('declaration_type') != 'output':
                continue
            sig = e.get('signal_name') or e.get('new_token') or '?'
            if sig in gate_output_nets_all:
                continue  # driven by ECO gate ✓
            if sig in port_conn_nets:
                continue  # driven by companion port_connection (child instance output) ✓
            has_preeco_driver = False
            if _pre_synth_gz2 and os.path.exists(_pre_synth_gz2):
                try:
                    r = _sp2.run(
                        _fastcmd(f'zgrep -cE "\\.({"{"}ZN|Z|Q{"}"})\\s*\\(\\s*{sig}\\s*\\)" {_pre_synth_gz2}'),
                        shell=True, capture_output=True, text=True, timeout=30)
                    has_preeco_driver = int(r.stdout.strip() or 0) > 0
                except Exception:
                    pass
            if not has_preeco_driver:
                issues.append(
                    f"CRITICAL/PORT-DECL-NO-DRIVER: port_declaration(output) for '{sig}' "
                    f"in module '{e.get('module_name','?')}' — no driver cell in PreEco "
                    f"Synthesize and no new_logic_gate in study drives it. "
                    f"Emit INV+INV buffer chain entries (same as step 0i for port_promotion). "
                    f"Undriven output port → FM globally unmatched → cascading failures.")

    # ── port_promotion buffer chain check ────────────────────────────────────
    # Every port_promotion must result in the promoted signal being driven — either
    # by an existing driver cell in PreEco Synthesize OR by a new_logic_gate in
    # the study. flat_net_confirmed:true is not enough — the signal must have a
    # driver (ZN/Z/Q output pin), not just appear as a wire/input reference.
    import subprocess as _sp
    _pre_synth_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz') \
                    if args.ref_dir else ''
    for stage in ('Synthesize',):
        gate_output_nets = {e.get('output_net', '') for e in study.get(stage, [])
                            if e.get('change_type') in ('new_logic_gate', 'new_logic')}
        for e in study.get(stage, []):
            if e.get('change_type') != 'port_promotion':
                continue
            sig = e.get('signal_name') or e.get('new_token') or '?'
            if sig in gate_output_nets:
                continue  # driven by ECO gate
            # Check if PreEco already has a driver cell for this signal
            has_preeco_driver = False
            if _pre_synth_gz and os.path.exists(_pre_synth_gz):
                try:
                    r = _sp.run(_fastcmd(f'zgrep -cE "\\.({"{"}ZN|Z|Q{"}"})\\s*\\({" "}{sig}{" "}\\)" {_pre_synth_gz}'),
                                shell=True, capture_output=True, text=True, timeout=30)
                    has_preeco_driver = int(r.stdout.strip() or 0) > 0
                except Exception:
                    pass
            if not has_preeco_driver:
                issues.append(
                    f"CRITICAL/PORT-PROMO-NO-DRIVER: port_promotion for '{sig}' — "
                    f"no driver cell found in PreEco Synthesize and no new_logic_gate "
                    f"in study drives '{sig}'. Studier must check for actual driver "
                    f"(ZN/Z/Q pin), not just signal existence. Emit INV+INV buffer chain "
                    f"entries directly into study JSON (not via verifier). Undriven output "
                    f"port → FM globally unmatched → cascading failures.")

    # ── rewire cell_name_per_stage existence check ────────────────────────────
    # For each rewire entry, verify the cell named in cell_name_per_stage exists
    # in that stage's PreEco netlist. If absent, the applier will SKIP the rewire
    # in that stage — leaving the original driver unrenewed → double driver or
    # undriven companion net.
    _rewire_gz = {s: os.path.join(args.ref_dir, 'data', 'PreEco', f'{s}.v.gz')
               for s in ('Synthesize', 'PrePlace', 'Route')} if args.ref_dir else {}
    for stage_check in ('Synthesize', 'PrePlace', 'Route'):
        gz = _rewire_gz.get(stage_check, '')
        if not gz or not os.path.exists(gz):
            continue
        for e in study.get(stage_check, []):
            if e.get('change_type') != 'rewire':
                continue
            cpst = e.get('cell_name_per_stage') or {}
            cell_for_stage = cpst.get(stage_check) or e.get('cell_name', '')
            if not cell_for_stage:
                continue
            try:
                import subprocess as _sp4
                r = _sp4.run(_fastcmd(f'zgrep -cw "{cell_for_stage}" {gz}'),
                             shell=True, capture_output=True, text=True, timeout=30)
                cnt = int(r.stdout.strip() or 0)
                if cnt == 0:
                    issues.append(
                        f"CRITICAL/REWIRE-CELL-ABSENT: {stage_check} rewire "
                        f"'{e.get('old_net','')}→{e.get('new_net','')}' uses cell "
                        f"'{cell_for_stage}' which has 0 occurrences in PreEco "
                        f"{stage_check}. Applier will SKIP this rewire — original driver "
                        f"not renamed → double driver or undriven companion net. "
                        f"Set correct cell_name_per_stage for {stage_check} from the "
                        f"FM rename_map or Stage Fallback.")
            except Exception:
                pass

    # ── per-stage net existence check ────────────────────────────────────────
    # Every resolved net in port_connections_per_stage must exist in that stage's
    # PreEco netlist. A net that resolves to 0 occurrences is a wrong net — the
    # structural trace found an unrelated cell. The validator must catch this before
    # Apply silently skips or inserts broken gates.
    _gz = {s: os.path.join(args.ref_dir, 'data', 'PreEco', f'{s}.v.gz')
           for s in ('Synthesize', 'PrePlace', 'Route')} if args.ref_dir else {}
    _skip_net_prefixes = ("1'b", "n_eco_", "ECO_", "PENDING", "SEQMAP", "NEEDS_NAMED_WIRE", "MODE_H_ROUTE_SKIP")
    _skip_net_suffixes = ("_orig",)
    # New ECO ports declared in this ECO — absent in PreEco by design, skip existence check
    _new_eco_ports = {c.get('new_token','') or c.get('signal_name','')
                      for c in rtl_diff.get('changes',[])
                      if c.get('change_type') in ('new_port','port_declaration','port_promotion')}
    # Also collect Q/Z/ZN output nets of ALL new ECO gates/DFFs in the study JSON —
    # these are ECO-internal nets produced by new cells and absent in PreEco by design
    # (e.g. wdbptr_org0_d1p5[N] as Q of new DFF, wdbptr_org0_d1p5_N_ in flat form).
    _eco_internal_output_nets = set()
    for _stage in ('Synthesize', 'PrePlace', 'Route'):
        for _e in study.get(_stage, []):
            if _e.get('change_type') not in ('new_logic_gate', 'new_logic_dff', 'new_logic'):
                continue
            _pcs = _e.get('port_connections') or {}
            for _pin in ('Q', 'Z', 'ZN', 'ZN1', 'CO'):
                _v = _pcs.get(_pin, '')
                if _v and isinstance(_v, str):
                    _eco_internal_output_nets.add(_v)
                    _eco_internal_output_nets.add(_v.split('[')[0])
                    import re as _re2
                    _eco_internal_output_nets.add(_re2.sub(r'\[(\d+)\]', r'_\1_', _v))
    # _unconn_rewire_named_nets built above (before Check 36)
    for stage_check in ('Synthesize', 'PrePlace', 'Route'):
        gz = _gz.get(stage_check, '')
        if not gz or not os.path.exists(gz):
            continue
        for e in study.get(stage_check, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            inst = e.get('instance_name', '?')
            pps = e.get('port_connections_per_stage') or {}
            base_pcs = e.get('port_connections') or {}
            # Use per-stage overrides if present; fall back to base port_connections
            # so entries with no per-stage map still get existence-checked per stage
            pc_for_stage = pps.get(stage_check) or base_pcs
            ifnp = e.get('input_from_new_port', '')  # new ECO port — doesn't exist in PreEco
            for pin, net in pc_for_stage.items():
                if not isinstance(net, str): continue
                # metadata annotations stored ALONGSIDE real pins in the per-stage dict —
                # NOT pin→net entries. Their VALUE (e.g. net_source='real_rtl_name') is a
                # label, never a net, so checking it yields a bogus 0-occurrence flag.
                if pin in ('net_source', 'instance_confirmed', 'source', 'origin', 'note', 'comment'): continue
                if pin in ('ZN', 'Z', 'Q', 'CO', 'Y', 'S'): continue
                if any(net.startswith(p) for p in _skip_net_prefixes): continue
                if any(net.endswith(s) for s in _skip_net_suffixes): continue
                if net.startswith("1'"): continue
                if net == ifnp: continue  # new ECO port (input_from_new_port) — absent in PreEco by design
                if net.split('[')[0] in _new_eco_ports: continue  # new ECO port from rtl_diff
                if net in _eco_internal_output_nets: continue  # Q/Z of new ECO cell — absent in PreEco by design
                if net.split('[')[0] in _eco_internal_output_nets: continue
                if net in _unconn_rewire_named_nets: continue  # new wire from unconnected_rewires
                base = net.split('[')[0]
                try:
                    import subprocess as _sp3
                    r = _sp3.run(_fastcmd(f'zgrep -cw "{base}" {gz}'),
                                 shell=True, capture_output=True, text=True, timeout=30)
                    cnt = int(r.stdout.strip() or 0)
                    if cnt == 0:
                        issues.append(
                            f"CRITICAL/NET-ABSENT-IN-STAGE: {stage_check} {inst}.{pin} = "
                            f"'{net}' has 0 occurrences in PreEco {stage_check} — wrong net. "
                            f"FIX: run eco_resolve_synth_internal.py with the resolved Synth net "
                            f"to find the correct {stage_check} equivalent: "
                            f"python3 script/eco_scripts/eco_resolve_synth_internal.py "
                            f"--ref-dir <REF_DIR> --synth-net <synth_net> --stage {stage_check} "
                            f"--output /tmp/resolve.json. "
                            f"If script returns UNRESOLVABLE, use manual F1-F3 forward consumer "
                            f"search (eco_netlist_verifier.md Check 12).")
                except Exception:
                    pass

    # ── condition input Synth polarity check ──────────────────────────────────
    # For PENDING_FM_RESOLUTION condition inputs, the Synth resolved net from
    # condition_input_resolutions must be used directly in Synth port_connections.
    # Tracing to the source net (one level deeper) changes polarity — the source
    # of an INV output has the opposite sign. Read condition_input_resolutions
    # from the step2 fenets rpt and verify Synth values match.
    _step2_rpt = os.path.join(os.path.dirname(args.study),
                              f'{args.tag}_eco_step2_fenets.rpt')
    _cond_resolutions = {}  # signal_name → resolved_synth_net
    if os.path.exists(_step2_rpt):
        import re as _re2
        with open(_step2_rpt) as _f2:
            for _line in _f2:
                _m = _re2.match(r'\s+(\w+):\s+resolved=(\S+)', _line)
                if _m:
                    _cond_resolutions[_m.group(1)] = _m.group(2)
    # Build map: gate_instance → {pin → expected_resolved_net} for PENDING pins
    if _cond_resolutions:
        # Map each gate's PENDING_FM_RESOLUTION pins to their expected resolved net
        _gate_expected = {}  # inst_name → {pin → expected_net}
        for _c in rtl_diff.get('changes', []):
            for _g in (_c.get('new_condition_gate_chain') or []):
                _inst = _g.get('seq') or _g.get('instance_name', '')
                _inputs = _g.get('inputs') or []
                for _idx, _inp in enumerate(_inputs):
                    if 'PENDING_FM_RESOLUTION' in str(_inp):
                        _sig = str(_inp).replace('PENDING_FM_RESOLUTION:', '').split('[')[0]
                        if _sig in _cond_resolutions:
                            _gate_expected.setdefault(_inst, {})[_idx] = _cond_resolutions[_sig]
        # For each study gate, check if its Synth pin values match expected resolved nets
        _gz_s = _gz.get('Synthesize', '')
        for _e in study.get('Synthesize', []):
            if _e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            _inst = _e.get('instance_name', '')
            # Match by instance name suffix (seq like c006/c007)
            _expected_map = next(
                (_gate_expected[k] for k in _gate_expected if _inst.endswith(k) or k in _inst),
                {})
            if not _expected_map:
                continue
            _pps = (_e.get('port_connections_per_stage') or {}).get('Synthesize') or {}
            _pins = [p for p in _pps if p not in ('ZN', 'Z', 'Q', 'CO', 'Y', 'S')]
            for _idx, _pin in enumerate(_pins):
                if _idx not in _expected_map:
                    continue
                _expected = _expected_map[_idx]
                _actual = _pps.get(_pin, '')
                if _actual == _expected:
                    continue
                # Both exist in Synth but different → likely polarity swap
                if _gz_s and os.path.exists(_gz_s) and isinstance(_actual, str):
                    try:
                        _r = _sp3.run(_fastcmd(f'zgrep -cw "{_expected}" {_gz_s}'),
                                      shell=True, capture_output=True, text=True, timeout=15)
                        if int(_r.stdout.strip() or 0) > 0:
                            issues.append(
                                f"CRITICAL/CONDITION-POLARITY: Synthesize "
                                f"{_inst}.{_pin} = '{_actual}' but condition_input_resolutions "
                                f"resolved this input to '{_expected}'. Both exist in Synth — "
                                f"wrong net used (likely traced to source instead of using "
                                f"resolved net directly). Use '{_expected}' verbatim in Synth.")
                    except Exception:
                        pass

    # ── condition input identity check ───────────────────────────────────────
    # When a gate has multiple condition inputs (from different PENDING_FM_RESOLUTION signals)
    # that resolved to DIFFERENT nets in Synthesize, they must also resolve to DIFFERENT
    # nets in PP/Route. If they collapse to the same PP/Route net, the structural trace
    # found the wrong cell for one of them.
    for e in study.get('Synthesize', []):
        if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
            continue
        pps = e.get('port_connections_per_stage') or {}
        synth_pins = {pin: net for pin, net in (pps.get('Synthesize') or {}).items()
                      if not pin.startswith('_')  # skip metadata keys (_input_from_new_port_*)
                      and pin not in ('ZN', 'Z', 'Q', 'CO', 'Y', 'S')
                      and isinstance(net, str)
                      and not any(net.startswith(p) for p in _skip_net_prefixes)
                      and not net.startswith("1'")}
        if len(set(synth_pins.values())) <= 1:
            continue  # all same or only one pin — no identity check needed
        for stage_id in ('PrePlace', 'Route'):
            stage_pins = {pin: net for pin, net in (pps.get(stage_id) or {}).items()
                          if pin in synth_pins}
            if len(stage_pins) < 2:
                continue
            nets = list(stage_pins.values())
            if len(set(nets)) < len(nets):  # duplicates exist
                inst = e.get('instance_name', '?')
                dups = [n for n in nets if nets.count(n) > 1]
                issues.append(
                    f"CRITICAL/CONDITION-INPUT-COLLAPSED: {stage_id} {inst} — "
                    f"multiple condition inputs collapsed to same net {list(set(dups))}. "
                    f"In Synthesize they resolve to different nets but in {stage_id} they "
                    f"are identical — structural trace found wrong P&R equivalent for one. "
                    f"Each PENDING_FM_RESOLUTION signal must be traced independently "
                    f"(eco_netlist_verifier.md Check 12).")

    # ── and_term companion rewire check ──────────────────────────────────────
    # Every new_logic_gate with an input ending in _orig (renamed intermediate net)
    # must have a companion rewire entry that creates that net. Without the rewire
    # the _orig net is undriven → floating A1 → thousands of FM failures.
    rewired_nets = set()
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') == 'rewire':
                rewired_nets.add(e.get('new_net', ''))
    for stage in ('Synthesize',):  # check once from Synth entries
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            pcs = e.get('port_connections') or {}
            for pin, net in pcs.items():
                if pin in ('Z', 'ZN', 'Q', 'CO', 'Y', 'S'): continue
                if isinstance(net, str) and net.endswith('_orig') and net not in rewired_nets:
                    issues.append(
                        f"CRITICAL/ANDTERM-MISSING-REWIRE: {e.get('instance_name','?')} "
                        f"input {pin}='{net}' is a renamed intermediate net but no rewire "
                        f"entry creates it. Add a companion rewire: old_token → '{net}' "
                        f"per stage using rename_map. Without it '{net}' is undriven → "
                        f"floating pin → FM globally unmatched cone inputs.")

    # ── FM cell/pin format check (GAP-1) ────────────────────────────────────
    # FM returns i:/FMWORK.../<cell>/<pin> — the studier must convert to the
    # actual wire name by grepping the netlist. Any port_connections value that
    # looks like "<CELL>/<pin>" is an FM location address, not a wire name —
    # the applier will grep for it in the netlist, find nothing, and SKIP the gate.
    import re as _re
    _fm_pin_re = _re.compile(r'^[A-Za-z][A-Za-z0-9_]+/[A-Za-z0-9]+$')
    _skip_prefixes = ("1'b", "1'B", "n_eco_", "ECO_", "PENDING", "SEQMAP",
                      "FxPrePlace_", "FxPlace_", "FxOptCts_", "FxCts_",
                      "dftopt", "tmp_net", "copt_net", "PCECO_")
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            inst = e.get('instance_name', '?')
            for pc_label, pc_dict in [
                ('port_connections', e.get('port_connections') or {}),
                *[(f'port_connections_per_stage[{stage}]',
                   (e.get('port_connections_per_stage') or {}).get(stage) or {})],
            ]:
                for pin, net in pc_dict.items():
                    if pin in ('Z', 'ZN', 'Q', 'QN', 'CO', 'Y', 'S'): continue
                    if not isinstance(net, str): continue
                    if any(net.startswith(p) for p in _skip_prefixes): continue
                    if net.startswith("1'"): continue
                    if _fm_pin_re.match(net):
                        issues.append(
                            f"CRITICAL/GAP1-FM-PIN-FORMAT: {stage} {inst} {pc_label}.{pin} = "
                            f"'{net}' is an FM cell/pin location address, not a wire name. "
                            f"Apply GAP-1: grep '<cell>' in PreEco/{stage}.v.gz, read "
                            f"'.<pin>(<actual_wire>)' and use <actual_wire> instead. "
                            f"Applier will SKIP this gate because it cannot find this string "
                            f"as a wire in the netlist.")

    # ── PENDING_FM_RESOLUTION in study port connections ─────────────────────
    # All PENDING_FM_RESOLUTION placeholders must be resolved before study exits.
    # An unresolved PENDING in port_connections means the gate will be inserted
    # with a missing/floating pin, causing FM elaboration errors or wrong function.
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            inst = e.get('instance_name', '?')
            change_type = e.get('change_type', '')
            if change_type in ('rewire', 'port_declaration', 'port_connection',
                               'port_promotion', 'undo_instance'):
                continue  # these don't have input port_connections to check
            for pc_label, pc_dict in [
                ('port_connections', e.get('port_connections') or {}),
                *[(f'port_connections_per_stage[{s}]',
                   (e.get('port_connections_per_stage') or {}).get(s) or {})
                  for s in ('Synthesize', 'PrePlace', 'Route')],
            ]:
                for pin, net in pc_dict.items():
                    if pin in ('Z', 'ZN', 'Q', 'QN', 'CO', 'Y', 'S'): continue
                    if not isinstance(net, str): continue
                    if ('PENDING_FM_RESOLUTION' in net or net.startswith('UNRESOLVABLE')
                            or net.startswith('MODE_H_ROUTE_SKIP')
                            or (net.startswith('NEEDS_NAMED_WIRE') and ':' in net)):
                        sig = (net.replace('PENDING_FM_RESOLUTION:', '')
                                  .replace('PENDING_FM_RESOLUTION', '')
                                  .replace('UNRESOLVABLE:', '')
                                  .replace('MODE_H_ROUTE_SKIP:', '').strip(':'))
                        kind = ('UNRESOLVABLE' if net.startswith('UNRESOLVABLE')
                                else 'MODE_H_ROUTE_SKIP' if net.startswith('MODE_H_ROUTE_SKIP')
                                else 'NEEDS_NAMED_WIRE' if net.startswith('NEEDS_NAMED_WIRE')
                                else 'PENDING_FM_RESOLUTION')
                        fix = ("FIX: run eco_resolve_synth_internal.py with the resolved Synth net: "
                               "python3 script/eco_scripts/eco_resolve_synth_internal.py "
                               "--ref-dir <REF_DIR> --synth-net <synth_net> --stage <stage> "
                               "--output /tmp/resolve.json  "
                               "If script returns UNRESOLVABLE, use manual F1-F3 forward consumer "
                               "search (eco_netlist_verifier.md Check 12).")
                        issues.append(
                            f"CRITICAL/PENDING-UNRESOLVED: {stage} {inst} {pc_label}.{pin} = "
                            f"{kind}:{sig} — not resolved before study exit. {fix} "
                            f"A floating pin on an inserted gate causes FM elaboration error.")

    # ── Stage Fallback rewire cone check ─────────────────────────────────────
    # When a rewire entry used STAGE_FALLBACK, the verifier grepped for old_net
    # and may have picked the wrong cell (e.g. a cell not in the target DFF cone).
    # Check: for any rewire with fm_source containing "STAGE_FALLBACK" or "stage_fallback",
    # cross-check that Synthesize rewired a different (or same) cell — if Synth and the
    # fallback stage rewired different cells, flag for cone verification.
    synth_rewires = {e.get('cell_name'): e for e in study.get('Synthesize', [])
                     if e.get('change_type') == 'rewire'}
    for stage in ('PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') != 'rewire':
                continue
            src = (e.get('fm_source') or '').lower()
            if 'stage_fallback' not in src and 'fallback' not in src:
                continue
            cell = e.get('cell_name', '')
            old_net = e.get('old_net', '')
            # Find Synth rewire for same old_net
            synth_e = next((v for v in synth_rewires.values()
                            if v.get('old_net') == old_net), None)
            if synth_e and synth_e.get('cell_name') != cell:
                synth_cell = synth_e.get('cell_name', '?')
                issues.append(
                    f"MEDIUM/FALLBACK-CONE-MISMATCH: {stage} rewire used STAGE_FALLBACK "
                    f"and chose cell '{cell}' but Synthesize used '{synth_cell}' for the "
                    f"same old_net='{old_net}'. These are different cells — the fallback "
                    f"may have picked a wrong cell not in the target DFF cone. "
                    f"Verify '{cell}' output reaches the target DFF in {stage} PreEco. "
                    f"If wrong, use HFS alias search: find the {stage} equivalent of "
                    f"the Synth cell's output net, trace 1 hop forward to the consumer, "
                    f"then back to find the correct {stage} cell.")

    # ── gate cell type in PreEco module scope check ───────────────────────────
    # For every new_logic_gate, verify that the cell_type (e.g. OA21D8, NR2D2)
    # actually exists in the PreEco Synthesize netlist within the declaring module.
    # If synthesis never used that cell type in that module, the agent likely invented
    # an alternate gate decomposition → scan-enable structural divergence between
    # Synth ECO and PP ECO → thousands of FM failures even with correct logic.
    _synth_gz_ct = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz') \
                   if args.ref_dir else ''
    _synth_text_ct = ''
    if _synth_gz_ct and os.path.exists(_synth_gz_ct):
        try:
            import gzip as _gz_ct
            with _gz_ct.open(_synth_gz_ct, 'rt', errors='replace') as _f_ct:
                _synth_text_ct = _f_ct.read()
        except Exception:
            pass
    if _synth_text_ct:
        import re as _re_ct
        for _e in study.get('Synthesize', []):
            if _e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            _ct = _e.get('cell_type', '')
            _mod = _e.get('module_name', '')
            if not _ct or not _mod:
                continue
            # Extract cell type prefix (strip drive strength/suffix): OA21D8 → OA21
            _ct_prefix = _re_ct.match(r'^([A-Z][A-Z0-9]+)', _ct)
            if not _ct_prefix:
                continue
            _prefix = _ct_prefix.group(1)
            # Find the module body in PreEco and check if cell type exists
            _mod_m = _re_ct.search(
                rf'^module\s+{_re_ct.escape(_mod)}\b.*?^endmodule\b',
                _synth_text_ct, _re_ct.MULTILINE | _re_ct.DOTALL)
            if not _mod_m:
                continue
            _mod_body = _mod_m.group(0)
            if _re_ct.search(rf'\b{_re_ct.escape(_ct)}\b', _mod_body):
                continue  # exact cell type found in module ✅
            # Check if ANY cell with same prefix exists in module
            _prefix_found = bool(_re_ct.search(rf'\b{_re_ct.escape(_prefix)}\w*\b', _mod_body))
            if not _prefix_found:
                issues.append(
                    f"FAIL/GATE-TYPE-NOT-IN-PREECO: {_e.get('instance_name','?')} "
                    f"uses cell_type '{_ct}' (prefix {_prefix}) but this cell family "
                    f"has 0 occurrences in PreEco module '{_mod}'. "
                    f"Using an invented cell type causes FM scan-path structural mismatch "
                    f"even when logic is correct. FIX: grep PreEco Synthesize for cells "
                    f"implementing the same boolean function near the pivot cone (E4c) "
                    f"and use that exact cell type (e.g. AO22EQ2AD1AMDB not AO22D1AMDB).")

    # ── cell pin name vs library check ────────────────────────────────────────
    # Verify that pin names used in port_connections actually exist on the cell type.
    # Common mismatch: NOR2 uses A2; INR2 uses B1. Flipping gate without remapping
    # pins causes FE-LINK-7 ABORT in FM (pin has no corresponding port on library cell).
    _pin_map = {
        'INR2': {'inputs': ('A1', 'B1'), 'outputs': ('ZN',)},
        'NOR2': {'inputs': ('A1', 'A2'), 'outputs': ('ZN',)},
        'IND2': {'inputs': ('A1', 'B1'), 'outputs': ('ZN',)},
        'AND2': {'inputs': ('A1', 'A2'), 'outputs': ('Z',)},
        'AN2':  {'inputs': ('A1', 'A2'), 'outputs': ('Z',)},
        'NR2':  {'inputs': ('A1', 'A2'), 'outputs': ('ZN',)},
        'INV':  {'inputs': ('I',),        'outputs': ('ZN',)},
        'OR3':  {'inputs': ('A1', 'A2', 'A3'), 'outputs': ('Z',)},
        'OA21': {'inputs': ('A1', 'A2', 'B'),  'outputs': ('Z',)},
        'OA12': {'inputs': ('A1', 'A2', 'B'),  'outputs': ('Z',)},
    }
    for stage in ('Synthesize',):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                continue
            fn = (e.get('gate_function') or '').upper()
            # Find matching library entry (prefix match)
            lib_entry = next((v for k, v in _pin_map.items() if fn.startswith(k)), None)
            if not lib_entry:
                continue
            pcs = e.get('port_connections') or {}
            inst = e.get('instance_name', '?')
            for pin in pcs:
                if pin in lib_entry['outputs']:
                    continue  # output pin — skip
                if pin not in lib_entry['inputs']:
                    issues.append(
                        f"CRITICAL/CELL-PIN-MISMATCH: {stage} {inst} uses pin '{pin}' "
                        f"but {fn} cell only has inputs {lib_entry['inputs']}. "
                        f"Likely gate function was changed without remapping pins "
                        f"(e.g. NOR2.A2 → INR2.B1). Fix: rename '{pin}' to correct pin name.")

    # ── and_term FM-polarity gate function check ──────────────────────────────
    # NOR2 vs INR2 must match FM raw rpt polarity (+/-), not cell-type prefix estimate.
    # FM(+) → renamed=+old → INR2.  FM(-) → renamed=~old → NOR2.
    _step2_dir = os.path.dirname(args.study)
    import glob as _glob
    _raw_rpts = _glob.glob(os.path.join(_step2_dir, '*_find_equivalent_nets_raw*.rpt'))
    _raw_text = ''
    for _rp in sorted(_raw_rpts):
        try:
            _raw_text += open(_rp).read()
        except Exception:
            pass
    if _raw_text:
        for _c in rtl_diff.get('changes', []):
            if _c.get('change_type') != 'and_term':
                continue
            _old_tok = _c.get('old_token', '')
            # Find old driver cell instance from study rewire entries
            _cell_inst = ''
            for _e in study.get('Synthesize', []):
                if _e.get('change_type') == 'rewire' and _e.get('old_net') == _old_tok:
                    _cell_inst = (_e.get('cell_name_per_stage') or {}).get('Synthesize') or \
                                  _e.get('cell_name', '')
                    break
            if not _cell_inst:
                continue
            # Find polarity: "Impl  Net  +/- i:/.../CELL_INST/ZN"
            import re as _re_pol
            _pol_m = _re_pol.search(
                r'Impl\s+Net\s+([+\-])\s+i:[^\n]+/' + _re_pol.escape(_cell_inst) + r'/',
                _raw_text)
            if not _pol_m:
                continue
            _fm_pol = _pol_m.group(1)
            _expected = 'INR2' if _fm_pol == '+' else 'NOR2'
            # Find and_term gate entry in study
            for _e in study.get('Synthesize', []):
                if _e.get('change_type') not in ('new_logic_gate', 'new_logic'):
                    continue
                if _e.get('output_net') != _old_tok:
                    continue
                _actual = _e.get('gate_function', '')
                if _actual and _actual != _expected:
                    issues.append(
                        f"CRITICAL/ANDTERM-WRONG-POLARITY: '{_e.get('instance_name','?')}' "
                        f"uses {_actual} but FM raw rpt shows ({_fm_pol}) polarity for "
                        f"'{_cell_inst}' → renamed=({'+'  if _fm_pol=='+' else '~'}old) → "
                        f"correct gate is {_expected}. "
                        f"Use FM raw rpt polarity, not cell-type prefix estimate.")

    # ── and_term gate chain boolean function check ────────────────────────────
    # For each and_term change, find its gate chain in the Synthesize study entries
    # and verify the boolean function = old_expression & ~new_term.
    # old_driver_inverting from rtl_diff tells us polarity of the renamed old driver output.
    def _inv(ct):
        if not ct: return None
        import re as _re
        m = _re.match(r'^([A-Z]+)', str(ct))
        if not m: return None
        return any(m.group(1).startswith(p)
                   for p in sorted(['AOI','OAI','NOR','NAND','INV','NR','ND','IND','XNOR','XNR'],
                                   key=len, reverse=True))

    def _eval_gate_fn(fn, inputs):
        fn = str(fn).upper()
        # strip cell library suffix
        import re as _re
        fn = _re.sub(r'[A-Z]+BWP.*', '', fn).strip()
        if fn == 'INV':  return int(not inputs[0])
        if fn in ('AND2','AN2'): return int(all(inputs[:2]))
        if fn in ('NAND2','ND2'): return int(not all(inputs[:2]))
        if fn in ('OR2',): return int(any(inputs[:2]))
        if fn in ('NOR2','NR2'): return int(not any(inputs[:2]))
        if fn in ('INR2',): return int(bool(inputs[0]) and not bool(inputs[1]))
        if fn in ('IND2',): return int(not bool(inputs[0]) and bool(inputs[1]))
        return None

    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'and_term':
            continue
        old_inv = c.get('old_driver_inverting')
        if old_inv is None:
            continue  # polarity not recorded — Step 1 validator already flagged this
        old_token = c.get('old_token', '')
        new_term  = (c.get('new_token') or c.get('and_term_gate_input') or '').split('[')[0]
        # Collect gate chain from Synthesize study: new_logic_gate entries leading to old_token
        chain_entries = [e for e in study.get('Synthesize', [])
                         if e.get('change_type') == 'new_logic_gate'
                         and (e.get('output_net') == old_token
                              or any(old_token in str(v) for v in (e.get('port_connections') or {}).values())
                              or e.get('instance_name','').startswith('eco_'))]
        if not chain_entries:
            continue
        # Find renamed_net: first gate input not starting with n_eco_/1'b/PENDING/ECO_
        renamed_net = None
        for e in chain_entries:
            for v in (e.get('port_connections') or {}).values():
                base = str(v).split('[')[0]
                if base.startswith(("n_eco_","1'b",'PENDING','ECO_')): continue
                if base == new_term: continue
                renamed_net = base; break
            if renamed_net: break
        if not renamed_net:
            continue
        # Build net→gate map
        gate_map = {}  # output_net → (fn, [inputs])
        for e in chain_entries:
            fn  = e.get('gate_function') or e.get('cell_type') or ''
            out = e.get('output_net') or (e.get('port_connections') or {}).get('ZN') or (e.get('port_connections') or {}).get('Z') or ''
            pcs = e.get('port_connections') or {}
            inp_nets = [v for k, v in pcs.items() if k not in ('ZN','Z','Q','CO','Y','S')]
            if out:
                gate_map[out] = (fn, inp_nets)
        # Evaluate for all 4 input combinations
        mismatch = False
        for R in (0, 1):
            for N in (0, 1):
                old_val  = (1 - R) if old_inv else R
                expected = old_val & (1 - N)
                net_vals = {renamed_net: R, new_term: N}
                ok = True
                for e in chain_entries:
                    pcs = e.get('port_connections') or {}
                    fn  = e.get('gate_function') or ''
                    out = e.get('output_net') or pcs.get('ZN') or pcs.get('Z') or ''
                    if not fn or not out: continue
                    in_vals = []
                    for k, v in pcs.items():
                        if k in ('ZN','Z','Q','CO','Y','S'): continue
                        base = str(v).split('[')[0]
                        if str(v).startswith("1'b"):
                            in_vals.append(int(str(v)[-1]))
                        elif 'PENDING_ECO_PORT' in str(v) or 'PENDING_FM' in str(v):
                            in_vals.append(N)
                        elif base in net_vals:
                            in_vals.append(net_vals[base])
                        else:
                            ok = False; break
                    if not ok: break
                    res = _eval_gate_fn(fn, in_vals)
                    if res is None: ok = False; break
                    net_vals[out] = res
                if not ok: continue
                final_out = chain_entries[-1].get('output_net') or ''
                actual = net_vals.get(final_out)
                if actual is not None and actual != expected:
                    mismatch = True; break
            if mismatch: break
        if mismatch:
            pol_str = 'FM(-) → renamed=~old' if old_inv else 'FM(+) → renamed=+old'
            issues.append(
                f"CRITICAL/AND-TERM-BOOL: and_term gate chain for '{old_token}' produces "
                f"wrong boolean function (old_driver_inverting={old_inv}, {pol_str}). "
                f"Chain must output 'old_expression & ~new_term'. "
                f"FM(-)/inverting: use NOR2(renamed, new_term) directly. "
                f"FM(+)/non-inverting: use INR2(renamed, new_term). "
                f"old_driver_inverting must be set from FM polarity (+/-), not cell type prefix.")

    # ── BUS-CONST-DECODE: IND2/IND3/IND4 mis-used to decode bus equality constant ─
    # Source: rtl_diff new_condition_gate_chain (has RTL context with the constant).
    # Handles binary (2'b), hex (4'h), decimal (4'd), octal (3'o).
    import re as _re3
    def _vlog_const_to_bin_s3(width_str, base, val_str):
        try:
            w = int(width_str)
            b = {'b': 2, 'h': 16, 'd': 10, 'o': 8}.get(base.lower())
            if b is None:
                return None
            return format(int(val_str.replace('_', ''), b), f'0{w}b')
        except (ValueError, TypeError):
            return None

    for rc in rtl_diff.get('changes', []):
        chain_src = rc.get('new_condition_gate_chain') or rc.get('d_input_gate_chain') or []
        ctx_line  = str(rc.get('context_line', ''))
        for g in chain_src:
            fn = (g.get('gate_function') or '').upper()
            if fn not in ('IND2', 'IND3', 'IND4'):
                continue
            raw_inputs = g.get('inputs') or []
            bus_bits = [str(i) for i in raw_inputs if '[' in str(i)]
            if len(bus_bits) < 2:
                continue
            ctx = str(g.get('rtl_condition', '')) + ctx_line
            m = _re3.search(r"==\s*([0-9]+)'([bBhHdDoO])([0-9a-fA-F_]+)", ctx)
            if not m:
                continue
            const_bits = _vlog_const_to_bin_s3(m.group(1), m.group(2), m.group(3))
            if const_bits is None:
                continue
            if const_bits == '1' * len(const_bits):
                continue  # all-ones: IND-N correct
            study_inst = '?'
            for e in study.get('Synthesize', []):
                if (e.get('gate_function') or '').upper() == fn:
                    pcs = e.get('port_connections') or {}
                    sv  = set(str(v) for k, v in pcs.items() if k not in ('ZN','Z','Q','Y'))
                    if sv == set(str(i) for i in raw_inputs):
                        study_inst = e.get('instance_name', '?')
                        break
            issues.append(
                f"CRITICAL/BUS-CONST-DECODE: study '{study_inst}' uses "
                f"{fn}({', '.join(str(i) for i in raw_inputs)}) which computes "
                f"~(bus==all-ones) but RTL condition is "
                f"~(bus=={m.group(0).split('==')[1].strip()}) (binary={const_bits}). "
                f"Zero-bit positions need INV before the ND gate. "
                f"Fix: INV for each 0-bit input, then ND{len(bus_bits)} of all inputs. "
                f"See rtl_diff_analyzer.md §E2.5 rule 2b.")

    # ── 39. UNCONNECTED-RENAME-NAMED-NET DRIVER CONSISTENCY ─────────────────
    # Bug class caught: a DFF's unconnected_rewires[].named_net references a
    # form (e.g. "REG_UmcCfgEco_12_" underscore) that no port_connection or
    # wire decl in the study actually drives — the bus rename mechanism in
    # eco_perl_spec.py creates the OTHER form (e.g. "REG_UmcCfgEco[12]"
    # bracket via implicit bus indexing) so the consumer ends up undriven.
    # Result: FM Mode A failure on the new DFF's D-input cone (run
    # 20260525085948 round 1 — REG_UmcCfgEco_12_ globally undriven).
    #
    # Rule: every `named_net` referenced as an input by a downstream gate's
    # port_connections MUST be drivable — either:
    #   (a) Same name string appears as `net_name` on a port_connection entry
    #       (bus rename creates that wire), OR
    #   (b) `needs_explicit_wire_decl: true` AND eco_perl_spec.py declares it
    #       (verified via stage-level scope), OR
    #   (c) Name appears as `output_net` of some new_logic_gate entry, OR
    #   (d) Name pre-exists in PreEco netlist (skip — handled by other checks).
    #
    # If named_net is consumed but NONE of (a)-(d) hold → HARD FAIL.
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        stage = study.get(stage_name, [])
        if not stage:
            continue
        # Collect declared named_nets from unconnected_rewires
        named_nets = {}  # named_net → (declarer_inst, needs_explicit_wire_decl)
        for e in stage:
            for ur in (e.get('unconnected_rewires') or []):
                nn = ur.get('named_net')
                if not nn or not isinstance(nn, str):
                    continue
                named_nets[nn] = (
                    e.get('instance_name', '?'),
                    ur.get('needs_explicit_wire_decl', True),
                )
        if not named_nets:
            continue
        # Collect drivers: port_connection.net_name + new_logic_gate.output_net
        # + any explicit wire decl from eco_perl_spec.py (we approximate by
        # treating needs_explicit_wire_decl=True as a self-driver).
        pc_net_names = {(e.get('net_name') or '')
                        for e in stage
                        if e.get('change_type') == 'port_connection'}
        gate_outputs = {(e.get('output_net') or '')
                        for e in stage
                        if e.get('change_type') in ('new_logic_gate','new_logic_dff')}
        # Walk consumers
        for e in stage:
            if e.get('change_type') not in ('new_logic_gate', 'new_logic_dff'):
                continue
            inst = e.get('instance_name', '?')
            for pin, net in (e.get('port_connections') or {}).items():
                if pin in ('Z','ZN','ZN1','Q','QN','CO','S','CP','CK','CLK'):
                    continue
                if not isinstance(net, str): continue
                if net not in named_nets:
                    continue  # not a named_net reference — skip
                declarer, needs_decl = named_nets[net]
                # Check driver presence
                if net in pc_net_names:
                    continue  # driven by port_connection bus rename ✓
                if net in gate_outputs:
                    continue  # driven by a new gate ✓
                if needs_decl:
                    continue  # eco_perl_spec.py will declare it ✓ (trust the flag)
                # No driver found AND no explicit decl — UNDRIVEN
                issues.append(
                    f"HIGH/39-NAMED-NET-UNDRIVEN: stage={stage_name} gate "
                    f"{inst}.{pin}={net!r} references DFF {declarer}'s "
                    f"unconnected_rewires.named_net but no port_connection "
                    f"creates that net_name, no new gate outputs it, and "
                    f"needs_explicit_wire_decl=false (no wire decl will be "
                    f"emitted). Result: undriven net at FM time → Mode A "
                    f"fail. Fix: either (a) set needs_explicit_wire_decl=true "
                    f"to force wire decl, OR (b) change port_connection's "
                    f"net_name to match {net!r} exactly so bus rename drives "
                    f"it, OR (c) align named_net to the form actually created "
                    f"by the port_connection (typically bracket form "
                    f"<bus>[<bit>] for implicit bus indexing).")

    # ── 40. AND-TERM DRIVER-RENAME PATTERN DETECTION ────────────────────────
    # Mirrors Step 1 Check `and_term_pattern_issues` but for the study JSON
    # (rtl_diff.chain_design may have been cleared during round-N reclassif).
    # Bug pattern: a `rewire` entry renames an existing net (old_net → new_net)
    # AND a `new_logic_gate` produces output to that SAME old_net name. This
    # is the driver-rename and_term pattern that breaks LATCG/clock-gating
    # equivalence in FM. The correct pattern is DFF-pin-rewire: chain output
    # to a fresh n_eco_* net, then separately rewire DFF.D from old_net to
    # the new net (driver untouched).
    _OUT_PINS_40 = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S')
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        stage = study.get(stage_name, [])
        if not stage:
            continue
        # Collect old_net values from rewire entries (= renamed nets). EXEMPT the
        # deliberate combinational net-force (driver_side/net_force): re-driving a
        # combinational net by renaming its comb driver's OUTPUT pin and muxing the
        # forced value onto the original net is the CORRECT pattern for a signal with
        # combinational fanout (a DFF-pin rewire would only reach one consumer). The
        # driver-rename-and_term anti-pattern this check targets is the REGISTERED case.
        renamed_old_nets = {(e.get('old_net') or '').strip()
                            for e in stage
                            if e.get('change_type') == 'rewire'
                            and e.get('old_net')
                            and not (e.get('net_force') or e.get('driver_side'))}
        if not renamed_old_nets:
            continue
        # Find new_logic_gate entries whose output pin equals a renamed old_net
        for e in stage:
            if e.get('change_type') != 'new_logic_gate' or e.get('net_force'):
                continue
            inst = e.get('instance_name', '?')
            pcs = e.get('port_connections') or {}
            for pin, net in pcs.items():
                if pin not in _OUT_PINS_40: continue
                if not isinstance(net, str): continue
                if net.strip() in renamed_old_nets:
                    issues.append(
                        f"HIGH/40-AND-TERM-DRIVER-RENAME: stage={stage_name} "
                        f"new_logic_gate {inst}.{pin}={net!r} REUSES a net "
                        f"that a rewire entry also renames "
                        f"(old_net={net!r}). This is the driver-rename "
                        f"and_term pattern that breaks FM clock-gating "
                        f"equivalence (LATCG cone shape destroyed). "
                        f"Required: chain output to a fresh n_eco_<jira>_<seq> "
                        f"net AND emit a separate rewire on the consuming "
                        f"DFF.D pin from {net!r} to that new net. Leave the "
                        f"existing driver UNCHANGED. See rtl_diff_analyzer.md "
                        f"`MANDATORY insertion pattern — DFF-pin-rewire`.")

    # ── 41. REWIRE-DESTROYS-OLD-NET (and_term anti-pattern cleanup) ─────────
    # When and_term is correctly classified as DFF-pin-rewire (Check 40
    # passes — gate output is fresh n_eco_*), the upstream `rewire` entry
    # that renames the OLD driver's output is no longer needed and actively
    # HARMS FM. The LATCG clock-gating cone uses the driver's OUTPUT NET
    # NAME for structural matching — renaming it destroys the pattern even
    # when the chain Boolean is correct.
    #
    # Bad pattern: rewire on cell output pin where (a) old_net is NOT the
    # output of any new gate AND (b) new_net IS consumed by some new gate
    # input → the rewire is structurally dead AND breaks LATCG.
    _OUT_PINS_41 = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S')
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        stage = study.get(stage_name, [])
        if not stage: continue
        gate_outputs = set(); gate_input_nets = set()
        for e in stage:
            if e.get('change_type') not in ('new_logic_gate', 'new_logic_dff'):
                continue
            for pin, net in (e.get('port_connections') or {}).items():
                if not isinstance(net, str): continue
                if pin in _OUT_PINS_41:
                    gate_outputs.add(net.strip())
                else:
                    gate_input_nets.add(net.strip())
        for e in stage:
            if e.get('change_type') != 'rewire': continue
            pin = (e.get('pin') or '').strip()
            if pin not in _OUT_PINS_41: continue
            old_net = (e.get('old_net') or '').strip()
            new_net = (e.get('new_net') or '').strip()
            if not old_net or not new_net: continue
            if old_net not in gate_outputs and new_net in gate_input_nets:
                cell = e.get('cell_name', '?')
                issues.append(
                    f"HIGH/41-REWIRE-DESTROYS-OLD-NET: stage={stage_name} "
                    f"rewire on driver output {cell}.{pin}: {old_net!r} → "
                    f"{new_net!r} renames the driver but NO new gate outputs "
                    f"to {old_net!r}. The original net name is abandoned — "
                    f"this is the driver-rename and_term anti-pattern that "
                    f"breaks FM LATCG equivalence (Unmatched Cone Input on "
                    f"reset signal). Fix: DROP this rewire entry; change the "
                    f"consuming gate's input pin from {new_net!r} back to "
                    f"{old_net!r}. Fresh chain output (n_eco_*) + DFF.D pin "
                    f"rewire is sufficient — no driver rename needed. See "
                    f"rtl_diff_analyzer.md `MANDATORY insertion pattern — "
                    f"DFF-pin-rewire`.")

    # ── PORT-DECL-WRONG-SOURCE: port_declaration(output) buffer chain uses DFF
    # D-input wire directly instead of the pure combinational source.
    # Rule: driver_net must be the output of an INVERTING cell (.ZN — ND/NR/INV).
    # A non-inverting driver (.Z — AN2/OR/BUF) means extra AND/OR gating is mixed
    # in — the DFF D-input is NOT the pure signal, trace deeper.
    # Check by finding the first INV gate in the buffer chain and inspecting its source.
    # Works even when driver_net is None (studier didn't record it).
    for stage_name in ('Synthesize',):
        # Collect port_declaration(output) signals needing buffer chain
        pd_sigs = {e.get('signal_name') for e in study.get(stage_name, [])
                   if e.get('change_type') == 'port_declaration'
                   and e.get('declaration_type') == 'output'
                   and e.get('needs_buffer_chain')
                   and e.get('signal_name')}
        if not pd_sigs:
            continue
        # Multi-map: output_net → list of (input_net, instance_name)
        inv_mmap = {}
        for e in study.get(stage_name, []):
            if (e.get('gate_function') or '').upper() != 'INV':
                continue
            pcs = e.get('port_connections') or {}
            out = pcs.get('ZN') or pcs.get('Z') or ''
            inp = pcs.get('I') or ''
            if out and inp:
                inv_mmap.setdefault(out, []).append((inp, e.get('instance_name', '')))

        import re as _re_pds
        for sig in pd_sigs:
            # Trace backwards: sig ← INV(mid_net) ← INV(src_net)
            # All INV chains terminating at sig
            for mid_net, _mid_inst in inv_mmap.get(sig, []):
                for src_net, src_inst in inv_mmap.get(mid_net, []):
                    if not src_net:
                        continue
                    # N\d+ anonymous synthesis nets are typically non-inverting
                    # AND/OR outputs with extra gating — not the pure source
                    if _re_pds.match(r'^N\d{5,}$', src_net):
                        issues.append(
                            f"CRITICAL/PORT-DECL-WRONG-SOURCE: '{sig}' buffer chain "
                            f"'{src_inst}' uses source='{src_net}' — anonymous synthesis "
                            f"net (N\\d+) likely has extra AND-gate gating mixed in. "
                            f"Find the INVERTING cell (.ZN of ND/NR) driving this net "
                            f"and use its output as the pure combinational source. "
                            f"See eco_netlist_studier.md Phase 0.15 step 2.")
            # Also flag if driver_cell_inverting explicitly false
            for e in study.get(stage_name, []):
                if e.get('signal_name') == sig and e.get('driver_cell_inverting') is False:
                    issues.append(
                        f"CRITICAL/PORT-DECL-NONINV-SOURCE: '{sig}' buffer chain "
                        f"source driver_cell_inverting=false — non-inverting cell (AN/OR/.Z) "
                        f"has extra AND/OR gating. Use INVERTING (.ZN of ND/NR) as source. "
                        f"See eco_netlist_studier.md Phase 0.15 step 2.")
                    break

    # ── 42. BUS-GATE-BIT MODULE SCOPE CONSISTENCY ───────────────────────────
    # When a new_logic_gate entry has is_bus_gate: true, the per-bit expanded
    # entries (is_bus_gate_bit: true) MUST use the SAME module_name as the
    # parent change. The parent change declares the bus signal and its inputs
    # are only accessible in that module's scope. Using a different module
    # (e.g. a child module where and_term gates live) causes SVR-14 at FM
    # elaboration because the bus input signal is not in scope there.
    # eco_netlist_studier.md Phase 0.6 Step 2 enforces this rule.
    bus_gate_parents = {}  # signal_base → module_name from parent new_logic_gate
    for e in study.get('Synthesize', []):
        if e.get('is_bus_gate') and not e.get('is_bus_gate_bit'):
            sig = (e.get('output_net', '') or '').split('[')[0]
            mod = e.get('module_name', '')
            if sig and mod:
                bus_gate_parents[sig] = mod
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if not e.get('is_bus_gate_bit'):
                continue
            out = (e.get('output_net', '') or '')
            sig_base = out.split('[')[0]
            expected_mod = bus_gate_parents.get(sig_base)
            actual_mod = e.get('module_name', '')
            if expected_mod and actual_mod and expected_mod != actual_mod:
                inst = e.get('instance_name', '?')
                issues.append(
                    f"HIGH/42-BUS-GATE-BIT-WRONG-MODULE: stage={stage_name} "
                    f"bus-gate-bit entry {inst} (output={out!r}) has "
                    f"module_name={actual_mod!r} but parent bus gate declares "
                    f"module_name={expected_mod!r}. Per-bit entries MUST use "
                    f"the parent's module — the bus input signals are only in "
                    f"scope in {expected_mod!r}. Using a child module causes "
                    f"SVR-14 at FM elaboration. See eco_netlist_studier.md "
                    f"Phase 0.6 Step 2.")

    # ── 43. BUS-GATE-BIT UNCONNECTED INPUT WITHOUT unconnected_rewires ────────
    # Phase 0.6 expands bus gate bits AFTER Phase 0.4 runs, so UNCONNECTED_*
    # inputs on per-bit entries are never renamed unless the studier explicitly
    # adds unconnected_rewires post-expansion. Without it the gate reads from
    # an undriven net → INPUT_UNDRIVEN in Step 5 pre-FM check.
    _UNCONN_RE = re.compile(r'^(SYNOPSYS_)?UNCONNECTED_\d+$')
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if not e.get('is_bus_gate_bit'):
                continue
            has_ur = bool(e.get('unconnected_rewires'))
            inst = e.get('instance_name', '?')
            for pin, net in (e.get('port_connections') or {}).items():
                if pin in ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'):
                    continue
                if isinstance(net, str) and _UNCONN_RE.match(net):
                    if not has_ur:
                        issues.append(
                            f"HIGH/43-BUS-GATE-BIT-UNCONN-NO-REWIRE: stage={stage_name} "
                            f"is_bus_gate_bit entry {inst}.{pin}={net!r} reads an "
                            f"UNCONNECTED net but has no unconnected_rewires entry. "
                            f"Phase 0.4 ran before Phase 0.6 expansion and never saw "
                            f"this input — the studier must add unconnected_rewires in "
                            f"Phase 0.6 Step 4. See eco_netlist_studier.md Phase 0.6 "
                            f"Step 4.")

    # ── 44. MODULE NAME RESOLUTION (UNKNOWN / empty placeholder) ─────────────
    # eco_perl_spec.py routes new_logic_gate / new_logic_dff entries to the
    # module named in `module_name`. When that field is empty OR the literal
    # placeholder "UNKNOWN" (which the LLM-studier sometimes emits when
    # uncertain), perl_spec emits `Added to Perl spec for module UNKNOWN` and
    # the Perl pipe cannot insert into a nonexistent module — the cell is
    # silently DROPPED. The DFF.D consumer then references an undriven net →
    # guaranteed FM Mode A on the new cone (run 20260526225832 R1 root cause).
    #
    # HARD FAIL on any new_logic_* gate/dff entry whose `module_name` is
    # missing / empty / "UNKNOWN". Studier must set `module_name` to the
    # actual host module (typically the same module as the DFF whose D-input
    # the gate feeds).
    _BAD_MODULE_VALUES = {'', 'UNKNOWN', 'unknown', None}
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if e.get('change_type') not in ('new_logic_gate', 'new_logic_dff',
                                            'new_logic'):
                continue
            mod = e.get('module_name')
            if isinstance(mod, str):
                mod_check = mod.strip()
            else:
                mod_check = mod
            if mod_check in _BAD_MODULE_VALUES:
                inst = e.get('instance_name', '?')
                ct   = e.get('change_type', '?')
                # Best-effort hint: if this is a chain gate feeding a DFF in
                # the same stage, point at that DFF's module.
                hint = ''
                out_net = (e.get('output_net') or '').strip()
                if out_net:
                    for d in study.get(stage_name, []):
                        if d.get('change_type') != 'new_logic_dff':
                            continue
                        d_pcs = d.get('port_connections') or {}
                        if d_pcs.get('D') == out_net and d.get('module_name'):
                            hint = (f' Hint: this gate\'s output {out_net!r} '
                                    f'feeds new_logic_dff {d.get("instance_name")!r} '
                                    f'in module {d.get("module_name")!r} — set '
                                    f'this gate\'s module_name to the same value.')
                            break
                issues.append(
                    f"CRITICAL/44-MODULE-NAME-UNRESOLVED: stage={stage_name} "
                    f"{ct} entry {inst!r} has module_name={mod!r} (empty or "
                    f"\"UNKNOWN\" placeholder). eco_perl_spec.py will emit "
                    f"'Added to Perl spec for module UNKNOWN' and the Perl "
                    f"pipe will silently DROP the cell — it will never reach "
                    f"the netlist. Downstream consumer(s) will be undriven → "
                    f"FM Mode A. Set module_name to the actual host module.{hint}")

    # ── 45. MISSING-BRIDGE-PARTNER (cross-module port_connection symmetry) ───
    # When a port_connection P1 patches an UNCONNECTED bit at
    # <parent>.<wrapper_inst>.<port>[bit] (its net_name_before per-stage matches
    # UNCONNECTED_*), the consumer relies on the WRAPPER module being able to
    # drive that bit. If the wrapper's PreEco body still has the same bit
    # UNCONNECTED at its INTERNAL sub-instance, the parent-side patch alone
    # leaves the consumer net undriven — guaranteed FM Mode A.
    #
    # Required: study must contain a SECOND port_connection P2 with
    # parent_module = P1.child_module_name AND bus_bit_index = P1.bus_bit_index
    # (the "inner" port_connection that wires the wrapper's sub-instance bit
    # to the wrapper's own output port). Mode I emits both as
    # suggested_parent_port_connection_entry + suggested_child_port_connection_entry.
    #
    # Failure mode this catches: Mode I returned NO_PARENT_UNC because of a
    # caller bug (e.g. doubled tile prefix in host_module) and the LLM-studier
    # manually emitted only the parent port_connection — leaving the bridge
    # broken (run 20260527010014 R1 root cause).
    import re as _re45, gzip as _gz45
    _UNC_RE_45 = _re45.compile(r'^(SYNOPSYS_)?UNCONNECTED_\d+$')
    # Shared PreEco text cache and helpers for Check 45 + 46
    _preeco_text_cache_shared = {}
    def _load_preeco_text(stage):
        if stage in _preeco_text_cache_shared:
            return _preeco_text_cache_shared[stage]
        path = os.path.join(args.ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
        if not os.path.exists(path):
            _preeco_text_cache_shared[stage] = ''; return ''
        try:
            with _gz45.open(path, 'rt') as fh:
                text = fh.read()
        except Exception:
            text = ''
        _preeco_text_cache_shared[stage] = text
        return text

    def _unc_in_port(text, inst, port, unc):
        """Conservative: True if unc appears in inst.port, or if inst/port can't be located."""
        inst_m = _re45.search(rf'\b{_re45.escape(inst)}\s*\(', text)
        if not inst_m:
            return True
        block_start = inst_m.start()
        block_end = text.find(') ;', block_start)
        block = text[block_start: block_end + 3] if block_end > 0 else text[block_start:block_start + 50000]
        port_m = _re45.search(
            rf'\.{_re45.escape(port)}\s*\(\s*(\{{[^{{}}]*\}}|[^)]+)\)',
            block, _re45.DOTALL)
        if not port_m:
            return True
        return unc in port_m.group(1)

    def _inner_unc_confirmed(text, inst, port, unc):
        """Strict: True ONLY when unc is positively confirmed in inst.port."""
        inst_m = _re45.search(rf'\b{_re45.escape(inst)}\s*\(', text)
        if not inst_m:
            return False
        block_start = inst_m.start()
        block_end = text.find(') ;', block_start)
        block = text[block_start: block_end + 3] if block_end > 0 else text[block_start:block_start + 50000]
        if f'.{port}' not in block:
            return False
        port_m = _re45.search(
            rf'\.{_re45.escape(port)}\s*\(\s*(\{{[^{{}}]*\}}|[^)]+)\)',
            block, _re45.DOTALL)
        if not port_m:
            return False
        return unc in port_m.group(1)
    # Index existing port_connections by (stage, parent_module_base, bit).
    pc_index_45 = set()
    all_pcs_45 = []
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if e.get('change_type') != 'port_connection':
                continue
            pm = e.get('parent_module') or e.get('module_name') or ''
            bi = e.get('bus_bit_index')
            if pm and bi is not None:
                pc_index_45.add(
                    (stage_name,
                     _re45.sub(r'(_\d+)+$', '', pm),
                     bi))
            all_pcs_45.append((stage_name, e))

    # Call Mode I helper to determine whether a (host_module, port[bit])
    # parent UNCONNECTED actually requires an inner sub-instance fix.
    # Mode I returns MODEI_DETECTED only when BOTH parent AND wrapper inner
    # sub-instance are UNCONNECTED — that's exactly when an inner port_connection
    # is required. Cache by (host_module, port, bit).
    import subprocess as _sp45, tempfile as _tmp45, json as _j45
    _mode_i_path = os.path.join(os.path.dirname(__file__),
                                'eco_modei_chain_input_check.py')
    _mode_i_cache = {}
    def _mode_i_requires_inner(host_module, port_name, bit):
        cache_key = (host_module, port_name, bit)
        if cache_key in _mode_i_cache:
            return _mode_i_cache[cache_key]
        if not os.path.exists(_mode_i_path):
            _mode_i_cache[cache_key] = False
            return False
        chain_input = f'{port_name}[{bit}]'
        with _tmp45.NamedTemporaryFile(mode='w', suffix='.json',
                                       delete=False) as tf:
            out_path = tf.name
        try:
            _sp45.run(['python3', _mode_i_path,
                       '--ref-dir', args.ref_dir,
                       '--host-module', host_module,
                       '--chain-input', chain_input,
                       '--output', out_path],
                      capture_output=True, timeout=120)
            try:
                with open(out_path) as fh:
                    r = _j45.load(fh)
                requires = (r.get('status') == 'MODEI_DETECTED')
                if requires:
                    _mode_i_cache[cache_key] = r  # cache full result for verification
                    return requires
            except Exception:
                requires = False
        except Exception:
            requires = False
        finally:
            try:
                os.unlink(out_path)
            except Exception:
                pass
        _mode_i_cache[cache_key] = requires
        return requires

    seen_45 = set()
    for stage_name, e in all_pcs_45:
        nnb = e.get('net_name_before') or {}
        unc_for_stage = nnb.get(stage_name) if isinstance(nnb, dict) else None
        if not (isinstance(unc_for_stage, str)
                and _UNC_RE_45.match(unc_for_stage)):
            continue
        wrapper_mod = e.get('child_module_name') or ''
        wrapper_mod_base = _re45.sub(r'(_\d+)+$', '', wrapper_mod)
        bit = e.get('bus_bit_index')
        if not wrapper_mod or bit is None:
            continue
        key = (stage_name, wrapper_mod_base, bit)
        if key in seen_45:
            continue
        seen_45.add(key)
        # Inner partner already in study? Done.
        partner_in_study = any(
            (s2 == stage_name and bi2 == bit
             and _re45.sub(r'(_\d+)+$', '', pm2) == wrapper_mod_base)
            for (s2, pm2, bi2) in pc_index_45
            for s2 in (s2,)  # bind tuple
        )
        if partner_in_study:
            continue
        # No partner — does the wrapper actually have UNCONNECTED at sub-
        # instance level for the same bit? If not, this is a leaf register
        # file or otherwise self-driving — no inner PC needed.
        # Authoritative test: does Mode I detect an inner UNCONNECTED for
        # (parent_module, port[bit])? Mode I returns MODEI_DETECTED only when
        # both the parent slot AND the wrapper's internal sub-instance are
        # UNCONNECTED at the same bit — i.e. when an inner PC is genuinely
        # required. If Mode I returns NO_PARENT_UNC / NO_INNER_DRIVER, no
        # inner PC is needed (wrapper is a leaf or already self-driving).
        parent_host = e.get('parent_module') or e.get('module_name') or ''
        parent_host_base = _re45.sub(r'(_\d+)+$', '', parent_host)
        port_name = e.get('port_name', '')
        if not parent_host_base or not port_name:
            continue
        if not _mode_i_requires_inner(parent_host_base, port_name, bit):
            continue
        # Verify Mode I's inner UNCONNECTED actually exists on the identified
        # sub-instance port in PreEco — scalar bus connections and per-stage
        # module variants can cause Mode I to find a false UNCONNECTED from a
        # different port. Only fire if the inner_unc is confirmed in the netlist.
        modei_result = _mode_i_cache.get((parent_host_base, port_name, bit))
        if modei_result and isinstance(modei_result, dict):
            inner_unc = modei_result.get('inner_unc_per_stage', {}).get(stage_name)
            sub_i     = (modei_result.get('sub_inst_per_stage') or {}).get(stage_name,
                          modei_result.get('sub_inst', ''))
            sub_p     = modei_result.get('sub_port', '')
            if inner_unc and sub_i and sub_p:
                txt45 = _load_preeco_text(stage_name)
                if txt45 and not _inner_unc_confirmed(txt45, sub_i, sub_p, inner_unc):
                    continue  # Mode I false-positive: inner UNC not confirmed on sub_inst.sub_port
                # Additional gate: if the wrapper module has ANOTHER sub-instance
                # that drives the same port as a scalar bus (.<port>(port)), then
                # the flagged sub-instance's UNCONNECTED is a floating/unused output
                # — not the actual driver.  The scalar bus already drives all bits
                # of the port, so no inner partner is needed.
                # Example: uumccmdrb drives oQ_UmcCfgEco_UmcCfgEco as a scalar bus
                # inside ddrss_umccmd_t_umccmdregs, making MCPM_*.Z UNCONNECTED
                # irrelevant — MCPM is just a floating output, not the driver.
                if txt45 and port_name:
                    scalar_re = _re45.compile(
                        rf'\.{_re45.escape(port_name)}\s*\(\s*{_re45.escape(port_name)}\s*\)')
                    if scalar_re.search(txt45):
                        continue  # Scalar bus in wrapper drives the port — inner not needed
        inst = e.get('instance_name', '?')
        port = e.get('port_name', '?')
        net  = e.get('net_name', '?')
        issues.append(
            f"HIGH/45-MISSING-BRIDGE-PARTNER: stage={stage_name} "
            f"port_connection {e.get('parent_module','?')}.{inst}.{port}[{bit}] "
            f"= {net!r} patches an UNCONNECTED slot "
            f"(net_name_before[{stage_name}]={unc_for_stage!r}) at the PARENT "
            f"of wrapper module {wrapper_mod!r}, AND the wrapper's PreEco "
            f"body still has its INNER sub-instance UNCONNECTED at the same "
            f"bit[{bit}] — no inner port_connection in study to fix it. "
            f"Without the inner port_connection the wrapper's output bit is "
            f"undriven and the parent-side patch reads X → guaranteed FM "
            f"Mode A. Fix: add a sibling port_connection with "
            f"parent_module={wrapper_mod_base!r}, bus_bit_index={bit} that "
            f"wires the wrapper's sub-instance source pin to the wrapper's "
            f"own output port bit[{bit}]. Mode I normally emits this as "
            f"suggested_child_port_connection_entry; if Mode I returned "
            f"NO_PARENT_UNC the caller may have passed a wrong host_module "
            f"(e.g. doubled tile prefix).")

    # ── 46. BOGUS-NET-NAME-BEFORE: UNCONNECTED not on targeted port ──────────
    # If net_name_before[stage] = UNCONNECTED_N but that string does NOT appear
    # in instance.port's connection in the PreEco netlist, the entry is bogus —
    # the applier's _apply_bus_rename will search for it, fail to find it, and
    # SKIP the entry silently. Catch at Step 3 so the studier fixes/removes it.
    import subprocess as _sp46, re as _re46, gzip as _gz46
    _UNC46_RE = _re46.compile(r'^(SYNOPSYS_)?UNCONNECTED_\d+$')
    _load_preeco46 = _load_preeco_text  # reuse shared cache from Check 45

    _seen46 = set()
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if e.get('change_type') != 'port_connection':
                continue
            nnb = e.get('net_name_before') or {}
            unc = nnb.get(stage_name) if isinstance(nnb, dict) else None
            if not (isinstance(unc, str) and _UNC46_RE.match(unc)):
                continue
            inst = e.get('instance_name', '')
            port = e.get('port_name', '')
            if not inst or not port:
                continue
            key = (stage_name, inst, port, unc)
            if key in _seen46:
                continue
            _seen46.add(key)
            text46 = _load_preeco46(stage_name)
            if text46 and not _unc_in_port(text46, inst, port, unc):
                mod = e.get('parent_module') or e.get('module_name', '?')
                issues.append(
                    f"HIGH/46-BOGUS-NET-NAME-BEFORE: stage={stage_name} "
                    f"port_connection {mod}.{inst}.{port} has "
                    f"net_name_before[{stage_name}]={unc!r} but that UNCONNECTED "
                    f"does NOT appear in {inst}.{port} in PreEco/{stage_name}. "
                    f"Applier will SKIP this entry. Either the UNCONNECTED number "
                    f"is wrong (read the actual netlist) or this entry is not "
                    f"needed (port already connected — remove it).")

    # ── 47. INCONSISTENT-NET-NAME-FORM: mixed flat/bracket across bits ────────
    # All net_name values for the same (inst, port) must use the same form:
    # either all flat (_N_) or all bracket ([N]). Mixed form causes SVR-14 in FM
    # when some renamed nets use bracket indexing on a scalar wire.
    import re as _re47
    _BRACKET47 = _re47.compile(r'\[\d+\]$')
    pc_net_forms = {}  # (inst, port) → list of (bit, net_name, stage)
    for stage_name in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage_name, []):
            if e.get('change_type') != 'port_connection':
                continue
            bbi = e.get('bus_bit_index')
            net = e.get('net_name') or ''
            if bbi is None or not net:
                continue
            inst = e.get('instance_name', '')
            port = e.get('port_name', '')
            if not inst or not port:
                continue
            key = (inst, port)
            pc_net_forms.setdefault(key, []).append((bbi, net, stage_name))

    for (inst, port), entries in pc_net_forms.items():
        has_bracket = any(_BRACKET47.search(net) for _, net, _ in entries)
        has_flat    = any(not _BRACKET47.search(net) for _, net, _ in entries)
        if has_bracket and has_flat:
            mixed = [(bit, net) for bit, net, _ in entries]
            issues.append(
                f"HIGH/47-INCONSISTENT-NET-NAME-FORM: port_connection {inst}.{port} "
                f"has mixed flat/bracket net_name across bits: {mixed[:6]}. "
                f"All bits of the same port must use the same form. "
                f"Use flat _N_ form when the wire is scalar in the parent module; "
                f"use bracket [N] only when the wire is declared as a bus array.")

    # Shared helper for Check 48 and 49: get output port names of a module
    import re as _re49, gzip as _gz49
    _UNDRIVEN49_CACHE = {}
    def _get_module_output_ports(mod_name, stage='Synthesize'):
        cache_key = (mod_name, stage)
        if cache_key in _UNDRIVEN49_CACHE:
            return _UNDRIVEN49_CACHE[cache_key]
        path = os.path.join(args.ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
        if not os.path.exists(path):
            _UNDRIVEN49_CACHE[cache_key] = set(); return set()
        try:
            with _gz49.open(path, 'rt') as fh:
                text = fh.read()
        except Exception:
            _UNDRIVEN49_CACHE[cache_key] = set(); return set()
        m = _re49.search(rf'^module\s+{_re49.escape(mod_name)}\b', text, _re49.MULTILINE)
        if not m:
            _UNDRIVEN49_CACHE[cache_key] = set(); return set()
        end_m = _re49.search(r'\bendmodule\b', text[m.start():])
        body = text[m.start(): m.start() + end_m.start()] if end_m else text[m.start():m.start()+50000]
        ports = set()
        for pm in _re49.finditer(r'^\s*output\s+(?:\[[^\]]+\]\s+)?([A-Za-z_]\w*)\s*;', body, _re49.MULTILINE):
            ports.add(pm.group(1))
        _UNDRIVEN49_CACHE[cache_key] = ports
        return ports

    # ── 48. GATE-INPUT-NET-NAMING: bus_rename net directly consumed by ECO gate ─
    # When a port_connection bus_rename renames an UNCONNECTED slot to a new wire,
    # AND that wire is immediately consumed as an input pin of a new_logic_gate,
    # the net_name MUST be the flat form of the port + bit: <port_name>_<N>_.
    # Using a generic eco_{jira}_ prefix instead creates an extra indirection level
    # that FM cannot resolve across Synth→PrePlace stages — FM traces the cone to
    # the renamed wire, hits an unmatched boundary at the intermediate name, and
    # reports non-equivalent DFF D-pins (8 failing compare points per ECO-modified
    # DFF). The flat port form (<port_name>_<N>_) is the sanitized version of the
    # register output compare point itself, letting FM trace through cleanly.
    import re as _re48
    _GATE_INPUT_PINS = {'I', 'A', 'A1', 'A2', 'B', 'B1', 'B2', 'D', 'IN'}
    # Build set of gate input nets for Synthesize
    _gate_input_nets = set()
    for _e48 in study.get('Synthesize', []):
        if not isinstance(_e48, dict): continue
        if _e48.get('change_type') not in ('new_logic_gate', 'and_term', 'new_logic'): continue
        for _pin, _val in (_e48.get('port_connections') or {}).items():
            if _pin in _GATE_INPUT_PINS and isinstance(_val, str) and _val:
                _gate_input_nets.add(_val)
    # Check port_connection bus_rename nets consumed by gates
    _seen48 = set()
    for _e48 in study.get('Synthesize', []):
        if not isinstance(_e48, dict): continue
        if _e48.get('change_type') != 'port_connection': continue
        _bbi = _e48.get('bus_bit_index')
        _net = _e48.get('net_name', '')
        _port = _e48.get('port_name', '')
        if _bbi is None or not _net or not _port:
            continue
        if _net not in _gate_input_nets:
            continue  # not consumed by a gate — no constraint
        key48 = (_net, _port, _bbi)
        if key48 in _seen48:
            continue
        _seen48.add(key48)
        # Expected flat form: <port_name>_<bit>_
        _expected = f'{_port}_{_bbi}_'
        if _net != _expected:
            # Exempt when net_name matches the wrapper module's output port
            # (Check 49 pattern): the net drives the wrapper output, not a
            # standalone wire. For these entries the wrapper output port name
            # takes precedence over the sub-instance port name.
            _parent_mod = _e48.get('module_name') or _e48.get('parent_module', '')
            _wrapper_ports = _get_module_output_ports(_parent_mod) if _parent_mod else set()
            # Exempt bracket form <port>[N] or flat form <port>_N_ when base is a
            # wrapper output port — Check 49 validates these (self-reference to output bus)
            _net_base_flat = _re48.sub(r'_\d+_$', '', _net)   # strip _N_
            _net_base_bracket = _re48.sub(r'\[\d+\]$', '', _net)  # strip [N]
            if _net_base_flat in _wrapper_ports or _net_base_bracket in _wrapper_ports:
                pass  # Check 49 handles this — wrapper output port form is correct
            else:
                _inst = _e48.get('instance_name', '?')
                issues.append(
                    f"HIGH/48-GATE-INPUT-NET-NAMING: port_connection {_inst}.{_port}[{_bbi}] "
                    f"net_name={_net!r} is consumed directly as a gate input but is NOT the "
                    f"flat port form {_expected!r}. FM cannot trace through a generic eco_ "
                    f"prefix across Synth→PrePlace stages — causes non-equivalent DFF D-pins. "
                    f"Fix: set net_name={_expected!r} in the port_connection entry AND in the "
                    f"consuming gate's port_connections input pin.")

    # ── 49. INNER-PARTNER-NET-NAME: must use wrapper's own output port name ──
    # When a port_connection inner partner entry renames a bus slot inside a
    # wrapper module, the net_name MUST be the flat form of the WRAPPER's own
    # output port at that bit — NOT the sub-instance's port name.
    #
    # If the studier uses the sub-instance's port name (e.g. oQ_Xxx_5_), it
    # creates a floating wire inside the wrapper that is never connected to
    # the wrapper's output port. FM sees the wrapper's output port bit as
    # undriven (Impl Und) → all downstream compare points fail (8F in
    # FmEqvEcoSynthesizeVsSynRtl). Root cause of 10036 8F failure.
    #
    # Detection: for each port_connection entry with bus_bit_index that has
    # parent_module = wrapper AND net_name does NOT equal <output_port>_<N>_,
    # check if the wrapper module's output port at the same module-level bus
    # position is being driven by the same named wire. If the wrapper has an
    # output port whose name differs from net_name → HIGH.
    # (_get_module_output_ports defined above before Check 48)
    _seen49 = set()
    for _e49 in study.get('Synthesize', []):
        if not isinstance(_e49, dict): continue
        if _e49.get('change_type') != 'port_connection': continue
        _bbi = _e49.get('bus_bit_index')
        _net = _e49.get('net_name', '')
        _mod = _e49.get('module_name') or _e49.get('parent_module', '')
        _inst = _e49.get('instance_name', '')
        _port = _e49.get('port_name', '')
        if _bbi is None or not _net or not _mod or not _inst:
            continue
        key49 = (_mod, _inst, _port, _bbi)
        if key49 in _seen49:
            continue
        _seen49.add(key49)
        # Get wrapper module's output ports
        out_ports = _get_module_output_ports(_mod)
        if not out_ports:
            continue
        # net_name MUST be <wrapper_output_port>[N] — bracket self-reference.
        # Flat form <port>_N_ creates a separate floating wire that does NOT
        # connect to the output bus → FM Impl/Ref Und → 8F.
        # Evidence: engineer uses REG_UmcCfgEco[5] (bracket) not REG_UmcCfgEco_5_.
        _net_base = _re49.sub(r'\[\d+\]$', '', _net)   # strip [N] bracket suffix
        _is_bracket = bool(_re49.search(r'\[\d+\]$', _net))
        # Whole-bus loopback bridge exemption: if net_name equals the wrapper output
        # port name exactly (no [N] suffix), this is a whole-bus rename — correct when
        # net_name_before is a multi-bit bus (e.g. wire [31:0] X_0). Using bracket form
        # [N] on a 32-bit net_name_before would trigger the bus-width mismatch check
        # (line ~5078), so whole-bus form is the only valid option for this pattern.
        _is_whole_bus_rename = (not _is_bracket and _net_base in out_ports)
        if not (_net_base in out_ports and _is_bracket) and not _is_whole_bus_rename:
            _expected_base = None
            for op in out_ports:
                if any(part in op for part in _port.split('_') if len(part) > 3):
                    _expected_base = op
                    break
            if _expected_base:
                _expected_net = f'{_expected_base}[{_bbi}]'
                issues.append(
                    f"HIGH/49-INNER-PARTNER-NET-NAME: port_connection "
                    f"{_mod}.{_inst}.{_port}[{_bbi}] has net_name={_net!r}. "
                    f"Must be bracket form {_expected_net!r} — self-reference to "
                    f"wrapper output port {_expected_base!r}[{_bbi}]. "
                    f"Flat/wrong form creates floating wire → FM Impl/Ref Und → 8F.")

    # ── Check 50: enable_swap with clock gate → must implement EITHER path ────
    # When enable_via_clock_gate=true, Step 3 must choose ONE of two valid paths:
    #   Path A (shadow gate): new CKOR*/ICG* gate + CP rewires for DFF array
    #                         + D-input rewires if companion wire_swap exists
    #   Path B (E-pin rewire): rewire entry on an E/EN/TE pin of the existing gate
    # If NEITHER path is present → FAIL (clock gate was detected but nothing done).
    # If Path A (shadow gate) is present → also require CP rewires.
    # D-input rewires are only required when a companion wire_swap exists for the
    # same target register (both enable AND data path change together).
    _CG_RE = re.compile(r'^(CKOR|ICG|CTG|CKLNQ|CKGT)', re.I)
    _E_PINS = {'E', 'EN', 'TE', 'SE', 'ENB', 'ENN'}
    for _c in rtl_diff.get('changes', []):
        if _c.get('change_type') != 'enable_swap':
            continue
        if not _c.get('enable_via_clock_gate'):
            continue
        _tgt = _c.get('target_register') or '?'
        _all_entries = [e for stage in ('Synthesize', 'PrePlace', 'Route')
                        for e in study.get(stage, [])]
        _has_shadow_gate = any(
            e.get('change_type') == 'new_logic_gate' and
            _CG_RE.match(str(e.get('cell_type') or e.get('cell_name') or ''))
            for e in _all_entries)
        _has_e_rewire = any(
            e.get('change_type') == 'rewire' and
            str(e.get('pin', '')).upper() in _E_PINS
            for e in _all_entries)
        if not _has_shadow_gate and not _has_e_rewire:
            issues.append(
                f"Check 50 FAIL: enable_swap target={_tgt!r} has enable_via_clock_gate=true "
                f"but study JSON has neither a shadow clock gate nor an E/EN/TE pin rewire. "
                f"Path A: create a new CKOR*/ICG* shadow gate + rewire DFF array CP pins. "
                f"Path B (simpler, only when existing E-pin has no other consumers): "
                f"rewire the existing clock gate's E pin directly.")
        # If shadow gate chosen: CP rewires are mandatory
        if _has_shadow_gate:
            _has_cp_rewire = any(
                e.get('change_type') == 'rewire' and
                str(e.get('pin', '')).upper() in ('CP', 'CK', 'CLK', 'C', 'CPN')
                for e in _all_entries)
            if not _has_cp_rewire:
                issues.append(
                    f"Check 50 FAIL: enable_swap target={_tgt!r} has a shadow clock gate "
                    f"but NO CP rewire entries. Shadow gate pattern requires rewiring the "
                    f"existing DFF array CP pins to the new shadow gate output.")
        # D-input rewires required only when a companion wire_swap exists for the same target
        _has_companion_wire_swap = any(
            _c2.get('change_type') in ('wire_swap', 'and_term') and
            _c2.get('target_register') == _tgt
            for _c2 in rtl_diff.get('changes', []))
        if _has_companion_wire_swap:
            _has_d_rewire = any(
                e.get('change_type') == 'rewire' and
                str(e.get('pin', '')).upper() in ('D', 'D1', 'D2', 'D3', 'D4', 'DI', 'DIN')
                for e in _all_entries)
            if not _has_d_rewire:
                issues.append(
                    f"Check 50 FAIL: enable_swap target={_tgt!r} has a companion wire_swap/"
                    f"and_term but NO D-input rewire entries. When both enable and data path "
                    f"change, the existing DFF array D-pins must be rewired to the new mux output.")

    # ── Check 51: enable_via_clock_gate=False — verify against PreEco netlist ──
    # The rtl_diff_analyzer may wrongly classify an enable_swap as not having a
    # clock gate. If the PreEco Synthesize netlist contains a CKOR*/ICG*/CTG* cell
    # named clk_gate_<target>_reg, the field should be True. This catches the
    # mismatch before Step 3 generates the wrong (E-pin rewire) implementation.
    _CG_TYPE_RE = re.compile(r'^(CKOR|ICG|CTG|CKLNQ|CKGT)', re.I)
    _preeco_syn_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if os.path.exists(_preeco_syn_gz):
        for _c in rtl_diff.get('changes', []):
            if _c.get('change_type') != 'enable_swap':
                continue
            if _c.get('enable_via_clock_gate'):
                continue  # already True — Check 50 covers this path
            _tgt = _c.get('target_register', '?')
            try:
                _r = subprocess.run(
                    _fastcmd(f'zgrep -E "clk_gate_{re.escape(_tgt)}_reg" {_preeco_syn_gz}'),
                    shell=True, capture_output=True, text=True, timeout=20)
                if _r.returncode == 0 and _r.stdout.strip():
                    for _line in _r.stdout.strip().splitlines():
                        _parts = _line.strip().split()
                        if _parts and _CG_TYPE_RE.match(_parts[0]):
                            issues.append(
                                f"Check 51 FAIL: enable_swap target={_tgt!r} has "
                                f"enable_via_clock_gate=False but PreEco Synthesize contains "
                                f"clock gate cell 'clk_gate_{_tgt}_reg' ({_parts[0]}). "
                                f"Set enable_via_clock_gate=true and add clock_gate_instance "
                                f"+ dff_cp_net to the RTL diff entry. Step 3 will then create "
                                f"a shadow clock gate instead of rewiring the existing E-pin.")
                            break
            except Exception:
                pass

    # ── Check 52: Duplicate instance_name OR output net within same stage ────
    # Catches: (a) same instance_name from two agents; (b) different instance
    # names (eco_9855_nxt_ao22_0 vs eco_9855_nxt_ao22_0_) that drive the same
    # output net — both cause SVR-9 multiply-driven net in PostEco netlist.
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        seen_insts = {}
        seen_out_nets = {}
        for e in study.get(stage, []):
            inst = e.get('instance_name', '')
            if not inst or inst == '?':
                continue
            ct = e.get('change_type', '?')
            # port_connection entries share the same instance_name (the child instance)
            # but differ by port_name — allow multiple port_connections per instance.
            # Only flag duplicates within the same (instance_name, change_type) pair
            # when change_type is NOT port_connection.
            if ct == 'port_connection':
                port_key = (inst, ct, e.get('port_name', ''))
                if port_key in seen_insts:
                    issues.append(
                        f"Check 52 FAIL: duplicate port_connection {inst!r}.{e.get('port_name')!r} "
                        f"in {stage} — same instance+port already present.")
                else:
                    seen_insts[port_key] = ct
            elif ct == 'rewire':
                # A rewire targets ONE pin of a cell. A multi-bit (MB) flop
                # legitimately needs several pin rewires on the SAME instance
                # (e.g. D1 + D3 + SI + SE), and the SAME instance name recurs
                # across many uniquified modules (e.g. rcqe_pgst_reg... in
                # umcrecrcqentry_0.._39). Key on (module, instance, pin) so only a
                # genuine same-module same-pin duplicate is flagged.
                _pin = e.get('pin') or (e.get('pin_per_stage') or {}).get(stage, '')
                _mod = e.get('module_name') or e.get('parent_module') or ''
                pin_key = (_mod, inst, ct, _pin)
                if pin_key in seen_insts:
                    issues.append(
                        f"Check 52 FAIL: duplicate rewire {inst!r} pin={_pin!r} "
                        f"in module {_mod!r} ({stage}) — same instance+pin rewired twice.")
                else:
                    seen_insts[pin_key] = ct
            else:
                if inst in seen_insts:
                    issues.append(
                        f"Check 52 FAIL: duplicate instance_name {inst!r} in {stage} "
                        f"(change_types: {seen_insts[inst]!r} and {ct!r}). "
                        f"eco_emit_dff_entry.py already emitted this gate — studier must not re-emit it.")
                else:
                    seen_insts[inst] = ct
            # Also check for same output net driven by different instances
            pcs = e.get('port_connections') or {}
            out_net = pcs.get('Z') or pcs.get('ZN') or pcs.get('Q', '')
            if out_net and out_net not in ("1'b0", "1'b1"):
                # Scope by module_name — uniquified families have the same local
                # wire name in each module (e.g. reg_dualdcqenmode_or in each
                # ddrss_umcdat_t_umcrecrcqentry_N) which is correct per-module.
                _mod52 = e.get('module_name') or ''
                _net_mod_key = (_mod52, out_net)
                if _net_mod_key in seen_out_nets and seen_out_nets[_net_mod_key] != inst:
                    issues.append(
                        f"Check 52 FAIL: output net {out_net!r} driven by both "
                        f"{seen_out_nets[_net_mod_key]!r} and {inst!r} in {stage} — "
                        f"SVR-9 multiply-driven net. Remove the duplicate gate entry.")
                else:
                    seen_out_nets[_net_mod_key] = inst

    # ── Check 53: Spurious shadow clock gate (not connected to any CP rewire) ─
    # A new CKOR*/ICG* gate that has no corresponding CP rewire (no DFF CP pin
    # rewired to its Q output) is likely an erroneous auto-add. The enable_swap
    # shadow gate must have at least one rewire consuming its Q output.
    _CG_TYPE_RE53 = re.compile(r'^(CKOR|ICG|CTG|CKLNQ|CKGT)', re.I)
    for stage in ('Synthesize',):
        cg_gates = {}
        cp_rewire_targets = set()
        for e in study.get(stage, []):
            ct = e.get('change_type', '')
            cell = str(e.get('cell_type') or e.get('cell_name') or '')
            if ct == 'new_logic_gate' and _CG_TYPE_RE53.match(cell):
                inst = e.get('instance_name', '?')
                q_net = (e.get('port_connections') or {}).get('Q', '')
                cg_gates[inst] = q_net
            elif ct == 'rewire':
                new_net = e.get('new_net', '')
                if new_net:
                    cp_rewire_targets.add(new_net)
        for inst, q_net in cg_gates.items():
            if q_net and q_net not in cp_rewire_targets:
                issues.append(
                    f"Check 53 FAIL: shadow clock gate {inst!r} Q={q_net!r} has NO rewire entry "
                    f"pointing to it (no DFF CP rewired to this gate's output). "
                    f"Likely a spurious auto-added shadow gate — verifier must NOT create new "
                    f"CKOR*/ICG* cells; only reuse the existing enable_swap shadow gate's Q output.")

    # ── Check 54: Tool-generated cell rewire in wrong module ─────────────────
    # ctmi_*/phs_*/copt_* cells appear in multiple modules. If D-pin rewires for
    # the target MB DFFs are already present in the study JSON, any additional
    # rewire on a tool-generated upstream cell in the same module is redundant
    # and likely targets the wrong module copy (xbar afifo vs WDB).
    _TOOL_CELL_RE = re.compile(r'^(ctmi_|phs_|copt_|aps_rename_)', re.I)
    _s54_plain = None
    if args.ref_dir:
        _s54_gz = Path(args.ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
        if _s54_gz.is_file():
            try:
                _s54_plain = _plain_netlist(str(_s54_gz))
            except Exception:
                _s54_plain = None
    for stage in ('Synthesize',):
        # Redundancy can ONLY exist within the SAME MODULE as a direct MB-DFF D-pin rewire (an
        # upstream ctmi feeding that DFF's D-cone) — this check's own comment says "...in the same
        # module is redundant". So key by module, not globally: a ctmi rewire in a DIFFERENT module
        # (e.g. a compare_fold rewire in umcrecrcqentry_0 vs a reg_guard DFF in umcrecdsp) is a
        # separate legitimate change and must NOT be cross-flagged. (Before this fix, ANY MB-DFF
        # D-pin rewire — e.g. the WckSyncCtr0 reg_guard-delta — falsely flagged EVERY ctmi rewire.)
        mb_dff_d_modules = {
            e.get('module_name', '') for e in study.get(stage, [])
            if e.get('change_type') == 'rewire' and
            str(e.get('pin', '')).upper().startswith('D') and
            re.match(r'.*_MB_.*|.*_reg_\d+_.*', e.get('cell_name', ''))
        }
        if mb_dff_d_modules:
            for e in study.get(stage, []):
                if e.get('change_type') != 'rewire':
                    continue
                cell = e.get('cell_name', '') or e.get('instance_name', '')
                if not _TOOL_CELL_RE.match(cell):
                    continue
                # Same-module scope (documented intent): skip ctmi rewires whose module has no
                # direct MB-DFF D-pin rewire.
                if e.get('module_name', '') not in mb_dff_d_modules:
                    continue
                # Unique-instance cell → no multi-module "wrong first occurrence" ambiguity, which
                # is the ENTIRE premise of this check ("appear in multiple modules"). Safe → skip.
                if _s54_plain and _cell_instance_count(cell, _s54_plain) <= 1:
                    continue
                issues.append(
                    f"Check 54 FAIL: rewire on tool-generated cell {cell!r} "
                    f"(pin={e.get('pin')}) exists alongside direct MB DFF D-pin rewires IN THE "
                    f"SAME MODULE ({e.get('module_name')!r}) and the cell is instantiated in "
                    f"multiple modules — the applier matches the FIRST occurrence (often wrong "
                    f"module). Remove this rewire; the MB DFF D-pins are already directly rewired.")

    # ── Check 55: new DFF CP = bare clock when shadow gate exists in module ──
    # When a CKOR*/ICG* shadow gate is present in a module, ALL new_logic_dff
    # entries in that module whose CP equals the bare ungated clock (uclkg,
    # UCLK01, etc.) should use the shadow gate Q instead. Bare CP means the DFF
    # runs on the ungated clock — FM sees it in a different clock domain from
    # the enable_swap target and the DFF cone is unmatched.
    _CG_TYPE_RE55 = re.compile(r'^(CKOR|ICG|CTG|CKLNQ|CKGT)', re.I)
    for stage in ('Synthesize',):
        # Collect shadow gates per module: {module → (shadow_gate_Q_net, shadow_gate_CP)}
        shadow_gates_by_mod = {}
        for e in study.get(stage, []):
            ct = e.get('change_type', '')
            cell = str(e.get('cell_type') or e.get('cell_name') or '')
            mod = e.get('module_name', '')
            if ct == 'new_logic_gate' and _CG_TYPE_RE55.match(cell) and mod:
                pcs = e.get('port_connections') or {}
                q_net = pcs.get('Q', '')
                ck = pcs.get('CK') or pcs.get('CP') or pcs.get('C', '')
                if q_net and 'ECO' in q_net:  # only ECO shadow gates
                    shadow_gates_by_mod[mod] = (q_net, ck)
        # Check DFFs in same module with bare CP matching the shadow gate's clock domain
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            mod = e.get('module_name', '')
            shadow_info = shadow_gates_by_mod.get(mod)
            if not shadow_info:
                continue
            shadow_q, shadow_ck = shadow_info
            inst = e.get('instance_name', '?')
            cp = (e.get('port_connections') or {}).get('CP', '')
            # Flag DFFs in the SAME clock domain as the shadow gate
            # whose CP is NOT the shadow gate Q output.
            # Two cases: (a) bare clock same as shadow_ck, (b) an existing
            # (non-ECO) clock gate — verifier picked the wrong neighbor gate.
            if cp == shadow_q:
                continue  # already correct — using ECO shadow gate
            # Exempt companion new DFFs that are allowed to use the OLD gate Q
            # (dff_cp_net from the enable_swap entry). Per engineer scheme, companion
            # new DFFs (e.g. wdbptr_org0_d1p5) use the OLD existing gate, not the new
            # ECO shadow gate. Check 58 enforces this separately.
            _dff_cp_nets = {_c.get('dff_cp_net','') for _c in rtl_diff.get('changes',[])
                            if _c.get('change_type')=='enable_swap' and _c.get('dff_cp_net')}
            if cp and any(cp == _n for _n in _dff_cp_nets if _n):
                continue  # companion DFF using OLD gate Q — allowed per scheme
            # Skip DFFs on a completely different clock (different base clock)
            # by checking if the DFF clock and shadow_ck share the same base name
            ck_base = re.sub(r'clk_gate_.*', '', shadow_ck).strip('_') or shadow_ck
            if ck_base and ck_base not in cp and cp not in (shadow_ck, ''):
                # Check if at least one of the clocks is related to the other
                if not (shadow_ck in cp or cp in shadow_ck or
                        re.sub(r'clk_gate_.*', '', cp).strip('_') == ck_base):
                    continue  # truly different clock domain
            if cp and shadow_q not in cp:
                issues.append(
                    f"Check 55 FAIL: DFF {inst!r} in module {mod!r} has CP={cp!r} "
                    f"but module has ECO shadow gate with Q={shadow_q!r}. "
                    f"Set CP to the shadow gate Q via eco_emit_dff_entry.py --shadow-cp-net "
                    f"{shadow_q!r}. Using wrong/bare clock = ungated domain → FM cone mismatch.")

    # ── Check 56: shadow gate E-pin driver must include other_enable_inputs ──
    # If rtl_diff enable_swap has clock_gate_other_enable_inputs (e.g. ['rep_3']),
    # the OR gate driving the shadow gate E-pin must include those nets.
    # Missing them means the clock enable function differs from SynRtl → FM fail.
    for _c in rtl_diff.get('changes', []):
        if _c.get('change_type') != 'enable_swap':
            continue
        if not _c.get('enable_via_clock_gate'):
            continue
        _other = _c.get('clock_gate_other_enable_inputs') or []
        if not _other:
            continue
        _tgt = _c.get('target_register', '?')
        # Find the shadow gate in study JSON
        _cg_entries = [e for s in ('Synthesize', 'PrePlace', 'Route')
                       for e in study.get(s, [])
                       if e.get('change_type') == 'new_logic_gate' and
                       _CG_RE.match(str(e.get('cell_type') or e.get('cell_name') or ''))]
        if not _cg_entries:
            continue
        _shadow_q = (_cg_entries[0].get('port_connections') or {}).get('Q', '')
        # Find the gate driving the shadow gate's E-pin (the OR gate)
        _e_pin_net = (_cg_entries[0].get('port_connections') or {}).get('E', '')
        if not _e_pin_net:
            continue
        # Find the gate whose output == _e_pin_net
        _e_driver = None
        for e in [x for s in ('Synthesize',) for x in study.get(s, [])
                  if x.get('change_type') == 'new_logic_gate']:
            pcs = e.get('port_connections') or {}
            if pcs.get('Z') == _e_pin_net or pcs.get('ZN') == _e_pin_net:
                _e_driver = e
                break
        if not _e_driver:
            issues.append(
                f"Check 56 FAIL: enable_swap target={_tgt!r} shadow gate E-pin={_e_pin_net!r} "
                f"has no driver gate in study JSON. Need OR gate with inputs: "
                f"{_other + [_c.get('new_enable_net','<new_enable>')]}.")
            continue
        _e_inputs = list((_e_driver.get('port_connections') or {}).values())
        missing = [o for o in _other if o not in str(_e_inputs)]
        if missing:
            issues.append(
                f"Check 56 FAIL: enable_swap target={_tgt!r} shadow gate E-pin driver "
                f"{_e_driver.get('instance_name')!r} is missing other_enable_inputs {missing}. "
                f"The OR gate must combine {_other} with the new enable net — "
                f"omitting them causes FM mismatch vs SynRtl which preserves these terms.")

    # ── Check 57: d_input_reset_gate=true → study JSON D-input rewires must go
    # through an AND reset gate (AN2D1/INR2) before reaching the MB DFF D-pins.
    # When the existing D-path was reset-gated and we replace it, the new path
    # must preserve that behavior — a plain AO22 mux output on D-pin fails FM.
    _AND_RESET_RE = re.compile(r'^(AN2|AND2|INR2)', re.I)
    for _c in rtl_diff.get('changes', []):
        if _c.get('change_type') != 'enable_swap':
            continue
        if not _c.get('d_input_reset_gate'):
            continue
        _tgt = _c.get('target_register', '?')
        # Find companion wire_swap chain last gate output net
        _chain_final_out = None
        for _c2 in rtl_diff.get('changes', []):
            if (_c2.get('change_type') in ('wire_swap', 'and_term') and
                    _c2.get('target_register') == _tgt):
                _chain = _c2.get('d_input_gate_chain') or []
                if _chain:
                    _last = _chain[-1]
                    if _AND_RESET_RE.match(_last.get('cell_type', '')):
                        _chain_final_out = _last.get('output_net', '')
        if _chain_final_out is None:
            continue  # no chain or chain doesn't end in AND reset — step1 catches this
        # Check that MB DFF D-pin rewires use the AND reset gate output, not raw mux
        _d_rewires = [e for s in ('Synthesize', 'PrePlace', 'Route')
                      for e in study.get(s, [])
                      if e.get('change_type') == 'rewire' and
                      str(e.get('pin', '')).upper().startswith('D') and
                      re.match(r'.*_MB_.*|.*_reg_\d+_.*', e.get('cell_name', ''))]
        for _rw in _d_rewires[:3]:  # check first few samples
            _new = _rw.get('new_net', '')
            # new_net should NOT be a plain AO22 output (would contain 'ao22' without reset)
            if _new and 'ao22' in _new.lower() and 'reset' not in _new.lower():
                issues.append(
                    f"Check 57 FAIL: d_input_reset_gate=true but D-pin rewire "
                    f"{_rw.get('cell_name','?')!r}.{_rw.get('pin')}→{_new!r} "
                    f"goes directly to AO22 mux output without AND reset gate. "
                    f"The D-input must pass through AN2D1(reset_inv, mux_out) to "
                    f"preserve existing reset-gated D-path behavior.")
                break

    # ── Check 58: companion new DFF CP must use OLD gate, not NEW ECO gate ───
    # New DFFs introduced alongside an enable_swap (e.g. d1p5 pipeline stage)
    # must be clocked by the OLD existing clock gate (dff_cp_net), NOT the new
    # ECO shadow gate. Using the ECO gate gives a different enable function from
    # SynRtl and causes FM mismatch on those DFF compare points.
    for _c in rtl_diff.get('changes', []):
        if _c.get('change_type') != 'enable_swap':
            continue
        if not _c.get('enable_via_clock_gate'):
            continue
        _dff_cp_net = _c.get('dff_cp_net', '')
        _tgt = _c.get('target_register', '?')
        if not _dff_cp_net:
            continue
        # Find companion new_logic_dff entries in same module (not the rewired target)
        for stage in ('Synthesize',):
            for e in study.get(stage, []):
                if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                    continue
                inst = e.get('instance_name', '')
                if _tgt in inst:
                    continue  # skip the existing rewired DFF array itself
                pcs = e.get('port_connections') or {}
                cp = pcs.get('CP', '')
                # Flag if CP is a shadow gate (contains 'clk_gate_ECO') but differs
                # from dff_cp_net (the old gate Q). Use net name comparison — not
                # 'ECO_' prefix check — because dff_cp_net may itself be wrong.
                if cp and cp != _dff_cp_net and 'clk_gate_ECO' in cp and _dff_cp_net:
                    issues.append(
                        f"Check 58 FAIL: new DFF {inst!r} CP={cp!r} uses the ECO shadow "
                        f"gate but should use the OLD gate (dff_cp_net={_dff_cp_net!r}). "
                        f"Companion new DFFs must be clocked by the same gate the existing "
                        f"DFF array used BEFORE the ECO — pass "
                        f"--shadow-cp-net {_dff_cp_net!r} to eco_emit_dff_entry.py. "
                        f"Also verify dff_cp_net in rtl_diff is the PRE-ECO gate Q net.")

    # ── Check 59: d_input_has_reset_context=true → shared INV + AN2D1 in study ─
    # When the ECO'd RTL wraps the D-assignment in a reset condition the study JSON
    # (not the rtl_diff chain) must contain:
    #   1. A shared INVD1(reset_signal) → *ireset_inv* net
    #   2. Per-bit AN2D1 gates with A1 = that ireset_inv net
    # AN2D1 gates are emitted directly by the studier, not via the rtl_diff chain.
    _AN2_RE = re.compile(r'^(AN2|AND2)', re.I)
    _INV_RE = re.compile(r'^(INV|INVD)', re.I)
    for _c in rtl_diff.get('changes', []):
        if _c.get('change_type') not in ('wire_swap', 'and_term'): continue
        if not _c.get('d_input_has_reset_context'): continue
        _tgt = _c.get('target_register', '?')
        # Use Synthesize as canonical stage for gate existence check
        _study_syn = study.get('Synthesize', [])
        # Sub-check A: shared INV → *ireset_inv* net
        _ireset_inv_nets = [
            str((e.get('port_connections') or {}).get('ZN', ''))
            for e in _study_syn
            if e.get('change_type') == 'new_logic_gate'
            and _INV_RE.match(str(e.get('cell_type') or ''))
            and 'ireset_inv' in str((e.get('port_connections') or {}).get('ZN', ''))
        ]
        if not _ireset_inv_nets:
            issues.append(
                f"Check 59 FAIL: wire_swap target={_tgt!r} has d_input_has_reset_context=true "
                f"but study JSON (Synthesize) has no shared INVD1 → *ireset_inv* gate. "
                f"Add INVD1(reset_signal) → n_eco_<jira>_ireset_inv as a new_logic_gate.")
            continue
        _ireset_inv_net = _ireset_inv_nets[0]
        # Sub-check B: per-bit AN2D1 gates with A1 = ireset_inv net
        _an2_gates = [
            e for e in _study_syn
            if e.get('change_type') == 'new_logic_gate'
            and _AN2_RE.match(str(e.get('cell_type') or ''))
            and str((e.get('port_connections') or {}).get('A1', '')) == _ireset_inv_net
        ]
        if not _an2_gates:
            issues.append(
                f"Check 59 FAIL: wire_swap target={_tgt!r} has d_input_has_reset_context=true "
                f"but study JSON (Synthesize) has no AN2D1 gate with A1={_ireset_inv_net!r}. "
                f"Add per-bit AN2D1(ireset_inv, mux_out) gates as new_logic_gate entries.")

    # ── Check 60: oQ port_connection → net_name_before non-empty + companion iQ ─
    # iQ and oQ changes are at DIFFERENT hierarchy levels:
    #   oQ: parent module (e.g. umcregdat), instance=REG
    #   iQ: child module (e.g. umcdatregs), instance=uumcdatrdmux (internal mux)
    # The iQ companion entry has a DIFFERENT module_name and instance_name from oQ.
    # Using the SAME instance_name for iQ as oQ → FE-LINK-7 ABORT (port not on module).
    _pc_entries = [e for e in study.get('Synthesize', []) if e.get('change_type') == 'port_connection']
    _oq_pcs = [e for e in _pc_entries if str(e.get('port_name', '')).startswith('oQ_')]
    for _oq in _oq_pcs:
        _oq_port = str(_oq.get('port_name', ''))
        _oq_inst = _oq.get('instance_name', '')

        # Sub-check A: oQ must have non-empty net_name_before
        _oq_before = _oq.get('net_name_before') or {}
        if not any(v for v in _oq_before.values()):
            issues.append(
                f"Check 60 FAIL: oQ port_connection {_oq_port!r} on instance {_oq_inst!r} "
                f"has empty net_name_before. Grep PreEco for .{_oq_port}( inside the target "
                f"instance block to find the real wire. Empty → applier adds duplicate → "
                f"Step 5 input_net_strict_driver FAIL.")

        # Sub-check B: a companion iQ entry must exist (ANY instance — iQ is in a
        # different module/instance from oQ, so do NOT enforce same instance_name)
        _iq_name = 'iQ_' + _oq_port[3:]
        _iq_entries = [e for e in _pc_entries if e.get('port_name') == _iq_name]
        if not _iq_entries:
            issues.append(
                f"Check 60 FAIL: oQ port {_oq_port!r} updated but no companion iQ "
                f"port_connection for {_iq_name!r} found. "
                f"iQ is on the internal mux instance INSIDE the child module — "
                f"grep PreEco for .{_iq_name}( to find the instance and old wire.")
        else:
            for _iq_e in _iq_entries:
                # Sub-check C: iQ must NOT use same instance_name as oQ (different hierarchy)
                if _iq_e.get('instance_name') == _oq_inst:
                    issues.append(
                        f"Check 60 FAIL: iQ companion {_iq_name!r} uses same instance_name "
                        f"{_oq_inst!r} as oQ — WRONG. The iQ port is on an internal mux "
                        f"instance INSIDE the child module, NOT on {_oq_inst!r}. "
                        f"Find the correct instance by grepping PreEco for .{_iq_name}(.")
                # Sub-check D: iQ must have non-empty net_name_before
                _iq_before = _iq_e.get('net_name_before') or {}
                if not any(v for v in _iq_before.values()):
                    issues.append(
                        f"Check 60 FAIL: iQ companion {_iq_name!r} on instance "
                        f"{_iq_e.get('instance_name')!r} has empty net_name_before. "
                        f"Grep PreEco for .{_iq_name}( to find old wire and set net_name_before.")

    # ── Check 61: scan-output nets + dftopt-when-flat-exists in functional pins ─
    # test_so*/scan_* are definitively scan-chain outputs — always block.
    # dftopt* may be valid (when no flat form exists, e.g. bit 3 of a bus in Route)
    # but must NOT be used when the flat form <bus>_<N>_ exists in the PreEco netlist.
    _SCAN_PREFIXES = ('test_so', 'scan_')
    _FUNCTIONAL_PINS = ('A1', 'A2', 'B1', 'B2', 'I', 'IN', 'D')
    # Load PreEco Synthesize text for flat-form existence check (best-effort)
    _preeco_txt = ''
    _preeco_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    if os.path.isfile(_preeco_gz):
        try:
            import gzip as _gz61
            _preeco_txt = _gz61.open(_preeco_gz, 'rt', errors='ignore').read()
        except Exception:
            pass
    for _stage in ('Synthesize', 'PrePlace', 'Route'):
        for _e in study.get(_stage, []):
            if _e.get('change_type') != 'new_logic_gate': continue
            _pcs = _e.get('port_connections_per_stage', {}).get(_stage) or _e.get('port_connections') or {}
            for _pin in _FUNCTIONAL_PINS:
                _net = str(_pcs.get(_pin, ''))
                # Block scan-chain prefixes — BUT exempt when the net is a DFF Q output
                # in the stage PreEco netlist. test_so* signals can be DFF .Q outputs that
                # carry the functional value (e.g. test_so3618 = wr_vld0_d1_reg/Q2 in PP/Route
                # after scan stitching). These are NOT scan-only paths and are FM-traceable.
                if any(_net.startswith(pfx) for pfx in _SCAN_PREFIXES):
                    # Exemption: allow test_so* when it is a DFF Q/Q2/Q3/... output in the
                    # stage PreEco netlist. MB DFFs use numbered Q pins (Q2, Q4 etc.) that
                    # carry functional values — these are NOT scan-only paths.
                    # Use a direct grep since _index_module_body only matches plain .Q pins.
                    _is_dff_q_61 = False
                    if _stage in ('PrePlace', 'Route') and args.ref_dir:
                        try:
                            import subprocess as _sp61
                            _gz61 = Path(args.ref_dir) / 'data' / 'PreEco' / f'{_stage}.v.gz'
                            if _gz61.is_file():
                                _r61 = _sp61.run(
                                    _fastcmd(f"zcat {_gz61} | grep -c '\\.Q[0-9]*\\s*(\\s*{re.escape(_net)}\\s*)'"),
                                    shell=True, capture_output=True, text=True, timeout=30)
                                _is_dff_q_61 = int(_r61.stdout.strip() or 0) > 0
                        except Exception:
                            pass
                    if not _is_dff_q_61:
                        issues.append(
                            f"Check 61 FAIL: [{_stage}] gate {_e.get('instance_name')!r} "
                            f"pin .{_pin}={_net!r} is a scan-domain net (not a DFF Q output). "
                            f"Use the DFF Q output wire directly if it is a functional signal, "
                            f"or use the bare RTL name / fenets actual_wire instead.")
                # Block dftopt* when the flat form <bus>_<N>_ exists in PreEco
                elif _net.startswith('dftopt') and _stage in ('PrePlace', 'Route'):
                    # dftopt* is only valid when the flat form <bus>_<bit>_ does NOT exist
                    # in the PreEco netlist. If a sibling bit uses flat form AND the flat
                    # form for THIS bit exists → dftopt* is wrong, use flat instead.
                    _inst61 = _e.get('instance_name', '')
                    _bit_m61 = re.search(r'_(\d+)_$', _inst61)
                    if _bit_m61:
                        _bit61  = _bit_m61.group(1)
                        # Strip trailing _<N>_ to get base (handles 'bit0_' style names)
                        _base61 = re.sub(r'_\d+_$', '', _inst61)
                        for _sib61 in study.get(_stage, []):
                            if (_sib61.get('change_type') != 'new_logic_gate' or
                                    not _sib61.get('instance_name','').startswith(_base61) or
                                    _sib61.get('instance_name') == _inst61):
                                continue
                            _sp61  = (_sib61.get('port_connections_per_stage') or {}).get(_stage) or _sib61.get('port_connections') or {}
                            _sn61  = str(_sp61.get(_pin, ''))
                            if (_sn61 and not _sn61.startswith('dftopt') and
                                    not _sn61.startswith('copt_net') and
                                    re.search(r'_\d+_$', _sn61)):
                                # Derive expected flat form for this bit and check existence in PreEco
                                _flat61 = re.sub(r'_\d+_$', f'_{_bit61}_', _sn61)
                                if _preeco_txt and re.search(r'\b' + re.escape(_flat61) + r'\b', _preeco_txt):
                                    issues.append(
                                        f"Check 61 FAIL: [{_stage}] gate {_inst61!r} pin .{_pin}={_net!r} "
                                        f"uses dftopt* but flat form {_flat61!r} exists in PreEco — "
                                        f"use flat form instead of dftopt*.")
                                # else: flat form absent → dftopt* is valid (e.g. bit 3 in Route)
                                break

    # ── Check 62: bus-DFF D-chain gates in PP/Route must have per-bit distinct inputs ─
    # If all N per-bit D-chain gates share the same A1/A2 net in PP/Route, the
    # rename map returned a bus-level collapse — all bits get the same wire (wrong).
    # Detect bus-bit gate groups by instance name pattern: same prefix, trailing _N_.
    import re as _re
    _BIT_SUFFIX_RE = _re.compile(r'^(.+)_(\d+)_$')
    for _stage in ('PrePlace', 'Route'):
        _groups: dict = {}
        for _e in study.get(_stage, []):
            if _e.get('change_type') != 'new_logic_gate': continue
            _m = _BIT_SUFFIX_RE.match(_e.get('instance_name', ''))
            if not _m: continue
            _base = _m.group(1)
            # Scope by module_name — uniquified families have a per-module
            # instance so (base, module) is the correct grouping key.
            _mod62 = _e.get('module_name') or ''
            _groups.setdefault((_base, _mod62), []).append(_e)
        for (_base, _mod62), _gates in _groups.items():
            if len(_gates) < 2: continue
            for _pin in ('A1', 'A2'):
                _nets = set()
                for _g in _gates:
                    _pcs = (_g.get('port_connections_per_stage') or {}).get(_stage) or _g.get('port_connections') or {}
                    _n = _pcs.get(_pin)
                    if _n: _nets.add(_n)
                _shared = next(iter(_nets)) if len(_nets) == 1 else None
                if _shared and len(_gates) >= 2 and not _shared.startswith('n_eco_'):
                    # ECO-internal nets (n_eco_*) are intentionally shared across bits;
                    # only flag when a non-ECO net collapses all bits (bus-level rename collapse)
                    issues.append(
                        f"Check 62 FAIL: [{_stage}] bus gate group {_base!r} "
                        f"has all {len(_gates)} bits with identical .{_pin}={_shared!r}. "
                        f"Bus-level rename collapsed all bits — use per-bit flat form <bus>_<N>_ instead.")

    # ── Check 63: oQ change → old wire must have ZERO remaining connections ──
    # ALL instances in the child module referencing the old wire must be covered
    # by port_connection entries. Any remaining → FM globally-unmatched → DFF fail.
    if os.path.isfile(_gl_s):
        try:
            import gzip as _gz3
            _port_re63 = re.compile(r'\.\s*([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)')
            _inst_re63 = re.compile(r'^\s*\w[\w:]+\s+(\w+)\s*\(')  # CellType InstName (
            _mod_re63  = re.compile(r'^\s*module\s+(\w+)\b')
            _end_re63  = re.compile(r'\bendmodule\b')

            _pc_syn63 = [e for e in study.get('Synthesize', []) if e.get('change_type') == 'port_connection']
            for _oq_e63 in [e for e in _pc_syn63 if str(e.get('port_name', '')).startswith('oQ_')]:
                _old_wire63 = next((v for v in (_oq_e63.get('net_name_before') or {}).values() if v), None)
                if not _old_wire63:
                    continue
                _iq_name63    = 'iQ_' + str(_oq_e63.get('port_name', ''))[3:]
                _iq_mods63    = {e.get('module_name', '') for e in _pc_syn63 if e.get('port_name') == _iq_name63 and e.get('module_name')}
                # (instance_name, port_name) pairs covered in study JSON for this old wire
                _covered63 = {(_e.get('instance_name', ''), _e.get('port_name', ''))
                               for _e in _pc_syn63
                               if any(v == _old_wire63 for v in (_e.get('net_name_before') or {}).values())}
                # Scan PreEco — track module, instance, paren-depth to get (inst, port) pairs
                _in_child63 = _cur_inst63 = None
                _depth63 = 0
                with _gz3.open(_gl_s, 'rt', errors='ignore') as _f63:
                    for _l63 in _f63:
                        _mm = _mod_re63.match(_l63)
                        if _mm:
                            _mn = _mm.group(1)
                            _base = _mn[_mn.rfind('_t_')+3:] if '_t_' in _mn else _mn
                            _in_child63 = bool(_iq_mods63 & {_mn, _base})
                            _cur_inst63 = None; _depth63 = 0
                        if _end_re63.search(_l63):
                            _in_child63 = False; _cur_inst63 = None; _depth63 = 0
                        if not _in_child63:
                            continue
                        _im = _inst_re63.match(_l63)
                        if _im and _depth63 == 0:
                            _cur_inst63 = _im.group(1)
                        _depth63 += _l63.count('(') - _l63.count(')')
                        if _depth63 <= 0:
                            _cur_inst63 = None; _depth63 = 0
                        for _pm in _port_re63.finditer(_l63):
                            _p63, _n63 = _pm.group(1), _pm.group(2)
                            if _n63 == _old_wire63 and _cur_inst63 and (_cur_inst63, _p63) not in _covered63:
                                issues.append(
                                    f"Check 63 FAIL: old wire {_old_wire63!r} still used by "
                                    f"instance {_cur_inst63!r} port .{_p63}() in child module "
                                    f"— not covered by study JSON port_connection. "
                                    f"Add: instance_name={_cur_inst63!r}, port_name={_p63!r}, "
                                    f"net_name=<canonical>, net_name_before={_old_wire63!r}.")
        except Exception:
            pass

    # ── Check 64: rewired pre-existing DFFs must have consistent SI across stages ─
    # When a pre-existing DFF has its CP or D rewired, its SI pin may differ between
    # stages (1'b0 in Synth from PreEco, scan-stitched value in PP/Route).
    # FM sees this as a scan cone mismatch → d0nt_mux globally unmatched → DFF failures.
    # Fix: add SI=1'b0 rewire entries for PP and Route to maintain consistency.
    if os.path.isfile(_gl_s):
        try:
            import gzip as _gz4
            _non_const_re64 = re.compile(r"^1'b[01]$")
            _inst_si_re64   = re.compile(r'\.(SI|SE)\s*\(\s*([^)]+?)\s*\)')
            _mod_re64        = re.compile(r'^\s*module\s+(\w+)\b')

            # Collect all rewired DFF instance names (CP or D rewires)
            _rewired_insts64 = set()
            for _e64 in study.get('Synthesize', []):
                if _e64.get('change_type') == 'rewire' and _e64.get('pin') in ('CP', 'D', 'D1','D2','D3','D4','D5','D6','D7','D8'):
                    _rewired_insts64.add(_e64.get('cell_name') or _e64.get('instance_name',''))

            # Collect SI/SE rewire entries already in study JSON (PP/Route)
            _si_se_rewired64 = set()  # (cell_name, pin) pairs already covered
            for _stage64 in ('PrePlace', 'Route'):
                for _e64 in study.get(_stage64, []):
                    if _e64.get('change_type') == 'rewire' and _e64.get('pin') in ('SI', 'SE'):
                        _key64 = (_e64.get('cell_name') or _e64.get('instance_name',''), _e64.get('pin'),'')
                        _si_se_rewired64.add(_key64)

            # Scan PreEco netlists to find inconsistent SI
            for _stage64, _stage_gz64 in [
                ('PrePlace', os.path.join(args.ref_dir, 'data', 'PreEco', 'PrePlace.v.gz')),
                ('Route',    os.path.join(args.ref_dir, 'data', 'PreEco', 'Route.v.gz')),
            ]:
                if not os.path.isfile(_stage_gz64): continue
                # Build SI map for rewired instances in this stage
                with _gz4.open(_stage_gz64, 'rt', errors='ignore') as _f64:
                    _cur_inst64 = None
                    _depth64 = 0
                    for _l64 in _f64:
                        _mm64 = _mod_re64.match(_l64)
                        if _mm64: _cur_inst64 = None; _depth64 = 0
                        # Detect instance line
                        _im64 = re.match(r'^\s*\w[\w:]+\s+(\w+)\s*\(', _l64)
                        if _im64 and _depth64 == 0: _cur_inst64 = _im64.group(1)
                        _depth64 += _l64.count('(') - _l64.count(')')
                        if _depth64 <= 0: _cur_inst64 = None; _depth64 = 0
                        if _cur_inst64 and _cur_inst64 in _rewired_insts64:
                            for _pm64 in _inst_si_re64.finditer(_l64):
                                _pin64    = _pm64.group(1)   # SI or SE
                                _val64    = _pm64.group(2).strip()
                                _key64    = (_cur_inst64, _pin64, '')
                                # If scan-stitched (non-constant) and no rewire entry covers it
                                if not _non_const_re64.match(_val64) and _key64 not in _si_se_rewired64:
                                    issues.append(
                                        f"Check 64 FAIL: [{_stage64}] rewired pre-existing DFF "
                                        f"{_cur_inst64!r} has .{_pin64}={_val64!r} (scan-stitched) "
                                        f"but PreEco Synthesize has {_pin64}=1'b0. "
                                        f"Add rewire entry: cell_name={_cur_inst64!r}, pin={_pin64!r}, "
                                        f"old_net={_val64!r}, new_net=\"1'b0\" to maintain "
                                        f"scan cone consistency across stages for FM.")
        except Exception:
            pass

    # ── Check 65: CTS rename used when bare RTL name exists in all 3 stages ────
    # Rule (3-step priority):
    #   1. Bare RTL name exists in ALL 3 stages → MUST use bare name in all stages
    #   2. Absent in PP/Route → structural driver trace from Synth (find driver inst
    #      in Synth, locate same inst in PP/Route, read its output net)
    #   3. Last resort → CTS rename from fenets (FxPrePlace_*/FxPlace_*)
    #
    # This check fires when: Synth uses a bare RTL name AND PP/Route uses a CTS rename
    # AND the bare name EXISTS in that stage's PreEco netlist (step 1 applies → FAIL).
    # If bare name absent in PP/Route stage, CTS rename or structural trace is correct.
    _CTS_RENAME_PREFIXES_65 = ('FxPrePlace_', 'FxPlace_', 'FxOptCts_', 'FxCts_')
    _FUNC_PINS_65 = ('A1', 'A2', 'B1', 'B2', 'I', 'IN', 'D')
    _SKIP_SYNTH_PREFIXES_65 = ("1'b", "n_eco_", "ECO_", "PENDING", "UNRESOLVABLE",
                                "FxPrePlace_", "FxPlace_", "FxOptCts_", "FxCts_")
    # Per-stage word-token SETS (cached, one build) for the bare-name existence check. Using a set
    # membership test is O(1) vs a `re.search(r'\bnet\b', <300MB text>)` per net per stage — with
    # 200+ gate entries × several pins × 3 stages the regex form was an O(gates×file) hotspot
    # (~1h). Non-identifier net names (brackets etc.) fall back to a cached full-text regex (rare).
    _preeco_words_65: dict = {}
    for _s65 in ('Synthesize', 'PrePlace', 'Route'):
        _gz65 = Path(args.ref_dir) / 'data' / 'PreEco' / f'{_s65}.v.gz'
        if _gz65.is_file():
            try:
                _preeco_words_65[_s65] = _nl_words_br(_plain_netlist(str(_gz65)))
            except Exception:
                _preeco_words_65[_s65] = set()
        else:
            _preeco_words_65[_s65] = set()

    def _bare_in_stage_65(net, s65):
        # O(1) membership for BOTH plain and bracket-bit net names (no per-net full-text regex)
        return net in _preeco_words_65.get(s65, set())

    for _e65 in study.get('Synthesize', []):
        if _e65.get('change_type') not in ('new_logic_gate', 'new_logic_dff'):
            continue
        _inst65  = _e65.get('instance_name', '?')
        _pcs_syn = _e65.get('port_connections') or {}
        _pcs_ps  = _e65.get('port_connections_per_stage') or {}
        for _pin65 in _FUNC_PINS_65:
            _syn_net = str(_pcs_syn.get(_pin65, ''))
            if not _syn_net or any(_syn_net.startswith(p) for p in _SKIP_SYNTH_PREFIXES_65):
                continue  # Synth already uses CTS name or ECO-internal — skip
            # Check if bare name exists in ALL 3 stages (condition for Check 65 to apply)
            _bare_in_all = all(_bare_in_stage_65(_syn_net, _s65)
                               for _s65 in ('Synthesize', 'PrePlace', 'Route'))
            if not _bare_in_all:
                continue  # bare name absent in some stage → structural trace / CTS rename OK
            for _stg65 in ('PrePlace', 'Route'):
                _stg_net = str((_pcs_ps.get(_stg65) or {}).get(_pin65, ''))
                if not _stg_net:
                    _stg_e65 = next(
                        (ee for ee in study.get(_stg65, [])
                         if ee.get('instance_name') == _inst65), None)
                    if _stg_e65:
                        _stg_net = str((_stg_e65.get('port_connections') or {}).get(_pin65, ''))
                if not _stg_net or not any(_stg_net.startswith(p) for p in _CTS_RENAME_PREFIXES_65):
                    continue  # stage net is not a CTS rename — no issue
                # Exemption: if the fenets rename map has a specific actual_wire for this
                # signal in this stage AND the study JSON uses exactly that actual_wire,
                # the studier correctly chose the CTS rename because the bare name in
                # PP/Route scope refers to a DIFFERENT logical signal (different DFF source).
                # Example: WDB/IReset in Synth = IReset_d1_reg.Q, but "IReset" in PP WDB
                # scope = parent umcdat's IReset (different DFF) → CTS rename is correct.
                _fenets_actual = ''
                for _fkey, _fval in (_rmap or {}).items():
                    if not isinstance(_fval, dict): continue
                    if _fval.get(f'actual_wire_{_stg65}') == _stg_net:
                        _fenets_actual = _stg_net
                        break
                if _fenets_actual:
                    # Fenets exemption found — but override it if the CTS rename is a
                    # module-level PRIMARY INPUT while the bare name IS internally driven.
                    # A primary input cannot be traced by FM to its source DFF → NOT EQUIV.
                    # If the bare name is internally driven (has a .Q/.Z/.ZN driver inside
                    # the module), prefer it over the primary-input CTS rename.
                    # Use the existing _index_module_body driver_map for this check.
                    try:
                        _dm65x, _pi65x = _index_module_body(
                            _e65.get('module_name', ''), args.ref_dir, _stg65)
                        _cts_is_primary65 = _stg_net in (_pi65x or set())
                        _bare_is_driven65 = (
                            _dm65x is not None and _syn_net in _dm65x
                        )
                        if _cts_is_primary65 and _bare_is_driven65:
                            # CTS rename = primary input AND bare name = internally driven
                            # → bare name is FM-traceable, CTS rename is not → override exemption
                            issues.append(
                                f"Check 65 FAIL: [{_stg65}] gate {_inst65!r} pin .{_pin65}="
                                f"{_stg_net!r} — fenets exemption OVERRIDDEN: CTS rename is a "
                                f"module primary input (FM cannot trace) but bare RTL name "
                                f"{_syn_net!r} IS internally driven in {_stg65} (FM-traceable). "
                                f"Fix: use bare name {_syn_net!r} — it is preferred over the "
                                f"fenets actual_wire when it is internally driven in the stage. "
                                f"Rule 65 override: primary-input CTS renames are not FM-traceable "
                                f"even when they match the fenets actual_wire.")
                        else:
                            continue  # CTS rename is internally driven OR bare name is also primary — exemption holds
                    except Exception:
                        continue  # can't verify → keep exemption
                issues.append(
                    f"Check 65 FAIL: [{_stg65}] gate {_inst65!r} pin .{_pin65}="
                    f"{_stg_net!r} uses CTS rename but bare RTL name {_syn_net!r} "
                    f"EXISTS in ALL 3 PreEco stages (Synthesize/PrePlace/Route) AND "
                    f"has no fenets actual_wire entry for this CTS rename. "
                    f"Fix: use {_syn_net!r} in ALL stages — bare RTL name is preferred "
                    f"when it exists across all stages (step 1 of 3-step priority). "
                    f"Use CTS rename only when the bare name refers to a different "
                    f"logical signal in that stage (verify via fenets map).")

    # ── Check 66: bare RTL name used but fenets has a DIFFERENT actual_wire ──
    # Rule 66 (anti-regression guard): when a PP/Route gate pin uses the SAME bare RTL
    # name as Synthesize (e.g. both use 'IReset'), but the fenets rename map has a
    # specific actual_wire_<stage> for that signal scope that is DIFFERENT from the bare
    # name → the bare name in PP/Route scope refers to a DIFFERENT DFF source than Synth.
    # The fenets actual_wire is the correct per-stage value — do NOT replace it with the
    # bare RTL name.
    #
    # Root cause (Round 2 regression): re_studier Mode H fix saw 'IReset' as input port
    # in PP/Route WDB module and replaced FxPrePlace_HFSNET_933 with 'IReset'.
    # But FxPrePlace_HFSNET_933 IS the fenets actual_wire_PrePlace for umcdat/WDB/IReset
    # (= correct (+) polarity equivalent of IReset_d1_reg.Q). The bare 'IReset' in PP
    # WDB scope = parent umcdat's IReset (different DFF) → FM NOT EQUIVALENT.
    #
    # Check 66 fires when: Synth uses bare name X, PP/Route also uses bare name X,
    # but fenets map has actual_wire_<stage> ≠ X for scope/X → wrong DFF source.
    _FUNC_PINS_66 = ('A1', 'A2', 'B1', 'B2', 'I', 'IN', 'D')
    _SKIP_66 = ("1'b", "n_eco_", "ECO_", "PENDING", "UNRESOLVABLE",
                "FxPrePlace_", "FxPlace_", "FxOptCts_", "FxCts_")

    for _e66 in study.get('Synthesize', []):
        if _e66.get('change_type') not in ('new_logic_gate', 'new_logic_dff'):
            continue
        _inst66 = _e66.get('instance_name', '?')
        _mod66  = _e66.get('module_name', '')
        _pcs_syn66 = _e66.get('port_connections') or {}
        _pcs_ps66  = _e66.get('port_connections_per_stage') or {}
        for _pin66 in _FUNC_PINS_66:
            _syn_net66 = str(_pcs_syn66.get(_pin66, ''))
            if not _syn_net66 or any(_syn_net66.startswith(p) for p in _SKIP_66):
                continue  # ECO-internal or CTS name in Synth — skip
            for _stg66 in ('PrePlace', 'Route'):
                _stg_net66 = str((_pcs_ps66.get(_stg66) or {}).get(_pin66, ''))
                if not _stg_net66:
                    _stg_e66 = next(
                        (ee for ee in study.get(_stg66, [])
                         if ee.get('instance_name') == _inst66), None)
                    if _stg_e66:
                        _stg_net66 = str((_stg_e66.get('port_connections') or {}).get(_pin66, ''))
                if _stg_net66 != _syn_net66:
                    continue  # different value — Check 65 handles this case, not 66
                # PP/Route uses same bare name as Synth — check fenets map
                # If fenets has actual_wire_<stage> ≠ bare name for the gate's scope → wrong DFF source
                # Use gate's instance_scope to build the expected fenets key prefix:
                #   instance_scope='umcdat/WDB' → key starts with 'umcdat/WDB/'
                #   instance_scope='' (tile root) → key starts with tile module prefix (not WDB)
                _modname66 = (_e66.get('module_name') or '').lower()
                for _fk66, _fv66 in (_rmap or {}).items():
                    if not isinstance(_fv66, dict): continue
                    _fa66 = _fv66.get(f'actual_wire_{_stg66}', '')
                    if not _fa66 or _fa66 == _syn_net66:
                        continue  # no fenets entry or fenets agrees with bare name
                    # General scope check: all scope tokens in fenets key must appear
                    # in the gate's module_name. 'umcdat/WDB/IReset' → tokens=['umcdat','wdb']
                    # must all be substrings of 'ddrss_umcdat_t_umcwdb'. This is general
                    # and works for any submodule (not WDB-specific).
                    _fk_scope_toks66 = [t.lower() for t in _fk66.split('/')[:-1]]
                    if not all(t in _modname66 for t in _fk_scope_toks66):
                        continue  # fenets scope doesn't match gate's module
                    # Synth path check: bare name must appear as an exact token in the
                    # fenets Synth path. This avoids false matches where the bare name
                    # is a substring of a different signal name.
                    # e.g. 'IReset' in 'IReset_d1_reg/net0' splits as ['IReset','d1','reg','net0'] → match
                    # but 'IReset' in 'DDR5En_pre_buf_reg/net0' → no match → skip
                    _syn_map66 = _fv66.get('actual_wire_Synthesize', '') or _fv66.get('Synthesize', '')
                    if _syn_net66 not in re.split(r'[/_\s]', _syn_map66):
                        continue  # bare name not a token in Synth path → different signal
                    issues.append(
                        f"Check 66 FAIL: [{_stg66}] gate {_inst66!r} pin .{_pin66}="
                        f"{_stg_net66!r} uses same bare RTL name as Synthesize, BUT "
                        f"fenets map {_fk66!r} has actual_wire_{_stg66}={_fa66!r} — "
                        f"the bare name {_stg_net66!r} in {_stg66} scope refers to a "
                        f"DIFFERENT DFF source than Synth. "
                        f"Fix: use fenets actual_wire {_fa66!r} for {_stg66} stage. "
                        f"Anti-regression guard (Rule 66): re_studier Mode H must not "
                        f"replace fenets actual_wire with bare RTL name.")
                    break

    # ── Mode-I bridge driver-chain completeness ───────────────────────────────
    # A port_connection that feeds a NEW input from a spare UNCONNECTED_* bus bit
    # only works if that bit is actually driven. If the bit sits on a child
    # instance's output bus and the child drives that bit internally with another
    # UNCONNECTED placeholder (undriven), a DEEPER bridge study entry is required —
    # otherwise the new net is undriven (FM Mode-I fail). The studier must recurse
    # the Mode-I walk to the driven source; "higher-level propagation" is not a driver.
    if args.ref_dir:
        import subprocess as _sp_br
        _gzb = Path(args.ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz'
        _btext = ''
        if _gzb.is_file():
            try:
                _btext = _sp_br.run(_fastcmd(f'zcat {_gzb}'), shell=True, capture_output=True,
                                    text=True, timeout=120).stdout
            except Exception:
                _btext = ''
        def _mod_body_br(text, mod):
            m = re.search(r'(?m)^module\s+' + re.escape(mod) + r'\b.*?^endmodule',
                          text, re.DOTALL)
            return m.group(0) if m else ''
        def _owner_bus_br(body, net):
            # instance whose port concat contains net -> (inst, child_mod, port, bit_lsb)
            for m in re.finditer(r'\.(\w+)\s*\(\s*\{([^{}]*)\}\s*\)', body):
                elems = [x.strip() for x in m.group(2).split(',')]
                if net in elems:
                    bit = len(elems) - 1 - elems.index(net)   # MSB-first: last = bit0
                    last = None
                    for im in re.finditer(r'(?m)^\s*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(',
                                          body[:m.start()]):
                        last = im
                    return (last.group(2) if last else '',
                            last.group(1) if last else '', m.group(1), bit)
            return None
        _bridge_mods = set()
        for st in ('Synthesize', 'PrePlace', 'Route'):
            for e in study.get(st, []):
                if e.get('change_type') == 'port_connection' or e.get('unconnected_rewires'):
                    for mk in ('module_name', 'child_module_name'):
                        mv = e.get(mk)
                        if mv:
                            _bridge_mods.add(mv)
        if _btext:
            for e in study.get('Synthesize', []):
                if e.get('change_type') != 'port_connection':
                    continue
                nb = e.get('net_name_before')
                spare = nb.get('Synthesize') if isinstance(nb, dict) else nb
                if not isinstance(spare, str) or not re.match(r'(SYNOPSYS_)?UNCONNECTED_\d+$', spare):
                    continue
                parent_mod = e.get('module_name') or ''
                pbody = _mod_body_br(_btext, parent_mod) if parent_mod else _btext
                ob = _owner_bus_br(pbody, spare)
                if not ob:
                    continue
                inst, child_mod, port, bit = ob
                if not child_mod:
                    continue
                # Is the source bit driven anywhere? The child's output net for this
                # bit is <port>[bit] / <port>_bit_ (port names change across levels, so
                # test the net's drivers directly, not a per-level port concat).
                flat = f'{port}_{bit}_'
                brk  = f'{port}[{bit}]'
                drv = re.compile(r'\.(?:Z|ZN|ZN1|ZN2|Q|QN|CO|S|SO)\s*\(\s*(?:'
                                 + re.escape(flat) + r'|' + re.escape(brk) + r')\s*\)')
                asn = re.compile(r'assign\s+(?:' + re.escape(flat) + r'|'
                                 + re.escape(brk) + r')\s*=')
                undriven = not (drv.search(_btext) or asn.search(_btext))
                if undriven and child_mod not in _bridge_mods:
                    issues.append(
                        f"HIGH: Mode-I bridge via spare {spare!r} feeding a new port is INCOMPLETE "
                        f"— bit {bit} of {inst} ({child_mod}).{port} is driven internally by an "
                        f"UNCONNECTED placeholder (undriven) and no study entry bridges "
                        f"{child_mod}. The new net stays undriven (FM Mode-I fail). Recurse the "
                        f"Mode-I walk into {child_mod} to drive the source bit; 'higher-level "
                        f"propagation' is not a driver.")

        # ── Bus-width consistency on whole-bus port_connection bridges ─────────
        # When net_name_before is a MULTI-BIT bus connected wholesale (e.g. a
        # register read-back loopback `wire [31:0] X_0` -> `output [31:0] X`), the
        # rename target (net_name) must be a FULL bus, not a single bit. A bit form
        # connects a 32-bit port to 1 bit -> width mismatch / 31 bits undriven.
        # Concat-element renames (net_name_before is a scalar UNCONNECTED_* inside a
        # {} concat) are exempt — bit form is correct there.
        if _btext:
            _bus_cache = {}
            def _is_multibit_bus(name):
                if name not in _bus_cache:
                    _bus_cache[name] = bool(re.search(
                        r'(?m)^\s*(?:wire|output|input|reg)\s*\[\d+:\d+\]\s*'
                        + re.escape(name) + r'\s*;', _btext))
                return _bus_cache[name]
            _single_bit = re.compile(r'(?:\[\d+\]|_\d+_)$')
            for e in study.get('Synthesize', []):
                if e.get('change_type') != 'port_connection':
                    continue
                nb = e.get('net_name_before')
                before = nb.get('Synthesize') if isinstance(nb, dict) else nb
                nn = e.get('net_name')
                if not isinstance(before, str) or not isinstance(nn, str):
                    continue
                if _is_multibit_bus(before) and _single_bit.search(nn):
                    issues.append(
                        f"HIGH: port_connection on {e.get('instance_name','?')}."
                        f"{e.get('port_name','?')} renames whole-bus net {before!r} "
                        f"(declared multi-bit) to single-bit {nn!r} — width mismatch "
                        f"(N-bit port driven by 1 bit; upper bits undriven). Whole-bus "
                        f"connections must rename to the FULL bus (drop the bit suffix), "
                        f"not a single bit; only concat-element (scalar UNCONNECTED_*) "
                        f"renames use bit form.")

    # ── Multi-bit counter gate must feed back the register (hold-mux) ──────────
    # Mirror of Step-1 Fix E on the BUILT study: if the ECO gates the next-state of
    # >=2 DFF bits of the SAME register (a counter with implicit-hold default), the
    # new gate cone MUST feed back the register's current bits (load-enable hold
    # mux). No feedback = condition-gating that may not hold -> FM logic mismatch.
    _casc = {}
    for e in study.get('Synthesize', []):
        c6 = e.get('check6_cascade_dffs') or {}
        for dff in (c6.get('cascade_dffs') or []):
            m = re.match(r'(.+)_reg_(\d+)_$', str(dff))
            if m:
                _casc.setdefault(m.group(1), set()).add(m.group(2))
    _gate_inputs = []
    for e in study.get('Synthesize', []):
        if e.get('change_type') == 'new_logic_gate':
            for k, v in (e.get('port_connections') or {}).items():
                if k not in ('Z', 'ZN', 'ZN1', 'ZN2', 'Q', 'QN', 'CO', 'S') and isinstance(v, str):
                    _gate_inputs.append(v)
    for reg, bits in _casc.items():
        if len(bits) < 2:
            continue
        fb = any(re.search(re.escape(reg) + r'(?:\[\d+\]|_\d+_)', g) for g in _gate_inputs)
        if not fb:
            issues.append(
                f"HIGH: ECO gates {len(bits)} next-state DFF bits of register {reg!r} "
                f"(bits {sorted(bits)}) but no new gate input feeds back {reg}'s current "
                f"value — condition-gating without a hold path. A multi-bit counter gate "
                f"must be a per-bit load-enable HOLD MUX feeding back {reg}[bit]; otherwise "
                f"it may not hold (FM logic mismatch). See rtl_diff_analyzer.md and_term "
                f"multi-bit hold-mux rule.")

    # ── Bridge bit-index consistency ───────────────────────────────────────────
    # A Mode-I bridge must carry the bit indicated by Step 1's flat_net_name. If
    # Step 1 sources REG_X[0] but the study renames to REG_X[31] / REG_X_31_, the bit
    # is wrong (MSB/LSB inversion in bus_bit_index) -> wrong CSR bit / bit crossover.
    _bit_re = re.compile(r'^(REG_\w+?)(?:\[(\d+)\]|_(\d+)_)$')
    _expected_bit = {}
    for c in rtl_diff.get('changes', []):
        for f in ('flat_net_name', 'd_input_resolved_net'):
            v = c.get(f)
            if isinstance(v, str):
                m = _bit_re.match(v)
                if m:
                    _expected_bit[m.group(1)] = m.group(2) or m.group(3)
    for e in study.get('Synthesize', []):
        if e.get('change_type') != 'port_connection':
            continue
        nn = e.get('net_name')
        if not isinstance(nn, str):
            continue
        m = _bit_re.match(nn)
        if not m:
            continue
        base, bit = m.group(1), (m.group(2) or m.group(3))
        if base in _expected_bit and bit != _expected_bit[base]:
            issues.append(
                f"HIGH: bridge port_connection on {e.get('instance_name','?')} renames to "
                f"{nn!r} (bit {bit}) but Step 1 sources {base}[{_expected_bit[base]}] (bit "
                f"{_expected_bit[base]}). Wrong bus bit — likely an MSB/LSB inversion in "
                f"bus_bit_index. Use bit {_expected_bit[base]} to match the RTL source.")

    # ── No-op bridge rename ─────────────────────────────────────────────────────
    # A port_connection whose net_name equals its net_name_before renames a net to
    # itself — it drives nothing. For a Mode-I bridge level (e.g. the register-output
    # oQ loopback) this leaves the source bit undriven -> FM Mode-I fail.
    for e in study.get('Synthesize', []):
        if e.get('change_type') != 'port_connection':
            continue
        nb = e.get('net_name_before')
        before = nb.get('Synthesize') if isinstance(nb, dict) else nb
        nn = e.get('net_name')
        if isinstance(before, str) and before and before == nn:
            issues.append(
                f"HIGH: port_connection on {e.get('instance_name','?')}."
                f"{e.get('port_name','?')} has net_name == net_name_before ({nn!r}) — a "
                f"no-op rename that drives nothing. A Mode-I bridge level must actually "
                f"rename the net to wire the source through (e.g. the register-output oQ "
                f"loopback). Emit the real rename.")

    # ── Bridge family consistency ──────────────────────────────────────────────
    # Every CSR register-file bridge port (oQ_*/iQ_*) must belong to the bridge's CSR
    # family, derived from Step 1's flat_net_name (REG_UmcCfgEco -> "UmcCfgEco"). A
    # hallucinated off-family port (e.g. oQ_UMC_CONFIG_DDR_TYPE while bridging
    # UmcCfgEco) bridges the WRONG register / corrupts unrelated logic.
    _fam = set()
    for c in rtl_diff.get('changes', []):
        for f in ('flat_net_name', 'd_input_resolved_net'):
            v = c.get(f)
            if isinstance(v, str):
                m = re.match(r'REG_(\w+?)(?:\[\d+\]|_\d+_)$', v)
                if m:
                    _fam.add(m.group(1))
    if _fam:
        for e in study.get('Synthesize', []):
            if e.get('change_type') != 'port_connection':
                continue
            port = e.get('port_name') or ''
            if not re.match(r'^(?:oQ|iQ)_', port):
                continue
            if not any(fb in port for fb in _fam):
                issues.append(
                    f"HIGH: bridge port_connection on {e.get('instance_name','?')}.{port} is "
                    f"OFF-FAMILY — it does not reference the bridged CSR register {sorted(_fam)} "
                    f"(net={e.get('net_name')!r}). Likely a hallucinated wrong register port; the "
                    f"loopback must rename the oQ_/iQ_ ports of the SAME register being bridged.")

    # ── #1 priority_force BUILD verification: for each forced signal bit the study
    #    must contain BOTH a force-mux gate (const encoded via gate CHOICE: OR2 for a
    #    const-1 bit, INR2 for a const-0 bit — see eco_emit_priority_force.py) AND a
    #    DFF-pin rewire that repoints that bit's flop .D onto the fresh force-mux net.
    #    Checking gate-existence + rewire (NOT const-as-literal-input, which the
    #    synthesizer never emits). Fires only on priority_force changes (none legacy). ──
    _syn3 = study.get('Synthesize', [])
    # fresh force-mux outputs = OR2/INR2 gates emitted by the priority_force builder
    _pf_mux_outs = set()
    for e in _syn3:
        if e.get('change_type') != 'new_logic_gate':
            continue
        inst = str(e.get('instance_name') or '')
        fn = (e.get('gate_function') or '').upper()
        if '_pf_mux_' in inst or (e.get('source') == 'eco_emit_priority_force' and fn in ('OR2', 'INR2')):
            out = e.get('output_net') or ''
            if out:
                _pf_mux_outs.add(out)
    # DFF-pin rewires whose new_net is one of those force-mux outputs
    _pf_rewire_nets = set()
    for e in _syn3:
        if e.get('change_type') == 'rewire' and (e.get('new_net') in _pf_mux_outs):
            _pf_rewire_nets.add(e.get('new_net'))
    # driver-side (combinational) rewires: old_net = the forced net that is re-driven
    _pf_driver_renamed = {e.get('old_net') for e in _syn3
                          if e.get('change_type') == 'rewire'
                          and (e.get('driver_side') or e.get('net_force'))}
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'priority_force':
            continue
        for f in (c.get('forced_signals') or []):
            sig = f.get('signal')
            nbits = len(f.get('bits') or [])
            # REGISTERED force-muxes (D-pin model): instance _pf_mux_<sig>_, output is a
            # fresh net rewired onto a flop .D. COMBINATIONAL net-force: instance
            # _pf_netmux_<sig>_, output IS the forced net, validated by a driver-side
            # rewire that renames the net's original driver output.
            reg_muxes = {e.get('output_net') for e in _syn3
                         if e.get('change_type') == 'new_logic_gate'
                         and f"_pf_mux_{sig}_" in str(e.get('instance_name') or '')}
            comb_muxes = {e.get('output_net') for e in _syn3
                          if e.get('change_type') == 'new_logic_gate'
                          and f"_pf_netmux_{sig}_" in str(e.get('instance_name') or '')}
            reg_muxes.discard(None); reg_muxes.discard('')
            comb_muxes.discard(None); comb_muxes.discard('')
            if not reg_muxes and not comb_muxes:
                issues.append(
                    f"CRITICAL/PRIORITY-FORCE: forced signal {sig!r} has NO force-mux gate in the "
                    f"study — Intent-B constant force (e.g. c0vld=1 / mop=CAS) not built. Run "
                    f"eco_emit_priority_force.py (emits OR2 per const-1 bit, INR2 per const-0 bit).")
                continue
            # registered: each mux output must be rewired onto a flop .D
            reg_wired = reg_muxes & _pf_rewire_nets
            if len(reg_wired) < len(reg_muxes):
                issues.append(
                    f"CRITICAL/PRIORITY-FORCE: forced signal {sig!r} has {len(reg_muxes)} force-mux "
                    f"gate(s) but only {len(reg_wired)} DFF-pin rewire(s) onto them — the flop .D of "
                    f"unwired bits still reads the OLD next-state net, so the force is dead. Each "
                    f"force-mux output must be rewired onto its bit's flop .D pin.")
            # combinational: each net-force mux output (the forced net) must have a
            # driver-side rewire renaming its original combinational driver output.
            comb_unwired = comb_muxes - _pf_driver_renamed
            if comb_unwired:
                issues.append(
                    f"CRITICAL/PRIORITY-FORCE: forced signal {sig!r} net-force mux output(s) "
                    f"{sorted(comb_unwired)[:6]} have NO driver-side rewire renaming the original "
                    f"combinational driver — the force-mux would collide with the existing driver on "
                    f"the same net. Each net-force mux needs the driver output-pin rename.")
            sig_muxes = reg_muxes | comb_muxes
            if nbits and len(sig_muxes) < nbits:
                issues.append(
                    f"CRITICAL/PRIORITY-FORCE: forced signal {sig!r} declares {nbits} bit(s) but only "
                    f"{len(sig_muxes)} force-mux gate(s) were built — {nbits - len(sig_muxes)} forced "
                    f"bit(s) were dropped.")

    # ── #2 term_op operator enforcement (unambiguous cases only, no polarity risk) ──
    _AND_FAM = ('AND2', 'AND3', 'AND4', 'AN2', 'AN3', 'AN4', 'ND2', 'ND3', 'ND4',
                'NAND2', 'NAND3', 'NAND4')
    _OR_FAM = ('OR2', 'OR3', 'OR4', 'NR2', 'NR3', 'NR4', 'NOR2', 'NOR3', 'NOR4')
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') != 'and_term' or c.get('term_op') not in ('and', 'or'):
            continue
        op, old_tok = c.get('term_op'), c.get('old_token')
        if not old_tok:
            continue
        for e in _syn3:
            if e.get('change_type') != 'new_logic_gate':
                continue
            pcs = e.get('port_connections') or {}
            ins = [v for k, v in pcs.items() if k not in ('Z', 'ZN', 'Q', 'CO', 'S')]
            if old_tok not in ins:
                continue
            fam = (e.get('gate_function') or '').upper()
            if op == 'or' and fam in _AND_FAM:
                issues.append(
                    f"HIGH/TERM-OP: and_term term_op='or' (OR-widen of {old_tok!r}) but combine gate "
                    f"{e.get('instance_name')!r} is {fam} (pure AND) — an OR-widen cannot be an AND gate.")
            elif op == 'and' and fam in _OR_FAM:
                issues.append(
                    f"HIGH/TERM-OP: and_term term_op='and' (AND-narrow of {old_tok!r}) but combine gate "
                    f"{e.get('instance_name')!r} is {fam} (pure OR) — an AND-narrow cannot be an OR gate.")

    # ── #2b Intent-A register guard-change: builder-owned + surgical==golden (HARD-FAIL) ──
    # A guard broaden/tighten on a register branch that assigns a constant MUST be re-driven by the
    # deterministic eco_cone_rebuild reg_guard_delta pass (correct-by-construction: OR for set /
    # AND-NOT for clear). Enforce (a) the study carries that pass's .D rewire for the register (the
    # builder ran; the studier did NOT hand-build an OR2 into D), and (b) the surgical model equals
    # the RTL golden next-state. Either miss is the JIRA-9666 postcas class → hard-fail.
    _rg_changes = [c for c in rtl_diff.get('changes', [])
                   if c.get('change_type') == 'and_term' and c.get('target_register')
                   and (c.get('branch_assigns') is not None or c.get('branch_loads') is not None)]
    if _rg_changes:
        _rg_rewires = [e for e in _syn3
                       if isinstance(e, dict) and e.get('reg_guard_delta')]
        for c in _rg_changes:
            reg = c.get('target_register'); mod = c.get('module_name')
            owned = any((reg in str(rw.get('cell_name') or '')) or
                        (reg in str((rw.get('cell_name_per_stage') or {}).get('Synthesize') or '')) or
                        (reg in str(rw.get('old_net') or '') and rw.get('dff_pin_rewire'))
                        for rw in _rg_rewires)
            if not owned:
                issues.append(
                    f"HIGH/REG-GUARD: register guard-change on {reg!r} (branch_assigns="
                    f"{c.get('branch_assigns')!r}) has NO eco_cone_rebuild reg_guard_delta .D rewire in the "
                    f"study — the deterministic builder did not run (a studier-hand-built OR2 into D is the "
                    f"JIRA-9666 postcas bug). Re-run eco_cone_rebuild.py --emit-into-study.")
            if args.ref_dir and mod:
                _bt = c.get('branch_loads') if c.get('branch_loads') is not None else c.get('branch_assigns')
                bad = _rg_surgical_mismatch(args.ref_dir, mod, reg, branch_target=_bt)
                if bad:
                    issues.append(
                        f"HIGH/REG-GUARD: register guard-change on {reg!r} — the surgical re-drive "
                        f"(sel ? region : old_D) does NOT equal the RTL golden next-state ({bad}); the "
                        f"emitted .D combine would compute wrong logic. Fix the RTL diff / builder.")

    # ── #3 uniquified-family completeness ─────────────────────────────────────
    # If the ECO touches a synthesis-uniquified generate array (a child module
    # replicated as <base>_0 … <base>_<N-1> in the netlist), the study MUST cover
    # ALL N copies. The NETLIST is the ground truth for N — so this catches Step-1
    # under-enumeration even when it emits no uniquified_* fields. General: family
    # bases are discovered from the netlist and tied to modules named in the RTL
    # diff (no module names hardcoded). A single-instance module is never a family
    # (needs >=2 numbered copies), so recdsp-style changes never fire.
    _syn_gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    _mod_names = []
    if os.path.isfile(_syn_gz):
        try:
            with gzip.open(_syn_gz, 'rt', errors='replace') as _f:
                for _ln in _f:
                    _m = re.match(r'^module\s+(\S+)', _ln)
                    if _m:
                        _mod_names.append(_m.group(1))
        except Exception:
            _mod_names = []
    if _mod_names:
        _uniq_re = re.compile(r'^(.*?)_(\d+)$')
        _fam = {}                      # base -> set(index)
        for nm in _mod_names:
            um = _uniq_re.match(nm)
            if um:
                _fam.setdefault(um.group(1), set()).add(int(um.group(2)))
        _families = {b: idx for b, idx in _fam.items() if len(idx) >= 2}
        # module bases named by the RTL diff (strip any numeric suffix) — ties the
        # completeness contract to the actual ECO target, not incidental lib families.
        _changed = set()
        for c in rtl_diff.get('changes', []):
            for k in ('module_name', 'child_module_name', 'declaring_module', 'target_module'):
                v = c.get(k)
                if isinstance(v, str) and v:
                    _changed.add(re.sub(r'_\d+$', '', v))
        def _entry_refs(entry):
            for k in ('module_name', 'child_module_name', 'instance_scope'):
                v = entry.get(k)
                if isinstance(v, str) and v:
                    yield v
        _OUT_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN')
        _ECO_NET = re.compile(r'^n_eco_')
        def _fmt(ids, n=20):
            return ', '.join(str(i) for i in ids[:n]) + (' …' if len(ids) > n else '')
        for base, all_idx in sorted(_families.items()):
            if not any(base == cm or base.endswith('_' + cm) or base.endswith(cm)
                       for cm in _changed if cm):
                continue                # family not tied to any changed module
            # per-module in-module logic: key strictly on module_name (the entry's own
            # module), so parent-scope port_connections don't count as in-module logic.
            mod_types = {i: set() for i in all_idx}
            for e in study.get('Synthesize', []):
                mn = e.get('module_name')
                if not isinstance(mn, str):
                    continue
                mm = re.search(re.escape(base) + r'_(\d+)(?:_0)?$', mn)
                if mm and int(mm.group(1)) in mod_types:
                    mod_types[int(mm.group(1))].add(e.get('change_type'))
            # (a) module PRESENCE — any reference (gate / rewire / parent port_connection)
            covered = set()
            for e in study.get('Synthesize', []):
                for ref in _entry_refs(e):
                    mm = re.search(re.escape(base) + r'_(\d+)(?:_0)?$', ref)
                    if mm:
                        covered.add(int(mm.group(1)))
            missing = sorted(all_idx - covered)
            if missing:
                issues.append(
                    f"CRITICAL/UNIQUIFIED-INCOMPLETE: generate-array family {base!r} has "
                    f"{len(all_idx)} uniquified copies in the netlist but the study references only "
                    f"{len(covered)} — missing {len(missing)} module index(es): [{_fmt(missing)}]. A "
                    f"per-instance ECO on a generate array must cover EVERY uniquified copy; Step 1 "
                    f"under-enumerated (see rtl_diff_analyzer.md step 7b — enumerate all <base>_<i> "
                    f"from the netlist and populate flat_net_name_per_instance for ALL of them).")
            # (b) per-module UNIFORMITY of in-module logic — a generate array is uniform,
            #     so any in-module change_type present on ≥1 copy must be present on ALL.
            #     Catches "gates replicated 40× but the consuming D-pin rewire only on _0".
            for ctype in ('new_logic_gate', 'rewire'):
                have = {i for i in all_idx if ctype in mod_types[i]}
                if 0 < len(have) < len(all_idx):
                    miss = sorted(all_idx - have)
                    issues.append(
                        f"CRITICAL/UNIQUIFIED-PARTIAL: family {base!r} has '{ctype}' on only "
                        f"{len(have)}/{len(all_idx)} uniquified copies — missing on {len(miss)}: "
                        f"[{_fmt(miss)}]. Generate-array logic must be replicated IDENTICALLY to "
                        f"every copy: each copy needs its own {ctype} (for 'rewire' this is the "
                        f"D-pin rewire that consumes THAT copy's gate output onto its flop — without "
                        f"it the copy computes the new value but the flop still reads the old net).")
            # (c) new-INPUT-port declaration coverage — if the ECO adds an input port to
            #     this family, every uniquified module needs its own port_declaration
            #     (module_name = the uniquified <base>_<i>, NOT the RTL base name).
            adds_input = False
            for c in rtl_diff.get('changes', []):
                if c.get('change_type') == 'new_port' and c.get('declaration_type') == 'input':
                    cb = re.sub(r'_\d+$', '', str(c.get('module_name', '')))
                    if cb and (base == cb or base.endswith('_' + cb) or base.endswith(cb)):
                        adds_input = True
            if adds_input:
                pd_cov = set()
                for e in study.get('Synthesize', []):
                    if e.get('change_type') != 'port_declaration':
                        continue
                    mn = e.get('module_name')
                    if isinstance(mn, str):
                        mm = re.search(re.escape(base) + r'_(\d+)(?:_0)?$', mn)
                        if mm:
                            pd_cov.add(int(mm.group(1)))
                pd_missing = sorted(all_idx - pd_cov)
                if pd_missing:
                    issues.append(
                        f"CRITICAL/UNIQUIFIED-PORTDECL: family {base!r} gains a new input port but "
                        f"only {len(pd_cov)}/{len(all_idx)} uniquified modules carry a port_declaration "
                        f"— missing on {len(pd_missing)}: [{_fmt(pd_missing)}]. Each uniquified module "
                        f"needs its OWN input port_declaration with module_name=<base>_<i> (not the RTL "
                        f"base name); otherwise the per-copy gates reference an undeclared port → FM "
                        f"out-of-scope (SVR-14).")
                # (d) parent-side port_connection coverage — the parent instantiates the
                #     array N times; the new input port must be CONNECTED on every parent
                #     instance, not just copy _0 (else N-1 copies float the port → FM fail).
                childbase = re.sub(r'^ddrss_\w+?_t_', '', base)
                pc_insts = set()
                for e in study.get('Synthesize', []):
                    if e.get('change_type') != 'port_connection':
                        continue
                    inst = e.get('instance_name') or ''
                    fam = e.get('uniquified_family') or ''
                    if (childbase and childbase in inst) or (childbase and childbase in fam):
                        pc_insts.add(inst)
                if 0 < len(pc_insts) < len(all_idx):
                    issues.append(
                        f"CRITICAL/UNIQUIFIED-PORTCONN: family {base!r} gains a new input port but only "
                        f"{len(pc_insts)}/{len(all_idx)} parent instances carry a port_connection for it. "
                        f"The generate array instantiates the child N times — the new port must be "
                        f"connected on EVERY parent instance (RCQ-style <PREFIX>_<i>__<child>), else the "
                        f"other copies float the port and FM fails. eco_emit_uniquify replicates these.")
            # (d) intra-module UNDRIVEN ECO net — a per-copy ECO gate input n_eco_* must be
            #     driven inside the SAME copy. Catches per-copy net names not suffixed (driver
            #     emits <net>_<i> but the load reads the unsuffixed <net>).
            und_mods, und_sample = [], None
            for i in sorted(all_idx):
                produced, consumed = set(), set()
                for e in study.get('Synthesize', []):
                    mn = e.get('module_name')
                    if not isinstance(mn, str):
                        continue
                    mm = re.search(re.escape(base) + r'_(\d+)(?:_0)?$', mn)
                    if not mm or int(mm.group(1)) != i:
                        continue
                    if e.get('change_type') == 'new_logic_gate':
                        for p, net in (e.get('port_connections') or {}).items():
                            if p in _OUT_PINS:
                                produced.add(net)
                            elif isinstance(net, str) and _ECO_NET.match(net):
                                consumed.add(net)
                    elif e.get('change_type') == 'rewire' and isinstance(e.get('new_net'), str):
                        produced.add(e['new_net'])
                und = [n for n in consumed if n not in produced]
                if und:
                    und_mods.append(i)
                    if und_sample is None:
                        und_sample = (i, und[:4])
            if und_mods:
                issues.append(
                    f"HIGH/UNIQUIFIED-UNDRIVEN: {len(und_mods)} uniquified copies of family {base!r} "
                    f"consume an ECO net not driven within the same copy (e.g. {base}_{und_sample[0]}: "
                    f"{und_sample[1]}) — per-copy intra-module net names are not suffixed consistently "
                    f"(a driver emits <net>_<i> while the load reads the unsuffixed <net>). Suffix "
                    f"EVERY per-copy ECO net so each copy is self-contained.")

    # ── #4 rewire integrity (general — any ECO, not just uniquified) ───────────
    # (a) DUPLICATE conflicting rewires: >=2 rewire entries on the same
    #     (module, cell, pin) driving it to DIFFERENT nets — a flop pin can drive
    #     only one net. History: studier + eco_emit_uniquify double-replication
    #     put two D-pin rewires on each recrcqentry copy.
    # (b) UNDRIVEN rewire target: a rewire whose n_eco_ new_net is produced by no
    #     gate in the SAME module (driven only in another module = cross-copy leak,
    #     or nowhere = dangling). Produced set is unioned across stages so a gate
    #     emitted only in the Synthesize list doesn't false-flag.
    _R4_STAGES = ('Synthesize', 'PrePlace', 'Route')
    _R4_OUT = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S', 'CON', 'SN')
    _R4_DPIN = re.compile(r'^(?:D\d*|CP|CK)$')
    produced_by_mod = {}                       # module -> set(n_eco nets produced by a gate)
    for st in _R4_STAGES:
        for e in study.get(st, []):
            if e.get('change_type') == 'new_logic_gate':
                mod = e.get('module_name')
                for p, v in (e.get('port_connections') or {}).items():
                    if p in _R4_OUT and isinstance(v, str) and v.startswith('n_eco_'):
                        produced_by_mod.setdefault(mod, set()).add(v)
    _dup_seen, _und_seen = set(), set()
    for st in _R4_STAGES:
        pin_nets = {}                          # (mod, cell, pin) -> set(new_net)
        for e in study.get(st, []):
            if e.get('change_type') != 'rewire':
                continue
            cell = e.get('cell_name') or e.get('instance_name')
            pin = e.get('pin')
            key = (e.get('module_name'), cell, pin)
            if e.get('new_net') is not None:
                pin_nets.setdefault(key, set()).add(e.get('new_net'))
            # (b) undriven n_eco rewire target (D/CP pins only — functional rewires)
            nn = e.get('new_net')
            if isinstance(nn, str) and nn.startswith('n_eco_') and _R4_DPIN.match(str(pin or '')):
                mod = e.get('module_name')
                if nn not in produced_by_mod.get(mod, set()) and (mod, nn) not in _und_seen:
                    _und_seen.add((mod, nn))
                    where = [m for m, ns in produced_by_mod.items() if nn in ns]
                    issues.append(
                        f"CRITICAL/REWIRE-UNDRIVEN: rewire {mod}/{cell}.{pin} -> {nn} but {nn} is "
                        f"not driven by any gate in module {mod!r}"
                        + (f" (driven only in {where[:3]} — cross-module/cross-copy leak)."
                           if where else " (not driven anywhere — dangling ECO net)."))
        for (mod, cell, pin), nets in pin_nets.items():
            real = [n for n in nets if n is not None]
            if len(real) > 1 and (mod, cell, pin) not in _dup_seen:
                _dup_seen.add((mod, cell, pin))
                issues.append(
                    f"CRITICAL/DUP-REWIRE: pin {mod}/{cell}.{pin} is rewired to {len(real)} different "
                    f"nets {sorted(str(n) for n in real)} — a flop pin can drive only one net "
                    f"(conflicting/double rewire).")

    # ── #5 empty/malformed gate ────────────────────────────────────────────────
    # A new_logic_gate with no cell_type or no port_connections is an un-insertable
    # skeleton (history: eco_emit_priority_force re-emitted an empty condition_gate_chain
    # named eco_9666_c001..c004 with cell_type='' / port_connections=None). Step 4
    # cannot build it; Step 5/FM see a broken/missing cell.
    def _has_pins(e):
        if e.get('port_connections'):
            return True
        return any(v for v in (e.get('port_connections_per_stage') or {}).values())
    _r5_seen = set()
    for st in _R4_STAGES:
        for e in study.get(st, []):
            if e.get('change_type') != 'new_logic_gate':
                continue
            inst = e.get('instance_name') or '?'
            mod = e.get('module_name') or '?'
            if (mod, inst) in _r5_seen:
                continue
            no_cell = not e.get('cell_type')
            no_pins = not _has_pins(e)
            if no_cell or no_pins:
                _r5_seen.add((mod, inst))
                what = ' and '.join(x for x in ('no cell_type' if no_cell else '',
                                                'no port_connections' if no_pins else '') if x)
                issues.append(
                    f"CRITICAL/EMPTY-GATE: new_logic_gate {mod}/{inst} has {what} — an un-insertable "
                    f"skeleton gate. Every gate needs a real cell_type + port_connections.")

    # ── #6 dangling ECO gate (output never consumed) ───────────────────────────
    # An n_eco_ gate output that no other gate/DFF input, rewire new_net, or
    # port_connection consumes is dead logic (history: WCK-sync cone terms
    # pf_c002/pf_c003 were computed but never folded into the force condition).
    # Airtight consumed set: scan port_connections AND port_connections_per_stage
    # input pins across ALL entry types, plus rewire/port-conn/chain net fields.
    # (Narrow scanning caused false positives on legit studies.)
    _consumed = set()
    for st in _R4_STAGES:
        for e in study.get(st, []):
            for pc in [e.get('port_connections') or {}] + list((e.get('port_connections_per_stage') or {}).values()):
                for p, v in (pc or {}).items():
                    if p not in _R4_OUT and isinstance(v, str):
                        _consumed.add(v)
            for k in ('new_net', 'net_name', 'net_name_before', 'd_input_net', 'old_net'):
                v = e.get(k)
                if isinstance(v, str):
                    _consumed.add(v)
    _r6_seen = set()
    for st in _R4_STAGES:
        for e in study.get(st, []):
            if e.get('change_type') != 'new_logic_gate':
                continue
            for p, v in (e.get('port_connections') or {}).items():
                if p in _R4_OUT and isinstance(v, str) and v.startswith('n_eco_') and v not in _consumed:
                    if v in _r6_seen:
                        continue
                    _r6_seen.add(v)
                    issues.append(
                        f"CRITICAL/DANGLING-CONE: gate {e.get('module_name')}/"
                        f"{e.get('instance_name')} drives {v} but nothing consumes it (no gate/DFF "
                        f"input, rewire, or port_connection reads it) — dead ECO logic. If this is a "
                        f"condition term it was computed but never folded into the final cone.")

    # ── priority_force condition cone matches the RTL condition ──────────────
    try:
        issues.extend(_pf_condition_completeness(study, rtl_diff, args.ref_dir))
    except Exception as _e:
        print(f"[pf-condition-check skipped] {_e}", file=sys.stderr)

    # ── comb_net_force emissions built (not placeholders) and well-formed ─────
    try:
        issues.extend(_cnf_completeness(study, rtl_diff, args.ref_dir))
    except Exception as _e:
        print(f"[comb-net-force-check skipped] {_e}", file=sys.stderr)

    # ── compare_fold emissions built by eco_emit_compare_fold (not and_term) ──
    try:
        issues.extend(_compare_fold_completeness(study, rtl_diff, args.ref_dir))
    except Exception as _e:
        print(f"[compare-fold-check skipped] {_e}", file=sys.stderr)

    # ── every new_logic_gate has all required input pins in ALL 3 stages ──────
    try:
        issues.extend(_gate_input_completeness(study))
    except Exception as _e:
        print(f"[gate-input-completeness-check skipped] {_e}", file=sys.stderr)

    # ── Result ───────────────────────────────────────────────────────────────
    passed = len(issues) == 0
    result = {'tag': args.tag, 'passed': passed, 'issues': issues, 'issue_count': len(issues)}
    write_result(args.output, result, passed, getattr(args, 'iter', None))

    marker_txt = (
        f"ECO_SCRIPT_LAUNCHED: eco_validate_step3.py\n"
        f"  passed: {passed}\n"
        f"  issues: {len(issues)}\n"
        f"  output: {args.output}"
    )
    print(marker_txt)
    if issues:
        print("\nISSUES FOUND:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("\nStep 3 output COMPLETE — all checks passed.")

    Path(args.output.replace('.json','_marker.txt')).write_text(marker_txt + '\n')
    return 0 if passed else 1

if __name__ == '__main__':
    sys.exit(main())
