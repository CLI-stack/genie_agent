#!/usr/bin/env python3
"""
test_step4_step5.py — Comprehensive test suite for Step 4 (eco_applier) and Step 5 (eco_pre_fm_check)

Tests eco_perl_spec.py, eco_passes_2_4.py, and eco_pre_fm_check.py
with synthetic netlists and study JSONs.

Each test verifies a specific scenario:
  PASS cases  — expected to succeed end-to-end
  FAIL cases  — expected to be caught by Step 5 script

Usage:
    python3 script/eco_scripts/test_step4_step5.py
    python3 script/eco_scripts/test_step4_step5.py -v   (verbose)
    python3 script/eco_scripts/test_step4_step5.py -k T3 (run single test)
"""

import argparse, gzip, json, os, shutil, subprocess, sys, tempfile, textwrap

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PERL_SPEC  = os.path.join(SCRIPT_DIR, 'eco_perl_spec.py')
PASSES     = os.path.join(SCRIPT_DIR, 'eco_passes_2_4.py')
PRE_FM     = os.path.join(SCRIPT_DIR, 'eco_pre_fm_check.py')
CHECK8     = os.path.join(SCRIPT_DIR, 'eco_check8.sh')

PASS = 'PASS'
FAIL = 'FAIL'

RESULTS = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def gz_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, 'wt') as f:
        f.write(content)

def gz_read(path):
    with gzip.open(path, 'rt', errors='replace') as f:
        return f.read()

def run(cmd, cwd=None, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       cwd=cwd, timeout=timeout)
    return r.returncode, r.stdout + r.stderr

def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def read_json(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}

# Minimal hierarchical Verilog module for testing
def make_verilog(module_name, ports=None, cells=None, extra_wires=None):
    ports = ports or ['input CLK', 'input IN1', 'output OUT1']
    cells = cells or []
    extra_wires = extra_wires or []
    port_names = [p.split()[-1] for p in ports]
    port_list = ' , '.join(port_names)
    wire_decls = '\n'.join(f'  wire {w} ;' for w in extra_wires)
    cell_lines = '\n'.join(cells)
    return textwrap.dedent(f"""\
        module {module_name} ( {port_list} ) ;
        {"  " + chr(10).join("  " + p + " ;" for p in ports)}
        {wire_decls}
        {cell_lines}
        endmodule
    """)

def make_netlist(modules):
    """Concatenate multiple modules into one netlist string."""
    return '\n\n'.join(modules)

# Minimal study JSON for a gate insertion
def gate_entry(instance_name, cell_type, gate_fn, output_net, port_connections,
               module_name, needs_wire=True, change_type='new_logic_gate',
               **kwargs):
    e = {
        'change_type': change_type,
        'instance_name': instance_name,
        'cell_type': cell_type,
        'gate_function': gate_fn,
        'output_net': output_net,
        'module_name': module_name,
        'instance_scope': '',
        'scope_is_tile_root': False,
        'needs_explicit_wire_decl': needs_wire,
        'confirmed': True,
        'port_connections': port_connections,
        'port_connections_per_stage': {
            'Synthesize': port_connections,
            'PrePlace':   port_connections,
            'Route':      port_connections,
        },
    }
    e.update(kwargs)
    return e

def port_decl_entry(signal_name, module_name, direction='output'):
    return {
        'change_type': 'port_declaration',
        'signal_name': signal_name,
        'module_name': module_name,
        'declaration_type': direction,
        'instance_name': signal_name,
        'confirmed': True,
    }

def port_conn_entry(instance_name, port_name, net_name, parent_module):
    return {
        'change_type': 'port_connection',
        'instance_name': instance_name,
        'port_name': port_name,
        'net_name': net_name,
        'module_name': parent_module,
        'instance_name': instance_name,
        'confirmed': True,
    }

def rewire_entry(cell_name, pin, old_net, new_net, module_name):
    return {
        'change_type': 'rewire',
        'cell_name': cell_name,
        'pin': pin,
        'old_net': old_net,
        'new_net': new_net,
        'module_name': module_name,
        'instance_name': cell_name,
        'confirmed': True,
    }


# ── Test runner ───────────────────────────────────────────────────────────────

def run_test(name, desc, setup_fn, expected_step5, expected_failures=None,
             verbose=False, run_filter=None):
    if run_filter and run_filter not in name:
        return

    tmpdir = tempfile.mkdtemp(prefix=f'eco_test_{name}_')
    try:
        passed, detail = _run_test_inner(
            name, desc, setup_fn, expected_step5, expected_failures,
            tmpdir, verbose
        )
    except Exception as ex:
        passed = False
        detail = f'EXCEPTION: {ex}'
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    status = '✅ PASS' if passed else '❌ FAIL'
    print(f'{status}  {name}: {desc}')
    if not passed or verbose:
        print(f'       {detail}')
    RESULTS.append((name, passed, detail))


def _run_test_inner(name, desc, setup_fn, expected_step5, expected_failures,
                    tmpdir, verbose):
    tag   = f'test_{name}'
    jira  = '9999'
    base  = tmpdir
    ref   = os.path.join(tmpdir, 'ref')
    round_n = 1

    # Setup directories
    os.makedirs(f'{ref}/data/PreEco', exist_ok=True)
    os.makedirs(f'{ref}/data/PostEco', exist_ok=True)
    os.makedirs(f'{base}/data', exist_ok=True)
    os.makedirs(f'{base}/runs', exist_ok=True)

    # Call test-specific setup to get study JSON + netlist content
    # setup_fn may return (study, netlist) or (study, netlist, applied_override)
    result = setup_fn(jira, tag)
    study, netlist_content = result[0], result[1]
    applied_override = result[2] if len(result) > 2 else None

    # Write preeco_study.json
    write_json(f'{base}/data/{tag}_eco_preeco_study.json', study)

    # Write netlist to all 3 stages
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        gz_write(f'{ref}/data/PreEco/{stage}.v.gz',  netlist_content)
        gz_write(f'{ref}/data/PostEco/{stage}.v.gz', netlist_content)

    # Write fake check8 result (PASS by default — test can override via study)
    check8_override = study.pop('__check8_override__', None)
    check8_result = check8_override or {
        'Synthesize': 'PASS', 'PrePlace': 'PASS', 'Route': 'PASS',
        'errors': [], 'f2_preexisting_count': 0
    }
    write_json(f'{base}/data/{tag}_eco_check8_round{round_n}.json', check8_result)

    # ── Step 4a: eco_perl_spec.py ─────────────────────────────────────────────
    applied_all = {}
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        rc, out = run(
            f'python3 {PERL_SPEC} '
            f'--tag {tag} --stage {stage} --round {round_n} '
            f'--jira {jira} --tile test_tile '
            f'--study {base}/data/{tag}_eco_preeco_study.json '
            f'--posteco {ref}/data/PostEco/{stage}.v.gz '
            f'--status {base}/data/{tag}_eco_perl_spec_{stage}.json',
            cwd=base
        )
        if verbose:
            print(f'  perl_spec {stage} rc={rc}')

        spec_j = read_json(f'{base}/data/{tag}_eco_perl_spec_{stage}.json')
        pl_path = f'{base}/runs/eco_apply_{tag}_{stage}.pl'

        # ── Step 4b: execute Perl if script exists ────────────────────────────
        if os.path.exists(pl_path):
            pl_rc, pl_out = run(f'perl {pl_path} {ref}/data/PostEco/{stage}.v.gz', cwd=base)
            if verbose and pl_rc != 0:
                print(f'  perl {stage} FAILED: {pl_out[:200]}')

        # ── Step 4c: eco_passes_2_4.py ────────────────────────────────────────
        rc2, out2 = run(
            f'python3 {PASSES} '
            f'--stage {stage} --tag {tag} --round {round_n} --jira {jira} '
            f'--study {base}/data/{tag}_eco_preeco_study.json '
            f'--ref-dir {ref} '
            f'--status {base}/data/{tag}_eco_passes_2_4_{stage}.json',
            cwd=base
        )
        if verbose:
            print(f'  passes_2_4 {stage} rc={rc2}')

        p_j = read_json(f'{base}/data/{tag}_eco_passes_2_4_{stage}.json')
        applied_all[stage] = (spec_j.get('entries', []) + p_j.get('entries', []))

    # Write combined applied JSON — use override if test provides pre-built applied state
    if applied_override is not None:
        write_json(f'{base}/data/{tag}_eco_applied_round{round_n}.json', applied_override)
    else:
        write_json(f'{base}/data/{tag}_eco_applied_round{round_n}.json', applied_all)

    # ── Step 5: eco_pre_fm_check.py ───────────────────────────────────────────
    rc5, out5 = run(
        f'python3 {PRE_FM} '
        f'--tag {tag} --round {round_n} '
        f'--base-dir {base} --ref-dir {ref} --jira {jira}',
        cwd=base
    )

    result_j = read_json(f'{base}/data/{tag}_eco_pre_fm_check_round{round_n}.json')
    actual_passed = result_j.get('passed', False)
    actual_failures = result_j.get('failures', [])

    if verbose:
        print(f'  Step5 exit={rc5}, passed={actual_passed}')
        print(f'  failures: {actual_failures}')

    # Verify outcome
    if expected_step5 == PASS and not actual_passed:
        return False, f'Expected PASS but FAIL: {actual_failures}'
    if expected_step5 == FAIL and actual_passed:
        return False, f'Expected FAIL but PASS'

    # Verify specific failure types if requested
    if expected_failures and expected_step5 == FAIL:
        all_fail_text = ' '.join(actual_failures)
        for expected_kw in expected_failures:
            if expected_kw not in all_fail_text:
                return False, f'Expected failure keyword "{expected_kw}" not found in: {actual_failures}'

    return True, f'passed={actual_passed}, failures={len(actual_failures)}'


# ── Test cases ────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# T1 — PASS: Clean gate insertion + rewire, all entries applied
# ─────────────────────────────────────────────────────────────────────────────
def setup_T1(jira, tag):
    netlist = make_netlist([
        make_verilog('test_mod',
            ports=['input IN1', 'input IN2', 'output OUT1'],
            extra_wires=['old_net'],
            cells=[
                f'AND2D1 existing_and ( .A1( IN1 ) , .A2( IN2 ) , .Z( old_net ) ) ;',
                f'INVD1 existing_inv ( .I( old_net ) , .ZN( OUT1 ) ) ;',
            ])
    ])
    study = {
        'Synthesize': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
            rewire_entry('existing_inv', 'I', 'old_net', 'n_eco_9999_d001', 'test_mod'),
        ],
        'PrePlace': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
            rewire_entry('existing_inv', 'I', 'old_net', 'n_eco_9999_d001', 'test_mod'),
        ],
        'Route': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
            rewire_entry('existing_inv', 'I', 'old_net', 'n_eco_9999_d001', 'test_mod'),
        ],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T2 — FAIL: Port declaration deferred ("deferred to Round 2")
# ─────────────────────────────────────────────────────────────────────────────
def setup_T2(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    applied = {
        'Synthesize': [
            {'change_type': 'new_logic_gate', 'name': 'eco_9999_d001', 'status': 'INSERTED', 'reason': ''},
            {'change_type': 'port_declaration', 'name': 'NewPort',
             'status': 'SKIPPED', 'reason': 'port_declaration deferred to Round 2'},
        ],
        'PrePlace': [], 'Route': [],
    }
    return study, netlist, applied


# ─────────────────────────────────────────────────────────────────────────────
# T3 — FAIL: Port connection without comma (depth tracker finds wrong inst_close)
# Simulate what happened with DCQARB: last port line ends with ) not ,
# ─────────────────────────────────────────────────────────────────────────────
def setup_T3(jira, tag):
    # Instance with many ports — last port before ) ; has NO trailing comma
    cells = [
        'AND2D1 big_inst ( .A1( IN1 ) , .A2( IN2 ) ,',
        '    .portA( IN1 ) ,',
        '    .portB( IN2 ) ,',
        '    .portC( IN1 ) ,',
        '    .portD( IN2 )',   # <- NO trailing comma here
        ') ;',
    ]
    netlist = make_netlist([make_verilog('test_mod',
        ports=['input IN1', 'input IN2', 'output OUT1'],
        cells=cells)])
    study = {
        'Synthesize': [
            {**port_conn_entry('big_inst', 'NewPort', 'IN1', 'test_mod'),
             'confirmed': True},
        ],
        'PrePlace': [], 'Route': [],
    }
    # After applying, manually corrupt to simulate missing comma
    # (the fix should prevent this, so T3 should PASS with the fix applied)
    # We test that the fix works correctly — this should now PASS
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T4 — FAIL: Gate inserted without cell_type (SVR4_missing_cell_type)
# eco_pre_fm_check detects via eco_check8 result
# ─────────────────────────────────────────────────────────────────────────────
def setup_T4(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    # Inject failing check8 result
    check8_fail = {
        'Synthesize': 'FAIL', 'PrePlace': 'PASS', 'Route': 'PASS',
        'errors': ['[SVR4_missing_cell_type] test_mod | line 10: eco_9999_d001 ( .I(IN1) )'],
        'f2_preexisting_count': 0
    }
    study = {
        '__check8_override__': check8_fail,
        'Synthesize': [], 'PrePlace': [], 'Route': [],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T5 — FAIL: Duplicate wire declaration (F1_dup_wire → SVR9)
# eco_check8 catches F1_dup_wire as FAIL
# ─────────────────────────────────────────────────────────────────────────────
def setup_T5(jira, tag):
    netlist = make_netlist([make_verilog('test_mod', extra_wires=['dup_wire'])])
    check8_fail = {
        'Synthesize': 'FAIL', 'PrePlace': 'PASS', 'Route': 'PASS',
        'errors': ['[F1_dup_wire] test_mod | line 5: wire dup_wire declared twice'],
        'f2_preexisting_count': 0
    }
    study = {
        '__check8_override__': check8_fail,
        'Synthesize': [], 'PrePlace': [], 'Route': [],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T6 — FAIL: Stage mismatch — gate INSERTED in Syn/PP but SKIPPED in Route
# ─────────────────────────────────────────────────────────────────────────────
def setup_T6(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    applied = {
        'Synthesize': [{'change_type': 'new_logic_gate', 'name': 'eco_9999_d001', 'status': 'INSERTED', 'reason': ''}],
        'PrePlace':   [{'change_type': 'new_logic_gate', 'name': 'eco_9999_d001', 'status': 'INSERTED', 'reason': ''}],
        'Route':      [{'change_type': 'new_logic_gate', 'name': 'eco_9999_d001', 'status': 'SKIPPED',  'reason': 'module not found in Route'}],
    }
    return study, netlist, applied


# ─────────────────────────────────────────────────────────────────────────────
# T7 — FAIL: UNHANDLED change_type
# ─────────────────────────────────────────────────────────────────────────────
def setup_T7(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    applied = {
        'Synthesize': [{'change_type': 'unknown_type', 'name': 'eco_x',
                        'status': 'UNHANDLED', 'reason': 'unknown_type not handled by eco_perl_spec'}],
        'PrePlace': [], 'Route': [],
    }
    return study, netlist, applied


# ─────────────────────────────────────────────────────────────────────────────
# T8 — FAIL: Port declaration SKIPPED (not deferred — module not found)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T8(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    applied = {
        'Synthesize': [{'change_type': 'port_declaration', 'name': 'NewPort',
                        'status': 'SKIPPED', 'reason': 'module nonexistent_module not found in Synthesize'}],
        'PrePlace': [], 'Route': [],
    }
    return study, netlist, applied


# ─────────────────────────────────────────────────────────────────────────────
# T9 — FAIL: SVR4 trailing comma (check8 FAIL)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T9(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    check8_fail = {
        'Synthesize': 'FAIL', 'PrePlace': 'FAIL', 'Route': 'PASS',
        'errors': [
            '[SVR4_trailing_comma] test_mod | line 7: trailing comma before ) ;',
            '[SVR4_trailing_comma] test_mod | line 15: trailing comma before ) ;',
        ],
        'f2_preexisting_count': 5
    }
    study = {
        '__check8_override__': check8_fail,
        'Synthesize': [], 'PrePlace': [], 'Route': [],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T10 — FAIL: Zero ECO cells applied in Route (module renamed by P&R)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T10(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    ins = lambda n: {'change_type': 'new_logic_gate', 'name': n, 'status': 'INSERTED', 'reason': ''}
    applied = {
        'Synthesize': [ins('eco_9999_d001'), ins('eco_9999_d002')],
        'PrePlace':   [ins('eco_9999_d001'), ins('eco_9999_d002')],
        'Route':      [],  # zero cells — module renamed in Route
    }
    return study, netlist, applied


# ─────────────────────────────────────────────────────────────────────────────
# T11 — PASS: Pre-existing F2 implicit wire conflicts (NOT a FAIL)
# Hundreds of pre-existing F2 — eco_check8 must not fail for these
# ─────────────────────────────────────────────────────────────────────────────
def setup_T11(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    # check8 PASS despite many F2 pre-existing
    check8_pass = {
        'Synthesize': 'PASS', 'PrePlace': 'PASS', 'Route': 'PASS',
        'errors': [],
        'f2_preexisting_count': 150  # many pre-existing F2 — should still PASS
    }
    study = {
        '__check8_override__': check8_pass,
        'Synthesize': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
        ],
        'PrePlace': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
        ],
        'Route': [
            gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod'),
        ],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T12 — FAIL: SVR4 double comma in port connections
# ─────────────────────────────────────────────────────────────────────────────
def setup_T12(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    check8_fail = {
        'Synthesize': 'FAIL', 'PrePlace': 'PASS', 'Route': 'PASS',
        'errors': ['[SVR4_double_comma] test_mod | line 12: double comma ,, in port connections'],
        'f2_preexisting_count': 0
    }
    study = {
        '__check8_override__': check8_fail,
        'Synthesize': [], 'PrePlace': [], 'Route': [],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T13 — PASS: UNCONNECTED_* properly renamed (no FAIL)
# eco_perl_spec should rename and add explicit wire — check5 should PASS
# ─────────────────────────────────────────────────────────────────────────────
def setup_T13(jira, tag):
    # Netlist with REGCMD-like instance containing UNCONNECTED in port bus
    netlist = make_netlist([
        make_verilog('test_mod',
            ports=['input IN1', 'output OUT1'],
            extra_wires=['UNCONNECTED_42'],
            cells=[
                'REGMOD REGCMD ( .REG_Data( { UNCONNECTED_41 , UNCONNECTED_42 , UNCONNECTED_43 } ) ) ;',
                'AND2D1 eco_9999_g1 ( .A1( IN1 ) , .A2( n_eco_9999_cfg ) , .Z( OUT1 ) ) ;',
            ])
    ])
    unconn_rewire = {
        'original_unconnected': 'UNCONNECTED_42',
        'original_per_stage': {
            'Synthesize': 'UNCONNECTED_42',
            'PrePlace':   'UNCONNECTED_42',
            'Route':      'UNCONNECTED_42',
        },
        'named_net': 'n_eco_9999_cfg',
        'needs_explicit_wire_decl': True,
        'also_rewire_port_bus': True,
        'port_bus_instance': 'REGCMD',
        'port_bus_instance_per_stage': {},
        'port_bus_name': 'REG_Data',
        'port_bus_bit': 1,
    }
    entry = gate_entry('eco_9999_g1', 'AND2D1', 'AND2', 'OUT1',
                       {'A1': 'IN1', 'A2': 'n_eco_9999_cfg', 'Z': 'OUT1'},
                       'test_mod', needs_wire=False)
    entry['unconnected_rewires'] = [unconn_rewire]
    study = {
        'Synthesize': [entry],
        'PrePlace':   [entry],
        'Route':      [entry],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T14 — FAIL: All 3 stages FAIL in check8 (SVR4_bare_paren)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T14(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    check8_fail = {
        'Synthesize': 'FAIL', 'PrePlace': 'FAIL', 'Route': 'FAIL',
        'errors': [
            '[SVR4_bare_paren] test_mod | line 8: bare ) without ; in port list',
        ],
        'f2_preexisting_count': 0
    }
    study = {
        '__check8_override__': check8_fail,
        'Synthesize': [], 'PrePlace': [], 'Route': [],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T15 — PASS: ALREADY_APPLIED entries are fine (re-running same round)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T15(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    entry = gate_entry('eco_9999_d001', 'INVD1', 'INV', 'n_eco_9999_d001',
                       {'I': 'IN1', 'ZN': 'n_eco_9999_d001'}, 'test_mod')
    entry['status'] = 'ALREADY_APPLIED'
    entry['reason'] = 'grep found eco_9999_d001 in PostEco Synthesize'
    study = {
        'Synthesize': [entry],
        'PrePlace':   [entry],
        'Route':      [entry],
    }
    return study, netlist


# ─────────────────────────────────────────────────────────────────────────────
# T16 — FAIL: Port connection SKIPPED (not deferred — net missing)
# Should be caught by Check 2 (port_declarations_applied)
# ─────────────────────────────────────────────────────────────────────────────
def setup_T16(jira, tag):
    netlist = make_netlist([make_verilog('test_mod')])
    study = {'Synthesize': [], 'PrePlace': [], 'Route': []}
    applied = {
        'Synthesize': [{'change_type': 'port_connection', 'name': 'CHILD_INST',
                        'status': 'SKIPPED', 'reason': 'net new_net not found in PostEco Synthesize'}],
        'PrePlace': [], 'Route': [],
    }
    return study, netlist, applied


# ── Main ──────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# T17 — PASS: undo_instance removes old gate + wire, new gate inserted cleanly
# Simulates replacing a previously-inserted gate with a different one
# ─────────────────────────────────────────────────────────────────────────────
def setup_T17(jira, tag):
    # PostEco already has old gate from a prior round (gate strategy being replaced)
    old_inst = f'eco_{jira}_c001_old'
    old_wire = f'n_eco_{jira}_c001_old'
    new_inst = f'eco_{jira}_c001_new'
    new_wire = f'n_eco_{jira}_c001_new'
    netlist = make_netlist([
        make_verilog('test_mod',
            ports=['input IN1', 'output OUT1'],
            extra_wires=[old_wire],
            cells=[
                f'  INVD1 {old_inst} ( .I( IN1 ) , .ZN( {old_wire} ) ) ;',
                f'  AND2D1 existing_and ( .A1( IN1 ) , .A2( {old_wire} ) , .Z( OUT1 ) ) ;',
            ])
    ])
    # Round N: undo old gate, insert replacement
    undo = {
        'change_type': 'undo_instance',
        'instance_name': old_inst,
        'output_net': old_wire,
        'module_name': 'test_mod',
        'confirmed': True,
    }
    new_gate = gate_entry(new_inst, 'AND2D1', 'AND2', new_wire,
                          {'A1': 'IN1', 'A2': 'IN1', 'Z': new_wire},
                          'test_mod', needs_wire=True)
    study = {
        'Synthesize': [undo, new_gate],
        'PrePlace':   [undo, new_gate],
        'Route':      [undo, new_gate],
    }
    return study, netlist


def main():
    p = argparse.ArgumentParser()
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('-k', '--filter', default=None, help='Run only test matching this string')
    args = p.parse_args()

    kw = dict(verbose=args.verbose, run_filter=args.filter)

    print('\n' + '='*65)
    print('  Step 4 + Step 5 Test Suite')
    print('='*65 + '\n')

    # PASS cases
    run_test('T1',  'Clean insertion + rewire (baseline PASS)',
             setup_T1,  PASS, **kw)
    run_test('T3',  'Port connection on instance with multi-line bus ports (PASS after fix)',
             setup_T3,  PASS, **kw)
    run_test('T11', 'Pre-existing F2 implicit wires do NOT fail (PASS)',
             setup_T11, PASS, **kw)
    run_test('T13', 'UNCONNECTED_* rename + explicit wire (PASS)',
             setup_T13, PASS, **kw)
    run_test('T15', 'ALREADY_APPLIED entries are valid (PASS)',
             setup_T15, PASS, **kw)
    run_test('T17', 'undo_instance removes old gate + wire, new gate inserted (PASS)',
             setup_T17, PASS, **kw)

    # FAIL cases
    run_test('T2',  'Port declaration deferred to Round 2',
             setup_T2,  FAIL, expected_failures=['DEFERRED'], **kw)
    run_test('T4',  'SVR4_missing_cell_type in check8',
             setup_T4,  FAIL, expected_failures=['SVR4'], **kw)
    run_test('T5',  'F1_dup_wire (duplicate wire) in check8',
             setup_T5,  FAIL, expected_failures=['SVR4_SVR9'], **kw)
    run_test('T6',  'Stage mismatch — gate SKIPPED in Route',
             setup_T6,  FAIL, expected_failures=['STAGE_MISMATCH'], **kw)
    run_test('T7',  'UNHANDLED change_type',
             setup_T7,  FAIL, expected_failures=['UNHANDLED'], **kw)
    run_test('T8',  'Port declaration SKIPPED (module not found)',
             setup_T8,  FAIL, expected_failures=['PORT_SKIP'], **kw)
    run_test('T9',  'SVR4_trailing_comma in check8',
             setup_T9,  FAIL, expected_failures=['SVR4_SVR9'], **kw)
    run_test('T10', 'Zero ECO cells in Route',
             setup_T10, FAIL, expected_failures=['ZERO_CELLS'], **kw)
    run_test('T12', 'SVR4_double_comma in check8',
             setup_T12, FAIL, expected_failures=['SVR4_SVR9'], **kw)
    run_test('T14', 'SVR4_bare_paren all stages',
             setup_T14, FAIL, expected_failures=['SVR4_SVR9'], **kw)
    run_test('T16', 'Port connection SKIPPED (net missing)',
             setup_T16, FAIL, expected_failures=['PORT_SKIP'], **kw)

    # Summary
    ran     = [r for r in RESULTS if args.filter is None or args.filter in r[0]]
    passed  = sum(1 for _, ok, _ in ran if ok)
    total   = len(ran)
    failed  = [(n, d) for n, ok, d in ran if not ok]

    print('\n' + '='*65)
    print(f'  Results: {passed}/{total} passed')
    if failed:
        print('\n  Failed tests:')
        for name, detail in failed:
            print(f'    {name}: {detail}')
    print('='*65 + '\n')
    sys.exit(0 if not failed else 1)


if __name__ == '__main__':
    main()
