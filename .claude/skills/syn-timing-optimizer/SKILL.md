# Synthesis Timing Optimizer Skill

One-shot FxSynthesize timing fix for any tile.
Reads QoR → diagnoses 7 root causes → proposes plan → writes tune files.

## Output Policy
**Never summarize. Print every table in full. No truncation.**
Plan JSON is a machine artifact. The terminal report is primary — must be complete.

## Trigger
`/syn-timing-optimizer`

## Usage
```
/syn-timing-optimizer <tile_dir>                # full run  — Steps 1→2→3→4
/syn-timing-optimizer <tile_dir> --analyze-only # Steps 1→2→3, no writes
/syn-timing-optimizer <tile_dir> --apply        # Step 4 only, reads saved plan
/syn-timing-optimizer --list                    # show this help
```

**--analyze-only → --apply workflow:**
```
/syn-timing-optimizer /proj/.../my_tile --analyze-only  # review plan
/syn-timing-optimizer /proj/.../my_tile --apply         # apply it
```
Plan saved to `<tile_dir>/.optimizer_plan.json` after Step 3.

## --list: Print Inline (no subagent)
```
OPTIONS
  (no flag)        Steps 1→2→3→4   full run, writes tune files
  --analyze-only   Steps 1→2→3     review only, no writes, saves plan
  --apply          Step 4 only     reads .optimizer_plan.json, writes files
  --list           show this help

STEPS
  1 READ      QoR passes, endpoint hierarchies, effective weights
  2 DIAGNOSE  7 root cause checks A–G
  3 SUGGEST   full plan + RTL flags, saves .optimizer_plan.json
  4 GENERATE  writes tune files exactly as proposed in Step 3

CHECKS  [A] Phantom clocks  [B] Weight starvation  [C] Override conflicts
        [D] Arch limits     [E] Wire-dominated      [F] Missing groups
        [G] I2R catch-all split

WRITES (Step 4)
  tune/FxSynthesize/FxSynthesize.group_paths.tcl
  tune/FxSynthesize/FxSynthesize.r2r_optimization.tcl
  tune/FxSynthesize/FxSynthesize.pre_opt.tcl  (tunesource added if missing)
  override.params  (DDRSS_FEINT_NUM_COMPILES = 5)

REQUIREMENTS
  rpts/FxSynthesize/FxSynthesize.pass_*.proc_qor.rpt.gz  (≥1 pass)
  tune/FxSynthesize/  (may be empty — created if missing)
```

---

## Execution Model

Spawn ONE `general-purpose` subagent. Wait. Print its output **verbatim in full** — no summary, no highlights, no paraphrasing.

```python
Agent(
  description="syn-timing-optimizer — <tile_name>",
  subagent_type="general-purpose",
  prompt="""
DO NOT SPAWN ANY FURTHER SUBAGENTS. Execute all steps directly with Bash/Read/Write.

TILE_DIR  = <resolved_tile_dir>
MODE      = <FULL | ANALYZE_ONLY | APPLY>
PLAN_FILE = <tile_dir>/.optimizer_plan.json

Follow Steps 1–4 exactly. Return the full report.
=== INSTRUCTIONS BELOW ===
<paste full Step 1,2,3,4 content>
"""
)
```

---

## Step 1: READ

### 1a. Detect type, inventory tune files
```bash
basename <TILE_DIR>
ls -la <TILE_DIR>/tune/FxSynthesize/
```
Read ALL existing `.tcl` files fully. Find every `tunesource` line in `pre_opt.tcl`.

### 1b. Find QoR passes
```bash
ls -v <TILE_DIR>/rpts/FxSynthesize/FxSynthesize.pass_*.proc_qor.rpt.gz 2>/dev/null
```
STOP if none found. Highest N = PRIMARY dataset.

### 1c. Extract per-group timing — all passes
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/FxSynthesize.pass_N.proc_qor.rpt.gz \
  | grep -E "Timing Path Group|Levels of Logic|Critical Path Slack|Critical Path Length|Critical Path Clk Period|Total Negative Slack|No\. of Violating"
```
Extract per group: `WNS_ps`, `TNS_ps`, `NVP`, `LOL`, `CritLen_ps`, `Period_ps`.

### 1d. Read endpoint hierarchies
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/report_timing.pass_*.rpt.sum.sort_slack.endpts.gz \
  2>/dev/null | head -60
```
Top 20 worst endpoints — this reveals WHICH modules are failing.

### 1e. Read worst-path traces (top 3 groups)
```bash
zcat <TILE_DIR>/rpts/FxSynthesize/FuncTT0p9v_<GROUP>_max.rpt.gz 2>/dev/null | head -80
```
Extract: startpoint/endpoint hierarchy, wire vs gate delay split, MB4/MB8 launch cell.

### 1f. Build effective weight map
Rule: `r2r_optimization.tcl` sourced AFTER `group_paths.tcl` → r2r_opt weight wins if both define same group.
`effective_weight[group] = weight from last-sourced file`

### 1g. Read override.params
```bash
grep -E "DDRSS_FEINT_NUM_COMPILES|max_multibit_size|FLOORPLAN_DEF" <TILE_DIR>/override.params
```

### Step 1 Output — ALL groups, ALL passes, full tables
```
╔══════════════════════════════════════════════════════════════════╗
║  STEP 1: READ   Tile:<name>  Type:<type>  Period:<ps>  P<list>  ║
╚══════════════════════════════════════════════════════════════════╝
Tune files: pre_opt=YES  group_paths=YES/NO  r2r_opt=YES/NO  post_opt=YES/NO
DEF: <path>  [EXISTS / MISSING]

--- ALL Groups Primary Pass (P<N>) ---
  Path Group          WNS(ps)   TNS(ps)    NVP   LOL  W(eff)  CritLen(ps)  CritLen%
  <every group, sorted worst WNS first, including non-violating>

--- Pass Progression ALL Groups ---
  Path Group          P1        P2        P3        P4        P5

--- Top 20 Endpoints ---
  Rank  Group          Endpoint (last 3 levels)              Slack(ps)  LOL

--- Effective Weight Map ALL Groups ---
  Group               gp.tcl W   r2r_opt W   Effective   Conflict?

Compile passes: <N> (target: 5)
```

---

## Step 2: DIAGNOSE — 7 Checks

### Check A: Phantom Clock Paths
**Detect:** group with no scenario suffix + `NVP > 500` + `|TNS| > 50,000 ps`
Paths between mutually exclusive modes dominate WNS/TNS, hiding real violations.
**Fix:** split into one group per mode.
**Verdict:** FOUND(group, NVP, TNS) or CLEAN

### Check B: Weight Starvation
**Formula:** `Cost = effective_weight × NVP × |WNS_ps|`
**Starved if:** `Cost < 5% of max Cost`
**New weight:** `clamp(round(sqrt(max_cost / (NVP × |WNS|))), 6, 16)`
Always use effective_weight — r2r_opt override may silently reduce it.
**Verdict:** full cost table, all violating groups

### Check C: Override Conflicts
**Detect:** same `group_path -name X` in both files with different weights.
r2r_opt.tcl weight wins silently. Compare effective_weight vs group_paths.tcl weight.
**Fix:** correct the weight in r2r_opt.tcl.
**Verdict:** table of all groups showing both weights

### Check D: Architecture Limits
**Detect:** `LOL > 28` AND `WNS < -20 ps`
~9.5 ps/gate × 30 levels ≈ 285 ps = 96% of 295 ps period. No synthesis fix.
**Fix:** comment block only. Escalate to RTL team.
**Verdict:** list of arch-limited groups

### Check E: Wire-Dominated Paths

**Two triggers (either sufficient):**
- T1: `CritLen_ps > Period_ps × 0.95` AND `WNS < -30 ps` — fires regardless of LOL
- T2: `LOL < 15` AND `WNS < -50 ps`

**Step E-1: Classify each flagged group as BOUND or PORT-LIMITED**

Read the worst-path trace (FuncTT0p9v) for each flagged group:
- If `Startpoint:` has hierarchy levels (`/`) → register → **BOUND**
- If `Startpoint:` is a bare port name (no `/`) → I/O port → **PORT-LIMITED**

Common PORT-LIMITED groups (startpoint is always an I/O port):
- `io_to_io`, `io_to_flop`, `SYN_I2R` when endpoints all come from primary input ports

PORT-LIMITED groups: add to RTL action items only. Do NOT generate a create_bound.

**Step E-2: One Fix E entry per BOUND group (never merge multiple groups)**

Each BOUND group gets its OWN separate fix entry. Never combine multiple groups
into one entry (e.g. `umc_ARB_r2r_to+umc_ARB_internal_r2r` is WRONG).
If two groups share a similar hierarchy, give each its own bound with its own filter.

**Step E-3: Populate coordinates for EVERY BOUND group**

Coordinates must NEVER be left empty. Compute actual numbers.

**DEF available → derive from COMPONENTS:**
```bash
zcat <def>.gz | grep "UNITS DISTANCE"           # → scale DBU/um
zcat <def>.gz | grep "^\- .*<hier_keyword>" \
  | grep -oE "\( [0-9]+ [0-9]+ \)" | tr -d '()' \
  | awk 'BEGIN{minx=1e9;miny=1e9;maxx=0;maxy=0}
         {if($1<minx)minx=$1; if($1>maxx)maxx=$1;
          if($2<miny)miny=$2; if($2>maxy)maxy=$2}
         END{print minx/S,miny/S,maxx/S,maxy/S}' S=<scale>
# expand bbox 25%, verify density < 400 cells/um²
```

**DEF missing → estimate using quadrant table (400 × 600 um default die):**
```
ARB/DCQARB*        → center      x1=133, y1=200, x2=267, y2=400
ARB/DCQARB1*       → center-high x1=133, y1=220, x2=280, y2=440
ARB/PGT*           → right       x1=267, y1=200, x2=400, y2=400
ARB internal       → lower       x1=0,   y1=0,   x2=267, y2=200
ARB/RH*            → upper-left  x1=0,   y1=370, x2=210, y2=600
FEI*               → left        x1=0,   y1=0,   x2=133, y2=440
ADDR*              → bottom      x1=0,   y1=0,   x2=267, y2=200
SPAZ*              → right-bot   x1=267, y1=0,   x2=400, y2=200
clock_gating_default→ center     x1=100, y1=150, x2=300, y2=450
SYN_R2R            → use top endpoint module from sort_slack.endpts,
                     map that module to its quadrant above
TIM*               → lower-right x1=200, y1=0,   x2=400, y2=200
CMD*               → center-left x1=0,   y1=150, x2=240, y2=490
```
Expand by 20% margin. Mark `coords_source="ESTIMATED"`.

**Step E-4: Cell filter — precise hierarchy from sort_slack.endpts**

Take top-ranked endpoint for the group from sort_slack.endpts.
Strip the signal name and last register instance → that prefix is the filter.

```
Example: ARB/DCQARB/ArbSafeRegPc_d1/q_reg[3]
  → prefix = ARB/DCQARB
  → filter = "full_name =~ *ARB/DCQARB*"

clock_gating_default: read worst CG path trace → use clock-gater cell hierarchy
  → filter = "full_name =~ *<module_from_cg_trace>/clk_gate*"
```

**SPECIAL CASE — ARB internal (`umc_ARB_internal_r2r`):**
This group covers the residual ARB logic that is NOT part of DCQARB, PGT, or RH.
Never use bare `*ARB/*` — it will capture ALL ARB sub-hierarchies including DCQARB
(which already has its own bound), causing the optimizer to double-bound DCQARB cells
and create a density spike.

Mandatory exclusion filter for ARB internal:
```
"full_name =~ *ARB/* && full_name !~ *ARB/DCQARB* && full_name !~ *ARB/DCQARB1* && full_name !~ *ARB/PGT* && full_name !~ *ARB/RH*"
```

**GENERAL RULE for any group that is a parent hierarchy:**
If the group is named `<module>_internal` or represents residual cells after
sub-hierarchies are split off, always use exclusion filters for every known
sub-hierarchy that has its own dedicated bound.

Never use bare `*`. Never use broad `*ARB/*` when a specific sub-hierarchy is the target.

**PORT-LIMITED groups — add to diagnoses, no Fix E entry:**
Groups where startpoint is an I/O port (`io_to_io`, `io_to_flop`, `SYN_I2R` I/O paths)
must be recorded in `diagnoses.E_wire_dominated.flagged` as PORT-LIMITED and added
to RTL action items. They must NOT appear in the Fix E list.
```json
{"group":"io_to_io","trigger":"T1","CritLen_ps":363.91,"pct_period":123.3,
 "type":"PORT-LIMITED","reason":"startpoint is primary I/O port — bound cannot help",
 "rtl_action":"Register at tile boundary to reduce wire to port"}
```

**Plan entry — ALL fields required, one entry per BOUND group:**
```json
{"fix_id":"FE-N","check":"E","group":"<name>","action":"create_bound",
 "bound_name":"<name>_bound",
 "cell_filter":"full_name =~ *<precise_hierarchy>*",
 "cell_filter_exclusions":["full_name !~ *<sub>*"],
 "coordinates_um":{"x1":<num>,"y1":<num>,"x2":<num>,"y2":<num>},
 "coords_source":"DEF-derived|ESTIMATED",
 "density_cells_per_um2":<num>,
 "warning":"ESTIMATED — verify against DEF"}
```

**Verdict:** ALL flagged groups listed with BOUND or PORT-LIMITED classification.
Diagnoses section must be populated: `diagnoses.E_wire_dominated.flagged = [...]`

### Check F: Missing Groups
**Detect:** module in top-20 endpoints but no `group_path -to [get_cells *module*]` exists.
**Fix:** add named group_path for that hierarchy.
**Verdict:** table of all unrepresented hierarchies

### Check G: I2R Catch-All Split
**Detect:** SYN_I2R endpoints span 2+ distinct modules OR 2+ clocks, OR WNS improves < 5 ps/pass.
**Fix:** split into `SYN_I2R_<module>` sub-groups.
**Verdict:** SPLIT NEEDED (list sub-groups) or CLEAN

### Step 2 Output — full tables, no omissions
```
╔══════════════════════════════════════════════════════════════════╗
║  STEP 2: DIAGNOSIS                                              ║
╚══════════════════════════════════════════════════════════════════╝
[A] Phantom Clocks    : FOUND⚠ / CLEAN✓
[B] Starvation        : N groups⚠ / CLEAN✓
[C] Override Conflicts: N conflicts⚠ / CLEAN✓
[D] Arch Limits       : N groups / CLEAN✓
[E] Wire-Dominated    : N groups (M BOUND, K PORT-LIMITED) / CLEAN✓
[F] Missing Groups    : N hierarchies⚠ / CLEAN✓
[G] I2R Split         : NEEDED⚠ / CLEAN✓

--- Check B: Cost Table ALL violating groups ---
  Group  W(eff)  NVP  WNS(ps)  Cost  Cost%  Starved?  New W
  (all groups with WNS<0, sorted by cost desc)
  Max cost: <val>  5% threshold: <val>

--- Check C: ALL groups in any tune file ---
  Group  gp.tcl W  r2r_opt W  Effective  Conflict?

--- Check D: Arch Limits ---
  Group  LOL  WNS(ps)  Gate Time(ps)  Period%  Action

--- Check E: ALL flagged groups ---
  Group  LOL  WNS(ps)  CritLen(ps)  CritLen%  Trigger  Fixable?

--- Check F: Missing hierarchies ---
  Module  Ranks  Named group?

--- Check G: I2R detail ---
  Endpoint modules: <list>   Clock domains: <list>
  WNS/pass: P1=<v> P2=<v> P3=<v>   Verdict: SPLIT/CLEAN
  Sub-groups: <name> w=<W> to=<pattern> ...

Priority: [A]>[F]>[G]>[B][C]>[E]>[D]
```

---

## Step 3: SUGGEST — Propose All Changes

**Both modes.** No file writes. Saves `.optimizer_plan.json`.
Show complete TCL for every change. Never abbreviate with `...`.

### 3a. Build hierarchy map from timing data
From sort_slack.endpts + path traces: `group → endpoint prefix → get_cells filter`
This drives every group_path and create_bound written.

### 3b. What to create vs update
| File | Exists | Action |
|------|--------|--------|
| pre_opt.tcl | YES | add tunesource for r2r_opt if missing |
| group_paths.tcl | YES/NO | add missing groups; fix conflicts |
| r2r_optimization.tcl | YES/NO | append or create from scratch |

If r2r_opt.tcl missing → add to pre_opt.tcl:
```
tunesource tune/$TARGET_NAME/$TARGET_NAME.group_paths.tcl
tunesource tune/$TARGET_NAME/$TARGET_NAME.r2r_optimization.tcl
```

### 3c. group_paths.tcl — base generic groups
```tcl
group_path -name SYN_I2O -critical_range 200 -weight 0.001
group_path -name SYN_R2O -critical_range 200 -weight 1
group_path -name SYN_R2R -critical_range 200 -weight 5
group_path -name SYN_I2R -critical_range 200 -weight 3
# + named groups from Check F/G with weight from starvation formula
```

### 3d. r2r_optimization.tcl — write in order
1. **Header** — baseline WNS/TNS/period, checks found
2. **Fix A** — phantom splits: `group_path -name <g>_<MODE> -weight <W>`
3. **Fix F** — new named groups for missing hierarchies
4. **Fix G** — I2R sub-groups per destination/clock
5. **Fix B+C** — corrected weights: `# cost was X% / conflict resolved`
6. **Fix D** — arch-limit comment blocks only
7. **Fix E** — create_bound blocks (see template below)
8. **Compile settings** — effort knobs (conditional table below)
9. **Closing puts** — summary

**Fix E TCL template:**
```tcl
set_app_options -name compile.flow.enable_auto_feasibility_recovery -value true

# === <group_name> bound ===
# CritLen=<X>ps (<pct>%), LOL=<N>, WNS=<val>ps | coords: <source>
# bbox=(<x1>,<y1>)–(<x2>,<y2>)um | area=<A>um² | density=<D>cells/um²
set <name>_cells [get_cells -quiet -hier -filter \
    "full_name =~ *<precise_hierarchy>*"]
if {[sizeof_collection $<name>_cells] > 0} {
    create_bound -name <name>_bound \
        -boundary [list [list <x1> <y1>] [list <x2> <y2>]] \
        -type soft $<name>_cells
} else {
    puts "WARNING: <name>_bound — no cells matched *<precise_hierarchy>*"
}
```
Rules: `-hier` required; filter case-sensitive; always `-type soft`; density < 400 cells/um².

**Compile settings — apply by condition:**
```tcl
# Always:
set_app_options -name compile.flow.high_effort_timing              -value 1
set_app_options -name opt.timing.effort                            -value high
set_app_options -name opt.area.effort                              -value high
set_app_options -name opt.common.buffer_area_effort                -value ultra
set_app_options -name compile.timing.prioritize_tns                -value true
set_app_options -name opt.timing.slack_based_tns_optimization      -value true
set_app_options -name opt.timing.tns_optimization_paths_per_endpoint -value <NVP/10 min 5>
set_app_options -name opt.common.advanced_logic_restructuring_mode -value area_timing
set_app_options -name opt.common.advanced_logic_restructuring_wirelength_costing -value high

# If Check E fired (wire-dominated):
set_app_options -name compile.final_place.effort                   -value high
set_app_options -name place_opt.final_place.effort                 -value high
set_app_options -name place_opt.place.congestion_effort            -value high
set_app_options -name route.common.rc_driven_setup_effort_level    -value high
set_app_options -name route.global.effort_level                    -value high
set_congestion_optimization [get_designs] TRUE

# If MB4/MB8 in launch FF (from path trace):
set_app_options -name compile.flow.max_multibit_size               -value <4|6>
set_app_options -name compile.flow.enable_rtl_multibit_debanking   -value true
set_multibit_options -slack_threshold 0

# If fanout > 200 in path trace (specific register only):
# set_max_fanout <N*0.60> [get_cells -quiet -hier -filter "full_name =~ *<reg>*"]

# If same startpoint ≥3 times in sort_slack.endpts:
set_app_options -name compile.timing.buffer_replication            -value true

# If clock transition in path trace > 50 ps:
# set_clock_transition <T> [get_clocks -quiet *<clock_name>*]

# If timing appears more pessimistic than LOL count suggests:
# set_app_options -name time.enable_cond_default_arcs -value true
```

### Step 3 Output
```
╔══════════════════════════════════════════════════════════════════╗
║  STEP 3: SUGGESTION PLAN  Mode:<ANALYZE-ONLY|FULL>              ║
╚══════════════════════════════════════════════════════════════════╝

⚠ DEF WARNING (if applicable): <path> MISSING — bounds use ESTIMATED coords

--- Fix List (ALL fixes) ---
  Fix  Check  Group/Module  Finding                 Action

--- Full Diff: group_paths.tcl ---
  - group_path -name <g> -weight <old> ...
  + group_path -name <g> -weight <new> ...
  + group_path -name <new_group> -critical_range <CR> -weight <W> \
        -to [get_cells -quiet -hier -filter "full_name =~ *<pattern>*"]

--- Full Diff: r2r_optimization.tcl ---
  [Fix A]  + group_path -name <g>_<MODE> -critical_range <CR> -weight <W>
  [Fix B/C] - old line / + corrected line with weight rationale comment
  [Fix E]  + full create_bound block (complete TCL, no placeholders)
  [Compile] + every set_app_options line that will be written

--- Full Diff: pre_opt.tcl ---
  + tunesource ... r2r_optimization.tcl  (if missing)

--- Full Diff: override.params ---
  DDRSS_FEINT_NUM_COMPILES = <cur> → 5

--- Expected WNS Gain per Fix ---
  Fix  Check  Group  Gain(ps)  Basis

--- RTL Action Items (ALL) ---
  Priority  Group  LOL  WNS(ps)  Reason  Action  Endpoint
  (every arch-limited and port-limited group)

--- Next Steps ---
  ANALYZE_ONLY: run --apply when ready
  FULL/APPLY:   run FxSynthesize → /syn-timing --comparison <baseline> <new>
```

### Save plan file (always, every mode)
`<TILE_DIR>/.optimizer_plan.json`
```json
{
  "tile_dir":"<>","tile_type":"<>","baseline_wns_ps":<>,"baseline_tns_ps":<>,
  "period_ps":<>,"primary_pass":<>,
  "fixes":[
    {"check":"A","group":"<>","action":"split","modes":[],"weights":[]},
    {"check":"B","group":"<>","old_weight":<>,"new_weight":<>},
    {"check":"C","group":"<>","intended_weight":<>},
    {"check":"D","group":"<>","lol":<>,"comment":"<>"},
    {"check":"E","group":"<>","action":"create_bound","bound_name":"<>_bound",
     "cell_filter":"<>","cell_filter_exclusions":[],
     "coordinates_um":{"x1":<>,"y1":<>,"x2":<>,"y2":<>},
     "coords_source":"DEF-derived|ESTIMATED","density_cells_per_um2":<>},
    {"check":"F","module":"<>","action":"add_group","name":"<>","weight":<>},
    {"check":"G","action":"split_i2r","sub_groups":[]}
  ],
  "compile_settings":{"num_compiles":5,"high_effort_timing":true,"max_multibit_size":<>},
  "rtl_action_items":[{"group":"<>","lol":<>,"wns_ps":<>,"reason":"<>","action":"<>"}]
}
```

---

## Step 4: GENERATE — Write Tune Files

**FULL and APPLY modes only.**

### APPLY mode — load plan
```bash
cat <TILE_DIR>/.optimizer_plan.json
```
STOP if missing: `"No saved plan. Run --analyze-only first."`

**DEF gate for ESTIMATED bounds:**
```bash
ls -lh $(grep FLOORPLAN_DEF <TILE_DIR>/override.params | awk '{print $NF}')
```
- DEF accessible → re-derive coordinates before writing
- DEF still missing → print warning, proceed with estimates

### Write order
1. `pre_opt.tcl` — add tunesource if missing
2. `group_paths.tcl` — base groups + Check F/G groups
3. `r2r_optimization.tcl` — sections 1–9 as planned in Step 3
4. `override.params` — DDRSS_FEINT_NUM_COMPILES = 5

### Step 4 Output
```
╔══════════════════════════════════════════════════════════════════╗
║  STEP 4: GENERATE — FILES WRITTEN                               ║
╚══════════════════════════════════════════════════════════════════╝
  group_paths.tcl        <N> lines  [CREATED/UPDATED]
  r2r_optimization.tcl   <N> lines  [CREATED/UPDATED]
  pre_opt.tcl            [tunesource added/already present]
  override.params        DDRSS_FEINT_NUM_COMPILES → 5 [UPDATED/OK]
All changes match Step 3 proposal exactly.
```

---

## Key Principles

1. **Name groups before tuning weights.** Named groups alone give the first WNS gain.
2. **Effective weight = last-sourced file wins.** r2r_opt.tcl overrides group_paths.tcl.
3. **Cost = W × NVP × |WNS| reveals starvation.** A group at 2% of max is invisible.
4. **Bounds: loose is safer than tight.** Dense hierarchies: 40–65% util. Congestion-sensitive: 10–20%.
5. **Arch limits need RTL.** LOL > 28 with WNS < -20 ps — synthesis cannot reduce logic levels.
6. **Safety-check after rebalancing.** Recompute cost table; no group should drop below 5%.

---

## Pitfall Reference

| Pitfall | Risk | Safe Practice |
|---------|------|---------------|
| Bounds density > 400 cells/um² | Hours of non-convergence | Target ~200; always -type soft |
| `set_boundary_optimization` on large hierarchy | TNS explosion | Only on specific sub-hierarchy |
| Same group in both tune files | Weight silently overridden | r2r_opt wins; verify effective weight |
| `set_max_fanout` on broad hierarchy | Buffering overhead | Specific registers from path trace only |
| Bound without `enable_auto_feasibility_recovery` | No congestion recovery | Set it before any create_bound |
| Weight raised without cost rebalance | Creates new starvation | Recompute cost table after every change |
| `group_path` without -to/-from | Empty group, wasted memory | Always specify cell scope |
| Bound on arch-limited group (LOL > 28) | Zero gain | Check D first; skip bound |
| Backward retiming enabled | Pass instability | Forward only; validate monotonic convergence |
