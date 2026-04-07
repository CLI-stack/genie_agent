---
name: lint
description: >
  Use when linting TileBuilder target scripts (CSH, TCL, templates) for coding
  guideline violations. Trigger phrases: "lint this target", "check code quality",
  "validate before commit", "find coding issues", "run TileBuilderLint". Checks
  for common violations: VovInput/VovOutput usage, barrier placement, template
  quality, and TileBuilder coding standards.
allowed-tools: Read, Grep, Glob, Bash
---

# TileBuilder Lint

This skill runs TileBuilderLint to analyze TileBuilder target scripts for coding guideline violations.

## Usage

The user invokes this skill with one or more file paths:

```
/lint path/to/target.csh
/lint target1.csh target2.csh template.cmd
```

## Running the Linter

Use the TileBuilderLint tool located in the TileBuilder flow:

```bash
# Quick mode (static checks only, no AI/genie) - recommended for fast feedback
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --quick <files>

# Full mode (includes genie-powered checks)
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint <files>

# Verbose mode (shows fix suggestions)
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --verbose <files>

# JSON output for programmatic processing
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --format json <files>
```

## Finding FLOW_DIR

If `$FLOW_DIR` is not set in the environment, determine it from:

1. **From params.json** (if in a run directory):
   ```bash
   jq -r .params.FLOW_DIR params.json
   ```

2. **From the file path** (if linting a file in the TileBuilder checkout):
   - For files in `$FLOW_DIR/$FAMILY/actions/*/Target.csh`, FLOW_DIR is 4 directories up
   - Verify by checking for `$FLOW_DIR/activate.sh`

3. **Default location**:
   ```bash
   /tool/aticad/1.0/flow/TileBuilder
   ```

## Interpreting Results

TileBuilderLint reports violations with severity levels:

| Severity | Meaning | Exit Code Impact |
|----------|---------|------------------|
| **error** | Must fix before commit | Causes non-zero exit |
| **warning** | Should fix, but not blocking | No exit code impact |
| **info** | Style suggestion | No exit code impact |

## Check Categories

- **structural**: Shebang, barrier, header, param formatting
- **file_io**: File path conventions, naming
- **template**: eperl quality, exec usage
- **vov**: VovInput/VovOutput completeness (genie-powered)
- **style**: Code readability (genie-powered)

## Auto-Fix

Some checks support auto-fix:

```bash
# Preview fixes
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --fix --dry-run <files>

# Apply fixes
$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --fix <files>
```

## Example Workflow

1. User runs `/lint FxPlace.csh`
2. Determine FLOW_DIR
3. Run: `$FLOW_DIR/lib/TileBuilderLint/TileBuilderLint --verbose FxPlace.csh`
4. Report findings to user with file:line references
5. If errors found, suggest running with `--fix` to auto-correct fixable issues

## Documentation

- [TileBuilderLint Documentation](https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/1381385340)
- [TileBuilder Target Coding Guidelines](https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/1225869313)
