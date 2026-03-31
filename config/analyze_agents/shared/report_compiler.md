# Report Compiler Agent

**PERMISSIONS:** You have FULL READ ACCESS to all files under /proj/. Do not ask for permission - just read the files directly.

Generate a clean, readable HTML analysis report for **ONE specific check type** with FULL COVERAGE for email.

Each report compiler handles ONLY its assigned check type (CDC/RDC, Lint, OR SpgDFT) — never a combined multi-check report.

## ██████████████████████████████████████████████████████
## ██  INLINE STYLES ONLY — NO <style> TAGS EVER       ██
## ██████████████████████████████████████████████████████

**Email clients render `<style>` block content as VISIBLE TEXT.**
**NEVER use `<style>` tags. NEVER use CSS classes. NEVER use flexbox or grid.**

Every element MUST use `style="..."` inline attributes. No exceptions.

For multi-column layouts: use `<table>`.

**Checklist before writing any HTML:**
- [ ] Zero `<style>` tags in the entire document
- [ ] Zero CSS class references anywhere
- [ ] Every `<td>` has explicit `color:` attribute
- [ ] Every layout uses `<table>` (not flexbox/grid)

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

Use Glob to find all RTL analyzer files for your check type:
- CDC/RDC: `data/<tag>_rtl_cdc_*.json` and `data/<tag>_rtl_rdc_*.json`
- Lint: `data/<tag>_rtl_lint_*.json`
- SpgDFT: `data/<tag>_rtl_spgdft_*.json`

## Output

| check_type | Output File |
|------------|-------------|
| `cdc_rdc`  | `data/<tag>_analysis_cdc.html` |
| `lint`     | `data/<tag>_analysis_lint.html` |
| `spg_dft`  | `data/<tag>_analysis_spgdft.html` |

Write HTML using the Write tool to the appropriate file above.

## Report Sections

**For `cdc_rdc`:**
1. Header (IP, tag, ref_dir, check type = CDC/RDC)
2. Summary table (CDC + RDC counts, focus, status)
3. Preconditions table (inferred clocks/resets, unresolved modules)
4. Library finder results (if any)
5. Violations by clock domain pair
6. Violations by type (bucket)
7. Violation cards (up to 10, covering all buckets)
8. Recommendations (High / Medium / Low)
9. Configuration files reference

**For `lint`:**
1. Header (IP, tag, ref_dir, check type = Lint)
2. Summary table (counts, focus, status)
3. Violations by code/type
4. Violation cards (up to 10)
5. Recommendations
6. Configuration files reference

**For `spg_dft`:**
1. Header (IP, tag, ref_dir, check type = SpgDFT)
2. Summary table (counts, focus, status)
3. Blackbox modules table (if any)
4. Library finder results (if any)
5. Violations by rule
6. Violation cards (up to 10)
7. Recommendations
8. Configuration files reference

---

## ⚠️ STRICT LAYOUT RULES — VIOLATIONS WILL BREAK THE EMAIL

**Read these rules BEFORE writing any HTML. Do NOT deviate from the template below.**

1. **COPY THE TEMPLATE LITERALLY.** Do NOT add outer wrapper tables, gray page backgrounds (`#f0f0f0`), or any structural elements not in the template.
2. **`max-width` is `680px`** — NEVER use `width="860"` or any other fixed table width. NEVER use `max-width:960px`.
3. **`body padding` is `16px 20px`** — NEVER use `padding:28px` or more. That creates the large empty space at the top.
4. **MINIMUM font-size is `14px` everywhere** — NEVER write `font-size:11px`, `font-size:12px`, or `font-size:13px` ANYWHERE. This includes badges, labels, monospace text, footers, and `<pre>` blocks.
5. **Header is a light left-border div** (as shown in template) — NEVER use a dark/filled background color block for the header.
6. **Table cell numbers use the SAME font-size as the table** — NEVER set numbers to `font-size:18px` or `font-size:20px` to make them look "big".
7. **Zero nested table structures in the header** — one simple `<div>` is enough.

---

## HTML Template — Light / Clean Style

**White background, 15px font, minimal decoration. No flowchart, no arrows, no gates.**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{check_type_label} Analysis - {ip} @ {dir_name}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; font-size:15px; color:#1a1a1a; background:#ffffff; margin:0; padding:16px 20px;">
<div style="max-width:680px; margin:0 auto;">

<!-- ══════════════════════════════════════════
     HEADER
     ══════════════════════════════════════════ -->
<div style="border-left:5px solid {accent_color}; padding:14px 20px; background:{accent_light_bg}; border-radius:0 6px 6px 0; margin-bottom:28px;">
  <div style="font-size:22px; font-weight:700; color:{accent_dark};">{check_type_label} Analysis Report</div>
  <div style="font-size:14px; color:#555; margin-top:6px;">
    <b>IP:</b> {ip} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Tag:</b> {tag} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Tree:</b> {dir_name}
  </div>
  <div style="font-size:14px; color:#888; margin-top:3px;">{ref_dir}</div>
</div>

<!-- ══════════════════════════════════════════
     SUMMARY TABLE
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid {accent_color}; padding-left:12px; margin-bottom:14px;">Summary</div>
  <table style="width:100%; border-collapse:collapse; font-size:15px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:11px 14px; text-align:left; color:#444; font-weight:600;">Check</th>
        <th style="padding:11px 14px; text-align:center; color:#444; font-weight:600;">Total</th>
        <th style="padding:11px 14px; text-align:center; color:#444; font-weight:600;">Filtered (DFT/RSMU)</th>
        <th style="padding:11px 14px; text-align:center; color:#444; font-weight:600;">Focus</th>
        <th style="padding:11px 14px; text-align:center; color:#444; font-weight:600;">Status</th>
      </tr>
    </thead>
    <tbody>
      <!-- CDC row — only for cdc_rdc check_type -->
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:11px 14px; color:#c0392b; font-weight:600;">CDC</td>
        <td style="padding:11px 14px; text-align:center; color:#1a1a1a;">{cdc_total}</td>
        <td style="padding:11px 14px; text-align:center; color:#666;">{cdc_filtered}</td>
        <td style="padding:11px 14px; text-align:center; color:#2563eb; font-weight:700;">{cdc_focus}</td>
        <td style="padding:11px 14px; text-align:center;">
          <!-- NEEDS ACTION badge: bg #fee2e2 color #b91c1c | CLEAN badge: bg #d1fae5 color #065f46 -->
          <span style="background:{cdc_badge_bg}; color:{cdc_badge_color}; padding:3px 11px; border-radius:4px; font-size:14px; font-weight:600;">{cdc_status}</span>
        </td>
      </tr>
      <!-- RDC row — only for cdc_rdc check_type -->
      <tr style="border-bottom:1px solid #eee; background:#fafafa;">
        <td style="padding:11px 14px; color:#c0392b; font-weight:600;">RDC</td>
        <td style="padding:11px 14px; text-align:center; color:#1a1a1a;">{rdc_total}</td>
        <td style="padding:11px 14px; text-align:center; color:#666;">{rdc_filtered}</td>
        <td style="padding:11px 14px; text-align:center; color:#2563eb; font-weight:700;">{rdc_focus}</td>
        <td style="padding:11px 14px; text-align:center;">
          <span style="background:{rdc_badge_bg}; color:{rdc_badge_color}; padding:3px 11px; border-radius:4px; font-size:14px; font-weight:600;">{rdc_status}</span>
        </td>
      </tr>
      <!-- Lint row — only for lint check_type -->
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:11px 14px; color:#d97706; font-weight:600;">Lint</td>
        <td style="padding:11px 14px; text-align:center; color:#1a1a1a;">{lint_total}</td>
        <td style="padding:11px 14px; text-align:center; color:#666;">{lint_filtered}</td>
        <td style="padding:11px 14px; text-align:center; color:#2563eb; font-weight:700;">{lint_focus}</td>
        <td style="padding:11px 14px; text-align:center;">
          <span style="background:{lint_badge_bg}; color:{lint_badge_color}; padding:3px 11px; border-radius:4px; font-size:14px; font-weight:600;">{lint_status}</span>
        </td>
      </tr>
      <!-- SpgDFT row — only for spg_dft check_type -->
      <tr style="border-bottom:1px solid #eee; background:#fafafa;">
        <td style="padding:11px 14px; color:#059669; font-weight:600;">SpgDFT</td>
        <td style="padding:11px 14px; text-align:center; color:#1a1a1a;">{spg_total}</td>
        <td style="padding:11px 14px; text-align:center; color:#666;">{spg_filtered}</td>
        <td style="padding:11px 14px; text-align:center; color:#2563eb; font-weight:700;">{spg_focus}</td>
        <td style="padding:11px 14px; text-align:center;">
          <span style="background:{spg_badge_bg}; color:{spg_badge_color}; padding:3px 11px; border-radius:4px; font-size:14px; font-weight:600;">{spg_status}</span>
        </td>
      </tr>
    </tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════
     PRECONDITIONS (CDC/RDC only)
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid #c0392b; padding-left:12px; margin-bottom:14px;">Preconditions</div>
  <table style="width:100%; border-collapse:collapse; font-size:14px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Type</th>
        <th style="padding:10px 14px; text-align:center; color:#444; font-weight:600;">Count</th>
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Signals / Modules</th>
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Action</th>
      </tr>
    </thead>
    <tbody>
      <!-- One row per precondition type; alternate background #fff / #fafafa -->
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#555;">Inferred Clocks</td>
        <td style="padding:10px 14px; text-align:center; color:#1a1a1a; font-family:monospace;">{inferred_clk_count}</td>
        <td style="padding:10px 14px; color:#333; font-family:monospace; font-size:14px;">{inferred_clk_signals}</td>
        <td style="padding:10px 14px; color:#555; font-size:14px;">{inferred_clk_action}</td>
      </tr>
      <tr style="border-bottom:1px solid #eee; background:#fafafa;">
        <td style="padding:10px 14px; color:#555;">Inferred Resets</td>
        <td style="padding:10px 14px; text-align:center; color:#1a1a1a; font-family:monospace;">{inferred_rst_count}</td>
        <td style="padding:10px 14px; color:#333; font-family:monospace; font-size:14px;">{inferred_rst_signals}</td>
        <td style="padding:10px 14px; color:#555; font-size:14px;">{inferred_rst_action}</td>
      </tr>
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#c0392b;">Unresolved Modules</td>
        <td style="padding:10px 14px; text-align:center; color:#1a1a1a; font-family:monospace;">{unresolved_count}</td>
        <td style="padding:10px 14px; color:#333; font-family:monospace; font-size:14px;">{unresolved_modules}</td>
        <td style="padding:10px 14px; color:#555; font-size:14px;">{unresolved_action}</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════
     LIBRARY FINDER (if blackbox/unresolved > 0)
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid #2563eb; padding-left:12px; margin-bottom:14px;">Library Additions Required</div>
  <table style="width:100%; border-collapse:collapse; font-size:14px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Module</th>
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Library Path</th>
      </tr>
    </thead>
    <tbody>
      <!-- One row per module found -->
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#333; font-family:monospace; font-size:14px;">{module_name}</td>
        <td style="padding:10px 14px; color:#2563eb; font-family:monospace; font-size:14px;">{library_path}</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════
     VIOLATIONS BY TYPE / BUCKET
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid {accent_color}; padding-left:12px; margin-bottom:14px;">Violations by Type</div>
  <table style="width:100%; border-collapse:collapse; font-size:14px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Type</th>
        <th style="padding:10px 14px; text-align:center; color:#444; font-weight:600;">Total</th>
        <th style="padding:10px 14px; text-align:center; color:#444; font-weight:600;">Filtered</th>
        <th style="padding:10px 14px; text-align:center; color:#444; font-weight:600;">Focus</th>
      </tr>
    </thead>
    <tbody>
      <!-- One row per violation type; alternate #fff / #fafafa -->
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#333; font-family:monospace;">{viol_type}</td>
        <td style="padding:10px 14px; text-align:center; color:#1a1a1a;">{viol_total}</td>
        <td style="padding:10px 14px; text-align:center; color:#666;">{viol_filtered}</td>
        <td style="padding:10px 14px; text-align:center; color:#2563eb; font-weight:600;">{viol_focus}</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════
     VIOLATION CARDS — repeat for each violation
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid {accent_color}; padding-left:12px; margin-bottom:16px;">
    Top Violations ({shown} of {total} focus)
  </div>

  <!-- Violation card — repeat this block for each violation -->
  <div style="border:1px solid #e5e7eb; border-left:4px solid {accent_color}; border-radius:6px; padding:18px; margin-bottom:14px; background:#ffffff;">

    <!-- Title row -->
    <div style="font-size:15px; font-weight:700; color:#1a1a1a; margin-bottom:10px;">
      {violation_id}
      <!-- Risk badge: HIGH=#fee2e2/#b91c1c  MEDIUM=#fef3c7/#92400e  LOW=#d1fae5/#065f46 -->
      <span style="background:{risk_bg}; color:{risk_color}; padding:2px 9px; border-radius:4px; font-size:14px; font-weight:700; margin-left:8px;">{risk_level}</span>
      <span style="background:#f1f5f9; color:#475569; padding:2px 9px; border-radius:4px; font-size:14px; margin-left:4px;">{viol_type}</span>
    </div>

    <!-- Signal/location details -->
    <table style="font-size:14px; border-collapse:collapse; width:100%; margin-bottom:12px;">
      <tr>
        <td style="padding:4px 0; color:#666; width:150px; vertical-align:top;"><b>Signal:</b></td>
        <td style="padding:4px 0; color:#1a1a1a; font-family:monospace; font-size:14px;">{signal_name}</td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#666; vertical-align:top;"><b>Clock Crossing:</b></td>
        <td style="padding:4px 0; color:#2563eb; font-family:monospace; font-size:14px;">{src_clock} → {dst_clock}</td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#666; vertical-align:top;"><b>RTL Location:</b></td>
        <td style="padding:4px 0; color:#059669; font-family:monospace; font-size:14px;">{rtl_file}:{line}</td>
      </tr>
      <tr>
        <td style="padding:4px 0; color:#666; vertical-align:top;"><b>Signal Purpose:</b></td>
        <td style="padding:4px 0; color:#333; font-size:14px;">{signal_purpose}</td>
      </tr>
    </table>

    <!-- Root cause -->
    <div style="padding:11px 14px; background:#fffbeb; border-left:3px solid #d97706; border-radius:4px; font-size:14px; color:#333; margin-bottom:8px;">
      <b style="color:#b45309;">Root Cause:</b> {why_no_sync}
    </div>

    <!-- Recommendation -->
    <div style="padding:11px 14px; background:#f0fdf4; border-left:3px solid #059669; border-radius:4px; font-size:14px; color:#333; margin-bottom:8px;">
      <b style="color:#059669;">Fix ({fix_type}):</b> {fix_justification}
    </div>

    <!-- Code snippet -->
    <pre style="background:#f5f5f5; color:#1a1a1a; padding:12px 14px; border-radius:4px; font-size:14px; font-family:'Courier New',Courier,monospace; overflow-x:auto; margin:0; border:1px solid #e5e7eb; white-space:pre;">{fix_action}</pre>

  </div>
  <!-- end violation card -->

</div>

<!-- ══════════════════════════════════════════
     BLACKBOX MODULES (SpgDFT only)
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid #059669; padding-left:12px; margin-bottom:14px;">Blackbox Modules</div>
  <table style="width:100%; border-collapse:collapse; font-size:14px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Module</th>
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Message</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#333; font-family:monospace; font-size:14px;">{module_name}</td>
        <td style="padding:10px 14px; color:#555; font-size:14px;">{message}</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- ══════════════════════════════════════════
     RECOMMENDATIONS
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid #2563eb; padding-left:12px; margin-bottom:16px;">Recommendations</div>

  <!-- High priority -->
  <div style="padding:14px 18px; background:#fff5f5; border-left:4px solid #c0392b; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:14px; font-weight:700; color:#c0392b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">High Priority ({high_count})</div>
    <ul style="margin:0; padding-left:20px; font-size:14px; color:#333; line-height:1.7;">
      <li style="color:#333;">{high_item_1}</li>
    </ul>
  </div>

  <!-- Medium priority -->
  <div style="padding:14px 18px; background:#fffbeb; border-left:4px solid #d97706; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:14px; font-weight:700; color:#d97706; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">Medium Priority ({med_count})</div>
    <ul style="margin:0; padding-left:20px; font-size:14px; color:#333; line-height:1.7;">
      <li style="color:#333;">{med_item_1}</li>
    </ul>
  </div>

  <!-- Low priority -->
  <div style="padding:14px 18px; background:#f0fdf4; border-left:4px solid #059669; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:14px; font-weight:700; color:#059669; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">Low Priority ({low_count})</div>
    <ul style="margin:0; padding-left:20px; font-size:14px; color:#333; line-height:1.7;">
      <li style="color:#333;">{low_item_1}</li>
    </ul>
  </div>
</div>

<!-- ══════════════════════════════════════════
     CONFIGURATION FILES
     ══════════════════════════════════════════ -->
<div style="margin-bottom:32px;">
  <div style="font-size:17px; font-weight:700; color:#1a1a1a; border-left:4px solid #2563eb; padding-left:12px; margin-bottom:14px;">Configuration Files</div>
  <table style="width:100%; border-collapse:collapse; font-size:14px;">
    <thead>
      <tr style="background:#f5f7fa; border-bottom:2px solid #dde1e7;">
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Check</th>
        <th style="padding:10px 14px; text-align:left; color:#444; font-weight:600;">Config File</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#c0392b; font-weight:600;">CDC / RDC</td>
        <td style="padding:10px 14px; color:#1a1a1a; font-family:monospace; font-size:14px;">{cdc_config_path}</td>
      </tr>
      <tr style="border-bottom:1px solid #eee; background:#fafafa;">
        <td style="padding:10px 14px; color:#d97706; font-weight:600;">Lint</td>
        <td style="padding:10px 14px; color:#1a1a1a; font-family:monospace; font-size:14px;">{lint_config_path}</td>
      </tr>
      <tr style="border-bottom:1px solid #eee;">
        <td style="padding:10px 14px; color:#059669; font-weight:600;">SpgDFT</td>
        <td style="padding:10px 14px; color:#1a1a1a; font-family:monospace; font-size:14px;">{spgdft_config_path}</td>
      </tr>
    </tbody>
  </table>
</div>

<!-- Footer -->
<div style="text-align:center; padding:16px; font-size:14px; color:#888; border-top:1px solid #eee; margin-top:16px;">
  Generated by Claude Code Analysis &nbsp;|&nbsp; {check_type_label} &nbsp;|&nbsp; {tag} &nbsp;|&nbsp; {ip} @ {dir_name}
</div>

</div>
</body>
</html>
```

---

## Color Reference

### Accent colors by check type

| check_type | `{accent_color}` | `{accent_dark}` | `{accent_light_bg}` |
|------------|-----------------|-----------------|---------------------|
| `cdc_rdc`  | `#c0392b`       | `#7f1d1d`       | `#fff5f5`           |
| `lint`     | `#d97706`       | `#78350f`       | `#fffbeb`           |
| `spg_dft`  | `#059669`       | `#064e3b`       | `#f0fdf4`           |

### Status badges

| Status | `{*_badge_bg}` | `{*_badge_color}` |
|--------|----------------|-------------------|
| NEEDS ACTION | `#fee2e2` | `#b91c1c` |
| CLEAN | `#d1fae5` | `#065f46` |

### Risk level badges (violation cards)

| Risk | `{risk_bg}` | `{risk_color}` |
|------|-------------|----------------|
| HIGH | `#fee2e2` | `#b91c1c` |
| MEDIUM | `#fef3c7` | `#92400e` |
| LOW | `#d1fae5` | `#065f46` |

### General palette

| Use | Color |
|-----|-------|
| Page background | `#ffffff` |
| Body text | `#1a1a1a` |
| Secondary text | `#555` / `#666` |
| Dim / footer | `#888` |
| Table header bg | `#f5f7fa` |
| Alternating row | `#fafafa` |
| Row separator | `#eee` |
| Header separator | `#dde1e7` |
| Card border | `#e5e7eb` |
| Focus count | `#2563eb` (blue) |
| Root cause bg | `#fffbeb` + border `#d97706` |
| Fix/rec bg | `#f0fdf4` + border `#059669` |
| Code block bg | `#f5f5f5` + border `#e5e7eb` |
| Code text | `#1a1a1a` |

---

## Instructions

1. **Read only the JSON files for your check_type** (skip files from other check types)
2. **Set accent colors** based on check_type (see Color Reference above)
3. **Show only the rows for your check_type** in the summary table:
   - `cdc_rdc` → show CDC row + RDC row only
   - `lint` → show Lint row only
   - `spg_dft` → show SpgDFT row only
4. **Skip sections with zero data** gracefully (e.g., if no library finder results, omit that section)
5. **Show only sections for your check_type** — do NOT include sections for other checks
6. **Violation cards:** repeat the card block for each violation, up to 10
7. **Every `<td>`** must have explicit `color:` attribute
8. **Code snippets** go in `<pre>` blocks with `#f5f5f5` background
9. **Write to the correct output file**:
   - `cdc_rdc`  → `data/<tag>_analysis_cdc.html`
   - `lint`     → `data/<tag>_analysis_lint.html`
   - `spg_dft`  → `data/<tag>_analysis_spgdft.html`
