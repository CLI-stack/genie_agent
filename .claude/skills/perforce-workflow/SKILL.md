---
name: perforce-workflow
description: >
  Use when working with Perforce (p4) for TileBuilder development: creating
  changelists, editing files under p4, submitting changes, or managing versions.
  Trigger phrases: "create a p4 change", "p4 edit this file", "submit to
  perforce", "shelve changes", "p4 diff", "revert changes". Handles p4 commands
  and TileBuilder code submissions with change-first workflow.
allowed-tools: Bash, Read, Grep, Glob
---

# Perforce Workflow for TileBuilder Development

This skill covers Perforce (p4) workflows for making changes to TileBuilder code and related files.

## Important: Perforce Areas

**CRITICAL**: Never commit anything without explicit approval. All commits require a JIRA ticket in the commit comment.

## Complete Workflow

### Step 1: Create an Empty Changelist

First, create an empty changelist with a proper description:

```bash
p4 change -o | grep -v "^Files:" | sed '/<enter description here>/c\
\tYour commit message here\
\t\
\tJIRA-TICKET-123' | p4 change -i
```

**Important**:
- Use `grep -v "^Files:"` to remove the Files section, creating an empty changelist
- Always include a JIRA ticket reference (e.g., DMPTBINF-10235)
- The output will show: `Change XXXXX created.`

### Step 2: Add Files to the Changelist

Open files for edit in the specific changelist:

```bash
# For existing files
p4 edit -c XXXXX file1.md file2.json

# For new files
p4 add -c XXXXX newfile.md
```

### Step 3: Verify the Changelist

Check that only your intended files are in the changelist:

```bash
p4 describe -s XXXXX
```

This shows:
- The changelist description
- JIRA ticket reference
- List of affected files
- Status (pending/submitted)

### Step 4: Review Changes

View diffs before submitting:

```bash
p4 diff file1.md file2.json

# Or see all changes in the changelist
p4 describe XXXXX
```

### Step 5: Submit the Changelist

```bash
p4 submit -c XXXXX
```

**Important**:
- Don't use `p4 submit file1 file2` - p4 submit doesn't accept file arguments
- Use `p4 submit -c <changelist_number>` to submit a specific changelist
- Submitting without `-c` submits the entire default changelist

## Complete Example

```bash
# 1. Create empty changelist
p4 change -o | grep -v "^Files:" | sed '/<enter description here>/c\
\tAdd pd-guru MCP server to tbgenie configuration\
\t\
\tDMPTBINF-10235' | p4 change -i
# Output: Change 838501 created.

# 2. Edit files in that changelist
p4 edit -c 838501 .mcp.json
p4 edit -c 838501 tbgenie-CLAUDE.md

# 3. Make your changes to the files
# (edit files with your editor or Edit tool)

# 4. Verify only those files are in the changelist
p4 describe -s 838501
# Should show only .mcp.json and tbgenie-CLAUDE.md

# 5. Review the diffs
p4 diff .mcp.json tbgenie-CLAUDE.md

# 6. Submit
p4 submit -c 838501
```

## Common Mistakes to Avoid

| Mistake | Problem | Correct Approach |
|---------|---------|------------------|
| `p4 change -o | p4 change -i` without removing Files section | Pulls in ALL files from default changelist | Use `grep -v "^Files:"` to create empty changelist |
| Creating changelist expecting it to be empty | Will include all pending files | Always create empty changelist first, then move specific files |
| `p4 submit file1 file2` | p4 submit doesn't accept file arguments | Use `p4 submit -c <changelist_number>` |

## Other Useful Commands

### Check What's Open in Default Changelist

```bash
p4 opened
```

### Move Files Between Changelists

```bash
# Move to default changelist
p4 reopen -c default file.md

# Move to specific changelist
p4 reopen -c 838501 file.md
```

### Delete a Changelist

```bash
# Only works if changelist has no files
p4 change -d XXXXX

# If files are in it, move them first
p4 reopen -c default file1.md file2.md
p4 change -d XXXXX
```

### View File History

```bash
p4 filelog -l -m 5 filename.md
```

This shows recent changelists, who made them, and the commit messages (including JIRA tickets).

## Version Management

When making changes to tbgenie files, always update versions:

1. **tbgenie-CLAUDE.md**: Update `TBGENIE_VERSION` and `TBGENIE_GENERATED` headers
   ```markdown
   <!-- TBGENIE_VERSION: 1.0.1 -->
   <!-- TBGENIE_GENERATED: 2025-11-15 -->
   ```

2. **.mcp.json**: Update `_tbgenie_metadata.version` field
   ```json
   {
     "_tbgenie_metadata": {
       "version": "1.0.1",
       "generated": "2025-11-15"
     }
   }
   ```

## Finding the Right JIRA Ticket

When submitting without a specified JIRA ticket:

1. **Check recent file history**: Use `p4 filelog -l -m 5 <filename>` to see what JIRA ticket was used in recent changes
2. **Ask the user**: Present the previously used ticket and ask if they want to reuse it
3. **Wait for confirmation**: Never assume - always get explicit user approval for which ticket to use

## TileBuilder Perforce Integration

- Flow code is checked out from Perforce per workspace
- Users have local copies immune to CAD checkins
- Sync specific files with `p4 sync` when CAD provides fixes
- Login with `TileBuilderP4Login`, may take up to an hour to propagate

## Testing Changes Before Submission

Before submitting:
1. Verify JSON files are valid: `python3 -m json.tool .mcp.json`
2. Check markdown syntax
3. Verify file sizes remain reasonable
4. Test MCP server configuration if modified
