# Report Compiler Agent

**PERMISSIONS:** You have FULL READ ACCESS to all files under /proj/. Do not ask for permission - just read the files directly.

Generate a comprehensive HTML analysis report for **ONE specific check type** with FULL COVERAGE for email.

Each report compiler handles ONLY its assigned check type (CDC/RDC, Lint, OR SpgDFT) — never a combined multi-check report.

## ██████████████████████████████████████████████████████
## ██  INLINE STYLES ONLY — NO <style> TAGS EVER       ██
## ██████████████████████████████████████████████████████

**Email clients render `<style>` block content as VISIBLE TEXT.**
**NEVER use `<style>` tags. NEVER use CSS classes. NEVER use flexbox or grid.**

Every single element — including flowchart nodes, arrows, check cards, diamond gate —
MUST use `style="..."` inline attributes. No exceptions.

For multi-column layouts: use `<table>` with `border-collapse:separate; border-spacing:Npx`.
For arrows: use centered `<div>` with inline border tricks.
For the diamond gate: use inline-styled `<div>` with ◆ character — NO CSS transforms.

**Checklist before writing any HTML:**
- [ ] Zero `<style>` tags in the entire document
- [ ] Zero CSS class references anywhere
- [ ] Every `<td>` has explicit `color:` attribute
- [ ] Every layout uses `<table>` (not flexbox/grid)
- [ ] Arrow elements use inline styles only
- [ ] Diamond gate uses inline styles only

## Input

Read findings from JSON files written by each agent. Do NOT rely on context.

```
base_dir: <base_dir>
tag: <tag>
ip: <ip>
ref_dir: <ref_dir>
check_type: <check_type>   ← cdc_rdc | lint | spg_dft  (always exactly one check type)
```

Read ONLY the JSON files for your assigned `check_type` (skip missing files gracefully):

| check_type | Files to Read |
|------------|---------------|
| `cdc_rdc`  | `data/<tag>_precondition_cdc.json`, `data/<tag>_extractor_cdc.json`, `data/<tag>_rtl_cdc_*.json`, `data/<tag>_rtl_rdc_*.json`, `data/<tag>_library_finder.json` |
| `lint`     | `data/<tag>_extractor_lint.json`, `data/<tag>_rtl_lint_*.json` |
| `spg_dft`  | `data/<tag>_precondition_spgdft.json`, `data/<tag>_extractor_spgdft.json`, `data/<tag>_rtl_spgdft_*.json`, `data/<tag>_library_finder.json` |

Use Glob to find all RTL analyzer files for your check type, e.g.:
- CDC/RDC: `data/<tag>_rtl_cdc_*.json` and `data/<tag>_rtl_rdc_*.json`
- Lint: `data/<tag>_rtl_lint_*.json`
- SpgDFT: `data/<tag>_rtl_spgdft_*.json`

## Output

Output filename depends on `check_type`:

| check_type | Output File |
|------------|-------------|
| `cdc_rdc`  | `data/<tag>_analysis_cdc.html` |
| `lint`     | `data/<tag>_analysis_lint.html` |
| `spg_dft`  | `data/<tag>_analysis_spgdft.html` |

Write HTML using the Write tool to the appropriate file above.

## Report Sections

Generate sections for your assigned check type ONLY:

**For `cdc_rdc`:**
1. Header (IP, tag, ref_dir, check type = CDC/RDC)
2. CDC/RDC status card (counts, focus)
3. CDC/RDC section — preconditions, clock pairs, violation types, top violations
4. Recommendations (High / Medium / Low)
5. Configuration files reference

**For `lint`:**
1. Header (IP, tag, ref_dir, check type = Lint)
2. Lint status card (counts, focus)
3. Lint section — violation types, top violations with RTL analysis
4. Recommendations (High / Medium / Low)
5. Configuration files reference

**For `spg_dft`:**
1. Header (IP, tag, ref_dir, check type = SpgDFT)
2. SpgDFT status card (counts, focus)
3. SpgDFT section — blackbox modules, violation types, top violations
4. Recommendations (High / Medium / Low)
5. Configuration files reference

## HTML Template Structure

**ALL inline styles. Tables for layout. No exceptions.**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{check_type_label} Analysis - {ip} @ {dir_name}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif; font-size:14px; color:#e0e0e0; background:#16213e; margin:0; padding:20px;">
<div style="max-width:1100px; margin:0 auto;">

<!-- ══════════════════════════════════════════════════════
     FLOWCHART HEADER — inline styles + table layout only
     ══════════════════════════════════════════════════════ -->

<!-- Header Node -->
<div style="text-align:center; margin-bottom:0;">
  <div style="display:inline-block; background:#1e2d4a; border:2px solid #00d4ff; border-radius:8px; padding:14px 24px; text-align:center; min-width:480px;">
    <div style="font-size:18px; font-weight:700; color:#00d4ff; letter-spacing:1px;">{check_type_label} Analysis Report</div>
    <div style="font-size:12px; color:#c0cfe0; margin-top:6px;">{ip} &nbsp;@&nbsp; {dir_name} &nbsp;|&nbsp; Tag: {tag}</div>
    <div style="font-size:10px; color:#445566; margin-top:3px;">{ref_dir}</div>
  </div>
</div>

<!-- Arrow down -->
<div style="text-align:center; line-height:0; margin:0;">
  <div style="display:inline-block; width:2px; height:18px; background:#2a3f5f; vertical-align:top;"></div>
</div>
<div style="text-align:center; margin:0 0 0 0;">
  <div style="display:inline-block; width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-top:8px solid #2a3f5f;"></div>
</div>

<!-- Bracket top -->
<div style="border-top:1px solid #2a3f5f; border-left:1px solid #2a3f5f; border-right:1px solid #2a3f5f; height:12px; width:88%; margin:0 auto;"></div>

<!-- 4-column Check Status — TABLE layout -->
<div style="background:rgba(0,0,0,0.18); border-left:1px solid #2a3f5f; border-right:1px solid #2a3f5f; padding:14px 18px; width:88%; margin:0 auto; box-sizing:border-box;">
  <div style="text-align:center; font-size:10px; color:#667788; text-transform:uppercase; letter-spacing:2px; margin-bottom:10px;">check status</div>
  <table style="width:100%; border-collapse:separate; border-spacing:10px;">
    <tr>
      <!-- CDC card -->
      <td style="background:#1f1020; border:1px solid #ff6b6b; border-radius:8px; padding:14px 8px; text-align:center; width:25%;">
        <div style="font-size:13px; font-weight:700; color:#ff6b6b; margin-bottom:6px;">CDC</div>
        <div style="font-size:28px; font-weight:700; color:#ff6b6b; font-family:monospace; line-height:1;">{cdc_total}</div>
        <div style="font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#667788; margin-top:3px;">total errors</div>
        <div style="font-size:10px; color:#8899aa; margin-top:6px;">{cdc_filtered} filtered</div>
        <div style="font-size:14px; font-family:monospace; color:#00d4ff;">{cdc_focus} focus</div>
        <!-- badge: #ff6b6b if NEEDS ACTION, #6bcb77 if CLEAN -->
        <div style="display:inline-block; padding:3px 8px; border-radius:3px; font-size:10px; font-weight:700; margin-top:8px; background:{cdc_badge_bg}; color:#1a1a2e;">{cdc_status}</div>
      </td>
      <!-- RDC card -->
      <td style="background:#1f1020; border:1px solid #ff8e8e; border-radius:8px; padding:14px 8px; text-align:center; width:25%;">
        <div style="font-size:13px; font-weight:700; color:#ff8e8e; margin-bottom:6px;">RDC</div>
        <div style="font-size:28px; font-weight:700; color:#ff8e8e; font-family:monospace; line-height:1;">{rdc_total}</div>
        <div style="font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#667788; margin-top:3px;">total errors</div>
        <div style="font-size:10px; color:#8899aa; margin-top:6px;">{rdc_filtered} filtered</div>
        <div style="font-size:14px; font-family:monospace; color:#00d4ff;">{rdc_focus} focus</div>
        <div style="display:inline-block; padding:3px 8px; border-radius:3px; font-size:10px; font-weight:700; margin-top:8px; background:{rdc_badge_bg}; color:#1a1a2e;">{rdc_status}</div>
      </td>
      <!-- Lint card -->
      <td style="background:#0e1f18; border:1px solid #6bcb77; border-radius:8px; padding:14px 8px; text-align:center; width:25%;">
        <div style="font-size:13px; font-weight:700; color:#6bcb77; margin-bottom:6px;">LINT</div>
        <div style="font-size:28px; font-weight:700; color:#6bcb77; font-family:monospace; line-height:1;">{lint_total}</div>
        <div style="font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#667788; margin-top:3px;">total errors</div>
        <div style="font-size:10px; color:#8899aa; margin-top:6px;">{lint_filtered} filtered</div>
        <div style="font-size:14px; font-family:monospace; color:#00d4ff;">{lint_focus} focus</div>
        <div style="display:inline-block; padding:3px 8px; border-radius:3px; font-size:10px; font-weight:700; margin-top:8px; background:{lint_badge_bg}; color:#1a1a2e;">{lint_status}</div>
      </td>
      <!-- SpgDFT card -->
      <td style="background:#1f1020; border:1px solid #ff6b6b; border-radius:8px; padding:14px 8px; text-align:center; width:25%;">
        <div style="font-size:13px; font-weight:700; color:#ff6b6b; margin-bottom:6px;">SpgDFT</div>
        <div style="font-size:28px; font-weight:700; color:#ff6b6b; font-family:monospace; line-height:1;">{spg_total}</div>
        <div style="font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#667788; margin-top:3px;">total errors</div>
        <div style="font-size:10px; color:#8899aa; margin-top:6px;">{spg_filtered} filtered</div>
        <div style="font-size:14px; font-family:monospace; color:#00d4ff;">{spg_focus} focus</div>
        <div style="display:inline-block; padding:3px 8px; border-radius:3px; font-size:10px; font-weight:700; margin-top:8px; background:{spg_badge_bg}; color:#1a1a2e;">{spg_status}</div>
      </td>
    </tr>
  </table>
</div>

<!-- Bracket bottom -->
<div style="border-bottom:1px solid #2a3f5f; border-left:1px solid #2a3f5f; border-right:1px solid #2a3f5f; height:12px; width:88%; margin:0 auto;"></div>

<!-- Arrow down -->
<div style="text-align:center; line-height:0; margin:0;">
  <div style="display:inline-block; width:2px; height:18px; background:#2a3f5f; vertical-align:top;"></div>
</div>
<div style="text-align:center; margin:0;">
  <div style="display:inline-block; width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-top:8px solid #2a3f5f;"></div>
</div>

<!-- Overall Status Gate — NO CSS transforms, just inline styled div -->
<!-- overall_status_text: "NEEDS ACTION" if any focus > 0, else "ALL CLEAN" -->
<!-- gate_border_color: #ff6b6b if NEEDS ACTION, #6bcb77 if ALL CLEAN -->
<div style="text-align:center; padding:10px 0;">
  <div style="display:inline-block; padding:12px 28px; background:#1a1510; border:2px solid {gate_border_color}; border-radius:6px; text-align:center;">
    <div style="font-size:14px; font-weight:700; color:{gate_text_color};">◆ &nbsp; {overall_status_text}</div>
    <div style="font-size:10px; color:#667788; margin-top:3px;">overall result</div>
  </div>
</div>

<!-- Arrow down -->
<div style="text-align:center; line-height:0; margin:0;">
  <div style="display:inline-block; width:2px; height:18px; background:#2a3f5f; vertical-align:top;"></div>
</div>
<div style="text-align:center; margin:0 0 20px 0;">
  <div style="display:inline-block; width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-top:8px solid #2a3f5f;"></div>
</div>

<!-- Detail pointer node -->
<div style="text-align:center; margin-bottom:28px;">
  <div style="display:inline-block; background:#162030; border:2px solid #00d4ff; border-radius:8px; padding:12px 28px; text-align:center; min-width:400px;">
    <div style="font-size:13px; font-weight:600; color:#00d4ff;">Detailed Violation Analysis ↓</div>
    <div style="font-size:11px; color:#8899aa; margin-top:4px;">CDC / RDC · Lint · SpgDFT sections below</div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════
     ALL DATA SECTIONS BELOW — inline styles throughout
     Every <td> MUST have explicit color: attribute
     ══════════════════════════════════════════════════════ -->

<!-- Per-Check Summary Table -->
<div style="background:#1e2d4a; border:1px solid #2a3f5f; border-radius:8px; padding:18px; margin-bottom:18px;">
  <h2 style="margin:0 0 14px 0; font-size:15px; color:#00d4ff; border-bottom:1px solid #2a3f5f; padding-bottom:8px;">Summary</h2>
  <table style="width:100%; border-collapse:collapse; font-size:13px;">
    <tr style="background:#0d1b2a;">
      <th style="padding:10px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase; letter-spacing:1px; width:25%;">Check</th>
      <th style="padding:10px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Total</th>
      <th style="padding:10px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Filtered</th>
      <th style="padding:10px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Focus</th>
      <th style="padding:10px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase; letter-spacing:1px;">Status</th>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:10px 12px; color:#ff6b6b; font-weight:600;">CDC / RDC</td>
      <td style="padding:10px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{cdc_rdc_total}</td>
      <td style="padding:10px 12px; text-align:center; color:#ffd93d; font-family:monospace;">{cdc_rdc_filtered}</td>
      <td style="padding:10px 12px; text-align:center; color:#00d4ff; font-family:monospace;">{cdc_rdc_focus}</td>
      <td style="padding:10px 12px; text-align:center; color:{cdc_rdc_status_color}; font-weight:600;">{cdc_rdc_status}</td>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#1e2d4a;">
      <td style="padding:10px 12px; color:#ffd93d; font-weight:600;">Lint</td>
      <td style="padding:10px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{lint_total}</td>
      <td style="padding:10px 12px; text-align:center; color:#ffd93d; font-family:monospace;">{lint_filtered}</td>
      <td style="padding:10px 12px; text-align:center; color:#00d4ff; font-family:monospace;">{lint_focus}</td>
      <td style="padding:10px 12px; text-align:center; color:{lint_status_color}; font-weight:600;">{lint_status}</td>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:10px 12px; color:#6bcb77; font-weight:600;">SpgDFT</td>
      <td style="padding:10px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{spg_total}</td>
      <td style="padding:10px 12px; text-align:center; color:#ffd93d; font-family:monospace;">{spg_filtered}</td>
      <td style="padding:10px 12px; text-align:center; color:#00d4ff; font-family:monospace;">{spg_focus}</td>
      <td style="padding:10px 12px; text-align:center; color:{spg_status_color}; font-weight:600;">{spg_status}</td>
    </tr>
  </table>
</div>

<!-- ═══════════════════ CDC/RDC SECTION ═══════════════════ -->
<div style="background:#1e2d4a; border:1px solid #2a3f5f; border-radius:8px; padding:20px; margin-bottom:18px;">
  <h2 style="margin:0 0 16px 0; font-size:17px; color:#ff6b6b; border-bottom:2px solid #ff6b6b; padding-bottom:10px;">CDC / RDC Analysis</h2>

  <h3 style="font-size:13px; color:#c0cfe0; text-transform:uppercase; letter-spacing:1px; margin:0 0 10px 0;">Preconditions</h3>
  <table style="width:100%; border-collapse:collapse; font-size:13px; margin-bottom:18px;">
    <tr style="background:#0d1b2a;">
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Type</th>
      <th style="padding:8px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Count</th>
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Signals / Modules</th>
    </tr>
    <!-- Rows: alternate background #192840 / #1e2d4a, every <td> needs color: -->
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:8px 12px; color:#ffd93d;">Inferred Clocks</td>
      <td style="padding:8px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{inferred_clk_count}</td>
      <td style="padding:8px 12px; color:#c0cfe0; font-family:monospace; font-size:11px;">{inferred_clk_signals}</td>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:8px 12px; color:#ffd93d;">Inferred Resets</td>
      <td style="padding:8px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{inferred_rst_count}</td>
      <td style="padding:8px 12px; color:#c0cfe0; font-family:monospace; font-size:11px;">{inferred_rst_signals}</td>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:8px 12px; color:#ff6b6b;">Unresolved Modules</td>
      <td style="padding:8px 12px; text-align:center; color:#e0e0e0; font-family:monospace;">{unresolved_count}</td>
      <td style="padding:8px 12px; color:#c0cfe0; font-family:monospace; font-size:11px;">{unresolved_modules}</td>
    </tr>
  </table>

  <h3 style="font-size:13px; color:#c0cfe0; text-transform:uppercase; letter-spacing:1px; margin:0 0 10px 0;">Violations by Clock Domain Pair</h3>
  <table style="width:100%; border-collapse:collapse; font-size:13px; margin-bottom:18px;">
    <tr style="background:#0d1b2a;">
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Source Clock</th>
      <th style="padding:8px 12px; text-align:center; color:#00d4ff; font-size:13px;">→</th>
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Dest Clock</th>
      <th style="padding:8px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Count</th>
    </tr>
    <!-- Rows for each clock pair — every <td> needs color: -->
  </table>

  <h3 style="font-size:13px; color:#c0cfe0; text-transform:uppercase; letter-spacing:1px; margin:0 0 10px 0;">Violations by Type</h3>
  <table style="width:100%; border-collapse:collapse; font-size:13px; margin-bottom:18px;">
    <tr style="background:#0d1b2a;">
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Type</th>
      <th style="padding:8px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Total</th>
      <th style="padding:8px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Filtered</th>
      <th style="padding:8px 12px; text-align:center; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Focus</th>
    </tr>
    <!-- Rows for each type -->
  </table>

  <h3 style="font-size:13px; color:#c0cfe0; text-transform:uppercase; letter-spacing:1px; margin:0 0 12px 0;">Top Violations (showing {shown} of {total} focus)</h3>

  <!-- Violation card — repeat for each violation -->
  <div style="background:#162030; border:1px solid #ff6b6b; border-left:4px solid #ff6b6b; border-radius:6px; padding:14px; margin:10px 0;">
    <div style="margin-bottom:10px;">
      <span style="font-weight:bold; font-size:13px; color:#ff8e8e;">{violation_id}</span>
      <span style="background:#ff6b6b; color:#16213e; padding:2px 7px; border-radius:3px; font-size:10px; font-weight:bold; margin-left:8px; text-transform:uppercase;">{risk_level}</span>
      <span style="background:#2a3f5f; color:#c0cfe0; padding:2px 7px; border-radius:3px; font-size:10px; margin-left:4px;">{type}</span>
    </div>
    <table style="width:100%; font-size:12px; border-collapse:collapse;">
      <tr>
        <td style="padding:4px 0; color:#c0cfe0; width:130px;"><b>Source:</b></td>
        <td style="padding:4px 0; color:#e0e0e0;"><code style="background:#0d1b2a; color:#c0cfe0; padding:2px 6px; border-radius:3px;">{source}</code></td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#c0cfe0;"><b>Destination:</b></td>
        <td style="padding:4px 0; color:#e0e0e0;"><code style="background:#0d1b2a; color:#c0cfe0; padding:2px 6px; border-radius:3px;">{dest}</code></td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#c0cfe0;"><b>Clock Crossing:</b></td>
        <td style="padding:4px 0; color:#e0e0e0;"><code style="background:#0d1b2a; color:#00d4ff; padding:2px 6px; border-radius:3px;">{source_clock} → {dest_clock}</code></td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#c0cfe0;"><b>Module:</b></td>
        <td style="padding:4px 0; color:#e0e0e0;"><code style="background:#0d1b2a; color:#c0cfe0; padding:2px 6px; border-radius:3px;">{module}</code></td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#c0cfe0;"><b>RTL Location:</b></td>
        <td style="padding:4px 0; color:#e0e0e0;"><code style="background:#0d1b2a; color:#6bcb77; padding:2px 6px; border-radius:3px;">{rtl_file}:{line}</code></td>
      </tr>
    </table>
    <div style="margin-top:10px; padding:10px 12px; background:#1a2010; border-left:3px solid #ffd93d; border-radius:4px; font-size:12px;">
      <b style="color:#ffd93d;">Root Cause:</b> <span style="color:#c0cfe0;">{root_cause}</span>
    </div>
    <div style="margin-top:8px; padding:10px 12px; background:#0e1f18; border-left:3px solid #6bcb77; border-radius:4px; font-size:12px;">
      <b style="color:#6bcb77;">Recommendation:</b> <span style="color:#c0cfe0;">{recommendation}</span>
    </div>
    <div style="background:#0d1b2a; color:#c0cfe0; padding:12px 14px; border-radius:4px; font-family:'Monaco','Consolas',monospace; font-size:11px; white-space:pre; overflow-x:auto; margin-top:8px; border:1px solid #2a3f5f;">{code_snippet}</div>
  </div>

</div>

<!-- ═══════════════════ LINT SECTION ═══════════════════ -->
<!-- Same structure — yellow accent #ffd93d -->
<div style="background:#1e2d4a; border:1px solid #2a3f5f; border-radius:8px; padding:20px; margin-bottom:18px;">
  <h2 style="margin:0 0 16px 0; font-size:17px; color:#ffd93d; border-bottom:2px solid #ffd93d; padding-bottom:10px;">Lint Analysis</h2>
  <!-- violation cards: border-left:4px solid #ffd93d; violation_id color:#ffe566; badge bg:#ffd93d -->
</div>

<!-- ═══════════════════ SPGDFT SECTION ═══════════════════ -->
<!-- Same structure — green accent #6bcb77 -->
<div style="background:#1e2d4a; border:1px solid #2a3f5f; border-radius:8px; padding:20px; margin-bottom:18px;">
  <h2 style="margin:0 0 16px 0; font-size:17px; color:#6bcb77; border-bottom:2px solid #6bcb77; padding-bottom:10px;">SpgDFT Analysis</h2>
  <!-- violation cards: border-left:4px solid #6bcb77; violation_id color:#8ed99a; badge bg:#6bcb77 -->
</div>

<!-- ═══════════════════ RECOMMENDATIONS ═══════════════════ -->
<div style="background:#1e2d4a; border:1px solid #2a3f5f; border-radius:8px; padding:20px; margin-bottom:18px;">
  <h2 style="margin:0 0 16px 0; font-size:17px; color:#00d4ff; border-bottom:2px solid #00d4ff; padding-bottom:10px;">Recommendations Summary</h2>
  <div style="background:#1f1020; border-left:4px solid #ff6b6b; padding:12px 16px; margin:10px 0; border-radius:4px;">
    <b style="font-size:11px; color:#ff6b6b; text-transform:uppercase; letter-spacing:1px;">High Priority ({count})</b>
    <ul style="margin:8px 0 0 0; padding-left:20px; font-size:13px; color:#c0cfe0;"><!-- items --></ul>
  </div>
  <div style="background:#1a1a10; border-left:4px solid #ffd93d; padding:12px 16px; margin:10px 0; border-radius:4px;">
    <b style="font-size:11px; color:#ffd93d; text-transform:uppercase; letter-spacing:1px;">Medium Priority ({count})</b>
    <ul style="margin:8px 0 0 0; padding-left:20px; font-size:13px; color:#c0cfe0;"><!-- items --></ul>
  </div>
  <div style="background:#0e1f18; border-left:4px solid #6bcb77; padding:12px 16px; margin:10px 0; border-radius:4px;">
    <b style="font-size:11px; color:#6bcb77; text-transform:uppercase; letter-spacing:1px;">Low Priority ({count})</b>
    <ul style="margin:8px 0 0 0; padding-left:20px; font-size:13px; color:#c0cfe0;"><!-- items --></ul>
  </div>
</div>

<!-- ═══════════════════ CONFIG FILES ═══════════════════ -->
<div style="background:#162030; border:1px solid #00d4ff; border-radius:8px; padding:18px; margin-bottom:18px;">
  <h2 style="margin:0 0 14px 0; font-size:15px; color:#00d4ff;">Configuration Files</h2>
  <table style="width:100%; border-collapse:collapse; font-size:12px;">
    <tr style="background:#0d1b2a;">
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Check</th>
      <th style="padding:8px 12px; text-align:left; color:#c0cfe0; font-size:11px; text-transform:uppercase;">Config File</th>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:8px 12px; color:#ff6b6b; font-weight:600;">CDC / RDC</td>
      <td style="padding:8px 12px; color:#e0e0e0;"><code style="color:#c0cfe0; font-size:11px;">{cdc_config}</code></td>
    </tr>
    <tr style="border-bottom:1px solid #4a6080; background:#192840;">
      <td style="padding:8px 12px; color:#6bcb77; font-weight:600;">SpgDFT</td>
      <td style="padding:8px 12px; color:#e0e0e0;"><code style="color:#c0cfe0; font-size:11px;">{spgdft_config}</code></td>
    </tr>
    <tr>
      <td style="padding:8px 12px; color:#ffd93d; font-weight:600;">Lint</td>
      <td style="padding:8px 12px; color:#e0e0e0;"><code style="color:#c0cfe0; font-size:11px;">{lint_config}</code></td>
    </tr>
  </table>
</div>

<!-- Footer -->
<div style="text-align:center; padding:16px; font-size:11px; color:#445566; border-top:1px solid #2a3f5f; margin-top:10px;">
  Generated by Claude Code Analysis &nbsp;|&nbsp; {check_type_label} &nbsp;|&nbsp; {tag} &nbsp;|&nbsp; {ip} @ {dir_name}
</div>

</div>
</body>
</html>
```

## Badge / Gate Color Guide

| Status | `{*_badge_bg}` | `{gate_border_color}` | `{gate_text_color}` |
|--------|---------------|----------------------|---------------------|
| NEEDS ACTION | `#ff6b6b` | `#ff6b6b` | `#ff6b6b` |
| CLEAN | `#6bcb77` | `#6bcb77` | `#6bcb77` |

## Color Reference

| Use | Color |
|-----|-------|
| Page background | `#16213e` |
| Card/panel background | `#1e2d4a` |
| Deep background (headers, code) | `#0d1b2a` |
| Panel border | `#2a3f5f` |
| Row separator | `#4a6080` |
| Accent cyan | `#00d4ff` |
| Primary text | `#e0e0e0` |
| Labels / secondary text | `#c0cfe0` |
| Dim / footer | `#445566` |
| CDC/RDC accent | `#ff6b6b` / `#ff8e8e` |
| Lint accent | `#ffd93d` / `#ffe566` |
| SpgDFT accent | `#6bcb77` / `#8ed99a` |
| Alternating data row | `#192840` |
| Root cause bg | `#1a2010` + border `#ffd93d` |
| Recommendation bg | `#0e1f18` + border `#6bcb77` |
| High priority bg | `#1f1020` + border `#ff6b6b` |
| Medium priority bg | `#1a1a10` + border `#ffd93d` |
| Low priority bg | `#0e1f18` + border `#6bcb77` |

## Instructions

1. **Read only the JSON files for your check_type** (skip files from other check types)
2. **Set `check_type_label`** based on check_type:
   - `cdc_rdc` → `"CDC / RDC"`
   - `lint`    → `"Lint"`
   - `spg_dft` → `"SpgDFT"`
3. **Generate HTML** — zero `<style>` tags, zero CSS classes, all inline
4. **Show only sections for your check type** — do NOT include sections for other checks
5. **Flowchart header:** use `<table border-spacing>` for grid; `display:inline-block` for centered nodes; simple `border-top/left/right` divs for brackets; `◆` character in inline-styled div for gate
6. **Every `<td>`** must have explicit `color:` attribute
7. **Include code snippets** for fixes
8. **Write to the correct output file** (see Output section above):
   - `cdc_rdc`  → `data/<tag>_analysis_cdc.html`
   - `lint`     → `data/<tag>_analysis_lint.html`
   - `spg_dft`  → `data/<tag>_analysis_spgdft.html`
