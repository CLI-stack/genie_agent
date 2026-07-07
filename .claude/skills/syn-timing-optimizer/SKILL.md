# Synthesis Timing Optimizer Skill

Fix FxSynthesize timing violations from scratch for any tile.
Given only a completed synthesis run and a tune directory, this skill reads the
timing report, diagnoses all root causes, and generates complete tune files in
one shot — collapsing what typically takes multiple iterative runs into a single pass.

## Trigger
`/syn-timing-optimizer`

## Usage
```
/syn-timing-optimizer <tile_run_dir>                   # full run  — Steps 1→2→3→4
/syn-timing-optimizer <tile_run_dir> --analyze-only    # safe mode — Steps 1→2→3 only
/syn-timing-optimizer <tile_run_dir> --apply           # apply saved plan — Step 4 only
```

| Mode | Steps | File writes? | When to use |
|------|-------|-------------|-------------|
| Full run | 1→2→3→4 | Yes | First time on a new tile, or when you trust the process |
| `--analyze-only` | 1→2→3 | No | Review the plan before committing |
| `--apply` | 4 only | Yes | After reviewing `--analyze-only` output and deciding to proceed |

### Typical workflow with review step
```
# Step A — review first
/syn-timing-optimizer /proj/.../tiles/my_tile --analyze-only

# Step B — after reading the plan, if happy:
/syn-timing-optimizer /proj/.../tiles/my_tile --apply
```

`--apply` reads the saved plan file from the previous `--analyze-only` run
(`<tile_dir>/tune/FxSynthesize/.optimizer_plan.json`) and executes Step 4
directly — no re-analysis needed.

Requirements:
- `rpts/FxSynthesize/FxSynthesize.pass_*.proc_qor.rpt.gz`  (at least one pass)
- `tune/FxSynthesize/`  (may be empty — skill creates what is missing in Step 4)

The skill works regardless of what tune files already exist.
If `r2r_optimization.tcl` does not exist, it is created from scratch in Step 4.

---

## Execution Model

Spawn one `general-purpose` subagent with the full instructions below.
Wait for it to complete. Print its output verbatim.

```python
Agent(
  description="syn-timing-optimizer — <tile_name>",
  subagent_type="general-purpose",
  prompt="""
You are a synthesis timing optimizer. Analyze a tile's FxSynthesize QoR,
diagnose all timing root causes, and generate complete tune files that fix
every fixable violation in one pass. Work entirely from what the timing
report shows — do not assume any prior knowledge of the design hierarchy.

TILE_DIR     = <resolved_tile_dir>
MODE         = <FULL | ANALYZE_ONLY | APPLY>
PLAN_FILE    = <tile_dir>/.optimizer_plan.json

Follow Steps 1, 2, 3 exactly as written. Return the full report.

=== FULL INSTRUCTIONS BELOW ===
"""
)
```

---

## Step 1: READ — Understand the Current State

### 1a. Detect Design Type
```bash
basename <TILE_DIR>
```
Read `override.params` for `NICKNAME` or `SYN_VF_FILE` if basename is ambiguous.

### 1b. Inventory Existing Tune Files
```bash
ls -la <TILE_DIR>/tune/FxSynthesize/
```
Read ALL existing `.tcl` files in full. Understand what is already defined before
adding anything. Record which files exist:
- `pre_opt.tcl`              — always present; defines sourcing order
- `group_paths.tcl`          — may have basic groups or may be empty
- `r2r_optimization.tcl`     — may NOT exist; skill creates it if missing
- `post_opt.tcl`             — compile loop control

Read `pre_opt.tcl` carefully: find every `tunesource` line to understand the
order in which files are loaded into FC.

### 1c. Find All Completed QoR Passes
```bash
ls -v <TILE_DIR>/rpts/FxSynthesize/FxSynthesize.pass_*.proc_qor.rpt.gz 2>/dev/null
```
If none found → print error and STOP: `"No QoR data found. Run FxSynthesize first."`

The HIGHEST numbered pass is the PRIMARY dataset.

### 1d. Extract Per-Group Timing — All Passes
For each pass file:
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/FxSynthesize.pass_N.proc_qor.rpt.gz \
  | grep -E "Timing Path Group|Levels of Logic|Critical Path Slack|Critical Path Length|Critical Path Clk Period|Total Negative Slack|No\. of Violating"
```

For each path group extract:
- `WNS_ps`      from `Critical Path Slack:`       (negative = violation)
- `TNS_ps`      from `Total Negative Slack:`
- `NVP`         from `No. of Violating Paths:`
- `LOL`         from `Levels of Logic:`
- `CritLen_ps`  from `Critical Path Length:`
- `Period_ps`   from `Critical Path Clk Period:`

### 1e. Read Worst-Path Endpoint Hierarchies
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/report_timing.pass_*.rpt.sum.sort_slack.endpts.gz \
  2>/dev/null | head -60
```
Extract the RTL hierarchy of the top 20 worst violating endpoints.
This is the only way to know WHICH module/sub-hierarchy is failing —
the path group name alone does not tell you this.

### 1f. Read Worst-Path Trace for Top 3 Groups
For the 3 groups with worst WNS, read their critical path detail:
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/FuncTT0p9v_<GROUP>_max.rpt.gz 2>/dev/null | head -80
```
From each trace extract:
- Startpoint full hierarchy
- Endpoint full hierarchy
- `Critical Path Length` and proportion that is wire delay vs gate delay
- Whether the launch or capture flop is a multibit (MB4/MB8) cell

### 1g. Build Effective Weight Map
From all tune files, collect every `group_path -name X -weight W` line.

**Sourcing order rule:** `r2r_optimization.tcl` is sourced AFTER `group_paths.tcl`.
If the same group name appears in both files, the `r2r_optimization.tcl` weight
is what FC actually uses — regardless of what `group_paths.tcl` says.

Build: `effective_weight[group_name] = weight from last-sourced file that defines it`

### 1h. Read override.params
```bash
grep -E "DDRSS_FEINT_NUM_COMPILES|max_multibit_size|FLOORPLAN_DEF" \
  <TILE_DIR>/override.params 2>/dev/null
```

### Step 1 Output
```
╔══════════════════════════════════════════════════════════════════════════════╗
║  STEP 1: READ                                                               ║
║  Tile: <basename>   Type: <design_type>   Period: <period> ps               ║
║  Passes: P<list>    Primary: P<N>                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

Existing tune files:
  pre_opt.tcl            : YES
  group_paths.tcl        : YES / NO
  r2r_optimization.tcl   : YES / NO  ← will CREATE if NO
  post_opt.tcl           : YES / NO

--- Primary Pass (P<N>) Timing by Path Group ---

  Path Group                  WNS (ps)    TNS (ps)    NVP    LOL   W(eff)
  ─────────────────────────────────────────────────────────────────────────
  <name>                      <val>       <val>       <val>  <val>  <val>
  ...  (sorted worst WNS first; violating groups only)

--- Pass-over-Pass WNS Progression ---

  Path Group                  P1          P2          P3      ...
  ──────────────────────────────────────────────────────────────
  <name>                      <val>       <val>       <val>   ...

--- Top 10 Worst Endpoint Hierarchies ---

  Rank  Path Group          Endpoint (last 3 levels)             Slack (ps)
  ──────────────────────────────────────────────────────────────────────────
  #1    <group>             <module>/<sub>/<signal>               <val>
  ...

Compile passes currently: <N>    (target: 5)
```

---

## Step 2: DIAGNOSE — 7 Root Cause Checks

Run all 7 checks on the PRIMARY pass data and effective weights from Step 1.
Every check produces a verdict and a specific recommended action for Step 3.

---

### Check A: Phantom Clock Paths

**What it is:**
A path group whose cell filter uses a wildcard that captures paths between
registers in MUTUALLY EXCLUSIVE operating modes. These paths can never occur
in real silicon — they are artifacts of how the group is defined. But FC
spends optimization budget trying to fix them, and they dominate WNS/TNS,
hiding the real violations underneath.

**How to detect:**
1. Find any path group whose name or filter covers a clock domain WITHOUT
   a specific scenario or mode suffix (e.g., a group named generically after
   a clock that operates in multiple exclusive modes)
2. Check that group's QoR: if `NVP > 500` AND `|TNS_ps| > 50,000` → likely phantom
3. Confirm: are the paths crossing between modes that cannot be simultaneously active?

**Fix:** Split the monolithic group into one group per scenario/mode.
Each sub-group gets a specific `-from` or `-to` filter for that mode's registers.

**Verdict [A]:** FOUND (group name, NVP, TNS) or CLEAN

---

### Check B: Weight Starvation

**What it is:**
FC allocates optimization effort proportional to each path group's cost.
A group can have a reasonable weight and real violations but receive almost
no optimizer attention because another group's cost is 20× or 40× higher.

**Formula:** `Cost(group) = effective_weight × NVP × |WNS_ps|`

A group is **starved** if: `Cost(group) < 5% of max Cost across all violating groups`

**Important:** Always use `effective_weight` (from Step 1g), not the weight
in `group_paths.tcl`. A weight override in `r2r_optimization.tcl` may be
silently reducing the effective weight below what was intended.

**New weight formula for starved groups:**
```
new_weight = clamp( round( sqrt(max_cost / (NVP × |WNS_ps|)) ), min=6, max=16 )
```
This brings the starved group's cost up to ~100% of the dominant group's cost.

**Verdict [B]:** Table of all violating groups — cost, cost%, current weight, new weight for starved ones.

---

### Check C: Override Conflicts

**What it is:**
The same `group_path -name X` is defined in both `group_paths.tcl` AND
`r2r_optimization.tcl` with DIFFERENT weights. Since `r2r_optimization.tcl`
is sourced last, its weight silently wins — even if the engineer intended
the `group_paths.tcl` value.

**How to detect:**
Compare `effective_weight[group]` against the weight in `group_paths.tcl`.
Any mismatch = conflict.

**Fix:** In `r2r_optimization.tcl`, update the conflicting `group_path` to use
the correct intended weight, and add a comment explaining the resolution.

**Verdict [C]:** Table — group name, group_paths.tcl weight, r2r_opt.tcl weight, effective weight, conflict flag.

---

### Check D: Architecture Limits

**What it is:**
Some paths have so many combinational logic levels that synthesis cannot close
them within the clock period, regardless of weight, bounds, or effort settings.
The logic depth alone consumes the entire cycle time.

**How to detect:** `LOL > 28` AND `WNS < -20 ps`

At approximately 9–10 ps per gate delay on modern nodes, 30 logic levels
consumes 270–300 ps. With typical synthesis periods around 295 ps, there is
zero budget remaining for wire delay or clock skew. No synthesis knob can
reduce the number of logic levels — only RTL restructuring can.

**Fix:** No tune change. Add a comment block in the tune file documenting the
architectural constraint and the required RTL action.

**Verdict [D]:** List groups, LOL, WNS. All marked as RTL-only — synthesis cannot help.

---

### Check E: Wire-Dominated Paths

**What it is:**
Low logic level count but large timing violation and long critical path length.
The gates are fast enough; the problem is that communicating cells are placed
far apart on the die, creating long wires that consume most of the clock period.

**How to detect:** `LOL < 15` AND `WNS < -50 ps` AND `CritLen_ps > 0.80 × Period_ps`

**Two sub-cases:**

*Register-to-register wire:* Cells in the same module are physically scattered.
Fix: `create_bound` to co-locate them.

*I/O port wire:* The path starts or ends at a fixed I/O port that cannot be moved.
The wire length is determined by the port location in the floorplan — a bound
cannot fix this. Requires RTL pipelining to insert a register closer to the port.

Distinguish by reading the critical path trace (Step 1f): if startpoint is a
primary input port → port-limited. If startpoint is a register → wire-dominated
and a bound can help.

**Verdict [E]:** List groups with LOL, WNS, CritLen. Mark each as bound-fixable or port-limited.

---

### Check F: Unrepresented Hierarchies (Missing Groups)

**What it is:**
A sub-hierarchy that has violating paths but NO dedicated named path group.
Those paths fall into the generic catch-all (e.g., `SYN_R2R`), receiving
whatever optimizer budget is left after named groups are served — effectively
starved by omission rather than by weight.

**How to detect:**
1. From the top 20 worst endpoint hierarchies (Step 1e), identify the unique
   module/sub-module prefixes (e.g., the first 2–3 levels of the hierarchy)
2. For each unique prefix, check whether any `group_path -to [get_cells ... *prefix*]`
   exists in the tune files
3. If a module appears in the top 20 worst paths but has NO dedicated group → MISSING

**Fix:** Create a new named `group_path` targeting that hierarchy in Step 3.

**Verdict [F]:** List all hierarchies in the top 20 that have no dedicated group.

---

### Check G: I2R Catch-All Hiding Multiple Sub-Groups

**What it is:**
`SYN_I2R` (input-to-register) is typically a single catch-all group.
If it captures input paths destined for multiple distinct modules or multiple
clock domains, the optimizer cannot differentiate between them. It focuses on
the single worst I2R path; all other I2R destinations receive no targeted effort.

**How to detect:**
1. Look at the worst endpoints inside `SYN_I2R` from sort_slack.endpts
2. If they span 2+ distinct destination modules OR 2+ clock domains → split needed
3. Also flag if `SYN_I2R` WNS improves by less than 5 ps per compile pass —
   this stagnation indicates multiple sub-paths competing inside the catch-all

**How to split:**
Create one named sub-group per distinct destination or clock:
```tcl
group_path -name SYN_I2R_<clock_or_module> -weight <W> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<module_pattern>*"]
```
Each sub-group gets its own weight based on its WNS severity.

**Verdict [G]:** SPLIT NEEDED (list which sub-groups to create) or CLEAN.

---

### Step 2 Output

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  STEP 2: DIAGNOSIS                                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

[A] Phantom Clocks      : <FOUND ⚠ / CLEAN ✓>
[B] Weight Starvation   : <N groups starved ⚠ / CLEAN ✓>
[C] Override Conflicts  : <N conflicts ⚠ / CLEAN ✓>
[D] Architecture Limits : <N groups — RTL only / CLEAN ✓>
[E] Wire-Dominated      : <N groups ⚠ (M bound-fixable, K port-limited) / CLEAN ✓>
[F] Missing Groups      : <N hierarchies with no named group ⚠ / CLEAN ✓>
[G] I2R Catch-All       : <SPLIT NEEDED ⚠ — N sub-groups / CLEAN ✓>

--- Cost Analysis (Check B) ---

  Path Group          W(eff)  NVP    WNS (ps)   Cost      Cost %   Starved?  New W
  ──────────────────────────────────────────────────────────────────────────────────
  <dominant>          <W>     <N>    <val>       <cost>    100%     —         —
  <group>             <W>     <N>    <val>       <cost>    <pct>%   —         —
  <starved group>     <W>     <N>    <val>       <cost>    <pct>%   YES ⚠     <new_W>
  ...

--- Override Conflicts (Check C) ---

  Group          group_paths W   r2r_opt W   Effective   Conflict?
  ─────────────────────────────────────────────────────────────────
  <group>        <W1>            <W2>        <W2>        YES ⚠

--- Architecture Limits (Check D) ---

  Group          LOL    WNS (ps)   Gate Time (ps)   % of Period   Action
  ──────────────────────────────────────────────────────────────────────────
  <group>        <N>    <val>      <N × 9.5>        <pct>%        RTL pipeline

--- Wire-Dominated (Check E) ---

  Group          LOL    WNS (ps)   CritLen (ps)   CritLen %   Fixable?
  ──────────────────────────────────────────────────────────────────────────
  <group>        <N>    <val>      <val>          <pct>%      BOUND / PORT-LIMITED

--- Missing Groups (Check F) ---

  Module (from sort_slack.endpts)   Appears at rank   Has named group?
  ────────────────────────────────────────────────────────────────────────
  <module>/<sub>                    #<N>–#<M>         NO ⚠

--- I2R Split (Check G) ---

  SYN_I2R worst endpoints span: <N> distinct modules, <M> clock domains
  Status: <SPLIT NEEDED / CLEAN>

Priority for Step 3 (highest impact first):
  1. [A] Phantom fix — eliminates cross-mode noise, reveals real violations
  2. [F] Missing groups — name unrepresented hierarchies before tuning weights
  3. [G] I2R split — separate catch-all before applying weights inside it
  4. [B][C] Starvation + override fixes — calibrate weights to actual violations
  5. [E] Bounds — co-locate wire-dominated register hierarchies
  6. [D] Architecture limits — comment only, escalate to RTL team
```

---

## Step 3: SUGGEST — Propose All Changes + Flag RTL Issues

**Runs in BOTH modes.** No files are written in this step.
Step 3 shows the complete plan — exactly what Step 4 will do — so you can
review and confirm before any changes are made.

Also flags groups that require RTL attention (synthesis cannot fix them alone).
This is a pointer only — no RTL files are read, no code is generated.

---

### 3a. Discover Hierarchy From Timing Report

Before writing any TCL, extract the actual cell hierarchy for each failing group.
Do NOT guess hierarchy names — read them from the timing data.

**From sort_slack.endpts (Step 1e):**
- Group the top 20 worst endpoints by their common module prefix (first 2–3 levels)
- Each distinct prefix = a candidate named group

**From worst-path traces (Step 1f):**
- Full `Startpoint` and `Endpoint` hierarchy from the critical path trace
- Common prefix of start and endpoint = the RTL module containing the failing logic

**Build a hierarchy map:**
```
Path Group Name    →   Endpoint Module Prefix   →   get_cells filter pattern
<group_name>           <module>/<submodule>          "full_name =~ *<module>/<submodule>*"
```

This map drives every `group_path -to/-from` and `create_bound` in Step 3.

---

### 3b. Determine What to Create vs Update

| File | Exists? | Action |
|------|---------|--------|
| `pre_opt.tcl` | YES | Read. Add tunesource for r2r_opt if missing. |
| `group_paths.tcl` | YES | Read. Only add groups not already defined. Fix Check C conflicts. |
| `group_paths.tcl` | NO | Create with base generic groups. |
| `r2r_optimization.tcl` | YES | Read. Append targeted fixes only. |
| `r2r_optimization.tcl` | NO | Create from scratch with all Step 3 content. |

**If `r2r_optimization.tcl` does not exist:**
1. Create: `<TILE_DIR>/tune/FxSynthesize/FxSynthesize.r2r_optimization.tcl`
2. Add tunesource in `pre_opt.tcl` immediately after the group_paths line:
```
tunesource tune/$TARGET_NAME/$TARGET_NAME.group_paths.tcl
tunesource tune/$TARGET_NAME/$TARGET_NAME.r2r_optimization.tcl   ← ADD THIS
```

---

### 3c. Base Generic Groups (group_paths.tcl)

If `group_paths.tcl` is missing or only has a bare SYN_R2R, start with:

```tcl
# Generic catch-all groups — every design needs these
# r2r_optimization.tcl will override weights for specific critical groups
group_path -name SYN_I2O -critical_range 200 -weight 0.001
group_path -name SYN_R2O -critical_range 200 -weight 1
group_path -name SYN_R2R -critical_range 200 -weight 5
group_path -name SYN_I2R -critical_range 200 -weight 3
```

Then add named groups for each hierarchy found in Step 3a:
```tcl
group_path -name <design>_<module>_r2r \
    -critical_range <3 × |WNS_ps|, min 100, max 600> \
    -weight <starting_weight> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<module_pattern>*"]
```

Starting weight guidelines (starvation formula will refine these in r2r_opt.tcl):
- Most critical group (worst WNS, highest NVP): 10–12
- Secondary groups: 6–9
- Generic catch-all SYN_R2R: 4–6

---

### 3d. r2r_optimization.tcl — Section by Section

Write this file in this order:

---

#### Section 1: Header

```tcl
################################################################################
# R2R Timing Optimization
# Created: <date>
#
# Baseline (P<N>):  Design WNS = <val> ps    TNS = <val> ps    Period = <period> ps
# Target:           WNS < -50 ps             TNS < -10 ns
#
# Root causes found:
#   [A] Phantom clocks:   <YES: <group> / NO>
#   [B] Starvation:       <YES: N groups / NO>
#   [C] Override bugs:    <YES: N conflicts / NO>
#   [D] Arch limits:      <YES: N groups, RTL-only / NO>
#   [E] Wire-dominated:   <YES: N groups / NO>
#   [F] Missing groups:   <YES: N added / NO>
#   [G] I2R split:        <YES: N sub-groups / NO>
################################################################################
```

---

#### Section 2: Fix A — Phantom Clock Split (if Check A fired)

```tcl
# [Fix-A] Phantom clock group split
# <original_group> was capturing paths between mutually exclusive operating modes.
# These paths are physically impossible and were dominating WNS/TNS, hiding
# real violations. Split into per-mode groups eliminates the phantoms.

# The monolithic group must not be defined — do not add it to group_paths.tcl.
# Per-mode replacements (one group per exclusive operating scenario):
group_path -name <group>_<MODE_A> -critical_range 200 -weight 1
    # mode A: <describe why weight is low — e.g., rarely violates once phantoms removed>
group_path -name <group>_<MODE_B> -critical_range 200 -weight <W>
    # mode B: real violations remain here — weight reflects actual NVP × |WNS|
```

---

#### Section 3: Fix F — Add Missing Groups (Check F)

For each hierarchy found in top-20 endpoints but with no named group:

```tcl
# [Fix-F] New group: <module_prefix> appears at ranks #N–#M with no named group.
# Was absorbed into generic catch-all and receiving no targeted optimization.
group_path -name <design>_<module>_r2r \
    -critical_range <3 × |WNS_ps|> \
    -weight <initial_weight_from_starvation_formula> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<module_pattern>*"]
```

---

#### Section 4: Fix G — I2R Sub-Group Split (if Check G fired)

```tcl
# [Fix-G] SYN_I2R catch-all split into per-destination sub-groups.
# The catch-all was hiding N distinct I2R paths from different sources/destinations.
# Each sub-group now gets its own weight calibrated to its actual WNS/NVP.

# Remove the monolithic SYN_I2R (leave it in group_paths.tcl as a catch-all
# for any I2R paths NOT captured by the sub-groups below)

group_path -name SYN_I2R_<destination_or_clock_A> \
    -critical_range <val> \
    -weight <W> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<pattern_A>*"]

group_path -name SYN_I2R_<destination_or_clock_B> \
    -critical_range <val> \
    -weight <W> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<pattern_B>*"]
# ... one group per distinct destination module or clock domain
```

---

#### Section 5: Fix B+C — Weight Corrections

This section is the authoritative weight source — it overrides group_paths.tcl
for any group redefined here (because r2r_opt.tcl is sourced last).

```tcl
# === Weight corrections — starvation formula applied ===
# new_W = clamp(round(sqrt(max_cost / (NVP × |WNS_ps|))), min=6, max=16)
# Override conflicts resolved — this file is sourced last and wins.

# [Fix-B] <group>: starved at <pct>% of max cost
# new_W = clamp(sqrt(<max_cost> / (<NVP> × <|WNS|>)), 6, 16) = <new_W>
group_path -name <group> \
    -critical_range <val> \
    -weight <new_W> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<pattern>*"]

# [Fix-C] <group>: override conflict
# group_paths.tcl had W=<W1>, r2r_opt had W=<W2> (r2r_opt was winning silently).
# Correct effective weight should be <correct_W> — set it explicitly here.
group_path -name <group> \
    -critical_range <val> \
    -weight <correct_W> \
    -to [get_cells -quiet -hier -filter "full_name =~ *<pattern>*"]

# [OK] <group>: W=<W> — no starvation, no conflict — no change needed
```

**Critical range guideline:** `3 × |WNS_ps|`, minimum 100 ps, maximum 600 ps.
Groups with many NVP: use wider range to capture near-violating paths too.

---

#### Section 6: Fix D — Architecture Limit Comments

```tcl
# ─────────────────────────────────────────────────────────────────────────────
# [ARCH-LIMIT] Group: <group_name>
# LOL = <N> levels × ~9–10 ps/gate = <N × 9.5> ps ≈ <pct>% of <period> ps period.
# No weight, bound, or effort setting can close this — synthesis cannot reduce
# logic levels. Only RTL pipeline insertion (splitting the <N>-level cone across
# two clock cycles) will fix this path.
# Required action: escalate to RTL design team.
# ─────────────────────────────────────────────────────────────────────────────
```

---

#### Section 7: Fix E — Placement Bounds (wire-dominated, bound-fixable groups only)

First — ALWAYS set the congestion safety net before any bound:
```tcl
# Safety net: if bounds create local congestion overflow, FC recovers gracefully
# rather than running indefinitely trying to force placement.
set_app_options -name compile.flow.enable_auto_feasibility_recovery -value true
```

For each wire-dominated, bound-fixable group:

```tcl
# === Placement bound: <group_name> ===
# Why: wire-dominated path — LOL=<N>, CritLen=<X>ps (<pct>% of period).
# The logic is fast enough; cells are physically scattered causing long wires.
# Soft bound encourages FC to co-locate <module> cells, reducing wire delay.
#
# Sizing:
#   Estimated cell area : ~<cell_area> um²  (from report_area for <module>)
#   Target utilization  : 40–65% for dense groups; 10–20% for congestion-sensitive
#   Bound area          : cell_area / target_util = <bound_area> um²
#   Density check       : <num_cells> / <bound_area> = <density> cells/um²
#   HARD LIMIT          : density MUST be < 400 cells/um²
#   SAFE TARGET         : ~200 cells/um²
#
# Coordinates: derived from DEF macro positions (see Step CB below).
#   Bound anchored near <macro_name> at (<mx>, <my>) in the floorplan.
#   (<x1>,<y1>) → (<x2>,<y2>)  =  <width> × <height>  =  <area> um²
#
set <name>_cells [get_cells -quiet -hier -filter \
    "full_name =~ *<hierarchy_pattern>*"]
if {[sizeof_collection $<name>_cells] > 0} {
    create_bound -name <name>_bound \
        -boundary [list [list <x1> <y1>] [list <x2> <y2>]] \
        -type soft \
        $<name>_cells
    puts "  Bound <name>_bound: (<x1>,<y1>)–(<x2>,<y2>) | [sizeof_collection $<name>_cells] cells"
} else {
    puts "  WARNING: No cells found for <name>_bound — verify hierarchy filter"
}
```

**How to find bound coordinates from DEF:**
```bash
# Step 1: get DEF path
grep "FLOORPLAN_DEF" <TILE_DIR>/override.params

# Step 2: find SRAM/macro locations that the target hierarchy communicates with
zcat <def_path>.gz | grep -E "PLACED|FIXED" \
  | grep -iv "FILLCAP\|FILL\|DCAP" | head -40

# Step 3: anchor the bound so the target hierarchy is near those macros
# Bound coordinates: enclose the actual macro location + margin for std cells
```

**Density MUST be checked before writing any bound:**
```
density = estimated_cell_count / (bound_width × bound_height)
If density > 400 → DOUBLE the bound area. Do not proceed with tight bounds.
A bound that is too large costs very little. A bound that is too tight can
cause the optimizer to run for many hours without convergence.
```

**Utilization guideline (not a strict formula — use judgment):**
- Dense, tightly-coupled hierarchies: 40–65% utilization inside the bound
- Large modules or modules with known congestion risk: 10–20% utilization
  (a loose bound still enforces locality; it just allows FC more flexibility)
- When unsure: start loose, tighten in a later run if cells are still scattered

---

#### Section 8: Compile Settings and Effort Knobs

Apply all settings that are relevant to the root causes found. Every setting
includes the condition that triggers it — do not apply blindly.

```tcl
# === 8a. Compile passes ===
# Set in override.params: DDRSS_FEINT_NUM_COMPILES = 5
# 5 passes allow later passes to clean up what earlier passes created.
# 3 passes is insufficient after major weight or group changes.

# === 8b. Effort — always apply these ===
set_app_options -name compile.flow.high_effort_timing -value 1
set_app_options -name opt.timing.effort              -value high
set_app_options -name opt.area.effort                -value high
set_app_options -name opt.common.buffer_area_effort  -value ultra

# === 8c. TNS spread — always apply ===
set_app_options -name compile.timing.prioritize_tns         -value true
set_app_options -name opt.timing.slack_based_tns_optimization -value true
# NVP/10 (minimum 5): distribute effort proportional to violation count
set_app_options -name opt.timing.tns_optimization_paths_per_endpoint \
    -value <NVP_of_worst_group / 10, min 5>

# === 8d. Logic restructuring ===
# Use area_timing when TNS is spread across many paths (general case).
# Switch to timing when LOL is the primary bottleneck (Check D present).
set_app_options -name opt.common.advanced_logic_restructuring_mode \
    -value area_timing
set_app_options -name opt.common.advanced_logic_restructuring_wirelength_costing \
    -value high

# === 8e. Multibit banking — apply when FuncTT0p9v trace shows MB4/MB8 launch FF ===
# MB8FF Q delay = 48–51 ps vs single FF = 30 ps — up to 20 ps difference per launch.
# Identify by reading the FuncTT0p9v path trace: if the launch cell name contains
# MB4 or MB8, exclude that register hierarchy from banking.
set_app_options -name compile.flow.max_multibit_size -value <N>
    # N=4: conservative — prevents debanking regression on data-heavy tiles
    # N=6: acceptable for control-logic tiles
set_app_options -name compile.flow.enable_rtl_multibit_debanking      -value true
set_app_options -name compile.flow.enable_physical_multibit_banking    -value true
set_app_options -name compile.flow.enable_rtl_multibit_banking         -value true
set_app_options -name compile.flow.enable_multibit_debanking           -value true
set_app_options -name multibit.banking.enable_tns_degradation_estimation -value true
set_multibit_options -slack_threshold 0
# If a specific MB launch register is identified from path trace:
# set_multibit_options -exclude [get_cells -quiet -hier * \
#     -filter "full_name =~ *<critical_register_hierarchy>*"]

# === 8f. Fanout and net loading — apply when high-fanout nets found in path trace ===
# Read FuncTT0p9v trace — look at Fanout column. If any net fanout > 200:
# set_max_fanout on that specific register (50–70% of observed fanout).
# CAUTION: do NOT apply to a broad hierarchy — causes massive buffering overhead.
# Apply only to the specific cells identified in the path trace.
# set_max_fanout <N × 0.60> [get_cells -quiet -hier -filter "full_name =~ *<specific_reg>*"]
# set_max_transition <T> [get_nets -quiet -hier *<net_name>*]   ;# T from trans column
# set_max_capacitance <C> [get_nets -quiet -hier *<net_name>*]  ;# C from cap column

# === 8g. Placement and routing effort — apply when wire delay dominates (Check E) ===
set_app_options -name compile.final_place.effort                           -value high
set_app_options -name compile.initial_place.buffering_aware_placement_effort -value high
set_app_options -name place_opt.final_place.effort                         -value high
set_app_options -name place_opt.place.congestion_effort                    -value high
set_app_options -name clock_opt.place.congestion_effort                    -value high
set_app_options -name route.common.rc_driven_setup_effort_level            -value high
set_app_options -name route.global.effort_level                            -value high
set_app_options -name route.detail.optimize_wire_via_effort_level          -value high
set_app_options -name ccd.hold_control_effort                              -value high
set_congestion_optimization [get_designs] TRUE
set_congestion_optimization [get_cells -hier * -filter "is_hierarchical == true"] true
# If wire delay is the dominant bottleneck, reduce placement density slightly:
# set_app_options -name place.coarse.max_density -value <current_minus_0.05>

# === 8h. Register replication — apply when a single register fans out to many endpoints ===
# Identify from sort_slack.endpts: if the SAME startpoint appears ≥3 times
# in the top violating paths, that register needs replication.
# N copies: replicate so each copy drives ≤ observed_fanout / N loads.
# set_register_replication -num_copies <N> \
#     [get_cells -hier * -filter "full_name =~ *<high_fanout_register>*"]
set_app_options -name compile.timing.buffer_replication -value true
set_app_options -name compile.seqmap.register_replication_placement_effort -value high

# === 8i. Retiming — apply only when Check D shows LOL is the bottleneck ===
# Forward retiming: safe, moves registers in the direction that reduces logic depth.
# Backward retiming: can cause pass instability — validate monotonic convergence.
# set_app_options -name compile.register_retiming.mode            -value full
# set_app_options -name compile.retiming.optimization_priority    -value setup_timing
# set_app_options -name compile.retiming.enable_forward_retiming  -value true
# set_app_options -name compile.retiming.enable_backward_retiming -value true
#   NOTE: if backward retiming is enabled, verify WNS improves every pass.
#         Disable it if any pass regresses vs the prior pass.

# === 8j. Clock transition — apply when path trace shows large clock transition ===
# Derive T from the clock transition column in FuncTT0p9v worst-path trace.
# Tighter clock transition reduces setup uncertainty on the capture clock.
# set_clock_transition <T> [get_clocks -quiet *<clock_name>*]

# === 8k. Conditional arcs — apply when timing looks more pessimistic than expected ===
# Enables optimistic arc selection for paths with known-exclusive conditions.
# set_app_options -name time.enable_cond_default_arcs -value true
```

**Which settings to actually write (decision rules):**

| Setting group | Write when |
|--------------|------------|
| 8b effort + 8c TNS + 8d restructuring | Always — baseline for any optimization |
| 8e multibit | FuncTT0p9v trace shows MB4/MB8 in launch FF cell name |
| 8f fanout/net | FuncTT0p9v Fanout column shows any net > 200 fanout |
| 8g placement/routing effort | Check E fired (wire-dominated) |
| 8h register replication | Same startpoint appears ≥3 times in sort_slack.endpts top-20 |
| 8i retiming | Check D fired (LOL > 28) AND forward retiming requested by team |
| 8j clock transition | Clock transition in path trace is > 50 ps |
| 8k conditional arcs | Timing appears abnormally pessimistic vs LOL count |

Commented-out settings (`# set_...`) are included as reference — uncomment only
when the specific condition above is met.

---

#### Section 9: Safety Check Before Finalizing

Before writing the files, run this mental verification:

**Cost rebalance check:** With the new weights applied, recompute Cost for all groups.
Confirm no previously-healthy group is now starved (cost < 5% of new max).

**Bounds density check:** Confirm every bound has density < 400 cells/um².

**Override check:** Confirm every group that appears in both tune files has the
intended weight as the effective weight. The last-sourced file wins.

**Monotonic convergence check:** New weights should not create a situation where
one group's cost so dominates that all others are left behind. Check the cost
distribution — no group should be < 5% of max after the fix is applied.

---

#### Section 10: Closing Print

```tcl
puts ""
puts "========================================================================"
puts "INFO: R2R optimization complete"
puts "========================================================================"
puts "  Fixes applied:"
puts "    [A] Phantom clocks  : <SPLIT applied / N/A>"
puts "    [B] Starvation      : <N groups re-weighted / NONE>"
puts "    [C] Override bugs   : <N conflicts resolved / NONE>"
puts "    [D] Arch limits     : <N groups — RTL action required>"
puts "    [E] Wire bounds     : <N bounds added / NONE>"
puts "    [F] Missing groups  : <N groups added / NONE>"
puts "    [G] I2R split       : <N sub-groups created / N/A>"
puts ""
puts "  Baseline: WNS=<val>ps  TNS=<val>ps  Period=<val>ps"
puts ""
```

---

### 3e. Update override.params

Check and apply if missing:
```
DDRSS_FEINT_NUM_COMPILES = 5
```
Do NOT change any other override.params values.

---

### Step 3 Output

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  STEP 3: SUGGESTION PLAN                                                    ║
║  Mode: <ANALYZE-ONLY — review only, no writes / FULL RUN — Step 4 will write>║
╚══════════════════════════════════════════════════════════════════════════════╝

--- Proposed Tune File Changes ---

  Fix   Check   Finding                          Proposed Action
  ────────────────────────────────────────────────────────────────────────────
  1     [A]     <group> phantom, NVP=<N>         Split into <N> per-mode groups
  2     [F]     <module> in top-20, no group     Add <design>_<module>_r2r group
  3     [G]     SYN_I2R spans <N> modules        Split into <N> sub-groups
  4     [B]     <group> cost=<pct>% of max       Raise weight <old> → <new>
  5     [C]     <group> W mismatch               Correct effective W to <val>
  6     [D]     <group> LOL=<N>                  Add arch-limit comment only
  7     [E]     <group> wire-dominated           Add bound (<x1>,<y1>)–(<x2>,<y2>)
  ────────────────────────────────────────────────────────────────────────────

--- Proposed Tune File Diff Preview ---

  group_paths.tcl:
    + group_path -name <design>_<module>_r2r -weight <W> -critical_range <CR> ...
    + group_path -name SYN_I2R_<sub> -weight <W> ...
    - group_path -name <conflicted_group> -weight <old_W>   ← removed from group_paths
    + group_path -name <conflicted_group> -weight <new_W>   ← correct value in r2r_opt

  r2r_optimization.tcl:
    + group_path -name <group>_<MODE_A> -weight <W> ...     ← phantom split
    + group_path -name <group>_<MODE_B> -weight <W> ...
    + group_path -name <starved_group> -weight <new_W> ...  ← starvation fix
    + create_bound -name <name>_bound ...                   ← wire-dominated bound
    + set_app_options -name compile.flow.high_effort_timing -value 1
    + set_app_options -name opt.timing.effort -value high
    ... (all compile settings from Section 8)

  pre_opt.tcl:
    + tunesource tune/$TARGET_NAME/$TARGET_NAME.r2r_optimization.tcl   ← if missing

  override.params:
    DDRSS_FEINT_NUM_COMPILES  <current> → 5   [needs update / already correct]

--- Expected WNS Improvement ---

  Fix [A] phantom split :  potentially large  (eliminates cross-mode noise)
  Fix [B] starvation    :  ~+<N> ps per starved group raised
  Fix [C] override bug  :  ~+<N> ps per conflict corrected
  Fix [E] bounds        :  ~+<N> ps for wire-dominated groups
  Fix [D] arch limits   :  0 ps — RTL change required (see below)
  Total estimated       :  ~+<N> ps WNS improvement from synthesis fixes

--- RTL Action Items (synthesis cannot fix these) ---

  Group               LOL    WNS (ps)   Why synthesis cannot fix        RTL Action Needed
  ──────────────────────────────────────────────────────────────────────────────────────────────
  <group>             <N>    <val>      LOL > 28 — logic depth = period  Pipeline insertion
  <group>             <N>    <val>      Port-limited wire (I/O fixed)    Pipeline flop near port
  <group>             <N>    <val>      High fanout startpoint           Register duplication in RTL
  <group>             <N>    <val>      Clock gate enable depth > 15     Register the enable signal
  ──────────────────────────────────────────────────────────────────────────────────────────────

  Note: RTL action items are pointers only — no RTL files read, no code generated.
  Bring these to your RTL design team with the group name and endpoint hierarchy
  from sort_slack.endpts as context.

--- Next Steps ---

  If ANALYZE_ONLY:
    Review the proposed changes above.
    When ready to apply, run:
      /syn-timing-optimizer <TILE_DIR> --apply
    (reads the saved plan file — no re-analysis needed)

  If FULL or APPLY:
    Step 4 will now write the tune files exactly as proposed above.
    After Step 4 completes:
      1. Run FxSynthesize:
           /agent run supra regression for <tile> target FxSynthesize at <TILE_DIR>
      2. Compare result vs baseline:
           /syn-timing --comparison <baseline_dir> <new_run_dir>
      3. Escalate RTL action items to design team (table above)
      4. If violations remain, re-run this skill to recalibrate
```

---

---

## Step 4: GENERATE — Write Tune Files

**Runs in FULL and APPLY modes. Skipped in ANALYZE_ONLY.**

Write the exact changes proposed in Step 3 to the actual tune files.
No new decisions are made here — Step 3 is the plan, Step 4 executes it.

### APPLY mode — read plan from file

If `MODE=APPLY`, skip Steps 1–3 entirely and read the saved plan:
```bash
cat <TILE_DIR>/.optimizer_plan.json
```
If the plan file does not exist → print error and STOP:
`"No saved plan found. Run --analyze-only first to generate a plan."`

Verify the plan file's `tile_dir` matches the current `TILE_DIR`.
Then proceed directly to 4a using the plan data.

---

### 4a. Determine What to Create vs Update

| File | Exists? | Action |
|------|---------|--------|
| `pre_opt.tcl` | YES | Add tunesource for r2r_opt if not already present |
| `group_paths.tcl` | YES | Add missing groups. Fix Check C conflicts. |
| `group_paths.tcl` | NO | Create with base generic groups |
| `r2r_optimization.tcl` | YES | Append targeted fixes from Step 3 plan |
| `r2r_optimization.tcl` | NO | Create from scratch with all Step 3 content |

**If `r2r_optimization.tcl` does not exist, add tunesource in `pre_opt.tcl`:**
```
tunesource tune/$TARGET_NAME/$TARGET_NAME.group_paths.tcl
tunesource tune/$TARGET_NAME/$TARGET_NAME.r2r_optimization.tcl   ← ADD THIS
```

---

### 4b. Write group_paths.tcl

Base generic groups if file does not exist:
```tcl
group_path -name SYN_I2O -critical_range 200 -weight 0.001
group_path -name SYN_R2O -critical_range 200 -weight 1
group_path -name SYN_R2R -critical_range 200 -weight 5
group_path -name SYN_I2R -critical_range 200 -weight 3
```
Then add all named groups discovered in Step 3 (Check F, Check G).

---

### 4c. Write r2r_optimization.tcl

Write sections in this order — exactly as planned in Step 3:

1. **Header** — baseline WNS, target, root causes found
2. **Fix A** — phantom clock splits (if found)
3. **Fix F** — new named groups for missing hierarchies
4. **Fix G** — I2R sub-group splits
5. **Fix B+C** — corrected weights with starvation formula applied
6. **Fix D** — architecture limit comment blocks (no tune change)
7. **Fix E** — `create_bound` blocks for wire-dominated groups
8. **Compile settings** — effort knobs from Section 8 (apply only those whose condition was met)
9. **Closing print** — summary puts statements

---

### 4d. Update override.params

If `DDRSS_FEINT_NUM_COMPILES` is not set to 5, update it:
```
DDRSS_FEINT_NUM_COMPILES = 5
```
Do NOT change any other override.params values.

---

### Step 4 Output

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  STEP 4: GENERATE — FILES WRITTEN                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

Files written to: <TILE_DIR>/tune/FxSynthesize/

  group_paths.tcl          <N> lines  [CREATED / UPDATED]
  r2r_optimization.tcl     <N> lines  [CREATED / UPDATED]
  pre_opt.tcl              [tunesource added / already present]

override.params: DDRSS_FEINT_NUM_COMPILES → 5  [UPDATED / already correct]

All changes match the Step 3 proposal exactly.
```

### After Step 3 — Always Save Plan File

Regardless of mode (ANALYZE_ONLY, FULL, or APPLY), Step 3 always saves a plan
file so that `--apply` can execute it later without re-running analysis.

Save to: `<TILE_DIR>/.optimizer_plan.json`

Content:
```json
{
  "tile_dir": "<TILE_DIR>",
  "tile_type": "<design_type>",
  "baseline_wns_ps": <val>,
  "baseline_tns_ps": <val>,
  "period_ps": <val>,
  "primary_pass": <N>,
  "fixes": [
    {"check": "A", "group": "<name>", "action": "split", "modes": ["<MODE_A>", "<MODE_B>"], "weights": [1, <W>]},
    {"check": "F", "module": "<pattern>", "action": "add_group", "name": "<group_name>", "weight": <W>, "critical_range": <CR>},
    {"check": "G", "action": "split_i2r", "sub_groups": [{"name": "<name>", "pattern": "<pat>", "weight": <W>}]},
    {"check": "B", "group": "<name>", "old_weight": <W1>, "new_weight": <W2>},
    {"check": "C", "group": "<name>", "intended_weight": <W>},
    {"check": "D", "group": "<name>", "lol": <N>, "comment": "<arch limit message>"},
    {"check": "E", "group": "<name>", "bound_name": "<name>_bound", "x1": <v>, "y1": <v>, "x2": <v>, "y2": <v>, "cell_filter": "<pattern>"}
  ],
  "compile_settings": {
    "num_compiles": 5,
    "high_effort_timing": true,
    "max_multibit_size": <N>,
    "restructuring_mode": "area_timing"
  },
  "rtl_action_items": [
    {"group": "<name>", "lol": <N>, "wns_ps": <val>, "reason": "<why synthesis cannot fix>", "action": "<Pipeline insertion|Register duplication|Enable pipelining>"}
  ]
}
```

If the plan file already exists (from a previous `--analyze-only` run), overwrite it.

---

## Key Principles

**1. Name groups before tuning weights.**
Generic catch-all groups give the optimizer no specific targets. Named groups
per hierarchy allow it to focus. Introducing named groups alone — without any
weight changes — typically delivers the first meaningful WNS improvement.

**2. Effective weight is what FC sees, not what group_paths.tcl says.**
Build the effective_weight map using the sourcing order rule.
The last-sourced file wins. Verify this before computing starvation.

**3. The starvation formula (`Cost = W × NVP × |WNS|`) reveals hidden problems.**
A group can look fine in isolation but be invisible to the optimizer because
another group has 20–40× higher cost. The formula reveals this in one calculation
without needing to run another pass.

**4. Bounds must be sized from actual cell area — and loose is safer than tight.**
Too tight: the optimizer runs for many hours trying to force placement into an
over-dense region and cannot converge.
Too loose: minor loss of wire reduction benefit, but the run completes normally.
Dense, well-coupled hierarchies: target 40–65% utilization inside the bound.
Large modules or congestion-sensitive cases: 10–20% utilization is intentionally
loose and is the correct choice, not a mistake.
When in doubt: start loose. Tighten in a subsequent run if cells remain scattered.

**5. Architecture limits are permanent until RTL changes.**
No weight, bound, or effort knob reduces logic levels. Identify them early
(LOL > 28, WNS < -20 ps) and stop spending synthesis budget on them.

**6. Safety-check every change before applying.**
After computing new weights, rerun the cost table to confirm no previously-healthy
group is now starved by the rebalancing. Before writing bounds, verify density.
Before finalizing, check that the effective weight map is consistent.

**7. One pass of this skill should equal many iterative runs combined.**
Iterative runs each discover one root cause per pass. This skill checks all 7
root causes from the first run's data and applies all fixes at once. The expected
total improvement should approach what multiple manual iterations would achieve.

---

## Pitfall Reference

| Pitfall | Risk | Safe Practice |
|---------|------|---------------|
| Bounds too tight (density > 400 cells/um²) | Optimizer runs indefinitely | Target ~200 cells/um²; always -type soft |
| `set_boundary_optimization` on large hierarchy | TNS explosion | Apply only to specific, targeted sub-hierarchy |
| Same group in both tune files | Weight silently overridden | Build effective_weight map; last-sourced file wins |
| `set_max_fanout` on broad hierarchy | Buffering overhead, wasted budget | Apply only to specific named registers from path trace |
| Any bound without `enable_auto_feasibility_recovery` | No recovery path from congestion | Always set it before any `create_bound` |
| Raising weights without rebalancing cost table | Creates a new dominant group that starves others | Recompute cost distribution after every weight change |
| `group_path` without `-to` or `-from` | Empty group, wastes optimizer memory | Always specify cell scope from hierarchy map |
| Adding bounds to architecture-limited groups | Zero timing gain | Check D first; skip bounds for LOL > 28 |
| I2R sub-group weight too high vs R2R groups | I2R starves R2R, overall WNS does not improve | Check cost distribution includes both I2R and R2R groups |
| Retiming with backward mode enabled | Pass instability — WNS regresses between passes | Enable forward only first; add backward only if verified stable |
| set_max_fanout on broad hierarchy | Buffering overhead, wasted optimizer budget | Apply only to specific registers named in FuncTT0p9v path trace |
| RTL fix suggested without reading actual source | Wrong file, wrong signal, wrong fix | Always read from GetRTL.source.vf paths — never guess |
