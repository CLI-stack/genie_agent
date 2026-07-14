#!/usr/bin/env python3
"""eco_build_final_html.py — Comprehensive final HTML report for FINAL_ORCHESTRATOR.

VS Code dark theme. CSS in <head><style> — confirmed working in Outlook.

Usage:
    python3 script/eco_scripts/eco_build_final_html.py \
        --tag <TAG> --jira <JIRA> --tile <TILE> \
        --base-dir <BASE_DIR> --total-rounds <N> \
        --ai-eco-flow-dir <AI_ECO_FLOW_DIR> \
        [--ref-dir <REF_DIR>] [--output <path>]
"""
import argparse, json, os, re, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eco_html_design import esc, badge, tbl, pre_block, html_wrap, section_wrap
from eco_fm_targets import detect_targets, target_to_stage

# Ordered Eco target triple for FM-result display. Defaults to canonical plain
# names; build_html() overrides with the tile's real triple (plain or UPF) once
# ref_dir is resolved, so UPF-named projects (soundwave) render correctly.
ECO_TARGETS = (
    "FmEqvEcoSynthesizeVsSynRtl",
    "FmEqvEcoPrePlaceVsEcoSynthesize",
    "FmEqvEcoRouteVsEcoPrePlace",
)


def read(p):
    try:
        p = Path(p)
        return p.read_text(errors="replace") if p.exists() else ""
    except: return ""

def readj(p):
    try:
        p = Path(p)
        return json.loads(p.read_text()) if p.exists() else None
    except: return None

def parse_fm_rpt(rpt_text):
    results = []
    for tgt in ECO_TARGETS:
        m = re.search(
            rf'{re.escape(tgt)}\s*[:\|]\s*(PASS|FAIL|ABORT\S*)'
            rf'(?:\s*\[failing(?:_count)?:\s*(\d+)\]'
            rf'|\s*\([^)]*?(\d+)\s+failing'
            rf'|\s*\|\s*(\d+))?',
            rpt_text)
        if m:
            st  = m.group(1)
            cnt = int(m.group(2) or m.group(3) or m.group(4) or 0)
            short = target_to_stage(tgt) or tgt.replace("FmEqvEco","").replace("VsEco"," vs Eco").replace("VsSynRtl"," vs SynRtl")
            results.append((short, st, cnt))
    return results


def build_html(args):
    tag   = args.tag
    jira  = args.jira
    tile  = args.tile
    base  = Path(args.base_dir)
    total = args.total_rounds
    data  = base / "data"
    ai_flow = Path(args.ai_eco_flow_dir) if args.ai_eco_flow_dir else None

    handoff     = readj(data / f"{tag}_round_handoff.json") or {}
    rtl_diff    = readj(data / f"{tag}_eco_rtl_diff.json") or {}
    study_json  = readj(data / f"{tag}_eco_preeco_study.json") or {}
    fixer_state = readj(data / f"{tag}_eco_fixer_state") or {}
    ref_dir     = args.ref_dir or handoff.get("ref_dir", "")

    # Resolve the tile's real Eco target triple for display (infix-tolerant).
    global ECO_TARGETS
    if ref_dir:
        ECO_TARGETS = tuple(detect_targets(ref_dir, "Eco"))

    final_verdict = handoff.get("status", "UNKNOWN")
    next_phase    = handoff.get("next_phase", "UNKNOWN")
    verdict_word  = ("PASS" if "PASS" in final_verdict.upper() else
                     "MAX_ROUNDS" if "MAX" in final_verdict.upper() else "FAIL")
    banner_cls    = {"PASS": "banner-pass"}.get(verdict_word,
                    "banner-fail" if verdict_word == "FAIL" else "banner-warn")
    subject = f"[ECO {jira} FINAL] {tile} — {verdict_word} ({tag})"

    # ── HEADER ────────────────────────────────────────────────────────────
    header = (
        f"<h1>ECO Final Report — JIRA {esc(jira)} ({esc(tile)})</h1>"
        f"<div class='box'>"
        + tbl(["Tag","JIRA","Tile","Total Rounds","Final Verdict"],
               [[esc(tag), esc(jira), esc(tile), str(total), badge(verdict_word)]])
        + (f"<p class='meta'><b>TileBuilder:</b> {esc(ref_dir)}</p>" if ref_dir else "")
        + "</div>"
    )

    # ── VERDICT BANNER ─────────────────────────────────────────────────────
    banner = (
        f"<div class='banner {banner_cls}'>"
        f"<span style='font-size:22px;font-weight:bold'>{esc(verdict_word)}</span>"
        f"<span style='font-size:13px;margin-left:12px;opacity:0.85'>{esc(final_verdict)}</span>"
        f"<br><span class='meta'>next_phase: {esc(next_phase)}</span>"
        f"</div>"
    )

    # ── FINAL FM STATUS ────────────────────────────────────────────────────
    fm_status_tbl = ""
    fm_rnd = 0
    for rnd in range(total, 0, -1):
        rpt = read(data / f"{tag}_eco_step6_fm_verify_round{rnd}.rpt")
        if rpt:
            results = parse_fm_rpt(rpt)
            if results:
                fm_rows = [[esc(t), badge(st), str(cnt) if cnt else ("0" if st=="PASS" else "—")]
                           for t, st, cnt in results]
                fm_status_tbl = tbl(["FM Target","Status","Failing Points"], fm_rows)
                fm_rnd = rnd
                break
    fm_section = (f"<h2>Final FM Verification (Round {fm_rnd})</h2>"
                  f"<div class='box'>{fm_status_tbl}</div>" if fm_status_tbl else "")

    # ── STRATEGY TIMELINE ─────────────────────────────────────────────────
    strat_tried  = fixer_state.get("strategies_tried", [])
    fm_per_round = {r["round"]: r for r in fixer_state.get("fm_results_per_round", [])}
    strategy_rows = []
    for st in strat_tried:
        rnd     = st.get("round", "?")
        fm_mode = st.get("failure_mode", "—").split("_")[0]
        diag    = st.get("diagnosis", "—")
        actions = "<br>".join(esc(a) for a in st.get("actions", [])[:5])
        fm_r    = fm_per_round.get(rnd, {})
        fc      = fm_r.get("failing_count", {})
        fm_sum  = (f"S:{fc.get(ECO_TARGETS[0],'—')} "
                   f"PP:{fc.get(ECO_TARGETS[1],'—')} "
                   f"RT:{fc.get(ECO_TARGETS[2],'—')}")
        progress = fm_r.get("progress_note", "")
        strategy_rows.append([str(rnd), esc(fm_mode), esc(diag[:180]),
                               actions, esc(fm_sum),
                               f'<span class="meta">{esc(progress[:120])}</span>' if progress else "—"])
    strategy_section = ""
    if strategy_rows:
        strategy_section = (
            "<h2>Strategy Timeline (from eco_fixer_state)</h2>"
            "<div class='box'>"
            + tbl(["Round","Mode","Diagnosis","Actions","FM Result","Progress"], strategy_rows)
            + "</div>"
        )

    # ── STEP 1 — RTL Diff ─────────────────────────────────────────────────
    changes = rtl_diff.get("changes", [])
    nets    = rtl_diff.get("nets_to_query", [])
    chg_rows = [[esc(c.get("change_type","")),
                 f'<b>{esc(c.get("new_token") or c.get("old_token",""))}</b>',
                 esc(c.get("module_name","")),
                 esc(c.get("fallback_strategy","") or c.get("declaration_type","")),
                 esc(c.get("target_register","") or "—")]
                for c in changes]
    step1 = (
        f"<h2>Step 1 — RTL Diff ({len(changes)} changes)</h2>"
        f"<div class='box'>"
        + tbl(["Change Type","Signal","Module","Strategy/Type","Target Reg"], chg_rows)
        + (f"<p><b>Nets to query ({len(nets)}):</b></p>"
           + tbl(["Net Path","Reason"],
                 [[esc(n.get("net_path","")), esc(n.get("reason","")[:80])] for n in nets])
           if nets else "")
        + "</div>"
    )

    # ── STEP 2 — Fenets ───────────────────────────────────────────────────
    step2 = (
        "<h2>Step 2 — Find Equivalent Nets</h2>"
        f"<div class='box'>{pre_block(read(data / f'{tag}_eco_step2_fenets.rpt'), 3000)}</div>"
    )

    # ── STEP 3 — Study ────────────────────────────────────────────────────
    from collections import Counter as _Counter
    synth_e = study_json.get("Synthesize", [])
    pp_e    = study_json.get("PrePlace", [])
    rt_e    = study_json.get("Route", [])
    ct_s = _Counter(e.get("change_type","?") for e in synth_e)
    ct_p = _Counter(e.get("change_type","?") for e in pp_e)
    ct_r = _Counter(e.get("change_type","?") for e in rt_e)
    all_types = sorted(set(ct_s)|set(ct_p)|set(ct_r))
    sum_rows = [[esc(t), str(ct_s.get(t,0)), str(ct_p.get(t,0)), str(ct_r.get(t,0))]
                for t in all_types]
    sum_rows.append(["<b>Total</b>", f"<b>{len(synth_e)}</b>", f"<b>{len(pp_e)}</b>", f"<b>{len(rt_e)}</b>"])

    gate_rows = [[esc(e.get("instance_name","")), esc(e.get("gate_function","")),
                  esc((e.get("cell_type","") or "").split("D")[0]),
                  esc(e.get("output_net","")), esc(e.get("instance_scope",""))]
                 for e in synth_e if e.get("change_type")=="new_logic_gate"]
    rewire_rows = [[esc(e.get("cell_name","") or e.get("instance_name","")),
                    esc((e.get("cell_type","") or "").split("D")[0]),
                    esc(e.get("pin","")), esc(e.get("old_net","")), esc(e.get("new_net","")),
                    esc(e.get("instance_scope",""))]
                   for e in synth_e if e.get("change_type")=="rewire"]
    port_rows = [[esc(e.get("change_type","")),
                  esc(e.get("signal_name","") or e.get("port_name","") or e.get("net_name","")),
                  esc(e.get("module_name","")), esc(e.get("instance_name","") or e.get("instance_scope",""))]
                 for e in synth_e if e.get("change_type") in
                 ("port_declaration","port_connection","port_promotion","new_port")]
    step3 = (
        f"<h2>Step 3 — Netlist Study ({len(synth_e)} entries/stage)</h2>"
        "<div class='box'>"
        + "<h3>Entry Breakdown per Stage</h3>"
        + tbl(["Change Type","Synthesize","PrePlace","Route"], sum_rows)
        + (f"<h3>New Logic Gates ({len(gate_rows)})</h3>"
           + tbl(["Instance","Gate Function","Cell Family","Output Net","Scope"], gate_rows)
           if gate_rows else "")
        + (f"<h3>Rewires ({len(rewire_rows)})</h3>"
           + tbl(["Cell","Family","Pin","Old Net","New Net","Scope"], rewire_rows)
           if rewire_rows else "")
        + (f"<h3>Port Changes ({len(port_rows)})</h3>"
           + tbl(["Type","Signal/Port","Module","Instance"], port_rows)
           if port_rows else "")
        + "</div>"
    )

    # ── PER-ROUND BREAKDOWN ────────────────────────────────────────────────
    rounds_html = ""
    for rnd in range(1, total + 1):
        apply_j   = readj(data / f"{tag}_eco_applied_round{rnd}.json") or {}
        sm        = apply_j.get("summary", {})
        prefm_j   = readj(data / f"{tag}_eco_pre_fm_check_round{rnd}.json") or {}
        fm_rpt    = read(data / f"{tag}_eco_step6_fm_verify_round{rnd}.rpt")
        analysis  = readj(data / f"{tag}_eco_fm_analysis_round{rnd}.json") or {}

        apply_sum = tbl(["Applied","Inserted","Already Applied","Skipped","Verify Failed"],
                        [[str(sm.get(k,0)) for k in
                          ("applied","inserted","already_applied","skipped","verify_failed")]])

        prefm_pass = prefm_j.get("passed")
        pf_status  = badge("PASS" if prefm_pass else "FAIL")
        pf_note    = ("<br>".join(f'<span class="fail">{esc(f)}</span>'
                                  for f in prefm_j.get("failures",[])[:3])
                      if not prefm_pass else "")

        fm_results = parse_fm_rpt(fm_rpt)
        fm_tbl = (tbl(["FM Target","Status","Failing Points"],
                       [[esc(t), badge(st), str(cnt) if cnt else ("0" if st=="PASS" else "—")]
                        for t, st, cnt in fm_results])
                  if fm_results else '<p class="meta"><i>FM not run</i></p>')

        diag     = analysis.get("diagnosis","")
        reasoning= analysis.get("root_cause_reasoning","")
        loop_v   = analysis.get("loop_verdict","")
        fm_mode  = analysis.get("failure_mode","")
        rev_ch   = analysis.get("revised_changes",[])
        ana_block = ""
        if analysis:
            ana_block = (
                "<h3>FM Analyzer</h3>"
                + tbl(["Field","Value"],
                      [["Loop Verdict", badge(loop_v)],
                       ["Failure Mode", esc(fm_mode)],
                       ["Diagnosis",    esc(diag)]])
                + (f"<h4>Root Cause</h4><div class='box'><p>{esc(reasoning[:500])}</p></div>"
                   if reasoning else "")
            )
            if rev_ch:
                rc_rows = [[esc(rc.get("action","")),
                            esc(rc.get("cell_name") or rc.get("signal_name","—")),
                            esc(rc.get("stage","all")),
                            esc(rc.get("rationale","")[:100])]
                           for rc in rev_ch[:8]]
                ana_block += (f"<h4>Revised Changes ({len(rev_ch)})</h4>"
                              + tbl(["Action","Cell/Signal","Stage","Rationale"], rc_rows))

        rounds_html += (
            f"<h2>Round {rnd}</h2>"
            "<div class='box'>"
            f"<h3>Step 4 — ECO Apply</h3>{apply_sum}"
            f"<h3>Step 5 — Pre-FM Check: {pf_status}</h3>"
            + (f"<p>{pf_note}</p>" if pf_note else "")
            + f"<h3>Step 6 — FM Verification</h3>{fm_tbl}"
            + ana_block
            + "</div>"
        )

    # ── ARTIFACTS ─────────────────────────────────────────────────────────
    artifacts_section = ""
    if ai_flow and ai_flow.exists():
        files = sorted(f for f in ai_flow.iterdir() if f.is_file())
        # Truncate long filenames to keep table cell lines short
        art_rows = [[esc(f.name[:80]), f"{f.stat().st_size//1024} KB"] for f in files]
        artifacts_section = (
            f"<h2>AI_ECO_FLOW Artifacts ({len(files)} files)</h2>"
            f"<p class='meta'>{esc(str(ai_flow))}</p>"
            f"<div class='box'>{tbl(['Filename','Size'], art_rows)}</div>"
        )

    body = (
        header + banner + fm_section + strategy_section
        + "<h2>Study Phase — Steps 1-3</h2>"
        + step1 + step2 + step3
        + "<h2>Apply & FM Phase — Steps 4-6 (Per Round)</h2>"
        + rounds_html
        + artifacts_section
        + f"<hr><p class='meta'>Generated by eco_build_final_html.py — {esc(tag)}</p>"
    )

    return html_wrap(subject, body, tag=tag, jira=jira, tile=tile), subject


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag",          required=True)
    ap.add_argument("--jira",         required=True)
    ap.add_argument("--tile",         required=True)
    ap.add_argument("--base-dir",     required=True)
    ap.add_argument("--total-rounds", type=int, required=True)
    ap.add_argument("--ai-eco-flow-dir", default=None)
    ap.add_argument("--ref-dir",      default=None)
    ap.add_argument("--output",       default=None)
    args = ap.parse_args()

    html_out, subject = build_html(args)
    out_path = Path(args.output) if args.output else (
        Path(args.base_dir) / "data" / f"{args.tag}_eco_report.html")
    out_path.write_text(html_out)
    print(f"ECO_RPT_GENERATED: final HTML → {out_path}")

    if args.ai_eco_flow_dir:
        import shutil
        dst = Path(args.ai_eco_flow_dir) / f"{args.tag}_eco_report.html"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(out_path, dst)
        print(f"  synced to {dst}")


if __name__ == "__main__":
    main()
