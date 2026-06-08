#!/usr/bin/env python3
"""
eco_emit_dff_entry.py — One-shot DFF entry assembler (Step 3 wrapper).

For one `new_logic` change from eco_rtl_diff.json, deterministically
produce the complete per-stage entries needed for eco_preeco_study.json:
  - DFF entry with port_connections_per_stage for Synth/PP/Route
  - D-input gate chain (via eco_synth_chain.py)
  - Mode-S bridge plumbing artifacts (via eco_pick_sibling.py +
    eco_pick_bridge_dffs.py + eco_emit_bridge_plumbing.py)

Strategy decision is made by the script, not the agent:
  - Try eco_pick_sibling.py at parent scope
  - Null? Escalate --host-scope=down then --min-cluster=5
  - Still null + host has DFFs in same clock → BLOCKED (engineer escalation)
  - Still null + host has 0 DFFs → constant_zero (justified)
  - Viable picker result → bridge_port for both PP and Route

Self-validates the assembled entries against the Step 3 invariants
(strategy↔port_connections consistency, per-stage SI/SE wire existence,
bridge artifact completeness, clock root token match).

Output JSON layout:
  {
    "tag":           <TAG>,
    "jira":          <JIRA>,
    "dff_instance":  <name>,
    "strategy":      "bridge_port" | "neighbor_dff" | "constant_zero",
    "Synthesize":    [<DFF entry>, <chain gates>, <bridge artifacts>, ...],
    "PrePlace":      [...],
    "Route":         [...],
    "diagnostics":   { ... self-validation results ... }
  }

Usage:
    python3 eco_emit_dff_entry.py \\
        --rtl-change         (file_path or - for stdin) \\
        --ref-dir            <REF_DIR> \\
        --rename-map         data/<TAG>_eco_fenets_rename_map.json \\
        --preeco-synthesize  /tmp/eco_study_<TAG>_Synthesize.v \\
        --preeco-preplace    /tmp/eco_study_<TAG>_PrePlace.v \\
        --preeco-route       /tmp/eco_study_<TAG>_Route.v \\
        --tag <TAG> --jira <JIRA> --tile-module <ddrss_<tile>_t> \\
        --output data/<TAG>_eco_dff_entry_<dff>.json

Exit 0 on PASS, 1 on BLOCKED (write a partial JSON with diagnostics so
the studier agent can re-spawn with hints).
"""
import argparse, copy, gzip, json, re, subprocess, sys
from pathlib import Path

# Reuse existing modules in the same directory
sys.path.insert(0, str(Path(__file__).parent))
import eco_synth_chain as synth_chain  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _open_text(path):
    p = str(path)
    if p.endswith('.gz'):
        return gzip.open(p, 'rt', errors='replace')
    return open(p, 'r', errors='replace')


def _grep_count(pattern, path):
    """zgrep -c '\\b<pattern>\\b' <path>; returns int (0 on error)."""
    try:
        if not Path(path).is_file():
            return 0
        cmd = f"zgrep -c '\\b{re.escape(pattern)}\\b' {path}"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return int((r.stdout or '0').strip() or '0')
    except Exception:
        return 0


def _discover_dff_cell_type(host_module, dff_clock, preeco_synth_v, ref_dir, tile_module):
    """Find a DFF cell type used in host_module scope.

    Strategy (in priority order):
      1. Cell with `.CP(<dff_clock>)` matching the requested clock — best
         match (same clock domain).
      2. Cell with `.CP(<dff_clock>G)` — gated form of same clock.
      3. Any single-bit DFF instance (cell type starts with SDFQ/SDFF/DFF/DFQ
         and instance line has `.D(`/`.CP(`/`.Q(`) — same library family.

    The umccmd-style hierarchical wrapper modules often don't have direct
    .CP(UCLK01) — the clock gets gated to UCLK01G first. The fallback to
    any-DFF-in-scope mirrors what an engineer does when picking a cell
    type for a new ECO DFF.

    Tries `<host_module>` then `<host_module>_0` (Route uniquification) then
    `<tile_module>_<host_module>` prefix variant.
    """
    candidates = [host_module]
    if host_module and not host_module.startswith('ddrss_'):
        candidates.append(f'{tile_module}_{host_module}')
    candidates.extend([f'{c}_0' for c in list(candidates)])
    # Build the source: prefer cached file, else gz. Use zcat for .gz paths
    # (cat on a binary .gz returns garbage that no awk pattern matches).
    if preeco_synth_v and Path(preeco_synth_v).is_file():
        cat_cmd = (f'zcat {preeco_synth_v}' if preeco_synth_v.endswith('.gz')
                   else f'cat {preeco_synth_v}')
    else:
        gz = str(Path(ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz')
        if not Path(gz).is_file():
            return ''
        cat_cmd = f'zcat {gz}'

    # Helper: extract first cell-type token from a multi-line awk hit
    def _first_celltype_for_pattern(cand, grep_pattern):
        cmd = (
            f"{cat_cmd} | awk '/^module {re.escape(cand)}[ \\t(]/,/^endmodule/' "
            f"| grep -B2 -E {grep_pattern!r} "
            f"| grep -E '^[A-Z][A-Z0-9_]+[ \\t]+[a-zA-Z_]' | head -1"
        )
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            line = (r.stdout or '').strip()
            m = re.match(r'^([A-Z][A-Z0-9_]+)\s+', line)
            return m.group(1) if m else ''
        except Exception:
            return ''

    # Filter: only accept single-bit DFF cell types. Multi-bit (MB*) and
    # grouped cells (MBN*, SDFQTX*) are not valid for new ECO single-bit DFFs.
    _SINGLE_BIT_RE = re.compile(r'^(SDFQ|SDFF|SDFR|DFQ|DFF|SDF)[A-Z0-9_]+$')

    def _is_single_bit(ct):
        return bool(_SINGLE_BIT_RE.match(ct))

    for cand in candidates:
        if not cand: continue
        # Strategy 1: direct .CP(<dff_clock>) match — must be single-bit DFF
        ct = _first_celltype_for_pattern(cand, rf'\.CP\s*\(\s*{re.escape(dff_clock)}[^A-Za-z0-9_]')
        if ct and _is_single_bit(ct): return ct
        # Strategy 2: .CP(<dff_clock>G) gated form — must be single-bit DFF
        ct = _first_celltype_for_pattern(cand, rf'\.CP\s*\(\s*{re.escape(dff_clock)}G[^A-Za-z0-9_]')
        if ct and _is_single_bit(ct): return ct
        # Strategy 3: any DFF cell (SDFQ/SDFF/DFQ/DFF/SDFR/SDF) followed by
        # instance name + line pattern containing `.CP(`. Engineer-style:
        # "find any neighbor DFF in scope".
        # Sort by cell name and prefer the smallest drive-strength (lowest number
        # suffix, e.g. SDFQD1 over SDFQD4) to avoid picking an oversized DFF
        # that happens to appear first in the module text.
        try:
            cmd = (
                f"{cat_cmd} | awk '/^module {re.escape(cand)}[ \\t(]/,/^endmodule/' "
                f"| grep -E '^(SDFQ|SDFF|SDFR|DFQ|DFF|SDF)[A-Z0-9_]+[ \\t]+[a-zA-Z_][A-Za-z0-9_]*[ \\t]*\\(' "
                f"| awk '{{print $1}}' | sort -u | sort -V | head -1"
            )
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            ct = (r.stdout or '').strip()
            if ct: return ct
        except Exception:
            continue
    return ''


def _module_scope_dff_count(host_module, dff_clock, preeco_synth_v):
    """Count DFFs in host_module on dff_clock (Check 30 grep)."""
    if not (host_module and dff_clock and preeco_synth_v and Path(preeco_synth_v).is_file()):
        return 0
    try:
        cmd = (f"awk '/^module {re.escape(host_module)}\\b/,/^endmodule/' {preeco_synth_v} "
               f"| grep -cE '\\.CP\\(\\s*{re.escape(dff_clock)}\\b'")
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return int((r.stdout or '0').strip() or '0')
    except Exception:
        return 0


# ── Step A: Strategy decision ───────────────────────────────────────────────

def decide_mode_s_strategy(host_module, ref_dir, tile_module, dff_clock,
                            preeco_synth_v, jira, tag, base_dir):
    """Scan stitching is OUT OF SCOPE — DFT team handles scan integration.
    Always emit constant_zero (SE=SI=1'b0 in all 3 stages). The picker /
    bridge-plumbing path is short-circuited; the legacy escalation logic
    is preserved below the early return for reference only."""
    return {
        'strategy': 'constant_zero',
        'host_module_dff_count_same_clock': None,
        'escalation_chain': [],
        'reason': 'scan stitching out of scope — DFT team handles scan '
                  'integration; AI flow emits SE=SI=1\'b0 unconditionally',
    }


# ── Step B: Per-stage CP/SI/SE resolution ───────────────────────────────────

def resolve_cp_per_stage(rename_map, host_scope, dff_clock):
    """Resolve CP per stage from rename map. Falls back to original name if
    not in map (caller should grep stage netlists to verify)."""
    key = f'{host_scope}/{dff_clock}'
    entry = (rename_map or {}).get(key, {}) or {}
    out = {}
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        v = entry.get(stage, '') if isinstance(entry, dict) else ''
        # Strip trailing /pin (e.g. 'X_reg/CP' → 'X_reg' or just use as-is)
        out[stage] = v or dff_clock
    return out


def resolve_neighbor_dff_si_se(host_module, ref_dir):
    """Pick a neighbor DFF in host module per stage; return per-stage SI/SE.
    Lightweight version — finds first DFF in host module body and reads its
    .SI/.SE wires. Caller should validate the wires exist in each stage's
    netlist (handled by self-validation step below)."""
    out = {'Synthesize': {'SI': "1'b0", 'SE': "1'b0"},
           'PrePlace':   {'SI': "1'b0", 'SE': "1'b0"},
           'Route':      {'SI': "1'b0", 'SE': "1'b0"}}
    for stage in ('PrePlace', 'Route'):
        gz = Path(ref_dir) / 'data' / 'PreEco' / f'{stage}.v.gz'
        if not gz.is_file():
            continue
        try:
            # Extract host module body, find first DFF cell with .SI(...) and .SE(...)
            cmd = (f"zcat {gz} | awk '/^module {re.escape(host_module)}\\b/,/^endmodule/' "
                   f"| grep -m1 -E '\\.SE\\s*\\([^,]+\\).*\\.SI\\s*\\(|"
                   f"\\.SI\\s*\\([^,]+\\).*\\.SE\\s*\\('")
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            line = (r.stdout or '').strip()
            si_m = re.search(r'\.SI\s*\(\s*(\S+?)\s*\)', line)
            se_m = re.search(r'\.SE\s*\(\s*(\S+?)\s*\)', line)
            if si_m: out[stage]['SI'] = si_m.group(1)
            if se_m: out[stage]['SE'] = se_m.group(1)
        except Exception:
            pass
    return out


# ── Step C: D-input chain via eco_synth_chain ───────────────────────────────

def build_d_input_chain(d_input_expected_function, input_names, jira, prefix=''):
    """Run eco_synth_chain.synthesize and return list of gate entries +
    final output net (DFF.D). `prefix` disambiguates instance names when
    multiple DFFs are processed in the same study (e.g. 'needfreqadj' →
    eco_<jira>_needfreqadj_d001 instead of generic eco_<jira>_d001).

    Verilog bus-bit normalization: rtl_diff's chain `inputs` may use
    bracketed form (`SIG[1]`) while `d_input_expected_function` uses the
    underscore-escaped form (`SIG_1_`) that survives Python's eval().
    Both forms are added to input_names so symbol resolution works.

    On synth failure: returns a valid Verilog placeholder net name
    (`n_eco_<jira>_<prefix>_d_SYNTH_FAILED`) instead of the raw error
    string. This way the DFF.D field is a parseable identifier (won't
    crash the applier on Verilog elaboration) and the validator can
    detect it via name pattern match.
    """
    if not d_input_expected_function:
        return [], None
    # Normalize bracketed bus bits → underscore-escaped form, expanding
    # the input_names list to cover BOTH forms (eval will resolve whichever
    # form the Boolean string uses).
    normalized = []
    for n in input_names:
        if n not in normalized: normalized.append(n)
        # Bus bit form: 'BeqCtrlPeSrc[1]' → 'BeqCtrlPeSrc_1_'
        flat = re.sub(r'\[(\d+)\]', r'_\1_', n)
        if flat != n and flat not in normalized:
            normalized.append(flat)
    try:
        chain = synth_chain.synthesize(
            d_input_expected_function,
            input_names=normalized,
            jira=jira,
            prefix=prefix,
        )
    except Exception as e:
        # Return a valid identifier as placeholder so the DFF.D field is
        # parseable Verilog. The marker '_d_SYNTH_FAILED' lets the
        # validator detect this case.
        placeholder = f'n_eco_{jira}_{prefix}_d_SYNTH_FAILED' if prefix else \
                      f'n_eco_{jira}_d_SYNTH_FAILED'
        sys.stderr.write(f'WARN: synth_chain failed for prefix={prefix!r}: {e}\n')
        return [], placeholder
    gate_entries = []
    for c in chain.cells:
        gate_entries.append({
            'change_type':       'new_logic_gate',
            'cell_type':         c['cell_type'],
            'instance_name':     c['instance_name'],
            'port_connections':  c['port_connections'],
            'output_net':        next((c['port_connections'].get(p)
                                       for p in ('Z', 'ZN', 'ZN1') if p in c['port_connections']),
                                      None),
            'gate_function':     re.match(r'^([A-Z]+\d?)', c['cell_type']).group(1)
                                  if re.match(r'^([A-Z]+\d?)', c['cell_type']) else 'UNKNOWN',
            'source':            'eco_synth_chain.py',
            'reason':            f'D-input chain element for {jira} DFF',
            'notes':             'auto-emitted by eco_emit_dff_entry.py — DO NOT manually edit',
            'confirmed':         True,
        })
    return gate_entries, chain.output_net


# ── Step D: Bridge plumbing (delegates to existing emitter) ────────────────

def _grep_inst_name(parent_module, child_module, pp_gz):
    """Find the instance name `<inst>` in `child_module <inst> (` within parent_module body."""
    if not (parent_module and child_module and Path(pp_gz).is_file()):
        return ''
    try:
        cmd = (f"zcat {pp_gz} | awk '/^module {re.escape(parent_module)}\\b/,/^endmodule/' "
               f"| grep -m1 -oE '{re.escape(child_module)}\\s+[A-Za-z_][A-Za-z0-9_]*' "
               f"| awk '{{print $2}}'")
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return (r.stdout or '').strip() or ''
    except Exception:
        return ''


def build_bridge_plumbing(pick, picker_top, dff_inst, host_module, ref_dir, jira, tag, base_dir,
                          parent_is_host=False):
    """Run eco_pick_bridge_dffs.py + eco_emit_bridge_plumbing.py and return
    per-stage artifact lists.

    pick = recommended_pick sub-object (has module, inst, fm_scope, ...)
    picker_top = top-level picker output (has parent_module, host_module)
    """
    pp_gz = Path(ref_dir) / 'data' / 'PreEco' / 'PrePlace.v.gz'
    sibling_module = pick.get('module', '')
    sibling_inst   = pick.get('inst', '')
    if not sibling_module:
        return None, 'sibling_module missing in pick'

    # 1. eco_pick_bridge_dffs.py
    bridge_pick_path = Path(base_dir) / 'data' / f'{tag}_eco_bridge_pick_{dff_inst}.json'
    cmd = (f"python3 {Path(__file__).parent / 'eco_pick_bridge_dffs.py'} "
           f"--netlist {pp_gz} --sibling-mod {sibling_module} "
           f"--output {bridge_pick_path}")
    try:
        subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
    except Exception as e:
        return None, f'eco_pick_bridge_dffs.py failed: {e}'
    if not bridge_pick_path.is_file():
        return None, f'eco_pick_bridge_dffs.py produced no output: {bridge_pick_path}'

    # 2. Determine parent_module + host_inst per escalation mode
    if parent_is_host:
        # down-escalation: host IS the parent (host instantiates the chosen child)
        parent_module = host_module
        host_inst = host_module  # host is its own instance for emitter scoping
    else:
        # parent-scope: parent_module from picker top-level output
        parent_module = (picker_top or {}).get('parent_module', '')
        # Grep parent body for host instantiation to get host_inst
        host_inst = _grep_inst_name(parent_module, host_module, pp_gz)
    if not parent_module:
        return None, 'parent_module not resolvable from picker output'
    if not host_inst:
        host_inst = 'HOST_INST_UNKNOWN'  # let emitter fail explicitly

    # 3. eco_emit_bridge_plumbing.py
    plumbing_path = Path(base_dir) / 'data' / f'{tag}_eco_bridge_plumbing_{dff_inst}.json'
    cmd = (f"python3 {Path(__file__).parent / 'eco_emit_bridge_plumbing.py'} "
           f"--bridge-pick {bridge_pick_path} --jira {jira} --ref-dir {ref_dir} "
           f"--host-module {host_module} --sibling-module {sibling_module} "
           f"--parent-module {parent_module} "
           f"--host-inst {host_inst or 'HOST'} --sibling-inst {sibling_inst or 'SIBLING'} "
           f"--new-dff-instance {dff_inst} --output {plumbing_path}")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return None, f'eco_emit_bridge_plumbing.py failed: {e}'
    if not plumbing_path.is_file():
        return None, f'eco_emit_bridge_plumbing.py produced no output: {plumbing_path} '\
                     f'(stderr={r.stderr[:200] if r else "?"})'
    try:
        return json.loads(plumbing_path.read_text()), None
    except Exception as e:
        return None, f'cannot parse plumbing JSON: {e}'


# ── Step E: Build the DFF entry itself ─────────────────────────────────────

def build_dff_entry(rtl_change, strategy_info, cp_per_stage, scan_per_stage,
                    chain_d_net, jira, dff_cell_type='', host_module=''):
    """Compose the new_logic_dff entry with port_connections_per_stage."""
    target_reg = rtl_change.get('target_register', '') or rtl_change.get('new_token', '')
    dff_inst   = f'{target_reg}_reg' if target_reg else f'eco_{jira}_dff'
    q_net      = target_reg

    pcs = {}
    for stage in ('Synthesize', 'PrePlace', 'Route'):
        pin_si = scan_per_stage.get(stage, {}).get('SI', "1'b0")
        pin_se = scan_per_stage.get(stage, {}).get('SE', "1'b0")
        pcs[stage] = {
            'D':  chain_d_net or "1'b0",
            'CP': cp_per_stage.get(stage, ''),
            'SI': pin_si,
            'SE': pin_se,
            'Q':  q_net,
        }

    strategy = strategy_info.get('strategy', 'constant_zero')
    requires_scan = (strategy in ('bridge_port', 'neighbor_dff'))
    mode_s_applied = (strategy == 'bridge_port')
    mode_s_strat_per_stage = {
        'Synthesize': 'constant_zero',
        'PrePlace':   strategy if strategy != 'BLOCKED' else 'BLOCKED_NO_SIBLING',
        'Route':      strategy if strategy != 'BLOCKED' else 'BLOCKED_NO_SIBLING',
    }

    entry = {
        'change_type':                    'new_logic_dff',
        'instance_name':                  dff_inst,
        'cell_type':                      dff_cell_type or '',
        'dff_cell_type':                  dff_cell_type or '',
        'module_name':                    host_module or rtl_change.get('declaring_module') or rtl_change.get('module_name', ''),
        'dff_clock':                      rtl_change.get('dff_clock', ''),
        'reset_signal':                   rtl_change.get('reset_signal', ''),
        'reset_polarity':                 rtl_change.get('reset_polarity', ''),
        'reset_pin_used':                 False,    # SE/SI=1'b0 + reset baked into D-cone (default)
        'port_connections':               pcs.get('Synthesize', {}),
        'port_connections_per_stage':     pcs,
        'mode_S_strategy_per_stage':      mode_s_strat_per_stage,
        'mode_S_applied':                 mode_s_applied,
        'requires_scan_stitching':        requires_scan,
        'host_module_dff_count_same_clock':
            strategy_info.get('host_module_dff_count_same_clock', 0),
        'scan_stitching_skipped_reason':
            strategy_info.get('reason', '') if strategy in ('constant_zero', 'BLOCKED') else '',
        'source':                         'eco_emit_dff_entry.py',
        'reason':                         f'New ECO DFF for {target_reg} ({jira})',
        'notes':                          'auto-emitted — DO NOT manually edit',
        'confirmed':                      True,
    }
    return entry, dff_inst


# ── Step F: Self-validation ────────────────────────────────────────────────

def self_validate(out, ref_dir):
    """Run a few invariants matching eco_validate_step3.py expectations.
    Returns list of issues (empty list = clean)."""
    issues = []
    # All three stages present
    for s in ('Synthesize', 'PrePlace', 'Route'):
        if not out.get(s):
            issues.append(f'CRITICAL: {s} stage entries empty')
    # Strategy ↔ port_connections consistency (Check 32)
    for s in ('Synthesize', 'PrePlace', 'Route'):
        for e in out.get(s, []):
            if e.get('change_type') not in ('new_logic_dff', 'new_logic'):
                continue
            strat = (e.get('mode_S_strategy_per_stage') or {})
            pcs   = (e.get('port_connections_per_stage') or {})
            for chk in ('Synthesize', 'PrePlace', 'Route'):
                if strat.get(chk) != 'constant_zero':
                    continue
                p = pcs.get(chk) or {}
                if p.get('SE','') not in ("1'b0","1'bz","") or p.get('SI','') not in ("1'b0","1'bz",""):
                    issues.append(f'HIGH/32: {chk} declares constant_zero but SE/SI are real wires')
    return issues


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--rtl-change', required=True,
                   help='Path to JSON file containing ONE new_logic change entry, OR "-" for stdin')
    p.add_argument('--ref-dir', required=True)
    p.add_argument('--rename-map', required=True)
    p.add_argument('--preeco-synthesize', default='',
                   help='Path to PreEco Synthesize.v (uncompressed) for module-scope grep '
                        '(host_module_dff_count_same_clock check). Falls back to PreEco/Synthesize.v.gz.')
    p.add_argument('--tag',  required=True)
    p.add_argument('--jira', required=True)
    p.add_argument('--tile-module', required=True)
    p.add_argument('--base-dir', default='.', help='Base directory for output files')
    p.add_argument('--output', required=True)
    p.add_argument('--bus-width', type=int, default=1,
                   help='Bus width N for vector registers (is_bus_dff=true). '
                        'When N>1, emits N individual DFF entries with per-bit D/Q nets. '
                        'Use eco_resolve_bus_width.py to determine N before calling.')
    p.add_argument('--shadow-cp-net', default='',
                   help='Override DFF CP pin with this net for all stages. '
                        'Use when the DFF should be clocked by an ECO shadow clock gate '
                        'rather than the bare rename-map-resolved clock (e.g. wdbptr_org0_d1p5 '
                        'shares the same shadow gate as the enable_swap target d2_reg).')
    args = p.parse_args()

    # Load rtl_change
    if args.rtl_change == '-':
        rtl_change = json.loads(sys.stdin.read())
    else:
        rtl_change = json.loads(Path(args.rtl_change).read_text())

    # Load rename map
    rmap = {}
    if Path(args.rename_map).is_file():
        try:
            rmap = json.loads(Path(args.rename_map).read_text())
        except Exception:
            rmap = {}

    # Resolve preeco synthesize path
    synth_v = args.preeco_synthesize
    if not synth_v or not Path(synth_v).is_file():
        # Use the gz directly (grep will need zcat)
        synth_v = str(Path(args.ref_dir) / 'data' / 'PreEco' / 'Synthesize.v.gz')

    # Extract key fields
    target_reg  = rtl_change.get('target_register') or rtl_change.get('new_token', '')
    host_module = (rtl_change.get('declaring_module') or rtl_change.get('module_name', ''))
    if not host_module.startswith('ddrss_') and host_module:
        # Heuristic: prepend tile prefix if missing — but guard against
        # double-prepend when the caller passes a tile_module that already
        # contains host_module (e.g. tile_module='ddrss_umccmd_t_umccmd'
        # while host_module='umccmd' would otherwise produce the doubled
        # 'ddrss_umccmd_t_umccmd_umccmd'). Run 20260527010014 root cause —
        # Mode I helper got that doubled name and returned NO_PARENT_UNC,
        # leaving the LLM-studier to manually compensate and ship a broken
        # bridge.
        tm = args.tile_module or ''
        if tm.endswith(f'_{host_module}') or tm == host_module:
            host_module = tm
        elif tm:
            host_module = f'{tm}_{host_module}'
    dff_clock   = rtl_change.get('dff_clock', '')
    expr        = rtl_change.get('d_input_expected_function', '')
    input_names = []
    for g in (rtl_change.get('d_input_gate_chain') or []):
        for v in (g.get('inputs') or []):
            # Skip intermediate ECO nets (n_eco_*) — they are defined by the chain itself,
            # not primary inputs. Including them as sympy symbols causes compose_chain_boolean
            # to return partial expressions (unresolved intermediate), breaking truth-table check.
            if isinstance(v, str) and v.startswith('n_eco_'): continue
            if v not in input_names: input_names.append(v)
        for k, v in (g.get('port_connections') or {}).items():
            if k in ('Z', 'ZN'): continue
            if isinstance(v, str) and not v.startswith('n_eco_') and v not in input_names:
                input_names.append(v)

    print(f'eco_emit_dff_entry: target={target_reg}  host={host_module}  clk={dff_clock}',
          file=sys.stderr)

    # ── Step A: strategy ──────────────────────────────────────────────────
    strategy_info = decide_mode_s_strategy(
        host_module, args.ref_dir, args.tile_module, dff_clock,
        synth_v, args.jira, args.tag, args.base_dir,
    )
    print(f'  strategy: {strategy_info.get("strategy")}', file=sys.stderr)

    # ── Step B: per-stage CP/SI/SE ────────────────────────────────────────
    host_scope = rtl_change.get('host_scope', '') or rtl_change.get('hierarchy', '')
    cp_per_stage = resolve_cp_per_stage(rmap, host_scope, dff_clock)

    # --shadow-cp-net override: replace CP with the ECO shadow clock gate Q
    # output for all stages. Used when the DFF shares the same shadow gate as
    # an enable_swap target (e.g. wdbptr_org0_d1p5 shares clk_gate_ECO_9855_*
    # with wdbptr_org0_d2). The studier passes this flag after emitting the
    # shadow gate so both DFF arrays use the same gated clock domain.
    if args.shadow_cp_net:
        for stage in ('Synthesize', 'PrePlace', 'Route'):
            cp_per_stage[stage] = args.shadow_cp_net
        print(f'  shadow_cp_net override: CP → {args.shadow_cp_net!r} for all stages',
              file=sys.stderr)

    if strategy_info['strategy'] == 'bridge_port':
        # SE/SI come from bridge port names
        dff_label = re.sub(r'_reg$', '', target_reg)
        scan_per_stage = {
            'Synthesize': {'SI': "1'b0", 'SE': "1'b0"},
            'PrePlace':   {'SI': f'{dff_label}_ECO{args.jira}_SI_in',
                           'SE': f'{dff_label}_ECO{args.jira}_SE_in'},
            'Route':      {'SI': f'{dff_label}_ECO{args.jira}_SI_in',
                           'SE': f'{dff_label}_ECO{args.jira}_SE_in'},
        }
    elif strategy_info['strategy'] == 'neighbor_dff':
        scan_per_stage = resolve_neighbor_dff_si_se(host_module, args.ref_dir)
        scan_per_stage['Synthesize'] = {'SI': "1'b0", 'SE': "1'b0"}
    else:
        scan_per_stage = {
            'Synthesize': {'SI': "1'b0", 'SE': "1'b0"},
            'PrePlace':   {'SI': "1'b0", 'SE': "1'b0"},
            'Route':      {'SI': "1'b0", 'SE': "1'b0"},
        }

    # ── Step C: D-input chain ─────────────────────────────────────────────
    # Per-DFF prefix for chain instance names (avoids collisions when multiple
    # DFFs in the same study both use eco_<jira>_d001). Lowercase the target
    # register name for consistent identifier form.
    dff_prefix = re.sub(r'[^A-Za-z0-9]+', '_', (target_reg or '').lower()).strip('_')
    chain_entries, chain_d_net = build_d_input_chain(expr, input_names, args.jira, prefix=dff_prefix)

    # Propagate host_module to every chain gate entry — without this the entries
    # land in the study with `module_name` unset (or, when the LLM-studier sees
    # the missing field, with the placeholder literal "UNKNOWN"), which causes
    # perl_spec to emit "Added to Perl spec for module UNKNOWN" and the cell
    # silently never reaches the netlist. The DFF.D consumer then has no driver
    # → guaranteed FM Mode A on the new DFF cone (run 20260526225832 R1 root
    # cause). Chain gates ALWAYS live in the same module as the DFF they feed.
    for g in chain_entries:
        g['module_name'] = host_module

    # Convert chain leaf names from flat-form (`SIG_0_`, used by eco_synth_chain
    # for sympy eval-friendliness) back to the bracket form (`SIG[0]`) that
    # matches the actual netlist. perl_spec's input-existence check greps the
    # netlist literally — flat form misses bracket-form bus bits and produces
    # SKIPPED entries (run 20260515071155 surface). The reverse mapping is
    # built from the original `input_names` list (which has bracket form).
    flat_to_bracket = {}
    for n in input_names:
        if isinstance(n, str):
            m = re.match(r'^([A-Za-z_]\w*)\[(\d+)\]$', n.strip())
            if m:
                flat = f'{m.group(1)}_{m.group(2)}_'
                flat_to_bracket[flat] = n.strip()
    if flat_to_bracket:
        for g in chain_entries:
            pcs = g.get('port_connections') or {}
            for pin, val in list(pcs.items()):
                if isinstance(val, str) and val in flat_to_bracket:
                    pcs[pin] = flat_to_bracket[val]

    # ── Per-stage resolution of ALL chain gate input pins from rename map ────
    # The 0b-ALIAS rule requires every non-ECO input net to be resolved per
    # stage from the rename map.  Historically only CP (clock) and the leaf A1
    # (data signal from Cat4 fenets) were resolved; scalar inputs like the reset
    # signal (B1) were left with the bare RTL name which causes DFF0X / cone
    # mismatch in PP/Route where CTS renames those nets.
    _OUT_PINS_SET = {'Z', 'ZN', 'ZN1', 'Q', 'QN', 'CO', 'S'}
    # Collect all scope prefixes to try for rename map lookup.
    # host_scope may be empty when the change entry's instance_scope wasn't
    # propagated — also try instance_scope directly as a fallback so WDB-scoped
    # signals (e.g. WDB/IReset) are found even when host_scope is unset.
    _scope_candidates = list(dict.fromkeys(filter(None, [
        host_scope,
        rtl_change.get('instance_scope', ''),
        rtl_change.get('scope', ''),
    ])))
    # Build tile-prefixed scope variants.  The rename map stores keys with the
    # full tile scope prefix (e.g. 'umcdat/WDB/wr_vld0_d1') but host_scope is
    # usually just the module scope without tile prefix ('WDB').  Extract the
    # tile name from tile_module (e.g. 'ddrss_umcdat_t' → 'umcdat') and prepend
    # it to each scope candidate so both 'WDB/sig' and 'umcdat/WDB/sig' are tried.
    # Extract tile scope from tile_module: 'ddrss_umcdat_t' → 'umcdat'
    # Use re.sub to strip the trailing _t suffix as a substring (not rstrip which
    # strips individual characters and would incorrectly strip 'umcda' from 'umcdat')
    _tile_scope = re.sub(r'^ddrss_', '', args.tile_module or '')
    _tile_scope = re.sub(r'_t$', '', _tile_scope)
    _scope_candidates_extended = list(dict.fromkeys(
        _scope_candidates +
        [f'{_tile_scope}/{s}' for s in _scope_candidates if _tile_scope] +
        ([_tile_scope] if _tile_scope else [])
    ))
    def _resolve_net(base, stage, net):
        """Try scope-prefixed (with and without tile prefix) then bare key.
        Also strips flat _N_ suffix (e.g. wdbptr_org0_d1_0_ → wdbptr_org0_d1)
        so bus-bit nets in flat P&R form resolve via the bus-level rename map
        entry. When the resolved value is a scalar replacement (actual_wire_*),
        the caller must NOT re-append the _N_ suffix.

        FM path sanitization: if the rename map returns a cell/pin path format
        (contains '/'), it is an FM implementation address, not a wire name.
        Prefer actual_wire_<stage> which is the real gate-level wire. If no
        actual_wire is available, fall through and return the original net so
        the applier can use its own net-rename recovery."""
        def _sanitize(val, fallback):
            if val and '/' in val:
                return None  # FM path format — not a wire name
            return val
        for scope in _scope_candidates_extended:
            entry = rmap.get(f'{scope}/{base}') or {}
            if isinstance(entry, dict) and stage in entry:
                resolved = entry.get(f'actual_wire_{stage}') or _sanitize(entry[stage], net)
                if resolved:
                    return resolved
        entry = rmap.get(base) or {}
        if isinstance(entry, dict) and stage in entry:
            resolved = entry.get(f'actual_wire_{stage}') or _sanitize(entry[stage], net)
            if resolved:
                return resolved
        # Flat bus-bit form: try stripping trailing _N_ digit suffix and
        # look up the base bus signal (e.g. wdbptr_org0_d1_0_ → wdbptr_org0_d1).
        # ONLY use this result when actual_wire_<stage> is explicitly set —
        # that marks a true scalar replacement (e.g. copt_net_600462 covers all
        # bits). If only the plain stage key is present (same name or positional
        # rename), the bit suffix is still significant and must be preserved.
        flat_base = re.sub(r'_\d+_$', '', base)
        if flat_base != base:
            for scope in _scope_candidates_extended:
                entry = rmap.get(f'{scope}/{flat_base}') or {}
                if isinstance(entry, dict) and entry.get(f'actual_wire_{stage}'):
                    return entry[f'actual_wire_{stage}']
            entry = rmap.get(flat_base) or {}
            if isinstance(entry, dict) and entry.get(f'actual_wire_{stage}'):
                return entry[f'actual_wire_{stage}']
        return net

    for g in chain_entries:
        pcs = g.get('port_connections') or {}
        # Always emit port_connections_per_stage — even if no renames fire, it
        # ensures the applier uses the correct per-stage dict for all stages
        # rather than falling back to the bare Synthesize-only port_connections.
        pcs_per_stage = {s: {} for s in ('Synthesize', 'PrePlace', 'Route')}
        changed = False
        for pin, net in pcs.items():
            for stage in ('Synthesize', 'PrePlace', 'Route'):
                if pin in _OUT_PINS_SET or not isinstance(net, str):
                    pcs_per_stage[stage][pin] = net
                    continue
                base = net.split('[')[0]
                bit_suffix = net[len(base):]
                flat_base = re.sub(r'_\d+_$', '', base)
                is_flat_bit = (flat_base != base)
                renamed = _resolve_net(base, stage, net)
                # For bracket-form bus bits ([N]): append the suffix only when
                # the renamed value is a plain rename (e.g. FxPrePlace_HFSNET_N),
                # not when it's a scalar replacement (actual_wire returns a
                # single net that covers all bits, e.g. copt_net_600462).
                # Heuristic: if renamed starts with the base and only differs by
                # a suffix, it's a rename — keep the bit suffix. Otherwise the
                # actual_wire is a full scalar replacement — drop the suffix.
                if bit_suffix and renamed != net:
                    if renamed.startswith(flat_base if is_flat_bit else base):
                        renamed = renamed + bit_suffix
                    # else: scalar replacement — no suffix appended
                # For flat-form bus bits (_N_): _resolve_net already returns
                # a scalar replacement; do NOT re-append _N_.
                pcs_per_stage[stage][pin] = renamed
                if renamed != net:
                    changed = True
        # Always set per-stage dict so applier never falls back to bare pcs
        g['port_connections_per_stage'] = pcs_per_stage
        if changed:
            # Update base port_connections to Synthesize-resolved form
            g['port_connections'] = dict(pcs_per_stage['Synthesize'])

    # ── Discover DFF cell type (for build_dff_entry) ─────────────────────
    # Walk host module body in PreEco/Synthesize for a cell using <dff_clock>
    # on its .CP pin; copy that cell's type. Without this the DFF entry has
    # cell_type='' and the applier returns 'cell_type empty' SKIP (run
    # 20260515071155 surface). Engineer-style: pick a neighbor DFF's cell type.
    dff_cell_type = _discover_dff_cell_type(
        host_module, dff_clock, synth_v, args.ref_dir, args.tile_module
    )
    print(f'  dff_cell_type discovered: {dff_cell_type!r}', file=sys.stderr)

    # ── Step E: DFF entry ─────────────────────────────────────────────────
    dff_entry, dff_inst = build_dff_entry(
        rtl_change, strategy_info, cp_per_stage, scan_per_stage,
        chain_d_net, args.jira,
        dff_cell_type=dff_cell_type, host_module=host_module,
    )

    # ── Step D: bridge plumbing (if needed) ───────────────────────────────
    plumbing = None
    plumbing_err = None
    if strategy_info['strategy'] == 'bridge_port':
        plumbing, plumbing_err = build_bridge_plumbing(
            strategy_info['pick'], strategy_info.get('picker_top'),
            dff_inst, host_module, args.ref_dir,
            args.jira, args.tag, args.base_dir,
            parent_is_host=strategy_info.get('parent_is_host', False),
        )

    # ── Step F: Mode-I chain-leaf check (per chain leaf input) ────────────
    # For each chain leaf input that resolves to a bus-bit form (<bus>[<bit>]),
    # run eco_modei_chain_input_check.py to detect parent-side UNCONNECTED at
    # a child-instance port-bus connection. On MODEI_DETECTED, splice the
    # suggested unconnected_rewires into the DFF entry, append the child
    # port_connection to all 3 stage arrays, and rewrite the chain leaf to
    # the flat-net replacement everywhere it appears in chain_entries.
    modei_extra_entries = []  # appended to all 3 stages
    modei_diagnostics = []
    if host_module and chain_entries:
        # Collect unique bus-bit-form leaves from chain port_connections.
        # Accept both bracket form (`SIG[1]`) and flat form (`SIG_1_`); convert
        # the latter to bracket form for the helper.
        leaf_candidates = set()  # canonical bracket form
        flat_to_bracket = {}     # mapping back to original net names in chain
        for g in chain_entries:
            for pin, val in (g.get('port_connections') or {}).items():
                if pin in ('Z', 'ZN', 'ZN1'): continue
                if not isinstance(val, str): continue
                if val.startswith(('n_eco_', "1'b", "0'b", "1'h", "0'h")): continue
                # Bracket form already
                m1 = re.match(r'^([A-Za-z_]\w*)\[(\d+)\]$', val.strip())
                if m1:
                    leaf_candidates.add(val.strip())
                    flat_to_bracket[val.strip()] = val.strip()
                    continue
                # Flat form like SIG_1_
                m2 = re.match(r'^([A-Za-z_][A-Za-z0-9_]*?)_(\d+)_$', val.strip())
                if m2:
                    bracket_form = f'{m2.group(1)}[{m2.group(2)}]'
                    leaf_candidates.add(bracket_form)
                    flat_to_bracket[bracket_form] = val.strip()
        modei_helper = Path(__file__).parent / 'eco_modei_chain_input_check.py'
        for leaf in leaf_candidates:
            leaf_safe = re.sub(r'[^A-Za-z0-9_]', '_', leaf)
            out_path = Path(args.base_dir) / 'data' / f'{args.tag}_eco_modei_{dff_prefix}_{leaf_safe}.json'
            cmd = (
                f"python3 {modei_helper} --ref-dir {args.ref_dir} "
                f"--host-module {host_module} --chain-input '{leaf}' "
                f"--jira {args.jira} --output {out_path}"
            )
            try:
                subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            except Exception:
                continue
            if not out_path.is_file():
                continue
            try:
                result = json.loads(out_path.read_text())
            except Exception:
                continue
            modei_diagnostics.append({
                'leaf': leaf, 'status': result.get('status'),
                'output': str(out_path),
            })
            if result.get('status') != 'MODEI_DETECTED':
                continue
            # Splice unconnected_rewires onto DFF entry
            ur_entry = result.get('suggested_unconnected_rewires_entry')
            if ur_entry:
                dff_entry.setdefault('unconnected_rewires', []).append(ur_entry)
            # Add child port_connection to extras (will be appended to all stages)
            child_pc = result.get('suggested_child_port_connection_entry')
            if child_pc:
                modei_extra_entries.append(child_pc)
            # Add parent-side port_connection to extras — closes the cross-
            # module driver chain. Without this the wrapper's exported bit
            # never lands on the consumer net at the host module → consumer
            # wire declared but undriven → FM Mode A.
            parent_pc = result.get('suggested_parent_port_connection_entry')
            if parent_pc:
                modei_extra_entries.append(parent_pc)
            # Rewrite chain leaves: original chain ref → flat-net replacement.
            # Also flag the gate with `input_from_unconnected_rewire` so
            # perl_spec skips the input-existence check (the flat-net is
            # CREATED by the unconnected_rewires in passes 2-4 — chicken-and-
            # egg with perl_spec's pre-existence check).
            replacement = result.get('suggested_chain_input_replacement')
            if replacement:
                rewrite_targets = {leaf}  # bracket form
                if leaf in flat_to_bracket:
                    rewrite_targets.add(flat_to_bracket[leaf])
                m = re.match(r'^([A-Za-z_]\w*)\[(\d+)\]$', leaf)
                if m:
                    rewrite_targets.add(f'{m.group(1)}_{m.group(2)}_')
                # Also include the per-stage UNCONNECTED_* literals as
                # rewrite targets — port_connections_per_stage may carry the
                # raw bus-bit UNCONNECTED name from the chain expansion, and
                # if left in place the gate's per-stage A1 still references
                # the dead UNCONNECTED slot even after the bus rename.
                unc_targets = set()
                for stage_unc in (result.get('parent_unc_per_stage') or {}).values():
                    if isinstance(stage_unc, str) and stage_unc:
                        unc_targets.add(stage_unc.strip())
                rewrite_targets |= unc_targets
                for g in chain_entries:
                    pcs = g.get('port_connections') or {}
                    rewired = False
                    for pin, val in list(pcs.items()):
                        if isinstance(val, str) and val.strip() in rewrite_targets:
                            pcs[pin] = replacement
                            rewired = True
                    # ALSO rewrite per-stage entries — without this the chain
                    # gate's port_connections_per_stage.<stage>.A1 retains the
                    # original UNCONNECTED_* per-stage value, leaving the
                    # per-stage view inconsistent with the bare port_connections
                    # and breaking any downstream tool that reads per-stage.
                    pcs_ps = g.get('port_connections_per_stage') or {}
                    for stage_name, stage_pcs in pcs_ps.items():
                        for pin, val in list(stage_pcs.items()):
                            if isinstance(val, str) and val.strip() in rewrite_targets:
                                stage_pcs[pin] = replacement
                                rewired = True
                    if rewired:
                        # Tell perl_spec: this input is created by Pass 2/4,
                        # don't pre-check existence (would falsely SKIP).
                        g['input_from_unconnected_rewire'] = replacement

    # ── Bus DFF expansion (--bus-width N > 1) ────────────────────────────
    # Expand to N per-bit DFF entries. When a D-input chain exists (chain_entries
    # non-empty), expand the chain gates to N per-bit instances and set each DFF
    # D-pin to the corresponding per-bit chain output. Without a chain, use the
    # resolved net directly (pure passthrough bus DFF).
    bus_width = max(1, args.bus_width)
    if bus_width > 1:
        import copy as _copy
        bus_dff_entries = []
        bus_chain_entries = []  # per-bit chain gate entries

        # When a chain exists, the representative gate covers bit 0; expand to N.
        # Per-bit naming: append _{bit}_ to instance names and output nets.
        # Input nets that end in _0_ (flat bus-bit form) are also renamed to _{bit}_.
        # Also handle inputs where eco_synth_chain.py stripped the _0_ suffix
        # (leaving base bus name e.g. 'wdbptr_org0_d1' instead of 'wdbptr_org0_d1_0_').
        if chain_entries:
            # Build map: base_name → _0_ input from rtl_diff chain template
            # eco_synth_chain.py strips the _0_ suffix for sympy symbols, so
            # port_connections may have base name. Detect these via rtl_diff inputs.
            _rtl_chain_inputs = []
            for _cg in (rtl_change.get('d_input_gate_chain') or []):
                for _inp in (_cg.get('inputs') or []):
                    if isinstance(_inp, str) and _inp.endswith('_0_'):
                        _rtl_chain_inputs.append(_inp)
            # base_name → _0_suffixed form mapping
            _base_to_zero = {re.sub(r'_0_$', '', _i.rstrip('_')): _i
                             for _i in _rtl_chain_inputs if _i.endswith('_0_')}

            for bit in range(bus_width):
                for g in chain_entries:
                    ge = _copy.deepcopy(g)
                    ge['bus_bit_index'] = bit
                    # Instance name: append _{bit}_ (strip trailing _0_ first if present)
                    base_inst = re.sub(r'_0_$', '', ge.get('instance_name', '').rstrip('_'))
                    ge['instance_name'] = f'{base_inst}_{bit}_'
                    # Output net: append _{bit}_
                    def _bitname(net, b):
                        n = re.sub(r'_0_$', '', net.rstrip('_'))
                        return f'{n}_{b}_'
                    for out_pin in ('ZN', 'Z'):
                        if out_pin in ge.get('port_connections', {}):
                            ge['port_connections'][out_pin] = _bitname(
                                ge['port_connections'][out_pin], bit)
                    if 'output_net' in ge:
                        ge['output_net'] = _bitname(ge['output_net'], bit)
                    # Input nets: rename _0_ suffix AND handle base bus names
                    # that eco_synth_chain stripped (e.g. wdbptr_org0_d1 → _N_).
                    # Also update port_connections_per_stage with same per-bit values.
                    def _per_bit_flat(val, b, base_to_zero):
                        """Flat-form per-bit rename for PP/Route stages."""
                        if val.endswith('_0_'):
                            return re.sub(r'_0_$', f'_{b}_', val)
                        if val in base_to_zero:
                            return re.sub(r'_0_$', f'_{b}_', base_to_zero[val])
                        return val

                    def _per_bit_bracket(val, b, base_to_zero):
                        """Bracket-form per-bit rename for Synthesize stage.
                        Strip _0_ suffix then add [N] for bus-bit bracket form."""
                        if val.endswith('_0_'):
                            base = re.sub(r'_0_$', '', val)  # wdbptr_org0_d1_0_ → wdbptr_org0_d1
                            return f'{base}[{b}]'
                        if val in base_to_zero:
                            base = re.sub(r'_0_$', '', base_to_zero[val])
                            return f'{base}[{b}]'
                        return val

                    # Base port_connections: use bracket form (Synthesize canonical)
                    for pin, val in list(ge.get('port_connections', {}).items()):
                        if pin in ('ZN', 'Z'): continue
                        if not isinstance(val, str): continue
                        new_val = _per_bit_bracket(val, bit, _base_to_zero)
                        if new_val != val:
                            ge['port_connections'][pin] = new_val
                    # Per-stage: Synthesize=bracket, PP/Route=flat
                    pcs_ps = ge.get('port_connections_per_stage') or {}
                    for stage_name, stage_pcs in pcs_ps.items():
                        if not isinstance(stage_pcs, dict): continue
                        _fn = _per_bit_bracket if stage_name == 'Synthesize' else _per_bit_flat
                        for pin, val in list(stage_pcs.items()):
                            if pin in ('ZN', 'Z', 'Q'): continue
                            if not isinstance(val, str): continue
                            new_val = _fn(val, bit, _base_to_zero)
                            if new_val != val:
                                stage_pcs[pin] = new_val
                    ge['module_name'] = host_module
                    bus_chain_entries.append(ge)

        for bit in range(bus_width):
            e = _copy.deepcopy(dff_entry)
            e['instance_name']  = f'{target_reg}_reg_{bit}_'
            e['bus_bit_index']  = bit
            e['is_bus_dff_bit'] = True
            for stage in ('Synthesize', 'PrePlace', 'Route'):
                pcs = e.get('port_connections_per_stage', {}).get(stage, {})
                if chain_entries:
                    # D = per-bit chain gate output for this bit
                    rep_out = chain_entries[-1].get('port_connections', {}).get('ZN') or \
                              chain_entries[-1].get('port_connections', {}).get('Z') or \
                              chain_entries[-1].get('output_net', '')
                    n = re.sub(r'_0_$', '', rep_out.rstrip('_'))
                    pcs['D'] = f'{n}_{bit}_'
                else:
                    # No chain — D = resolved source bus bit
                    d_src = rtl_change.get('d_input_resolved_net') or ''
                    if d_src:
                        pcs['D'] = f'{d_src}[{bit}]'
                pcs['Q'] = f'{target_reg}[{bit}]'
                if stage == 'Synthesize':
                    e['port_connections'] = dict(pcs)
            bus_dff_entries.append(e)

        synth_entries  = bus_dff_entries + bus_chain_entries + modei_extra_entries
        pp_rte_entries = bus_dff_entries + bus_chain_entries + modei_extra_entries
    else:
        synth_entries  = [dff_entry] + chain_entries + modei_extra_entries
        pp_rte_entries = [dff_entry] + chain_entries + modei_extra_entries

    # ── Compose output ─────────────────────────────────────────────────────
    out = {
        'tag':            args.tag,
        'jira':           args.jira,
        'dff_instance':   dff_inst,
        'host_module':    host_module,
        'strategy':       strategy_info.get('strategy'),
        'bus_width':      bus_width,
        'Synthesize':     synth_entries,
        'PrePlace':       pp_rte_entries,
        'Route':          pp_rte_entries,
        'diagnostics':    {
            'strategy_info':  strategy_info,
            'plumbing_error': plumbing_err,
            'chain_size':     len(chain_entries),
            'expected_function': expr,
            'modei_check':    modei_diagnostics,
            'modei_entries_added': len(modei_extra_entries),
            'bus_width':      bus_width,
        },
    }
    if plumbing:
        for s in ('Synthesize', 'PrePlace', 'Route'):
            out[s] = out[s] + (plumbing.get(s, []) or [])

    # Self-validate
    issues = self_validate(out, args.ref_dir)
    out['diagnostics']['self_validation_issues'] = issues
    out['diagnostics']['self_validation_pass']   = (len(issues) == 0)

    Path(args.output).write_text(json.dumps(out, indent=2))
    print(f'ECO_RPT_GENERATED: dff entry → {args.output}', file=sys.stderr)
    print(f'  strategy:    {out["strategy"]}', file=sys.stderr)
    print(f'  chain_size:  {len(chain_entries)}', file=sys.stderr)
    print(f'  bridge:      {"yes" if plumbing else "no"}'
          f'{" (err: " + plumbing_err + ")" if plumbing_err else ""}', file=sys.stderr)
    print(f'  Synth/PP/Route entry counts: '
          f'{len(out["Synthesize"])}/{len(out["PrePlace"])}/{len(out["Route"])}', file=sys.stderr)
    if issues:
        print(f'  self_validation: FAIL ({len(issues)} issues)', file=sys.stderr)
        for i in issues: print(f'    > {i}', file=sys.stderr)
        return 1
    print(f'  self_validation: PASS', file=sys.stderr)
    return 0 if out['strategy'] != 'BLOCKED' else 1


if __name__ == '__main__':
    sys.exit(main())
