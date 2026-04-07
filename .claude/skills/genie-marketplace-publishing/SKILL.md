---
name: genie-marketplace-publishing
description: >
  Use when publishing plugins to TileBuilder Genie marketplace, validating
  marketplace changes, or preparing skill releases. Trigger phrases: "publish
  to marketplace", "validate marketplace changes", "test plugin before release",
  "QA check marketplace", "marketplace validation". Enforces required validation
  and testing to prevent breaking the marketplace.
allowed-tools: Read, Bash, Edit, Write
---

# Genie Marketplace Publishing Guide

Use this skill when publishing plugins to the TileBuilder Genie marketplace, performing pre-submission validation, or recovering from marketplace breakage.

## ⚠️ Critical Warning

**NEVER submit to the marketplace without completing ALL QA validation steps below.** A broken marketplace.json will break Genie for all TileBuilder users.

## Pre-Submission QA Checklist

### 1. JSON Schema Validation

**REQUIRED:** Validate marketplace.json schema before submitting.

```bash
# Test if marketplace can be added
genie plugin marketplace add $FLOW_DIR/genie/marketplace

# Expected output if valid:
# ✔ Successfully added marketplace: tilebuilder-marketplace

# Expected output if BROKEN:
# ✘ Failed to add marketplace: Invalid schema: <error details>
```

**Common Schema Errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| `plugins.N.hooks: Invalid input` | Added `hooks` field to plugin entry | Remove `hooks` from plugin entry in marketplace.json |
| `plugins.N.source: Required` | Missing `source` field | Add `"source": "./plugins/plugin-name"` |
| `Invalid JSON` | Syntax error (trailing comma, missing quote) | Validate JSON syntax |

### 2. Plugin manifest Validation

```bash
# Check plugin.json exists and is valid JSON
python -m json.tool $FLOW_DIR/genie/marketplace/plugins/my-plugin/.claude-plugin/plugin.json

# Verify required fields
grep -E '"name"|"description"|"version"|"author"' \
  $FLOW_DIR/genie/marketplace/plugins/my-plugin/.claude-plugin/plugin.json
```

**Required plugin.json fields:**
- `name` (string): Plugin identifier
- `description` (string): What the plugin does
- `version` (string): Semantic version
- `author` (object): name and email
- `keywords` (array): Search terms

### 3. Skill Validation

```bash
# Verify all SKILL.md files have valid frontmatter
for skill in $FLOW_DIR/genie/marketplace/plugins/my-plugin/skills/*/SKILL.md; do
  echo "Checking $skill..."
  # Extract frontmatter
  awk '/^---$/{if(++count==2) exit} count==1' "$skill" | grep -q "name:" || echo "ERROR: Missing 'name' in $skill"
  awk '/^---$/{if(++count==2) exit} count==1' "$skill" | grep -q "description:" || echo "ERROR: Missing 'description' in $skill"
done
```

### 4. Local Testing

**CRITICAL:** Test the marketplace locally before P4 submission.

```bash
# 1. Sync latest marketplace
cd $FLOW_DIR
p4 sync genie/marketplace/...

# 2. Make your changes (edit marketplace.json, add skills, etc.)

# 3. Test marketplace can be loaded
genie plugin marketplace add $FLOW_DIR/genie/marketplace

# 4. Test skills are discoverable
genie plugin marketplace list

# 5. Test a skill invocation (ask a question that should trigger your skill)
genie "What does my new skill do?"

# 6. Verify no errors in output
```

### 5. File Permissions Check

```bash
# Ensure files are readable
find $FLOW_DIR/genie/marketplace/plugins/my-plugin -type f -name "*.md" -o -name "*.json" | \
  xargs ls -la

# All files should have at least r--r--r-- permissions
```

### 6. No Sensitive Data

```bash
# Check for accidental credentials/secrets
grep -ri "password\|secret\|api_key\|token" \
  $FLOW_DIR/genie/marketplace/plugins/my-plugin/

# Should return no results
```

## Publishing Workflow

### Step 1: Create Plugin Structure

```bash
cd $FLOW_DIR/genie/marketplace/plugins
mkdir -p my-plugin/.claude-plugin
mkdir -p my-plugin/skills
```

### Step 2: Create plugin.json

```json
{
  "name": "my-plugin",
  "description": "Brief description of plugin functionality",
  "version": "1.0.0",
  "author": {
    "name": "Your Name",
    "email": "your.email@amd.com"
  },
  "homepage": "https://amd.atlassian.net/wiki/...",
  "license": "AMD Internal Use Only",
  "keywords": ["tilebuilder", "keyword1", "keyword2"]
}
```

### Step 3: Create Skills

See `genie/marketplace/CLAUDE.md` for skill format specification.

### Step 4: Register in marketplace.json

**⚠️ CRITICAL: Valid plugin entry schema**

```json
{
  "plugins": [
    {
      "name": "my-plugin",
      "source": "./plugins/my-plugin",
      "description": "Brief description",
      "version": "1.0.0",
      "author": {
        "name": "Your Name",
        "email": "your.email@amd.com"
      },
      "category": "productivity",
      "keywords": ["keyword1", "keyword2"]
    }
  ]
}
```

**❌ INVALID FIELDS (will break marketplace):**
- `hooks` - NOT allowed in plugin entry
- `enabled` - NOT allowed in plugin entry
- Custom fields - Only documented fields allowed

### Step 5: Run Complete QA Checklist

Execute all 6 validation steps above. **DO NOT SKIP THIS STEP.**

### Step 6: P4 Submission

```bash
# Add new files
p4 add genie/marketplace/plugins/my-plugin/...
p4 edit genie/marketplace/.claude-plugin/marketplace.json

# Create changelist
p4 change -o > /tmp/my_change.txt
# Edit description to include JIRA and testing statement

# Create numbered changelist
p4 change -i < /tmp/my_change.txt

# CRITICAL: Lock files before submitting (genie directory requires this)
p4 lock -c <changelist_number> genie/marketplace/...

# Submit
p4 submit -c <changelist_number>
```

**Required in changelist description:**
- JIRA ticket (e.g., DMPTBINF-XXXXX)
- Statement: "Tested locally with: genie plugin marketplace add"
- Brief description of what was added/changed

## Real-World Example: What Went Wrong (CL 848394 → 848425)

### The Mistake

**Broken submission (CL 848394):**
```json
{
  "plugins": [
    {
      "name": "fcfp-expert-skills",
      ...
      "keywords": ["fcfp", "floorplan"],
      "hooks": {
        "post-start": "./plugins/fcfp-expert-skills/hooks/post-start"
      }
    }
  ]
}
```

**Error:**
```
$ genie plugin marketplace add /tool/aticad/1.0/flow/TileBuilder/genie/marketplace
✘ Failed to add marketplace: Invalid schema: plugins.1.hooks: Invalid input
```

### The Fix (CL 848425)

Removed the invalid `hooks` field:
```json
{
  "plugins": [
    {
      "name": "fcfp-expert-skills",
      ...
      "keywords": ["fcfp", "floorplan"]
    }
  ]
}
```

### Why It Happened

- Added `hooks` field thinking it would register plugin hooks
- Did NOT test locally before submitting
- Schema validation only happens at marketplace load time
- Broke marketplace for all users until fix was submitted

### Prevention

✅ **ALWAYS run:** `genie plugin marketplace add $FLOW_DIR/genie/marketplace`
✅ **BEFORE:** P4 submission

## Hooks vs Plugin Entry

**Important distinction:**

| Where | Purpose | Location |
|-------|---------|----------|
| **Plugin hooks** | Executable scripts run during target execution | `$FLOW_DIR/genie/pre/`, `$FLOW_DIR/genie/post/` |
| **Plugin entry** | Metadata registration in marketplace | `marketplace.json` plugins array |

**Hooks are NOT configured in marketplace.json.** They are separate files in the `genie/pre/` or `genie/post/` directories.

## Recovery from Broken Marketplace

If marketplace is broken after submission:

```bash
# 1. Identify the error
genie plugin marketplace add $FLOW_DIR/genie/marketplace
# Read error message carefully

# 2. Check recent changes
p4 changes -m 5 genie/marketplace/.claude-plugin/marketplace.json

# 3. Compare to working version
p4 diff2 //depot/.../marketplace.json#N //depot/.../marketplace.json#N+1

# 4. Fix the issue
p4 edit genie/marketplace/.claude-plugin/marketplace.json
# Remove invalid fields, fix JSON syntax, etc.

# 5. Test fix locally
genie plugin marketplace add $FLOW_DIR/genie/marketplace

# 6. Submit fix immediately
p4 lock -c <changelist> genie/marketplace/.claude-plugin/marketplace.json
p4 submit -c <changelist>

# 7. Notify users
# Email TileBuilderInfrastructure@amd.com that marketplace is fixed
```

## Testing Best Practices

### Before Every Submission

```bash
#!/bin/bash
# Save as: validate_marketplace.sh

set -e

MARKETPLACE_DIR="$FLOW_DIR/genie/marketplace"

echo "=== Validating Marketplace ==="

# 1. JSON syntax
echo "✓ Checking JSON syntax..."
python -m json.tool "$MARKETPLACE_DIR/.claude-plugin/marketplace.json" > /dev/null

# 2. Schema validation
echo "✓ Checking schema..."
genie plugin marketplace add "$MARKETPLACE_DIR" 2>&1 | grep -q "Successfully added" || {
  echo "✘ Schema validation FAILED"
  genie plugin marketplace add "$MARKETPLACE_DIR"
  exit 1
}

# 3. Plugin manifests
echo "✓ Checking plugin manifests..."
for plugin_json in "$MARKETPLACE_DIR"/plugins/*/.claude-plugin/plugin.json; do
  python -m json.tool "$plugin_json" > /dev/null || {
    echo "✘ Invalid JSON: $plugin_json"
    exit 1
  }
done

# 4. Skill frontmatter
echo "✓ Checking skill frontmatter..."
for skill_md in "$MARKETPLACE_DIR"/plugins/*/skills/*/SKILL.md; do
  grep -q "^---$" "$skill_md" || {
    echo "✘ Missing frontmatter: $skill_md"
    exit 1
  }
done

echo "✅ All validations passed!"
echo "Safe to submit to P4"
```

### After Submission

```bash
# 1. Sync and verify others can use it
p4 sync $FLOW_DIR/genie/marketplace/...
genie plugin marketplace add $FLOW_DIR/genie/marketplace

# 2. Test skill invocation
genie "test question for new skill"

# 3. Monitor for issues
# Check email, Slack for reports of broken marketplace
```

## Common Pitfalls

| Mistake | Impact | Prevention |
|---------|--------|------------|
| Added invalid field to plugin entry | Breaks marketplace for all users | Always validate schema locally |
| Trailing comma in JSON | JSON parse error | Use `python -m json.tool` |
| Missing required field | Schema validation fails | Check against template |
| Forgot to lock files before submit | P4 submit fails | Always run `p4 lock -c <CL>` |
| Submitted without testing | Unknown breakage in production | **ALWAYS** test locally first |
| Sensitive data in skills | Security issue | Grep for secrets before submit |

## Related Documentation

- **Marketplace Developer Guide**: `$FLOW_DIR/genie/marketplace/CLAUDE.md`
- **Skill Format Specification**: `$FLOW_DIR/genie/marketplace/CLAUDE.md#skill-format-specification`
- **Hooks Documentation**: `$FLOW_DIR/lib/run_genie_hooks/README.md`
- **JIRA Project**: DMPTBINF (TileBuilder Infrastructure)

## Emergency Contacts

If marketplace is broken and you can't fix it:

1. **Email**: TileBuilderInfrastructure@amd.com
2. **File JIRA**: DMPTBINF project, High priority
3. **Slack**: #tilebuilder-infrastructure
4. **Direct escalation**: Eric Miller (emiller@amd.com)

## Summary: The Golden Rule

**Test locally BEFORE submitting to P4. ALWAYS.**

```bash
# This command must succeed before ANY P4 submission:
genie plugin marketplace add $FLOW_DIR/genie/marketplace
```

If this fails, **DO NOT SUBMIT**. Fix the issue first.

---

*Skill created based on real-world marketplace breakage (CL 848394 → 848425)*
*Last updated: 2026-01-23*
