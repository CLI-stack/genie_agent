#!/usr/bin/env python3
"""eco_build_round_html.py — Deterministic per-round HTML builder for ECO email.

Reads ALL per-round artifacts (handoff, FM verify, applied, evidence walk,
xstage compare, analysis, contract check, fixer_state, pre-FM check rpt) and
emits a structured HTML for the per-round email summary.

Replaces the inline-HTML template that lived in ROUND_ORCHESTRATOR.md Step 6a.
The script is idempotent and verdict-aware.

Output: <BASE_DIR>/data/<TAG>_eco_report_round<ROUND>.html

Usage:
    python3 script/eco_scripts/eco_build_round_html.py \\
        --tag <TAG> --round <ROUND> --base-dir <BASE_DIR> --jira <JIRA> --tile <TILE>
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

# Shared design constants — keeps round + final emails visually consistent
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from eco_html_design import (
    esc as _esc_shared, badge, section_wrap, tbl as design_tbl,
    pre_block as design_pre, html_wrap
)

# Email-safe table header cell (avoids double-quote in f-string)
def _th(text): return f'<th>{text}</th>'
def _th_row(*headers): return '<tr>' + ''.join(_th(h) for h in headers) + '</tr>'




# -----------------------------------------------------------------------------
# I/O helpers
# -----------------------------------------------------------------------------
def read_json(p: Path) -> Any:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {"_read_error": str(e)}


def read_text(p: Path, max_lines: int | None = None) -> str:
    if not p.exists():
        return ""
    try:
        text = p.read_text(errors="replace")
        if max_lines:
            text = "\n".join(text.splitlines()[:max_lines])
        return text
    except OSError as e:
        return f"[READ ERROR: {e}]"


def esc(s: Any) -> str:
    """HTML-escape a value, coerce to string."""
    return html.escape(str(s)) if s is not None else ""


def _parse_fm_verify_rpt(rpt_path: Path) -> dict | None:
    """Parse eco_step6_fm_verify_round<N>.rpt to extract per-target FM results.
    Returns a dict compatible with fm_results_table(), or None if not found.
    eco_fm_verify.json is overwritten each round so this RPT is the only
    round-specific source of truth for historical round results.
    """
    text = read_text(rpt_path)
    if not text:
        return None
    result = {"per_target": {}}
    for tgt in ("FmEqvEcoSynthesizeVsSynRtl",
                "FmEqvEcoPrePlaceVsEcoSynthesize",
                "FmEqvEcoRouteVsEcoPrePlace"):
        # Match lines like: FmEqvEcoSynthesizeVsSynRtl : FAIL  [failing_count: 3071]
        import re as _re
        m = _re.search(
            rf'{_re.escape(tgt)}\s*[:\|]\s*(PASS|FAIL|ABORT\S*)'
            rf'(?:'
            rf'\s*\[failing(?:_count)?:\s*(\d+)\]'           # [failing_count: N] or [failing: N]
            rf'|\s*\([^)]*?failing\s*=\s*(\d+)[^)]*\)'        # (...failing=N...) — Step 6 RPT format
            rf'|\s*\([^)]*?(\d+)\s+failing'                   # (... N failing point...)
            rf'|\s*\|\s*(\d+)'                                # | N (pipe-separated)
            rf'|\s*\[(\d+)\s+failing'                         # [N failing DFFs/points...]
            rf'|\s+failing=(\d+)'                             # failing=N (space-separated k=v)
            rf'|\s+Failing points \((\d+)\)'                  # Failing points (N): on next line
            rf')?',
            text
        )
        if m:
            status = m.group(1)
            raw_count = m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6) or m.group(7) or m.group(8)
            count = int(raw_count) if raw_count else (0 if status == "PASS" else "—")
            result["per_target"][tgt] = {"status": status, "failing_count": count}
    return result if result["per_target"] else None


# -----------------------------------------------------------------------------
# Verdict banner
# -----------------------------------------------------------------------------
VERDICT_COLORS = {
    "CONVERGED":          "#2e7d32",   # green
    "ADVANCE_NEXT_ROUND": "#1565c0",   # blue
    "RERUN_SAME_ROUND":   "#ef6c00",   # orange
    "FAILED":             "#c62828",   # red
    "UNKNOWN":            "#616161",   # gray
}


def verdict_banner(analysis: dict | None, fixer_state: dict | None,
                   round_n: int) -> str:
    if not analysis:
        return _banner("UNKNOWN", "FM analysis JSON not available", "?", round_n, 0)
    verdict = analysis.get("loop_verdict", "UNKNOWN")
    reason  = analysis.get("verdict_reason", "")
    next_r  = analysis.get("next_round", round_n)
    rerun_count = (fixer_state or {}).get("rerun_count_in_round", 0)
    return _banner(verdict, reason, next_r, round_n, rerun_count)


def _banner(verdict: str, reason: str, next_r: Any, round_n: int, rerun_count: int) -> str:
    color = VERDICT_COLORS.get(verdict, "#616161")
    next_label = (f"Same round (retry {rerun_count}/3)"
                  if verdict == "RERUN_SAME_ROUND"
                  else f"Round {next_r}" if verdict == "ADVANCE_NEXT_ROUND"
                  else "—")
    banner_cls = {"CONVERGED":"banner-pass","ADVANCE_NEXT_ROUND":"banner-info",
                  "RERUN_SAME_ROUND":"banner-warn"}.get(verdict, "banner-fail")
    return (
        f"\n<div class='banner {banner_cls}'>\n"
        f"<strong>{esc(verdict)}</strong>"
        f" &nbsp;<span class='meta'>round={round_n}</span><br>\n"
        f"<span style='font-size:12px'>{esc(reason)}</span><br>\n"
        f"<span class='meta'>Next: {esc(next_label)}</span>\n"
        f"</div>\n"
    )


# -----------------------------------------------------------------------------
# FM Results table
# -----------------------------------------------------------------------------
def fm_results_table(fm_verify: dict | None) -> str:
    if not fm_verify:
        return "<p><i>FM verify JSON not available.</i></p>"
    # Support both nested schema (per_target dict) and flat top-level schema
    nested = fm_verify.get("per_target", {}) or {}
    rows = []
    for tgt in ("FmEqvEcoSynthesizeVsSynRtl",
                "FmEqvEcoPrePlaceVsEcoSynthesize",
                "FmEqvEcoRouteVsEcoPrePlace"):
        det = nested.get(tgt) or fm_verify.get(tgt)
        if isinstance(det, dict):
            status = det.get("status") or det.get("verdict", "?")
            # Support both failing_count int and failing_points list
            if "failing_count" in det:
                count = det["failing_count"]
            elif "failing_points" in det:
                count = len(det["failing_points"])
            else:
                count = "—"
        else:
            status = str(det) if det is not None else "MISSING"
            count  = "—"
        rows.append(
            f"<tr><td>{esc(tgt)}</td>"
            f"<td>{badge(status)}</td>"
            f"<td>{esc(count)}</td></tr>"
        )
    return f"""
<table>
  <tr><th>Target</th><th>Status</th><th>Failing Points</th></tr>
  {''.join(rows)}
</table>
"""


# -----------------------------------------------------------------------------
# Failing points detail — reads from evidence walk (round-specific) first,
# falls back to fm_verify failing_points list
# -----------------------------------------------------------------------------
def failing_points_detail(fm_verify: dict | None, max_lines: int = 30,
                          evidence: dict | None = None) -> str:
    blocks = []
    FM_TGTS = ("FmEqvEcoSynthesizeVsSynRtl",
                "FmEqvEcoPrePlaceVsEcoSynthesize",
                "FmEqvEcoRouteVsEcoPrePlace")

    # Primary: evidence walk per_target dossiers (round-specific)
    if evidence and evidence.get("per_target"):
        for tgt in FM_TGTS:
            tgt_data = evidence["per_target"].get(tgt, {})
            if tgt_data.get("status") != "FAIL":
                continue
            diag = tgt_data.get("failing_diagnostics", {})
            ps = diag.get("pattern_summary", {})
            dossiers = diag.get("per_dff_dossiers", [])
            total = diag.get("failing_count", len(dossiers))
            if not dossiers and not ps:
                continue
            lines = []
            # Show pattern summary first
            if ps:
                top_mod = ps.get("top_failing_modules", [])
                dom = ps.get("dominant_pattern", "")
                sig = ps.get("dominant_signal", "")
                lines.append(f"Pattern: {dom}" + (f" (signal: {sig})" if sig else ""))
                if top_mod:
                    lines.append(f"Top scope: {top_mod[0].get('scope','')} ({top_mod[0].get('count',0)}/{total} DFFs)")
            # Show sample DFF paths
            for d in dossiers[:max_lines]:
                lines.append(d.get("impl_path", d.get("ref_path", str(d))))
            more = total - len(dossiers[:max_lines])
            if more > 0:
                lines.append(f"... + {more} more ({total} total)")
            listing = "\n".join(esc(l) for l in lines)
            blocks.append(f"<h4>{esc(tgt)}</h4>"
                          f"<pre>{listing}</pre>")
        if blocks:
            return "\n".join(blocks)

    # Fallback: fm_verify failing_points list (may be from latest round, not this round)
    if not fm_verify:
        return "<p><i>No failing points data.</i></p>"
    nested = fm_verify.get("per_target", {}) or {}
    source = nested if nested else fm_verify
    for tgt, det in source.items():
        if not isinstance(det, dict):
            continue
        fp = det.get("failing_points", [])
        if not fp:
            continue
        listing = "\n".join(esc(p) for p in fp[:max_lines])
        more = f"\n... + {len(fp) - max_lines} more" if len(fp) > max_lines else ""
        blocks.append(f"<h4>{esc(tgt)}</h4><pre style='background:#fafafa;padding:8px;font-size:11px'>{listing}{more}</pre>")
    if not blocks:
        return "<p><i>No failing points (or not yet diagnosed).</i></p>"
    return "\n".join(blocks)


# -----------------------------------------------------------------------------
# ECO changes summary
# -----------------------------------------------------------------------------
def eco_changes_summary(eco_applied: dict | None) -> str:
    if not eco_applied:
        return "<p><i>eco_applied JSON not available.</i></p>"
    summary = eco_applied.get("summary", {})
    if not summary:
        # Fall back to manually counting
        counts = {"APPLIED": 0, "INSERTED": 0, "SKIPPED": 0,
                  "ALREADY_APPLIED": 0, "VERIFY_FAILED": 0}
        for stage, entries in eco_applied.items():
            if stage == "summary" or not isinstance(entries, list):
                continue
            for e in entries:
                st = e.get("status", "?")
                if st in counts:
                    counts[st] += 1
        summary = counts
    chips = "  ".join(
        "<span class='chip'>"
        f"<b>{esc(k)}:</b> {esc(v)}</span>"
        for k, v in summary.items()
    )
    return f"<p>{chips}</p>"


# -----------------------------------------------------------------------------
# Evidence walk summary signals
# -----------------------------------------------------------------------------
def evidence_summary_section(evidence: dict | None) -> str:
    if not evidence:
        return "<p><i>Evidence walk JSON not available.</i></p>"
    signals = evidence.get("summary_signals", [])
    if not signals:
        return "<p><i>No summary signals.</i></p>"
    # Check if any actionable signals exist (only critical/high matter)
    actionable = [s for s in signals if s.get("level") in ("critical", "high")]
    if not actionable:
        return "<p><i>No critical/high signals — see FM Results table for diagnosis.</i></p>"
    by_level: dict[str, list[dict]] = {}
    for s in signals:
        by_level.setdefault(s.get("level", "info"), []).append(s)
    out = []
    badge = {"critical": "#c62828", "high": "#ef6c00"}
    # Only show critical and high — info signals are not actionable
    for lvl in ("critical", "high"):
        items = by_level.get(lvl, [])
        if not items:
            continue
        out.append(f"<h4>{lvl.upper()} ({len(items)})</h4>")
        out.append("<ul style='margin-top:0'>")
        for s in items[:15]:
            out.append(f"<li><b>{esc(s.get('type'))}</b>: {esc(s.get('hint',''))}")
            extras = [(k, v) for k, v in s.items() if k not in ("level", "type", "hint")]
            if extras:
                out.append("<br><span style='color:#616161;font-size:11px'>")
                out.append(", ".join(f"{esc(k)}={esc(v)}" for k, v in extras))
                out.append("</span>")
            out.append("</li>")
        if len(items) > 15:
            out.append(f"<li><i>... + {len(items) - 15} more</i></li>")
        out.append("</ul>")
    # Also surface tune directives status
    tune = evidence.get("tune_directives_status", {})
    if tune:
        rows = "".join(
            f"<tr><td>{esc(t)}</td><td style='text-align:right'>{len(c)}</td></tr>"
            for t, c in tune.get("user_added_constants_per_target", {}).items()
        )
        if rows:
            out.append("<h4>Tune directives applied (set_constant)</h4>")
            out.append('<table>'
                       '<tr><th>Target</th><th># constants</th></tr>'
                       + rows + '</table>')
    return "\n".join(out)


# -----------------------------------------------------------------------------
# Cross-stage compare deltas
# -----------------------------------------------------------------------------
def xstage_section(xstage: dict | None) -> str:
    if not xstage:
        return "<p><i>Cross-stage compare not available.</i></p>"
    if xstage.get("skipped"):
        return f"<p><i>Skipped: {esc(xstage.get('reason',''))}</i></p>"
    per_dff = xstage.get("per_failing_dff", {})
    if not per_dff:
        return "<p><i>No failing DFFs analyzed.</i></p>"
    blocks = []
    for inst, dff in per_dff.items():
        d = dff.get("deltas", {})
        rows = []
        if d.get("pin_changes"):
            for pc in d["pin_changes"]:
                if isinstance(pc, str):
                    # String format: plain description
                    rows.append(f"<tr><td colspan='2'>{esc(pc)}</td></tr>")
                elif isinstance(pc, dict):
                    # Dict format: {pin, stages}
                    stages = " | ".join(f"<b>{esc(s)}</b>={esc(v)}"
                                        for s, v in pc.get("stages", {}).items())
                    rows.append(f"<tr><td>{esc(pc.get('pin','?'))} pin diverges</td><td>{stages}</td></tr>")
        wps = d.get("wire_present_per_stage", {})
        if wps:
            if isinstance(wps, dict):
                # Dict format: {wire_name: {Synthesize: bool, PrePlace: bool, Route: bool}}
                for wire, presence in list(wps.items())[:5]:
                    stages = " | ".join(f"{esc(s)}={'✓' if presence.get(s) else '✗'}"
                                        for s in ("Synthesize", "PrePlace", "Route"))
                    rows.append(f"<tr><td>wire <code>{esc(wire)}</code></td><td>{stages}</td></tr>")
            elif isinstance(wps, list):
                # List format: [{wire, Synthesize, PrePlace, Route}]
                for w in wps[:5]:
                    stages = " | ".join(f"{esc(s)}={esc(w.get(s))}"
                                        for s in ("Synthesize", "PrePlace", "Route") if s in w)
                    rows.append(f"<tr><td>wire <code>{esc(w.get('wire'))}</code></td><td>{stages}</td></tr>")
        if d.get("cell_blackboxed"):
            for bb in d["cell_blackboxed"][:5]:
                if isinstance(bb, dict):
                    rows.append(f"<tr><td>cell <code>{esc(bb.get('cell'))}</code> blackboxed</td>"
                                f"<td>missing in: {esc(str(bb.get('missing_in')))}</td></tr>")
                elif isinstance(bb, str):
                    rows.append(f"<tr><td colspan='2'>blackboxed: {esc(bb)}</td></tr>")
        if not rows:
            blocks.append(f"<h4><code>{esc(inst)}</code></h4>"
                          f"<p style='color:#616161;font-size:12px;margin:0'><i>No structural deltas (cone match across stages)</i></p>")
        else:
            tbl_attr = ''
            blocks.append(f"<h4><code>{esc(inst)}</code></h4>"
                          f"<table>{''.join(rows)}</table>")
    return "\n".join(blocks)


# -----------------------------------------------------------------------------
# Diagnosis + reasoning + alternatives
# -----------------------------------------------------------------------------
def diagnosis_section(analysis: dict | None) -> str:
    if not analysis:
        return "<p><i>Analysis JSON not available.</i></p>"
    fmode = analysis.get("failure_mode", "?")
    diag  = analysis.get("diagnosis", "")
    reasoning = analysis.get("root_cause_reasoning", "")
    alts = analysis.get("alternatives_considered", [])
    out = [
        f"<p><b>Failure Mode:</b> <code>{esc(fmode)}</code></p>\n",
        f"<p><b>Diagnosis:</b><br>\n{esc(diag[:300])}</p>\n"
    ]
    if reasoning:
        out.append(
            f"<h4>Root Cause Reasoning</h4>\n"
            f"<div class='box'>\n{esc(reasoning[:600])}\n</div>\n"
        )
    if alts:
        rows = "\n".join(
            f"  <tr>\n"
            f"    <td>{esc(a.get('hypothesis','') if isinstance(a,dict) else a)}</td>\n"
            f"    <td>{esc(a.get('rejected_because','') if isinstance(a,dict) else '')}</td>\n"
            f"  </tr>"
            for a in alts
        )
        out.append(
            f"<h4>Alternatives Considered</h4>\n"
            f"<table>\n{_th_row('Hypothesis','Rejected because')}\n{rows}\n</table>\n"
        )
    return "\n".join(out)


# -----------------------------------------------------------------------------
# Revised changes + evidence_for_studier
# -----------------------------------------------------------------------------
def revised_changes_section(analysis: dict | None) -> str:
    if not analysis:
        return "<p><i>Analysis JSON not available.</i></p>"
    rc = analysis.get("revised_changes", [])
    if not rc:
        return "<p><i>No revised changes (CONVERGED or empty).</i></p>"
    blocks = []
    for i, c in enumerate(rc):
        action = c.get("action", "?")
        cell = c.get("cell_name", c.get("signal_name", "?"))
        stage = c.get("stage", "?")
        rationale = c.get("rationale", "")
        fallback = c.get("fallback_action", "")

        e4s = c.get("evidence_for_studier", {})
        recipes = e4s.get("candidate_fix_recipes", [])
        constraints = e4s.get("constraints", {})
        fdp = e4s.get("first_divergent_point", {})

        # Top recipe summary
        top_recipe_html = "<i>no recipes</i>"
        if recipes:
            r = recipes[0]
            top_recipe_html = (f"<code>{esc(r.get('kind'))}</code> "
                               f"(score={esc(r.get('applicability_score'))})")

        # Constraints chips
        scope = constraints.get("scope_module", "")
        do_not_mods = constraints.get("do_not_modify_modules", [])
        scope_chip = (f"<span style='background:#e8f5e9;padding:2px 6px;border-radius:8px;font-size:11px'>"
                      f"scope: <code>{esc(scope)}</code></span>" if scope else "")
        avoid_chip = (f"<span style='background:#ffebee;padding:2px 6px;border-radius:8px;font-size:11px;margin-left:6px'>"
                      f"avoid: {', '.join(esc(m) for m in do_not_mods)}</span>" if do_not_mods else "")

        # Divergent point summary
        fdp_html = ""
        if fdp:
            fdp_html = (f"<div style='font-size:12px;color:#616161;margin-top:4px'>"
                        f"first divergent point: <b>{esc(fdp.get('kind'))}</b> @ <code>{esc(fdp.get('what'))}</code>"
                        f"</div>")

        blocks.append(f"""
<div style="border:1px solid #ddd;border-radius:4px;padding:10px;margin-bottom:8px">
  <div style="font-weight:bold">[{i+1}] <code>{esc(action)}</code> on <code>{esc(cell)}</code> (stage={esc(stage)})</div>
  <div style="font-size:13px;margin-top:6px"><b>Rationale:</b> {esc(rationale)}</div>
  <div style="font-size:12px;color:#616161;margin-top:4px">
    <b>Fallback:</b> <code>{esc(fallback)}</code> &nbsp;&middot;&nbsp;
    <b>Top recipe:</b> {top_recipe_html} &nbsp;&middot;&nbsp;
    <b>Recipes total:</b> {len(recipes)}
  </div>
  {fdp_html}
  <div style="margin-top:6px">{scope_chip}{avoid_chip}</div>
</div>
""")
    return "\n".join(blocks)


# -----------------------------------------------------------------------------
# Contract compliance
# -----------------------------------------------------------------------------
def contract_section(contract: dict | None) -> str:
    if not contract:
        return "<p><i>Contract check not run (or JSON missing).</i></p>"
    compliant = contract.get("compliant", False)
    n_viol = contract.get("violation_count", 0)
    color = "#2e7d32" if compliant else "#c62828"
    label = "PASS" if compliant else "FAIL"
    summary = (f"<p><b style='color:{color}'>Contract: {label}</b> &middot; "
               f"actionable changes: {esc(contract.get('actionable_changes',0))} &middot; "
               f"violations: {esc(n_viol)} &middot; "
               f"strict: {esc(contract.get('strict', False))}</p>")
    if compliant:
        return summary
    rows = "".join(
        f"<tr><td><code>{esc(v.get('ctx'))}</code></td><td>{esc(v.get('violation'))}</td></tr>"
        for v in contract.get("violations", [])[:30]
    )
    _TA = ''
    return (f"{summary}<table>"
            f"{_th_row('Context','Violation')}{rows}</table>")


# -----------------------------------------------------------------------------
# Companion files
# -----------------------------------------------------------------------------
def companion_files_section(base_dir: Path, ai_eco_flow_dir: Path | None,
                            tag: str, round_n: int) -> str:
    items = [
        ("evidence walk JSON",          base_dir / "data" / f"{tag}_eco_fm_evidence_round{round_n}.json"),
        ("xstage compare JSON",         base_dir / "data" / f"{tag}_eco_fm_xstage_round{round_n}.json"),
        ("FM analysis JSON",            base_dir / "data" / f"{tag}_eco_fm_analysis_round{round_n}.json"),
        ("contract check JSON",         base_dir / "data" / f"{tag}_eco_fm_analysis_round{round_n}.contract_check.json"),
        ("eco_applied JSON",            base_dir / "data" / f"{tag}_eco_applied_round{round_n}.json"),
    ]
    if ai_eco_flow_dir:
        items += [
            ("evidence walk RPT",       ai_eco_flow_dir / f"{tag}_eco_step6_evidence_walk_round{round_n}.rpt"),
            ("xstage compare RPT",      ai_eco_flow_dir / f"{tag}_eco_step6_xstage_compare_round{round_n}.rpt"),
            ("FM analysis RPT",         ai_eco_flow_dir / f"{tag}_eco_step6_fm_analysis_round{round_n}.rpt"),
            ("contract check RPT",      ai_eco_flow_dir / f"{tag}_eco_step6_evidence_contract_check_round{round_n}.rpt"),
            ("pre-FM check RPT",        ai_eco_flow_dir / f"{tag}_eco_step5_pre_fm_check_round{round_n}.rpt"),
            ("FM verify RPT",           ai_eco_flow_dir / f"{tag}_eco_step6_fm_verify_round{round_n}.rpt"),
        ]
    rows = []
    for label, path in items:
        exists = "✓" if path.exists() else "✗"
        cls = "pass" if path.exists() else "fail"
        fname = path.name  # filename only — keeps lines short
        rows.append(
            f"\n  <tr>\n"
            f"    <td><span class='{cls}'>{exists}</span> {esc(label)}</td>\n"
            f"    <td><code>{esc(fname)}</code></td>\n"
            f"  </tr>"
        )
    return f"<table>\n{_th_row('Artifact','File')}\n{''.join(rows)}\n</table>\n"


# -----------------------------------------------------------------------------
# Main HTML assembly
# -----------------------------------------------------------------------------
def build_html(args) -> str:
    base_dir = Path(args.base_dir)
    tag, round_n = args.tag, args.round
    data_dir = base_dir / "data"

    ai_eco_flow_dir = Path(args.ai_eco_flow_dir) if args.ai_eco_flow_dir else None

    # Load all artifacts (best-effort; some may be missing depending on verdict)
    handoff      = read_json(data_dir / f"{tag}_round_handoff.json")
    # eco_fm_verify.json is overwritten each round — use round-specific RPT to get
    # the correct results for THIS round (not the latest round's merged data).
    fm_verify = _parse_fm_verify_rpt(
        data_dir / f"{tag}_eco_step6_fm_verify_round{round_n}.rpt"
    ) or read_json(data_dir / f"{tag}_eco_fm_verify.json")
    eco_applied  = read_json(data_dir / f"{tag}_eco_applied_round{round_n}.json")
    evidence     = read_json(data_dir / f"{tag}_eco_fm_evidence_round{round_n}.json")
    xstage       = read_json(data_dir / f"{tag}_eco_fm_xstage_round{round_n}.json")
    analysis     = read_json(data_dir / f"{tag}_eco_fm_analysis_round{round_n}.json")
    contract     = read_json(data_dir / f"{tag}_eco_fm_analysis_round{round_n}.contract_check.json")
    fixer_state  = read_json(data_dir / f"{tag}_eco_fixer_state")

    # pre-FM check rpt (text, optional)
    pre_fm_rpt_path = (ai_eco_flow_dir / f"{tag}_eco_step5_pre_fm_check_round{round_n}.rpt"
                       if ai_eco_flow_dir else
                       data_dir / f"{tag}_eco_step5_pre_fm_check_round{round_n}.rpt")
    pre_fm_text = read_text(pre_fm_rpt_path, max_lines=30)

    eco_fm_tag = (handoff or {}).get("eco_fm_tag", "?")
    pre_fm_check_failed = (handoff or {}).get("pre_fm_check_failed", False)
    overall_status = (handoff or {}).get("status", "?")

    # Subject line for email (commented in HTML for genie_cli to pick up)
    verdict = (analysis or {}).get("loop_verdict", "UNKNOWN")
    subject = f"[ECO Round {round_n}] {tag} {overall_status} [{verdict}] - {args.jira} ({args.tile})"

    # Title bar
    # Assemble with dark theme via html_wrap
    title_bar = (
        f"<h1>ECO Round {round_n} — JIRA {esc(args.jira)} ({esc(args.tile)})</h1>"
        f"<p class='meta'>Tag: {esc(tag)} &nbsp;|&nbsp; FM Tag: {esc(eco_fm_tag)} &nbsp;|&nbsp; Status: {esc(overall_status)}</p>"
    )

    if pre_fm_check_failed:
        body_sections = [
            section_wrap("Pre-FM Check FAILED — FM was not submitted this round",
                         design_pre(pre_fm_text)),
        ]
    else:
        body_sections = [
            section_wrap("1. FM Results",                fm_results_table(fm_verify)),
            section_wrap("2. Failing Points Detail",     failing_points_detail(fm_verify, evidence=evidence)),
            section_wrap("3. Evidence Walk Summary",     evidence_summary_section(evidence)),
            section_wrap("4. Cross-Stage Netlist Deltas", xstage_section(xstage)),
            section_wrap("5. ECO Changes Applied",       eco_changes_summary(eco_applied)),
            section_wrap("6. Pre-FM Check",              design_pre(pre_fm_text, 2000)),
            section_wrap("7. Failure Diagnosis",         diagnosis_section(analysis)),
            section_wrap("8. Revised Changes",           revised_changes_section(analysis)),
            section_wrap("9. Analyzer Evidence Contract", contract_section(contract)),
        ]

    body_sections += [
        section_wrap("10. Companion Artifacts", companion_files_section(base_dir, ai_eco_flow_dir, tag, round_n)),
        f"<hr><p class='meta'>Generated by eco_build_round_html.py — Round {round_n} | {esc(tag)}</p>",
    ]

    body = title_bar + verdict_banner(analysis, fixer_state, round_n) + "\n".join(body_sections)
    return html_wrap(subject, body, tag=tag, jira=args.jira, tile=args.tile)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tag",      required=True)
    p.add_argument("--round",    type=int, required=True)
    p.add_argument("--base-dir", required=True)
    p.add_argument("--jira",     required=True)
    p.add_argument("--tile",     required=True)
    p.add_argument("--ai-eco-flow-dir", default=None,
                   help="If set, also list AI_ECO_FLOW_DIR rpts in companion files section")
    p.add_argument("--output",   default=None,
                   help="Output HTML path (default: <BASE_DIR>/data/<TAG>_eco_report_round<N>.html)")
    args = p.parse_args()

    out_path = Path(args.output) if args.output else (
        Path(args.base_dir) / "data" / f"{args.tag}_eco_report_round{args.round}.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(args))
    print(f"ECO_RPT_GENERATED: round HTML → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
