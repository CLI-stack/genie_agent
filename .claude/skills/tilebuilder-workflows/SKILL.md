---
name: tilebuilder-workflows
description: >
  Use when helping with TileBuilder operational workflows: starting workspaces
  (TileBuilderStart), running targets (TileBuilderRunThrough), branching
  (TileBuilderBranch), checking logs (TileBuilderCheckLogs), regenerating flow
  (TileBuilderMake). Trigger phrases: "how do I start a workspace?", "run
  FxPlace target", "create a branch", "check logs for errors", "regenerate
  commands". Does NOT cover editing params (use param-editing skill).
allowed-tools: Read, Grep, Glob, Bash, mcp__TileBuilderStart__*, mcp__TileBuilderGenParams__*, mcp__TileBuilderMake__*, mcp__TileBuilderBranch__*, mcp__TileBuilderRunThrough__*, mcp__TileBuilderCheckLogs__*, mcp__TileBuilderOverwriteCommand__*, mcp__TileBuilderShow__*, mcp__TileBuilderCone__*, mcp__TileBuilderForeachRundir__*, mcp__TileBuilderPredict__*
---

# TileBuilder Common Workflows

This skill covers the common workflows and commands for working with TileBuilder.

## Starting a Workspace

```bash
source /tool/aticad/1.0/src/sysadmin/cpd.cshrc  # Set up environment
cd /proj/my_area/my_workspace
# Create/copy override.params with NICKNAME, project settings
TileBuilderStart --params override.params
```

This creates:
- Colored TileBuilderTerm (xterm)
- TileBuilderConsole (Seras GUI)
- Flow code checkout in TileBuilder/
- Run directory in main/pd/tiles/$NICKNAME/

## Making Param Changes

```bash
# Edit override.params or override.controls
vi override.params
TileBuilderGenParams                  # Regenerate resolved params
TileBuilderMake                       # Update flow graph (if needed)
GenerateAllCommands                   # Regenerate cmd files (if needed)
```

**Important**: After any change to `override.controls`, you will need to rerun TileBuilderGenParams and then TileBuilderMake. Your job should now reflect these new settings.

## Running Targets

### From GUI
Select target → Press "Run" button

### From Command Line

```bash
# Using TileBuilderRunThrough (preferred)
TileBuilderRunThrough TargetName

# Using serascmd directly
serascmd --find_jobs 'name==$TARGET_NAME set=~$NICKNAME' --action run
```

### Run Until a Specific Target

To run only until a particular target and stop there, use the GUI's "Run Upcone" option or:
```bash
serascmd --find_jobs 'name==TargetName' --action run
```
This runs only the target and its upstream dependencies.

## Branching Experiments

Use `TileBuilderBranch` to experiment with different options without affecting the main flow:

```bash
TileBuilderBranch --startfrom RoutingTarget --params branch_override.params
```

**Example use case**: If you've completed CTS, and now you want to experiment with different routing options.

## Adding Another Tile to a Session

In a TileBuilderTerm:

```bash
# 1. Create a new directory
mkdir my_new_directory

# 2. Create/copy override files with unique NICKNAME
cd my_new_directory
cp ../other_directory/override.* .
echo 'NICKNAME = MY_NEW_RUN' >> override.params

# 3. Run TileBuilderGenParams and TileBuilderMake
TileBuilderGenParams
TileBuilderMake
```

## Checking Logs

```bash
TileBuilderCheckLogs logs/TargetName.log
```

Opens log with vim highlighting errors/warnings. Runs automatically after targets complete.

## Handling Failed Jobs

### Skip a Failed Job

If you've reviewed the reason for the failure and determined it's okay to ignore:
1. Select the job in the GUI
2. Press the **Skip** button

### Get Notified on Job Errors

**Email notification** - Add to `target.controls`:
```
TARGET_ERROR_SCRIPT_$MyTarget = (echo "ERROR in target $TARGET_NAME";date) | mail -s "ERROR in $VOV_PROJECT_NAME $TARGET_NAME" $USER@amd.com
```

**GUI alert** - Double-click a job → "Job info" → "Overview" tab → check "Alert on finish"

## LSF Integration

### Basic LSF Params

Jobs are dispatched to AMD's LSF compute farm with settings controlled by params:

| Param | Description |
|-------|-------------|
| `TILEBUILDER_LSFPROJECT` | Which project to charge (set by project module) |
| `TILEBUILDER_LSFQUEUE` | Which queue (regr_high, fstsim, gb128, etc.) |
| `TILEBUILDER_RAM_TargetName` | Memory request in MB |
| `TILEBUILDER_CPUS_TargetName` | CPU count |
| `TILEBUILDER_EXECARCH` | OS version (rhel7, rhel8, rhel8or7, etc.) |
| `TILEBUILDER_LSFSELECT` | Additional select constraints |

### Changing LSF Project or Queue

**The TileBuilder way** - Add to `override.controls`:
```
TILEBUILDER_LSFQUEUE = pdq_hdama
TILEBUILDER_LSFQUEUE_IcPlace = pdq_hdama    # Target-specific

TILEBUILDER_LSFPROJECT = hdama-pd
TILEBUILDER_LSFPROJECT_IcPlace = hdama-pd   # Target-specific
```
Then run `TileBuilderGenParams` and `TileBuilderMake`.

**The GUI way** - Click the pencil icon next to those fields in the JobDialog. Note: GUI changes are not saved to param files.

### Adding High-Memory Flags

To add gb128/gb256/gb512/gb1024 flags:
```
TILEBUILDER_LSFQUEUE = gb128
TILEBUILDER_LSFQUEUE_BigTarget = gb256
```

### Requesting More Memory

If your job is killed for using too much memory:
1. Look at the log for the actual memory used
2. Increase `TILEBUILDER_RAM_TargetName` to ~2x that amount
3. Run `TileBuilderGenParams` and `TileBuilderMake`
4. Rerun the job

## Running Commands Across Multiple Directories

```bash
TileBuilderForeachRundir --cmd 'your_command_here'
```

Or use the MCP tool `mcp__TileBuilderForeachRundir__tile_builder_foreach_rundir`.

## Cleaning Up

### Delete a Run Directory

```bash
# First stop all jobs and server
seras -shutdown
# Or use: Workspace → Stop Server

# Then clean up
TileBuilderClean $NICKNAME
# Or: rm -rf $ALLTILES_DIR/$NICKNAME

# Close all TileBuilderTerms for that workspace
```

## Common Commands Reference

| Command | Purpose |
|---------|---------|
| `TileBuilderStart` | Create new TileBuilder session |
| `TileBuilderTerm` | Open colored terminal with flow environment |
| `TileBuilderConsole` | Open Seras GUI |
| `TileBuilderGenParams` | Regenerate params from overrides |
| `TileBuilderMake` | Rebuild flow graph (after param/target changes) |
| `TileBuilderBranch` | Create experimental branch |
| `TileBuilderClean` | Clean up old runs |
| `TileBuilderSync` | Sync flow code to newer version |
| `TileBuilderUpdateSeras` | Update Seras to latest version |
| `TileBuilderCheckLogs` | Check logs for errors/warnings |
| `TileBuilderRunThrough` | Run target from command line |
| `TileBuilderForeachRundir` | Run commands across multiple run directories |
| `serascmd` | Command-line interface to Seras |

## Troubleshooting

### Why is my job QUEUED (blue) forever?

A queued job is waiting for its input dependencies. Check what's holding it up:
- Look at the job's input files in the GUI
- Check if upstream jobs have failed

### Why is my job PENDING (purple) forever?

First, check the **Reason** field in the JobDialog. Then press the "Why?" button next to "PENDING" to run `bjobstat` for full details.

**Common causes:**

| Cause | Solution |
|-------|----------|
| **Incorrect license requirements** | Check "tokens" field, fix with `PT_SHELL_LSFRESOURCE = primetimeX` |
| **/tmp is full** | Check with `df -hl /tmp` on server machine |
| **Resource requests too big** | Check RAM/CPU counts for extra zeros |
| **No access to resource** | Remove fstsim from LSFQUEUE or dedicated from LSFSELECT |

### Why was my job killed with TERM_ADMIN after 15 days?

LSF has a 15-day default runtime limit. Request a longer limit or check why the job ran so long.

### Job Out of Memory

If you see `wide1800ERROR: Your job did not finish properly`:
1. Check the log for actual memory usage
2. Increase `TILEBUILDER_RAM_TargetName` in override.controls
3. Rerun `TileBuilderGenParams` and `TileBuilderMake`
4. Rerun the job

## Using Specific Seras Version

```bash
TB_SERASMODULE=/proj/verif_release/seras/42/modulefile TileBuilderStart <opts>
```

## AI-Powered MCP Tools

The following MCP tools are available for TileBuilder operations:

| Tool | Purpose |
|------|---------|
| **TileBuilderStart** | Create new TileBuilder sessions |
| **TileBuilderGenParams** | Regenerate resolved parameters |
| **TileBuilderMake** | Rebuild the flow graph |
| **TileBuilderBranch** | Create experimental flow branches |
| **TileBuilderRunThrough** | Run targets and dependencies |
| **TileBuilderCheckLogs** | Analyze target logs for errors |
| **TileBuilderShow** | Display target information |
| **TileBuilderCone** | Query target dependency chains |
| **TileBuilderForeachRundir** | Execute commands across multiple run directories |
| **TileBuilderOverwriteCommand** | Regenerate command files from templates |
| **TileBuilderPredict** | Predict LSF resource requirements |

## Important Notes

### Session vs Run vs Tree

| Term | Definition |
|------|------------|
| **Tree/Workspace** | Root directory ($ROOT_DIR) containing everything |
| **Session** | One Seras server + one or more runs, one GUI |
| **Run** | One tile configuration with unique params, lives in main/pd/tiles/$NICKNAME |

### Memory and CPU Management

- TileBuilder predicts requirements automatically (TileBuilderPredict)
- Avoid manual overrides unless prediction fails
- Override via `TILEBUILDER_RAM_TargetName` and `TILEBUILDER_CPUS_TargetName`
- Jobs killed for excess memory usage should have RAM increased ~2x

## Documentation References

- **Usage FAQ**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530551556
- **TileBuilder 101**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530564899
- **New User Debug FAQ**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530564425
