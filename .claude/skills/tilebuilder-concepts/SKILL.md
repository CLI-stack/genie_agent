---
name: tilebuilder-concepts
description: >
  Use when explaining TileBuilder core concepts, terminology, directory structure,
  targets, families, personalities, params, controls, templates, or tunables.
  Trigger phrases: "what is a target?", "explain params hierarchy", "what's the
  difference between params and controls?", "what are families?", "how do
  templates work?", "what is CHOSEN_ROUTER?". Covers TileBuilder architecture
  and fundamentals.
allowed-tools: Read, Grep, Glob, Bash
---

# TileBuilder Core Concepts

This skill provides foundational knowledge about TileBuilder, AMD's comprehensive flow management tool for digital design.

## Overview

TileBuilder is a graphic flow management tool built on top of Seras. It productizes the million individual steps in digital design (LVS, routing, design checks, RTL verification, etc.) as executable **targets** that designers can run, with a GUI to monitor flow progress.

No one designer wants to see all million targets. Some targets are only for SAPR, some only for stdcell work, some only for integration, etc. To that end, the million targets are gathered into high-level groups called **families**.

## Targets

A **target** is the basic coding unit - a single atomic step in the flow. Targets are based around a CSH script which performs 4 basic tasks:
1. Declare inputs and outputs
2. Set up the working environment
3. Perform the core task (usually by calling a tool script)
4. Perform postprocessing

### Target Naming Convention

Target names start with a two-character prefix identifying the main tool they run:

| Prefix | Tool |
|--------|------|
| **Ic** | ICCompiler |
| **I2** | ICCompiler2 |
| **Fx** | Fusion Compiler |
| **Pt** | PrimeTime |
| **Rc** | RTL Compiler |
| **Sh** | Generic shell script |
| **Py** | Python script |
| **Pl** | Perl script |

Examples:
- `I2Route` - routing done in ICCompiler2
- `FxPlace` - placement in Fusion Compiler
- `PtTimFuncSS0p72vrcbest0css0p72v0cEcoRouteSxHld` - PrimeTime generating SI hold timing report

**Documentation**: Run `man $TARGET` to get information about any target.

### Generic Targets

A **generic target** is a target implementation that can appear multiple times in the same flow under different names. This avoids copying csh files:

- Common code goes into `Generic$TARGET.csh` (e.g., `GenericPtTiming.csh`)
- A param `INSTANCES_Generic$TARGET_LIST` specifies the symlinks to create
- Each instance gets a unique name but shares the same implementation

## Families and Personalities

### Families

**Families** group targets by high-level design area:

| Family | Description |
|--------|-------------|
| **Supra** | Block-level synthesis, placement, routing |
| **LibraryBuilder** | Stdcell library assembly |
| **FCNL** | Full Chip Net Listing - joins tile-level netlists and constraints |
| **FCFP** | Full Chip Floor Plan |
| **FCCHECK** | Full Chip CHECKs - verification including LEC and LP |
| **FCPV** | Full Chip Physical Verification - DRC, LVS |

A given target can be included in any number of families.

### Personalities

**Personalities** are smaller subgroups within families. For example, Supra has:
- **FEINT** (Front End INTegration)
- **analysis**: Static Timing Analysis
- **Rqual** (RTL Qualification)
- **benchmarking**

A given target can be included in any number of personalities.

## Actions

An **action** is a set of related targets performing similar tasks. This includes:
- All substeps in a process (e.g., *route* action includes Route, OptRoute, and ReRoute)
- All tool versions of a target (e.g., I2Route, FxRoute, InRoute)

All targets in an action are found in the same directory within the family.

## Directory Structure

The original directory where you ran TileBuilderStart is called the **ROOT_DIR**:

```
$ROOT_DIR/                           # Where TileBuilderStart was run
├── TileBuilder/                     # Flow code checkout (FLOW_DIR)
│   ├── $FAMILY/
│   │   ├── actions/                 # Target CSH scripts
│   │   ├── templates/               # Command file templates
│   │   └── params/                  # Flow-level params
│   └── util/                        # Utilities
├── vov/                             # Seras flow engine data
└── main/pd/tiles/                   # Design data (ALLTILES_DIR)
    └── $NICKNAME/                   # A run directory (symlink to unique name)
        ├── cmds/                    # Expanded tool cmd files (from GenerateAllCommands)
        ├── data/                    # Final versions of design data
        ├── logs/                    # Each target writes its log here
        ├── rpts/                    # Reports
        ├── runs/                    # Scratch area where jobs actually run
        ├── tech/                    # Stdcell data (from BuildTechDir)
        ├── tune/                    # Tunables (from UpdateTunable)
        ├── tile.params              # Final resolved params
        └── params.json              # JSON format params
```

**Key Points**:
- **TileBuilder/**: Perforce checkout of TileBuilder code. Your session is immune to CAD checkins because it's using your own local version.
- **vov/**: Where the flow manager (Seras) runs. Only need to look here for system-level problems.
- **main/pd/tiles/**: All your design data (ALLTILES_DIR).
- The NICKNAME is actually a symlink to a directory with the run's unique name.

## Params and Controls

### Params Hierarchy

**Params** are configuration settings that control flow behavior. They follow a hierarchy where lower levels override upper levels:

1. general
2. family
3. personality
4. technology
5. stack
6. project
7. project personality
8. chip_release
9. feint feedback
10. user (override.params/override.controls)

Source params live in:
- `$FLOW_DIR/` (for general.params)
- `$FLOW_DIR/$FAMILY/params/` (for flow-based params)
- `$ALLTILES_DIR/params/` (for design-specific params)

### Rendering Params

Run `TileBuilderGenParams` to render params from all sources into final values at:
- `$ALLTILES_DIR/$TILE/tile.params`
- `$ALLTILES_DIR/$TILE/params.json`

**To change params**:
1. Edit `$ALLTILES_DIR/$TILE/override.params` (NOT the one in ROOT_DIR)
2. Run `TileBuilderGenParams`
3. (Maybe) rerun `GenerateAllCommands` if cmd files are affected
4. (Maybe) rerun `TileBuilderMake` if flow structure is affected

**Do NOT** edit tile.params or params.json directly - some targets might not see changes due to the ParamsDB API.

### Finding Params

To find where a param is set across the hierarchy:
```bash
TileBuilderGenParamsGrep $param_name
```

### CHOSEN Params

Params starting with `CHOSEN_*` control major flow choices:
- `CHOSEN_PLACER` - which placement tool (iccompiler vs fusion compiler vs pinnacle)
- `CHOSEN_ROUTER` - which routing tool
- etc.

**Important**: Editing any CHOSEN param means you almost certainly need to rerun `TileBuilderMake`.

### Target-Specific Overrides

Append target name to param name for target-specific values:
```
ENCOUNTER_MODULE = soc/8.1-s384_1
ENCOUNTER_MODULE_FeFpInsertPowerGates = soc/8.1-e333_1
```

### Controls

**Controls** are syntactically the same as param files. Originally meant for system/flow settings vs design settings, but the distinction has blurred. When searching for a param, remember it can be in a control file too.

## CMD Files (Command Files)

Almost all vendor tools are controlled by command (cmd) files. TileBuilder productizes how they're generated:

- **Templates**: Located in `$FLOW_DIR/$FAMILY/templates/`
- **Expansion**: Run `GenerateAllCommands` target to expand templates
- **Output**: Generated cmd files go to `$ALLTILES_DIR/$NICKNAME/cmds/`

To regenerate just one template:
```bash
TileBuilderOverwriteCommand $TARGET
```

Running `GenerateAllCommands` moves current cmd files to `cmds/ZZZ_OLD/$TARGET.cmd.$DATE`.

## Tech and Stdcells

Stdcell info is stored in `$ALLTILES_DIR/$NICKNAME/tech/` directory. Key files include `.list` files such as `tech/lists/stdcell.lef.list`.

The tech dir is created by running the `BuildTechDir` target - one of the first things in most flows.

## Tunables

**Tunables** are tcl/csh/python files that let you customize flow behavior:

- **Location**: `tune/` directory
- **Purpose**: Macro placement, complex regioning, manual preroutes
- **Generation**: Created by `UpdateTunable` target

Tunables can come from:
- Release/checkpoint areas
- `$ALLTILES_DIR/ProjectTune/` - project-level tune files (Perforce-tracked)
- `$ALLTILES_DIR/$DESIGN/tune/` - design-level tune files (Perforce-tracked)

## Key Environment Variables

| Variable | Description |
|----------|-------------|
| `$ROOT_DIR` | Top-level workspace directory (where TileBuilderStart ran) |
| `$FLOW_DIR` | TileBuilder flow code directory |
| `$ALLTILES_DIR` | main/pd/tiles directory |
| `$BASE_DIR` or `$RUNDIR` | Current run directory |
| `$TB_SRV_DIR` | Seras server directory |
| `$TARGET_NAME` | Current target name |
| `$NICKNAME` | Unique identifier for this run |

## Session vs Run

- **Session**: Entire TileBuilder workspace including flow-engine area, code checkout, and all runs. Located at $ROOT_DIR. 1:1 relationship with Seras workspaces.
- **Run**: Build of a single configuration of a single tile. Located at $ALLTILES_DIR/$NICKNAME. Params are unique per run.

You can have any number of runs in a single session. There is one code checkout per session, applicable to all runs.

## Terminology Quick Reference

| Term | Definition |
|------|------------|
| **Action** | Set of related targets performing similar tasks |
| **ALLTILES_DIR** | main/pd/tiles directory containing run directories |
| **Barrier** | Output that may not change every run; downstream protected from invalidation |
| **Family** | High-level grouping of targets by design area |
| **FLOW_DIR** | TileBuilder flow code directory (Perforce checkout) |
| **Generic Target** | Reusable target implementation appearing multiple times |
| **LSF** | Load Sharing Facility - AMD's compute cluster job scheduler |
| **NICKNAME** | Unique identifier for a run |
| **Personality** | Subset of a family, grouping related targets |
| **ROOT_DIR** | Top-level workspace directory |
| **Run** | One configuration of one tile |
| **Session** | Entire TileBuilder workspace with server and runs |
| **Target** | Single atomic step in the flow |
| **eperl** | Templating language using `<:` and `:>` anchors |

## Documentation References

- **TileBuilder 101**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530564899/TileBuilder+101
- **TileBuilder Glossary**: https://amd.atlassian.net/wiki/spaces/MTHDINFRA/pages/530558971/TileBuilder+Glossary
- **Tool Registry**: https://twiki.amd.com/twiki/bin/view/Cpd/TileBuilderToolRegistry
