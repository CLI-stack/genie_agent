---
name: seras-debugging
description: >
  Use when debugging Seras flow engine issues, understanding job/file states,
  troubleshooting GUI sync problems, or investigating database corruption.
  Trigger phrases: "job stuck PENDING", "GUI won't update", "serascmd help",
  "database locked", "seras -sanity", "job states explained", "why is job
  BLOCKED". Covers Seras-specific questions, serascmd usage, and flow debugging.
allowed-tools: Read, Grep, Glob, Bash, mcp__TileBuilderShow__*, mcp__TileBuilderCone__*, mcp__TileBuilderCheckLogs__*
---

# Seras Flow Engine Debugging

This skill covers the Seras flow engine, job states, serascmd usage, and troubleshooting.

## Seras Overview

Seras is a graphical flow management tool that is the core of TileBuilder. It:
- Manages job dependencies and execution order
- Dispatches jobs to LSF compute farm
- Provides the graphical console
- Tracks file changes and invalidation

## Key Terminology

| Term | Definition |
|------|------------|
| **Workspace** | Directory where you build and run a Seras flow. In TileBuilder-speak, this is a *session* (not a *run*) |
| **Job** | Single command that will be run (same as TileBuilder *target*) |
| **File** | Any input, output, or intermediate file in the flow |
| **Mux** | Special job that dynamically chooses which input to pick up (basically a symlink you can change through GUI) |
| **Dependency** | Requirement establishing order between jobs (file dependency or job dependency) |
| **Upstream/Downstream** | Jobs that run before (upstream) or after (downstream) a given job. Also called *upcone*/*downcone* |
| **Set** | Collection of jobs logically grouped together. Sets may contain other sets (hierarchical). All flows belong to top-level set "top" |
| **Throttle** | Mechanism to limit concurrent jobs (e.g., for licensing) |
| **Priority** | Value determining which jobs run next (higher = higher priority) |
| **Gates** | Job-to-job dependencies (as opposed to file dependencies) |
| **Dispatch precedence** | Order of execution methods to try: local, lsf, tblsf, gcp, azure, aws |

## Job States

| Status | Description |
|--------|-------------|
| **NOTRUN** | Job has never run, was reset due to upstream changes, or was manually reset |
| **QUEUED** | Queued for running but not started, waiting for upstream to complete |
| **CHECKING_INPUT** | Seras is checking if input is ready |
| **INPUT_OK** | Job's inputs have shown up |
| **PREDICTING** | Job is having its resources (memory) predicted |
| **RUNNABLE** | Ready to send to LSF or run locally |
| **PENDING** | Submitted to LSF but pending (usually: licenses unavailable or unreasonable memory request) |
| **RUNNING** | Job is running |
| **STOPPING** | Job has finished, waiting for NFS to determine success/failure |
| **PASSED** | Completed and all pass criteria met (errcode=0, all outputs created) |
| **FAILED** | Completed but at least one pass criteria not met |
| **WARNING** | Completed with special errcode indicating warnings (e.g., DRC violations to review) |
| **SKIPPED** | Manually flagged to skip. Can skip NOTRUN (prevent running) or FAILED (continue despite failure) |
| **WAIVED** | A failed job manually flagged to skip |
| **BLOCKED** | At least one input is a primary input that does not exist |
| **UPLOADING** | Cloud job uploading data |
| **DOWNLOADING** | Cloud job downloading data |

## File States

| Status | Description |
|--------|-------------|
| **MISSING** | Primary input that does not exist |
| **NOTREADY** | Intermediate input that does not exist yet |
| **READY** | File exists with expected timestamp or checksum |
| **EDITED** | File exists but timestamp/checksum is unexpected. Treated as READY, but editing causes downstream jobs to become NOTRUN |

## serascmd Command-Line Interface

`serascmd` is the generalized tool for flow queries and manipulation.

### Querying

Queries are triggered by `--find_jobs`, `--find_files`, and `--find_sets` switches. Arguments are space-separated predicates that are logically AND'd.

Each predicate is `$field$operator$value` or the value "all".

#### Operators

| Operator | Meaning |
|----------|---------|
| `==` | Value must be equal |
| `!=` | Value must not be equal |
| `=~` | Value must match (case-sensitive) |
| `!~` | Value must not match |

#### Job Fields

| Field | Description |
|-------|-------------|
| **name** | Job's given name (not necessarily unique) |
| **number** | Job's assigned number (unique) |
| **status** | NOTRUN, QUEUED, PENDING, RUNNING, PASSED, WARNING, FAILED, SKIPPED, BLOCKED |
| **command** | Full commandline of the job |
| **dir** | Run directory (relative to workspace root) |
| **host** | Which machine it ran on |
| **set** | CSV of full hierarchical sets (e.g., "top::blah::blah") |
| **starttime** | When job started (epoch-seconds) |
| **endtime** | When job ended (epoch-seconds) |
| **duration** | Time between start and end (-1 if never run) |

#### File Fields

| Field | Description |
|-------|-------------|
| **path** | File's dir + basename (may be relative) |
| **abspath** | File's absolute path |
| **name** | File's basename |
| **dir** | File's dirname |
| **number** | File's assigned number (unique) |
| **status** | MISSING, NOTREADY, READY, EDITED |

### Query Examples

```bash
# Find job by name
serascmd --find_jobs "name==TileBuilderStart"

# Find failed jobs
serascmd --find_jobs "status==FAILED"

# Find unrun or failed jobs in a specific directory
serascmd --find_jobs "name!=AutoRetrace status=~NOTRUN|FAILED dir=~$dir_to_check"

# Find all files matching a pattern
serascmd --find_files "path=~.db status==READY"

# Find a specific set
serascmd --find_sets "fullname==top::placement"

# List all jobs
serascmd --find_jobs all
```

### Reporting

Reporting is enabled with `--report` switch. Argument is space-separated list of fields.

```bash
# List all failed jobs with their command
serascmd --find_jobs 'status==FAILED' --report 'number name command'

# Show status and memory for a specific job
serascmd --find_jobs "name==FxPlace" --report "status duration predicted_memory"
```

#### Additional Report Fields (Jobs)

| Field | Description |
|-------|-------------|
| **expected_wall_time** | Expected wall time for job |
| **history** | History of all changes (status, RAM changes) |
| **memory_limit** | Artificial memory limit |
| **memory_override** | Specific memory amount to use |
| **num_cores** | Number of cores |
| **predicted_memory** | External memory prediction |
| **upstream_jobs** | Numbers of all upstream jobs |
| **downstream_jobs** | Numbers of all downstream jobs |
| **input_files** | Numbers of job's input files |
| **output_files** | Numbers of job's output files |
| **stdout_path** | Path to stdout file |

#### Additional Report Fields (Files)

| Field | Description |
|-------|-------------|
| **producer_jobs** | Numbers of jobs that produce this file |
| **consumer_jobs** | Numbers of jobs that use this file |

### Actions

Actions are enabled with `--action` switch.

#### Execution Actions (Jobs)

| Action | Description |
|--------|-------------|
| **run** | Start job execution |
| **rerun** | Reset and rerun (even if passed). Running jobs will be marked to rerun when finished |
| **stop** | Stop running job |
| **skip** | Mark as skipped |
| **unskip** | Remove skip status |
| **reset** | Reset to NOTRUN |
| **delete** | Remove job from flow |
| **add-to-set=$name** | Add job to a named set |

#### Manipulation Actions (Jobs)

These require `-value $VALUE`:

| Action | Description |
|--------|-------------|
| **set-predicted-memory** | Override memory prediction |
| **set-memory-limit** | Set artificial memory limit |
| **set-memory-override** | Force specific memory amount |
| **set-num-cores** | Set CPU count |
| **set-expected-wall-time** | Set expected runtime |
| **set-tmp-override** | Set /tmp space override |

#### Actions for Files

| Action | Description |
|--------|-------------|
| **run** | Run |
| **delete** | Delete |

#### Actions for Sets

| Action | Description |
|--------|-------------|
| **run** | Run all jobs in set |
| **stop** | Stop all jobs in set |
| **skip** | Skip all jobs in set |
| **reset** | Reset all jobs in set |

### Action Examples

```bash
# Rerun all failed jobs
serascmd --find_jobs "status==FAILED" --action rerun

# Stop a specific job
serascmd --find_jobs "name==SlowTarget" --action stop

# Run everything in a set
serascmd --find_sets "fullname==top::routing" --action run

# Set memory for a job
serascmd --find_jobs "name==BigTarget" --action set-memory-override -value 128000

# Run everything
serascmd --find_sets "fullname==top" --action run
```

### Piping and Chaining

```bash
# Find failed jobs, then show what they blocked
serascmd --find_jobs "status==FAILED" --report "downstream_jobs" | \
  xargs serascmd --report "number name" --jobs

# Reset and rerun specific jobs
serascmd --find_jobs "name=~Place status==FAILED" | \
  xargs serascmd --action reset --jobs
```

### Supplying Lists Directly

Instead of searching, you can supply lists directly:

| Switch | Description |
|--------|-------------|
| `--jobs` | CSV of job numbers |
| `--files` | CSV of file numbers |
| `--sets` | CSV of full set paths |
| `--filepaths` | CSV of file paths (or "-" to read from stdin) |

## Debugging Seras Issues

### Step 1: Run seras -sanity

**First thing to do** - automatically checks for common issues:

```bash
seras -sanity
# Or use latest version:
/home/cverbur/p4_head/seras1p5/bin/seras -sanity
```

**Do NOT** automatically add `-force` or `-fix`. Let -sanity tell you what problems it finds first, then decide if they warrant destruction.

### Step 2: GUI Not Updating

If GUI isn't showing current state ("buttons don't work", "job finished but still shows RUNNING"):

1. **Workspace -> Re-sync GUI** menu option (versions 38+)
2. NFS lag can cause GUI desync - this is a user-accessible workaround
3. Less problematic in versions 70+

### Step 3: Update Seras

Many issues fixed in recent versions:

```bash
TileBuilderUpdateSeras
```

### Step 4: View Database State

The database is the **gold standard** for workspace state (trumps GUI display):

```bash
seras -showdb db.txt    # Write to file (recommended)
```

**Absolutely do NOT run** `seras -showdb | less` on versions before 33 - it keeps a lock on the database while browsing. On versions > 32, it loads the entire dump into RAM first.

### Step 5: Database Corruption

For catastrophic issues (jobs skipping states, schema update errors):

```bash
sqlitebrowser $TB_SRV_DIR/.seras/flow.db
```

**Always backup first!** Manual database editing is a last resort for CAD intervention.

#### Common Catastrophic Issues

1. **Jobs going NOTRUN->STOPPING** (skipping RUNNING) - usually the incremental-reload-job-renumbering problem
2. **Schema update crashes** claiming fields already exist - usually caused by syncing back to old versions then syncing forward again

#### Fixing Schema Version

For the error `[ERROR] problem trying to update the schema from version 20230104 to 20230407`:

1. Run `seras -sanity` (versions 46+) to find actual schema version
2. Look for: `[ok] Your database is really on schema 20230428.`
3. Or: `[PROBLEM] Your database says it's on schema 20230104, but it's actually on 20230328.`

This happens when you sync backwards and run TileBuilderMake. Requires CAD intervention to fix.

## Session Management

### Getting List of Sessions

**GUI way**:
```bash
seras  # With no arguments - pops up GUI listing workspaces
```
From there you can Reconnect or Kill sessions.

**Command-line way**:
```bash
seras -list_workspaces
```

### Adding a New Run

**Method 1**:
1. Make a new NICKNAME directory in $ALLTILES_DIR, cd to it
2. Create override.params and override.controls (change NICKNAME field)
3. Run `TileBuilderGenParams`
4. Run `TileBuilderMake`

**Method 2**:
1. cd to $ROOT_DIR
2. Create override.params/override.controls with unique NICKNAME
3. Run `TileBuilderStart --params --override`

### Deleting a Run

**Best approach**: Run `TileBuilderClean` on a nickname.

**Manual approach**: `seras -delete_set <set name>`

## Reporting Problems

For Seras problems:
1. File a ticket on DMPTBINF project (Seras has its own category)
2. Or email `TileBuilderInfrastructure@amd.com`
3. Or email `dl.SerasUsers` (announcements list)

**Always provide your working directory!**

## Documentation References

- **serascmd**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530584275
- **Seras Basics**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530561536
- **Debug Seras Problems**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530567895
- **Seras Landing Page**: https://amd.atlassian.net/wiki/x/IK6fHw
