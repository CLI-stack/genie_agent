# Fixer Checkpoint Checklist — Re-read this before STEP 3, 4, and 5 every round

## STEP 3 — Compile Round Report
```
Task: Report Compiler agent → config/analyze_agents/shared/report_compiler.md
Output MUST exist: data/<tag>_analysis_fixer_round<N>.html
```

## STEP 4 — Send Round Email ★ MANDATORY — NEVER SKIP ★
```bash
python3 script/genie_cli.py --send-fixer-round-email <tag> --round <N> --check-type <check_type>
```

## STEP 5 — Termination Check
| Condition | Action |
|-----------|--------|
| focus_violations == 0 | CLEAN → Final Summary |
| constraints=0 AND rtl_fixes=0 AND tie_offs=0 AND deep_dive=0 | STALLED → Final Summary |
| round >= max_rounds | MAX ROUNDS → Final Summary |
| Otherwise | Rerun (see below) |

**❌ NEVER stop and say "MANUAL RERUN NEEDED". ✅ ALWAYS rerun autonomously.**

## STEP 5a — Rerun Command
```bash
# Read fixer_state: normalize original_instruction (replace "fix "/"analyze and fix " with "run ")
cd <base_dir>
python3 script/genie_cli.py -i "<normalized_original_instruction>" --execute --email $XTERM_FLAG $TO_FLAG
```
**`--email` is MANDATORY on every rerun.**

Write new fixer_state to data/<new_tag>_fixer_state carrying forward: original_ref_dir, original_ip,
original_check_type, original_instruction, round=N+1, max_rounds, parent_tag=<current_tag>,
use_xterm, use_email=true, email_to.

## Final Summary Commands
```bash
# Round email (every round):
python3 script/genie_cli.py --send-fixer-round-email <tag> --round <N> --check-type <check_type>

# Final summary email (when done):
python3 script/genie_cli.py --send-fixer-summary-email <first_tag> --check-type <check_type> --result <CLEAN|STALLED|MAX_ROUNDS_REACHED>
```
