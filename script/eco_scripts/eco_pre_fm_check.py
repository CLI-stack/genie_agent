#!/usr/bin/env python3
"""
eco_pre_fm_check.py — Deterministic Step 5 Pre-FM Quality Checker

Replaces agent judgment in eco_pre_fm_checker.md with a script that
reads the applied JSON + study JSON and validates all required conditions.
No agent decisions — every check is deterministic: PASS or FAIL.

Usage:
    python3 eco_pre_fm_check.py \
        --tag <TAG> \
        --round <N> \
        --base-dir <BASE_DIR> \
        --ref-dir <REF_DIR> \
        --jira <JIRA>

Exit 0 = all checks PASS (safe to submit FM)
Exit 1 = any check FAIL (do NOT submit FM)

Writes:
    <BASE_DIR>/data/<TAG>_eco_pre_fm_check_round<N>.json
    <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<N>.rpt
    <BASE_DIR>/data/<TAG>_eco_step5_pre_fm_check_round<N>_marker.txt
"""

import argparse, json, os, re, subprocess, sys
from pathlib import Path


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--tag',      required=True)
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--base-dir', required=True, dest='base_dir')
    p.add_argument('--ref-dir',  required=True, dest='ref_dir')
    p.add_argument('--jira',     required=True)
    return p.parse_args()


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    try:
        return json.load(open(path))
    except Exception as e:
        return None


def zgrep_count(pattern, gz_path):
    try:
        r = subprocess.run(
            f'zcat {gz_path} | grep -c {re.escape(pattern)}',
            shell=True, capture_output=True, text=True, timeout=120
        )
        return int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
    except Exception:
        return 0


DEFERRED_REASONS = ('deferred', 'pending', 'round 2', 'application', 'defer')

def is_deferred(reason):
    r = (reason or '').lower()
    return any(k in r for k in DEFERRED_REASONS)


# ── Check implementations ─────────────────────────────────────────────────────

def check_no_deferred(applied):
    """
    FAIL if any port_declaration or port_connection entry is SKIPPED
    with a deferral reason. These cause FM ABORT.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            ct = e.get('change_type', '')
            st = e.get('status', '')
            reason = e.get('reason', '')
            if ct in ('port_declaration', 'port_promotion', 'port_connection') \
               and st == 'SKIPPED' and is_deferred(reason):
                failures.append(f'{stage}: {ct} {e.get("name","?")} — {reason[:80]}')
    return failures


def check_stage_consistency(applied):
    """
    FAIL if an ECO gate is INSERTED in some stages but SKIPPED in others.
    Each new_logic_gate/dff must appear in all 3 stages.

    G4 extension: also flag stage-divergent application of high-risk per-stage
    edit types (unconnected_rewires, port_connection, wire_swap, port_promotion,
    bus_rename). These edits modify shared logical instances; per-stage divergence
    produces silent stage-divergent netlists that fail FM on apparently-
    unrelated DFFs whose cone walks through the modified region.
    """
    gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    per_stage = {}
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        inserted = {e.get('name','') for e in entries
                    if e.get('change_type','') in gate_types
                    and e.get('status','') == 'INSERTED'}
        skipped  = {e.get('name','') for e in entries
                    if e.get('change_type','') in gate_types
                    and e.get('status','') == 'SKIPPED'}
        per_stage[stage] = {'inserted': inserted, 'skipped': skipped}

    stages = [s for s in per_stage if per_stage[s]['inserted'] or per_stage[s]['skipped']]
    failures = []
    if len(stages) >= 2:
        all_gates = set()
        for s in stages:
            all_gates |= per_stage[s]['inserted'] | per_stage[s]['skipped']
        for gate in sorted(all_gates):
            stage_results = {}
            for s in stages:
                if gate in per_stage[s]['inserted']:
                    stage_results[s] = 'INSERTED'
                elif gate in per_stage[s]['skipped']:
                    stage_results[s] = 'SKIPPED'
                else:
                    stage_results[s] = 'MISSING'
            if len(set(stage_results.values())) > 1:
                failures.append(f'{gate}: {stage_results}')

    # G4 — high-risk per-stage edit parity
    HIGH_RISK_TYPES = {'unconnected_rewires', 'port_connection',
                       'wire_swap', 'port_promotion', 'bus_rename'}
    SUCCESS_STATUS = {'APPLIED', 'INSERTED', 'QUEUED', 'AUTO_SANITIZED'}

    def _edit_key(e):
        ci = e.get('change_index')
        if ci is not None:
            return f'#{ci}'
        return (e.get('instance_name') or e.get('cell_name') or
                e.get('signal_name') or e.get('name') or '?')

    per_edit = {}
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        if stage not in ('Synthesize', 'PrePlace', 'Route'):
            continue
        for e in entries:
            ct = e.get('change_type', '')
            reason = e.get('reason', '')
            if not (ct in HIGH_RISK_TYPES or
                    'unconnected_rewires' in reason or
                    'bus_bit_replace' in reason or
                    'bus_rename' in reason):
                continue
            per_edit.setdefault(_edit_key(e), {})[stage] = e.get('status', '?')

    for key, by_stage in per_edit.items():
        succeeded = {s for s, st in by_stage.items() if st in SUCCESS_STATUS}
        if 0 < len(succeeded) < 3:
            missing = sorted({'Synthesize','PrePlace','Route'} - succeeded)
            failures.append(
                f'high-risk-edit {key!r} stage-divergent: succeeded in '
                f'{sorted(succeeded)}, missing/failed in {missing}, '
                f'by_stage={by_stage}')
    return failures


def check_port_declarations_applied(applied):
    """
    FAIL if any port_declaration/port_connection is SKIPPED (for any reason).
    These are all mandatory — no deferral, no skipping.
    Exception: 'wire' type entries (implicitly created) and ALREADY_APPLIED.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            ct = e.get('change_type', '')
            st = e.get('status', '')
            name = e.get('name', '?')
            reason = e.get('reason', '')
            if ct in ('port_declaration', 'port_promotion', 'port_connection') \
               and st == 'SKIPPED' \
               and 'wire' not in reason.lower() \
               and 'implicit' not in reason.lower():
                failures.append(f'{stage}: {ct} {name} SKIPPED — {reason[:80]}')
    return failures


def check_no_unhandled(applied):
    """
    FAIL if any entry has status UNHANDLED — indicates eco_perl_spec didn't
    recognize the change_type, so it was silently dropped.
    """
    failures = []
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if e.get('status','') == 'UNHANDLED':
                failures.append(
                    f'{stage}: {e.get("change_type","?")} {e.get("name","?")} UNHANDLED')
    return failures


def check_check8(check8_json_path):
    """
    Read pre-computed eco_verilog_validator result. FAIL if any stage is not PASS.
    """
    d = load_json(check8_json_path)
    if d is None:
        return ['eco_verilog_validator result not found — cannot validate Verilog syntax']
    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        result = d.get(stage, 'MISSING')
        if result != 'PASS':
            failures.append(f'eco_verilog_validator {stage}: {result}')
    return failures


def check_cells_in_netlist(applied, ref_dir, study_path=None):
    """
    FAIL if any gate marked INSERTED in applied JSON is physically absent
    from the PostEco netlist OR is present but inserted into the WRONG host
    module.

    Two failure modes covered:
      (a) GHOST_INSERT — applier reported INSERTED but Perl pipe couldn't
          inject anywhere (module not found, module_name="UNKNOWN" silently
          dropped, etc.). Caught by full-netlist grep returning zero.
      (b) WRONG_MODULE — cell is in the netlist but lives in a module
          different from study's `module_name` for that instance. This
          previously slipped past because the global grep returned >=1.
          Now we restrict the grep to the host module's body and fail if
          the cell isn't in there.

    When `study_path` is provided, we build {instance_name: host_module}
    from the study and enforce per-host-module presence. When `study_path`
    is None we fall back to the legacy global-grep behavior (backwards
    compatible).
    """
    gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    failures = []

    # Build {instance_name: host_module} from study, if available.
    inst_to_host = {}
    if study_path and os.path.exists(study_path):
        try:
            study = json.loads(Path(study_path).read_text())
            for stage in ('Synthesize', 'PrePlace', 'Route'):
                for e in study.get(stage, []):
                    if e.get('change_type') not in gate_types:
                        continue
                    inst = e.get('instance_name', '')
                    mod  = e.get('module_name', '')
                    if inst and mod:
                        # Last write wins — per-stage module_name may differ
                        # in Route (suffix _0) but we'll handle that below
                        # via try (mod, mod+'_0') variants.
                        inst_to_host[(stage, inst)] = mod
        except Exception:
            pass

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        entries = applied.get(stage, [])
        if not isinstance(entries, list):
            continue
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        inserted = [e.get('name','') for e in entries
                    if e.get('change_type','') in gate_types
                    and e.get('status','') == 'INSERTED'
                    and e.get('name','')]
        if not inserted:
            continue

        # Cache module body extracts per host module name (Route may use _0
        # suffix). Lazy-extract on first lookup.
        mod_body_cache = {}
        def _body_of(mod):
            if mod in mod_body_cache:
                return mod_body_cache[mod]
            for cand in (mod, mod + '_0', mod + '_1'):
                try:
                    r = subprocess.run(
                        f"zcat {gz} | awk '/^module {re.escape(cand)}\\b/,/^endmodule/'",
                        shell=True, capture_output=True, text=True, timeout=120
                    )
                    body = r.stdout or ''
                    if body:
                        mod_body_cache[mod] = body
                        return body
                except Exception:
                    pass
            mod_body_cache[mod] = ''
            return ''

        for inst in inserted:
            if not inst:
                continue
            host = inst_to_host.get((stage, inst), '')
            if host:
                # Per-host-module presence check (preferred path)
                body = _body_of(host)
                if not body:
                    failures.append(
                        f'[HOST_MODULE_NOT_FOUND] {stage}: instance {inst!r} '
                        f'has study module_name={host!r} but that module body '
                        f"could not be extracted from PostEco/{stage}.v.gz "
                        f'(tried {host}, {host}_0, {host}_1). Either '
                        f'module_name is wrong or this module was uniquified '
                        f'with an unexpected suffix.')
                    continue
                if re.search(rf'\b{re.escape(inst)}\s*\(', body):
                    continue  # ✓ present in host module
                # Not in host module — check if it landed in some OTHER module
                # (catches the "wrong module" silent-misroute case)
                try:
                    r2 = subprocess.run(
                        f"zcat {gz} | grep -cF ' {inst} ('",
                        shell=True, capture_output=True, text=True, timeout=120
                    )
                    elsewhere = (int(r2.stdout.strip())
                                 if r2.stdout.strip().isdigit() else 0)
                except Exception:
                    elsewhere = 0
                if elsewhere > 0:
                    failures.append(
                        f'[WRONG_MODULE] {stage}: {inst!r} marked INSERTED '
                        f'but is NOT in host module {host!r}; found '
                        f'{elsewhere} occurrence(s) elsewhere in '
                        f'PostEco/{stage}.v.gz. Likely module_name placeholder '
                        f'or perl_spec emitted to wrong module.')
                else:
                    failures.append(
                        f'[GHOST_INSERT] {stage}: {inst!r} marked INSERTED but '
                        f'absent from BOTH host module {host!r} AND the rest '
                        f'of PostEco/{stage}.v.gz. Perl pipe silently dropped '
                        f"the cell (module_name=UNKNOWN or non-existent "
                        f"module). Check 44 should have caught this at Step 3.")
            else:
                # Legacy global-grep fallback when study_path unavailable or
                # entry has no host mapping.
                try:
                    r = subprocess.run(
                        f'zcat {gz} | grep -cF " {inst} ("',
                        shell=True, capture_output=True, text=True, timeout=120
                    )
                    count = (int(r.stdout.strip())
                             if r.stdout.strip().isdigit() else 0)
                    if count == 0:
                        failures.append(
                            f'[GHOST_INSERT] {stage}: {inst} marked INSERTED '
                            f'in JSON but NOT found in PostEco/{stage}.v.gz — '
                            f'Perl spec generated but module not found '
                            f'in netlist')
                except Exception:
                    pass
    return failures


def check_port_edits_in_netlist(ref_dir, applied):
    """For every applied port_declaration / port_connection entry, verify the
    edit is physically in the netlist. Catches the silent-failure pattern where
    eco_netlist_port_rewire.py reported APPLIED but the regex sub did nothing because
    the target line wasn't in inst_close / port list spanned multiple lines.

    Reads the entry's `reason` text to extract signal/port/net since the applied
    JSON entries are minimal (only ct/status/name/reason).
    """
    failures = []
    # Reason patterns:
    #   'added NeedFreqAdj to port list and output decl in ddrss_umccmd_t_umcarbctrlsw'
    #   'added .NeedFreqAdj(ARB_FEI_NeedFreqAdj) to CTRLSW'
    #   'rewired existing .X to (Y) in INST'
    #   'bus_rename: REGCMD.REG_UmcCfgEco[1] OLD→NEW'  (skipped — Check 9 covers)
    pdec_re   = re.compile(r'added\s+(\S+)\s+to\s+port\s+list\s+and\s+(input|output|inout|wire)\s+decl\s+in\s+(\S+)')
    pconn_add_re = re.compile(r'added\s+\.(\w+)\s*\(\s*(\S+?)\s*\)\s+to\s+(\w+)')
    pconn_rew_re = re.compile(r'rewired\s+existing\s+\.(\w+)\s+to\s+\(\s*(\S+?)\s*\)\s+in\s+(\w+)')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            raw = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(raw)  # Option A: comments don't count
        for e in applied.get(stage, []):
            if e.get('status') != 'APPLIED':
                continue
            # Support both 'ct' (passes_2_4 JSON) and 'change_type' (study JSON)
            ct = e.get('ct') or e.get('change_type', '')
            reason = e.get('reason', '')
            if ct == 'port_declaration':
                m = pdec_re.search(reason)
                signal = m.group(1) if m else (e.get('signal_name') or e.get('name', ''))
                direction = m.group(2) if m else e.get('declaration_type', 'input')
                if direction == 'wire' or not signal:
                    continue
                if not re.search(rf'^\s*(input|output|inout)\s+{re.escape(signal)}\b', text, re.MULTILINE):
                    failures.append(f'[PORT_DECL_MISSING] {stage}: port_declaration APPLIED for {signal!r} ({direction}) but no input/output decl found in netlist')
            elif ct == 'port_connection':
                # Skip bus_bit_index entries (handled by Check 9 / bus_concat_intact)
                if e.get('bus_bit_index') is not None or 'bus_rename' in reason:
                    continue
                m = pconn_add_re.search(reason) or pconn_rew_re.search(reason)
                if m:
                    port, net, inst = m.group(1), m.group(2), m.group(3)
                else:
                    inst = e.get('instance_name') or e.get('submodule_instance')
                    port = e.get('port_name')   or e.get('new_token')
                    net  = e.get('net_name')    or e.get('flat_net_name')
                if not all([inst, port, net]):
                    continue
                pat = rf'\.\s*{re.escape(port)}\s*\(\s*{re.escape(net)}\s*\)'
                if not re.search(pat, text):
                    failures.append(f'[PORT_CONN_MISSING] {stage}: port_connection APPLIED .{port}({net}) on {inst} but not found in netlist')
    return failures


def check_semantic_verify(study_path, ref_dir):
    """Check 12 — Full semantic equivalence between Step 3 study JSON intent
    and PostEco netlist. Wraps eco_semantic_verify.NetlistView + per-entry-type
    verifiers. Catches what regex spot checks miss: comment-masked edits,
    bus-bit-position mismatches, wrong-instance matches, port-direction
    inconsistencies. Comprehensive — covers every confirmed study entry.
    """
    failures = []
    if not os.path.exists(study_path):
        return [f'[SEMANTIC] study JSON not found: {study_path}']
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import eco_semantic_verify as esv
    except Exception as e:
        return [f'[SEMANTIC] cannot import eco_semantic_verify: {e}']
    try:
        with open(study_path) as f:
            study = json.load(f)
    except Exception as e:
        return [f'[SEMANTIC] cannot read study JSON: {e}']
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            raw = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=300).stdout
        except Exception as e:
            failures.append(f'[SEMANTIC] {stage} netlist read err: {e}')
            continue
        view = esv.NetlistView(raw)
        for entry in study.get(stage, []):
            if not entry.get('confirmed', True):
                continue
            ct = entry.get('change_type', '')
            verifier = esv.VERIFIERS.get(ct)
            if verifier is None:
                continue
            err = verifier(entry, view, stage)
            if err:
                failures.append(f'[SEMANTIC_{ct.upper()}] {stage}: {err}')
    return failures


def strip_verilog_comments(text):
    """Remove // line comments and /* */ block comments. Critical for any
    semantic check on netlist content — Verilog comments don't count toward
    signal references, port concat positions, or driver declarations.
    """
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', '', text)
    return text


def parse_bus_concat_at_instance(text_no_comments, instance_name, port_name):
    """Find .port_name({...}) on instance_name; return parsed list of nets.
    Returns None if not found or not a {} concat. text_no_comments must already
    be comment-stripped — caller's responsibility.
    """
    inst_m = re.search(rf'\b{re.escape(instance_name)}\s*\(', text_no_comments)
    if not inst_m:
        return None
    block = text_no_comments[inst_m.start():inst_m.start() + 200000]
    port_m = re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*\{{([^{{}}]*)\}}', block, re.DOTALL)
    if not port_m:
        return None
    return [e.strip() for e in port_m.group(1).split(',')]


def check_rewires_in_netlist(ref_dir, applied):
    """For every applied rewire entry, verify the netlist's cell.pin actually
    points to new_net (not old_net). Catches silent-rewire-no-op pattern where
    apply_rewire's regex matched something but didn't change the right line.
    """
    failures = []
    # Reason format from apply_rewire: '{cell}.{pin}: {old} → {new}' (Unicode arrow)
    rew_re = re.compile(r'(\S+)\.(\w+):\s+(\S+)\s*(?:→|->|—>)\s*(\S+)')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            raw = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(raw)  # Option A: comments don't count
        for e in applied.get(stage, []):
            if e.get('status') != 'APPLIED':
                continue
            ct = e.get('ct') or e.get('change_type', '')
            if ct != 'rewire':
                continue
            m = rew_re.search(e.get('reason', ''))
            if not m:
                continue
            cell, pin, old_net, new_net = m.groups()
            # Find the cell instance in the netlist (best-effort by name)
            inst_m = re.search(rf'\b{re.escape(cell)}\s*\(', text)
            if not inst_m:
                failures.append(f'[REWIRE_CELL_MISSING] {stage}: rewire APPLIED on {cell}.{pin} but cell not found in netlist')
                continue
            cell_block = text[inst_m.start():inst_m.start() + 50000]
            if not re.search(rf'\.\s*{re.escape(pin)}\s*\(\s*{re.escape(new_net)}\s*\)', cell_block):
                failures.append(f'[REWIRE_MISSING] {stage}: rewire APPLIED {cell}.{pin}: {old_net}→{new_net} but .{pin}({new_net}) not in cell block')
    return failures


def check_bus_concat_intact(ref_dir, applied):
    """SEMANTIC bus-concat verification (Option B): comment-strip the netlist,
    parse the {...} content, and verify the renamed net is at the correct
    bus_bit_index position. Catches:
      - Bus collapsed to single net (Check 9 v1 pattern)
      - Rename text inside a comment (today's failure mode)
      - Rename applied to wrong bit position
      - Rename applied to wrong instance (multi-match)
    """
    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        # Both 'change_type' (study schema) and 'ct' (passes_2_4 schema) supported
        bus_entries = [e for e in applied.get(stage, [])
                       if (e.get('change_type') == 'port_connection' or e.get('ct') == 'port_connection')
                       and e.get('bus_bit_index') is not None]
        if not bus_entries:
            continue
        try:
            raw = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(raw)
        for e in bus_entries:
            inst = e.get('instance_name') or e.get('submodule_instance', '')
            port = e.get('port_name', '')
            new_net = e.get('net_name') or e.get('net_name_after', '')
            bbi  = e.get('bus_bit_index')
            if not all([inst, port, new_net]) or bbi is None:
                continue
            elements = parse_bus_concat_at_instance(text, inst, port)
            if elements is None:
                failures.append(f'[BUS_CONCAT_MISSING] {stage}: {inst}.{port} no {{}} concat found in active code (possibly collapsed or in comment)')
                continue
            width = len(elements)
            pos = width - 1 - bbi  # MSB-first
            if pos < 0 or pos >= width:
                failures.append(f'[BUS_BIT_RANGE] {stage}: {inst}.{port} bus_bit_index={bbi} out of range (width={width})')
                continue
            actual = elements[pos]
            if actual != new_net:
                failures.append(f'[BUS_BIT_WRONG_NET] {stage}: {inst}.{port}[{bbi}] = {actual!r} but expected {new_net!r} — rename did not take effect at bit position')
    return failures


def check_undriven_eco_nets(ref_dir):
    """
    FAIL if any n_eco_* net in PostEco netlist has < 2 occurrences in ACTIVE
    code (comments stripped). A driven net has at least one driver reference
    (cell output / wire decl / port concat slot) AND at least one consumer
    reference. Fewer than 2 in active code → likely no driver.
    """
    failures = []
    NET_RE = re.compile(r'\b(n_eco_[A-Za-z0-9_]+)\b')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)  # Option A: comments don't count
        from collections import Counter
        counts = Counter(NET_RE.findall(text))
        for net, c in sorted(counts.items()):
            if c < 2:
                failures.append(f'[UNDRIVEN_NET] {stage}: {net} appears only {c} time(s) in active code — no driver (bus-rename or driver insertion likely failed)')
    return failures


def check_eco_input_drivers(study_path, ref_dir):
    """Check 13 — for every confirmed new_logic_gate / new_logic_dff entry in the
    study JSON, verify each input pin's per-stage net actually has a driver
    IN THE SAME HOST MODULE in the PostEco netlist.

    SCOPE-AWARE: previous version used a global driven set, which let
    `IReset` pass even when umcarbctrlsw's local wire was renamed by P&R
    to `test_so4927` and the chain still referenced bare `IReset` (just
    because some OTHER module wired up `.<port>(IReset)`). Now we build
    the driven set per host module so this class of bug fails fast.

    Constants like 1'b0/1'b1 are skipped."""
    failures = []
    OUT_PINS = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO'}
    try:
        study = json.loads(Path(study_path).read_text())
    except Exception as e:
        return [f'[INPUT_DRIVER_READ_ERR] {e}']

    def _drivers_in_module(mod_text):
        """Build the set of nets that have a driver inside mod_text."""
        driven = set()
        # Strip comments first so a commented-out previous-ECO `.Q(net)`
        # doesn't fake-drive the net.
        body = strip_verilog_comments(mod_text)
        for m in re.finditer(r'\.\s*(?:Z|ZN|ZN1|Q|QN|CO|Q1|Q2|Q3|Q4|Q5|Q6|Q7|Q8)\s*\(\s*(\w+)', body):
            driven.add(m.group(1))
        for m in re.finditer(r'^\s*wire\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]', body, re.MULTILINE):
            driven.add(m.group(1))
        for m in re.finditer(r'^\s*(?:input|inout)\s+(?:\[[^\]]+\]\s+)?(\w+)\s*[;,]', body, re.MULTILINE):
            driven.add(m.group(1))
        # Submodule INSTANCE output ports — bare net or bus concat
        for m in re.finditer(r'\.\s*\w+\s*\(\s*([A-Za-z_][\w]*)\s*\)', body):
            driven.add(m.group(1))
        for m in re.finditer(r'\.\s*\w+\s*\(\s*\{([^{}]+)\}\s*\)', body):
            for w in re.findall(r'[A-Za-z_]\w*', m.group(1)):
                driven.add(w)
        return driven

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        # Cache per-module driven sets — built lazily on first lookup.
        per_mod_cache = {}
        def _drivers_for(mod):
            if mod in per_mod_cache:
                return per_mod_cache[mod]
            for cand in (mod, mod + '_0'):
                m = re.search(rf'^module\s+{re.escape(cand)}\b.*?^endmodule\b',
                              text, re.MULTILINE | re.DOTALL)
                if m:
                    per_mod_cache[mod] = _drivers_in_module(m.group(0))
                    return per_mod_cache[mod]
            per_mod_cache[mod] = set()
            return per_mod_cache[mod]

        for entry in study.get(stage, []):
            if entry.get('change_type') not in ('new_logic_gate', 'new_logic_dff', 'new_logic'):
                continue
            if not entry.get('confirmed', True):
                continue
            inst = entry.get('instance_name', '?')
            host = entry.get('module_name', '')
            if not host:
                # Fall back to global scan if host module unknown — old behavior
                continue
            local_driven = _drivers_for(host)
            pcs = (entry.get('port_connections_per_stage') or {}).get(stage) or entry.get('port_connections') or {}
            for pin, val in pcs.items():
                if pin in OUT_PINS or not isinstance(val, str):
                    continue
                base = re.sub(r'\[[^\]]*\]', '', val).strip()
                if not base or base.startswith(("1'b", "0'b", "1'h", "0'h")):
                    continue
                if base not in local_driven:
                    failures.append(
                        f'[INPUT_UNDRIVEN] {stage}: {inst}.{pin}={val!r} in host '
                        f'module {host!r} — net has no driver in this module body. '
                        f'Likely per-stage rename was missed (e.g. P&R renamed the '
                        f'driver from {val} to a stage-specific name).')
    return failures


def check_input_net_strict_driver(study_path, ref_dir):
    """Strict driver check for new-ECO-cell input nets — closes the gap in
    check_eco_input_drivers, which collects ALL submodule port connections
    (input AND output, since direction is unknown for user-defined modules)
    and so treats consumer-only `.A1(W)` references on the new ECO cell itself
    as a "driver" for W.

    For every input pin of every new_logic_gate/new_logic_dff entry, this
    check requires the net to have at least one REAL driver in the host module
    body:
      1. Standard-cell output pin: `.(Z|ZN|ZN1|Q|QN|CO|CO1|S|SN|Q[1-8])(W)`
      2. Continuous assign: `assign W = ...`
      3. Module input/inout port: `input W;` / `inout W;` declared
      4. Sub-instance OUTPUT port: `.<port>(W)` on an instance whose module's
         declared port direction is `output`. Module port directions are
         indexed from all module headers in the netlist.

    Bare `wire W;` declarations + consumer-only references do NOT count.

    Closes the specific failure mode of run 20260525085948 R1 and
    20260526203822 R1, where `REG_UmcCfgEco_12_` was declared but only
    consumed by a new ECO gate input — undriven cone → FM Mode A.
    """
    failures = []
    OUT_PINS = ('Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'CO1', 'S', 'SN',
                'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8')
    out_pin_alt = '|'.join(OUT_PINS)
    try:
        study = json.loads(Path(study_path).read_text())
    except Exception as e:
        return [f'[STRICT_DRIVER_READ_ERR] {e}']

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True,
                                  timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)

        # Build {module_name: {port_name: direction}} from module headers.
        port_dir_map = {}
        for mod_m in re.finditer(
                r'^module\s+(\w+)\s*\(.*?^endmodule\b',
                text, re.MULTILINE | re.DOTALL):
            mod_name = mod_m.group(1)
            body = mod_m.group(0)
            ports = {}
            for d in re.finditer(
                    r'^\s*(input|output|inout)\s+(?:\[[^\]]+\]\s+)?([\w,\s]+?)\s*;',
                    body, re.MULTILINE):
                direction = d.group(1)
                for p in d.group(2).split(','):
                    p = p.strip()
                    if p:
                        ports[p] = direction
            port_dir_map[mod_name] = ports

        # Cache module body by host name.
        body_cache = {}
        def _body_of(mod):
            if mod in body_cache:
                return body_cache[mod]
            for cand in (mod, mod + '_0'):
                m = re.search(rf'^module\s+{re.escape(cand)}\b.*?^endmodule\b',
                              text, re.MULTILINE | re.DOTALL)
                if m:
                    body_cache[mod] = m.group(0)
                    return body_cache[mod]
            body_cache[mod] = ''
            return ''

        def _net_has_driver(net, mod_body):
            net_esc = re.escape(net)
            # 1. Cell output pin
            if re.search(rf'\.\s*(?:{out_pin_alt})\s*\(\s*{net_esc}\s*\)',
                         mod_body):
                return True
            # 2. assign
            if re.search(rf'^\s*assign\s+{net_esc}\s*=',
                         mod_body, re.MULTILINE):
                return True
            # 3. Module input / inout port
            if re.search(
                    rf'^\s*(?:input|inout)\s+(?:\[[^\]]+\]\s+)?{net_esc}\s*[;,]',
                    mod_body, re.MULTILINE):
                return True
            # 4. Sub-instance output port — resolve direction via port_dir_map.
            # Match `<module_type> <inst_name> ( ... .<port>(net) ... );`
            for inst_m in re.finditer(
                    r'^\s*(\w+)\s+(\w+)\s*\(([^;]+?)\)\s*;',
                    mod_body, re.MULTILINE | re.DOTALL):
                inst_module = inst_m.group(1)
                if inst_module in ('wire', 'reg', 'assign', 'input', 'output',
                                   'inout', 'parameter', 'localparam',
                                   'tri', 'tri0', 'tri1', 'wand', 'wor',
                                   'supply0', 'supply1'):
                    continue
                inst_dir_map = port_dir_map.get(inst_module)
                if not inst_dir_map:
                    continue
                inst_body = inst_m.group(3)
                for pm in re.finditer(
                        rf'\.\s*(\w+)\s*\(\s*{net_esc}\s*\)', inst_body):
                    if inst_dir_map.get(pm.group(1)) == 'output':
                        return True
                # 4b. Bus-bit rename: net appears inside a concat {..., net, ...}
                # on a sub-instance OUTPUT port (e.g. REGCMD.REG_UmcCfgEco bus).
                # This covers UNCONNECTED renames where ECO bit is wired into
                # a bus output port connection — identical to ECO 9868 pattern
                # (eco9868_UmcCfgEco_1) which FM verified successfully.
                for pm in re.finditer(
                        rf'\.\s*(\w+)\s*\(\s*\{{[^}}]*\b{net_esc}\b[^}}]*\}}\s*\)',
                        inst_body, re.DOTALL):
                    if inst_dir_map.get(pm.group(1)) == 'output':
                        return True
            return False

        for entry in study.get(stage, []):
            if entry.get('change_type') not in ('new_logic_gate',
                                                'new_logic_dff'):
                continue
            if not entry.get('confirmed', True):
                continue
            inst = entry.get('instance_name', '?')
            host = entry.get('module_name', '')
            if not host:
                continue
            body = _body_of(host)
            if not body:
                continue
            pcs = (entry.get('port_connections_per_stage') or {}).get(stage) \
                  or entry.get('port_connections') or {}
            for pin, val in pcs.items():
                if pin in OUT_PINS or not isinstance(val, str):
                    continue
                base = re.sub(r'\[[^\]]*\]', '', val).strip()
                if not base or base.startswith(("1'b", "0'b", "1'h", "0'h")):
                    continue
                if not _net_has_driver(base, body):
                    failures.append(
                        f'[INPUT_NET_STRICT_UNDRIVEN] {stage}: {inst}.{pin}={val!r} '
                        f'in host module {host!r} — net is declared but has NO '
                        f'driver in this module body (no cell output pin '
                        f'connection, no `assign`, no module input port, no '
                        f'sub-instance output port). A bare `wire {base};` plus '
                        f"consumer-only `.<pin>({base})` references on new ECO "
                        f'cells does NOT count as a driver. Likely missing '
                        f'a parent-side port_connection that wires a '
                        f'sub-instance OUTPUT port to {base!r} (e.g. REGCMD-like '
                        f"bus rename didn't propagate to the consuming module). "
                        f'Will fail FM with Mode A undriven cone.')
    return failures


def check_duplicate_ports(ref_dir):
    """Check D — no duplicate port names in any module port list header.
    Duplicate ports cause Verilog compile errors that block FM elaboration.
    Returns list of failure strings."""
    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)
        for mod_m in re.finditer(r'^module\s+(\w+)\s*\(([^)]+)\)', text, re.MULTILINE):
            mod_name = mod_m.group(1)
            port_list = mod_m.group(2)
            ports = re.findall(r'\b([A-Za-z_]\w*)\b', port_list)
            kw = {'input', 'output', 'inout', 'wire', 'reg', 'logic', 'integer'}
            seen, dups = {}, []
            for p in ports:
                if p in kw:
                    continue
                seen[p] = seen.get(p, 0) + 1
                if seen[p] == 2:
                    dups.append(p)
            if dups:
                failures.append(f'[DUPLICATE_PORT] {stage}: module {mod_name!r} has duplicate port(s): {dups}')
    return failures


def check_eco_output_pin_names(applied, ref_dir):
    """Check H — ECO cell output pin names must match the cell's actual output pin.
    Wrong output pin causes FE-LINK-7 ABORT_LINK (FM cannot build verification model).
    The most common mistake: MUX2 output is Z not ZN; IND2 is ZN not Z.
    Returns list of failure strings."""
    GATE_OUTPUT_PIN = {
        'AND2': 'Z', 'AND3': 'Z', 'AND4': 'Z',
        'OR2':  'Z', 'OR3':  'Z', 'OR4':  'Z',
        'XOR2': 'Z', 'XOR3': 'Z',
        'MUX2': 'Z', 'MUX4': 'Z',
        'INV':  'ZN',
        'NAND2': 'ZN', 'NAND3': 'ZN', 'NAND4': 'ZN',
        'NOR2':  'ZN', 'NOR3':  'ZN', 'NOR4':  'ZN',
        'XNOR2': 'ZN', 'IND2': 'ZN', 'IND3': 'ZN',
        'DFF': 'Q', 'SDFF': 'Q', 'SDFQ': 'Q',
        'AOI21': 'ZN', 'AOI22': 'ZN', 'OAI21': 'ZN', 'OAI22': 'ZN',
        'AO21': 'Z',  'AO22': 'Z',  'OA21': 'Z',  'OA22': 'Z',
        'INR3': 'ZN', 'IND3': 'ZN', 'IAOI21': 'ZN',
    }
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import eco_cell_truth_tables as _ett
        _have_ett = True
    except ImportError:
        _have_ett = False

    failures = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for entry in applied.get(stage, []):
            if entry.get('change_type') not in ('new_logic_gate', 'new_logic', 'new_logic_dff'):
                continue
            if entry.get('status') not in ('INSERTED', 'ALREADY_APPLIED'):
                continue
            cell = entry.get('cell_type', '')
            fn   = entry.get('gate_function', '')
            inst = entry.get('instance_name', '?')
            out_net = entry.get('output_net', '')
            pcs  = entry.get('port_connections', {})
            if not cell or not pcs:
                continue
            # Determine expected output pin: prefer Liberty lookup, then gate_function table, then GATE_OUTPUT_PIN
            expected_pin = None
            if _have_ett:
                tt = _ett.truth_table_of(cell)
                if tt:
                    expected_pin = next(iter(tt.keys()))  # first (usually only) output pin
            if not expected_pin and fn:
                family = fn.replace('2','').replace('3','').replace('4','').rstrip('0123456789')
                expected_pin = GATE_OUTPUT_PIN.get(fn) or GATE_OUTPUT_PIN.get(family)
            if not expected_pin:
                continue  # cannot determine — skip
            # Find which pin in port_connections has the output_net
            actual_out_pins = [p for p, n in pcs.items() if n == out_net]
            if not actual_out_pins:
                continue  # output_net not in port_connections — skip
            for actual in actual_out_pins:
                if actual != expected_pin:
                    failures.append(
                        f'[WRONG_OUTPUT_PIN] {stage}: {inst} output_pin={actual!r} '
                        f'but cell {cell!r} (fn={fn!r}) expects {expected_pin!r}. '
                        f'Rename .{actual}({out_net}) → .{expected_pin}({out_net}) in PostEco.')
    return failures


def check_missing_output_port_decls(applied, ref_dir):
    """Check B2 — if an ECO cell's output net is referenced OUTSIDE its declaring module
    (i.e., used as an argument to a parent-level port connection), a port_declaration
    entry must have been applied for that net. Missing causes FE-LINK-7 ABORT_LINK.
    Returns list of failure strings."""
    failures = []
    OUT_TYPES = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        entries = applied.get(stage, [])
        if not isinstance(entries, list):
            continue
        # Build set of ECO output nets with their declaring module
        eco_outputs = {}  # output_net → (inst, module_name)
        for e in entries:
            if e.get('change_type') not in OUT_TYPES or e.get('status') not in ('INSERTED', 'ALREADY_APPLIED'):
                continue
            out_net = e.get('output_net', '')
            mod     = e.get('module_name', '')
            inst    = e.get('instance_name', '?')
            if out_net and mod:
                eco_outputs[out_net] = (inst, mod)
        if not eco_outputs:
            continue
        # Build set of port_declaration entries that were APPLIED for this stage
        declared = set()
        for e in entries:
            if e.get('change_type') == 'port_declaration' and e.get('status') in ('APPLIED', 'ALREADY_APPLIED'):
                declared.add(e.get('signal_name', ''))
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)
        for out_net, (inst, mod) in eco_outputs.items():
            # Count occurrences in full netlist vs inside declaring module
            total = len(re.findall(rf'\b{re.escape(out_net)}\b', text))
            mod_m = re.search(rf'^module\s+{re.escape(mod)}(?:_0)?\b.*?^endmodule\b', text, re.DOTALL | re.MULTILINE)
            local = len(re.findall(rf'\b{re.escape(out_net)}\b', mod_m.group(0))) if mod_m else 0
            if total > local and out_net not in declared:
                failures.append(
                    f'[MISSING_OUTPUT_PORT] {stage}: ECO net {out_net!r} (from {inst} in {mod}) '
                    f'referenced {total - local} time(s) outside its module but no port_declaration applied.')
    return failures


def check_port_conn_target_exists(study_path, ref_dir):
    """Check B3 — for every `port_connection` entry in the study, verify that
    the target port_name actually appears in the target module's port list in
    the PostEco netlist. Catches the FE-LINK-7 ABORT class (observed on 9868
    fresh run R1: port_connection .NeedFreqAdj on CTRLSW had no matching port
    on umcarbctrlsw → FM aborted before any verify).

    Mirrors Step 3 Check 3e but checks the LIVE netlist instead of the study,
    giving defense-in-depth: even if the studier emits the right port_decl
    entry, a Pass 2 failure would still be caught here before FM runs.
    """
    failures = []
    try:
        study = json.loads(Path(study_path).read_text())
    except Exception as e:
        return [f'[PORT_CONN_TARGET_READ_ERR] {e}']
    pc_entries = []
    for e in study.get('Synthesize', []):
        if e.get('change_type') == 'port_connection':
            pc_entries.append(e)
    if not pc_entries:
        return failures
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True,
                                  text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)
        # Cache: child_module → set of declared port names
        port_cache = {}
        def _ports_of(mod):
            if mod in port_cache:
                return port_cache[mod]
            ports = set()
            for cand in (mod, mod + '_0'):
                m = re.search(
                    rf'^module\s+{re.escape(cand)}\b.*?^endmodule\b',
                    text, re.MULTILINE | re.DOTALL)
                if not m:
                    continue
                body = m.group(0)
                for pm in re.finditer(
                        r'^\s*(?:input|output|inout)\s+(?:\[[^\]]+\]\s*)?'
                        r'([A-Za-z_]\w*)\s*[;,]', body, re.MULTILINE):
                    ports.add(pm.group(1))
                # Also pick up ports from the header port list (some files only
                # name them in the header and declare direction inline)
                hdr = re.search(rf'^module\s+{re.escape(cand)}\s*\(([^)]*)\)',
                                body, re.MULTILINE | re.DOTALL)
                if hdr:
                    for tok in re.findall(r'[A-Za-z_]\w*', hdr.group(1)):
                        ports.add(tok)
                break
            port_cache[mod] = ports
            return ports
        for e in pc_entries:
            child_mod = e.get('child_module_name') or ''
            if not child_mod:
                continue  # Step 3 Check 3e already flags this
            port = e.get('port_name', '')
            if not port:
                continue
            ports = _ports_of(child_mod)
            if not ports:
                continue  # module not found in netlist — separate concern
            if port not in ports:
                failures.append(
                    f'[PORT_CONN_TARGET_MISSING] {stage}: port_connection '
                    f'{e.get("instance_name","?")}.{port} → {child_mod}: '
                    f'port {port!r} NOT in module port list. FE-LINK-7 ABORT risk.')
    return failures


def check_mode_s_stitching(study_path, ref_dir):
    """Check 14 — for every new_logic_dff with mode_S_applied=true (or
    requires_scan_stitching=true) in the study JSON, verify the host module
    has the 3 stitching ports (SI_in, SE_in, Q_out) declared in the netlist
    AND the assign statement is present AND the DFF's per-stage SE/SI use
    those ports (NOT 1'b0) in PrePlace and Route stages.
    """
    failures = []
    try:
        study = json.loads(Path(study_path).read_text())
    except Exception as e:
        return [f'[MODE_S_READ_ERR] {e}']
    for stage in ('PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)
        for entry in study.get(stage, []):
            if entry.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            if not entry.get('confirmed', True):
                continue
            if not (entry.get('mode_S_applied') or entry.get('requires_scan_stitching')):
                continue
            # Per-stage strategy: only check bridge wiring when this stage uses
            # `bridge_port`. `neighbor_dff` strategy in this stage means the DFF's
            # SE/SI point at a neighbor-DFF net (no bridge needed in this stage).
            strat_per_stage = entry.get('mode_S_strategy_per_stage') or {}
            this_stage_strat = strat_per_stage.get(stage)
            if this_stage_strat == 'neighbor_dff':
                continue
            inst = entry.get('instance_name', '?')
            host = entry.get('module_name', '')
            if not host:
                continue
            # Find host module (with possible _0 suffix in Route)
            mod_m = re.search(rf'^module\s+{re.escape(host)}(?:_0)?\b.*?^endmodule\b',
                              text, re.MULTILINE | re.DOTALL)
            if not mod_m:
                failures.append(f'[MODE_S_MODULE_MISSING] {stage}: host module {host!r} not found for {inst}')
                continue
            body = mod_m.group(0)
            # Look for the 3 stitching port declarations (any ECO_*_SI_in / SE_in / Q_out
            # OR <target_reg>_reg_SI_in naming convention)
            si = re.search(r'^\s*input\s+(ECO_\w*_SI_in|eco\w*_si_bridge_in|\w+_reg_SI_in)\s*;', body, re.MULTILINE)
            se = re.search(r'^\s*input\s+(ECO_\w*_SE_in|eco\w*_se_bridge_in|\w+_reg_SE_in)\s*;', body, re.MULTILINE)
            qo = re.search(r'^\s*output\s+(ECO_\w*_Q_out|eco\w*_q_bridge_out|\w+_reg_Q_out)\s*;', body, re.MULTILINE)
            missing_ports = [p for p, m in (('SI_in', si), ('SE_in', se), ('Q_out', qo)) if not m]
            if missing_ports:
                failures.append(f'[MODE_S_PORT_MISSING] {stage}: {inst} requires Mode S but host {host!r} missing port(s) {missing_ports}')
                continue
            # NEW: bridge wire driver check at parent scope. The bridge port is
            # an input to the host module — at the parent module, the wire that
            # feeds the bridge (e.g. eco<jira>_si_bridge) MUST be driven by an
            # `assign` or by another module's output port_connection. A dangling
            # bridge wire produces undriven SE/SI at the new DFF → FM globally
            # unmatched (the failure mode that broke 9868 R1).
            #
            # Find the parent of host: look for `<host_mod>(_0)? <inst_name> (` in netlist
            # and check that inst_name's port_connections for ECO_*_SI_in / SE_in
            # reference wires that are driven somewhere in the parent module.
            si_port = si.group(1)
            se_port = se.group(1)
            for bridge_port in (si_port, se_port):
                # Find any instance that wires up this port — look for ".<bridge_port>(<wire>)"
                pc = re.search(rf'\.\s*{re.escape(bridge_port)}\s*\(\s*([A-Za-z_]\w*)\s*\)', text)
                if not pc:
                    failures.append(
                        f'[MODE_S_BRIDGE_NOT_WIRED] {stage}: host {host!r} declares port '
                        f'{bridge_port!r} but no parent instance wires it up — port is '
                        f'unused → DFF {inst} SE/SI undriven at upper scope.')
                    continue
                wire = pc.group(1)
                # Verify the wire has a driver: scan for `assign <wire> = ...` or
                # `.<some_out_port>(<wire>)` (output port_connection).
                drv_assign = re.search(rf'^\s*assign\s+{re.escape(wire)}\s*=\s*\S+', text, re.MULTILINE)
                drv_out_conn = re.search(rf'\.\s*\w*(?:_out|_OUT)\s*\(\s*{re.escape(wire)}\s*\)', text)
                if not (drv_assign or drv_out_conn):
                    failures.append(
                        f'[MODE_S_BRIDGE_DANGLING] {stage}: bridge wire {wire!r} feeding '
                        f'{host}.{bridge_port} for {inst} has NO driver in the netlist '
                        f'(no `assign {wire} = ...` and no `_out` port connection). FM '
                        f'will see DFF.SE/SI undriven → globally unmatched compare points.')
            # Verify the assign exists
            assn = re.search(rf'^\s*assign\s+{re.escape(qo.group(1))}\s*=\s*\w+\s*;', body, re.MULTILINE)
            if not assn:
                failures.append(f'[MODE_S_ASSIGN_MISSING] {stage}: {inst} requires Mode S but assign for {qo.group(1)} not found in {host!r}')
                continue
            # Verify DFF's SE/SI in netlist match the declared Mode S port names
            # exactly — not merely "≠ 1'b0". A neighbor-DFF net that bypasses
            # the bridge ports (e.g. test_so629, FxPrePlace_HFSNET_*) would pass
            # the old non-1'b0 check but break FM because the bridge wires don't
            # connect to the existing scan chain.
            dff_re = re.search(rf'\b\S+\s+{re.escape(inst)}\s*\(\s*([^;]+?)\)\s*;', body, re.DOTALL)
            if dff_re:
                pcs = dff_re.group(1)
                se_m = re.search(r'\.SE\s*\(\s*([^)]+?)\s*\)', pcs)
                si_m = re.search(r'\.SI\s*\(\s*([^)]+?)\s*\)', pcs)
                se_actual = se_m.group(1).strip() if se_m else ''
                si_actual = si_m.group(1).strip() if si_m else ''
                se_expected = se.group(1)  # ECO_*_SE_in name from port decl
                si_expected = si.group(1)
                if se_actual != se_expected:
                    failures.append(
                        f"[MODE_S_SE_MISMATCH] {stage}: {inst}.SE = {se_actual!r} "
                        f"but Mode S declared {se_expected!r} — DFF SE/SI must use "
                        f"the bridge ports, not a neighbor-DFF net")
                if si_actual != si_expected:
                    failures.append(
                        f"[MODE_S_SI_MISMATCH] {stage}: {inst}.SI = {si_actual!r} "
                        f"but Mode S declared {si_expected!r} — DFF SE/SI must use "
                        f"the bridge ports, not a neighbor-DFF net")
    return failures


def check_duplicate_wire_decls(ref_dir):
    """Check 19 — duplicate wire/tri/wand/wor/reg declarations in same module.

    FM aborts elaboration with `Duplicate wire/tri/wand/wor declaration for 'X'`
    + `read_verilog/read_sverilog command has been ignored due to errors (FM-599)`
    when a module has TWO identical wire-class declarations. Pre-FM check should
    catch this instead of wasting a full FM cycle (run 20260511083831 ABORT root
    cause: applier inserted `wire UNCONNECTED_19090 ;` twice in
    ddrss_umccmd_t_umcregcmd while processing a Mode-I rename).

    Streams through each PostEco netlist; per-module body, builds a Counter of
    declared net names from `wire/tri/wand/wor/reg <name>;` lines; reports any
    name with count >1.
    """
    failures = []
    # Match `wire NAME ;` or `wire [N:M] NAME ;` (single-name decls only — multi-
    # name decls like `wire A, B, C;` get split below)
    DECL_RE = re.compile(r'^\s*(wire|tri|wand|wor|reg)\s+(?:\[[^\]]+\]\s+)?([^;]+);', re.MULTILINE)
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        text = strip_verilog_comments(text)
        # Walk module-by-module so we count decls per-module-body (a wire X in
        # module A and wire X in module B is fine — different scopes).
        i = 0
        cur_mod = None
        body_lines = []
        from collections import Counter
        for line_idx, line in enumerate(text.split('\n'), start=1):
            m = re.match(r'^module\s+(\S+)', line)
            if m:
                cur_mod = m.group(1)
                body_lines = []
                continue
            if re.match(r'^\s*endmodule', line):
                if cur_mod:
                    # Check decls for this module
                    body = '\n'.join(body_lines)
                    counts = Counter()
                    decl_names = set()  # set of explicitly-declared wire names
                    for dm in DECL_RE.finditer(body):
                        # Split multi-name decls (e.g. `wire A, B, C;`)
                        names = [n.strip() for n in dm.group(2).split(',')]
                        for n in names:
                            # Strip vector range if present (rare in flat name list)
                            n = re.sub(r'\[[^\]]+\]', '', n).strip()
                            if n:
                                counts[n] += 1
                                decl_names.add(n)
                    for name, c in counts.items():
                        if c > 1:
                            failures.append(
                                f'[DUP_WIRE_DECL] {stage}: module {cur_mod!r} declares '
                                f'wire/reg {name!r} {c} times — FM elaboration WILL ABORT '
                                f'with "Duplicate wire declaration for {name!r}" + FM-599. '
                                f'Likely cause: applier inserted a wire decl for a net that '
                                f'pre-existing in the netlist (Mode-I rename or new wire '
                                f'insertion that didn\'t check for prior decl).')
                    # IMPLICIT-WIRE conflict: an explicit `wire X;` decl AND the same
                    # name X used as `.PORT(X)` port-connection net AT AN EARLIER LINE
                    # in the same module — FM treats the port connection as the first
                    # implicit declaration; the explicit decl that comes later is a
                    # duplicate. ORDER MATTERS: a `wire X;` followed by `.PORT(X)` is
                    # FINE (explicit comes first, port consumes it). It's only
                    # `.PORT(X)` followed by `wire X;` that triggers FM-599.
                    # Run 20260511201004 root cause: applier inserted ECO gate +
                    # `wire n_eco_9868_mux_sel ;` near endmodule (line 4423901),
                    # but ctmi_523004 had `.S(n_eco_9868_mux_sel)` at line 4235025
                    # (much earlier — implicit wire was already created).
                    PORT_CONN_RE = re.compile(r'\.\s*\w+\s*\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*\)')
                    DECL_LINE_RE = re.compile(r'^\s*(wire|tri|wand|wor|reg)\s+(?:\[[^\]]+\]\s+)?([^;]+);')
                    # Build per-name FIRST line: port-connection use, OR wire decl
                    first_use_line = {}      # name -> line index of first port-connection use
                    first_decl_line = {}     # name -> line index of explicit wire decl
                    body_split = body.split('\n')
                    for ln_idx, ln in enumerate(body_split):
                        for pm in PORT_CONN_RE.finditer(ln):
                            n = pm.group(1).strip()
                            if n not in first_use_line:
                                first_use_line[n] = ln_idx
                        dm = DECL_LINE_RE.match(ln)
                        if dm:
                            for n in [x.strip() for x in dm.group(2).split(',')]:
                                n = re.sub(r'\[[^\]]+\]', '', n).strip()
                                if n and n not in first_decl_line:
                                    first_decl_line[n] = ln_idx
                    # Order-aware flag: port-use BEFORE explicit decl
                    for name, decl_ln in first_decl_line.items():
                        use_ln = first_use_line.get(name, -1)
                        if use_ln >= 0 and use_ln < decl_ln:
                            failures.append(
                                f'[IMPLICIT_WIRE_CONFLICT] {stage}: module {cur_mod!r} — '
                                f'`.PORT({name})` port-connection at body line {use_ln+1} '
                                f'comes BEFORE explicit `wire {name} ;` at body line '
                                f'{decl_ln+1}. Verilog auto-created the wire from the '
                                f'port connection; explicit decl is a duplicate → FM-599 '
                                f'"Duplicate wire declaration" ABORT. Applier should skip '
                                f'wire decl when net is referenced earlier as a port '
                                f'connection. Either delete the `wire {name} ;` decl OR '
                                f'fix eco_perl_spec.py rewire_new_nets dedup.')
                cur_mod = None
                body_lines = []
                continue
            if cur_mod is not None:
                body_lines.append(line)
    return failures


def check_cross_module_bridge_connectivity(study_path, ref_dir):
    """Check 20 — cross-module bridge port connectivity audit.

    For every port_declaration with `bridge_port_role` (host_si/se/q or
    sibling_si/se/q), verify in the PostEco netlist:
      (a) a parent module instantiates the owning module
      (b) the parent has a wire declaration for the bridge wire (eco<jira>_*)
      (c) the parent's instance line has `.<PORT>(<wire>)` hookup
      (d) for OUTPUT bridge ports: a driver exists inside the owning module
          (buffer cell or assign producing the port net)

    Catches the FM ABORT class where bridge ports exist on a module but the
    parent never wires them up, leaving FM to find unresolved nets at
    instantiation scope.
    """
    failures = []
    study = load_json(study_path) or {}
    if not isinstance(study, dict):
        return failures
    # Cache stage→module body text
    body_cache = {}
    def _module_body(stage, mod):
        key = (stage, mod)
        if key in body_cache:
            return body_cache[key]
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            body_cache[key] = ''
            return ''
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            body_cache[key] = ''
            return ''
        text = strip_verilog_comments(text)
        m = re.search(rf'^module\s+{re.escape(mod)}\b.*?^endmodule', text, re.DOTALL | re.MULTILINE)
        body_cache[key] = m.group(0) if m else ''
        return body_cache[key]

    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') != 'port_declaration':
                continue
            role = e.get('bridge_port_role')
            if not role:
                continue
            owning_mod = e.get('module_name')
            port_name  = e.get('port_name')
            port_dir   = e.get('port_direction', '')
            if not (owning_mod and port_name):
                continue
            # (a) parent module — anything that instantiates owning_mod
            # Search every module body for `<owning_mod> <inst> (`
            # We only need to find ONE parent for the connectivity check.
            parent_inst_pat = re.compile(rf'^\s*{re.escape(owning_mod)}\s+(\w+)\s*\(', re.MULTILINE)
            full_text = ''
            gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
            if os.path.exists(gz):
                try:
                    full_text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
                    full_text = strip_verilog_comments(full_text)
                except Exception:
                    full_text = ''
            inst_match = parent_inst_pat.search(full_text)
            if not inst_match:
                failures.append(
                    f'[BRIDGE_PARENT_MISSING] {stage}: bridge port {port_name!r} '
                    f'declared on {owning_mod!r} but no parent module instantiates '
                    f'{owning_mod} → parent-scope wireup impossible → FM ABORT.')
                continue
            # (c) instance hookup — find `.PORT(<wire>)` in the instance block
            # Walk forward from the inst match until matching `)`; check for the port
            inst_end = full_text.find(';', inst_match.end())
            inst_block = full_text[inst_match.start():inst_end] if inst_end > 0 else ''
            hookup_pat = re.compile(rf'\.\s*{re.escape(port_name)}\s*\(\s*([^\)]+?)\s*\)')
            hm = hookup_pat.search(inst_block)
            if not hm:
                failures.append(
                    f'[BRIDGE_INSTANCE_HOOKUP_MISSING] {stage}: bridge port '
                    f'{port_name!r} on {owning_mod!r} has NO `.{port_name}(...)` '
                    f'hookup in parent instance → FM ABORT (port unresolved at '
                    f'instantiation scope).')
                continue
            wire_name = hm.group(1).strip()
            # (b) parent module wire decl for the bridge wire
            # Find which module contains the instance match
            preceding = full_text[:inst_match.start()]
            mod_start_re = re.compile(r'^module\s+(\S+)', re.MULTILINE)
            mods = list(mod_start_re.finditer(preceding))
            parent_mod = mods[-1].group(1) if mods else None
            if parent_mod:
                parent_body = _module_body(stage, parent_mod)
                wire_decl_re = re.compile(rf'^\s*(wire|tri|wand|wor)\s+(?:\[[^\]]+\]\s+)?[^;]*\b{re.escape(wire_name.split("[")[0])}\b[^;]*;', re.MULTILINE)
                # Skip wire declaration check for constants / 1'b0 / single-bit literal hookups
                if not wire_name.startswith(("1'b", "0'b", "1'h", "0'h")) and \
                   not wire_decl_re.search(parent_body):
                    failures.append(
                        f'[BRIDGE_PARENT_WIRE_MISSING] {stage}: bridge port {port_name!r} '
                        f'on {owning_mod!r} hooked to wire {wire_name!r} in parent '
                        f'{parent_mod!r} — but no `wire {wire_name};` declaration '
                        f'found in {parent_mod} body → FM ABORT (undeclared net).')
            # (d) driver check for OUTPUT ports
            if port_dir == 'output':
                owning_body = _module_body(stage, owning_mod)
                # Driver = cell whose output pin connects to port_name, or assign port_name = ...
                driver_re = re.compile(rf'(\.\s*(Z|ZN|Q|QN|O)\s*\(\s*{re.escape(port_name)}\s*\)|^\s*assign\s+{re.escape(port_name)}\s*=)', re.MULTILINE)
                if not driver_re.search(owning_body):
                    failures.append(
                        f'[BRIDGE_OUTPUT_UNDRIVEN] {stage}: bridge OUTPUT port '
                        f'{port_name!r} on {owning_mod!r} has NO driver inside the '
                        f'module (no cell .Z/.ZN/.Q/.QN/.O nor assign produces it) '
                        f'→ FM ABORT or undriven port warning escalating to error.')
    return failures


def check_eco_cell_type_in_library(study_path, ref_dir):
    """Check 21 — every NEW ECO cell's cell_type must exist in the technology
    library (proxy: it must appear at least once in the corresponding PreEco
    netlist stage). FM uses the technology .lib/.db at link time; cell types
    that the netlist references but the library doesn't define cause
    `FE-LINK-2 Cannot link cell` + `FM-234 Unresolved references` →
    `FM-156 Failed to set top design` ABORT.

    Run 20260511201004 (round 2 attempt) ABORT root cause: studier emitted
    invented cell types using LOGICAL function names instead of TSMC library
    short forms:
        AI emitted: NOR3D1BWP136P5M156H3P48CPDLVT  (NOR3 — not in library)
        TSMC has:   NR3D1BWP136P5M156H3P48CPDLVT   (NR3 short form)
        AI emitted: AND2D1BWP136P5M156H3P48CPDLVT  (AND2 — not in library)
        TSMC has:   AN2D1BWP136P5M156H3P48CPDLVT   (AN2 short form)
    Both invalid types passed all earlier checks (cell instance is in PostEco
    netlist) but failed at FM link.

    Strategy: for each new_logic_dff / new_logic_gate entry, grep the same-
    stage PreEco netlist for the cell_type string. If 0 matches, the cell type
    almost certainly isn't in the library (the netlist is built from the same
    library FM links against). One-off zero-counts can happen if the cell type
    is novel-but-valid; treat as MEDIUM warning unless the count is 0 in ALL
    3 stages — then HIGH.

    TSMC short-form aliases agent commonly mistypes:
        NR2/NR3/NR4 ≠ NOR2/NOR3/NOR4
        AN2/AN3/AN4 ≠ AND2/AND3/AND4
        ND2/ND3/ND4 ≠ NAND2/NAND3/NAND4
        IV/INV     ≠ INVERT
    When the bad type matches one of these patterns, suggest the corrected
    name in the failure message.
    """
    failures = []
    if not os.path.exists(study_path):
        return failures
    try:
        study = json.loads(Path(study_path).read_text())
    except Exception:
        return failures
    # Collect (inst, cell_type) pairs — dedupe across stages (same inst in all 3)
    seen_inst = set()
    cells = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        for e in study.get(stage, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic_gate', 'new_logic'):
                continue
            inst = e.get('instance_name') or e.get('dff_instance_name', '')
            if inst in seen_inst:
                continue
            seen_inst.add(inst)
            ct = e.get('cell_type', '')
            if ct:
                cells.append((inst, ct))
    if not cells:
        return failures

    # Common TSMC mis-naming corrections — used to suggest fixes
    NAMING_CORRECTIONS = [
        (re.compile(r'^NOR(\d)'),  r'NR\1'),    # NOR3* → NR3*
        (re.compile(r'^AND(\d)'),  r'AN\1'),    # AND2* → AN2*
        (re.compile(r'^NAND(\d)'), r'ND\1'),    # NAND2* → ND2*
        (re.compile(r'^XNOR(\d)'), r'XNR\1'),   # XNOR2* → XNR2*
        (re.compile(r'^INV(?!R)'), 'IV'),       # INV* → IV* (but not INVR)
    ]
    def suggest_correction(ct):
        for pat, repl in NAMING_CORRECTIONS:
            new = pat.sub(repl, ct)
            if new != ct:
                return new
        return None

    # For each cell type, count occurrences in each PreEco stage netlist
    for inst, ct in cells:
        zero_stages = []
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            gz = os.path.join(ref_dir, 'data', 'PreEco', f'{stage}.v.gz')
            if not os.path.exists(gz):
                continue
            try:
                cnt = int(subprocess.run(['zgrep', '-c', ct, gz],
                                         capture_output=True, text=True, timeout=120).stdout.strip() or '0')
            except Exception:
                cnt = 0
            if cnt == 0:
                zero_stages.append(stage)
        if zero_stages:
            sug = suggest_correction(ct)
            sug_text = f'  Suggested TSMC short-form: {sug!r}' if sug else ''
            sev = 'HIGH' if len(zero_stages) == 3 else 'MEDIUM'
            failures.append(
                f'[{sev}/CELL_TYPE_NOT_IN_LIB] {inst}: cell_type {ct!r} has 0 '
                f'occurrences in PreEco {zero_stages} netlist(s) — likely missing '
                f'from technology library, FM will fail with FE-LINK-2 + FM-234 + '
                f'FM-156 ABORT.{sug_text}  TSMC short-form convention: NR2/NR3 (not '
                f'NOR2/NOR3), AN2/AN3 (not AND2/AND3), ND2/ND3 (not NAND2/NAND3).')
    return failures


def check_invalid_wire_decl_syntax(ref_dir):
    """Check 22 — Verilog grammar lint on wire/reg declarations.

    A wire declaration must use a flat identifier as the name. Forms like
    `wire NAME[N] ;` are illegal — `[N]` is bus-bit indexing syntax that's
    valid in port_connections / concatenations but NOT in declarations.

    FM rejects these with:
        Warning: Size mismatch between port & wire declaration ... (SVR-64)
        Error:   Expected ',' or ';' but found '['               (SVR-4)
        Error:   read_verilog/read_sverilog ignored due to errors (FM-599)
    → ABORT before any comparison.

    Run 20260512070625 root cause: round 2 agent direct-edit added
    `wire REG_UmcCfgEco[1] ;` to all 3 PostEco netlists trying to fix a
    different signal-naming issue. FM aborted in PreVerify on every round
    until the engineer (manually) deleted the line. 5+ hours wasted because
    no pre-FM lint caught the broken syntax.

    Streams each PostEco netlist; flags any `wire <name>[<bit>] ;` decl.
    """
    failures = []
    BAD_DECL = re.compile(r'^\s*(wire|tri|wand|wor|reg)\s+(\w+)\[(\d+)\]\s*;\s*$')
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            text = subprocess.run(['zcat', gz], capture_output=True, text=True, timeout=240).stdout
        except Exception:
            continue
        cur_mod = None
        for line_idx, line in enumerate(text.split('\n'), start=1):
            mm = re.match(r'^module\s+(\S+)', line)
            if mm:
                cur_mod = mm.group(1); continue
            if re.match(r'^\s*endmodule', line):
                cur_mod = None; continue
            bm = BAD_DECL.match(line)
            if bm:
                kind, name, bit = bm.group(1), bm.group(2), bm.group(3)
                fixed = f'{name}_{bit}_'
                failures.append(
                    f'[INVALID_WIRE_DECL_SYNTAX] {stage}:{line_idx} module {cur_mod or "?"!r}: '
                    f'`{kind} {name}[{bit}] ;` — bracket form is illegal in a wire decl. '
                    f'Use flat-net underscore-escape: `{kind} {fixed} ;`. FM will reject '
                    f'with SVR-4 + SVR-64 + FM-599 → ABORT in PreVerify (no comparison runs). '
                    f'Likely cause: applier or agent direct-edit passed bus-bit syntax verbatim '
                    f'into a wire declaration without flattening. Delete this line OR rewrite '
                    f'as flat name and update consumers accordingly.')
    return failures


def check_eco_cell_counts(applied):
    """
    WARN (not FAIL) if ECO cell counts differ significantly across stages.
    Route may legitimately have fewer (module renamed in P&R).
    Returns (warnings, failures) — failures are hard FAIL conditions.
    """
    gate_types = ('new_logic_gate', 'new_logic_dff', 'new_logic')
    counts = {}
    for stage, entries in applied.items():
        if not isinstance(entries, list):
            continue
        counts[stage] = sum(
            1 for e in entries
            if e.get('change_type','') in gate_types
            and e.get('status','') in ('INSERTED', 'ALREADY_APPLIED')
        )

    if not counts:
        return [], []

    max_count = max(counts.values())
    warnings = []
    failures = []
    for stage, count in counts.items():
        if count == 0 and max_count > 0:
            failures.append(f'Stage {stage}: 0 ECO cells applied but other stages have {max_count}')
        elif count < max_count * 0.5:
            warnings.append(f'Stage {stage}: {count} cells vs max {max_count} — possible partial application')
    return warnings, failures


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    base  = args.base_dir
    tag   = args.tag
    rnd   = args.round
    jira  = args.jira

    applied_path  = f'{base}/data/{tag}_eco_applied_round{rnd}.json'
    check8_path   = f'{base}/data/{tag}_eco_verilog_validator_round{rnd}.json'
    out_json_path = f'{base}/data/{tag}_eco_pre_fm_check_round{rnd}.json'
    out_rpt_path  = f'{base}/data/{tag}_eco_step5_pre_fm_check_round{rnd}.rpt'
    marker_path   = f'{base}/data/{tag}_eco_step5_pre_fm_check_round{rnd}_marker.txt'

    applied = load_json(applied_path) or {}

    results   = {}
    all_fails = []
    warnings  = []

    # Check 1 — No deferred port declarations
    fails = check_no_deferred(applied)
    results['no_deferred_ports'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[DEFERRED] {f}' for f in fails])

    # Check 2 — Port declarations all applied
    fails = check_port_declarations_applied(applied)
    results['port_declarations_applied'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[PORT_SKIP] {f}' for f in fails])

    # Check 3 — Stage consistency (gates inserted in all stages)
    fails = check_stage_consistency(applied)
    results['stage_consistency'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[STAGE_MISMATCH] {f}' for f in fails])

    # Check 4 — No UNHANDLED entries
    fails = check_no_unhandled(applied)
    results['no_unhandled'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[UNHANDLED] {f}' for f in fails])

    # Check 5 — eco_verilog_validator Verilog validator (runs eco_verilog_validator.sh externally)
    fails = check_check8(check8_path)
    # Build nested per-stage structure as required by mandatory output contract
    chk8_json = load_json(check8_path) or {}
    results['check8_verilog_validator'] = {
        'Synthesize': chk8_json.get('Synthesize', 'MISSING'),
        'PrePlace':   chk8_json.get('PrePlace',   'MISSING'),
        'Route':      chk8_json.get('Route',      'MISSING'),
        'errors':     fails,
    }
    results['check8_verilog'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend([f'[SVR4_SVR9] {f}' for f in fails])

    # Check 6 — ECO cell counts (warnings only for partial, hard fail for zero)
    w, fails = check_eco_cell_counts(applied)
    results['eco_cell_counts'] = 'PASS' if not fails else 'FAIL'
    warnings.extend(w)
    all_fails.extend([f'[ZERO_CELLS] {f}' for f in fails])

    # Check 7 — Verify INSERTED gates actually exist in PostEco netlist
    # AND are in the host module the study assigned. Catches:
    #   (a) GHOST_INSERT — applier reports INSERTED but Perl pipe silently
    #       dropped the cell (e.g. module_name="UNKNOWN" / nonexistent module).
    #   (b) WRONG_MODULE — cell landed in netlist but in a different module
    #       than study's `module_name`. Run 20260526225832 R1 root cause was
    #       (a); the legacy global-grep would have missed (b).
    study_path_check7 = f'{base}/data/{tag}_eco_preeco_study.json'
    fails = check_cells_in_netlist(applied, args.ref_dir, study_path=study_path_check7)
    results['cells_in_netlist'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 8 — Every n_eco_* net in PostEco netlist must have ≥ 2 references.
    # Catches bus-rename failures where the rename was specified in study JSON
    # but eco_netlist_port_rewire.py didn't apply it to the netlist (driver missing).
    fails = check_undriven_eco_nets(args.ref_dir)
    results['undriven_eco_nets'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 9 — bus-concat integrity. For port_connection entries with
    # bus_bit_index, the netlist must still have .port({...}) — not collapsed
    # to a single net (catches broad-regex rewire corruption).
    fails = check_bus_concat_intact(args.ref_dir, applied)
    results['bus_concat_intact'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 10 — every APPLIED port_declaration / port_connection entry must
    # have its edit physically present in the netlist. Catches the silent
    # APPLIED-but-no-edit failure mode.
    fails = check_port_edits_in_netlist(args.ref_dir, applied)
    results['port_edits_in_netlist'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 11 — every APPLIED rewire entry must have cell.pin → new_net
    # physically in the netlist (closes the last "JSON trust" gap).
    fails = check_rewires_in_netlist(args.ref_dir, applied)
    results['rewires_in_netlist'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 12 — Full semantic equivalence between Step 3 study JSON and
    # PostEco netlist (Option B). Comment-aware Verilog-semantic parser
    # verifies every confirmed study entry's intent is physically present.
    # Catches comment-masked edits, bit-position errors, wrong-instance
    # matches that regex spot checks (Checks 8/9/10/11) can miss.
    study_path = f'{base}/data/{tag}_eco_preeco_study.json'
    fails = check_semantic_verify(study_path, args.ref_dir)
    results['semantic_verify'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 13 — every ECO cell's per-stage input pin must have a real driver
    # in the PostEco netlist (cell output / port / wire decl). Catches the
    # "agent recorded a stale or non-existent per-stage net name" class of bug.
    fails = check_eco_input_drivers(study_path, args.ref_dir)
    results['eco_input_drivers'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 13b — STRICT driver presence for new-ECO-cell input nets. Closes
    # the gap in Check 13: that check pollutes its driven-set with ALL
    # submodule port connections (input AND output, since direction is unknown
    # for user-defined modules), so it accepts the new ECO cell's own consumer
    # reference `.A1(W)` as a "driver" for W. This stricter check requires a
    # real driver: cell output pin, assign, module input/inout port, or
    # sub-instance OUTPUT port (direction resolved from module headers).
    # Catches the exact undriven-`REG_UmcCfgEco_12_` failure that bit
    # 20260525085948 R1 and 20260526203822 R1 (Mode A undriven cone).
    fails = check_input_net_strict_driver(study_path, args.ref_dir)
    results['input_net_strict_driver'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 14 — Duplicate port names in any module port list header.
    # Duplicate ports cause Verilog compile errors that block FM elaboration.
    fails = check_duplicate_ports(args.ref_dir)
    results['duplicate_ports'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 15 — ECO cell output pin names must match the cell's actual output pin.
    # Wrong pin causes FE-LINK-7 ABORT_LINK (FM cannot build verification model).
    # Example: MUX2 output is .Z not .ZN; IND2 is .ZN not .Z.
    fails = check_eco_output_pin_names(applied, args.ref_dir)
    results['eco_output_pin_names'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 16 — ECO cell output nets referenced outside declaring module must have
    # a port_declaration entry applied. Missing causes FE-LINK-7 ABORT_LINK.
    fails = check_missing_output_port_decls(applied, args.ref_dir)
    results['missing_output_port_decls'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 18 — defense-in-depth: every port_connection in study must reference
    # a port that exists in the target child module's PostEco port list. Mirrors
    # Step 3 Check 3e but on the live netlist — catches Pass 2 silent skips.
    fails = check_port_conn_target_exists(study_path, args.ref_dir)
    results['port_conn_target_exists'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 17 — Mode S (scan-stitching) stitching landed correctly in netlist.
    # For every new_logic_dff entry flagged with mode_S_applied / requires_scan_stitching,
    # verify the host module declares the 3 stitching ports (SI_in / SE_in / Q_out),
    # the assign for Q_out is present, and the DFF's per-stage SE/SI are bridged
    # (NOT tied to 1'b0). Catches missing stitching that breaks Route FM.
    fails = check_mode_s_stitching(study_path, args.ref_dir)
    results['mode_s_stitching'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 19 — duplicate wire/reg declarations in same module (FM-599 ABORT).
    # Run 20260511083831 ABORT root cause: applier inserted `wire UNCONNECTED_19090 ;`
    # twice in ddrss_umccmd_t_umcregcmd. Catches that exact bug + any future
    # duplicate-decl insertion before wasting a 30+ min FM cycle.
    fails = check_duplicate_wire_decls(args.ref_dir)
    results['duplicate_wire_decls'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 20 — cross-module bridge port connectivity audit.
    # For every bridge_port_role port_declaration, verify the parent module
    # actually instantiates the owning module AND has a wire decl for the
    # bridge wire AND the instance line has the .PORT(WIRE) hookup AND output
    # bridge ports have an internal driver. Catches the "studier emitted half
    # a bridge" failure mode at Step 5 instead of FM ABORT.
    fails = check_cross_module_bridge_connectivity(study_path, args.ref_dir)
    results['cross_module_bridge_connectivity'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 21 — every NEW ECO cell's cell_type must exist in PreEco netlist
    # (proxy for "exists in the technology library FM links against"). Catches
    # invented cell type names (e.g. NOR3D1... when TSMC short-form is NR3D1...)
    # before FM ABORTs with FE-LINK-2 + FM-234 + FM-156.
    fails = check_eco_cell_type_in_library(study_path, args.ref_dir)
    results['eco_cell_type_in_library'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 22 — Verilog grammar lint on wire decls (SVR-4 / SVR-64 ABORT
    # prevention). Catches `wire <name>[<bit>] ;` bracket-form decls before
    # FM rejects them at read_verilog time. Run 20260512070625 root cause:
    # `wire REG_UmcCfgEco[1] ;` triggered FM-599 ABORT for 3 rounds straight;
    # this 1-second check would have flagged it before round 1's FM submit.
    fails = check_invalid_wire_decl_syntax(args.ref_dir)
    results['invalid_wire_decl_syntax'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check: non-Verilog markers in PostEco netlists (SVR-4 prevention)
    # Agent-generated Perl scripts sometimes emit 'ECO_PERL_DONE: <stage>'
    # without a '//' prefix — this appears at the Verilog top-level and causes
    # SVR-4 "Expected 'module'" → FM-599 ABORT. Catch before FM submission.
    _marker_re = re.compile(r'^(ECO_PERL_DONE|ECO_DONE|ECO_END|PERL_DONE|ECO_SCRIPT_DONE)\b')
    fails = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(args.ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            import subprocess as _sp3b
            r = _sp3b.run(f'zcat {gz} | grep -nE "^(ECO_PERL_DONE|ECO_DONE|ECO_END|PERL_DONE|ECO_SCRIPT_DONE)"',
                          shell=True, capture_output=True, text=True, timeout=60)
            if r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    fails.append(f'[NON_VERILOG_MARKER] {stage}: {line.strip()} — '
                                 f'non-Verilog marker in PostEco (no // prefix). '
                                 f'Causes SVR-4 / FM-599 ABORT. '
                                 f'Remove line or prefix with //.')
        except Exception:
            pass
    results['non_verilog_markers'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    # Check 22 — GAP-3: no ECO functional gate with constant input (1'b0 or 1'b1)
    # A NOR3 with .A3(1'b0) is functionally a NOR2 — constant input disables gating.
    # DFF scan pins (SE, SI, RN etc.) are exempt — constants are valid there.
    _DFF_CONST_PINS = {'SE', 'SI', 'RN', 'R', 'SN', 'CK', 'CP', 'CDN', 'SDN'}
    fails = []
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz = os.path.join(args.ref_dir, 'data', 'PostEco', f'{stage}.v.gz')
        if not os.path.exists(gz):
            continue
        try:
            import subprocess as _sp
            r = _sp.run(
                f"zcat {gz} | grep -n \".A.*( 1'b[01] )\" ",
                shell=True, capture_output=True, text=True, timeout=60
            )
            for line in (r.stdout or '').splitlines():
                if 'eco_' not in line.lower():
                    continue  # only ECO-inserted cells
                pin_m = re.search(r'\.([A-Z][A-Z0-9]*)\s*\(\s*1\'b[01]\s*\)', line)
                if not pin_m:
                    continue
                pin = pin_m.group(1)
                if pin in _DFF_CONST_PINS:
                    continue
                lineno = line.split(':')[0]
                fails.append(
                    f"[NO_CONSTANT_FUNCTIONAL_INPUTS] {stage}:{lineno} — "
                    f"ECO gate pin .{pin}(1'b0/1) constant on functional input. "
                    f"Gate partially disabled — downgrade to fewer-input cell (NOR3→NOR2).")
        except Exception:
            pass
    results['no_constant_functional_inputs'] = 'PASS' if not fails else 'FAIL'
    all_fails.extend(fails)

    passed = len(all_fails) == 0

    # ── Write JSON ────────────────────────────────────────────────────────────
    out = {
        'tag':           tag,
        'round':         rnd,
        'jira':          jira,
        'passed':        passed,
        'failures':      all_fails,
        'warnings':      warnings,
        'check_summary': results,
    }
    with open(out_json_path, 'w') as f:
        json.dump(out, f, indent=2)

    # ── Write RPT ─────────────────────────────────────────────────────────────
    status_str = 'PASS' if passed else 'FAIL'
    lines = [
        '=' * 72,
        f'STEP 5 — PRE-FM QUALITY CHECK (Round {rnd})',
        f'Tag: {tag}  |  JIRA: {jira}',
        '=' * 72,
        f'RESULT: {status_str}',
        '',
    ]
    for check, result in results.items():
        lines.append(f'  {check:<35}: {result}')
    if all_fails:
        lines += ['', 'FAILURES:']
        lines += [f'  {f}' for f in all_fails]
    if warnings:
        lines += ['', 'WARNINGS (non-blocking):']
        lines += [f'  {w}' for w in warnings]
    lines += ['', '=' * 72]
    rpt_text = '\n'.join(lines) + '\n'
    with open(out_rpt_path, 'w') as f:
        f.write(rpt_text)

    # ── Write marker ──────────────────────────────────────────────────────────
    marker = f'ECO_SCRIPT_LAUNCHED: eco_pre_fm_check.py\n  result: {status_str}\n  output: {out_json_path}\n'
    with open(marker_path, 'w') as f:
        f.write(marker)

    print(rpt_text)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
