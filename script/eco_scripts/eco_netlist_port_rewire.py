#!/usr/bin/env python3
"""
eco_netlist_port_rewire.py — Apply Passes 2-4 ECO changes to PostEco netlists.

Pass 2: port_declaration  — add signals to module port lists + direction declarations
Pass 3: port_connection   — add .port(net) to submodule instance blocks
Pass 4: rewire            — change pin connections in existing cell instance blocks

Replaces eco_applier Passes 2-4 agent reasoning with deterministic code.

Usage:
    python3 script/eco_scripts/eco_netlist_port_rewire.py \
        --study    data/<TAG>_eco_preeco_study.json \
        --ref-dir  <REF_DIR> \
        --tag      <TAG> \
        --stage    Synthesize|PrePlace|Route \
        --round    <ROUND> \
        --status   data/<TAG>_eco_netlist_port_rewire_<Stage>.json

Exit code: 0 = OK, 1 = any VERIFY_FAILED
"""

import argparse
import gzip
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


# ── File I/O ──────────────────────────────────────────────────────────────────

def read_gz(path):
    with gzip.open(path, 'rt', errors='replace') as f:
        return f.readlines()

_NON_VERILOG_MARKER_RE = re.compile(
    r'^(ECO_PERL_DONE|ECO_DONE|ECO_END|PERL_DONE|ECO_SCRIPT_DONE)\b')

def write_gz(path, lines):
    # Strip non-Verilog marker lines that agent-generated Perl scripts sometimes
    # append (e.g. 'ECO_PERL_DONE: Synthesize'). These cause SVR-4 in FM because
    # they appear at the Verilog top level without a '//' comment prefix.
    clean = [l for l in lines if not _NON_VERILOG_MARKER_RE.match(l.strip())]
    with gzip.open(path, 'wt') as f:
        f.writelines(clean)

def grep_count(pattern, lines):
    return sum(1 for l in lines if re.search(pattern, l))

def grep_lineno(pattern, gz_path, timeout=30):
    """Use zcat+grep to find first matching line number. Fast even on large files."""
    try:
        proc = subprocess.run(
            f'zcat {gz_path} | grep -n {shlex.quote(pattern)} | head -1',
            shell=True, capture_output=True, text=True, timeout=timeout
        )
        m = re.match(r'^(\d+):', proc.stdout.strip())
        return int(m.group(1)) - 1 if m else -1  # 0-indexed
    except Exception:
        return -1

def read_lines_window(gz_path, start_lineno, window=2000, timeout=30):
    """Read a window of lines from gz file starting at start_lineno (0-indexed)."""
    try:
        proc = subprocess.run(
            f'zcat {gz_path} | tail -n +{start_lineno+1} | head -n {window}',
            shell=True, capture_output=True, text=True, timeout=timeout
        )
        return proc.stdout.splitlines(keepends=True)
    except Exception:
        return []


# ── Port list close finder (parenthesis depth tracking) ──────────────────────

def find_port_list_close(lines, mod_start):
    """Find the closing ')' of the module port list. Returns line index or -1."""
    depth = 0
    for i in range(mod_start, min(mod_start + 5000, len(lines))):
        clean = lines[i].split('//')[0]
        for ch in clean:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    # Validate: should not contain .pin( patterns (would be a cell port list)
                    if '.pin(' not in lines[i] and re.search(r'\)', lines[i]):
                        return i
    return -1


# ── Pass 2: port_declaration ─────────────────────────────────────────────────

def apply_port_declaration(lines, entry, stage='Synthesize'):
    """Add signal to module port list + direction declaration. Returns (lines, status, reason).

    GAP-3: stage-aware bridge port plumbing. When the entry is part of a Mode S
    bridge_port strategy (entry.bridge_port_role in {'sibling_si','sibling_se','sibling_q','host_si','host_se','host_q'}),
    skip in Synth — Synth uses constant_zero per the Mode S asymmetric pattern,
    so adding bridge ports there creates dangling/wasted plumbing.
    """
    if entry.get('bridge_port_role') and stage == 'Synthesize':
        return lines, 'SKIPPED', f"GAP-3: bridge_port_role={entry['bridge_port_role']} skipped in Synth (uses constant_zero)"

    mod_name = entry.get('module_name', '')
    mod_name_stage = (entry.get('module_name_per_stage') or {}).get(stage)
    signal   = entry.get('signal_name', '')
    direction = entry.get('declaration_type', 'input')  # 'input' or 'output'

    if direction == 'wire':
        return lines, 'SKIPPED', 'wire type — implicitly declared by port connections'

    mod_start = -1

    # Try the per-stage resolved module name FIRST (exact match) — handles
    # multi-level P&R uniquification suffixes (e.g. 'umcdcqarb_0_0') that the
    # generic _0 / prefix patterns below cannot express (they only strip one
    # trailing '_0'). Mirrors the fallback already used by the port_connection
    # path (mod_name resolution near _module_bounds callers) for consistency.
    if mod_name_stage and mod_name_stage != mod_name:
        re_exact_stage = re.compile(rf'^module\s+{re.escape(mod_name_stage)}\b')
        for i, line in enumerate(lines):
            if re_exact_stage.match(line):
                mod_start, mod_name = i, mod_name_stage
                break

    # Find module start: try exact, _0 suffix (P&R rename), then any '<prefix>_<bare>' (tile-prefixed netlist)
    # Also handles '<prefix>_<bare>_0' (P&R uniquification of tile-prefixed modules)
    re_bare     = re.compile(rf'^module\s+{re.escape(mod_name)}\b')
    re_p0       = re.compile(rf'^module\s+{re.escape(mod_name)}_0\b')
    re_prefix   = re.compile(rf'^module\s+(\S+_{re.escape(mod_name)})\b(?!_\d)')
    re_prefix_p0= re.compile(rf'^module\s+(\S+_{re.escape(mod_name)}_0)\b')
    for i, line in enumerate(lines):
        if mod_start >= 0:
            break
        if re_bare.match(line):
            mod_start = i
            break
        if re_p0.match(line):
            mod_start, mod_name = i, mod_name + '_0'
            break
        m = re_prefix_p0.match(line)
        if m:
            mod_start, mod_name = i, m.group(1)
            break
        m = re_prefix.match(line)
        if m:
            mod_start, mod_name = i, m.group(1)
            break
    if mod_start < 0:
        return lines, 'SKIPPED', f'module {mod_name} not found in stage'

    # Check already applied
    port_close = find_port_list_close(lines, mod_start)
    if port_close < 0:
        return lines, 'SKIPPED', f'cannot find port list close for {mod_name}'
    port_region = ''.join(lines[mod_start:port_close+1])
    if re.search(rf'\b{re.escape(signal)}\b', port_region):
        return lines, 'ALREADY_APPLIED', f'{signal} already in port list of {mod_name}'

    # Insert signal into port list: add ", signal" before last ")"
    close_line = lines[port_close]
    last_paren = close_line.rfind(')')
    if last_paren < 0:
        return lines, 'SKIPPED', f'no ) on port close line {port_close}'
    # Preserve original close suffix (e.g. ') ;' or ');') — must keep the semicolon
    orig_suffix = close_line[last_paren:]          # e.g. ') ;' or ') ;\n'
    if ';' not in orig_suffix:
        orig_suffix = ') ;\n'                       # ensure semicolon always present
    lines[port_close] = close_line[:last_paren] + f' , {signal}\n' + orig_suffix

    # Insert direction declaration after port list close.
    # If entry has bus_width > 1, emit the range prefix [W-1:0] so FM sees
    # an array-type port (e.g. input [7:0] RowUpperMask ;).
    # Without this, bus ports declared as scalar cause SVR-14 when the
    # consumer references RowUpperMask[N] bit-select indexing.
    # Accept both field names: complete-mode studier emits `bus_width` (e.g. "[7:0]"),
    # simple-mode studier emits `width` (e.g. 8). Reading only `bus_width` silently
    # dropped the range for simple entries -> scalar `input RowUpperMask ;` while the
    # body bit-selects RowUpperMask[N] -> FM read failure "Indexing into non-array"
    # (FM-599). Fall back to `width` so either schema produces the correct bus port.
    bus_width = entry.get('bus_width')
    if bus_width in (None, '', 0):
        bus_width = entry.get('width')
    # bus_width may be an int (e.g. 8) or a range string (e.g. "7:0").
    # Parse both forms to extract the range prefix [W-1:0].
    range_pfx = ''
    if bus_width:
        if isinstance(bus_width, int) and bus_width > 1:
            range_pfx = f'[{bus_width - 1}:0] '
        elif isinstance(bus_width, str) and ':' in bus_width:
            # Range string form e.g. "7:0" or "[7:0]" — strip outer brackets first
            bw = bus_width.strip().strip('[]')
            range_pfx = f'[{bw}] '
        elif isinstance(bus_width, str) and bus_width.isdigit() and int(bus_width) > 1:
            w = int(bus_width)
            range_pfx = f'[{w - 1}:0] '
    decl_line = f'  {direction} {range_pfx}{signal} ;\n'
    lines.insert(port_close + 1, decl_line)

    # Post-edit verification: confirm BOTH the port-list addition AND the
    # direction declaration are physically in the modified file. Catches
    # silent failures where lines[port_close] edit didn't take or the index
    # was wrong.
    new_port_region = ''.join(lines[mod_start:port_close + 2])
    in_port_list = bool(re.search(rf'\b{re.escape(signal)}\b', new_port_region))
    has_direction = bool(re.search(rf'^\s*{direction}\s+(?:\[\d+:\d+\]\s+)?{re.escape(signal)}\b', lines[port_close + 1]))
    if not (in_port_list and has_direction):
        return lines, 'SKIPPED', f'VERIFY_FAILED port_decl: in_port_list={in_port_list} has_direction={has_direction} for {signal} in {mod_name}'

    return lines, 'APPLIED', f'added {signal} to port list and {direction} decl in {mod_name}'


# ── Pass 3: port_connection ───────────────────────────────────────────────────

def _module_bounds(lines, line_no):
    """Find (start, end) line indices of the module containing line_no.
    Returns (None, None) if not inside a module."""
    start = None
    for i in range(line_no, -1, -1):
        if re.match(r'^\s*module\s+', lines[i]):
            start = i; break
    if start is None:
        return None, None
    depth = 0
    for j in range(start, len(lines)):
        if re.match(r'^\s*module\s+', lines[j]):
            depth += 1
        elif re.match(r'^\s*endmodule', lines[j]):
            depth -= 1
            if depth == 0:
                return start, j
    return start, None


def _is_wire_declared_in_module(body, name):
    """Robust check: is `name` already declared as wire/tri/wand/wor/reg OR as
    an input/output/inout PORT in any decl form anywhere in the module body?

    Handles:
      - Single-name decls:    `wire X ;` / `output X ;`
      - Bus decls:            `wire [3:0] X ;` / `output [31:0] X ;`
      - Multi-name decls:     `wire A, B, X, C ;`
      - Bit-indexed names:    `wire X[1] ;`
      - Whitespace variants

    Conservative: returns True on ANY match — better to skip an extra decl than
    to risk an FM-599 'Duplicate wire' ABORT (run 20260511083831 root cause)
    OR emit invalid `wire <bus>[N] ;` when <bus> is already a port (would also
    cascade to FM SVR-4/SVR-64 ABORT — see eco_pre_fm_check INVALID_WIRE_DECL_SYNTAX).
    """
    if not name:
        return False
    base_name = re.sub(r'\[[^\]]+\]', '', name).strip()  # strip [N] for bus-name match
    decl_re = re.compile(r'^\s*(wire|tri|wand|wor|reg|input|output|inout)\s+(?:\[[^\]]+\]\s+)?([^;]+);')
    for ln in body:
        dm = decl_re.match(ln)
        if not dm:
            continue
        for n in [x.strip() for x in dm.group(2).split(',')]:
            n_base = re.sub(r'\[[^\]]+\]', '', n).strip()
            if n.strip() == name or n_base == base_name:
                return True
    return False


def _cleanup_orphan_wire_and_add_new_decl(lines, mod_start, mod_end, old_net, new_net):
    """GAP-2 cleanup: within (mod_start, mod_end] window:
       (a) remove `wire <old_net> ;` if old_net has zero remaining references in module
       (b) add `wire <new_net> ;` after the last existing wire decl, if not already declared
    Returns (lines, removed_orphan: bool, added_decl: bool)."""
    if mod_start is None or mod_end is None:
        return lines, False, False
    body = lines[mod_start:mod_end + 1]
    # Step (a): remove orphan wire decl for old_net (only when old_net has no remaining refs)
    removed_orphan = False
    if old_net:
        # Count non-decl references to old_net in module body
        decl_re = re.compile(rf'^\s*wire\s+(?:\[[^\]]+\]\s+)?{re.escape(old_net)}\s*;')
        ref_re  = re.compile(rf'\b{re.escape(old_net)}\b')
        ref_count = 0
        decl_idx_in_body = None
        for i, ln in enumerate(body):
            if decl_re.match(ln):
                decl_idx_in_body = i
                continue  # decl line itself doesn't count as a reference
            if ref_re.search(ln):
                ref_count += 1
        if decl_idx_in_body is not None and ref_count == 0:
            del body[decl_idx_in_body]
            removed_orphan = True
    # Step (b): add wire decl for new_net if not already declared.
    # Uses _is_wire_declared_in_module() which handles bus decls, multi-name
    # decls, and bit-indexed names — the previous narrow regex missed bus
    # decls (e.g. `wire [3:0] REG_UmcCfgEco;` doesn't match new_net=
    # `REG_UmcCfgEco[1]`) and could insert a duplicate that triggers FM-599
    # ABORT (Duplicate wire/tri/wand/wor declaration) — see run 20260511083831.
    added_decl = False
    if new_net and not _is_wire_declared_in_module(body, new_net):
        # Insert BEFORE the first occurrence of new_net in the module body.
        # Inserting after the last `wire` decl risks placing the declaration
        # AFTER the first use of new_net when Perl-inserted ECO wires appear
        # near endmodule (Perl script appends to module end) — the REGCMD port
        # connection (first use) is in the middle, causing SVR-9 implicit/explicit
        # duplicate when the explicit decl lands after it.
        net_ref_re = re.compile(rf'\b{re.escape(new_net)}\b')
        first_use_idx = -1
        for i, ln in enumerate(body):
            if net_ref_re.search(ln):
                first_use_idx = i
                break
        if first_use_idx > 0:
            # Find the START of the statement containing the first use by scanning
            # backward for the previous ';' (end of prior statement). This prevents
            # inserting inside a multi-line concat or port-connection block, which
            # would produce invalid Verilog (SVR-4).
            stmt_start = first_use_idx
            for j in range(first_use_idx - 1, -1, -1):
                if ';' in body[j]:
                    stmt_start = j + 1
                    break
            insert_at = stmt_start
        else:
            # Fallback: after last wire decl if new_net not yet in body
            last_wire_idx = -1
            for i, ln in enumerate(body):
                if re.match(r'^\s*wire\s+', ln):
                    last_wire_idx = i
            insert_at = last_wire_idx + 1 if last_wire_idx >= 0 else 1
        indent = '  '
        body.insert(insert_at, f'{indent}wire {new_net} ;\n')
        added_decl = True
    if removed_orphan or added_decl:
        lines[mod_start:mod_end + 1] = body
    return lines, removed_orphan, added_decl


def _apply_bus_rename(lines, gz_path, inst_name, port_name, old_net, new_net, bus_bit_index=None):
    """Replace a single net in .port_name({...}) bus concatenation.
    Two modes:
      (a) old_net given → scope-search inside .port_name(...) region, replace by name.
      (b) old_net=None + bus_bit_index given → parse the {...} concat, identify the
          net at MSB-first position (width - 1 - bus_bit_index), replace it.
    """
    # Find instance start
    inst_start = -1
    if gz_path:
        inst_start = grep_lineno(rf'\b{inst_name}\s*\(', gz_path)
    if inst_start < 0:
        for i, line in enumerate(lines):
            if re.search(rf'\b{re.escape(inst_name)}\s*\(', line):
                inst_start = i; break
    # Recovery — try `_0` uniquification suffix (Route stage commonly renames
    # repeated submodule instances). Then try plain `<inst>(?:_\d+)?` regex.
    if inst_start < 0:
        for cand in (f'{inst_name}_0', f'{inst_name}_1'):
            for i, line in enumerate(lines):
                if re.search(rf'\b{re.escape(cand)}\s*\(', line):
                    inst_start = i; inst_name = cand; break
            if inst_start >= 0: break
    if inst_start < 0:
        return lines, 'SKIPPED', f'bus_rename: instance {inst_name} not found (incl. _0/_1 suffix variants)'
    # Find instance close (depth track)
    depth, inst_close = 0, -1
    for i in range(inst_start, len(lines)):
        for ch in lines[i].split('//')[0]:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0: inst_close = i; break
        if inst_close >= 0: break
    if inst_close < 0:
        return lines, 'SKIPPED', f'bus_rename: cannot find close of {inst_name}'

    # Mode (a): old_net given → simple scoped replace
    if old_net:
        in_port = False
        for i in range(inst_start, inst_close + 1):
            if not in_port and re.search(rf'\.\s*{re.escape(port_name)}\s*\(', lines[i]):
                in_port = True
            if in_port and re.search(rf'\b{re.escape(old_net)}\b', lines[i]):
                lines[i] = re.sub(rf'\b{re.escape(old_net)}\b', new_net, lines[i], count=1)
                # GAP-2 cleanup: remove orphan UNCONNECTED decl + add new wire decl in same module
                ms, me = _module_bounds(lines, inst_start)
                only_unc = old_net.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_'))
                lines, rm, ad = _cleanup_orphan_wire_and_add_new_decl(
                    lines, ms, me, old_net if only_unc else None, new_net)
                tag = []
                if rm: tag.append('removed_orphan')
                if ad: tag.append('added_decl')
                suffix = (' [' + ','.join(tag) + ']') if tag else ''
                return lines, 'APPLIED', f'bus_rename: {inst_name}.{port_name} {old_net}→{new_net}{suffix}'
        return lines, 'SKIPPED', f'bus_rename: {old_net} not found in {inst_name}.{port_name}'

    # Mode (b): bus_bit_index → parse {...} concat by position
    if bus_bit_index is None:
        return lines, 'SKIPPED', f'bus_rename: neither old_net nor bus_bit_index given'

    # Comment-aware: strip Verilog //... and /*...*/ comments before brace tracking
    # and content extraction. Critical for handling comment-mess corruption from
    # prior agent inline-fix attempts.
    def _strip_v_comments(s):
        s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
        s = re.sub(r'//[^\n]*', '', s)
        return s

    # Locate `.port_name(` line — MUST search the comment-stripped line so we
    # land on the ACTIVE port_connection, NOT a previous-ECO commented-out
    # line (which is a real failure mode: prior comments leaving `.port(` and
    # `{...}` artifacts that look real after _strip_v_comments).
    port_pat = re.compile(rf'\.\s*{re.escape(port_name)}\s*\(')
    open_line = -1
    for i in range(inst_start, inst_close + 1):
        clean = _strip_v_comments(lines[i])
        if port_pat.search(clean):
            open_line = i; break
    if open_line < 0:
        return lines, 'SKIPPED', f'bus_rename: .{port_name}( not found in {inst_name}'
    # Find matching `}` closing the {...} concat — depth-track on the FULL
    # joined comment-stripped text, not line-by-line. Per-line stripping +
    # per-line brace tracking falsely counted `}` characters from inside
    # commented-out previous-ECO lines (the `}` was inside a `//...` comment
    # so the line-strip removed it, but only AFTER the brace counter had
    # already incremented — wrong execution order). The result was that
    # `end_line` landed on a commented-out prior-ECO line rather than the
    # active one, and the rename modified the wrong line.
    joined_clean = _strip_v_comments(''.join(lines[open_line:inst_close + 1]))
    end_offset_in_clean = -1
    br_depth, started = 0, False
    for off, ch in enumerate(joined_clean):
        if ch == '{':
            started = True; br_depth += 1
        elif ch == '}' and started:
            br_depth -= 1
            if br_depth == 0:
                end_offset_in_clean = off
                break
    if end_offset_in_clean < 0:
        return lines, 'SKIPPED', f'bus_rename: {inst_name}.{port_name} not a {{}} concat'
    # Map clean-text offset back to a raw line index by counting newlines
    # in the comment-stripped text up to end_offset
    nl_count = joined_clean.count('\n', 0, end_offset_in_clean + 1)
    end_line = open_line + nl_count
    if end_line > inst_close:
        end_line = inst_close
    # Extract content from comment-stripped joined text. Output overwrites the
    # multi-line region with a single clean line — comments inside the bus
    # range are discarded (they're typically corruption artifacts anyway).
    full       = ''.join(lines[open_line:end_line + 1])
    full_clean = _strip_v_comments(full)
    m = re.search(r'\{([^{}]*)\}', full_clean, re.DOTALL)
    if not m:
        return lines, 'SKIPPED', f'bus_rename: cannot parse {{}} content for {inst_name}.{port_name}'
    elements = [e.strip() for e in m.group(1).split(',') if e.strip()]
    width = len(elements)
    pos = width - 1 - bus_bit_index  # MSB-first
    if pos < 0 or pos >= width:
        return lines, 'SKIPPED', f'bus_rename: bit_index {bus_bit_index} out of range (width={width})'
    old_at_pos = elements[pos]
    elements[pos] = new_net
    # Build new content from comment-stripped text. This overwrites the original
    # (potentially comment-corrupted) multi-line region with a single clean line.
    new_full_clean = full_clean.replace(m.group(0), '{' + ', '.join(elements) + '}', 1)
    candidate = new_full_clean.splitlines(keepends=True)
    if not candidate:  # safety: if splitlines returns empty (rare), keep one line
        candidate = [new_full_clean if new_full_clean.endswith('\n') else new_full_clean + '\n']
    # Verify candidate has new_net at expected position before commit (catches
    # wrong-instance match — e.g. multiple instances with same port name).
    cand_m = re.search(r'\{([^{}]*)\}', ''.join(candidate), re.DOTALL)
    cand_elems = [e.strip() for e in cand_m.group(1).split(',')] if cand_m else []
    if not cand_elems or cand_elems[pos] != new_net:
        return lines, 'SKIPPED', f'bus_rename verify FAILED: position {pos} = {cand_elems[pos] if cand_elems else "?"} (expected {new_net}) — likely matched wrong instance'
    lines[open_line:end_line + 1] = candidate
    # GAP-2 cleanup: remove orphan UNCONNECTED decl + add new wire decl in same module
    ms, me = _module_bounds(lines, inst_start)
    only_unc = isinstance(old_at_pos, str) and old_at_pos.startswith(('UNCONNECTED_', 'SYNOPSYS_UNCONNECTED_'))
    lines, rm, ad = _cleanup_orphan_wire_and_add_new_decl(
        lines, ms, me, old_at_pos if only_unc else None, new_net)
    tag = []
    if rm: tag.append('removed_orphan')
    if ad: tag.append('added_decl')
    suffix = (' [' + ','.join(tag) + ']') if tag else ''
    return lines, 'APPLIED', f'bus_rename: {inst_name}.{port_name}[{bus_bit_index}] {old_at_pos}→{new_net}{suffix}'


def apply_si_consumer_replace(lines, entry, stage='Synthesize'):
    """GAP-4c: Replace ONE sibling-module DFF's .SI net with the bridge Q_in port,
    closing the scan chain at the sibling module. Skipped in Synth.

    Entry schema:
      {
        "change_type": "si_consumer_replace",
        "sibling_module": "<module_name>",
        "consumer_dff_inst": "<dff_instance>",
        "new_si_net": "ECO_<jira>_Q_in"
      }
    """
    if stage == 'Synthesize':
        return lines, 'SKIPPED', 'GAP-4c: si_consumer_replace skipped in Synth'

    sib_mod = entry.get('sibling_module', '')
    inst    = entry.get('consumer_dff_inst', '')
    new_si  = entry.get('new_si_net', '')
    if not (sib_mod and inst and new_si):
        return lines, 'SKIPPED', 'si_consumer_replace entry missing required fields'

    re_candidates = [
        re.compile(rf'^module\s+{re.escape(sib_mod)}\b'),
        re.compile(rf'^module\s+{re.escape(sib_mod)}_0\b'),
        re.compile(rf'^module\s+{re.escape(sib_mod)}_1\b'),
        re.compile(rf'^module\s+\S+_{re.escape(sib_mod)}\b'),
    ]
    mod_start = -1
    for i, line in enumerate(lines):
        if any(p.match(line) for p in re_candidates):
            mod_start = i; break
    if mod_start < 0:
        return lines, 'SKIPPED', f'sibling module {sib_mod} not found in stage {stage}'
    _, mod_end = _module_bounds(lines, mod_start)
    if mod_end is None:
        return lines, 'SKIPPED', f'cannot find endmodule for {sib_mod}'

    inst_re = re.compile(rf'\b{re.escape(inst)}\s*\(')
    inst_idx = -1
    for i in range(mod_start, mod_end + 1):
        if inst_re.search(lines[i]):
            inst_idx = i; break
    if inst_idx < 0:
        return lines, 'SKIPPED', f'consumer DFF {inst} not found in {sib_mod}'

    pin_re = re.compile(r'\.\s*SI\s*\(\s*([^)]+?)\s*\)')
    depth = 0
    for j in range(inst_idx, mod_end + 1):
        for ch in lines[j].split('//')[0]:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0: break
        m = pin_re.search(lines[j])
        if m:
            old_si = m.group(1).strip()
            if old_si == new_si:
                return lines, 'ALREADY_APPLIED', f'{inst}.SI already = {new_si}'
            lines[j] = pin_re.sub(f'.SI ( {new_si} )', lines[j], count=1)
            return lines, 'APPLIED', f'si_consumer_replace: {sib_mod}/{inst}.SI {old_si}→{new_si}'
        if depth == 0 and j > inst_idx:
            break
    return lines, 'SKIPPED', f'.SI pin not found on {inst} in {sib_mod}'


def apply_sibling_pin_consolidation(lines, entry, stage='Synthesize'):
    """GAP-4: Rewrite a list of sibling-module DFFs' .SE (or .SI) pin from their
    current net to the new bridge wire. Module-scope-aware: only rewrites within
    the named sibling_module body. Skipped in Synth (per GAP-3).

    Entry schema:
      {
        "change_type": "sibling_pin_consolidation",
        "sibling_module": "<module_name>",      # exact or with _0/_1 suffix
        "pin_name": "SE",                       # or "SI"
        "new_net": "<bridge_wire_name>",         # e.g. ECO_<jira>_SE_out
        "consolidation_target_dffs": ["<inst1>", "<inst2>", ...]
      }
    """
    if stage == 'Synthesize':
        return lines, 'SKIPPED', 'GAP-4: sibling consolidation skipped in Synth'

    sib_mod   = entry.get('sibling_module', '')
    pin_name  = entry.get('pin_name', '')
    new_net   = entry.get('new_net', '')
    targets   = entry.get('consolidation_target_dffs') or []
    if not (sib_mod and pin_name and new_net and targets):
        return lines, 'SKIPPED', 'sibling consolidation entry missing required fields'

    # Find sibling module body (any of: exact, _0, _1, prefix-match)
    re_candidates = [
        re.compile(rf'^module\s+{re.escape(sib_mod)}\b'),
        re.compile(rf'^module\s+{re.escape(sib_mod)}_0\b'),
        re.compile(rf'^module\s+{re.escape(sib_mod)}_1\b'),
        re.compile(rf'^module\s+\S+_{re.escape(sib_mod)}\b'),
    ]
    mod_start = -1
    for i, line in enumerate(lines):
        if any(p.match(line) for p in re_candidates):
            mod_start = i; break
    if mod_start < 0:
        return lines, 'SKIPPED', f'sibling module {sib_mod} not found in stage {stage}'
    _, mod_end = _module_bounds(lines, mod_start)
    if mod_end is None:
        return lines, 'SKIPPED', f'cannot find endmodule for {sib_mod}'

    rewired = []
    not_found = []
    for inst in targets:
        # Find the instance line within the module body
        inst_re = re.compile(rf'\b{re.escape(inst)}\s*\(')
        inst_idx = -1
        for i in range(mod_start, mod_end + 1):
            if inst_re.search(lines[i]):
                inst_idx = i; break
        if inst_idx < 0:
            not_found.append(inst)
            continue
        # Walk forward to find the .pin_name(...) line within the instance
        pin_re = re.compile(rf'\.\s*{re.escape(pin_name)}\s*\(\s*([^)]+?)\s*\)')
        depth = 0
        for j in range(inst_idx, mod_end + 1):
            for ch in lines[j].split('//')[0]:
                if ch == '(': depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0: break
            m = pin_re.search(lines[j])
            if m and m.group(1).strip() != new_net:
                lines[j] = pin_re.sub(f'.{pin_name} ( {new_net} )', lines[j], count=1)
                rewired.append(inst)
                break
            if depth == 0 and j > inst_idx:
                break
    msg_parts = []
    if rewired:   msg_parts.append(f'rewired={len(rewired)}')
    if not_found: msg_parts.append(f'not_found={not_found}')
    status = 'APPLIED' if rewired else 'SKIPPED'
    return lines, status, f'sibling_pin_consolidation in {sib_mod} (stage={stage}): {", ".join(msg_parts)}'


def apply_port_connection(lines, entry, gz_path=None, stage='Synthesize'):
    """Add .port(net) to submodule instance block, OR perform bus-position rename.

    GAP-3: bridge_port instance hookups skipped in Synth (Synth uses constant_zero).
    """
    if entry.get('bridge_port_role') and stage == 'Synthesize':
        return lines, 'SKIPPED', f"GAP-3: bridge_port_role={entry['bridge_port_role']} hookup skipped in Synth"

    parent_mod  = entry.get('module_name', '') or entry.get('parent_module', '')
    inst_name   = entry.get('instance_name', '') or entry.get('submodule_instance', '')
    port_name   = entry.get('port_name', '')     or entry.get('new_token', '')

    # Bus-position rename branch — fires when entry has any of:
    #   (a) net_name_before (per-stage map) + net_name_after  — original schema
    #   (b) bus_bit_index — newer schema; old net derived by parsing the {} concat
    nb = entry.get('net_name_before')
    na = entry.get('net_name_after')
    bus_bit = entry.get('bus_bit_index')
    # Resolve the PER-STAGE old_net first. net_name_before may be a dict whose
    # per-stage value is None for a NEW port-connection addition (no net to rename)
    # — in that case the dict is non-None but there is nothing to rename, so this
    # must NOT route to _apply_bus_rename (which would SKIP "neither old_net nor
    # bus_bit_index"); it must fall through to the insert-new-port path below.
    old_net = (nb.get(stage) if isinstance(nb, dict) else nb) if nb is not None else None
    if (old_net is not None and na) or bus_bit is not None:
        new_net = na or entry.get('net_name', '') or entry.get('flat_net_name', '')
        if not all([inst_name, port_name, new_net]):
            return lines, 'SKIPPED', f'bus_rename missing inst/port/new (stage={stage})'
        return _apply_bus_rename(lines, gz_path, inst_name, port_name, old_net, new_net, bus_bit)

    net_name = entry.get('net_name', '') or entry.get('flat_net_name', '')

    if not all([inst_name, port_name, net_name]):
        return lines, 'SKIPPED', 'missing instance_name/port_name/net_name'

    # Fast path: use grep to find instance start line number in the gz file
    inst_start = -1
    if gz_path:
        inst_start = grep_lineno(rf'\b{inst_name}\s*\(', gz_path)

    # Fallback: scan lines array
    if inst_start < 0:
        for i, line in enumerate(lines):
            if re.search(rf'\b{re.escape(inst_name)}\s*\(', line):
                inst_start = i
                break
    if inst_start < 0:
        return lines, 'SKIPPED', f'instance {inst_name} not found'

    # Find instance close — depth track from inst_start
    # Search limit: scan to end of lines (no fixed cap — large instances like ARB can span 100k+ lines)
    depth = 0
    inst_close = -1
    for i in range(inst_start, len(lines)):
        clean = lines[i].split('//')[0]
        for ch in clean:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    inst_close = i
                    break
        if inst_close >= 0:
            break
    if inst_close < 0:
        for i in range(inst_start + 1, len(lines)):
            if re.match(r'^\)\s*;', lines[i].strip()):
                inst_close = i
                break
        if inst_close < 0:
            return lines, 'SKIPPED', f'cannot find instance close for {inst_name}'

    inst_block = ''.join(lines[inst_start:inst_close+1])

    # Already applied?
    if re.search(rf'\.\s*{re.escape(port_name)}\s*\(', inst_block):
        if re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', inst_block):
            return lines, 'ALREADY_APPLIED', f'.{port_name}({net_name}) already in {inst_name}'
        # Refuse to "rewire" a bus concat — would corrupt {a,b,c} into single net.
        # Bus-position renames belong on the _apply_bus_rename path (set bus_bit_index in study JSON).
        if re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*\{{', inst_block):
            return lines, 'SKIPPED', f'.{port_name} on {inst_name} is a {{}} bus concat — entry must set bus_bit_index for _apply_bus_rename'
        # Port exists with a single-net value — scan the full instance block for the line
        rewire_pat = re.compile(rf'\.\s*{re.escape(port_name)}\s*\([^)]*\)')
        rewire_applied = False
        for ri in range(inst_start, inst_close + 1):
            if rewire_pat.search(lines[ri]):
                before_ri = lines[ri]
                lines[ri] = rewire_pat.sub(f'.{port_name}( {net_name} )', lines[ri])
                if lines[ri] != before_ri and net_name in lines[ri]:
                    rewire_applied = True
                    break
        if not rewire_applied:
            return lines, 'SKIPPED', f'VERIFY_FAILED rewire: .{port_name} on {inst_name} — regex matched in inst_block but substitution failed on all lines'
        return lines, 'APPLIED', f'rewired existing .{port_name} to ({net_name}) in {inst_name}'

    # Insert new port as a separate line before inst_close.
    # Ensure the previous non-empty port line ends with ',' (add if missing).
    # Also: if inst_close line itself contains a port connection (not just ') ;'),
    # the inserted port needs a trailing comma too — otherwise the port on inst_close
    # line follows without a separator → FM "Expected ',' or ')' but found '.'".
    for prev_idx in range(inst_close - 1, max(inst_start - 1, 0), -1):
        stripped = lines[prev_idx].rstrip()
        if stripped:
            if not stripped.endswith(','):
                lines[prev_idx] = stripped + ' ,\n'
            break
    # Determine if inst_close line has a port connection (not just ') ;')
    close_has_port = bool(re.search(r'\.\s*\w+\s*\(', lines[inst_close].split('//')[0]))
    trailing = ' ,' if close_has_port else ''
    lines.insert(inst_close, f'    .{port_name}( {net_name} ){trailing}\n')

    # Post-edit verify: confirm the new line is actually in the modified instance block
    new_inst_block = ''.join(lines[inst_start:inst_close + 2])
    if not re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', new_inst_block):
        return lines, 'SKIPPED', f'VERIFY_FAILED port_conn insert: .{port_name}({net_name}) not found in {inst_name} after insert'
    return lines, 'APPLIED', f'added .{port_name}({net_name}) to {inst_name}'


# ── Pass 4: rewire ────────────────────────────────────────────────────────────

def _find_module_range(lines, mod_name):
    """(start, end) line indices of module `mod_name` (exact, then P&R _0 suffix, then
    tile-prefixed variant). Returns (None, None) if not found."""
    if not mod_name:
        return None, None
    pats = [rf'^\s*module\s+{re.escape(mod_name)}\b',
            rf'^\s*module\s+{re.escape(mod_name)}_0\b',
            rf'^\s*module\s+\S*_{re.escape(mod_name)}\b(?!_\d)',
            rf'^\s*module\s+\S*_{re.escape(mod_name)}_0\b']
    start = None
    for p in pats:
        rx = re.compile(p)
        start = next((i for i, l in enumerate(lines) if rx.match(l)), None)
        if start is not None:
            break
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^\s*endmodule\b', lines[j]):
            end = j + 1; break
        if re.match(r'^\s*module\s+', lines[j]):
            end = j; break
    return start, end


def apply_rewire(lines, entry, stage='Synthesize'):
    """Change pin connection in cell instance block. Returns (lines, status, reason)."""
    # Use per-stage cell name if available (handles P&R renamed cells).
    # Schema-resilient: try canonical → variants → mux-specific naming.
    per_stage = (entry.get('per_stage_cell_name') or entry.get('cell_name_per_stage')
                 or entry.get('mux_cell_instance_per_stage') or {})
    cell_name = per_stage.get(stage, '') or entry.get('cell_name', '')
    # Use per-stage pin name if available (e.g., ZN in Synthesize vs ZN1 in PrePlace/Route)
    # Support both field name conventions: per_stage_pin and pin_per_stage
    per_stage_pin = entry.get('per_stage_pin', {}) or entry.get('pin_per_stage', {})
    pin_name  = per_stage_pin.get(stage, '') or entry.get('pin', '')
    # Use per-stage nets if available
    old_net   = (entry.get('per_stage_old_net', {}) or {}).get(stage, '') or entry.get('old_net', '')
    new_net   = (entry.get('per_stage_new_net', {}) or {}).get(stage, '') or entry.get('new_net', '')

    if not all([cell_name, new_net]):
        return lines, 'SKIPPED', 'missing cell_name/new_net'

    # bus_element: replace old_net as a word within the cell's instance block
    # (Gap B: UNCONNECTED_* → named wire inside port bus { } concatenation)
    if entry.get('bus_element') and old_net:
        pat_bus = rf'\b{re.escape(old_net)}\b'
        # Find cell instance start — try literal name, then _0/_1 uniquification
        cell_start = next((i for i, l in enumerate(lines) if re.search(rf'\b{re.escape(cell_name)}\b', l)), -1)
        if cell_start < 0:
            for cand in (f'{cell_name}_0', f'{cell_name}_1'):
                cell_start = next((i for i, l in enumerate(lines) if re.search(rf'\b{re.escape(cand)}\b', l)), -1)
                if cell_start >= 0: cell_name = cand; break
        if cell_start < 0:
            return lines, 'SKIPPED', f'bus_element: cell {cell_name} not found (incl. _0/_1 suffix variants)'
        # Find cell block end using depth tracking
        depth, cell_close = 0, cell_start
        for i in range(cell_start, len(lines)):
            for ch in lines[i].split('//')[0]:
                if ch == '(': depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        cell_close = i
                        break
            if cell_close != cell_start: break
        # Search within cell block for old_net (handles multi-line buses)
        for i in range(cell_start, cell_close + 1):
            if re.search(pat_bus, lines[i]):
                lines[i] = re.sub(pat_bus, new_net, lines[i], count=1)
                return lines, 'APPLIED', f'{cell_name}: bus element {old_net} → {new_net}'
        return lines, 'SKIPPED', f'bus element {old_net} not found in {cell_name} block'

    if not pin_name:
        return lines, 'SKIPPED', 'missing pin_name'

    # MODULE SCOPING (uniquified-family collision fix): a uniquified generate array reuses
    # the SAME instance name (e.g. ctmi_1182) in N modules. A whole-file "first match" search
    # sends all N renames to copy 0 — the other copies' target nets stay double-driven /
    # undriven (see compare_fold on tag 20260707090807). When the entry names a module,
    # restrict the search to that module's line range; fall back to whole-file only if the
    # cell isn't in the named module (so entries without module_name keep old behavior).
    mod_name = ((entry.get('module_name_per_stage') or {}).get(stage)
                or entry.get('module_name', ''))
    m_lo, m_hi = _find_module_range(lines, mod_name)
    scoped = m_lo is not None

    # Find cell instance — module-scoped first (exact name), then whole-file fallback.
    cell_start = -1
    if scoped:
        for i in range(m_lo, m_hi):
            if re.search(rf'\b{re.escape(cell_name)}\b', lines[i]):
                cell_start = i
                break
    if cell_start < 0:
        for i, line in enumerate(lines):
            if re.search(rf'\b{re.escape(cell_name)}\b', line):
                cell_start = i
                break

    # Per-stage cell rename fallback: tool-generated MUX instance names
    # (ctmi_*, phs_*, FxPrePlace_*) AND cell-type variants (MUX2D2 vs
    # MUX2EQ2AD1 vs MUX2D1AMD — all MUX2 family, different drive/leakage)
    # get renamed by CTS/CTS-OPT in PP/Route. The studier is supposed to emit
    # cell_name_per_stage; when it doesn't, locate the cell by:
    #   (A) Pin connection to old_net (preferred — old select net is preserved
    #       across stages even when cell instance + cell type both change)
    #   (B) Backward-trace from <target_register>_reg.D — for the case where
    #       even the old_net was renamed
    # Both produce the per-stage instance name without trusting the Synth label.
    if cell_start < 0:
        ct        = entry.get('cell_type', '')
        target_reg = entry.get('target_register', '') or entry.get('mux_select_target_register', '')
        # Family prefix from cell_type (MUX2D2BWP… → MUX2; MUX2EQ2AD1AMD… → MUX2EQ2A;
        # AOI21D1AMD… → AOI21). Used as a SOFT filter — match preferred but optional.
        ct_family = re.match(r'^([A-Z]+\d*)', ct or '').group(1) if ct else ''

        # (A) pin .<pin>(<old_net>) — find ANY cell whose select pin matches.
        # Bus nets like ctmn_*/phfnn_* are preserved across stages even when
        # the consuming cell is renamed by CTS. Walk cell instance blocks
        # one at a time (bounded by `);` close), match pin INSIDE the block
        # only — guards against picking up the next cell's pin via a wide
        # lookahead. Prefer family-matched candidates over any-cell.
        def _cell_block_end(start):
            d = 0
            for k in range(start, min(start + 60, len(lines))):
                for ch in lines[k].split('//')[0]:
                    if ch == '(': d += 1
                    elif ch == ')':
                        d -= 1
                        if d == 0: return k
            return min(start + 30, len(lines) - 1)
        cand_nets = [old_net]
        nps = entry.get('net_per_stage', {}) or {}
        v = nps.get(stage) if isinstance(nps.get(stage), dict) else None
        if v: cand_nets.append(v.get(pin_name, ''))
        cand_nets = [n for n in cand_nets if n]
        family_hits = []   # (line_no, instance_name) — preferred
        any_hits    = []   # (line_no, instance_name) — fallback
        # Scope the pin/old_net match to the named module when known — uniquified copies
        # share old_net (e.g. ctmn_917) across N modules, so an unscoped match collides.
        i    = m_lo if scoped else 0
        _hi  = m_hi if scoped else len(lines)
        while i < _hi:
            m = re.match(r'^\s*([A-Z][A-Z0-9_]+)\s+(\w+)\s*\(', lines[i])
            if not m:
                i += 1; continue
            this_ct, inst_n = m.group(1), m.group(2)
            end = _cell_block_end(i)
            blk = ''.join(lines[i:end + 1])
            for cn in cand_nets:
                if re.search(rf'\.\s*{re.escape(pin_name)}\s*\(\s*{re.escape(cn)}\s*\)', blk):
                    if ct_family and this_ct.startswith(ct_family):
                        family_hits.append((i, inst_n))
                    else:
                        any_hits.append((i, inst_n))
                    break
            i = end + 1
        chosen = family_hits or any_hits
        if chosen:
            cell_start, cell_name = chosen[0]

        # (B) Backward-trace from <target_register>_reg* — covers the case
        # where even old_net was renamed by CTS (multibit packs, etc.). Match
        # the DFF instance by prefix (multibit packs use long _MB_<reg>… names).
        if cell_start < 0 and target_reg:
            dff_pat = rf'^\s*\w+\s+({re.escape(target_reg)}_reg\w*)\s*\('
            dff_line = next((i for i, l in enumerate(lines) if re.match(dff_pat, l)), -1)
            if dff_line >= 0:
                # Find .D(<wire>) or .D<bit>(<wire>) within next 60 lines
                d_wire = None
                for j in range(dff_line, min(dff_line + 60, len(lines))):
                    m = re.search(r'\.\s*D\d*\s*\(\s*(\w+)\s*\)', lines[j])
                    if m:
                        d_wire = m.group(1); break
                if d_wire:
                    # Find the cell driving d_wire — its output pin (Z/ZN) connects to d_wire
                    drv_pat = rf'\.\s*(Z|ZN|ZN1)\s*\(\s*{re.escape(d_wire)}\s*\)'
                    for i, line in enumerate(lines):
                        if not re.search(drv_pat, ''.join(lines[max(0,i-2):i+30])):
                            continue
                        blk = ''.join(lines[i:i+30])
                        if re.search(rf'\.\s*{re.escape(pin_name)}\s*\(', blk):
                            m2 = re.match(r'^\s*\w+\s+(\w+)\s*\(', line)
                            if m2:
                                cell_start = i
                                cell_name = m2.group(1)
                                break

    if cell_start < 0:
        return lines, 'SKIPPED', f'cell {cell_name} not found in {stage} (cell_type+pin grep + backward-trace from {entry.get("target_register","?")}_reg.D both failed)'

    # Find cell block end
    depth = 0
    cell_close = -1
    for i in range(cell_start, min(cell_start + 50, len(lines))):
        for ch in lines[i].split('//')[0]:
            if ch == '(': depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    cell_close = i
                    break
        if cell_close >= 0:
            break
    if cell_close < 0:
        cell_close = cell_start + 10  # fallback

    cell_block = ''.join(lines[cell_start:cell_close+1])

    # Already applied?
    if re.search(rf'\.\s*{re.escape(pin_name)}\s*\(\s*{re.escape(new_net)}\s*\)', cell_block):
        return lines, 'ALREADY_APPLIED', f'.{pin_name}({new_net}) already in {cell_name}'

    # Check old_net on pin
    pat = rf'(\.\s*{re.escape(pin_name)}\s*\()\s*{re.escape(old_net)}\s*(\))'
    found = False
    for i in range(cell_start, cell_close+1):
        if re.search(pat, lines[i]):
            lines[i] = re.sub(pat, rf'\g<1>{new_net}\g<2>', lines[i], count=1)
            found = True
            break
    if not found:
        # Net-rename recovery: P&R may have renamed <old_net> on the pin
        # (CTS-rebalanced wire / opt-merge). Read the pin's CURRENT net from
        # the cell block and use it as the effective old_net. This recovers
        # silent SKIPs caused by stage-divergent net names without trusting
        # the rtl_diff's Synth-only net label.
        pat2 = rf'(\.\s*{re.escape(pin_name)}\s*\()\s*([^)]+?)\s*(\))'
        actual_old = None
        for i in range(cell_start, cell_close+1):
            m = re.search(pat2, lines[i])
            if m:
                actual_old = m.group(2).strip()
                lines[i] = re.sub(pat2, rf'\g<1>{new_net}\g<3>', lines[i], count=1)
                found = True
                break
        if found:
            return lines, 'APPLIED', f'{cell_name}.{pin_name}: {actual_old} → {new_net} (net-rename recovery; rtl_diff old_net={old_net!r} not on pin in {stage})'
    if not found:
        return lines, 'SKIPPED', f'pin .{pin_name}({old_net}) not found in {cell_name} block (net-rename recovery also failed)'

    return lines, 'APPLIED', f'{cell_name}.{pin_name}: {old_net} → {new_net}'


# ── Pass 5: assign  (Mode S Q_out bridge) ────────────────────────────────────

def apply_assign(lines, entry):
    """Insert `assign LHS = RHS;` into the named module if not already present.
    Used by Mode S to bridge a new Q_out output port to the new DFF's Q net.
    Returns (lines, status, reason)."""
    mod_name = entry.get('module_name', '')
    lhs      = entry.get('lhs', '') or entry.get('signal_name', '')
    rhs      = entry.get('rhs', '')
    if not (mod_name and lhs and rhs):
        return lines, 'SKIPPED', f'assign: missing module_name/lhs/rhs ({mod_name},{lhs},{rhs})'

    # Find the module — exact, _0, or tile-prefixed
    re_bare   = re.compile(rf'^module\s+{re.escape(mod_name)}\b')
    re_p0     = re.compile(rf'^module\s+{re.escape(mod_name)}_0\b')
    re_prefix = re.compile(rf'^module\s+(\S+_{re.escape(mod_name)})\b')
    mod_start = -1
    for i, line in enumerate(lines):
        if re_bare.match(line) or re_p0.match(line) or re_prefix.match(line):
            mod_start = i; break
    if mod_start < 0:
        return lines, 'SKIPPED', f'assign: module {mod_name} not found'

    # Find matching endmodule
    mod_end = -1
    for i in range(mod_start + 1, len(lines)):
        if re.match(r'^endmodule\b', lines[i]):
            mod_end = i; break
    if mod_end < 0:
        return lines, 'SKIPPED', f'assign: endmodule for {mod_name} not found'

    # Already applied?
    body = ''.join(lines[mod_start:mod_end + 1])
    if re.search(rf'^\s*assign\s+{re.escape(lhs)}\s*=\s*{re.escape(rhs)}\s*;', body, re.MULTILINE):
        return lines, 'ALREADY_APPLIED', f'assign {lhs} = {rhs} already in {mod_name}'

    # Insert just before endmodule
    lines.insert(mod_end, f'  assign {lhs} = {rhs} ;\n')
    return lines, 'APPLIED', f'inserted assign {lhs} = {rhs} in {mod_name}'


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--study',    required=True)
    p.add_argument('--ref-dir',  required=True)
    p.add_argument('--tag',      required=True)
    p.add_argument('--stage',    required=True, choices=['Synthesize','PrePlace','Route'])
    p.add_argument('--round',    required=True, type=int)
    p.add_argument('--status',   required=True)
    args = p.parse_args()

    study   = json.loads(Path(args.study).read_text())
    posteco = f"{args.ref_dir}/data/PostEco/{args.stage}.v.gz"
    entries = study.get(args.stage, [])

    file_size = Path(posteco).stat().st_size if Path(posteco).exists() else 0
    size_mb = file_size // 1024 // 1024
    print(f"Loading {args.stage} netlist ({size_mb}MB compressed)...")

    # Memory guard: defer to agent only for very large files (>200MB compressed).
    # Below that threshold the script handles all 3 passes directly — important
    # for bus-position renames (REGCMD-style) which the agent often corrupts.
    if size_mb > 200:
        print(f"  File too large ({size_mb}MB) for in-memory processing — skipping Passes 3/4.")
        print(f"  Port_declarations were handled by eco_perl_spec.py Perl pass.")
        print(f"  Port_connections and rewires will be handled by eco_applier agent.")
        Path(args.status).write_text(json.dumps({
            'tag': args.tag, 'stage': args.stage, 'round': args.round,
            'entries': [{'name': '(skipped)', 'ct': 'all', 'status': 'SKIPPED',
                         'reason': f'File too large ({size_mb}MB) — agent handles Passes 3/4'}],
            'summary': {'applied': 0, 'already': 0, 'skipped': 1, 'verify_failed': 0}
        }, indent=2))
        marker = (f"ECO_SCRIPT_LAUNCHED: eco_netlist_port_rewire.py\n"
                  f"  stage:   {args.stage}\n"
                  f"  applied: 0 (large file — agent handles)\n"
                  f"  status:  {args.status}")
        print(f"\n{marker}")
        Path(args.status.replace('.json','_marker.txt')).write_text(marker + '\n')
        return 0

    lines = read_gz(posteco)
    print(f"Loaded {len(lines)} lines.")
    statuses = []
    verify_failed = 0

    for e in entries:
        if not e.get('confirmed', True):
            continue
        ct = e.get('change_type', '')

        if ct in ('port_declaration', 'port_promotion', 'new_port'):
            # GAP-4: new_port must also be applied in PP/Route — previously silently
            # dropped causing FE-LINK-7 ABORT (SplitActInProgOthDcq missing in PP/Route)
            lines, st, reason = apply_port_declaration(lines, e, stage=args.stage)
        elif ct == 'port_connection':
            lines, st, reason = apply_port_connection(lines, e, gz_path=posteco, stage=args.stage)
        elif ct == 'rewire':
            lines, st, reason = apply_rewire(lines, e, stage=args.stage)
        elif ct == 'assign':
            lines, st, reason = apply_assign(lines, e)
        elif ct == 'sibling_pin_consolidation':  # GAP-4
            lines, st, reason = apply_sibling_pin_consolidation(lines, e, stage=args.stage)
        elif ct == 'si_consumer_replace':  # GAP-4c
            lines, st, reason = apply_si_consumer_replace(lines, e, stage=args.stage)
        else:
            continue  # Handled by eco_perl_spec.py (Pass 1) or other pass

        inst = e.get('instance_name') or e.get('cell_name') or e.get('signal_name','?')
        statuses.append({'name': inst, 'ct': ct, 'status': st, 'reason': reason})
        if st == 'VERIFY_FAILED':
            verify_failed += 1
        print(f"  {st:15} {inst:35} {ct} — {reason[:60]}")

    # Write back if any changes were made
    applied = sum(1 for s in statuses if s['status'] == 'APPLIED')
    if applied > 0:
        write_gz(posteco, lines)
        print(f"\nRecompressed {args.stage}: {applied} changes applied.")
    else:
        print(f"\nNo changes written for {args.stage}.")

    # Write status JSON
    Path(args.status).write_text(json.dumps({
        'tag': args.tag, 'stage': args.stage, 'round': args.round,
        'entries': statuses,
        'summary': {
            'applied':       sum(1 for s in statuses if s['status']=='APPLIED'),
            'already':       sum(1 for s in statuses if s['status']=='ALREADY_APPLIED'),
            'skipped':       sum(1 for s in statuses if s['status']=='SKIPPED'),
            'verify_failed': verify_failed,
        }
    }, indent=2))

    # Write marker
    marker = (
        f"ECO_SCRIPT_LAUNCHED: eco_netlist_port_rewire.py\n"
        f"  stage:   {args.stage}\n"
        f"  applied: {applied}\n"
        f"  status:  {args.status}"
    )
    print(f"\n{marker}")
    Path(args.status.replace('.json','_marker.txt')).write_text(marker + '\n')

    return 1 if verify_failed else 0


if __name__ == '__main__':
    sys.exit(main())
