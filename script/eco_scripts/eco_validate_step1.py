#!/usr/bin/env python3
"""
eco_validate_step1.py — Deterministic Step 1 validator for eco_rtl_diff.json.
Runs as the post-rtl_diff_analyzer self-check; fails the orchestrator on any
defect so wrong RTL-diff data never reaches Step 2/3/4.

Checks performed:
  - MUX-select polarity (D-MUX-6 cross-checks 1-5; the original purpose)
  - Phantom WIRE/BUF pseudo-cells in any gate chain
  - new_port hygiene (declaration_type set; no duplicates)
  - port_connection completeness (inst/port/net populated)
  - Cell truth-table match: every gate in d_input_gate_chain /
    new_condition_gate_chain has preeco_cell_type whose actual boolean function
    equals the claimed gate_function (per script/eco_scripts/cell_libraries/*.json)

Usage:
    python3 script/eco_scripts/eco_validate_step1.py \
        --rtl-diff data/<TAG>_eco_rtl_diff.json \
        --output   data/<TAG>_eco_validate_step1.json

Exit: 0 = all wire_swap entries pass, 1 = any failure.
"""
import argparse, gzip, json, os, re, sys
from eco_validate_io import write_result
try:
    from eco_extract_pf_condition import (extract_condition, resolve_rtl,
                                          extract_added_branch_condition, _leaves)
except Exception:
    extract_condition = resolve_rtl = extract_added_branch_condition = _leaves = None
try:
    from eco_emit_priority_force import _local_defs
except Exception:
    _local_defs = None


_PF_MODBODY_CACHE = {}


def _mod_key(n):
    """Canonical module key: strip tile prefix (ddrss_*_t_) + uniquify suffix (_<i>)
    so a change's short name matches the netlist's prefixed/uniquified name."""
    return re.sub(r'_\d+$', '', re.sub(r'^ddrss_\w+?_t_', '', str(n or '')))


def _module_netlist_body(ref_dir, module):
    """PreEco Synthesize netlist body of <module> — tolerant of the tile prefix and
    uniquify suffix (the AI names modules inconsistently short vs full)."""
    key = ('nl', ref_dir, module)
    if key in _PF_MODBODY_CACHE:
        return _PF_MODBODY_CACHE[key]
    gz = os.path.join(ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
    want = _mod_key(module)
    body, grab = [], False
    if os.path.isfile(gz):
        try:
            with gzip.open(gz, 'rt', errors='replace') as f:
                for ln in f:
                    mm = re.match(r'^module\s+(\S+)', ln)
                    if mm:
                        grab = _mod_key(mm.group(1)) == want
                    if grab:
                        body.append(ln)
                        if ln.lstrip().startswith('endmodule'):
                            grab = False
        except Exception:
            body = []
    txt = ''.join(body)
    _PF_MODBODY_CACHE[key] = txt
    return txt


def _pf_condition_leaf_issues(rtl_diff, ref_dir):
    """For each priority_force, anchor the RTL condition (via const_macro) and verify
    every leaf signal is AVAILABLE in the target module: present in the netlist, OR a
    local wire/assign in the module RTL (decomposable), OR threaded in by a new_port
    in this rtl_diff. A leaf that is none of these — a signal computed in another
    module but absent from the target — is genuinely missing; the condition cannot use
    it until it is ported in. Requires --ref-dir + the extractor; else returns []."""
    if not (ref_dir and extract_added_branch_condition and resolve_rtl):
        return []
    issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'priority_force':
            continue
        module = c.get('module_name') or ''
        base = re.sub(r'^ddrss_\w+?_t_', '', module)
        rtl = resolve_rtl(ref_dir=ref_dir, module=base)   # old RTL — for local-def decomposition
        if not rtl:
            continue
        # GROUND TRUTH: the ECO adds a branch — its condition is the guard present in the
        # NEW RTL (data/SynRtl) but ABSENT in the OLD (data/PreEco/SynRtl). Anchor on a
        # const_macro forced signal. The AI's condition_expr is only a fallback.
        anchor = next((f for f in (c.get('forced_signals') or []) if f.get('const_macro')), None)
        if not anchor:
            continue
        added = extract_added_branch_condition(ref_dir, base, anchor['signal'], anchor['const_macro'])
        if len(added) > 1:
            issues.append(
                f"changes[{idx}] priority_force: the ECO diff adds {len(added)} branches assigning "
                f"{anchor['signal']}={anchor['const_macro']} — ambiguous; cannot uniquely anchor.")
            continue
        if len(added) == 1:
            leaves = added[0]['leaves']
        else:
            # no added branch found in the diff — validate the AI's condition_expr leaves
            cond_expr = c.get('condition_expr')
            if not cond_expr or not _leaves:
                continue
            leaves = _leaves(cond_expr)
        nl = _module_netlist_body(ref_dir, module)
        rtltxt = open(rtl, errors='replace').read()
        # local combinational defs (wire/assign/always@*) that synthesis flattened —
        # these are decomposable to real leaves, NOT missing. Base-index so a per-bit
        # reg (WckIsInSync[0]=...) makes the bare bus 'WckIsInSync' decomposable.
        ld = _local_defs(rtltxt) if _local_defs else {}
        ld_bases = set(ld)
        for k in ld:
            b = re.match(r'^(\w+)\[', k)
            if b:
                ld_bases.add(b.group(1))
        # new_port tokens threaded into this module by the ECO
        ported = {ch.get('new_token') for ch in rtl_diff.get('changes', [])
                  if ch.get('change_type') == 'new_port'
                  and re.sub(r'^ddrss_\w+?_t_', '', str(ch.get('module_name') or '')) == base}
        missing = []
        for leaf in leaves:
            if leaf.startswith('`'):
                continue                                   # macro field index, not a signal
            if re.search(r'\b' + re.escape(leaf) + r'\b', nl):
                continue                                   # present in netlist
            if leaf in ld_bases:
                continue                                   # local wire/assign/always@* => decomposable
            # fallback: local wire/assign in module RTL (uncommented)
            if re.search(r'^\s*(?:wire\b[^;]*\b|assign\s+)' + re.escape(leaf) + r'\b', rtltxt, re.MULTILINE):
                continue
            if leaf in ported:
                continue                                   # being threaded in by a new_port
            missing.append(leaf)
        if missing:
            issues.append(
                f"changes[{idx}] priority_force condition references signal(s) {sorted(missing)} "
                f"NOT available in module {module!r} (absent from the netlist, not a local wire, and "
                f"not threaded in by a new_port). The condition cannot be built until they are sourced "
                f"— add a new_port + port_connection to bring each signal into the module from wherever "
                f"it is computed. Do NOT reconstruct it from a different available signal.")
    return issues


def _is_logic_line(s):
    """True if a diff line is real RTL logic (not a comment / date / drop-path banner)."""
    s = re.sub(r'//.*$', '', s).strip()
    if not s or s.startswith('*') or s.startswith('/*') or s.startswith('*/'):
        return False
    if re.search(r'Date of Generation|/proj/|drops/|perlog|Generated by|Generation :', s):
        return False
    # must contain something that looks like Verilog logic
    return bool(re.search(r'[=&|]|(?<![\w.])(?:if|else|begin|input|output|inout|wire|reg|'
                          r'assign|always)\b|\.\w+\s*\(', s))


def _count_logic_hunks(diff_text):
    """Count contiguous diff hunks that change REAL logic (skip pure comment/date/path
    hunks). Normal `diff` output: hunks start with a `<n>[acd]<n>` header."""
    n, cur_has_logic, in_hunk = 0, False, False
    for ln in diff_text.split('\n'):
        if re.match(r'^\d+(,\d+)?[acd]\d+(,\d+)?$', ln):
            if in_hunk and cur_has_logic:
                n += 1
            in_hunk, cur_has_logic = True, False
        elif ln.startswith('<') or ln.startswith('>'):
            if _is_logic_line(ln[1:]):
                cur_has_logic = True
    if in_hunk and cur_has_logic:
        n += 1
    return n


def _module_base(name):
    """Strip tile prefix (ddrss_*_t_) and uniquify suffix (_<i>) -> canonical base."""
    if not name:
        return ''
    b = re.sub(r'^ddrss_\w+?_t_', '', str(name))
    return re.sub(r'_\d+$', '', b)


def _hunk_completeness_issues(rtl_diff, ref_dir):
    """FAIL-CLOSED completeness: every changed LOGIC hunk in the PreEco/SynRtl->SynRtl
    diff must be represented by a change in the rtl_diff. Counts real-code hunks per
    logic file and compares to the rtl_diff changes attributed to that module. Under-
    capture (fewer changes than hunks) means an edit was MISSED — e.g. a modified
    branch guard that shares tokens with another change and is easy to overlook."""
    import subprocess
    pre = os.path.join(ref_dir, 'data', 'PreEco', 'SynRtl')
    new = os.path.join(ref_dir, 'data', 'SynRtl')
    if not (os.path.isdir(pre) and os.path.isdir(new)):
        return []
    try:
        listing = subprocess.run(
            ['diff', '-rq', '--exclude=*.vf', '--exclude=*.vfe', '--exclude=*.d', pre, new],
            capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return []
    issues = []
    for line in listing.splitlines():
        m = re.match(r'Files (\S+) and (\S+) differ', line)
        if not m:
            continue
        pf, nf = m.group(1), m.group(2)
        fn = os.path.basename(nf)
        if not (fn.endswith('.v') or fn.endswith('.sv')):
            continue                                    # skip .vh config headers
        try:
            dtext = subprocess.run(['diff', pf, nf], capture_output=True, text=True,
                                   timeout=60).stdout
        except Exception:
            continue
        hunks = _count_logic_hunks(dtext)
        if hunks == 0:
            continue                                    # comment/date/path noise only
        mb = re.sub(r'^rtl_', '', re.sub(r'\.s?v$', '', fn))
        nchanges = sum(1 for c in rtl_diff.get('changes', [])
                       if _module_base(c.get('module_name')) == mb
                       or _module_base(c.get('uniquified_family')) == mb)
        if nchanges < hunks:
            issues.append(
                f"HUNK-COMPLETENESS: {fn} has {hunks} changed logic hunk(s) in the RTL diff but the "
                f"rtl_diff captured only {nchanges} change(s) for module {mb!r} — an edit was likely "
                f"MISSED (a modified branch guard / condition change is easy to overlook when it shares "
                f"signals with another change). Re-diff PreEco/SynRtl vs SynRtl for this file and add a "
                f"change for every hunk (a tightened/broadened existing-branch guard is an enable_swap/"
                f"and_term, NOT a priority_force).")
    return issues


def _bus_width_int(bw):
    """Normalize a bus_width field (int, 'msb:lsb' string, '[N:0]', or None) to a bit
    count. None/absent => scalar (1)."""
    if bw is None:
        return 1
    if isinstance(bw, int):
        return bw if bw > 0 else 1
    s = str(bw).strip()
    m = re.match(r'^\[?\s*(\d+)\s*:\s*(\d+)\s*\]?$', s)
    if m:
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    return int(s) if s.isdigit() else 1


def _rtl_port_width(ref_dir, module, port):
    """Declared bit-width of `port` in the module's NEW RTL (data/SynRtl). Returns an
    int (1 for scalar) or None if the RTL/declaration can't be found."""
    if not (ref_dir and resolve_rtl and port):
        return None
    base = re.sub(r'^ddrss_\w+?_t_', '', module or '')
    rtl = resolve_rtl(ref_dir=ref_dir, module=base, subdir='SynRtl')
    if not rtl:
        return None
    try:
        txt = open(rtl, errors='replace').read()
    except Exception:
        return None
    m = re.search(r'\b(?:input|output|inout|wire|reg)\b\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*'
                  + re.escape(port) + r'\b', txt)
    if not m:
        return None
    return abs(int(m.group(1)) - int(m.group(2))) + 1 if m.group(1) else 1


def _pf_const_macro_issues(rtl_diff):
    """FAIL-CLOSED: a priority_force that pins a signal to a MULTI-BIT constant (an
    opcode/enum, e.g. 5'b01011) MUST carry `const_macro` naming the RTL macro. Without
    it (a) the constant cannot be verified against the RTL `define (a wrong-but-valid
    opcode passes every schema check) and (b) the condition cannot be anchored to the
    correct RTL branch — the builder then trusts the AI's stored condition_expr, which
    may be the wrong branch. Trivial 1-bit forces (1'b0/1'b1) do not need a macro.
    Runs with NO ref-dir dependency so the gate is always on."""
    issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'priority_force':
            continue
        for f in (c.get('forced_signals') or []):
            m = re.match(r"^\s*(\d+)'[bB]([01xzXZ_]+)\s*$", str(f.get('const', '')))
            width = int(m.group(1)) if m else None
            if width and width > 1 and not f.get('const_macro'):
                issues.append(
                    f"changes[{idx}] priority_force forces {f.get('signal')!r} to a {width}-bit "
                    f"constant {f.get('const')!r} but const_macro is not set. A multi-bit opcode/enum "
                    f"MUST name its RTL macro so the value is verified against the `define AND the "
                    f"condition is anchored to the correct RTL branch (else the builder silently trusts "
                    f"the stored condition_expr). Set const_macro to the RTL macro name.")
    return issues

# Prefixes that mean the cell's output goes LOW when its inputs go HIGH.
# Keep generic — covers TSMC/AMD/GF library naming conventions.
INVERTING_PREFIXES = ('XNOR', 'XNR', 'NAND', 'NOR', 'INR', 'INV', 'IND', 'ND', 'NR')
BACKTRACK_PHRASES  = ('wait', 'actually', 're-analyz', 'correcting', 'inverts')

# Gate output when ALL combinational inputs are at logic 1.
# Used in Check 4 only when the new condition is a pure AND/OR/NAND/NOR of the
# gate's inputs — covers ~95% of MUX-select rewires.
GATE_OUT_WHEN_INPUTS_HIGH = {
    'AND2': 1, 'AND3': 1, 'AND4': 1, 'AN2': 1, 'AN3': 1, 'AN4': 1,
    'OR2':  1, 'OR3':  1, 'OR4':  1,
    'NAND2': 0, 'NAND3': 0, 'NAND4': 0, 'ND2': 0, 'ND3': 0, 'ND4': 0,
    'NOR2':  0, 'NOR3':  0, 'NOR4':  0, 'NR2': 0, 'NR3': 0, 'NR4': 0,
    'INV': 0, 'BUF': 1, 'XOR2': 0, 'XNOR2': 1,
}


def is_inverting(cell_type):
    """True if first uppercase token of cell_type starts with an inverting prefix."""
    if not cell_type:
        return None
    m = re.match(r'^([A-Z]+)', cell_type)
    if not m:
        return None
    prefix = m.group(1)
    # Longest match wins so 'NAND' isn't misread as starting with 'NA'.
    return any(prefix.startswith(p) for p in sorted(INVERTING_PREFIXES, key=len, reverse=True))


def evaluate_condition_at_inputs_high(expr):
    """
    Return condition value when every bare input signal is logic 1.
    Supports ~, &, |, parentheses. Returns None if expression too complex.
    """
    if not expr:
        return None
    # Strip whitespace; substitute every bare identifier with '1'
    e = re.sub(r'[A-Za-z_][A-Za-z_0-9\[\]]*', '1', expr)
    # Translate Verilog operators to Python
    e = e.replace('~', ' not ').replace('&&', ' and ').replace('||', ' or ')
    e = e.replace('&', ' and ').replace('|', ' or ')
    try:
        return int(bool(eval(e, {'__builtins__': {}}, {})))
    except Exception:
        return None


def check_entry(entry):
    """Run the 5 cross-checks on one wire_swap entry. Return list of issues."""
    issues = []
    cell  = entry.get('mux_select_old_driver_cell_type')
    inv   = entry.get('mux_select_old_driver_inverting')
    s_val = entry.get('mux_select_old_S_when_condition_true')
    branch = entry.get('mux_select_branch_true_on')
    gate  = entry.get('mux_select_gate_function')
    rsn   = (entry.get('mux_select_reasoning') or '').lower()

    if cell is None or inv is None or s_val is None or branch is None:
        issues.append('MISSING_FIELDS: D-MUX-3/4/5 derivation fields not recorded — re-run Step 1')
        return issues

    # 1. cell type prefix vs inverting flag
    exp_inv = is_inverting(cell)
    if exp_inv is None:
        issues.append(f'CHECK1: cannot parse cell_type prefix from {cell!r}')
    elif exp_inv != bool(inv):
        issues.append(f'CHECK1: cell_type {cell} prefix is_inverting={exp_inv} but flag={inv}')

    # 2. S follows inverting flag
    exp_s = 0 if inv else 1
    if s_val != exp_s:
        issues.append(f'CHECK2: inverting={inv} requires old_S={exp_s} but field={s_val}')

    # 3. branch follows S
    exp_branch = 'I0' if s_val == 0 else 'I1'
    if branch != exp_branch:
        issues.append(f'CHECK3: old_S={s_val} requires branch_true_on={exp_branch} but field={branch}')

    # 4. gate function output @ all-inputs-high equals required new S (best effort)
    if gate in GATE_OUT_WHEN_INPUTS_HIGH:
        # The "condition TRUE" case is normally the all-inputs-high case for
        # AND-style conditions. For ~A|~B style conditions the agent should
        # have inverted the polarity decision in D-MUX-4 (gate becomes AND).
        # If the gate output at all-inputs-high == required_S, the gate's TRUE
        # case is NOT the all-inputs-high case — that means the new condition
        # is something other than a pure AND of all inputs and we need the
        # actual condition expression to evaluate. We attempt that next.
        gate_at_high = GATE_OUT_WHEN_INPUTS_HIGH[gate]
        # Try to read the condition expression from a context_line / reasoning
        cond_expr = entry.get('context_line', '') or ''
        m = re.search(r'\(([^?)]+)\)\s*\?', cond_expr)
        new_cond_at_high = evaluate_condition_at_inputs_high(m.group(1)) if m else None
        if new_cond_at_high is not None:
            # When gate output AT condition=TRUE must equal s_val, and we have
            # the condition's value at inputs-high, we know what gate output to
            # require at inputs-high: must equal s_val if condition_at_high==1,
            # or != s_val if condition_at_high==0 (in which case any gate
            # behaviour at inputs-high is acceptable; skip).
            if new_cond_at_high == 1 and gate_at_high != s_val:
                issues.append(
                    f'CHECK4: gate {gate} outputs {gate_at_high} at inputs=high; '
                    f'new condition=TRUE at inputs=high requires gate output={s_val}'
                )

    # 5. reasoning stability
    bad = [w for w in BACKTRACK_PHRASES if w in rsn]
    if bad:
        issues.append(f'CHECK5: reasoning contains backtracking phrases {bad} — derivation unstable')

    return issues


def signals_in_module(text, module_name):
    """Return set of signal names visible in the given module: ports + wire decls
    + cell output nets. Uses comment-stripped Verilog text."""
    m = re.search(rf'^module\s+{re.escape(module_name)}(?:_0)?\b.*?^endmodule\b',
                  text, re.MULTILINE | re.DOTALL)
    if not m:
        return set()
    body = m.group(0)
    sigs = set()
    # Ports: `input [...] foo;` / `output [...] foo;` / `inout [...] foo;`
    for pm in re.finditer(r'^\s*(?:input|output|inout)\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]',
                          body, re.MULTILINE):
        sigs.add(pm.group(1))
    # Wire decls: `wire [...] foo;`
    for wm in re.finditer(r'^\s*wire\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]',
                          body, re.MULTILINE):
        sigs.add(wm.group(1))
    # Cell outputs: any `.Z(net)` / `.ZN(net)` / `.Q(net)` / `.QN(net)` / `.CO(net)`
    for cm in re.finditer(r'\.\s*(?:Z|ZN|ZN1|Q|QN|CO)\s*\(\s*(\w+)\s*\)', body):
        sigs.add(cm.group(1))
    return sigs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rtl-diff', required=True)
    ap.add_argument('--ref-dir',  default=None,
                    help='REF_DIR with data/PreEco/<stage>.v.gz for signal-in-scope check')
    ap.add_argument('--output',   required=True)
    ap.add_argument('--iter', type=int, default=None,
                    help='catch-and-fix iteration number; when set, ALSO write '
                         '<output>_iter<N>.json (history). The canonical --output is '
                         'always written and remains the latest/final result.')
    args = ap.parse_args()

    rtl_diff = json.load(open(args.rtl_diff))
    results, overall_pass = [], True

    # Phantom-cell scan: 'WIRE'/'BUF' as gate_function or cell_type is not a real
    # library cell — emit empty chain instead. Caught in any chain, any change_type.
    phantom = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        for fld in ('d_input_gate_chain', 'new_condition_gate_chain'):
            for g in (c.get(fld) or []):
                if g.get('gate_function') in ('WIRE',) or g.get('cell_type') in ('WIRE',):
                    phantom.append(f'changes[{idx}].{fld} seq={g.get("seq")}: phantom WIRE pseudo-cell — emit empty chain')
    if phantom:
        overall_pass = False

    # new_port hygiene: declaration_type must be set, and (module, signal) must
    # not appear as new_port more than once (catches misclassified wire decls).
    decl_issues, seen = [], {}
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'new_port':
            continue
        dt = c.get('declaration_type')
        if dt is None:
            decl_issues.append(f'changes[{idx}] new_port {c.get("new_token")!r} in {c.get("module_name")!r}: declaration_type is MISSING (MANDATORY — must be input/output/wire)')
        elif dt not in ('input', 'output', 'wire'):
            decl_issues.append(f'changes[{idx}] new_port {c.get("new_token")!r} in {c.get("module_name")!r}: declaration_type={dt!r} (must be input/output/wire)')
        key = (c.get('module_name'), c.get('new_token'))
        if key in seen:
            decl_issues.append(f'changes[{idx}] duplicate new_port for module={key[0]!r} signal={key[1]!r} (first at index {seen[key]})')
        else:
            seen[key] = idx
        # BUS WIDTH must match the NEW-RTL port declaration. A missing/1-bit bus_width
        # on a multi-bit port silently threads only bit 0 (F5: reg_dualdcqenmode is
        # `input [1:0]` but was captured with no bus_width).
        port = c.get('new_token') or c.get('signal_name') or c.get('port_name')
        rtl_w = _rtl_port_width(args.ref_dir, c.get('module_name'), port) if port else None
        if rtl_w and rtl_w > 1:
            got = _bus_width_int(c.get('bus_width'))
            if got != rtl_w:
                decl_issues.append(
                    f"changes[{idx}] new_port {port!r} in {c.get('module_name')!r}: NEW RTL declares it "
                    f"[{rtl_w-1}:0] ({rtl_w}-bit) but bus_width={c.get('bus_width')!r} ({got}-bit). "
                    f"Set bus_width to {rtl_w} — a too-narrow port threads only the low bits.")
    # new_port(output) + flat_net_exists:true → must also query <signal>_d1
    # so studier can trace to the pure combinational source (not DFF D-input).
    nq_paths = {q.get('net_path', '') for q in rtl_diff.get('nets_to_query', [])}
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'new_port':
            continue
        if c.get('declaration_type') != 'output':
            continue
        if not c.get('flat_net_exists'):
            continue
        sig  = c.get('new_token', '')
        insts = c.get('instances') or [None]
        for inst in insts:
            prefix = f'{inst}/' if inst else ''
            d1_path = f'{prefix}{sig}_d1'
            # Accept any nets_to_query entry whose path contains <signal>_d1
            if not any(d1_path in p or f'{sig}_d1' in p for p in nq_paths):
                decl_issues.append(
                    f"changes[{idx}] [FAIL/NEW-PORT-MISSING-D1-QUERY]: "
                    f"new_port(output) '{sig}' has flat_net_exists:true but "
                    f"nets_to_query is missing '{d1_path}'. "
                    f"Add query for <signal>_d1 so Step 2 FM can resolve the "
                    f"pure combinational source per instance. Without it, "
                    f"studier uses DFF D-input wire (which has extra AND gating) "
                    f"as buffer chain source → wrong PhArbFineGater value → FM fail. "
                    f"See rtl_diff_analyzer.md §D new_port(output) rule.")
                overall_pass = False

    if decl_issues:
        overall_pass = False

    # port_connection completeness: every entry must have inst/port/net populated
    # under SOME field name (canonical or alternative). Catches incomplete entries
    # that would silently SKIP in eco_netlist_port_rewire.py.
    pc_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'port_connection':
            continue
        inst = c.get('instance_name') or c.get('submodule_instance')
        port = c.get('port_name')     or c.get('new_token')
        net  = c.get('net_name')      or c.get('flat_net_name')
        if not all([inst, port, net]):
            pc_issues.append(f'changes[{idx}] port_connection in {c.get("module_name")!r}: missing inst={inst!r}/port={port!r}/net={net!r}')
    if pc_issues:
        overall_pass = False

    # Signal-in-scope check: every input signal referenced by a chain entry must
    # exist in the target module — as a port, wire decl, or cell output. Missing
    # signals cause undriven inputs in the inserted gate → FM cone divergence.
    sis_issues = []
    if args.ref_dir:
        import gzip as _gz, os as _os
        # Load Synthesize PreEco module text once (per module — cache by name)
        gz = _os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
        if not _os.path.exists(gz):
            gz = _os.path.join(args.ref_dir, 'data', 'PostEco', 'Synthesize.v.gz')
        if _os.path.exists(gz):
            try:
                with _gz.open(gz, 'rt') as f:
                    raw = f.read()
                raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
                raw = re.sub(r'//[^\n]*', '', raw)
            except Exception:
                raw = ''
            mod_sig_cache = {}
            def _sigs(mod):
                if mod not in mod_sig_cache:
                    mod_sig_cache[mod] = signals_in_module(raw, mod) if raw else set()
                return mod_sig_cache[mod]
            # Collect signals intentionally added by this ECO (new_port / port_connection
            # / port_promotion changes). These won't exist in PreEco — exempting them
            # avoids false positives when a new port is used in a gate chain.
            _eco_new_signals = set()
            for _c in rtl_diff.get('changes', []):
                if _c.get('change_type') in ('new_port', 'port_connection', 'port_promotion'):
                    for _fld in ('new_token', 'signal_name', 'target_register', 'net_name'):
                        _v = _c.get(_fld)
                        if _v and isinstance(_v, str):
                            _eco_new_signals.add(_v)
                            _eco_new_signals.add(re.sub(r'\[[^\]]*\]', '', _v).strip())

            for idx, c in enumerate(rtl_diff.get('changes', [])):
                target_mod = c.get('declaring_module') or c.get('module_name')
                if not target_mod:
                    continue
                sigs = _sigs(target_mod)
                if not sigs:
                    continue  # module not found — silent skip (probably a tile-prefix variant)
                for fld in ('d_input_gate_chain', 'new_condition_gate_chain'):
                    for g in (c.get(fld) or []):
                        for inp in (g.get('inputs') or []):
                            if not isinstance(inp, str):
                                continue
                            base = re.sub(r'\[[^\]]*\]', '', inp).strip()  # strip bit select
                            if not base or base.startswith(('1\'b', '0\'b')):
                                continue
                            if base.startswith('n_eco_'):
                                continue  # internal ECO net, may not yet exist
                            if base in _eco_new_signals:
                                continue  # signal added by this ECO — won't be in PreEco
                            if base not in sigs:
                                # Suggest closest in-scope match (heuristic: same prefix)
                                cand = next((s for s in sigs if s.startswith(base) or base.startswith(s)), None)
                                hint = f' (closest in-scope: {cand!r})' if cand else ''
                                sis_issues.append(
                                    f'changes[{idx}].{fld} seq={g.get("seq")}: input {inp!r} '
                                    f'NOT in scope of module {target_mod!r}{hint}. '
                                    f'Pick the in-scope alias or promote the signal as a new port.')
    if sis_issues:
        overall_pass = False

    # Truth-table check: every gate in d_input_gate_chain / new_condition_gate_chain
    # must have preeco_cell_type whose actual boolean function matches the claimed
    # gate_function. Catches the case where Step 1 picked a cell whose name suggests
    # one logic family but the cell actually computes something different.
    tt_issues = []
    try:
        import os as _os, sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import eco_cell_truth_tables as _ett
    except ImportError:
        _ett = None
    if _ett is not None:
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            for fld in ('d_input_gate_chain', 'new_condition_gate_chain'):
                for g in (c.get(fld) or []):
                    cell = g.get('preeco_cell_type') or g.get('cell_type') or ''
                    fn   = g.get('gate_function') or ''
                    if not cell or not fn:
                        continue
                    m, why = _ett.cell_function_matches(cell, fn, ref_dir=args.ref_dir)
                    if m is False:
                        tt_issues.append(f'changes[{idx}].{fld} seq={g.get("seq")}: cell={cell!r} does NOT compute claimed {fn!r} — {why}')
    if tt_issues:
        overall_pass = False

    # Whole-chain equivalence (Gap E): for every d_input_gate_chain, compose
    # the gates' boolean functions and verify the composed expression matches
    # the RTL spec stored in `d_input_expected_function`. Catches the
    # "individual cells valid but chain composition wrong for the role" class
    # of bug — Step 1 truth-table check (cell vs gate_function) cannot.
    #
    # The reference field `d_input_expected_function` is MANDATORY for any
    # change with a non-empty d_input_gate_chain — without it Gap E cannot
    # verify the chain's boolean intent, leaving a silent gap that allowed
    # the original 9868 INR3+IAOI21 bug through. If missing → HIGH issue
    # (forces rtl_diff_analyzer to re-emit the change with the field).
    chain_eq_issues = []
    try:
        import eco_chain_equivalence as _ece
    except ImportError:
        _ece = None
    if _ece is not None:
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            chain = c.get('d_input_gate_chain') or []
            if not chain:
                continue  # no chain to check
            ref_expr = c.get('d_input_expected_function')
            if not ref_expr:
                # Mandatory reference missing — block flow
                chain_eq_issues.append(
                    f"changes[{idx}] target={c.get('target_register','?')}: "
                    f"d_input_gate_chain present ({len(chain)} gates) but "
                    f"`d_input_expected_function` field MISSING. "
                    f"rtl_diff_analyzer must emit this field — see rtl_diff_analyzer.md "
                    f"'MANDATORY whole-chain equivalence reference field (Gap E)' rule.")
                continue
            dff_d = chain[-1].get('output_net') if chain else None
            if not dff_d:
                continue
            impl_expr, inputs, comp_issues = _ece.compose_chain(chain, dff_d)
            if impl_expr is None:
                chain_eq_issues.append(f"changes[{idx}] target={c.get('target_register','?')}: cannot compose chain — {'; '.join(comp_issues)}")
                continue
            ref_vars = sorted(set(re.findall(r'\b[A-Za-z_]\w*\b', ref_expr)) - {'and','or','not'})
            all_vars = sorted(set(inputs) | set(ref_vars))
            eq, details = _ece.equivalent(impl_expr, ref_expr, all_vars)
            if eq is False:
                preview = '; '.join(f"{combo}→impl={iv},ref={rv}" for combo, iv, rv in details[:3])
                chain_eq_issues.append(
                    f"changes[{idx}] target={c.get('target_register','?')}: chain NOT EQUIVALENT to RTL spec — "
                    f"{len(details)} mismatching combo(s). First: {preview}")
            elif eq is None:
                # Inconclusive (e.g., > 12 inputs); record as warning
                chain_eq_issues.append(
                    f"changes[{idx}] target={c.get('target_register','?')}: chain equivalence INCONCLUSIVE — {details}")
    if chain_eq_issues:
        overall_pass = False

    # ── enable_swap validation ────────────────────────────────────────────────
    # enable_swap changes rewire the clock-enable / write-enable pin of an existing
    # DFF.  Validate that required fields are present.
    enable_swap_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        for f in ('old_enable_net', 'new_enable_net'):
            if not c.get(f):
                enable_swap_issues.append(
                    f"changes[{idx}] target={tgt}: enable_swap missing `{f}` — "
                    f"Step 3 needs both old and new enable net names to rewire the CE pin")
        chain = c.get('new_enable_gate_chain') or []
        new_en = c.get('new_enable_net') or ''
        # Empty chain is valid when new_enable_net is a plain existing signal wired
        # directly to the E/EN pin. Only fail when new_enable_net is an n_eco_*
        # synthesized net — that net MUST be produced by at least one gate in the chain.
        if not chain and new_en.startswith('n_eco_'):
            enable_swap_issues.append(
                f"changes[{idx}] target={tgt}: enable_swap `new_enable_gate_chain` empty "
                f"but new_enable_net={new_en!r} is a synthesized n_eco_* net — "
                f"emit the MUX/AND/OR gates that produce it")
        if not c.get('dff_clock'):
            enable_swap_issues.append(
                f"changes[{idx}] target={tgt}: enable_swap missing `dff_clock` — "
                f"needed by Step 2 fenets to query old_enable_net in correct clock domain scope")
    if enable_swap_issues:
        overall_pass = False

    # ── enable_swap clock gate detection field ────────────────────────────────
    # rtl_diff_analyzer Step 0 (Phase 0.16) must check whether the target DFF's
    # CP is driven by a clock gate cell (ICG*/CKOR*/CTG*).  If so,
    # enable_via_clock_gate=true must be set so the studier uses Path A (clock
    # gate E-pin rewire) instead of Path B (DFF CE-pin rewire).  Path A is
    # immune to wrong-module cell name collisions and serves all bus-width DFF
    # bits with ONE rewire instead of N.  Without this field the studier always
    # falls back to CE-pin rewire — which caused the xbar module corruption in
    # previous rounds.  Require the field to be explicitly set (true or false).
    clk_gate_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        if c.get('enable_via_clock_gate') is None:
            clk_gate_field_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_swap missing "
                f"`enable_via_clock_gate` field (must be true or false). "
                f"rtl_diff_analyzer Step 0 must grep PreEco for a clock gate cell "
                f"(ICG*/CKOR*/CTG*) driving the DFF CP and set this field accordingly. "
                f"Omitting it forces CE-pin rewire (wrong-module risk) even when a "
                f"clock gate exists and should be used instead.")
    if clk_gate_field_issues:
        overall_pass = False

    # ── enable_via_clock_gate=False — verify against PreEco netlist ───────────
    # The analyzer may set enable_via_clock_gate=False even when the target DFF
    # is actually clock-gated.  If --ref-dir is provided, grep the PreEco
    # Synthesize netlist: if clk_gate_<target>_reg exists as a CKOR*/ICG*/CTG*
    # cell, the field is wrong and Step 3 will generate the wrong implementation.
    cg_verify_issues = []
    if args.ref_dir:
        _gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
        if not os.path.exists(_gz):
            _gz = os.path.join(args.ref_dir, 'data', 'PostEco', 'Synthesize.v.gz')
        _CG_TYPE_RE = re.compile(r'^(CKOR|ICG|CTG|CKLNQ|CKGT)', re.I)
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            if c.get('change_type') != 'enable_swap':
                continue
            if c.get('enable_via_clock_gate'):
                continue  # already True — no need to verify
            tgt = c.get('target_register') or c.get('new_token') or '?'
            try:
                import subprocess as _sp
                _r = _sp.run(
                    f'zgrep -E "clk_gate_{re.escape(tgt)}_reg" {_gz}',
                    shell=True, capture_output=True, text=True, timeout=20)
                if _r.returncode == 0 and _r.stdout.strip():
                    for _line in _r.stdout.strip().splitlines():
                        _parts = _line.strip().split()
                        if _parts and _CG_TYPE_RE.match(_parts[0]):
                            cg_verify_issues.append(
                                f"changes[{idx}] target={tgt!r}: enable_via_clock_gate=False "
                                f"but PreEco Synthesize has clock gate cell "
                                f"'clk_gate_{tgt}_reg' ({_parts[0]}). "
                                f"Set enable_via_clock_gate=true and add clock_gate_instance "
                                f"+ dff_cp_net. Step 3 will create a shadow gate instead of "
                                f"rewiring the existing E-pin.")
                            break
            except Exception:
                pass
    if cg_verify_issues:
        overall_pass = False

    # ── enable_swap + D-input change → companion wire_swap/and_term MANDATORY ──
    # If the analyzer queried the old D-input net as part of an enable_swap
    # (detectable via "D-input" in nets_to_query reason), the D-input also
    # changed — a companion wire_swap/and_term for the same target_register
    # MUST exist. Emitting only enable_swap leaves DFF D-inputs unchanged.
    companion_issues = []
    # nets_to_query is top-level in the rtl_diff JSON (not per-change)
    _top_nq = rtl_diff.get('nets_to_query') or []
    # Find enable_swap changes that have a D-input net queried at the top level
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        tgt = c.get('target_register') or '?'
        # Check top-level nets_to_query for D-input entries tied to this enable_swap
        has_d_input_query = any(
            isinstance(q, dict) and
            ('d-input' in str(q.get('reason', '')).lower() or
             'd_input' in str(q.get('reason', '')).lower()) and
            'enable_swap' in str(q.get('reason', '')).lower()
            for q in _top_nq
        )
        if not has_d_input_query:
            continue
        # D-input change detected — companion wire_swap/and_term must exist
        has_companion = any(
            c2.get('change_type') in ('wire_swap', 'and_term') and
            c2.get('target_register') == tgt
            for c2 in rtl_diff.get('changes', [])
        )
        if not has_companion:
            companion_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_swap nets_to_query contains "
                f"a D-input net (D-input also changed in same always block) but NO "
                f"companion wire_swap/and_term for the same target_register exists. "
                f"Emit a wire_swap entry with new D-input gate chain (AO22 mux + AND "
                f"reset per bit). Without it Step 3 cannot generate D-input rewires "
                f"→ FM will fail on the DFF cone.")
    if companion_issues:
        overall_pass = False

    # ── wire_swap with is_bus_gate=True must have non-empty d_input_gate_chain ─
    # When wire_swap has is_bus_gate=True and new_token is a computed net (not
    # already an n_eco_* net), the gate chain MUST be populated. An empty chain
    # means Step 3 cannot generate the per-bit mux/AND gates — eco_emit_shadow_gate
    # --d-map has no ECO net names to fill in, so D-input rewires are skipped.
    # Acceptable empty-chain cases: d_input_decompose_failed=True (decomp blocked)
    # OR d_input_resolved_net set (signal wired directly, no gates needed).
    bus_gate_chain_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('wire_swap', 'and_term'):
            continue
        if not c.get('is_bus_gate'):
            continue
        tgt = c.get('target_register') or '?'
        chain = c.get('d_input_gate_chain') or []
        new_tok = c.get('new_token') or ''
        resolved = c.get('d_input_resolved_net')
        decompose_failed = c.get('d_input_decompose_failed')
        if not chain and not resolved and not decompose_failed:
            bus_gate_chain_issues.append(
                f"changes[{idx}] target={tgt!r}: wire_swap is_bus_gate=True but "
                f"d_input_gate_chain is empty and d_input_decompose_failed is not set. "
                f"Decompose new_token={new_tok!r} into per-bit gate chain (e.g. INV + "
                f"AO22 mux per bit + optional AND reset gate). Without this, Step 3 "
                f"cannot generate D-input mux gates and eco_emit_shadow_gate --d-map "
                f"has no ECO net names — DFF D-inputs will not be rewired.")
    if bus_gate_chain_issues:
        overall_pass = False

    # ── Gate chain: cell_type must be non-None + chain input coverage check ───
    # Every gate in d_input_gate_chain and new_enable_gate_chain must have a
    # non-None cell_type. Also: every gate input that looks like a computed net
    # (not a plain RTL signal, not 1'b0/1'b1) must be produced by a preceding
    # gate in the same chain — uncovered inputs mean a missing chain gate.
    chain_cell_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        tgt = c.get('target_register') or '?'
        chains = []
        if c.get('change_type') == 'enable_swap':
            chains = [('new_enable_gate_chain', c.get('new_enable_gate_chain') or [])]
        elif c.get('change_type') in ('wire_swap', 'and_term', 'new_logic'):
            chains = [('d_input_gate_chain', c.get('d_input_gate_chain') or [])]
        for chain_name, chain in chains:
            produced = set()
            for gi, g in enumerate(chain):
                ct = g.get('cell_type')
                inst = g.get('instance_name', '?')
                out = g.get('output_net', '')
                if out:
                    produced.add(out)
                # cell_type must not be None
                if ct is None:
                    chain_cell_issues.append(
                        f"changes[{idx}] target={tgt!r} {chain_name}[{gi}] "
                        f"{inst!r}: cell_type is None. "
                        f"Look up the exact cell type from PreEco Synthesize "
                        f"(grep for the gate function near the pivot) and set "
                        f"cell_type + cell_type_from_preeco=True.")
                # Check each input: if it looks like a computed net (n_eco_* or
                # named *_inv/*_nxt/*_en + not a plain RTL name) it must have
                # been produced by an earlier gate
                for inp in (g.get('inputs') or []):
                    if inp in ("1'b0", "1'b1"):
                        continue
                    if (inp.startswith('n_eco_') or inp.endswith('_inv') or
                            inp.endswith('_nxt') or inp.endswith('_en')):
                        if inp not in produced:
                            chain_cell_issues.append(
                                f"changes[{idx}] target={tgt!r} {chain_name}[{gi}] "
                                f"{inst!r}: input {inp!r} looks like a computed net "
                                f"but is not produced by any earlier gate in this chain. "
                                f"Missing an upstream gate (e.g. INV for an _inv net).")
    if chain_cell_issues:
        overall_pass = False

    # ── enable_via_clock_gate=true → require clock_gate_instance + dff_cp_net ─
    # When enable_swap targets a clock-gated DFF (enable_via_clock_gate=true),
    # Step 3 must create a shadow clock gate and rewire the DFF array CPs.
    # It needs: clock_gate_instance (old CG cell name, for per-stage lookup)
    # and dff_cp_net (old CG output net, to find all DFF cells using it).
    shadow_gate_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        if not c.get('enable_via_clock_gate'):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        if not c.get('clock_gate_instance'):
            shadow_gate_field_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_via_clock_gate=true but "
                f"`clock_gate_instance` missing — record the existing clock gate cell name "
                f"so Step 3 can find the E-pin to rewire (Path B) or build the shadow gate "
                f"and per-stage CP lookup via rename_map (Path A).")
        # dff_cp_net only required when a companion wire_swap/and_term exists for the
        # same target (both enable and data path changing → shadow gate + CP rewire needed).
        _has_companion = any(
            c2.get('change_type') in ('wire_swap', 'and_term') and
            c2.get('target_register') == tgt
            for c2 in rtl_diff.get('changes', []))
        if _has_companion and not c.get('dff_cp_net'):
            shadow_gate_field_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_via_clock_gate=true with companion "
                f"wire_swap/and_term but `dff_cp_net` missing — record the clock gate's "
                f"Q output net so Step 3 can find all DFF CP pins to rewire to shadow gate.")
        # dff_cp_net must be the PRE-ECO existing clock gate Q (the OLD net the DFF
        # array currently uses). If it contains 'ECO_', the analyzer set it to the
        # NEW shadow gate Q — which is wrong and defeats Check 58 validation.
        _cp_net = c.get('dff_cp_net', '')
        if _cp_net and 'ECO_' in _cp_net:
            shadow_gate_field_issues.append(
                f"changes[{idx}] target={tgt!r}: `dff_cp_net`={_cp_net!r} contains 'ECO_' — "
                f"this must be the PRE-ECO existing clock gate Q output net (the net the "
                f"DFF array currently uses BEFORE the ECO), not the new shadow gate Q. "
                f"Grep PreEco Synthesize for `.Q` on `{c.get('clock_gate_instance','')}` "
                f"to get the correct old gate Q net.")
    if shadow_gate_field_issues:
        overall_pass = False

    # ── enable_via_clock_gate=true → require clock_gate_other_enable_inputs ───
    # The existing clock gate E-pin may have other inputs besides old_enable_net
    # (e.g. rep_3 in OR(rep_3, wr_vld0_d1)). The shadow gate E-pin OR gate must
    # include them. Set via eco_query_cg_context.py. Use [] if none found.
    cg_other_inputs_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        if not c.get('enable_via_clock_gate'):
            continue
        tgt = c.get('target_register') or '?'
        if c.get('clock_gate_other_enable_inputs') is None:
            cg_other_inputs_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_via_clock_gate=true but "
                f"`clock_gate_other_enable_inputs` missing — run eco_query_cg_context.py "
                f"(--cg-inst <clock_gate_instance> --old-en <old_enable_net>) to find "
                f"other fan-in of the existing CG E-pin driver. Set [] if none. "
                f"Missing this causes shadow gate E-pin to omit existing enable terms "
                f"(e.g. rep_3) → FM mismatch on all DFFs clocked by shadow gate.")
    if cg_other_inputs_issues:
        overall_pass = False

    # ── enable_via_clock_gate=true + companion wire_swap → d_input_reset_gate ─
    # When the existing DFF D-inputs are reset-gated (AN2D1/INR2), the companion
    # wire_swap chain must end with a reset AND gate. eco_query_cg_context.py
    # provides d_input_reset_gate. If True, the wire_swap chain must have >2
    # gates (mux gates + final AND reset gate).
    d_reset_gate_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'enable_swap':
            continue
        if not c.get('enable_via_clock_gate'):
            continue
        tgt = c.get('target_register') or '?'
        if c.get('d_input_reset_gate') is None:
            d_reset_gate_issues.append(
                f"changes[{idx}] target={tgt!r}: enable_via_clock_gate=true but "
                f"`d_input_reset_gate` missing — run eco_query_cg_context.py to check "
                f"whether existing DFF D-inputs are reset-gated (AN2D1/INR2). "
                f"Set true/false. If true, companion wire_swap chain must end with "
                f"AND reset gate to preserve existing D-path reset behavior.")
        elif c.get('d_input_reset_gate'):
            # Verify companion wire_swap chain has reset gate as last element
            for c2 in rtl_diff.get('changes', []):
                if (c2.get('change_type') in ('wire_swap', 'and_term') and
                        c2.get('target_register') == tgt):
                    chain = c2.get('d_input_gate_chain') or []
                    if chain:
                        last = chain[-1]
                        last_cell = last.get('cell_type', '')
                        if not re.match(r'^(AN2|AND2|INR2)', last_cell, re.I):
                            d_reset_gate_issues.append(
                                f"changes[{idx}] target={tgt!r}: d_input_reset_gate=true "
                                f"but wire_swap chain last gate {last.get('instance_name')!r} "
                                f"cell={last_cell!r} is not AN2D1/INR2. "
                                f"Add AND reset gate as last chain element.")
    if d_reset_gate_issues:
        overall_pass = False

    # ── wire_swap D-input reset context: must set d_input_has_reset_context ──
    # When the ECO'd RTL wraps the D-assignment in a reset condition, the
    # wire_swap chain must include a final AN2D1 reset gate. The analyzer must
    # inspect the always block context for `if (!reset)` / `else reg <= 0` and
    # set d_input_has_reset_context=true. Without this, the chain stops at the
    # mux and SynRtl's AND gate is missing → FM mismatch on DFF D-cone.
    reset_context_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('wire_swap', 'and_term'):
            continue
        if not c.get('is_bus_gate'):
            continue
        tgt = c.get('target_register') or '?'
        # Only check when companion enable_swap exists (combined ECO pattern)
        has_companion = any(
            c2.get('change_type') == 'enable_swap' and
            c2.get('target_register') == tgt
            for c2 in rtl_diff.get('changes', []))
        if not has_companion:
            continue
        if c.get('d_input_has_reset_context') is None:
            reset_context_issues.append(
                f"changes[{idx}] target={tgt!r}: bus_gate wire_swap with enable_swap companion "
                f"but `d_input_has_reset_context` missing. Inspect the ECO'd RTL always block "
                f"for reset wrapper (`if (!reset) reg<=expr; else reg<=0`). "
                f"If present: set true and add AN2D1(reset_inv, mux_out) as final chain gate. "
                f"If absent: set false. This determines whether FM sees a reset on DFF D-cone.")
    if reset_context_issues:
        overall_pass = False

    # ── has_sync_reset vs context_line reset detection ────────────────────────
    # The rtl_diff_analyzer detects reset from context_line, but context_line
    # may span multiple lines when the always block is captured in full.  When
    # has_sync_reset=False but the context_line contains a visible if(<reset>)
    # pattern, the analyzer's single-line regex silently missed the reset clause.
    # This causes the DFF to be emitted without a reset path → FM mismatch on the
    # new DFF because RTL has reset=0 while gate-level D-input never resets.
    reset_detection_issues = []
    _RESET_RE = re.compile(r'if\s*\(\s*(\w+)\s*\)\s*(?:begin\s*)?(?:\w+)\s*<=\s*[01]')
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        if c.get('has_sync_reset'):
            continue  # already detected — OK
        ctx = c.get('context_line', '') or ''
        tgt = c.get('target_register') or c.get('new_token') or '?'
        # Multi-line context: look for if(<signal>) ... <= 0/1 pattern
        m = _RESET_RE.search(ctx)
        if m:
            reset_signal_candidate = m.group(1)
            reset_detection_issues.append(
                f"changes[{idx}] target={tgt!r}: has_sync_reset=false but "
                f"context_line contains 'if({reset_signal_candidate})' reset clause. "
                f"rtl_diff_analyzer reset detection failed on multi-line context. "
                f"Must set has_sync_reset=true, reset_signal='{reset_signal_candidate}', "
                f"and bake reset into d_input_gate_chain (or use DFF reset pin).")
    if reset_detection_issues:
        overall_pass = False

    # ── Duplicate gate chains between standalone wire and parent change ────────
    # Any new_logic change whose gate chain output nets are ALREADY produced by
    # another change's embedded gate chain (enable_swap/wire_swap/and_term) is a
    # duplicate — studier would insert the gates twice → duplicate wire decls
    # → FM-599 ABORT_NETLIST.  Check covers both wire_change_type=new_logic_gate
    # (old naming) and plain change_type=new_logic (newer analyzer output).
    dup_chain_issues = []
    # Collect all output nets from embedded gate chains (enable_swap, wire_swap)
    embedded_out_nets = set()
    for c in rtl_diff.get('changes', []):
        for fld in ('new_enable_gate_chain', 'd_input_gate_chain', 'new_condition_gate_chain'):
            if c.get('change_type') in ('enable_swap', 'wire_swap', 'and_term'):
                for g in (c.get(fld) or []):
                    out = g.get('output_net', '')
                    if out: embedded_out_nets.add(out)
    # Flag ANY new_logic change whose d_input_gate_chain overlaps with embedded nets
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        # Match standalone wire changes regardless of naming convention
        is_standalone_wire = (
            c.get('wire_change_type') == 'new_logic_gate' or
            (c.get('change_type') == 'new_logic' and c.get('wire_name'))
        )
        if not is_standalone_wire:
            continue
        chain = c.get('d_input_gate_chain') or []
        dup = [g.get('output_net') for g in chain if g.get('output_net') in embedded_out_nets]
        if dup:
            dup_chain_issues.append(
                f"changes[{idx}] wire='{c.get('wire_name', c.get('new_token','?'))}': "
                f"standalone wire change has gate chain output nets {dup} that are ALREADY "
                f"in another change's embedded gate chain. "
                f"Duplicate gate insertions → duplicate wire decls → FM-599 ABORT. "
                f"Wire assignments must only appear embedded in their parent "
                f"enable_swap/wire_swap gate chain — never as independent changes.")
    if dup_chain_issues:
        overall_pass = False

    # ── Missing connector wire for new_logic DFF D-input ─────────────────────
    # When a new_logic DFF uses a signal from a new_port(output) in another module
    # as its d_input_resolved_net, a new_port(wire) connector must exist in the
    # DFF's declaring module.  If absent, no wire declaration is emitted for that
    # net and the DFF's D-input is an undeclared reference → FM-599 ABORT.
    missing_wire_issues = []
    # Collect all new_port(output) tokens and new_port(wire) tokens per module
    new_outputs = {c.get('new_token') for c in rtl_diff.get('changes', [])
                   if c.get('change_type') == 'new_port' and c.get('declaration_type') == 'output'}
    wire_decls = {(c.get('module_name'), c.get('new_token'))
                  for c in rtl_diff.get('changes', [])
                  if c.get('change_type') == 'new_port' and c.get('declaration_type') == 'wire'}
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        d_in = c.get('d_input_resolved_net', '')
        if not d_in or d_in.startswith(('n_eco_', "1'b")):
            continue
        if d_in in new_outputs:
            # Signal comes from a new_port(output) — needs a wire connector in DFF module
            mod = c.get('module_name', '')
            if (mod, d_in) not in wire_decls:
                tgt = c.get('target_register') or c.get('new_token') or '?'
                missing_wire_issues.append(
                    f"changes[{idx}] target={tgt!r}: d_input_resolved_net={d_in!r} is a "
                    f"new_port(output) from another module but no new_port(wire) for "
                    f"'{d_in}' exists in module '{mod}'. Add a new_port(wire) change so "
                    f"the studier emits a port_declaration(wire) connector in this module.")
    if missing_wire_issues:
        overall_pass = False

    # ── Phantom n_eco_* alias for new_port signals in chain inputs ─────────
    # When the D-input of a new_logic DFF is a new_port signal from the same
    # ECO (e.g. REG_PageRetEn), the analyzer must use the bare port name
    # directly with input_from_change set — NOT an n_eco_* alias.
    # An n_eco_* alias with input_from_change means the analyzer wrapped the
    # port name in a phantom net name that no gate produces → undriven wire
    # → FM globally unmatched. Flag CRITICAL so the analyzer corrects to use
    # the actual port name and the studier can emit input_from_new_port.
    phantom_alias_issues = []
    # Build set of actual new_port token names in this ECO
    new_port_tokens = {c.get('new_token') for c in rtl_diff.get('changes', [])
                       if c.get('change_type') == 'new_port' and c.get('new_token')}
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        tgt = c.get('target_register') or c.get('new_token') or '?'
        for fld in ('d_input_gate_chain', 'new_enable_gate_chain', 'new_condition_gate_chain'):
            for g in (c.get(fld) or []):
                ifc = g.get('input_from_change')
                for inp in (g.get('inputs') or []):
                    if not isinstance(inp, str): continue
                    if not inp.startswith('n_eco_'): continue
                    if ifc is None: continue
                    # n_eco_* input WITH input_from_change = phantom alias
                    # Determine what the actual port name should be
                    ref_change = rtl_diff.get('changes', [])[ifc] if ifc < len(rtl_diff.get('changes',[])) else {}
                    actual_name = ref_change.get('new_token') or ref_change.get('old_token') or '?'
                    phantom_alias_issues.append(
                        f"changes[{idx}] target={tgt!r} {fld} seq={g.get('seq','?')}: "
                        f"input {inp!r} is a phantom n_eco_* alias for new_port signal "
                        f"'{actual_name}' (input_from_change={ifc}). "
                        f"Use the bare port name '{actual_name}' directly — "
                        f"the phantom alias has no producing gate and will be an "
                        f"undriven wire → FM globally unmatched on DFF D-input.")
    if phantom_alias_issues:
        overall_pass = False

    # ── MUX2 in gate chains ──────────────────────────────────────────────────
    # MUX2 cells in any gate chain cause FM cone divergence — the MUX select
    # path creates globally-unmatched compare points because synthesis never
    # emits bare MUX2 for conditional-assign RTL patterns.  Engineers use
    # AO22 compound gates instead:
    #   sel ? A : B  →  INV(sel)→eco_sel_inv; AO22(A1=sel,A2=A,B1=eco_sel_inv,B2=B)
    # AO22 is structurally equivalent to synthesis output and FM-verifiable
    # without SVF.  Flag FAIL so the analyzer corrects the gate type before
    # the study phase runs.
    mux2_in_chain_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        tgt = c.get('target_register') or c.get('new_token') or '?'
        for fld in ('d_input_gate_chain', 'new_enable_gate_chain', 'new_condition_gate_chain'):
            for g in (c.get(fld) or []):
                gf = (g.get('gate_function') or '').upper()
                if gf.startswith('MUX'):
                    mux2_in_chain_issues.append(
                        f"changes[{idx}] target={tgt!r} {fld} seq={g.get('seq','?')}: "
                        f"gate_function={gf!r} — MUX cells cause FM cone divergence. "
                        f"Use AO22 instead: INV(sel)→eco_sel_inv; "
                        f"AO22(A1=sel,A2=A,B1=eco_sel_inv,B2=B)→output. "
                        f"Reuse the shared INV output across all bits for bus signals.")
    if mux2_in_chain_issues:
        overall_pass = False

    # ── port_promotion hygiene ───────────────────────────────────────────────
    # port_promotion is valid ONLY for flat netlists where an existing `reg`
    # is being changed to `output reg` (diff hunk type `c`, not `a`).
    # Two common misclassifications caught here:
    #   1. Pure additions (`output wire/reg` with no old `reg` line) — must be new_port
    #   2. `output wire` signals — wire is never a promotion; only `output reg` qualifies
    port_promo_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'port_promotion':
            continue
        tok = c.get('new_token') or c.get('signal_name') or '?'
        ctx = c.get('context_line', '') or ''
        # output wire is never a promotion — only output reg qualifies
        if 'output wire' in ctx or 'output wire' in (c.get('declaration_type') or ''):
            port_promo_issues.append(
                f"changes[{idx}] signal={tok!r}: classified as port_promotion but "
                f"context_line contains 'output wire' — output wire is a new port, never "
                f"a promotion. Reclassify as new_port (declaration_type=output).")
        # flat_net_exists must be true for port_promotion to make sense
        if not c.get('flat_net_exists'):
            port_promo_issues.append(
                f"changes[{idx}] signal={tok!r}: port_promotion with flat_net_exists=false — "
                f"promotion requires the net to already exist in PreEco flat netlist. "
                f"If this is a new signal, reclassify as new_port (declaration_type=output).")
    if port_promo_issues:
        overall_pass = False

    # ── bus combinational gate validation ────────────────────────────────────
    # When is_bus_gate=true on a new_logic_gate entry, the studier must know the
    # bus width to expand to N per-bit gate entries.  Catch missing fields here
    # so the studier never silently falls back to emitting a single (wrong) gate.
    #
    # Also catch is_bus_dff=true on wire_swap changes — wire_swap d_input_gate_chains
    # involving bus-width signals must use is_bus_gate (combinational), not is_bus_dff
    # (sequential).  is_bus_dff is only valid on new_logic (reg) changes.
    bus_gate_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        ct = c.get('change_type', '')
        # wire_swap should never carry is_bus_dff — it means the d_input_gate_chain
        # contains bus-width combinational gates, which requires is_bus_gate instead
        if ct == 'wire_swap' and c.get('is_bus_dff'):
            out = c.get('new_token') or c.get('old_token') or '?'
            bus_gate_issues.append(
                f"changes[{idx}] target={c.get('target_register','?')} new_token={out!r}: "
                f"wire_swap has is_bus_dff=true — wire_swap d_input_gate_chain involves "
                f"bus-width combinational gates; use is_bus_gate=true instead. "
                f"is_bus_dff is reserved for new_logic (reg) insertions only.")
        if c.get('change_type') not in ('new_logic_gate', 'new_logic'):
            continue
        if not c.get('is_bus_gate'):
            continue
        out = c.get('output_net') or c.get('new_token') or '?'
        if not c.get('bus_width_expr'):
            bus_gate_issues.append(
                f"changes[{idx}] output={out}: is_bus_gate=true but `bus_width_expr` is missing — "
                f"rtl_diff_analyzer must record the range macro or integer width so "
                f"eco_netlist_studier can call eco_resolve_bus_width.py to determine N")
    if bus_gate_issues:
        overall_pass = False

    # ── bus DFF validation ────────────────────────────────────────────────────
    # Same check for new_logic / new_logic_dff with is_bus_dff=true.
    bus_dff_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        if not c.get('is_bus_dff'):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        if not c.get('bus_width_expr'):
            bus_dff_issues.append(
                f"changes[{idx}] target={tgt}: is_bus_dff=true but `bus_width_expr` is missing — "
                f"rtl_diff_analyzer must record the range macro or integer width")
    if bus_dff_issues:
        overall_pass = False

    # ── and_term-preference check (WARN) — flag wire_swap+fallback when
    # and_term is likely feasible. Per rtl_diff_analyzer.md "PREFER and_term
    # WHEN FEASIBLE" rule: a single new else-if branch with a hop-0 compound
    # cell driver should classify as and_term, NOT wire_swap+fallback. The
    # most common mistake: agent reads d_input_decompose_failed=true and
    # falls back to wire_swap — that flag does NOT disqualify and_term.
    _COMPOUND_CELL_PREFIXES = ('IAOI', 'OAI', 'AOI', 'IOAI', 'AOAI',
                                'IND2', 'IND3', 'INR2', 'INR3',
                                'AOA', 'OAO', 'AO2', 'OA2')
    and_term_pref_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'wire_swap':
            continue
        if c.get('fallback_strategy') not in ('driver_substitution',
                                              'intermediate_net_insertion'):
            continue
        old_driver = (c.get('old_driver_cell_type') or '').upper()
        if not any(old_driver.startswith(p) for p in _COMPOUND_CELL_PREFIXES):
            continue  # no compound cell to host and_term — fallback acceptable
        # Single new branch + compound cell at hop 0 → and_term should have been tried.
        # NOTE: can't reliably count NEW else-if branches from context_line alone
        # (agent includes adjacent existing branches as context). Rely on the
        # mux_select_reasoning to indicate if the agent had a real polarity-fail
        # justification for the fallback.
        reason = (c.get('mux_select_reasoning') or '').lower()
        # If the agent documented a SPECIFIC infeasibility, accept the fallback
        if 'polarity' in reason and ('fail' in reason or 'infeasible' in reason):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        and_term_pref_issues.append(
            f"HIGH/AND-TERM-PREF: changes[{idx}] target={tgt}: wire_swap+{c.get('fallback_strategy')} "
            f"used but ONE new else-if branch + compound cell '{old_driver[:30]}' "
            f"at hop 0 — `and_term` is FEASIBLE and MANDATORY. Per rtl_diff_analyzer.md "
            f"`PREFER and_term WHEN FEASIBLE` rule, fall back only when polarity "
            f"check fails on BOTH NOR2 and INR2 candidates with explicit reason in "
            f"mux_select_reasoning. d_input_decompose_failed does NOT disqualify "
            f"and_term. Reclassify as `and_term` with DFF-pin-rewire pattern: chain "
            f"output to fresh n_eco_<jira>_andterm_<seq>, emit separate rewire on "
            f"the consuming DFF.D pin from old_token to the new net. Leave existing "
            f"driver untouched.")
    if and_term_pref_issues:
        overall_pass = False  # HARD FAIL — driver_substitution when and_term is feasible
                              # destroys LATCG cone matching and wastes FM rounds

    # ── SE/SI scan-pin check — new ECO DFFs MUST be isolated from scan chain
    # in ALL 3 stages (Synth/PrePlace/Route). Bridge wires (e.g.
    # eco<jira>_si_bridge, ECO_*_SI_out, neighbor_dff's scan pins) MUST NOT be
    # copied from a neighbor_dff lookup. See rtl_diff_analyzer.md Step D-SE-SI
    # and eco_netlist_studier.md HARD RULE 1.
    # ── and_term old_token sanity check — old_token MUST be the gate-level
    # DFF.D net (e.g. SEQMAP_NET_70624), NOT the target register's Q net
    # (e.g. BlockScrubReq). Confusing them means the studier emits gate
    # entries that target the DFF output instead of the D-input cone driver.
    and_term_old_token_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('and_term', 'wire_swap'): continue
        old_token = (c.get('old_token') or '').strip()
        target = (c.get('target_register') or '').strip()
        if not old_token or not target: continue
        if old_token == target:
            and_term_old_token_issues.append(
                f"changes[{idx}] target={target}: old_token={old_token!r} "
                f"equals target_register — this is the DFF Q-output net name, "
                f"NOT the D-input cone driver. old_token MUST be the gate-level "
                f"net that drives DFF.D (e.g. SEQMAP_NET_<N>). Re-extract from "
                f"the existing chain's hop-0 driver output.")
    if and_term_old_token_issues:
        overall_pass = False

    # ── and_term insertion-pattern check — chain output MUST be a fresh
    # n_eco_* net, NOT a reuse of old_token. Reusing old_token = driver-rename
    # pattern, which breaks LATCG/clock-gating equivalence in FM. See
    # rtl_diff_analyzer.md `MANDATORY insertion pattern — DFF-pin-rewire`.
    and_term_pattern_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'and_term':
            continue
        old_token = c.get('old_token') or ''
        chain = c.get('and_term_gate_chain_design') or []
        if not chain or not old_token:
            continue
        last_out = (chain[-1].get('output_net') or '').strip()
        if last_out == old_token:
            tgt = c.get('target_register') or '?'
            and_term_pattern_issues.append(
                f"changes[{idx}] target={tgt}: and_term_gate_chain_design[-1]."
                f"output_net={last_out!r} REUSES old_token (driver-rename "
                f"pattern). MANDATORY DFF-pin-rewire pattern: chain output "
                f"must be a fresh n_eco_<jira>_<seq> net AND emit a separate "
                f"rewire entry on the DFF.D pin. Reusing old_token breaks "
                f"LATCG/clock-gating equivalence — FM will FAIL with "
                f"'Unmatched Cone Input' on the reset signal.")
    if and_term_pattern_issues:
        overall_pass = False

    scan_pin_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        pcs = c.get('port_connections_per_stage') or {}
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            for pin in ('SE', 'SI'):
                v = (pcs.get(stage) or {}).get(pin)
                if v is not None and v != "1'b0":
                    scan_pin_issues.append(
                        f"changes[{idx}] target={tgt}: port_connections_per_stage[{stage}].{pin}={v!r} — "
                        f"new ECO DFF scan pins MUST be \"1'b0\" in all 3 stages (scan stitching out of scope). "
                        f"rtl_diff_analyzer should NOT copy bridge wires from neighbor_dff.")
    if scan_pin_issues:
        overall_pass = False

    # Mandatory-fields check for new_logic / new_logic_dff entries — every such
    # entry MUST have dff_clock (Step 3 needs it to pick neighbor DFF for per-stage
    # CP + Mode S clock-domain match), AND must have a non-empty d_input_gate_chain
    # + d_input_expected_function whenever has_sync_reset is true OR
    # requires_scan_stitching is true (sync-reset RTL collapses into a combinational
    # gate at the D-input, and any new ECO DFF we stitch needs a defined D logic).
    new_logic_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        if not c.get('dff_clock'):
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt}: `dff_clock` MISSING — "
                f"required for Step 3 per-stage CP + Mode S clock-domain match")
        # reset_polarity MANDATORY when has_sync_reset is true
        if c.get('has_sync_reset') and not c.get('reset_polarity'):
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/RESET-POLARITY-MISSING]: "
                f"has_sync_reset=true but reset_polarity not set. "
                f"Must be 'active_high' or 'active_low'.")
            overall_pass = False
        # dff_instance_name and dff_output_net MANDATORY for new_logic DFF
        inst_name = c.get('dff_instance_name') or ''
        out_net   = c.get('dff_output_net') or ''
        if tgt != '?' and not inst_name:
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/DFF-INSTANCE-NAME-MISSING]: "
                f"dff_instance_name not set. Must be '<target_register>_reg'.")
            overall_pass = False
        elif inst_name and tgt != '?' and inst_name != f'{tgt}_reg':
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt} [WARN/DFF-INSTANCE-NAME-FORMAT]: "
                f"dff_instance_name='{inst_name}' expected '{tgt}_reg'.")
        # Mode S decision: scan stitching is OUT OF SCOPE under the new policy
        # (DFT team handles scan integration). The rtl_diff MD instructs analyzers
        # NOT to emit `requires_scan_stitching` / `mode_s_anchor` / sibling fields.
        # When the field is absent or false, treat as "scan out of scope" — skip
        # the enforcement entirely. The full enforcement chain below remains as
        # a safety net for stale fields leaking through (only fires when rss=true).
        rss = c.get('requires_scan_stitching')
        if rss is True:
            anchor = c.get('mode_s_anchor') or {}
            missing_anchor = [k for k in ('sibling_module', 'anchor_dff', 'fm_scope')
                              if not anchor.get(k)]
            if missing_anchor:
                new_logic_field_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/13-FM-SCOPE-MISSING]: "
                    f"requires_scan_stitching=true but `mode_s_anchor` missing fields "
                    f"{missing_anchor}. fm_scope (instance hierarchy from tile-internal "
                    f"root to sibling, e.g. 'ARB/DCQARB') MUST come from "
                    f"eco_pick_sibling.py recommended_pick.fm_scope. Without it Step 2 "
                    f"Cat 8 queries use module-type names and FM returns Unknown name "
                    f"(FM-036) on every anchor — Step 3 then has no bridge data.")
            elif '/' in (anchor.get('fm_scope') or ''):
                # Sanity: fm_scope must look like instance/instance, not contain
                # any module-type token (e.g. ddrss_<tile>_t_<peer>). Module-type
                # tokens always start with the tile prefix.
                fms = anchor['fm_scope']
                bad = [tok for tok in fms.split('/') if tok.startswith('ddrss_')]
                if bad:
                    new_logic_field_issues.append(
                        f"changes[{idx}] target={tgt} [FAIL/13-FM-SCOPE-MODULE-TYPE]: "
                        f"mode_s_anchor.fm_scope={fms!r} contains module-type token(s) "
                        f"{bad} — must be INSTANCE names only (e.g. 'ARB/DCQARB'). "
                        f"Re-run eco_pick_sibling.py with --tile-module set, then copy "
                        f"recommended_pick.fm_scope verbatim.")
            else:
                # Check 12 — SIBLING-IS-SELF: sibling_module MUST be a peer module
                # different from the host. Picking the host module as its own sibling
                # is a degenerate self-loop that defeats the bridge_port strategy.
                sib  = (anchor.get('sibling_module') or '').strip()
                host = (c.get('module_name') or '').strip()
                # Also derive host from scope's last segment as a fallback
                if not host:
                    scope = (c.get('scope') or c.get('instance_scope') or '').strip()
                    if scope:
                        host = scope.split('/')[-1]
                # Compare with stripped trailing _0/_1 (DFT suffix variants)
                def _norm(name):
                    return re.sub(r'_\d+$', '', name)
                if sib and host and (_norm(sib) == _norm(host) or
                                     _norm(sib).endswith('_'+_norm(host)) or
                                     _norm(host).endswith('_'+_norm(sib))):
                    new_logic_field_issues.append(
                        f"changes[{idx}] target={tgt} [FAIL/12-SIBLING-IS-SELF]: "
                        f"mode_s_anchor.sibling_module={sib!r} matches the host module "
                        f"{host!r}. Bridge_port requires a PEER module under the same "
                        f"parent — using host as its own sibling is a degenerate self-loop "
                        f"that produces no real cross-module bridging. Pick a different "
                        f"peer module under the host's parent that contains scan-chain DFFs.")
        # Wrapper-clock heuristic + sibling-pick proof enforcement: skipped
        # under the new "scan stitching out of scope" policy. Re-enable by
        # restoring the original block from git history if scan ownership
        # ever moves back into the AI flow.
        needs_chain = c.get('has_sync_reset') or c.get('requires_scan_stitching')
        chain = c.get('d_input_gate_chain') or []
        d_in_net = c.get('d_input_net') or ''
        # UNCONNECTED placeholder ⇒ PreEco DFF has no D-driver; chain MUST replace it
        is_unconnected_d = d_in_net.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_'))
        if (needs_chain or is_unconnected_d) and not chain:
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt}: `d_input_gate_chain` empty but "
                f"has_sync_reset={c.get('has_sync_reset')} / "
                f"requires_scan_stitching={c.get('requires_scan_stitching')} / "
                f"d_input_net={d_in_net!r} — emit at least the sync-reset "
                f"combinational gate (D = ~reset & next_value)")
        if (needs_chain or is_unconnected_d) and not c.get('d_input_expected_function'):
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt}: `d_input_expected_function` MISSING "
                f"(needed by Gap E equivalence check)")
        # When has_sync_reset is true, agent MUST decide whether reset is baked
        # into the D-input combinational gate (DFF cell has no RN pin) or fed
        # through a separate reset port. Missing field blocks Step 3 from
        # picking the right DFF stitching pattern.
        if c.get('has_sync_reset') and c.get('reset_baked_in_d_input') is None:
            new_logic_field_issues.append(
                f"changes[{idx}] target={tgt}: `reset_baked_in_d_input` MISSING "
                f"(has_sync_reset=true requires explicit true/false — true if DFF "
                f"cell has no RN pin and reset is AND-ed into D, false if DFF has RN)")
    if new_logic_field_issues:
        overall_pass = False

    # Mode I source-port info — when a new_logic_dff has d_input_net starting
    # with UNCONNECTED_*, Step 3 needs to know which submodule output port
    # the UNCONNECTED was originally tied to so it can emit the paired Mode I
    # port_connection. Require submodule_instance + port_name + bus_bit_index.
    mode_i_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        d_in = c.get('d_input_net') or ''
        if not d_in.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_')):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        for f in ('submodule_instance', 'port_name', 'bus_bit_index'):
            if c.get(f) is None:
                mode_i_field_issues.append(
                    f"changes[{idx}] target={tgt}: d_input_net={d_in!r} (UNCONNECTED) but "
                    f"`{f}` MISSING — Step 3 needs it to emit the Mode I paired "
                    f"child-scope port_connection")
    if mode_i_field_issues:
        overall_pass = False

    # Hierarchy/scope path — every new_logic_dff must specify the full netlist
    # scope (e.g. 'umccmd/ARB/CTRLSW') in `scope` or `instance_scope` so Step 3
    # can land the new DFF in the correct instance when the host module has
    # multiple instantiations.
    scope_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        # scope_is_tile_root=true entries intentionally use "" scope (FM auto-scopes under tile)
        if c.get('scope_is_tile_root'):
            continue
        if not (c.get('scope') or c.get('instance_scope')):
            tgt = c.get('target_register') or c.get('new_token') or '?'
            scope_field_issues.append(
                f"changes[{idx}] target={tgt}: `scope` (or `instance_scope`) MISSING — "
                f"required by Step 3 to disambiguate when host module {c.get('module_name','?')!r} "
                f"is instantiated multiple times")
    if scope_field_issues:
        overall_pass = False

    # wire_swap MUX context — even when polarity is NOT pending, the agent must
    # emit mux_select_gate_function + mux_select_branch_true_on +
    # mux_select_i0_net + mux_select_i1_net so Step 3 can apply the rewire
    # correctly. Currently only polarity_pending entries get the existing
    # check_entry pass; non-pending entries can ship without I0/I1 nets.
    wire_swap_field_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'wire_swap':
            continue
        if c.get('mux_select_polarity_pending'):
            continue  # check_entry handles these
        tgt = c.get('target_register') or c.get('new_token') or '?'
        for f in ('mux_select_gate_function', 'mux_select_branch_true_on',
                  'mux_select_i0_net', 'mux_select_i1_net'):
            if not c.get(f):
                wire_swap_field_issues.append(
                    f"changes[{idx}] target={tgt}: wire_swap missing `{f}` — "
                    f"Step 3 needs full MUX context to apply rewire")
    if wire_swap_field_issues:
        overall_pass = False

    # UNCONNECTED-as-variable check — chain inputs and d_input_expected_function
    # must NEVER reference a literal `UNCONNECTED_<N>` placeholder as a signal.
    # The placeholder marks an undriven net in PreEco; the agent must trace it
    # to the real RTL source (e.g. REG_UmcCfgEco[1]) and rewrite the chain
    # against that. Letting UNCONNECTED leak through makes Gap E equivalence
    # vacuously true while the actual ECO is wired to a phantom signal.
    unconnected_var_issues = []
    _UNC_RE = re.compile(r'\bUNCONNECTED_\d+\b')
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        tgt = c.get('target_register') or c.get('new_token') or '?'
        ref = c.get('d_input_expected_function') or ''
        m = _UNC_RE.findall(ref)
        if m:
            unconnected_var_issues.append(
                f"changes[{idx}] target={tgt}: d_input_expected_function references "
                f"UNCONNECTED placeholder(s) {sorted(set(m))} — trace to the real "
                f"RTL source signal and rewrite (UNCONNECTED is not a real signal)")
        for g in (c.get('d_input_gate_chain') or []):
            for inp in (g.get('inputs') or []):
                if _UNC_RE.search(str(inp)):
                    unconnected_var_issues.append(
                        f"changes[{idx}] target={tgt}: chain seq={g.get('seq','?')} "
                        f"input {inp!r} is an UNCONNECTED placeholder — trace it to "
                        f"the real source signal")
    if unconnected_var_issues:
        overall_pass = False

    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'wire_swap' or c.get('mux_select_polarity_pending'):
            continue
        issues = check_entry(c)
        results.append({
            'change_index': idx,
            'target_register': c.get('target_register'),
            'gate_function':   c.get('mux_select_gate_function'),
            'branch_true_on':  c.get('mux_select_branch_true_on'),
            'passed': not issues,
            'issues': issues,
        })
        if issues:
            overall_pass = False

    # Check 9: Chain Compactness (GAP-8) — flag d_input_gate_chain that's
    # significantly larger than achievable via boolean simplification (De Morgan
    # transform, bus-equality folding, existing-inverted-signal reuse).
    # Engineer reference for 9868: 4-cell chain (INV+XOR2+OR4+NR2) vs our
    # 7-cell chain (NOR3+INV+AN4+OR2+INV+INV+AN4) for same boolean function.
    # Larger chain = larger cone for FM = higher chance of cone divergence
    # across PP/Route stages. WARN issues do not block; FAIL only on grossly
    # oversized chains.
    chain_compact_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        chain = c.get('d_input_gate_chain') or []
        if len(chain) < 4:
            continue
        tgt = c.get('target_register') or '?'

        # 9a — De Morgan opportunity: ≥2 INV cells whose outputs feed into a
        # final AND gate. Suggest collapsing to OR-of-positive + NOR transformation.
        inv_cells = [g for g in chain if (g.get('gate_function') or '').upper() == 'INV']
        final_gate = chain[-1] if chain else {}
        final_fn = (final_gate.get('gate_function') or '').upper()
        if len(inv_cells) >= 2 and final_fn.startswith('AN'):
            inv_outputs = {g.get('output_net') for g in inv_cells}
            and_inputs = set(final_gate.get('inputs', []))
            consumed = inv_outputs & and_inputs
            if len(consumed) >= 2:
                saved = len(consumed) - 1  # OR-N + NR2 replaces N INVs feeding ANDN
                chain_compact_issues.append(
                    f"changes[{idx}] target={tgt} [WARN/9a-DEMORGAN]: "
                    f"{len(consumed)} INV cells feed final {final_fn}. "
                    f"De Morgan transform → 1 OR{len(consumed)+1} + 1 NR2 "
                    f"saves ~{saved} cells. Engineer pattern preferred for FM cone simplicity.")

        # 9b — Bus equality fold: NOR3 + INV + AND4 + OR2 sequence likely came
        # from `(B==K1) | (B==K2)` where K1=000, K2=011. If K1, K2 differ in
        # ≤2 bits, can fold to XOR2 + OR2 + NR2 (smaller cone).
        nor_cells = [g for g in chain if (g.get('gate_function') or '').upper().startswith(('NOR', 'NR'))]
        and_after_inv = False
        for i, g in enumerate(chain[:-1]):
            if (g.get('gate_function') or '').upper() == 'INV':
                next_g = chain[i+1]
                if (next_g.get('gate_function') or '').upper().startswith('AN'):
                    and_after_inv = True
                    break
        or_in_chain = any((g.get('gate_function') or '').upper().startswith(('OR', 'OR2', 'OR3', 'OR4'))
                          for g in chain)
        if nor_cells and and_after_inv and or_in_chain:
            chain_compact_issues.append(
                f"changes[{idx}] target={tgt} [WARN/9b-BUS-FOLD]: "
                f"chain has NOR+INV+AND+OR sequence likely from `(bus==K1) | (bus==K2)`. "
                f"If K1,K2 differ by 1-2 bits → XOR2 fold saves cells.")

        # 9c — Existing inverted signal reuse: each INV cell in the chain whose
        # input is an RTL-level signal (not n_eco_*) is a candidate. The studier
        # should look for an EXISTING wire in PreEco that already produces the
        # inverted form — and use that wire per-stage instead of adding a new
        # INV cell. Each new INV widens the FM cone walk.
        # Per-INV: WARN. Aggregate: FAIL when ≥2 NEW INVs without reuse_existing_wire.
        unreused_invs = []
        for inv in inv_cells:
            inputs = inv.get('inputs') or []
            if not inputs:
                continue
            target_signal = inputs[0]
            # Skip if input is already an internal eco net or constant
            if target_signal.startswith(('n_eco_', "1'b", "1'b1", "1'b0")):
                continue
            # Skip if studier already marked this INV as reusing existing wire
            if inv.get('reuse_existing_wire') is True:
                continue
            unreused_invs.append((inv.get('seq', '?'), target_signal))
            chain_compact_issues.append(
                f"changes[{idx}] target={tgt} [WARN/9c-REUSE-INV] "
                f"seq={inv.get('seq','?')}: INV({target_signal}) is a NEW cell. "
                f"Studier should grep PreEco for an existing wire = ~{target_signal} "
                f"(per-stage rename like FxPlace_ZINV_*) and reuse it instead. "
                f"Reduces FM cone divergence risk across PP/Route.")
        if len(unreused_invs) >= 2:
            chain_compact_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/9c-MULTI-INV-NO-REUSE]: "
                f"{len(unreused_invs)} new INV cells without `reuse_existing_wire`: "
                f"{[f'{s}=INV({sig})' for s, sig in unreused_invs]}. "
                f"≥2 unreused INVs → high cone-divergence risk across PP/Route. "
                f"Studier MUST search PreEco for existing inverted wires and emit "
                f"`reuse_existing_wire: true` + `inputs_per_stage` on each.")
            overall_pass = False

        # Check 9c-v2: per-stage reuse verification — if reuse_existing_wire=true is
        # claimed, BOTH PrePlace AND Route must have use_existing_wire=true in
        # inputs_per_stage. Synth-only reuse doesn't count (Synth has no CTS-renamed
        # wires; the cell still needs to be inserted, and PP/Route are where cone
        # divergence happens). Catches the bypass pattern where the agent sets
        # reuse=true on a flag basis without backing per-stage data.
        for inv in inv_cells:
            if inv.get('reuse_existing_wire') is not True:
                continue
            ips = inv.get('inputs_per_stage') or {}
            pp_ok = (ips.get('PrePlace') or {}).get('use_existing_wire') is True
            rt_ok = (ips.get('Route') or {}).get('use_existing_wire') is True
            if not (pp_ok and rt_ok):
                chain_compact_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/9c-FAKE-REUSE] "
                    f"seq={inv.get('seq','?')}: reuse_existing_wire=true claimed but "
                    f"inputs_per_stage shows PP.use_existing_wire={pp_ok}, "
                    f"Route.use_existing_wire={rt_ok}. Reuse claim must be backed "
                    f"by existing wires in BOTH PP AND Route (the stages where "
                    f"FM cone divergence happens). Synth-only reuse is NOT enough.")
                overall_pass = False

        # Check 11 — DEMORGAN-MISSED: structural detection of the forbidden pattern
        # "≥2 INV cells whose outputs feed a common ANDN gate". Independent of any
        # reuse_existing_wire flag. Catches "literal text-to-cell" decomposition
        # that should have been rewritten via De Morgan to NOR-N + outer gate.
        # Triggers regardless of whether reuse claims are populated, because the
        # topology itself is FM-risky.
        and_consumers = {}  # output_net of INV → list of (downstream_gate, seq) that consume it
        for inv in inv_cells:
            ip_net = inv.get('output_net')
            if not ip_net:
                continue
            for g in chain:
                if g is inv:
                    continue
                gf = (g.get('gate_function') or '').upper()
                if not gf.startswith(('AN', 'AND')):
                    continue
                if ip_net in (g.get('inputs') or []):
                    and_consumers.setdefault(g.get('seq', '?'), []).append(inv.get('seq', '?'))
        for and_seq, inv_seqs in and_consumers.items():
            if len(inv_seqs) >= 2:
                chain_compact_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/11-DEMORGAN-MISSED]: "
                    f"AND gate seq={and_seq} consumes outputs of {len(inv_seqs)} INV cells "
                    f"({inv_seqs}). FORBIDDEN pattern — De Morgan transform required: "
                    f"collect the negated terms into a single NOR-N gate instead of "
                    f"emitting per-term INV cells feeding a common AND. NOR absorbs "
                    f"negation in its truth table; per-term INVs widen FM cone walks "
                    f"through CTS-rebalanced infrastructure → cone divergence on "
                    f"PP/Route stages.")
                overall_pass = False

        # 9d — Excessive cell count: if cells > 1.2× distinct RTL-input count
        # (heuristic for "AND-of-positive-terms" verbosity), flag as FAIL.
        # Engineer's reference chain for 9868: 4 cells for 5 inputs (0.8× ratio).
        # Threshold of 1.2× catches our verbose 7-cell chain (1.4× ratio) while
        # tolerating small overhead (e.g. 5 cells for 4 inputs).
        distinct_inputs = set()
        for g in chain:
            for inp in (g.get('inputs') or []):
                if inp and not inp.startswith('n_eco_') and not inp.startswith("1'b"):
                    distinct_inputs.add(inp)
        if distinct_inputs:
            # Rule: chain cell count must not EXCEED distinct input count.
            # Engineer 9868: 4 cells for 6 inputs (well under). Our verbose
            # chain: 7 cells for 6 inputs (exceeds). Each gate combines ≥2
            # signals into 1, so a well-decomposed chain has cells ≤ inputs - 1.
            # Using just `inputs` as the threshold gives a small safety margin.
            expected_max = max(4, len(distinct_inputs))
            if len(chain) > expected_max:
                chain_compact_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/9d-OVERSIZED]: "
                    f"chain has {len(chain)} cells for {len(distinct_inputs)} distinct RTL inputs "
                    f"({sorted(distinct_inputs)[:5]}...). "
                    f"Expected ≤{expected_max} cells. Mandatory simplification pass needed "
                    f"(De Morgan + bus-fold + compound-cell preference). "
                    f"See rtl_diff_analyzer.md §E2.5.")
                overall_pass = False

    # Check 9f-BUS-CONST-DECODE — detect IND2/IND3/IND4 used to decode a bus
    # equality against a non-all-ones constant.  IND-N(A,B,...)=~(A&B&...) only
    # correctly decodes ~(bus==all-ones).  For any other constant, zero-bit positions
    # need INV cells before the NAND gate.  Handles binary (2'b), hex (4'h), decimal.
    import re as _re_bcd
    def _vlog_const_to_bin(width_str, base, val_str):
        """Convert Verilog constant to binary string padded to width. Returns None on error."""
        try:
            w = int(width_str)
            b = {'b': 2, 'h': 16, 'd': 10, 'o': 8}.get(base.lower())
            if b is None:
                return None
            n = int(val_str.replace('_', ''), b)
            return format(n, f'0{w}b')
        except (ValueError, TypeError):
            return None

    for idx, c in enumerate(rtl_diff.get('changes', [])):
        chain = c.get('new_condition_gate_chain') or c.get('d_input_gate_chain') or []
        tgt   = c.get('target_register', c.get('new_token', '?'))
        for g in chain:
            fn = (g.get('gate_function') or '').upper()
            if fn not in ('IND2', 'IND3', 'IND4'):
                continue
            inputs = g.get('inputs') or []
            bus_bits = [i for i in inputs if '[' in str(i)]
            if len(bus_bits) < 2:
                continue
            ctx = str(g.get('rtl_condition', '')) + str(c.get('context_line', ''))
            m = _re_bcd.search(r"==\s*([0-9]+)'([bBhHdDoO])([0-9a-fA-F_]+)", ctx)
            if not m:
                continue
            const_bits = _vlog_const_to_bin(m.group(1), m.group(2), m.group(3))
            if const_bits is None:
                continue
            if const_bits == '1' * len(const_bits):
                continue  # all-ones: IND-N is correct
            chain_compact_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/9f-BUS-CONST-DECODE]: "
                f"{fn}({', '.join(str(i) for i in inputs)}) decodes ~(bus==all-ones) "
                f"but RTL condition is ~(bus=={m.group(0).split('==')[1].strip()})"
                f" (binary={const_bits}). "
                f"Zero-bit positions need INV before the ND gate — "
                f"use INV(bus[i]) for each 0-bit then ND{len(bus_bits)}(...). "
                f"See rtl_diff_analyzer.md §E2.5 rule 2b.")
            overall_pass = False

    # Check 9f-PREECO-FIRST — gates in new_condition_gate_chain must use PreEco compound
    # types (cell_type_from_preeco: true) when compound gates exist in scope.
    # SCOPE: new_condition_gate_chain (wire_swap/intermediate_net_insertion) only.
    # d_input_gate_chain for new_logic DFFs may use simple gates (AND/OR/INV/AO22
    # for ternary/boolean decomposition) — E4c rule does not apply there.
    # NOTE: MUX2 is FORBIDDEN in all chains (see mux2_in_chain_issues check above).
    _SIMPLE_GATES = {'OR2','AND2','NR2','NOR2','AN2','OR3','AND3','NR3','OR4','AND4',
                     'OR','AND','NR','AN','INV'}
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        # Only check new_condition_gate_chain — not d_input_gate_chain for new_logic DFFs
        chain = c.get('new_condition_gate_chain') or []
        tgt   = c.get('target_register', c.get('new_token', '?'))
        if not chain:
            continue
        if c.get('e4c_no_compound_found'):
            continue
        for g in chain:
            raw_fn = (g.get('gate_function') or '').upper()
            fn  = raw_fn.rstrip('0123456789')
            cpf = g.get('cell_type_from_preeco', False)
            if (fn in _SIMPLE_GATES or raw_fn in _SIMPLE_GATES) and not cpf:
                chain_compact_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/9f-PREECO-FIRST]: "
                    f"gate seq={g.get('seq','?')} uses simple gate '{g.get('gate_function')}' "
                    f"without cell_type_from_preeco:true. "
                    f"HARD FAIL — condition chains MUST use compound gates from PreEco "
                    f"(OA12/OAI21/AN3/ND3). Run E4c grep on PreEco declaring module first; "
                    f"simple gates (NR2/OR3/AN2) cause FM structural divergence between "
                    f"Synth and PP ECO even when boolean is correct. "
                    f"See rtl_diff_analyzer.md §E4c.")
                overall_pass = False

    # Check 9e — Compound gate preference: detect consecutive gate pairs (gate_i →
    # gate_i+1) where gate_i's output feeds ONLY gate_i+1 and the combined function
    # matches a known compound gate family. Using simple primitive chains (OR2→AND2,
    # AND2→OR2, etc.) when a compound exists creates intermediate wires that FM must
    # trace back to RTL without SVF → compare point failures on downstream DFFs.
    _COMPOUND_PATTERNS = {
        # OR + AND family → OA/OAI
        ('OR2',  'AND2')  : 'OA21/OA12',
        ('OR2',  'AN2')   : 'OA21/OA12',
        ('OR3',  'AND2')  : 'OA31',
        ('OR2',  'AND3')  : 'OA211',
        # AND + OR family → AO/AOI
        ('AND2', 'OR2')   : 'AO21',
        ('AND2', 'OR3')   : 'AO211',
        ('AND3', 'OR2')   : 'AO31',
        # OR + NAND/NOR → OAI/AOI
        ('OR2',  'NAND2') : 'OAI21',
        ('OR2',  'ND2')   : 'OAI21',
        ('OR3',  'NAND2') : 'OAI31',
        ('AND2', 'NOR2')  : 'AOI21',
        ('AND2', 'NR2')   : 'AOI21',
        ('AND3', 'NOR2')  : 'AOI31',
        # INV + AND/NAND → NAND (De Morgan: ~(A&B) = NAND2)
        ('INV',  'AND2')  : 'NAND2/INR2 (INV input absorbed into NAND)',
        ('INV',  'AN2')   : 'NAND2/INR2 (INV input absorbed into NAND)',
        ('INV',  'AND3')  : 'NAND3/INR3 (INV input absorbed into NAND)',
        # INV + OR/NOR → NOR (De Morgan: ~(A|B) = NOR2)
        ('INV',  'OR2')   : 'NOR2/INR2 (INV input absorbed into NOR)',
        ('INV',  'NOR2')  : 'AND2 (double inversion: INV+NOR = AND)',
        ('INV',  'NR2')   : 'AND2 (double inversion)',
        # XOR/XNOR patterns
        ('INV',  'XNOR2') : 'XOR2 (INV+XNOR = XOR)',
        ('INV',  'XOR2')  : 'XNOR2 (INV+XOR = XNOR)',
    }
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        for chain_field in ('new_condition_gate_chain', 'd_input_gate_chain'):
            chain = c.get(chain_field) or []
            if len(chain) < 2:
                continue
            # Map each n_eco_* net to list of gate indices that consume it
            output_to_consumers = {}
            for gi, g in enumerate(chain):
                for inp in (g.get('inputs') or []):
                    base = str(inp).split('[')[0]
                    if base.startswith('n_eco_'):
                        output_to_consumers.setdefault(base, []).append(gi)
            for gi in range(len(chain) - 1):
                g1 = chain[gi]
                g1_out = str(g1.get('output_net') or '').split('[')[0]
                if not g1_out.startswith('n_eco_'):
                    continue
                consumers = output_to_consumers.get(g1_out, [])
                if not consumers:
                    continue
                g1f = g1.get('gate_function', '').upper()
                tgt = c.get('target_register') or c.get('old_token') or '?'

                if len(consumers) == 1:
                    # Single consumer — can fold g1+g2 into one compound gate
                    g2 = chain[consumers[0]]
                    g2f = g2.get('gate_function', '').upper()
                    compound = _COMPOUND_PATTERNS.get((g1f, g2f))
                    if compound:
                        chain_compact_issues.append(
                            f"changes[{idx}] target={tgt} [FAIL/9e-COMPOUND-PREFER]: "
                            f"{g1f}(seq={g1.get('seq')})→{g2f}(seq={g2.get('seq')}) "
                            f"can be a single compound cell ({compound}). "
                            f"Compound gates avoid intermediate wires FM cannot trace to RTL. "
                            f"Apply E4d (rtl_diff_analyzer.md §E2.5/E4d).")
                        overall_pass = False
                else:
                    # Multiple consumers — g1 output fans to N gates of same type.
                    # Each consumer can become a separate compound gate using g1's raw
                    # inputs directly, eliminating the shared intermediate wire entirely.
                    consumer_funcs = set(chain[ci].get('gate_function','').upper() for ci in consumers)
                    if len(consumer_funcs) == 1:
                        g2f = next(iter(consumer_funcs))
                        compound = _COMPOUND_PATTERNS.get((g1f, g2f))
                        if compound:
                            chain_compact_issues.append(
                                f"changes[{idx}] target={tgt} [FAIL/9e-COMPOUND-PREFER]: "
                                f"{g1f}(seq={g1.get('seq')}) fans to {len(consumers)} {g2f} gates "
                                f"— each consumer can be an independent compound cell ({compound}) "
                                f"using {g1f}'s raw inputs directly, eliminating the shared "
                                f"intermediate wire '{g1_out}' that FM cannot trace to RTL. "
                                f"Apply E4d (rtl_diff_analyzer.md §E2.5/E4d).")
                            overall_pass = False

    # Check 10: Reset signal must be present in chain when reset_baked_in_d_input=True.
    # When the DFF has no RN/R reset pin and reset is sync, the reset must be baked
    # into the D-input combinational chain. If the reset signal is missing from
    # both d_input_expected_function AND every chain entry's inputs, the chain is
    # functionally INCOMPLETE — DFF will not zero out during reset → FM Synth-vs-RTL
    # mismatch on the new DFF.
    reset_inclusion_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('new_logic', 'new_logic_dff'):
            continue
        if not c.get('reset_baked_in_d_input'):
            continue
        rst = c.get('reset_signal') or ''
        if not rst:
            continue
        tgt = c.get('target_register') or '?'
        chain = c.get('d_input_gate_chain') or []
        expr = c.get('d_input_expected_function') or ''
        # Reset name may appear as IReset, IReset_, or with bit-select; bare-word search.
        rst_word_re = re.compile(rf'\b{re.escape(rst)}\b')
        in_expr  = bool(rst_word_re.search(expr))
        in_chain = False
        for g in chain:
            for inp in (g.get('inputs') or []):
                if isinstance(inp, str) and rst_word_re.search(inp):
                    in_chain = True; break
            if in_chain: break
            # Also accept reset reuse via reuse_existing_wire pointing to the reset register's Q
            ips = g.get('inputs_per_stage') or {}
            for stg_wire in ips.values():
                if isinstance(stg_wire, str) and rst_word_re.search(stg_wire):
                    in_chain = True; break
            if in_chain: break
        if not in_expr and not in_chain:
            reset_inclusion_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/10-RESET-MISSING]: "
                f"reset_baked_in_d_input=True with reset_signal={rst!r} but the reset name "
                f"appears in NEITHER d_input_expected_function NOR any chain entry's inputs. "
                f"DFF has no reset pin → reset MUST be baked into D as `~{rst} & <data_logic>`. "
                f"FM Synth-vs-RTL will fail (D=function-of-inputs in netlist vs D=0 during reset in RTL).")
            overall_pass = False
        elif not in_expr:
            reset_inclusion_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/10-RESET-MISSING-EXPR]: "
                f"reset_signal={rst!r} appears in chain but NOT in d_input_expected_function. "
                f"Chain-equivalence check (Gap E) will pass against an incomplete reference → "
                f"silently masks reset-handling bugs. Update d_input_expected_function to include `~{rst}`.")
            overall_pass = False
        elif not in_chain:
            reset_inclusion_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/10-RESET-MISSING-CHAIN]: "
                f"d_input_expected_function references {rst!r} but no chain entry consumes it. "
                f"Chain is functionally incomplete vs declared expected_function.")
            overall_pass = False

    # ── Check 27 — MUX_SELECT field consistency ─────────────────────────────
    # When mux_select_i{0,1}_net SHOULD come from new_select_inputs (because
    # the new MUX inputs are new_port signals, not yet in netlist), assert the
    # values agree. Run 20260512070625 root cause #2: AI populated
    # mux_select_i0_net="ctmn_517750" (random CTS-renamed wire) while
    # new_select_inputs[0]="EcoUseSdpOutstRdCnt" (correct). Studier may pick
    # the wrong field downstream → wrong AND2 inputs → FM logical mismatch.
    mux_select_issues = []
    rtl_diff_doc = json.loads(open(args.rtl_diff).read())
    for idx, ch in enumerate(rtl_diff_doc.get('changes', [])):
        if ch.get('change_type') != 'wire_swap':
            continue
        new_inputs = ch.get('new_select_inputs') or []
        from_change = ch.get('new_select_inputs_from_change') or []
        if not new_inputs or len(new_inputs) < 2:
            continue
        i0_net = ch.get('mux_select_i0_net')
        i1_net = ch.get('mux_select_i1_net')
        # Only enforce when the corresponding flag says new_port
        for k, field_name, actual in (
            (0, 'mux_select_i0_net', i0_net),
            (1, 'mux_select_i1_net', i1_net),
        ):
            if k >= len(from_change) or not from_change[k]:
                continue   # not a new_port — flat-net resolve is allowed
            expected = new_inputs[k]
            if actual != expected:
                tgt = ch.get('target_register') or ch.get('new_token') or '?'
                mux_select_issues.append(
                    f"changes[{idx}] target={tgt} [FAIL/27-MUX-SELECT-FIELD-MISMATCH]: "
                    f"{field_name}={actual!r} but new_select_inputs[{k}]={expected!r} "
                    f"(new_select_inputs_from_change[{k}]=true means this signal is a new_port "
                    f"that doesn't exist as a flat net yet). The {field_name} field must equal "
                    f"the symbolic RTL name from new_select_inputs[k] — flat-net-resolve grabbed "
                    f"an unrelated wire. Step 3 studier reading {field_name} would build the "
                    f"wrong AND2 inputs → FM logical mismatch on {tgt}. "
                    f"Fix Step E mux_select branch to use new_select_inputs[k] verbatim when "
                    f"new_select_inputs_from_change[k]=true."
                )
                overall_pass = False

    # Check: intermediate_net_insertion must not use MUX2 cascade.
    # MUX2 cascade creates structural cone divergence from SynRtl — FM sees
    # globally unmatched cut-points in the MUX select paths → thousands of failures.
    # Use compound gates (OA12/OAI21/AN3/ND3) instead.
    and_term_mux_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('fallback_strategy') != 'intermediate_net_insertion':
            continue
        chain = c.get('new_condition_gate_chain') or []
        mux2_gates = [g.get('gate_function','') for g in chain
                      if g.get('gate_function','').upper().startswith('MUX')]
        if mux2_gates:
            and_term_mux_issues.append(
                f"changes[{idx}] [FAIL/INTERMED-MUX2]: intermediate_net_insertion "
                f"new_condition_gate_chain contains {len(mux2_gates)} MUX2 gate(s). "
                f"MUX2 cascade causes structural cone divergence from SynRtl — FM sees "
                f"globally unmatched cut-points → thousands of Synth failures. "
                f"Use compound gates (OA12/OAI21/AN3/ND3) matching synthesis output instead.")
            overall_pass = False

    # Check: and_term changes must record old_driver_inverting + old_driver_cell_type
    # + and_term_gate_input (the module-scope port name, NOT the parent flat_net_name).
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'and_term':
            continue
        if c.get('old_driver_inverting') is None:
            and_term_mux_issues.append(
                f"changes[{idx}] [FAIL/AND-TERM-NO-POLARITY]: and_term change for "
                f"'{c.get('old_token','?')}' missing old_driver_inverting. "
                f"Grep PreEco Synthesize for gate driving old_token, record "
                f"old_driver_cell_type + old_driver_inverting.")
            overall_pass = False
        if not c.get('old_driver_cell_type'):
            and_term_mux_issues.append(
                f"changes[{idx}] [FAIL/AND-TERM-NO-CELL-TYPE]: and_term change for "
                f"'{c.get('old_token','?')}' missing old_driver_cell_type. "
                f"Required alongside old_driver_inverting.")
            overall_pass = False
        if not c.get('and_term_gate_input'):
            and_term_mux_issues.append(
                f"changes[{idx}] [WARN/AND-TERM-NO-GATE-INPUT]: and_term change for "
                f"'{c.get('old_token','?')}' missing and_term_gate_input. "
                f"Must be the port/signal name INSIDE the declaring module (not flat_net_name). "
                f"Required for studier to wire new AND gate correctly.")
        chain = c.get('new_condition_gate_chain') or []
        has_mux = any(g.get('gate_function', '').upper().startswith('MUX') for g in chain)
        if has_mux:
            and_term_mux_issues.append(
                f"changes[{idx}]: change_type='and_term' but new_condition_gate_chain "
                f"contains MUX2 gate(s) — this is a priority chain, NOT a simple and_term. "
                f"Must be classified as 'wire_swap' with fallback_strategy='intermediate_net_insertion'. "
                f"Studier will do simple gate modification and skip the MUX cascade.")
            overall_pass = False

    # AND-TERM-BOOL check lives in eco_validate_step3.py — the gate chain for and_term
    # is built by the studier (Step 3), not recorded in rtl_diff.json at Step 1 time.
    # Step 1 only enforces that old_driver_inverting is present (AND-TERM-NO-POLARITY above).
    and_term_bool_issues = []

    # Check: PENDING_FM_RESOLUTION on gate-structural inputs (inverted / comparison)
    # Check 9f — intermediate_net_insertion uses stage-unstable signals.
    # When intermediate_net_insertion is chosen but the new_condition_gate_chain
    # contains signals with 0 occurrences in any PreEco stage (synthesis-only
    # internal nets like phfnn_*, N<6-digit> synthesis nodes, etc.), the chain
    # will produce per-stage divergence that FM cannot verify without SVF.
    # Fix: use driver_substitution strategy instead — find a named intermediate
    # net in the backward cone, rename its driver, add compound gates using ONLY
    # stage-stable signals (new ECO ports, primary inputs).
    intermed_ins_issues = []
    if args.ref_dir:
        _preeco_gz = {
            s: os.path.join(args.ref_dir, 'data', 'PreEco', f'{s}.v.gz')
            for s in ('Synthesize', 'PrePlace', 'Route')
        }
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            if c.get('fallback_strategy') != 'intermediate_net_insertion':
                continue
            chain = c.get('new_condition_gate_chain') or []
            tgt = c.get('target_register') or c.get('old_token') or '?'
            for g in chain:
                for inp in (g.get('inputs') or []):
                    if not isinstance(inp, str): continue
                    base = inp.split('[')[0]
                    # PENDING_ECO_PORT signals are VALID — new ECO ports are stage-stable
                    # (they exist after ECO application). Do NOT flag these.
                    if 'PENDING_ECO_PORT' in base:
                        continue
                    # PENDING_FM_RESOLUTION signals: allowed when the signal is listed in
                    # condition_inputs_to_query (scheduled for Step 2 Mode H resolution).
                    # Only fail if it is NOT queued for resolution.
                    if 'PENDING_FM_RESOLUTION' in base:
                        raw = base.replace('PENDING_FM_RESOLUTION:', '').split('[')[0]
                        queued = [q.get('signal', '') for q in
                                  (c.get('condition_inputs_to_query') or [])]
                        if raw not in queued:
                            intermed_ins_issues.append(
                                f"changes[{idx}] target={tgt} [FAIL/9f-PENDING-NOT-QUEUED]: "
                                f"intermediate_net_insertion gate uses PENDING_FM_RESOLUTION "
                                f"signal '{raw}' but it is not in condition_inputs_to_query. "
                                f"Add it to condition_inputs_to_query so Step 2 resolves it via Mode H.")
                            overall_pass = False
                        continue
                    if base.startswith(("1'b", "0'b", "n_eco_", "SEQMAP_NET", "PENDING", "ECO_")): continue
                    # Check existence in all 3 PreEco stages
                    for stage, gz in _preeco_gz.items():
                        if not os.path.exists(gz): continue
                        try:
                            import subprocess as _sp
                            r = _sp.run(f'zgrep -c "{base}" {gz}',
                                shell=True, capture_output=True, text=True, timeout=30)
                            cnt = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
                            if cnt == 0:
                                intermed_ins_issues.append(
                                    f"changes[{idx}] target={tgt} [FAIL/9f-STAGE-UNSTABLE]: "
                                    f"intermediate_net_insertion gate uses '{base}' which has "
                                    f"0 occurrences in {stage} PreEco — signal won't survive P&R. "
                                    f"FIRST PREFERENCE: classify as `and_term` (NOT wire_swap) "
                                    f"when exactly ONE new else-if branch is prepended AND the old "
                                    f"D-input has a compound-cell driver at hop 0 — see "
                                    f"rtl_diff_analyzer.md `PREFER and_term WHEN FEASIBLE` rule. "
                                    f"and_term reuses the existing compound cell, avoids inserting "
                                    f"new gates with unstable inputs, and is FM-friendliest. "
                                    f"FALLBACK ONLY IF and_term polarity check fails for both "
                                    f"NOR2 and INR2 candidates: use driver_substitution (find a "
                                    f"named net 2-3 hops upstream of the pivot, rename its driver, "
                                    f"add compound gates using only stage-stable signals).")
                                overall_pass = False
                                break  # one stage failure is enough to flag
                        except Exception:
                            pass

    # Check 9g-DRVSUB-SCRIPT-IGNORED: if eco_drvsub_target_<register>.json exists
    # in the same data directory AND shows stage_stable=true, the wire_swap change
    # for that register MUST use fallback_strategy="driver_substitution".
    # Agent ignoring the script result and choosing intermediate_net_insertion
    # instead is a hard failure — the script is the authoritative source.
    import glob as _glob
    driver_sub_issues = []
    rtl_diff_dir = os.path.dirname(os.path.abspath(args.rtl_diff))
    # Extract tag from filename (rtl_diff JSON may not have 'tag' field)
    _rtl_fname = os.path.basename(args.rtl_diff)  # e.g. 20260515080721_eco_rtl_diff.json
    tag = rtl_diff.get('tag') or _rtl_fname.split('_eco_rtl_diff')[0]
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'wire_swap':
            continue
        reg = c.get('target_register', '')
        if not reg:
            continue
        # Look for drvsub script output for this register
        drvsub_paths = (
            _glob.glob(os.path.join(rtl_diff_dir, f'{tag}_eco_drvsub_target_{reg}.json')) +
            _glob.glob(os.path.join(rtl_diff_dir, f'{tag}_eco_drvsub_target.json'))
        )
        for dp in drvsub_paths:
            try:
                ds = json.load(open(dp))
                if ds.get('stage_stable') and ds.get('driver_sub_target_net'):
                    actual_fs = c.get('fallback_strategy', '')
                    drvsub_tgt = ds['driver_sub_target_net']
                    if actual_fs not in ('driver_substitution', 'intermediate_net_insertion'):
                        driver_sub_issues.append(
                            f"changes[{idx}] [FAIL/9g-DRVSUB-SCRIPT-IGNORED]: "
                            f"eco_find_drvsub_target.py returned stage_stable=True "
                            f"with target='{drvsub_tgt}' for register '{reg}', "
                            f"but agent chose fallback_strategy='{actual_fs}'. "
                            f"Must use driver_substitution (all conditions stage-stable) or "
                            f"intermediate_net_insertion (any condition has synthesis-internal signals)."
                        )
                        overall_pass = False
                    elif actual_fs == 'intermediate_net_insertion':
                        # Allowed when conditions have synthesis-internal signals.
                        # Verify the final gate in the chain outputs to drvsub_tgt (same net).
                        chain = c.get('new_condition_gate_chain') or []
                        final_out = chain[-1].get('output_net', '') if chain else ''
                        if final_out and final_out != drvsub_tgt:
                            driver_sub_issues.append(
                                f"changes[{idx}] [FAIL/9g-INTERMED-WRONG-TARGET]: "
                                f"intermediate_net_insertion chosen but final gate outputs to "
                                f"'{final_out}', not drvsub target '{drvsub_tgt}'. "
                                f"Final gate MUST output to '{drvsub_tgt}'."
                            )
                            overall_pass = False
            except Exception:
                pass

    # Check 9g — driver_substitution rules enforcement
    # Validates all 5 mandatory rules for driver_substitution target selection.
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('fallback_strategy') != 'driver_substitution': continue
        tgt = c.get('driver_sub_target_net', '')
        chain = c.get('new_condition_gate_chain') or []
        old_tok = c.get('old_token') or c.get('new_token') or '?'

        # Rule 1: target must NOT be the pivot net itself (SEQMAP_NET_*, old_token)
        if tgt and (tgt == old_tok or 'SEQMAP_NET' in tgt or tgt.startswith('SEQMAP')):
            driver_sub_issues.append(
                f"changes[{idx}] [FAIL/9g-DRVSUB-PIVOT-TARGET]: driver_sub_target_net='{tgt}' "
                f"is the pivot net itself — NEVER target the pivot net. Walk 2-5 hops UPSTREAM "
                f"to find a named intermediate net (ctmn_*) driven by a compound gate. "
                f"The pivot net path must remain UNCHANGED.")
            overall_pass = False

        # Rule 1a: driver_sub_target_net driver must NOT be a compound AOI/OAI gate
        # Primary: check pivot_net_driver_type field recorded by agent (most reliable).
        # Fallback: grep Synthesize for .ZN?(net) pattern with correct Verilog syntax.
        _consumer_fail_msg = (
            f"changes[{idx}] [FAIL/9g-DRVSUB-CONSUMER-TARGET]: "
            f"driver_sub_target_net='{tgt}' is driven by a compound AOI/OAI gate "
            f"(CONSUMER of the old expression, not its PRODUCER). "
            f"Go 1 hop further upstream to find the simpler functional gate "
            f"(XNR2/AND2/OR2) whose output enters this compound gate — that IS the target."
        )
        _consumer_detected = False
        _driver_type_reported = c.get('pivot_net_driver_type', '')
        if _driver_type_reported and re.search(r'^(AOI|OAI|AO[0-9]{1,2}|OA[0-9]{1,2})', _driver_type_reported, re.I):
            _consumer_detected = True
            driver_sub_issues.append(
                f"changes[{idx}] [FAIL/9g-DRVSUB-CONSUMER-TARGET]: "
                f"driver_sub_target_net='{tgt}' pivot_net_driver_type='{_driver_type_reported}' "
                f"is an AOI/OAI compound gate (CONSUMER). Go 1 hop upstream to the simpler "
                f"functional gate whose output feeds INTO this AOI/OAI — that output IS the target.")
            overall_pass = False

        if not _consumer_detected and tgt and args.ref_dir:
            gz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
            if os.path.exists(gz):
                try:
                    import subprocess as _sp2
                    # Correct pattern: match .ZN(net) or .Z(net) — no spaces around net name
                    r = _sp2.run(
                        f'zgrep -m5 -E "\\.ZN?\\s*\\(\\s*{re.escape(tgt)}\\s*\\)" {gz}',
                        shell=True, capture_output=True, text=True, timeout=30
                    )
                    for line in (r.stdout or '').splitlines():
                        cell = line.strip().split()[0] if line.strip() else ''
                        if re.search(r'^(AOI|OAI|AO[0-9]{1,2}|OA[0-9]{1,2})', cell, re.I):
                            driver_sub_issues.append(_consumer_fail_msg)
                            overall_pass = False
                            break
                except Exception:
                    pass

        # Rule 1b: driver_sub_renamed_to MUST appear in at least one gate's inputs
        # Without this, the old default expression (ECO_<jira>_net_orig) is completely
        # lost — the chain has no fallback case when no condition is true.
        renamed_to = c.get('driver_sub_renamed_to', '')
        if renamed_to and chain:
            uses_renamed = any(renamed_to in str(g.get('inputs', [])) for g in chain)
            if not uses_renamed:
                driver_sub_issues.append(
                    f"changes[{idx}] [FAIL/9g-DRVSUB-NO-DEFAULT]: driver_substitution chain "
                    f"never uses '{renamed_to}' (the renamed old expression) as a gate input. "
                    f"The old default case (BothArbPickCmds/old_expr) is completely lost. "
                    f"The final combination gate MUST include '{renamed_to}' as input — "
                    f"e.g. OA12(Cond2_trigger, {renamed_to}, ~Cond1_trigger) → {tgt}.")
                overall_pass = False

        # Rule 2: Last gate in chain MUST output driver_sub_target_net
        if chain and tgt:
            last_out = chain[-1].get('output_net', '')
            if last_out != tgt:
                driver_sub_issues.append(
                    f"changes[{idx}] [FAIL/9g-DRVSUB-INCOMPLETE]: driver_substitution chain "
                    f"last gate outputs '{last_out}' but must output '{tgt}' (driver_sub_target_net). "
                    f"The chain is INCOMPLETE — missing the final combination gate that drives "
                    f"'{tgt}' with the combined logic: "
                    f"(old_expr=ECO_<jira>_net_orig, Cond1_trigger, Cond2_trigger). "
                    f"Without this, '{tgt}' is UNDRIVEN after rename → FM ABORT. "
                    f"Add a final gate (e.g. OA12/OAI21/AO21) that combines the condition "
                    f"outputs + ECO_<jira>_net_orig and outputs '{tgt}'.")
                overall_pass = False

        # Rule 3: No MUX2 cascade in driver_substitution chains
        mux_gates = [g for g in chain if 'MUX' in g.get('gate_function','').upper()]
        if mux_gates:
            driver_sub_issues.append(
                f"changes[{idx}] [FAIL/9g-DRVSUB-NO-MUX]: driver_substitution chain contains "
                f"{len(mux_gates)} MUX2 gate(s) — MUX cascade belongs to intermediate_net_insertion only. "
                f"driver_substitution uses OA12/OAI21/AN3/ND3 compound gates DIRECTLY replacing "
                f"the target net driver. Remove MUX gates and use compound gates instead.")
            overall_pass = False

        # Rule: No PENDING_FM_RESOLUTION in driver_substitution chain (Check 9g)
        # Rule 4b: when driver_substitution has PENDING conditions, they must be REMOVED
        # from the chain — not kept and resolved in Step 2.
        pending_found = []
        for g in chain:
            for inp in (g.get('inputs') or []):
                if 'PENDING_FM_RESOLUTION' in str(inp):
                    raw = str(inp).replace('PENDING_FM_RESOLUTION:', '')
                    pending_found.append(raw)
        if pending_found:
            driver_sub_issues.append(
                f"changes[{idx}] [FAIL/9g-DRVSUB-PENDING]: driver_substitution chain contains "
                f"PENDING_FM_RESOLUTION signals: {list(set(pending_found))}. "
                f"These conditions MUST be removed from the chain (Rule 4b) — "
                f"driver_substitution uses only stage-stable signals. "
                f"Keep only conditions that use ECO ports and primary inputs. "
                f"If removed conditions are logically required, fall through to E4c.")
            overall_pass = False

    # Check 9h — driver_substitution: at least one stage-stable condition must remain
    # after removing PENDING_FM_RESOLUTION conditions. If all conditions were PENDING,
    # driver_substitution cannot be used at all — fall through to E4c/E4d.
    driver_sub_empty_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('fallback_strategy') != 'driver_substitution': continue
        chain = c.get('new_condition_gate_chain') or []
        tgt = c.get('old_token') or c.get('new_token') or '?'
        # Count gates with ALL inputs stage-stable (no PENDING_FM_RESOLUTION)
        stable_gates = []
        for g in chain:
            all_stable = all('PENDING_FM_RESOLUTION' not in str(i) for i in (g.get('inputs') or []))
            if all_stable and 'MUX' not in g.get('gate_function','').upper():
                stable_gates.append(g.get('seq','?'))
        if chain and not stable_gates:
            driver_sub_empty_issues.append(
                f"changes[{idx}] target={tgt} [FAIL/9h-DRVSUB-EMPTY]: after removing "
                f"PENDING_FM_RESOLUTION conditions, NO stage-stable gate conditions remain. "
                f"driver_substitution requires at least one condition using only ECO ports "
                f"and primary inputs. Fall through to E4c/E4d instead.")
            overall_pass = False

    # These should be decomposed as INV/NAND/AND gates, not marked as PENDING.
    # PENDING is only valid for raw RTL signal names that V3 grep cannot find.
    pending_structural_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        for fld in ('d_input_gate_chain', 'new_condition_gate_chain'):
            for g in (c.get(fld) or []):
                for inp in (g.get('inputs') or []):
                    if not isinstance(inp, str) or not inp.startswith('PENDING_FM_RESOLUTION:'):
                        continue
                    raw = inp[len('PENDING_FM_RESOLUTION:'):]
                    # Structural patterns that should never be PENDING.
                    # Rule: if the PENDING name itself describes a gate operation
                    # (~X inversion, X==K comparison) it should be decomposed as
                    # a gate — not looked up by FM.
                    # Heuristic: common synthesis suffixes that indicate the signal
                    # is a derived/inverted/compared form rather than a raw RTL reg.
                    structural = bool(re.search(
                        r'(_inv\d*|_bar|_n|_neq\w*|_eq\w*|_not\w*|_inverted)$',
                        raw, re.IGNORECASE
                    ))
                    if structural:
                        pending_structural_issues.append(
                            f"changes[{idx}].{fld}[{g.get('seq','?')}]: "
                            f"input {inp!r} looks like a gate operation (~X or X==K bit) "
                            f"that should be decomposed as INV/NAND/AND gate, not PENDING_FM_RESOLUTION. "
                            f"Only raw RTL signal names that fail V3 grep should be PENDING.")
                        overall_pass = False

    # ── Fix A: spare CSR bus bit referenced outside a Mode-I bridge path ──────
    # A spare/UNCONNECTED CSR register bit (REG_*[N] / REG_*_N_) is undriven in the
    # gate-level netlist until a Mode-I bridge wires it across module boundaries.
    # The DFF wrapper (eco_emit_dff_entry -> eco_modei_chain_input_check) auto-bridges
    # such bits when they are chain leaves of a new_logic DFF, so new_logic /
    # new_logic_dff are exempt. A non-DFF change (e.g. a new_logic_gate wire alias)
    # that sources a spare CSR bit WITHOUT original_unconnected_net and WITHOUT a
    # companion port_connection bridging it never triggers Mode-I -> net stays
    # undriven -> FM fail.
    csr_bit_re = re.compile(r'\bREG_\w+(?:\[\d+\]|_\d+_)')
    def _csr_bits(c):
        hits = set()
        for k in ('d_input_resolved_net', 'flat_net_name'):
            v = c.get(k)
            if isinstance(v, str):
                hits |= {m.group(0) for m in csr_bit_re.finditer(v)}
        for g in (c.get('d_input_gate_chain') or []):
            for inp in (g.get('inputs') or []):
                if isinstance(inp, str):
                    hits |= {m.group(0) for m in csr_bit_re.finditer(inp)}
        return hits
    _bridged_nets = set()
    for c in rtl_diff.get('changes', []):
        if c.get('change_type') == 'port_connection':
            for k in ('net_name', 'flat_net_name', 'new_token'):
                v = c.get(k)
                if isinstance(v, str):
                    _bridged_nets.add(v)
    csr_bridge_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') in ('new_logic', 'new_logic_dff'):
            continue
        bits = _csr_bits(c)
        if not bits or c.get('original_unconnected_net') or (bits & _bridged_nets):
            continue
        csr_bridge_issues.append(
            f"changes[{idx}] type={c.get('change_type')}: references spare CSR bus bit(s) "
            f"{sorted(bits)} as a source but is not a DFF chain leaf and has no Mode-I bridge "
            f"(original_unconnected_net unset, no companion port_connection). Spare CSR bits are "
            f"UNCONNECTED in gate-level until bridged. Route it through Mode-I: emit as a new_logic "
            f"DFF chain leaf, or set original_unconnected_net + a companion port_connection. "
            f"See rtl_diff_analyzer.md spare-CSR-bit guidance.")
    if csr_bridge_issues:
        overall_pass = False

    # ── Fix B: clock-gate enable_swap only valid on a single-update register ───
    # enable_swap gates WHEN the whole register updates. Correct only when the register
    # has ONE functional update branch. A multi-branch priority register (>=2 else-if
    # branches loading different values) whose single branch guard was narrowed is a
    # per-branch next-state gate, not enable_swap: clock-gating the shared enable would
    # freeze the non-gated branches -> regression.
    def _extract_always_block(text, start):
        i = text.find('begin', start)
        semi = text.find(';', start)
        if i == -1 or (semi != -1 and semi < i):
            end = semi if semi != -1 else len(text)
            return text[start:end]
        depth = 0
        for tm in re.finditer(r'\bbegin\b|\bend\b', text[i:]):
            depth += 1 if tm.group(0) == 'begin' else -1
            if depth == 0:
                return text[i:i + tm.end()]
        return text[start:]
    def _count_functional_branches(text, target):
        assign_re = re.compile(r'\b' + re.escape(target) + r'\b[^\n;]*<=')
        for m in re.finditer(r'always\s*@', text):
            body = _extract_always_block(text, m.start())
            if assign_re.search(body):
                return len(re.findall(r'\belse\s+if\b', body))
        return None
    enable_branch_issues = []
    if args.ref_dir:
        import os as _os2
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            if c.get('change_type') != 'enable_swap':
                continue
            tgt, fn = c.get('target_register'), c.get('file')
            if not (tgt and fn):
                continue
            rtl_text = None
            for sub in ('data/PreEco/SynRtl', 'data/SynRtl'):
                p = _os2.path.join(args.ref_dir, sub, fn)
                if _os2.path.exists(p):
                    try:
                        rtl_text = open(p).read()
                    except Exception:
                        rtl_text = None
                    break
            if rtl_text is None:
                continue
            nbr = _count_functional_branches(rtl_text, tgt)
            if nbr is not None and nbr >= 2:
                enable_branch_issues.append(
                    f"changes[{idx}] target={tgt}: enable_swap on a multi-branch priority "
                    f"register ({nbr} functional else-if branches in {fn}). Clock-gating the shared "
                    f"enable freezes the non-gated branches -> regression. This is a per-branch "
                    f"next-state gate (gate the narrowed branch's value, hold otherwise), not "
                    f"enable_swap. See rtl_diff_analyzer.md enable_swap branch-count guidance.")
    if enable_branch_issues:
        overall_pass = False

    # ── Fix C: and_term driver_rename must cover ALL consumers of old_token ────
    # When an and_term renames the old driver's output (driver_rename) instead of the
    # DFF-pin-rewire pattern, the original net ceases to exist. Every cell that read
    # old_token MUST be in rewire_consumers, else it reads an undriven net after the
    # rename -> FM fail.
    andterm_fanout_issues = []
    if args.ref_dir:
        import os as _os3, gzip as _gz3
        _gz = _os3.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
        _syn_text = None
        if _os3.path.exists(_gz):
            try:
                with _gz3.open(_gz, 'rt') as _f:
                    _syn_text = _f.read()
            except Exception:
                _syn_text = None
        _OUT_PINS = {'Z', 'ZN', 'ZN1', 'ZN2', 'Q', 'QN', 'CO', 'S', 'SO', 'CON'}
        def _consumers_of(text, mod, net):
            mm = re.search(r'(?m)^module\s+\w*' + re.escape(mod) + r'\b.*?^endmodule',
                           text, re.DOTALL) if mod else None
            body = mm.group(0) if mm else text
            cons = set()
            net_pin = re.compile(r'\.(\w+)\s*\(\s*' + re.escape(net) + r'\s*\)')
            for im in re.finditer(r'([A-Z][A-Za-z0-9_]+)\s+([A-Za-z_]\w*)\s*\(([^;]*?)\)\s*;',
                                  body, re.DOTALL):
                inst, ports = im.group(2), im.group(3)
                for pm in net_pin.finditer(ports):
                    if pm.group(1) not in _OUT_PINS:
                        cons.add(inst)
                        break
            return cons
        if _syn_text:
            for idx, c in enumerate(rtl_diff.get('changes', [])):
                if c.get('change_type') != 'and_term':
                    continue
                dr = c.get('driver_rename')
                if not dr:
                    continue
                old = dr.get('old_output_net') or c.get('old_token')
                if not old:
                    continue
                actual  = _consumers_of(_syn_text, c.get('module_name') or '', old)
                rewired = {r.get('cell_instance') for r in (c.get('rewire_consumers') or [])}
                missing = sorted(actual - rewired)
                if missing:
                    andterm_fanout_issues.append(
                        f"changes[{idx}] and_term old_token={old!r}: driver_rename renames its "
                        f"driver, but {len(missing)} consumer(s) {missing} are NOT in "
                        f"rewire_consumers -> they read an undriven net after the rename. Either "
                        f"rewire ALL consumers, or drop driver_rename and use the keep-driver + "
                        f"parallel-gate form (rewire only the intended consumers, leave old_token "
                        f"driven). See rtl_diff_analyzer.md and_term DFF-pin-rewire rule.")
    if andterm_fanout_issues:
        overall_pass = False

    # ── Fix D: Mode-I bridge must be anchored in its declaring module ─────────
    # An entry with original_unconnected_net renames a spare bus bit. That UNCONNECTED
    # net MUST exist in module_name's gate-level body — otherwise it names a net from a
    # DIFFERENT hierarchy level (e.g. the child's internal number instead of the
    # parent's child-instance-bus number), the applier finds nothing to rename, and the
    # bridge never lands -> signal undriven.
    mode_i_anchor_issues = []
    if args.ref_dir and _syn_text:
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            unc = c.get('original_unconnected_net')
            if not unc:
                continue
            mod = c.get('module_name') or ''
            mm = re.search(r'(?m)^module\s+\w*' + re.escape(mod) + r'\b.*?^endmodule',
                           _syn_text, re.DOTALL) if mod else None
            body = mm.group(0) if mm else ''
            if body and not re.search(r'\b' + re.escape(unc) + r'\b', body):
                mode_i_anchor_issues.append(
                    f"changes[{idx}] Mode-I bridge: original_unconnected_net={unc!r} does NOT "
                    f"appear in module {mod!r} — it belongs to a different hierarchy level. The "
                    f"applier will find nothing to rename -> bridge never lands -> signal undriven. "
                    f"Use the UNCONNECTED net that actually sits on this module's child-instance "
                    f"bus bit, or emit the bridge at the module that owns this net.")
    if mode_i_anchor_issues:
        overall_pass = False

    # ── Fix E: multi-bit register branch-gate needs a feedback hold-mux ────────
    # An and_term whose gated net fans out to >=2 next-state DFF bits of the SAME
    # register (a multi-bit counter with implicit-hold default) must build a per-bit
    # load-enable HOLD MUX that feeds back the current register value — NOT condition-
    # gating of the shared select net. Condition-gating without feedback drives the
    # next-state cone into don't-care input combinations and may not hold correctly
    # (FM logic mismatch). The engineer reference uses an explicit feedback mux.
    mb_holdmux_issues = []
    if args.ref_dir and _syn_text:
        for idx, c in enumerate(rtl_diff.get('changes', [])):
            if c.get('change_type') != 'and_term':
                continue
            old = c.get('old_token'); treg = c.get('target_register') or ''
            if not old or not treg:
                continue
            mm = re.search(r'(?m)^module\s+\w*' + re.escape(c.get('module_name') or '')
                           + r'\b.*?^endmodule', _syn_text, re.DOTALL) if c.get('module_name') else None
            body = mm.group(0) if mm else _syn_text
            cons = set()
            for m in re.finditer(r'([A-Z][A-Za-z0-9_]+)\s+([A-Za-z_]\w*)\s*\(([^;]*?)\)\s*;',
                                 body, re.DOTALL):
                ports = m.group(3)
                for pm in re.finditer(r'\.(\w+)\s*\(\s*' + re.escape(old) + r'\s*\)', ports):
                    if pm.group(1) not in ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'):
                        om = re.search(r'\.(?:Z|ZN|ZN1)\s*\(\s*(\w+)\s*\)', ports)
                        if om:
                            cons.add(om.group(1))
                        break
            bits = set()
            for net in cons:
                for dm in re.finditer(re.escape(treg) + r'_reg_(\d+)_\s*\([^;]*?\.D\s*\(\s*'
                                      + re.escape(net) + r'\s*\)', body, re.DOTALL):
                    bits.add(dm.group(1))
            if len(bits) < 2:
                continue
            ins = []
            for g in (c.get('and_term_gate_chain_design') or []):
                ins += (g.get('inputs') or [])
            if c.get('and_term_additional_input'):
                ins.append(c.get('and_term_additional_input'))
            fb = any(re.search(re.escape(treg) + r'(\[\d+\]|_\d+_)', str(i)) for i in ins)
            if not fb:
                mb_holdmux_issues.append(
                    f"changes[{idx}] and_term on multi-bit register {treg!r}: gated net {old!r} "
                    f"feeds {len(bits)} next-state DFF bits {sorted(bits)} but the gate has NO "
                    f"feedback of the register's current value. Condition-gating a shared select "
                    f"without feedback drives the next-state cone into don't-care combinations and "
                    f"may not hold correctly (FM logic mismatch). Build a per-bit load-enable HOLD "
                    f"MUX: D_new[b] = AO22(gate, D_orig[b], ~gate, {treg}[b]) feeding back the "
                    f"current register bit (engineer reference). See rtl_diff_analyzer.md and_term "
                    f"multi-bit hold-mux rule.")
    if mb_holdmux_issues:
        overall_pass = False

    # ── Fix F: multi-bit hold-mux enable must derive from the ORIGINAL next-state
    # The correct load-enable for a counter hold-mux is `(N28==N29) | new_cond` — an
    # equality/XNOR of the ORIGINAL next-state bits OR'd with the new condition. That
    # loads N for every non-decrement transition (preserving ACT/reset) and gates only
    # the decrement by the new condition. An enable built from an unrelated nearby net
    # (e.g. a reset-related ctmn_*) over-gates: when new_cond=0 it holds ALL branches,
    # freezing ACT/reset -> FM logic mismatch. Require the enable cone to reference the
    # d_orig_net of every hold-mux bit.
    holdmux_enable_issues = []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') != 'and_term':
            continue
        chain = c.get('and_term_gate_chain_design') or []
        hm = [g for g in chain if g.get('is_holdmux')]
        if len(hm) < 2:
            continue
        d_orig = {g.get('d_orig_net') for g in hm if g.get('d_orig_net')}
        if not d_orig:
            continue
        en_nets = {(g.get('inputs') or [None])[0] for g in hm if g.get('inputs')}
        by_out = {g.get('output_net'): g for g in chain}
        cone, stack, seen = set(), list(en_nets), set()
        while stack:
            net = stack.pop()
            if net in seen:
                continue
            seen.add(net)
            g = by_out.get(net)
            if g:
                for i in (g.get('inputs') or []):
                    cone.add(i); stack.append(i)
            elif net:
                cone.add(net)
        if not (d_orig <= cone):
            holdmux_enable_issues.append(
                f"changes[{idx}] multi-bit hold-mux enable does NOT derive from the original "
                f"next-state bits {sorted(d_orig)} (enable cone leaves: "
                f"{sorted(x for x in cone if x)[:6]}). An enable from an unrelated net over-gates "
                f"the non-decrement branches (ACT/reset frozen when the new condition is low). "
                f"Build the load-enable as (<d_orig_bit_a>==<d_orig_bit_b>) | <new_condition> "
                f"(XNOR of the original next-state bits OR'd with the new term), matching the "
                f"engineer hold-mux. See rtl_diff_analyzer.md and_term multi-bit hold-mux rule.")
    if holdmux_enable_issues:
        overall_pass = False

    # ── priority_force (#1) + term_op (#2) + pending-term (catch mismodel) ────
    _const_re = re.compile(r"^\d*'[bhdo][0-9a-fA-FxXzZ_]+$|^\d+$")

    def _rhs_is_constant(s):
        """Classify an assignment RHS from the RTL branch.
          True  -> a bare CONSTANT value  (=> priority_force)
          False -> combines signals with an operator (=> and_term / decompose)
          None  -> ambiguous (a lone signal name, etc.) — do not judge."""
        s = str(s or '').strip()
        if not s:
            return None
        # any boolean/ternary/arith combine operator => it's an expression, not a const
        if re.search(r"[&|^?~]|\b(and|or|xor)\b|[+\-*/]", s):
            return False
        if _const_re.match(s):
            return True
        if re.match(r"^[A-Z_][A-Z0-9_]*$", s):   # UMC_MOP_CAS-style define constant
            return True
        return None

    priority_force_issues, term_op_issues, pending_term_issues = [], [], []
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        ct = c.get('change_type')
        # A term punted to PENDING_FM_RESOLUTION is NOT a modeled edit — it means a
        # condition/expression was deferred to a placeholder that fenets can't resolve
        # (an expression is not a net). This is the recdsp WCK-sync CAS mismodel (9666):
        # `and_term ... new=PENDING_FM_RESOLUTION:wck_sync_condition`. Must be a
        # priority_force (force sig=CONST under <cond>) or a built condition_gate_chain.
        if ct in ('and_term', 'wire_swap') and \
           str(c.get('new_token', '')).startswith('PENDING_FM_RESOLUTION'):
            pending_term_issues.append(
                f"changes[{idx}] {ct} (target={c.get('target_register') or c.get('old_token')!r}) has "
                f"new_token={c.get('new_token')!r} — a condition was punted to a PENDING placeholder "
                f"instead of modeled. If it forces a signal to a constant under a new condition, use "
                f"change_type=priority_force; otherwise build the condition as a gate chain. Never PENDING a term.")
        if ct == 'priority_force':
            if not c.get('condition_gate_chain'):
                priority_force_issues.append(
                    f"changes[{idx}] priority_force missing condition_gate_chain — the branch "
                    f"condition must be BUILT as gates, not left as a PENDING/synthetic net.")
            fs = c.get('forced_signals') or []
            if not fs:
                priority_force_issues.append(
                    f"changes[{idx}] priority_force has no forced_signals — every forced signal "
                    f"(e.g. c0vld AND c0mop) must be listed with its constant.")
            for f in fs:
                if not _const_re.match(str(f.get('const', '')).strip()):
                    priority_force_issues.append(
                        f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}].const="
                        f"{f.get('const')!r} is not a valid Verilog constant.")
                # OPCODE-VALUE grounding: const must equal const_macro's RTL define value.
                # Catches a wrong-but-valid opcode (9666: UMC_MOP_CAS silently became
                # 5'b00001 instead of 5'b01011) that the constant-validity check accepts.
                cm = f.get('const_macro')
                if cm and args.ref_dir:
                    import subprocess as _sp
                    _root = os.path.join(args.ref_dir, 'data', 'PreEco', 'SynRtl')
                    try:
                        _o = _sp.run(['grep', '-rhoE',
                                      r"define[ \t]+" + re.escape(cm) + r"[ \t]+[0-9]+'[bBhHdD][0-9a-fA-FxXzZ_]+",
                                      _root], capture_output=True, text=True, timeout=90).stdout
                        _m2 = re.search(r"([0-9]+'[bBhHdD][0-9a-fA-FxXzZ_]+)", _o)
                        _lit = _m2.group(1) if _m2 else None
                    except Exception:
                        _lit = None
                    def _nc(s):
                        mm = re.match(r"^\s*(\d+)'([bBhHdD])([0-9a-fA-FxXzZ_]+)\s*$", str(s or ''))
                        if not mm:
                            return None
                        dd = mm.group(3).replace('_', '')
                        if re.search(r'[xXzZ]', dd):
                            return None
                        try:
                            return (int(mm.group(1)), int(dd, {'b': 2, 'h': 16, 'd': 10}[mm.group(2).lower()]))
                        except Exception:
                            return None
                    if _lit is not None:   # not-found => cannot verify (do not fail)
                        if _nc(_lit) is None or _nc(f.get('const')) != _nc(_lit):
                            priority_force_issues.append(
                                f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}] "
                                f"const={f.get('const')!r} does NOT match macro {cm} = {_lit!r} from the "
                                f"RTL `define` — WRONG opcode value (the ECO would force the wrong command).")
                # classification proof: the RTL branch RHS must be a bare constant. If the
                # analyzer records assignment_evidence (the verbatim `sig <= <RHS>` text),
                # it MUST be constant-like — an expression here means this is really an
                # and_term (term fold), not a priority_force. Missing evidence => must add it.
                ev = f.get('assignment_evidence')
                if ev is None:
                    priority_force_issues.append(
                        f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}] missing "
                        f"assignment_evidence — record the verbatim RTL RHS (e.g. \"1'b1\", "
                        f"\"UMC_MOP_CAS\") so the priority_force vs and_term classification is provable.")
                elif _rhs_is_constant(ev) is False:
                    priority_force_issues.append(
                        f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}] "
                        f"assignment_evidence={ev!r} COMBINES signals (has an operator) — a "
                        f"priority_force RHS must be a bare CONSTANT. Reclassify as and_term (term "
                        f"fold into an existing expression) or decompose the RHS into a gate chain.")
                bits = f.get('bits') or []
                if not bits:
                    priority_force_issues.append(
                        f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}] missing "
                        f"bits[] — each bit needs {{bit, old_net, dff_cell, dff_pin}} for the force-mux "
                        f"else-leg + DFF-pin rewire.")
                for bspec in bits:
                    if bspec.get('old_net') is None or not bspec.get('dff_cell'):
                        priority_force_issues.append(
                            f"changes[{idx}] priority_force forced_signals[{f.get('signal')!r}] bit "
                            f"{bspec.get('bit')} missing old_net/dff_cell.")
        elif ct == 'and_term':
            op = c.get('term_op')
            if op not in ('and', 'or'):
                term_op_issues.append(
                    f"changes[{idx}] and_term (old_token={c.get('old_token')!r}) missing term_op "
                    f"('and'|'or') — required to pick AND-narrow vs OR-widen gate (Intent-A MRR).")
            # reverse mis-classification guard (opt-in: only when evidence is recorded, so
            # legacy and_term entries without the field are unaffected). If the branch RHS is
            # a BARE constant, the signal is being PINNED to a value, not term-folded → this
            # is a priority_force, not an and_term.
            ev = c.get('assignment_evidence')
            if ev is not None and _rhs_is_constant(ev) is True:
                pending_term_issues.append(
                    f"changes[{idx}] and_term (old_token={c.get('old_token')!r}) has "
                    f"assignment_evidence={ev!r} — a BARE CONSTANT RHS pins the signal to a value, "
                    f"which is a priority_force (force sig=CONST under <cond>), NOT a term fold. "
                    f"Reclassify as change_type=priority_force with forced_signals[].bits[].")
    # priority_force condition-leaf availability (needs --ref-dir + extractor)
    pf_leaf_issues = _pf_condition_leaf_issues(rtl_diff, args.ref_dir)
    priority_force_issues.extend(pf_leaf_issues)
    priority_force_issues.extend(_pf_const_macro_issues(rtl_diff))

    # completeness: every changed logic hunk in the RTL diff must be represented
    hunk_issues = _hunk_completeness_issues(rtl_diff, args.ref_dir) if args.ref_dir else []
    priority_force_issues.extend(hunk_issues)

    if priority_force_issues:
        overall_pass = False
    if pending_term_issues:
        overall_pass = False
    # term_op is ADVISORY (do not fail) — legacy and_term entries predate the field;
    # correctness is enforced downstream by the Step-3 truth-table check when set.

    # ── EQUALITY-DECODE-PENDED guard ───────────────────────────────────────────
    # An and_term/wire_swap new term of the form (sig == CONST) is NEW combinational
    # logic — a bus-constant equality DECODE (rtl_diff_analyzer §2b) — and MUST be
    # BUILT as comparator gates. It must NOT be deferred as a PENDING_FM_RESOLUTION
    # of the new_token itself: FM cannot find a net that does not yet exist, so it
    # echo-falls-back and Step 3 substitutes an unrelated ECO net. History: 9666 run
    # 20260704085942 left (recdsp_c0mop==UMC_MOP_MRR) as
    # PENDING_FM_RESOLUTION:recdsp_c0mop_mrr_match → Step 3 wired the OR-widen to the
    # WCK-sync condition instead (Intent A silently wrong, all validators green).
    eq_decode_issues = []
    _eq_re = re.compile(r"==\s*(`?\w+|\d+'[bhdoBHDO][0-9a-fA-FxXzZ_]+)")
    for idx, c in enumerate(rtl_diff.get('changes', [])):
        if c.get('change_type') not in ('and_term', 'wire_swap'):
            continue
        nt = c.get('new_token')
        if not nt or not _eq_re.search(str(c.get('context_line') or '')):
            continue
        # If Step 1 emitted a structured equality_decode schema, the deterministic
        # builder (eco_emit_eq_decode.py) constructs the comparator and fixes the
        # combine gate — a PENDING placeholder is then harmless. Only fail when the
        # schema is ABSENT (nothing will build the match → Step 3 substitutes wrong).
        if isinstance(c.get('equality_decode'), dict):
            continue
        # Does the gate chain PEND the new_token itself (an equality decode deferred
        # instead of built)?
        pended_self = False
        for g in (c.get('and_term_gate_chain_design') or []):
            for inp in (g.get('inputs') or []):
                if isinstance(inp, str) and inp.startswith('PENDING_FM_RESOLUTION') \
                        and inp.split(':', 1)[-1] == nt:
                    pended_self = True
        if pended_self:
            eq_decode_issues.append(
                f"changes[{idx}] {c.get('change_type')} new_token={nt!r} is an EQUALITY match "
                f"(context has '== <CONST>') but the gate chain defers it as "
                f"PENDING_FM_RESOLUTION:{nt} instead of BUILDING it. (sig == CONST) is NEW logic — "
                f"decode it into comparator gates (rtl_diff_analyzer §2b bus-constant equality "
                f"decode) in and_term_gate_chain_design, driving a fresh n_eco net. Leaving it "
                f"PENDING makes Step 2 echo-fallback and Step 3 substitute an unrelated net.")
    if eq_decode_issues:
        overall_pass = False

    # ── UNIQUIFIED enumeration guard (needs --ref-dir) ─────────────────────────
    # A per-instance edit (and_term/wire_swap/new_port/port_connection) on a
    # synthesis-uniquified generate array must enumerate ALL N copies in
    # instances[]. N is the netlist ground truth (count of <base>_<i> modules).
    # Catches Step-1 under-enumeration at the earliest point. Fires only for
    # families with >=2 numbered copies tied to a changed module (single-instance
    # ECOs never trigger → regression-safe).
    uniquified_enum_issues = []
    if args.ref_dir:
        import gzip as _gz2
        _sgz = os.path.join(args.ref_dir, 'data', 'PreEco', 'Synthesize.v.gz')
        if not os.path.isfile(_sgz):
            _sgz = os.path.join(args.ref_dir, 'data', 'PostEco', 'Synthesize.v.gz')
        _mods = []
        if os.path.isfile(_sgz):
            try:
                with _gz2.open(_sgz, 'rt', errors='replace') as _fh:
                    for _ln in _fh:
                        _mm = re.match(r'^module\s+(\S+)', _ln)
                        if _mm:
                            _mods.append(_mm.group(1))
            except Exception:
                _mods = []
        if _mods:
            _ure = re.compile(r'^(.*?)_(\d+)$')
            _famsz = {}
            for _nm in _mods:
                _m2 = _ure.match(_nm)
                if _m2:
                    _famsz.setdefault(_m2.group(1), set()).add(int(_m2.group(2)))
            _fams = {b: ix for b, ix in _famsz.items() if len(ix) >= 2}
            for idx, c in enumerate(rtl_diff.get('changes', [])):
                if c.get('change_type') not in ('and_term', 'wire_swap', 'new_port', 'port_connection'):
                    continue
                cb = re.sub(r'_\d+$', '', str(c.get('module_name') or c.get('child_module_name') or ''))
                if not cb:
                    continue
                # netlist family whose base matches this change's module
                fam_n = None
                for b, ix in _fams.items():
                    if b == cb or b.endswith('_' + cb) or b.endswith(cb):
                        fam_n = len(ix); break
                if not fam_n:
                    continue
                n_enum = len(c.get('instances') or [])
                if n_enum < fam_n:
                    uniquified_enum_issues.append(
                        f"changes[{idx}] {c.get('change_type')} on uniquified family "
                        f"{cb!r} enumerates only {n_enum} instance(s) but the netlist has "
                        f"{fam_n} copies (<base>_0..{fam_n-1}). A per-instance edit on a "
                        f"generate array MUST list ALL {fam_n} in instances[] (step 7b) — "
                        f"else Step 2/3 silently drop the un-enumerated copies.")
    if uniquified_enum_issues:
        overall_pass = False

    out = {
        'rtl_diff': args.rtl_diff,
        'eq_decode_issue_count':       len(eq_decode_issues),
        'eq_decode_issues':            eq_decode_issues,
        'uniquified_enum_issue_count': len(uniquified_enum_issues),
        'uniquified_enum_issues':      uniquified_enum_issues,
        'priority_force_issue_count': len(priority_force_issues),
        'priority_force_issues':      priority_force_issues,
        'pending_term_issue_count':   len(pending_term_issues),
        'pending_term_issues':        pending_term_issues,
        'term_op_issue_count':        len(term_op_issues),
        'term_op_issues':             term_op_issues,
        'mux_select_issue_count': len(mux_select_issues),
        'mux_select_issues':      mux_select_issues,
        'wire_swap_count':       len(results),
        'phantom_wire_count':    len(phantom),
        'phantom_wire_issues':   phantom,
        'new_port_issue_count':  len(decl_issues),
        'new_port_issues':       decl_issues,
        'port_conn_issue_count': len(pc_issues),
        'port_conn_issues':      pc_issues,
        'truth_table_issue_count': len(tt_issues),
        'truth_table_issues':      tt_issues,
        'signal_in_scope_issue_count': len(sis_issues),
        'signal_in_scope_issues':      sis_issues,
        'chain_equivalence_issue_count': len(chain_eq_issues),
        'chain_equivalence_issues':      chain_eq_issues,
        'new_logic_field_issue_count':   len(new_logic_field_issues),
        'new_logic_field_issues':        new_logic_field_issues,
        'mode_i_field_issue_count':      len(mode_i_field_issues),
        'mode_i_field_issues':           mode_i_field_issues,
        'scope_field_issue_count':       len(scope_field_issues),
        'scope_field_issues':            scope_field_issues,
        'wire_swap_field_issue_count':   len(wire_swap_field_issues),
        'wire_swap_field_issues':        wire_swap_field_issues,
        'unconnected_var_issue_count':   len(unconnected_var_issues),
        'unconnected_var_issues':        unconnected_var_issues,
        'chain_compactness_issue_count': len(chain_compact_issues),
        'chain_compactness_issues':      chain_compact_issues,
        'reset_inclusion_issue_count':   len(reset_inclusion_issues),
        'reset_inclusion_issues':        reset_inclusion_issues,
        'and_term_mux_issue_count':        len(and_term_mux_issues),
        'and_term_mux_issues':             and_term_mux_issues,
        'and_term_bool_issue_count':       len(and_term_bool_issues),
        'and_term_bool_issues':            and_term_bool_issues,
        'driver_sub_issue_count':          len(driver_sub_issues),
        'driver_sub_issues':               driver_sub_issues,
        'driver_sub_empty_issue_count':    len(driver_sub_empty_issues),
        'driver_sub_empty_issues':         driver_sub_empty_issues,
        'intermed_ins_issue_count':        len(intermed_ins_issues),
        'intermed_ins_issues':             intermed_ins_issues,
        'pending_structural_issue_count': len(pending_structural_issues),
        'pending_structural_issues':      pending_structural_issues,
        'enable_swap_issue_count':        len(enable_swap_issues),
        'enable_swap_issues':             enable_swap_issues,
        'clk_gate_field_issue_count':      len(clk_gate_field_issues),
        'clk_gate_field_issues':          clk_gate_field_issues,
        'cg_verify_issue_count':           len(cg_verify_issues),
        'cg_verify_issues':               cg_verify_issues,
        'cg_other_inputs_issue_count':     len(cg_other_inputs_issues),
        'cg_other_inputs_issues':         cg_other_inputs_issues,
        'd_reset_gate_issue_count':        len(d_reset_gate_issues),
        'd_reset_gate_issues':            d_reset_gate_issues,
        'reset_context_issue_count':       len(reset_context_issues),
        'reset_context_issues':           reset_context_issues,
        'companion_issue_count':           len(companion_issues),
        'companion_issues':               companion_issues,
        'bus_gate_chain_issue_count':      len(bus_gate_chain_issues),
        'bus_gate_chain_issues':          bus_gate_chain_issues,
        'chain_cell_issue_count':          len(chain_cell_issues),
        'chain_cell_issues':              chain_cell_issues,
        'shadow_gate_field_issue_count':   len(shadow_gate_field_issues),
        'shadow_gate_field_issues':        shadow_gate_field_issues,
        'reset_detection_issue_count':     len(reset_detection_issues),
        'reset_detection_issues':         reset_detection_issues,
        'dup_chain_issue_count':           len(dup_chain_issues),
        'dup_chain_issues':               dup_chain_issues,
        'missing_wire_issue_count':        len(missing_wire_issues),
        'missing_wire_issues':            missing_wire_issues,
        'phantom_alias_issue_count':       len(phantom_alias_issues),
        'phantom_alias_issues':           phantom_alias_issues,
        'mux2_in_chain_issue_count':      len(mux2_in_chain_issues),
        'mux2_in_chain_issues':           mux2_in_chain_issues,
        'port_promo_issue_count':          len(port_promo_issues),
        'port_promo_issues':              port_promo_issues,
        'bus_gate_issue_count':           len(bus_gate_issues),
        'bus_gate_issues':                bus_gate_issues,
        'bus_dff_issue_count':            len(bus_dff_issues),
        'scan_pin_issue_count':           len(scan_pin_issues),
        'scan_pin_issues':                scan_pin_issues,
        'and_term_pref_issue_count':      len(and_term_pref_issues),
        'and_term_pref_issues':           and_term_pref_issues,
        'and_term_old_token_issue_count': len(and_term_old_token_issues),
        'and_term_old_token_issues':      and_term_old_token_issues,
        'and_term_pattern_issue_count':   len(and_term_pattern_issues),
        'and_term_pattern_issues':        and_term_pattern_issues,
        'bus_dff_issues':                 bus_dff_issues,
        'csr_bridge_issue_count':         len(csr_bridge_issues),
        'csr_bridge_issues':              csr_bridge_issues,
        'enable_branch_issue_count':      len(enable_branch_issues),
        'enable_branch_issues':           enable_branch_issues,
        'andterm_fanout_issue_count':     len(andterm_fanout_issues),
        'andterm_fanout_issues':          andterm_fanout_issues,
        'mode_i_anchor_issue_count':      len(mode_i_anchor_issues),
        'mode_i_anchor_issues':           mode_i_anchor_issues,
        'mb_holdmux_issue_count':         len(mb_holdmux_issues),
        'mb_holdmux_issues':              mb_holdmux_issues,
        'holdmux_enable_issue_count':     len(holdmux_enable_issues),
        'holdmux_enable_issues':          holdmux_enable_issues,
        'overall_pass':          overall_pass,
        'entries':               results,
    }
    write_result(args.output, out, overall_pass, getattr(args, 'iter', None))

    print('ECO_SCRIPT_LAUNCHED: eco_validate_step1.py')
    print(f'  rtl_diff: {args.rtl_diff}')
    print(f'  entries:  {len(results)}  phantom_wire: {len(phantom)}  new_port_issues: {len(decl_issues)}  port_conn_issues: {len(pc_issues)}  truth_table_issues: {len(tt_issues)}  signal_in_scope_issues: {len(sis_issues)}  chain_equivalence_issues: {len(chain_eq_issues)}  new_logic_field_issues: {len(new_logic_field_issues)}  mode_i_field_issues: {len(mode_i_field_issues)}  scope_field_issues: {len(scope_field_issues)}  wire_swap_field_issues: {len(wire_swap_field_issues)}  unconnected_var_issues: {len(unconnected_var_issues)}  chain_compactness_issues: {len(chain_compact_issues)}  reset_inclusion_issues: {len(reset_inclusion_issues)}')
    print(f'  and_term_bool: {len(and_term_bool_issues)}')
    print(f'  bus_gate_issues: {len(bus_gate_issues)}  bus_dff_issues: {len(bus_dff_issues)}')
    print(f'  overall:  {"PASS" if overall_pass else "FAIL"}')
    for p in phantom:
        print(f'    - {p}')
    for p in decl_issues:
        print(f'    - {p}')
    for p in pc_issues:
        print(f'    - {p}')
    for p in tt_issues:
        print(f'    - {p}')
    for p in sis_issues:
        print(f'    - {p}')
    for p in chain_eq_issues:
        print(f'    - {p}')
    for p in new_logic_field_issues:
        print(f'    - {p}')
    for p in mode_i_field_issues:
        print(f'    - {p}')
    for p in scope_field_issues:
        print(f'    - {p}')
    for p in wire_swap_field_issues:
        print(f'    - {p}')
    for p in unconnected_var_issues:
        print(f'    - {p}')
    for p in chain_compact_issues:
        print(f'    - {p}')
    for p in reset_inclusion_issues:
        print(f'    - {p}')
    for p in csr_bridge_issues:
        print(f'    - {p}')
    for p in enable_branch_issues:
        print(f'    - {p}')
    for p in andterm_fanout_issues:
        print(f'    - {p}')
    for p in mode_i_anchor_issues:
        print(f'    - {p}')
    for p in mb_holdmux_issues:
        print(f'    - {p}')
    for p in holdmux_enable_issues:
        print(f'    - {p}')
    for r in results:
        if r['issues']:
            print(f'  FAIL [{r["target_register"]}] gate={r["gate_function"]} branch={r["branch_true_on"]}')
            for iss in r['issues']:
                print(f'    - {iss}')

    sys.exit(0 if overall_pass else 1)


if __name__ == '__main__':
    main()
