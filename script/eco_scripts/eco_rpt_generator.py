#!/usr/bin/env python3
"""
eco_rpt_generator.py — Generate human-readable Step 3 and Step 4 RPTs from JSON.

Replaces the inline Python code blocks the orchestrator used to embed in
ORCHESTRATOR.md. Same output format, single source of truth.

Usage:
  Step 3 RPT (from preeco_study.json):
    python3 eco_rpt_generator.py step3 \\
        --study  data/<TAG>_eco_preeco_study.json \\
        --tag    <TAG> --jira <JIRA> --tile <TILE> \\
        --output data/<TAG>_eco_step3_netlist_study_round1.rpt

  Step 4 RPT (from eco_applied_round<N>.json):
    python3 eco_rpt_generator.py step4 \\
        --applied data/<TAG>_eco_applied_round<N>.json \\
        --tag <TAG> --jira <JIRA> --round <N> \\
        --output  data/<TAG>_eco_step4_eco_applied_round<N>.rpt

Exit: 0 on success, 1 on read/write error.
"""
import argparse, json, sys
from pathlib import Path


# ── Step 3 helpers ────────────────────────────────────────────────────────────

def s3_label(e, stage):
    ct = e.get("change_type", "")
    if ct == "rewire":
        return e.get("per_stage_cell_name", {}).get(stage) or e.get("cell_name", "?")
    if ct in ("port_declaration", "port_promotion"):
        return e.get("signal_name", "?")
    return e.get("instance_name") or e.get("cell_name") or e.get("signal_name", "?")

def s3_detail(e, stage):
    ct = e.get("change_type", "")
    if ct == "rewire":
        return f"pin={e.get('pin','?')}  {e.get('old_net','?')} → {e.get('new_net','?')}  scope={e.get('instance_scope','?')}"
    if ct in ("new_logic_gate", "new_logic"):
        # Resolve per-stage input pins from port_connections_per_stage (prefer) or port_connections
        pcs = (e.get("port_connections_per_stage") or {}).get(stage) or e.get("port_connections") or {}
        _OUT = {'Z','ZN','ZN1','Q','QN','CO','S'}
        inputs = {p: v for p, v in pcs.items() if p not in _OUT and isinstance(v, str)}
        inp_str = "  inputs=[" + ", ".join(f".{p}({v})" for p, v in sorted(inputs.items())) + "]" if inputs else ""
        return f"fn={e.get('gate_function','?')}  out={e.get('output_net','?')}  scope={e.get('instance_scope','?')}{inp_str}"
    if ct == "new_logic_dff":
        return f"reg={e.get('target_register','?')}  out={e.get('output_net','?')}  scope={e.get('instance_scope','?')}"
    if ct in ("port_declaration", "port_promotion"):
        return f"module={e.get('module_name','?')}  dir={e.get('declaration_type','output')}"
    if ct == "port_connection":
        return f".{e.get('port_name','?')}({e.get('net_name','?')})  parent={e.get('parent_module','?')}"
    return ""

def s3_extra_lines(e):
    """Indented context lines under each entry — pulls Source/Notes/re_study_note
    fields when present (legacy + current studier output)."""
    lines = []
    src = e.get('source') or e.get('determination_source')
    if src:
        lines.append(f"    Source: {src}")
    note = e.get('notes') or e.get('re_study_note')
    if note:
        lines.append(f"    Notes:  {note}")
    reason = e.get('reason') or e.get('reason_per_stage', {}).get('Synthesize') if isinstance(e.get('reason_per_stage'), dict) else e.get('reason')
    # Show reason only if it adds info beyond what's in the detail line
    if reason and not note:
        lines.append(f"    Reason: {reason}")
    return lines

def s3_summary_section(study):
    """Final SUMMARY block aggregating wire_swap (rewire) edits per stage."""
    # Collect rewires from any stage (use Synthesize as canonical when present)
    seen = {}
    for stage in ("Synthesize", "PrePlace", "Route"):
        for e in study.get(stage, []):
            if e.get('change_type') != 'rewire':
                continue
            key = (e.get('cell_name') or e.get('instance_name', '?'), e.get('pin', '?'),
                   e.get('old_net', '?'), e.get('new_net', '?'))
            seen.setdefault(key, {})[stage] = e
    if not seen:
        return []
    lines = ["", "=" * 80, f"SUMMARY: {len(seen)} wire_swap change(s)"]
    for (cell, pin, old, new), per_stage in seen.items():
        lines.append(f"  {cell}/{pin}: {old} → {new}")
        for stage in ("Synthesize", "PrePlace", "Route"):
            e = per_stage.get(stage)
            if e:
                cell_s = e.get('per_stage_cell_name', {}).get(stage) or e.get('cell_name', '?')
                lines.append(f"    {stage:11s}: {cell_s} (module={e.get('module_name','?')})")
    return lines

def gen_step3(study_path, tag, jira, tile, output_path):
    study = json.loads(Path(study_path).read_text())
    with open(output_path, "w") as f:
        f.write(f"STEP 3 — PREECO NETLIST STUDY\n")
        f.write(f"Tag: {tag}  |  JIRA: {jira}  |  Tile: {tile}\n")
        f.write("=" * 80 + "\n\n")
        for stage in ("Synthesize", "PrePlace", "Route"):
            confirmed = [e for e in study.get(stage, []) if e.get("confirmed", True)]
            excluded  = [e for e in study.get(stage, []) if not e.get("confirmed", True)]
            f.write(f"[{stage}] — {len(confirmed)} confirmed, {len(excluded)} excluded\n")
            for e in confirmed:
                f.write(f"  CONFIRMED: {s3_label(e,stage):<40} type={e.get('change_type','?'):<20} {s3_detail(e,stage)}\n")
                for ex in s3_extra_lines(e):
                    f.write(ex + "\n")
            for e in excluded:
                f.write(f"  EXCLUDED:  {s3_label(e,stage):<40} reason={e.get('reason', e.get('unresolvable_reason','?'))}\n")
            f.write("\n")
        # Final SUMMARY section (rewires)
        for line in s3_summary_section(study):
            f.write(line + "\n")


# ── Step 4 helpers ────────────────────────────────────────────────────────────

def s4_name(e):
    return (e.get('instance_name') or e.get('cell_name')
            or e.get('signal_name') or e.get('port_name') or '?')

def s4_detail_line(e, status):
    ct = e.get('change_type', '?')
    if status == 'INSERTED':
        out = [f"    → cell_type={e.get('cell_type','?')}  output={e.get('output_net','?')}  scope={e.get('instance_scope','?')}\n"]
        if e.get('reason'):
            out.append(f"    → {e['reason']}\n")
        return "".join(out)
    if status == 'APPLIED':
        if e.get('reason'):
            return f"    → {e['reason']}\n"
        if ct == 'rewire':
            return f"    → {e.get('old_net','?')} → {e.get('new_net','?')} on pin {e.get('pin','?')}\n"
        if ct in ('port_declaration', 'port_promotion'):
            return f"    → module={e.get('module_name','?')}  decl_type={e.get('declaration_type','?')}\n"
        if ct == 'port_connection':
            return f"    → .{e.get('port_name','?')}({e.get('net_name','?')}) on instance {e.get('instance_name','?')}\n"
        return ""
    if status == 'ALREADY_APPLIED':
        ar = e.get('already_applied_reason', e.get('reason', 'no reason recorded — eco_applier must provide already_applied_reason'))
        return f"    → {ar}\n"
    if status == 'SKIPPED':
        return f"    → REASON: {e.get('reason', 'no reason recorded')}\n"
    if status == 'VERIFY_FAILED':
        return f"    → VERIFY FAILED: {e.get('reason', 'no reason recorded')}\n"
    return ""

def gen_step4(applied_path, tag, jira, rnd, output_path):
    applied = json.loads(Path(applied_path).read_text())
    s = applied.get("summary", {})
    with open(output_path, "w") as f:
        f.write(f"STEP 4 — ECO APPLIED (Round {rnd})\n")
        f.write(f"Tag: {tag}  |  JIRA: {jira}\n")
        f.write("=" * 80 + "\n")
        f.write(
            f"Summary: {s.get('applied',0)} applied / {s.get('inserted',0)} inserted / "
            f"{s.get('already_applied',0)} already_applied / "
            f"{s.get('skipped',0)} skipped / {s.get('verify_failed',0)} verify_failed\n\n"
        )
        for stage in ("Synthesize", "PrePlace", "Route"):
            f.write(f"[{stage}]\n")
            for e in applied.get(stage, []):
                ct = e.get('change_type', '?')
                status = e.get('status', '?')
                f.write(f"  {status:15s} {s4_name(e):40s} type={ct}\n")
                f.write(s4_detail_line(e, status))
            f.write("\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    p3 = sub.add_parser("step3")
    p3.add_argument("--study",  required=True)
    p3.add_argument("--tag",    required=True)
    p3.add_argument("--jira",   required=True)
    p3.add_argument("--tile",   required=True)
    p3.add_argument("--output", required=True)

    p4 = sub.add_parser("step4")
    p4.add_argument("--applied", required=True)
    p4.add_argument("--tag",     required=True)
    p4.add_argument("--jira",    required=True)
    p4.add_argument("--round",   required=True)
    p4.add_argument("--output",  required=True)

    args = p.parse_args()
    try:
        if args.cmd == "step3":
            gen_step3(args.study, args.tag, args.jira, args.tile, args.output)
            print(f"ECO_RPT_GENERATED: step3 → {args.output}")
        elif args.cmd == "step4":
            gen_step4(args.applied, args.tag, args.jira, args.round, args.output)
            print(f"ECO_RPT_GENERATED: step4 → {args.output}")
    except Exception as e:
        print(f"ECO_RPT_FAILED: {args.cmd}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
