---
name: param-editing
description: >
  Use when user wants to edit params, set parameters, change settings,
  increase memory/RAM/CPU, switch tools (CHOSEN_*), or modify TileBuilder
  configuration. Trigger phrases: "edit the params and set FOO to bar",
  "increase memory for FxPlace", "change CHOSEN_ROUTER to fusion",
  "set DEBUG_MODE to 1", "override LSF queue". Automatically enforces
  tracking (timestamps + reasons) for all parameter modifications in
  override.params, override.controls, or override.params.json files.
allowed-tools: Read, Edit, Write, Bash, mcp__TileBuilderGenParams__*
---

# TileBuilder Parameter File Editing Rules

**CRITICAL**: This skill contains MANDATORY rules that the AI assistant MUST follow when editing TileBuilder parameter files.

## When This Skill Applies

This skill is automatically invoked when the AI assistant is about to:
- Edit `override.params`, `override.controls`, or `override.params.json`
- Add new parameters for debugging or troubleshooting
- Modify existing parameter values
- Change LSF resources, tool choices, or flow settings

## Parameter File Types

TileBuilder uses three types of override files:

| File | Format | Primary Use |
|------|--------|-------------|
| **override.params** | `PARAM = value` | Design-specific parameters |
| **override.controls** | `PARAM = value` | Flow/system controls (LSF, tool versions) |
| **override.params.json** | JSON | MCP-based parameter modifications |

All three files are processed by `TileBuilderGenParams` to generate the final `tile.params` and `params.json`.

**Location**: `$ALLTILES_DIR/$NICKNAME/override.*` (NOT the ROOT_DIR copy)

## Hard Rule: Track All AI Modifications

### For override.params and override.controls

When adding OR modifying parameters, **MUST** add a timestamp comment with reason above the parameter:

**Format for additions:**
```bash
# added by genie on YYYY-MM-DD HH:MM: reason for adding this parameter
NEW_PARAMETER = value
```

**Format for modifications:**
```bash
# modified by genie on YYYY-MM-DD HH:MM (was: old_value): reason for the change
EXISTING_PARAMETER = new_value
```

**Keep reasons concise** (one line, ~50 chars max):
- ✅ Good: "OOM error at log line 1234"
- ✅ Good: "Trying Fusion instead of ICC2 for routing"
- ❌ Bad: "The user reported that the placement target was failing with out of memory errors so I increased the RAM allocation"

### For override.params.json

JSON params support comments using a special convention: add "#" to the end of the parameter name, and the value is the comment.

**Comment format options:**

1. **Single-line comment** (short explanations):
```json
{
  "PARAM_NAME": "value",
  "PARAM_NAME#": "added by genie on 2026-02-12 10:30: reason"
}
```

2. **Multi-line comment** (longer explanations using array of strings):
```json
{
  "PARAM_NAME": "value",
  "PARAM_NAME#": [
    "modified by genie on 2026-02-12 10:30 (was: old_value)",
    "reason: trying Fusion instead of ICC2 for routing"
  ]
}
```

**General parameters with tracking:**
```json
{
  "params": {
    "DEBUG_MODE": "1",
    "DEBUG_MODE#": "added by genie on 2026-02-12 10:15: enable debug for troubleshooting"
  }
}
```

**Target-specific parameters with tracking:**
```json
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "modified by genie on 2026-02-12 11:45 (was: 64000): OOM at log line 1234"
  }
}
```

**Combined example with multiple params:**
```json
{
  "params": {
    "DEBUG_MODE": "1",
    "DEBUG_MODE#": "added by genie on 2026-02-12 10:15: enable debug reports",
    "ENABLE_REPORTING": "true",
    "ENABLE_REPORTING#": "added by genie on 2026-02-12 10:15: verbose output for analysis"
  },
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "modified by genie on 2026-02-12 11:45 (was: 64000): OOM error",
    "TILEBUILDER_CPUS": "16",
    "TILEBUILDER_CPUS#": "added by genie on 2026-02-12 11:46: enable parallel placement"
  }
}
```

## When Tracking Applies

✅ **MUST add tracking when:**
- Adding debug parameters
- Adding experimental/test parameters
- Modifying parameter values during troubleshooting
- Changing LSF resources (TILEBUILDER_RAM_*, TILEBUILDER_CPUS_*)
- Changing tool choices (CHOSEN_*)
- Adding workarounds for failures
- ANY parameter not explicitly provided by user with exact syntax

❌ **Do NOT add tracking when:**
- User provides exact parameter line to add/modify (copy-paste scenario)
- User explicitly says "set PARAM to value" with clear intent
- Removing/deleting parameters (just remove cleanly)
- User is a CAD developer making intentional changes

## Parameter Hierarchy Context

Before modifying parameters, understand the hierarchy (later overrides earlier):

1. `general.params` - Global defaults
2. `family.params` - Family-specific (Supra, FCNL, etc.)
3. `personality.params` - Personality-specific
4. `technology.params` - Tech node specific
5. `stack.params` - Stack-specific
6. `project.params` - Project-specific
7. `project_personality.params` - Project+personality
8. `chip_release.params` - Release-specific
9. `feint_feedback.params` - Feedback from integration
10. **override.params/override.controls** - User overrides (HIGHEST PRIORITY)

To check where a param is defined:
```bash
TileBuilderGenParamsGrep PARAM_NAME
```

## Common Parameter Patterns

### Target-Specific Overrides

Append target name to param for target-specific values:

```bash
# General default
TILEBUILDER_RAM = 64000

# added by genie on 2026-02-12 10:15: FxPlace needs more memory for large design
TILEBUILDER_RAM_FxPlace = 256000
```

In JSON:
```json
{
  "params": {
    "TILEBUILDER_RAM": "64000"
  },
  "FxPlace": {
    "TILEBUILDER_RAM": "256000",
    "TILEBUILDER_RAM#": "added by genie on 2026-02-12 10:15: large design needs more memory"
  }
}
```

### CHOSEN Parameters (Flow Structure)

Params starting with `CHOSEN_*` control flow structure. **Changing these requires `TileBuilderMake`:**

```bash
# modified by genie on 2026-02-12 11:30 (was: iccompiler2): ICC2 route failing
CHOSEN_ROUTER = fusion

# modified by genie on 2026-02-12 11:30 (was: primetime): trying Tempus for faster analysis
CHOSEN_STA_TOOL = tempus
```

In JSON:
```json
{
  "params": {
    "CHOSEN_ROUTER": "fusion",
    "CHOSEN_ROUTER#": "modified by genie on 2026-02-12 11:30 (was: iccompiler2): ICC2 route failing",
    "CHOSEN_STA_TOOL": "tempus",
    "CHOSEN_STA_TOOL#": [
      "modified by genie on 2026-02-12 11:30 (was: primetime)",
      "trying Tempus for faster analysis"
    ]
  }
}
```

### LSF Parameters

Common LSF-related params modified during debugging:

```bash
# modified by genie on 2026-02-12 09:45 (was: 64000): OOM at log line 1234
TILEBUILDER_RAM_FxRoute = 128000

# added by genie on 2026-02-12 09:46: enable parallel routing
TILEBUILDER_CPUS_FxRoute = 16

# modified by genie on 2026-02-12 09:50 (was: regr_high): need high-mem queue
TILEBUILDER_LSFQUEUE_FxRoute = gb256
```

In JSON:
```json
{
  "FxRoute": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "modified by genie on 2026-02-12 09:45 (was: 64000): OOM at log line 1234",
    "TILEBUILDER_CPUS": "16",
    "TILEBUILDER_CPUS#": "added by genie on 2026-02-12 09:46: enable parallel routing",
    "TILEBUILDER_LSFQUEUE": "gb256",
    "TILEBUILDER_LSFQUEUE#": "modified by genie on 2026-02-12 09:50 (was: regr_high): need high-mem queue"
  }
}
```

## Complete Workflow for Param Changes

### Step 1: Read Before Modifying

**Always** read the param file first to:
- Check if parameter already exists
- Understand current value
- Avoid creating duplicates

```bash
# Good practice
cat $ALLTILES_DIR/$NICKNAME/override.params
cat $ALLTILES_DIR/$NICKNAME/override.params.json
```

### Step 2: Make the Modification

Add tracking comment with reason, then parameter:

**Bash format:**
```bash
# For new parameter
# added by genie on 2026-02-12 10:15: enable debug mode for troubleshooting
DEBUG_MODE = 1

# For modified parameter
# modified by genie on 2026-02-12 10:16 (was: 0): user requested verbose reports
ENABLE_REPORTING = 1
```

**JSON format:**
```json
{
  "params": {
    "DEBUG_MODE": "1",
    "DEBUG_MODE#": "added by genie on 2026-02-12 10:15: enable debug mode",
    "ENABLE_REPORTING": "1",
    "ENABLE_REPORTING#": "modified by genie on 2026-02-12 10:16 (was: 0): verbose reports"
  }
}
```

### Step 3: Regenerate Resolved Params

**Always** run after editing override files:

```bash
TileBuilderGenParams
```

This regenerates:
- `$ALLTILES_DIR/$NICKNAME/tile.params`
- `$ALLTILES_DIR/$NICKNAME/params.json`

### Step 4: Conditionally Run Other Commands

**If CHOSEN_* params changed:**
```bash
TileBuilderMake
```

**If template-related params changed:**
```bash
GenerateAllCommands
```

**If specific target's cmd file needs update:**
```bash
TileBuilderOverwriteCommand $TARGET_NAME
```

### Step 5: Inform the User

Always tell the user what you changed and why:

```
"I modified TILEBUILDER_RAM_FxPlace from 64000 to 128000 due to the
out-of-memory error at logs/FxPlace.log:1234. The change is marked with
a timestamp comment in override.params.json. I also ran TileBuilderGenParams
to regenerate tile.params."
```

## Examples

### Example 1: Memory Increase for Failed Target

**Bash format:**
```bash
# File: override.params
# added by genie on 2026-02-12 11:45: OOM error at log line 1234
TILEBUILDER_RAM_FxPlace = 128000
```

**JSON format:**
```json
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "added by genie on 2026-02-12 11:45: OOM error at log line 1234"
  }
}
```

Then run:
```bash
TileBuilderGenParams
```

### Example 2: Switching Tools

**Bash format:**
```bash
# File: override.params
# modified by genie on 2026-02-12 14:20 (was: iccompiler2): ICC2 route failing, trying Fusion
CHOSEN_ROUTER = fusion
```

**JSON format:**
```json
{
  "params": {
    "CHOSEN_ROUTER": "fusion",
    "CHOSEN_ROUTER#": [
      "modified by genie on 2026-02-12 14:20 (was: iccompiler2)",
      "ICC2 route failing, trying Fusion Compiler instead"
    ]
  }
}
```

Then run:
```bash
TileBuilderGenParams
TileBuilderMake   # CHOSEN param requires Make
```

### Example 3: Multiple Debug Parameters

**Bash format:**
```bash
# File: override.params
# added by genie on 2026-02-12 15:30: enable detailed placement reports
PLACEMENT_DEBUG_REPORTS = true

# added by genie on 2026-02-12 15:30: save intermediate data for analysis
SAVE_INTERMEDIATE_STEPS = 1

# modified by genie on 2026-02-12 15:31 (was: 8): more CPUs for better QoR
TILEBUILDER_CPUS_FxPlace = 16
```

**JSON format:**
```json
{
  "params": {
    "PLACEMENT_DEBUG_REPORTS": "true",
    "PLACEMENT_DEBUG_REPORTS#": "added by genie on 2026-02-12 15:30: enable detailed reports",
    "SAVE_INTERMEDIATE_STEPS": "1",
    "SAVE_INTERMEDIATE_STEPS#": "added by genie on 2026-02-12 15:30: save data for analysis"
  },
  "FxPlace": {
    "TILEBUILDER_CPUS": "16",
    "TILEBUILDER_CPUS#": "modified by genie on 2026-02-12 15:31 (was: 8): more CPUs for better QoR"
  }
}
```

### Example 4: Complex JSON with Multiple Targets

```json
{
  "params": {
    "DEBUG_MODE": "1",
    "DEBUG_MODE#": "added by genie on 2026-02-12 10:00: enable debug for troubleshooting"
  },
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "modified by genie on 2026-02-12 10:15 (was: 64000): OOM at log line 1234",
    "TILEBUILDER_CPUS": "16",
    "TILEBUILDER_CPUS#": "added by genie on 2026-02-12 10:16: enable parallel placement"
  },
  "FxRoute": {
    "TILEBUILDER_RAM": "256000",
    "TILEBUILDER_RAM#": "added by genie on 2026-02-12 10:30: large design needs high memory",
    "TILEBUILDER_LSFQUEUE": "gb256",
    "TILEBUILDER_LSFQUEUE#": "added by genie on 2026-02-12 10:30: use high-mem queue"
  }
}
```

### Example 5: Long Comment with Array of Strings

```json
{
  "params": {
    "CUSTOM_OPTIMIZATION_FLAG": "aggressive",
    "CUSTOM_OPTIMIZATION_FLAG#": [
      "modified by genie on 2026-02-12 16:45 (was: conservative)",
      "User requested more aggressive optimization to meet timing goals.",
      "This may increase runtime but should improve WNS by ~50ps based on similar designs."
    ]
  }
}
```

## Cleanup Reminder

After debugging is complete, **always remind the user** to review AI changes:

**Template reminder:**
> "I made [N] parameter modification(s) marked with 'by genie' comments/tracking. Once debugging is complete:
>
> 1. Review bash-style params: `grep -A 1 'by genie' override.params override.controls`
> 2. Review JSON params: check override.params.json for parameters with "#" comment keys
> 3. Remove temporary debug parameters
> 4. Keep useful optimizations but remove tracking comments
> 5. Run `TileBuilderGenParams` after cleanup"

## Important Notes

### Don't Edit tile.params or params.json Directly

**NEVER** edit these files directly:
- `tile.params` - Generated file, will be overwritten
- `params.json` - Generated file, will be overwritten

**Always** edit:
- `override.params`
- `override.controls`
- `override.params.json`

Then run `TileBuilderGenParams`.

### Location Matters

Edit the override file in the run directory:
```bash
# Correct location
$ALLTILES_DIR/$NICKNAME/override.params
$ALLTILES_DIR/$NICKNAME/override.params.json

# Wrong location (ROOT_DIR copy is not used)
$ROOT_DIR/override.params
```

### Params vs Controls

While historically different (params = design settings, controls = system settings), the distinction has blurred. Both use identical syntax and are processed the same way.

**Recommendation:**
- Design-specific params → `override.params` or `override.params.json`
- LSF/tool/system params → `override.controls`

### JSON Comment Syntax

**Key points:**
- Comment keys are the parameter name plus "#" suffix
- Comment values can be a single string OR an array of strings
- Use arrays for long comments to avoid embedding `\n` characters
- Comments are ignored by TileBuilder but visible to users
- Each parameter can have its own comment

**Valid comment formats:**
```json
{
  "PARAM": "value",
  "PARAM#": "single line comment"
}
```

```json
{
  "PARAM": "value",
  "PARAM#": [
    "first line of comment",
    "second line of comment",
    "third line of comment"
  ]
}
```

## Troubleshooting Parameter Changes

| Problem | Cause | Solution |
|---------|-------|----------|
| Param change not taking effect | Forgot TileBuilderGenParams | Run `TileBuilderGenParams` |
| CMD file not updated | Template uses param but cmd not regenerated | Run `GenerateAllCommands` or `TileBuilderOverwriteCommand $TARGET` |
| Flow structure wrong | Changed CHOSEN param without Make | Run `TileBuilderMake` |
| Parameter in wrong file | Added to ROOT_DIR instead of ALLTILES_DIR/$NICKNAME | Edit correct file location |
| Can't find where param defined | Complex hierarchy | Use `TileBuilderGenParamsGrep PARAM_NAME` |
| Duplicate parameter | Param exists in both .params and .controls | Remove from one file (usually keep in .params) |
| JSON parse error | Invalid JSON syntax | Run `python -m json.tool override.params.json` to validate |
| Target-specific JSON not working | Wrong JSON structure | Use `{"TARGET_NAME": {"PARAM": "value"}}` format |
| JSON comment not showing | Forgot "#" suffix on param name | Add "#" to make `"PARAM#": "comment"` |

## Anti-Patterns to Avoid

❌ **Don't do this:**
```bash
# Bad: No tracking comment
TILEBUILDER_RAM_FxPlace = 128000

# Bad: No reason given
# added by genie on 2026-02-12
TILEBUILDER_RAM_FxPlace = 128000

# Bad: No original value on modification
# modified by genie on 2026-02-12: increased RAM
TILEBUILDER_RAM_FxPlace = 128000
```

```json
// Bad: No tracking comment in JSON
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000"
  }
}

// Bad: Wrong comment key (missing #)
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM_comment": "added by genie..."
  }
}

// Bad: Wrong JSON structure
{
  "TILEBUILDER_RAM_FxPlace": "128000",
  "TILEBUILDER_RAM_FxPlace#": "comment"
}
```

✅ **Do this:**
```bash
# Good: Complete tracking with reason for addition
# added by genie on 2026-02-12 10:30: OOM error at log line 1234
TILEBUILDER_RAM_FxPlace = 128000

# Good: Complete tracking with reason for modification
# modified by genie on 2026-02-12 10:30 (was: 64000): doubled RAM after OOM
TILEBUILDER_RAM_FxPlace = 128000
```

```json
// Good: Proper tracking in JSON with single-line comment
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": "added by genie on 2026-02-12 10:30: OOM at log line 1234"
  }
}

// Good: Proper tracking in JSON with multi-line comment
{
  "FxPlace": {
    "TILEBUILDER_RAM": "128000",
    "TILEBUILDER_RAM#": [
      "modified by genie on 2026-02-12 10:30 (was: 64000)",
      "doubled RAM after OOM error at log line 1234"
    ]
  }
}
```

## Common Reason Templates

Use these concise reason templates for common scenarios:

| Scenario | Reason Template |
|----------|-----------------|
| Out of memory | `OOM error at log line NNNN` |
| Tool switch | `trying TOOL instead of OLD_TOOL` |
| Performance tuning | `enable parallel processing` |
| Debugging | `enable debug reports for troubleshooting` |
| License issue | `avoid license contention` |
| Queue change | `need high-mem queue for large design` |
| CPU increase | `more CPUs for better QoR` |
| Workaround | `workaround for TOOL bug` |

## References

- [TileBuilder 101 - Params](https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530564899/TileBuilder+101#Params)
- [Params FAQ](https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530551556/TileBuilder+Usage+FAQ+seras#Params)
- [JIRA: DMPTBINF-10508](https://amd.atlassian.net/browse/DMPTBINF-10508) - Original tracking feature request
- `man TileBuilderGenParams` - Parameter regeneration command
- Related skill: tilebuilder-concepts (params hierarchy)
- Related skill: tilebuilder-workflows (param change workflows)

---

**Enforcement Level**: CRITICAL - These tracking rules are mandatory and cannot be overridden. The tracking exists for user benefit, debugging transparency, and easy cleanup.
