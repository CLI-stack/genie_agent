---
name: jira-tickets
description: >
  Use when filing JIRA tickets, transitioning ticket status, or managing backlog
  for TileBuilder or DM/DE projects. Trigger phrases: "file a ticket", "create
  a bug for DMPTBINF", "transition ticket to Implemented", "show my open tickets",
  "file enhancement request". Handles DMPTB* project mapping, field inference,
  and workflow transitions.
allowed-tools: mcp__atlassian__jira_*, mcp__atlassian__confluence_*, Read, Grep
---

# JIRA Ticket Management for TileBuilder

This skill covers JIRA ticket creation, field inference, and workflow transitions for TileBuilder and related DM/DE projects.

## Supported Projects

### TileBuilder Platform Projects (DMPTB*)

| Project Key | Project Name | When to Use |
|-------------|--------------|-------------|
| **DMPTBINF** | TB Infrastructure | TileBuilder core utils (Start, GenParams, Make, Branch, Status), Seras flow engine |
| **DMPTBSPE** | Supra PNR/ECO | Place, Route, CTS, StreamOut, physical verification (DRC, LVS, ANT, XOR) |
| **DMPTBSSD** | Supra Synthesis/DFT | Synthesis and DFT flow issues |
| **DMPTBSPA** | Supra Analysis | Static timing analysis and other analysis flows |
| **DMPTBFCCK** | FC Clock | Clock tree synthesis, timing closure |
| **DMPTBFCFP** | FC Floorplan | Floorplanning, partitioning |
| **DMPTBFCNL** | FC Netlist | Netlist processing, connectivity |
| **DMPTBFCPV** | FC Physical Verification | DRC, LVS, antenna checks |
| **DMPTBSGN** | Signoff | Final signoff flows, tapeout preparation |

### Other Methodology Tool Projects

| Project Key | Project Name | When to Use |
|-------------|--------------|-------------|
| **DMOGMATIC** | Ogmatic | Metrics database, table management, PowerBI dashboards |
| **DMCNMBX** | CMCAD Baxe | Baxe extraction tool issues |
| **DMCMSIPRC** | CMCAD SIPRC | Physical verification waivers |
| **DMCNMIC** | CMCAD IC Manage/Perforce | IC Manage or Perforce access/issues |

## Project Key Inference Rules

| Keywords/Aliases | Project Key |
|------------------|-------------|
| "TileBuilder", "TB Infra", "GenParams", "Make", "Start" | DMPTBINF |
| "Supra PNR", "PNR", "Place and Route", "ECO", "StreamOut" | DMPTBSPE |
| "Supra Synthesis", "Supra DFT", "Synthesis", "DFT" | DMPTBSSD |
| "Supra Analysis", "timing analysis", "power analysis" | DMPTBSPA |
| "clock tree", "CTS", "clock synthesis" | DMPTBFCCK |
| "floorplan", "partitioning" | DMPTBFCFP |
| "DRC", "LVS", "antenna", "physical verification" | DMPTBFCPV |
| "Ogmatic", "ogmatic" | DMOGMATIC |

## Issue Type Inference

| Keywords in Request | Issue Type |
|---------------------|------------|
| "bug", "defect", "broken", "error", "fails", "crash" | Defect |
| "enhancement", "feature", "improve", "optimize", "add support for" | Enhancement |
| "task", "need to", "implement", "create", "update" | Task |

## DMPTBINF Required Fields

These 4 fields are REQUIRED to create a DMPTBINF ticket:

```python
REQUIRED_FIELDS = {
    "security": {"id": "10042"},  # General security level
    "customfield_10166": {"value": "Unknown"},  # SOC Program
    "customfield_10170": "TileBuilder",  # Revision Where-Found
    "customfield_10182": {"value": "TileBuilder Core Utils [Start GenParams Make Branch Status]"},  # Issue Category
}
```

## Ticket Creation Workflow

1. **Parse natural language request** - Extract project, issue type, summary, details
2. **Infer field values** - Apply inference rules for project key, issue type, category
3. **Prompt for ambiguities** - Ask user for unclear or missing information
4. **Show ticket summary** - Display all fields for user approval
5. **Create ticket (after approval)** - Only create after explicit "yes" confirmation
6. **Provide ticket URL** - Return the created ticket link

### Example

```
User: "File a bug that GenParams is slow on large designs"

AI: I'm preparing to create a ticket with these details:
    - Project: DMPTBINF (TileBuilder Infrastructure)
    - Type: Defect
    - Summary: GenParams is slow on large designs
    - Issue Category: TileBuilder Core Utils

    Should I proceed? (yes/no)

User: "yes"

AI: Created: DMPTBINF-12345
    URL: https://amd.atlassian.net/browse/DMPTBINF-12345
```

## Ticket Transitions

Tickets in DMPTBINF follow this workflow:
```
Opened -> Analyzed -> Assessed -> Implemented -> Closed
                                -> Rejected -> Closed
                                -> Deferred
```

### Transition to Implemented (ID: 181)

**Required Field**: Root Cause Category (customfield_10151)
**Default Value**: "AMD: New Requirement"

```python
# Step 1: Update the field
jira_update_issue(issue_key="DMPTBINF-XXXX",
    fields={"customfield_10151": {"value": "AMD: New Requirement"}})
# Step 2: Transition
jira_transition_issue(issue_key="DMPTBINF-XXXX", transition_id=181)
```

### Transition to Rejected (ID: 271)

**Required Field**: Rejection Reason (customfield_10150)
**Common Values**: "Rejected - Will Not Fix", "Rejected - Duplicate"

```python
# Step 1: Update the field
jira_update_issue(issue_key="DMPTBINF-XXXX",
    fields={"customfield_10150": {"value": "Rejected - Will Not Fix"}})
# Step 2: Transition
jira_transition_issue(issue_key="DMPTBINF-XXXX", transition_id=271)
```

### Transition to Closed (ID: 111 - "Accept")

**Prerequisites**: Ticket must be in "Implemented" or "Rejected" status
**Required Fields**: None

```python
jira_transition_issue(issue_key="DMPTBINF-XXXX", transition_id=111)
```

## Backlog Review Workflow

### Query for Old Opened Tickets

**CRITICAL**: Always filter by BOTH assignee AND status:

```python
jira_search(
    jql="assignee = currentUser() AND status = Opened AND created < '2025-01-01' ORDER BY created DESC",
    fields="summary,description,created,updated,status,priority,labels",
    limit=50
)
```

### Common Mistakes to Avoid

1. **Wrong Query Scope**: Always include `assignee = currentUser()` to avoid affecting others' tickets
2. **Not Filtering by Status**: Include `status = Opened` when reviewing backlog
3. **Setting Fields During Transition**: Update fields BEFORE transitioning, not during

## AI Assistant Guidelines

### DO:
- Infer project and fields from natural language
- Prompt user for ambiguous or missing information
- Show complete summary before creating
- Wait for explicit user approval ("yes", "create it", "go ahead")
- Provide ticket URL immediately after creation

### DON'T:
- Create tickets without user approval
- Guess project keys if uncertain - always prompt
- Use placeholder values for required fields
- Assign to non-@amd.com email addresses
- Skip the summary/approval step
