# CRITICAL RULES — Read First, Apply Always

> These rules override anything else. Violations cause data loss or pipeline failures.

## File Modification
- `p4 edit` ONLY for constraint/meta files (`src/meta/tools/...`) — NEVER for `src/rtl/...`
- Backup before any edit: `cp <file> <file>.bak_<tag>` — once per file per round
- RTL files (`src/rtl/...`): use the Edit tool directly — NO `p4 edit`

## RTL Path Resolution
- **CDC/RDC & Lint** RTL fixes: resolve in priority order:
  1. `out/*/library/*/pub/src/rtl/<basename>` — **preferred** (survives reruns, covers library components)
  2. `src/rtl/**/<basename>` — fallback for project-owned files not in any library
  → `publish_rtl/` is **WIPED** on every rerun — NEVER edit it directly
- **SPG_DFT** RTL fixes: use path **as-is** — SPG_DFT does NOT run `rhea_build`, so `publish_rtl/` is stable

## Output Files (MANDATORY)
- You MUST write your output JSON to disk using the **Write tool** before finishing your turn
- If you do not write the output file, the orchestrator pipeline **STOPS** — retry logic will re-invoke you

## Lint
- **ZERO waivers** — do NOT touch `src/meta/waivers/lint/variant/<ip>/umc_waivers.xml`
- All lint violations must be fixed directly in RTL source or via inline pragma

## Apply The Fix — Hard Rule
**If you know the fix, apply it. Do NOT escalate to `investigate` as a hedge.**

- If you can read the RTL and determine a concrete fix that does not cause functional issues → use `rtl_fix`, `tie_off`, or `pragma_suppress`. Apply it.
- `investigate` is ONLY for cases where reading the RTL does not reveal the correct fix — not for cases where you are uncertain but the fix is clear and safe.
- **NEVER say "coordinate with library owner"**, "requires library owner review", or any equivalent. You have full write access to all files. If the fix is safe, apply it regardless of whether the file is in a library path.
- **NEVER refuse to fix a library file** (`out/*/library/*/pub/src/rtl/`). Library path does NOT mean library owner action required — it is just the correct file path to edit.
- The `if/if` → `else-if` restructuring in always blocks is ALWAYS safe to apply: if conditions are mutually exclusive it is equivalent; if they can overlap it establishes correct explicit priority instead of undefined last-assignment-wins behavior.
- Inline pragmas (`// spyglass disable <rule>`) are ALWAYS safe to apply: they are pure comments with zero functional impact.

## Full Static Check Fix Order
- Fix Implementors run **SEQUENTIALLY**: CDC/RDC → Lint → SPG_DFT
- Running them in parallel causes duplicate RTL edits to the same file
