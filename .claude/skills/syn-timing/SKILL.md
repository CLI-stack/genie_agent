# Synthesis Timing Report Skill

Report FxSynthesize timing and QoR for UMC project tiles.
Three modes: **simple**, **analysis**, **comparison**.

## Trigger
`/syn-timing`

## Usage
```
/syn-timing                                             # simple, latest umccmd + umcdat
/syn-timing --simple                                    # explicit simple mode
/syn-timing --analysis                                  # full analysis mode
/syn-timing umccmd                                      # simple, latest umccmd only
/syn-timing umcdat --analysis                           # analysis, latest umcdat only
/syn-timing /path/to/run                                # simple, specific run dir
/syn-timing /path/to/run --analysis                     # analysis, specific run dir

# Comparison — two run directories, same tile
/syn-timing --comparison /path/to/run_A /path/to/run_B
/syn-timing --comparison /path/to/run_A /path/to/run_B --analysis
```

- Default mode when no flag given: **simple**
- `--comparison` requires exactly two absolute run directory paths
- `--comparison` without `--analysis`: compare timing summary only (simple data)
- `--comparison --analysis`: compare full analysis + root cause of changes

## Tiles Base Directory
The user must supply the tile run path(s) directly. No hardcoded default.

---

## Execution Model

**Do NOT perform the analysis in the main session context.**

1. Resolve all tile dirs inline in the main session (quick `ls` + existence check).
2. Determine `MODE` from user args (`simple`, `analysis`, `comparison`, or `comparison+analysis`).
3. Spawn a `general-purpose` subagent with resolved paths and MODE in the prompt.
4. Wait for the agent to complete. Print its output verbatim — no post-processing.

```python
Agent(
  description="FxSynthesize timing — <MODE> — <run_dir_name(s)>",
  subagent_type="general-purpose",
  prompt="""
You are a timing analysis agent for UMC synthesis.

TILE_DIR_A = <resolved absolute path>        # always present
TILE_DIR_B = <resolved absolute path>        # only for comparison mode
MODE       = <simple | analysis | comparison | comparison+analysis>

Follow the instructions for MODE exactly. Return only the formatted report —
no preamble, no explanation.

=== INSTRUCTIONS ===
<paste the full MODE sections below>
"""
)
```

---

## Step 1 — Find the Tile Directory

If the user provides an absolute path, use it directly.

Otherwise scan the tiles base directory for subdirectories matching the tile
prefix (`umccmd*` or `umcdat*`). For each candidate, check that
`rpts/FxSynthesize/FxSynthesize.dat` exists **and contains at least one
`CostGroup:` line** (runs still in progress have no CostGroups yet). Among
qualifying directories, pick the one whose `FxSynthesize.dat` has the newest
modification time.

If no tile is specified, report both umccmd and umcdat (one block each).

---

## MODE: SIMPLE

Read only two sources for each tile:

### Files to read

**A. `FxSynthesize.dat`** — extract:
- All `CostGroup: <name> <WNS> <TNS> <NVP> <Period>` lines
- `totalCoreWNS`, `totalCoreTNS`, `totalCoreNVP`
- `totalIOWNS`, `totalIOTNS`, `totalIONVP`
- `DesignWNS`, `DesignTNS`, `DesignNVP`

**B. All available `FxSynthesize.pass_*.proc_qor.rpt.gz`** (pass_1, pass_2, pass_3 or
whichever exist) — these files are very large (~1M lines) and almost entirely
warning noise. **Do NOT read the full file.** Use grep to extract only the
relevant lines before parsing:

```bash
zcat FxSynthesize.pass_N.proc_qor.rpt.gz \
  | grep -E "Timing Path Group|Levels of Logic|Critical Path Slack|Critical Path Clk Period|Total Negative Slack|No\. of Violating"
```

From the filtered output, for each pass and each path group extract:
- `Timing Path Group` name
- `Critical Path Slack` (ps)
- `Total Negative Slack` (ps)
- `No. of Violating Paths`
- `Levels of Logic`

### Simple Output Layout

```
========================================================================
  FxSynthesize Timing Report  —  <MODULE UPPERCASE>  [SIMPLE]
  Run: <run_dir_name>
========================================================================

--- Final Timing by Path Group  (FxSynthesize.dat, ps) ---
  Path Group                WNS (ps)    TNS (ps)   Violations   Period (ps)  Status
  ───────────────────────────────────────────────────────────────────────────────────
  <name padded to 24>      <+WNS>      <TNS>       <NVP>        <Period>     OK / !!
  ...

--- Design Timing Totals  (ps) ---
  Scope     WNS (ps)      TNS (ps)       NVP
  ────────────────────────────────────────────
  Core      <val>         <val>          <val>
  IO        <val>         <val>          <val>
  DESIGN    <val>         <val>          <val>

--- Per-Pass Timing Progression by Path Group  (proc_qor, ps) ---

  Path Group            Lvls   Pass    WNS (ps)      TNS (ps)    Viol
  ─────────────────────────────────────────────────────────────────────
  <name padded to 22>   <L>     P1     <val>         <val>       <N>
                                P2     <val>         <val>       <N>
                                P3     <val>         <val>       <N>
  ─────────────────────────────────────────────────────────────────────
  <next group>          <L>     P1     <val>         <val>       <N>
                                P2     <val>         <val>       <N>
                                P3     <val>         <val>       <N>
  ─────────────────────────────────────────────────────────────────────
  ...

  Rules:
  - Path group name and Lvls appear only on the P1 row; P2/P3 rows leave those columns blank
  - A separator line ─── is drawn between every path group
  - Lvls shows the final pass value
  - WNS always shows sign (+ or -)
```

---

## MODE: ANALYSIS

Read all four sources:

### Files to read

**A. `FxSynthesize.dat`** — extract:
- All `CostGroup: <name> <WNS> <TNS> <NVP> <Period>` lines
- `totalCoreWNS`, `totalCoreTNS`, `totalCoreNVP`
- `totalIOWNS`, `totalIOTNS`, `totalIONVP`
- `DesignWNS`, `DesignTNS`, `DesignNVP`
- `totalCellArea`, `FlopCount`, `totalCellCount`, `TotalWireLength`
- `TotalFgcgGatedSeqRatio`, `MBBCellBankingRatio`

**B. `FxSynthesize.pass_*.proc_qor.rpt.gz`** (all available passes) — these files
are very large (~1M lines) and almost entirely warning noise. **Do NOT read the
full file.** Use grep to extract only the relevant lines:

```bash
zcat FxSynthesize.pass_N.proc_qor.rpt.gz \
  | grep -E "Timing Path Group|Levels of Logic|Critical Path Slack|Critical Path Length|Critical Path Clk Period|Total Negative Slack|No\. of Violating"
```

From the filtered output extract per group: `Timing Path Group`, `Levels of Logic`,
`Critical Path Length` (ps), `Critical Path Slack` (ps), `Critical Path Clk Period` (ps),
`Total Negative Slack` (ps), `No. of Violating Paths`.

**C. `report_timing.pass_3.rpt.sum.sort_slack.endpts.gz`** (or highest available pass)
— for each `#N` row: Rank, Path Group, Uniquified Endpoint, Worst Startpoint,
Slack (ps), Lvls, Real Lvls, bfx count.

**D. `FuncTT0p9v_<GROUP>_max.rpt.gz`** — for the **top 3 worst violating groups**
(by WNS). From the first timing path in each file extract:
- `Startpoint:`, `Endpoint:`, clock name, `Scenario:`
- The **3 nets with highest fanout** (Fanout column; net rows only)
- The **3 nets with highest capacitance** (Cap column)
- The **top 5 cells by incremental delay** (Incr column, non-clock rows)
- Final slack, data arrival time, data required time

Path group → filename mapping:
- `SYN_R2R`              → `FuncTT0p9v_SYN_R2R_max.rpt.gz`
- `SYN_I2R`              → `FuncTT0p9v_SYN_I2R_max.rpt.gz`
- `SYN_R2O`              → `FuncTT0p9v_SYN_R2O_max.rpt.gz`
- `SYN_nonIP`            → `FuncTT0p9v_SYN_nonIP_max.rpt.gz`
- `clock_gating_default` → `FuncTT0p9v___clock_gating_default___max.rpt.gz`
- `I2C` / `io_to_flop`  → `FuncTT0p9v_io_to_flop_max.rpt.gz`
- `C2O` / `flop_to_io`  → `FuncTT0p9v_flop_to_io_max.rpt.gz`

Also read `status_xls.rpt` for Setup/Hold detail and VT mix.

### Analysis Output Layout

```
========================================================================
  FxSynthesize Timing Report  —  <MODULE UPPERCASE>  [ANALYSIS]
  Run: <run_dir_name>
========================================================================

--- Design Statistics ---
  Metric            Value
  ──────────────────────────────────
  Flops             <FlopCount>
  Total Cells       <totalCellCount>
  Cell Area         <totalCellArea>
  Wire Length       <TotalWireLength>
  FGCG Ratio        <TotalFgcgGatedSeqRatio>
  MBB Banking       <MBBCellBankingRatio>

--- Setup Timing by Path Group  (ps) ---
  Path Group                WNS (ps)    TNS (ps)   Viol   Levels  CritLen (ps)  Period (ps)  Status
  ───────────────────────────────────────────────────────────────────────────────────────────────────
  <name padded to 24>      <+WNS>      <TNS>      <NVP>   <Lvls>   <CritLen>    <Period>     OK / !!
  ...
  (WNS/TNS/NVP from FxSynthesize.dat; Levels+CritLen from proc_qor)

--- Per-Pass Timing Progression by Path Group  (proc_qor, ps) ---

  Path Group            Lvls   Pass    WNS (ps)      TNS (ps)    Viol
  ─────────────────────────────────────────────────────────────────────
  <name padded to 22>   <L>     P1     <val>         <val>       <N>
                                P2     <val>         <val>       <N>
                                P3     <val>         <val>       <N>
  ─────────────────────────────────────────────────────────────────────
  <next group>          <L>     P1     <val>         <val>       <N>
                                P2     <val>         <val>       <N>
                                P3     <val>         <val>       <N>
  ─────────────────────────────────────────────────────────────────────
  ...

  (Path group name + Lvls on P1 row only; separator line between every group)

--- Design Timing Totals  (ps) ---
  Scope     WNS (ps)      TNS (ps)       NVP
  ────────────────────────────────────────────
  Core      <val>         <val>          <val>
  IO        <val>         <val>          <val>
  DESIGN    <val>         <val>          <val>

--- Setup Timing Detail  (ns, status_xls.rpt) ---
  Path Type    WNS (ns)    TNS (ns)    NVE
  ──────────────────────────────────────────
  R→R          <val>       <val>       <val>
  R→ICG        <val>       <val>       <val>
  I→R          <val>       <val>       <val>
  R→O          <val>       <val>       <val>
  I→O          <val>       <val>       <val>

--- Hold Timing Detail  (ns, status_xls.rpt) ---
  Path Type    WNS (ns)    TNS (ns)    NVE
  ──────────────────────────────────────────
  R→R          <val>       <val>       <val>
  R→ICG        <val>       <val>       <val>
  I→O          <val>       <val>       <val>

--- VT Mix ---
  VT Type    Cell Count    Percentage
  ──────────────────────────────────────
  LVTLL      <count>       <pct>%
  LVT        <count>       <pct>%
  ULVTLL     <count>       <pct>%
  ULVT       <count>       <pct>%

--- Top 15 Violating Paths  (ps, sort_slack.endpts pass_3) ---
  Rank  Path Group              Endpoint                               Startpoint                 Slack (ps)  Lvls  RealLvls  Buf
  ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  #1    <group padded to 22>  <endpoint last-2-levels ~36 chars>    <startpoint ~28 chars>       <slack>     <L>   <RL>      <bfx>
  ...  (15 rows)

--- Root Cause: Top 3 Worst Path Groups ---

  ┌──────────────────────────────────────────────────────────────────────┐
  │  [1]  <GROUP>   WNS: <X> ps   TNS: <Y> ps   Violations: <N>         │
  └──────────────────────────────────────────────────────────────────────┘

  Critical Path
  ──────────────────────────────────────────────────────────────────
  Field          Value
  ──────────────────────────────────────────────────────────────────
  From           <Startpoint last 3 hierarchy levels>
  Clock (from)   <clock name>
  To             <Endpoint last 3 hierarchy levels>
  Clock (to)     <clock name>
  Scenario       <scenario name>
  Logic Levels   <Lvls>  (<RealLvls> real + <bfx> buf/inv)
  Crit Path      <CritPathLen> ps  /  Period <Period> ps  →  <pct>% budget

  High-Fanout Nets  (top 3)
  ──────────────────────────────────────────────────────────────────────────
  Net Name (last 2 levels)               Fanout    Cap (fF)    Incr (ps)
  ──────────────────────────────────────────────────────────────────────────
  <net>                                  <N>       <C>         <incr>
  <net>                                  <N>       <C>         <incr>
  <net>                                  <N>       <C>         <incr>

  High-Cap Nets  (top 3)
  ──────────────────────────────────────────────────────────────────────────
  Net Name (last 2 levels)               Cap (fF)   Fanout    Incr (ps)
  ──────────────────────────────────────────────────────────────────────────
  <net>                                  <C>        <N>       <incr>
  <net>                                  <C>        <N>       <incr>
  <net>                                  <C>        <N>       <incr>

  Largest Cell Delays  (top 5)
  ───────────────────────────────────────────────────────────────────────────────────
  Cell Instance (last 2 levels)          Cell Type (base)       Incr (ps)  Cumul (ps)
  ───────────────────────────────────────────────────────────────────────────────────
  <instance>                             <cell_type>            <incr>     <cumul>
  ...  (5 rows)

  Root Cause Summary
  ────────────────────────────────────────────────────────────────────────────────────
  <2 sentences max citing the numbers above>

  ┌──────────────────────────────────────────────────────────────────────┐
  │  [2]  <GROUP>  ...                                                    │
  └──────────────────────────────────────────────────────────────────────┘
  ... (same structure)

  ┌──────────────────────────────────────────────────────────────────────┐
  │  [3]  <GROUP>  ...                                                    │
  └──────────────────────────────────────────────────────────────────────┘
  ... (same structure)

--- Tuning Recommendations ---

  Step A — Read existing tune files
  ───────────────────────────────────
  Read ALL files under <tile_dir>/tune/FxSynthesize/*.tcl before making any
  recommendation. Do not suggest commands or values already in place.

  File purposes and typical content:
    pre_opt.tcl              — path groups (sources group_paths.tcl), app_options,
                               max_fanout/max_transition, congestion settings,
                               multibit options, set_size_only; also sources
                               r2r_optimization.tcl if it exists
    group_paths.tcl          — path group weights and priorities (base definitions)
    r2r_optimization.tcl     — targeted R2R path groups, effort, boundary opt,
                               physical bounds (create_bound), high-fanout cells
                               *** MAY NOT EXIST — see creation instructions below ***
    post_initial_map.tcl     — set_register_replication for specific reset/critical regs
    post_opt.tcl             — incremental compile loop control
    post_opt_path_margin.tcl — clock gating check margins

  IMPORTANT — sourcing order matters: r2r_optimization.tcl is sourced AFTER
  group_paths.tcl. Same-named group_path definitions in r2r_optimization.tcl
  OVERRIDE the group_paths.tcl definitions. Always check effective final weight.

  FILE CREATION — if r2r_optimization.tcl does not exist:
  ──────────────────────────────────────────────────────────
  1. CREATE the file: tune/FxSynthesize/FxSynthesize.r2r_optimization.tcl
  2. ADD a tunesource line in pre_opt.tcl, immediately after the existing
     group_paths.tcl tunesource line:

     Before (existing in pre_opt.tcl):
       tunesource tune/$TARGET_NAME/$TARGET_NAME.group_paths.tcl

     After (add the new line directly below):
       tunesource tune/$TARGET_NAME/$TARGET_NAME.group_paths.tcl
       tunesource tune/$TARGET_NAME/$TARGET_NAME.r2r_optimization.tcl


  Step B — Derive FC commands from the actual timing data
  ─────────────────────────────────────────────────────────
  Use ONLY the findings from the analysis above (WNS, TNS, NVP per group,
  logic depth, fanout values, cap values, which endpoints/startpoints repeat,
  pass progression, hierarchy names from violating paths). Do NOT use fixed
  ranges. Scale every parameter to what the data shows.

  ── 1. PATH GROUP PRIORITIZATION ──────────────────────────────────────────
  Belongs in: r2r_optimization.tcl (overrides group_paths.tcl if same name)

    group_path -name <group> -weight <W> -critical_range <CR> \
        -from <cells> -to <cells>
      W  : scale with severity — mild → 2–5, moderate → 6–9, severe → 10–15
      CR : set to ~3–5× |WNS| to capture near-boundary paths too; wider for
           TNS-heavy groups with many NVP
    group_path -name <group> ... -to <cells>    — endpoint-only group for fanout targets
    group_path -name <group> ... -from <cells>  — startpoint-only group
    remove_path_group <name>   — clean up stale groups before redefining

  CAUTION: set_boundary_optimization on a broad hierarchy can explode TNS.
  Apply only to the specific sub-hierarchy that contains violating paths
  (e.g. DCQARB/dcq only, not all DCQARB). Always check before/after TNS.
    set_boundary_optimization <targeted_hier_cells> true
    set_dont_touch <same_cells> false   — must pair with boundary opt

  ── 2. PHYSICAL PLACEMENT BOUNDS ──────────────────────────────────────────
  Belongs in: r2r_optimization.tcl
  Use when wire delay dominates (large cap/fanout in path trace) and related
  cell hierarchies are physically spread apart.

    create_bound -name <bound_name> \
        -boundary [list [list <x1> <y1>] [list <x2> <y2>]] \
        -type soft \
        <cell_collection>

  Sizing rules (from actual failures):
  - Always use -type soft (hard bounds cause non-convergence at high density)
  - Target ~200 cells/um2 inside bound; >400 cells/um2 causes 89-hr stalls
  - Size from actual report_area cell area: bound_area = cell_area / 0.60
  - Anchor bounds near communicating SRAMs/macros to minimise port wire delay
  - Derive coordinates from DEF file macro positions, not guesses

  ── 3. MULTIBIT BANKING CONTROL ───────────────────────────────────────────
  Belongs in: pre_opt.tcl
  MB8FF Q delay = 48–51ps vs single FF = 30ps → exclude timing-critical
  pipeline registers from banking to save 15–20ps per launch.

    set_multibit_options -slack_threshold 0
    set_multibit_options -exclude [get_cells -quiet -hier * \
        -filter "full_name=~*<critical_pipeline_hier>*<reg_pattern>*"]
    set_app_option -name compile.flow.enable_rtl_multibit_debanking  -value true
    set_app_option -name compile.flow.enable_physical_multibit_banking -value true
    set_app_option -name compile.flow.enable_rtl_multibit_banking     -value true
    set_app_option -name compile.flow.enable_multibit_debanking       -value true
    set_app_options -name compile.flow.max_multibit_size -value <N>
      N : 4 for data tiles (prevents debanking regression), 6 for cmd tiles
    set_app_options -name multibit.banking.enable_tns_degradation_estimation \
        -value true

  Identify which pipeline registers to exclude: look at FuncTT0p9v path
  trace for the worst group — exclude the launch FF cell type/hierarchy if
  it is a multibit cell (MB4/MB8 in cell name).

  ── 4. FANOUT & NET LOADING ───────────────────────────────────────────────
  Belongs in: r2r_optimization.tcl

    set_max_fanout <N> <specific_cells>
      N : 50–70% of observed fanout on that specific net/cell
      CAUTION: do NOT apply set_max_fanout to broad hierarchies (e.g. all
      DCQARB) — it causes massive buffering overhead and wastes optimizer
      budget. Apply only to the specific high-fanout registers identified
      in the FuncTT0p9v path trace.
    set_max_transition <T> <nets>   — from trans column in path trace
    set_max_capacitance <C> <nets>  — from cap column in path trace

  ── 5. EFFORT & LOGIC RESTRUCTURING ──────────────────────────────────────
  Belongs in: r2r_optimization.tcl or pre_opt.tcl

    set_app_options -name opt.timing.effort -value high
    set_app_options -name opt.area.effort -value high
    set_app_options -name opt.common.buffer_area_effort -value ultra
    set_app_options -name compile.flow.high_effort_timing -value 1
    set_app_options -name opt.common.advanced_logic_restructuring_mode \
        -value area_timing     — TNS spread across many paths (general case)
    set_app_options -name opt.common.advanced_logic_restructuring_mode \
        -value timing          — logic depth is the dominant bottleneck
    set_app_options -name opt.common.advanced_logic_restructuring_wirelength_costing \
        -value high
    set_app_options -name compile.timing.prioritize_tns -value true
    set_app_options -name opt.timing.slack_based_tns_optimization -value true
    set_app_options -name opt.timing.tns_optimization_paths_per_endpoint \
        -value <N>             — derive from NVP: NVP/10, minimum 5
    set_app_options -name compile.flow.enable_auto_feasibility_recovery -value true
                               — safety net when bounds risk congestion overflow

  ── 6. PLACEMENT & ROUTING EFFORT ─────────────────────────────────────────
  Belongs in: r2r_optimization.tcl

    set_app_options -name compile.final_place.effort -value high
    set_app_options -name compile.initial_place.buffering_aware_placement_effort \
        -value high
    set_app_options -name place_opt.final_place.effort -value high
    set_app_options -name place_opt.place.congestion_effort -value high
    set_app_options -name clock_opt.place.congestion_effort -value high
    set_app_options -name route.common.rc_driven_setup_effort_level -value high
    set_app_options -name route.global.effort_level -value high
    set_app_options -name route.detail.optimize_wire_via_effort_level -value high
    set_app_options -name ccd.hold_control_effort -value high
    set_app_options -name place.coarse.max_density -value <D>
      D : reduce from current by 0.05–0.10 when wire delay dominates
    set_congestion_optimization [get_designs] TRUE
    set_congestion_optimization [get_cells -hier * \
        -filter "is_hierarchical == true"] true

  ── 7. RETIMING (when logic depth is the bottleneck) ──────────────────────
  Belongs in: r2r_optimization.tcl

    set_app_options -name compile.register_retiming.mode -value full
    set_app_options -name compile.retiming.optimization_priority -value setup_timing
    set_app_options -name compile.retiming.enable_forward_retiming -value true
    set_app_options -name compile.retiming.enable_backward_retiming -value true
      NOTE: backward retiming can cause instability — validate pass-over-pass
    set_app_options -name compile.seqmap.register_replication_placement_effort \
        -value high

  ── 8. REGISTER REPLICATION ───────────────────────────────────────────────
  Belongs in: post_initial_map.tcl (for reset/specific regs) or r2r_optimization.tcl

    set_register_replication -num_copies <N> \
        [get_cells -hier * -filter "full_name=~*<hier_pattern>*"]
      N : replicate so each copy drives ≤ actual_fanout / N
    set_app_options -name compile.timing.buffer_replication -value true

  ── 9. CLOCK TRANSITION ───────────────────────────────────────────────────
  Belongs in: pre_opt.tcl

    set_clock_transition <T> [get_clocks -quiet *UCLK*]
      T : derive from clock transition values seen in FuncTT0p9v path trace

  ── 11. CONDITIONAL ARCS ──────────────────────────────────────────────────
    set_app_options -name time.enable_cond_default_arcs -value true
      — use when timing appears pessimistic vs expected path length

  ── KNOWN PITFALLS ────────────────────────────────────────────────────────
  Always check these before making recommendations:

  | Pitfall                                    | Effect                        | Safe Practice                          |
  |────────────────────────────────────────────|───────────────────────────────|────────────────────────────────────────|
  | set_boundary_optimization on broad hier    | TNS explosion (+57K ps)       | Target /submodule only, not whole block|
  | set_max_fanout on large hierarchy          | Buffering overhead, wasted opt| Apply to specific named registers only |
  | create_bound too tight (>400 cells/um2)    | 89-hr non-convergence         | size to ~200 cells/um2, always soft    |
  | Same group name in r2r_optimization.tcl    | Silently overrides weight     | Always verify final effective weight   |
  | set_boundary_optimization on cross-domain  | CDC path regression           | Never apply across clock domain        |
  | Backward retiming enabled                  | Pass instability              | Validate carefully before keeping      |

  Step C — RTL Fix Analysis
  ──────────────────────────
  RTL source paths are listed in <tile_dir>/data/GetRTL.source.vf
  Each line that ends in .v or .sv is a full absolute path to the original
  published RTL file. Do NOT use data/GetRTL/ copies — use the source paths
  directly from GetRTL.source.vf.

  How to find the right RTL file:
  1. Read <tile_dir>/data/GetRTL.source.vf
  2. Extract all lines ending in .v or .sv (these are full absolute paths)
  3. Match the endpoint hierarchy module name to a file — the naming convention
     is rtl_umc<modulename>.v:
       ARB/DCQARB  →  grep for "rtl_umcdcqarb.v" in the vf → use that full path
       ARB/PGT     →  grep for "rtl_umcpgt.v"
       FEI         →  grep for "rtl_umcfei.v"
       ARB/TIM     →  grep for "rtl_umc*tim*.v" (use partial match if needed)
  4. Read the file at its original source path

  For each of the top 3 worst violating path groups:
  1. Take the endpoint hierarchy from the sort_slack.endpts table
  2. Find the RTL file path from GetRTL.source.vf using the naming convention
  3. Read the original RTL file — search for the signal name of the violating endpoint
  4. Analyse the driving logic: count levels, check fanout, identify the
     combinational cone feeding that register
  5. Apply this fix decision logic:

  LOGIC DEPTH > 25 levels  →  Pipeline Insertion
    The combinational path is too long for one clock cycle. Suggest inserting
    a pipeline register stage in RTL to break it into two shorter paths.
    Show the exact always block and signal name where the register should be added.
    Note: pipeline insertion changes latency — flag this to the designer.

  SAME REGISTER IS STARTPOINT FOR ≥3 PATHS  →  Register Duplication
    The register fans out to too many endpoints. Suggest explicitly duplicating
    it in RTL so each copy drives a subset of the fanout.
    Show the exact reg declaration and assignment to duplicate.

  CLOCK GATE ENABLE HAS DEEP LOGIC (>15 levels)  →  Register the Enable
    The enable signal arrives too late at the clock gate. Suggest registering
    the enable one cycle earlier in RTL to give it a full clock budget.
    Show the enable signal name and the always block to add the pipeline flop.

  MULTIBIT CELL ON CRITICAL LAUNCH  →  Exclude from Banking
    If the launch FF in the path trace is an MB4/MB8 cell, suggest adding
    set_multibit_options -exclude in pre_opt.tcl for that register hierarchy
    (this is a synthesis fix, not an RTL change, but flag it here).

  Output format for RTL fixes:

  --- RTL Fix Suggestions ---

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  [1]  <GROUP>                                                             │
  └──────────────────────────────────────────────────────────────────────────┘

  Finding
  ──────────────────────────────────────────────────────
  Signal      : <endpoint signal name>
  Module      : <module name in RTL>
  Logic Depth : <N> levels  →  <fix type>
  Fanout      : <N> (if relevant)

  Suggested RTL Fix
  ──────────────────────────────────────────────────────────────────────────────
  File : <full absolute path from GetRTL.source.vf>
         e.g. /proj/rtg_oss_er_feint1/abinbaba/umc_godavari_Jun1175758/out/
              linux_4.18.0_64.VCS/umc17_x/config/umc_top_drop2cad/pub/sim/
              publish/tiles/tile/umc_top/publish_rtl/rtl_umcdcqarb.v
  Type : <Pipeline Insertion | Register Duplication | Enable Pipelining>
  Risk : <Latency change: +1 cycle | No functional change>

  // Before  (line ~<N> in the file above):
  <existing RTL snippet — exact lines from the original source file>

  // After (suggested change):
  <modified RTL snippet with the fix applied>

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  [2]  ...                                                                 │
  └──────────────────────────────────────────────────────────────────────────┘
  ... (same structure for groups 2 and 3)

  If no RTL file found for a group: single row — `| <group> | RTL file not found |`
  If the fix is not clear from the RTL (e.g. glue logic, external IP): note it
  and skip rather than guess.

  Step D — Output format (FC tuning)
  ────────────────────────────────────

  Recommendations Summary
  ──────────────────────────────────────────────────────────────────────────────────────
  #   Finding (from analysis data)                FC Command / Knob        Tune File
  ──────────────────────────────────────────────────────────────────────────────────────
  1   <specific finding with actual numbers>      <command + derived value>  <file.tcl>
  2   ...
  ──────────────────────────────────────────────────────────────────────────────────────

  Proposed TCL Changes
  ──────────────────────────────────────────────────────────────────────────
  File: tune/FxSynthesize/<filename>.tcl
  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  # <why: what finding this addresses, with the actual number>
  + <exact TCL line with derived value>

  File: tune/FxSynthesize/<filename>.tcl
  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
  - <existing line>
  + <replacement line with updated value>
  ──────────────────────────────────────────────────────────────────────────
  Note: + = add, - old / + new = replace. Verify against existing file before applying.
```

---

## MODE: COMPARISON

Compare two FxSynthesize runs side-by-side. Run A is the **baseline**,
Run B is the **new run**. Delta = B − A. Positive delta on WNS/TNS is improvement.

### Files to read (for EACH run — A and B)

Same as MODE: SIMPLE:
- `FxSynthesize.dat` — CostGroups, design totals, design stats
- All `FxSynthesize.pass_*.proc_qor.rpt.gz` (grepped) — per-pass WNS/TNS/NVP/Levels

If `--analysis` is also specified, additionally read for each run:
- `report_timing.pass_3.rpt.sum.sort_slack.endpts.gz` — top violating endpoints
- `FuncTT0p9v_<GROUP>_max.rpt.gz` — worst path detail for top 3 groups

### Comparison Output Layout

```
========================================================================
  FxSynthesize Timing Comparison
  Run A (baseline) : <run_dir_A_name>
  Run B (new)      : <run_dir_B_name>
========================================================================

--- Design Statistics Delta ---
  Metric            Run A          Run B          Delta
  ─────────────────────────────────────────────────────
  Flops             <val>          <val>          <+/- N>
  Total Cells       <val>          <val>          <+/- N>
  Cell Area         <val>          <val>          <+/- val>
  Wire Length       <val>          <val>          <+/- val>
  FGCG Ratio        <val>          <val>          <+/- pct>
  MBB Banking       <val>          <val>          <+/- pct>

--- Setup Timing Comparison by Path Group  (ps) ---
  Path Group           Run A WNS   Run B WNS   ΔWNS    Run A TNS    Run B TNS    ΔTNS   Run A NVP  Run B NVP  ΔNVP  Trend
  ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  <group padded 22>    <val>       <val>       <+/->   <val>        <val>        <+/->  <N>        <N>        <+/-> ↑/↓/─
  ...
  (Trend: ↑ = improved WNS, ↓ = degraded WNS, ─ = unchanged within 5ps)

--- Design Timing Totals Comparison  (ps) ---
  Scope     Run A WNS   Run B WNS   ΔWNS    Run A TNS    Run B TNS    ΔTNS    ΔNVP
  ──────────────────────────────────────────────────────────────────────────────────
  Core      <val>       <val>       <+/->   <val>        <val>        <+/->   <+/->
  IO        <val>       <val>       <+/->   <val>        <val>        <+/->   <+/->
  DESIGN    <val>       <val>       <+/->   <val>        <val>        <+/->   <+/->

--- Per-Pass Progression Comparison  (ps) ---

  Path Group            Lvls   Pass   Run A WNS    Run B WNS    ΔWNS    Run A NVP  Run B NVP
  ────────────────────────────────────────────────────────────────────────────────────────────
  <group padded 22>     <L>     P1    <val>        <val>        <+/->   <N>        <N>
                                P2    <val>        <val>        <+/->   <N>        <N>
                                P3    <val>        <val>        <+/->   <N>        <N>
  ────────────────────────────────────────────────────────────────────────────────────────────
  <next group>          <L>     P1    ...
  ...

--- VT Mix Comparison ---
  VT Type    Run A Count   Run A %    Run B Count   Run B %    Δ%
  ──────────────────────────────────────────────────────────────────
  LVTLL      <val>         <pct>%     <val>         <pct>%     <+/->
  LVT        <val>         <pct>%     <val>         <pct>%     <+/->
  ULVTLL     <val>         <pct>%     <val>         <pct>%     <+/->
  ULVT       <val>         <pct>%     <val>         <pct>%     <+/->

--- Overall Verdict ---
  Metric        Result     Detail
  ──────────────────────────────────────────────────────────────────
  Design WNS    ↑ / ↓ / ─  <Run A val> → <Run B val>  (Δ <val> ps)
  Design TNS    ↑ / ↓ / ─  <Run A val> → <Run B val>  (Δ <val> ps)
  Design NVP    ↑ / ↓ / ─  <Run A val> → <Run B val>  (Δ <val>)
  Best group    <group>     improved by <val> ps WNS
  Worst group   <group>     degraded by <val> ps WNS
```

If `--analysis` is also specified, append these additional sections:

```
--- Top Violating Paths — New in Run B (regressions) ---
  Paths present in Run B sort_slack.endpts but NOT in Run A (by endpoint name)
  Rank  Group              Endpoint                        Slack B (ps)  Lvls
  ────────────────────────────────────────────────────────────────────────────
  #N    <group>            <endpoint>                      <slack>       <L>
  ...  (up to 10 new regressions)

--- Top Violating Paths — Fixed in Run B (improvements) ---
  Paths present in Run A sort_slack.endpts but NOT in Run B
  Rank  Group              Endpoint                        Slack A (ps)  Lvls
  ────────────────────────────────────────────────────────────────────────────
  #N    <group>            <endpoint>                      <slack>       <L>
  ...  (up to 10 fixed paths)

--- Root Cause of Changes ---

  For each group where |ΔWNS| > 10 ps (degraded OR improved), analyse why:
  - Compare logic levels A vs B (deeper logic = more restructuring happened)
  - Compare top violating endpoint/startpoint between runs
  - Note any path groups that gained/lost weight (check tune files in both runs)
  - For degradations: identify the new worst path and what drove the change

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  <GROUP>  │  <↑ improved / ↓ degraded>  │  ΔWNS: <val> ps               │
  └──────────────────────────────────────────────────────────────────────────┘

  Change Summary
  ────────────────────────────────────────────────────────────────
  Field           Run A                    Run B
  ────────────────────────────────────────────────────────────────
  WNS             <val> ps                 <val> ps
  TNS             <val> ps                 <val> ps
  NVP             <N>                      <N>
  Logic Levels    <N>                      <N>
  Worst Endpoint  <endpoint A>             <endpoint B>
  Worst Start     <startpoint A>           <startpoint B>

  Why:
  <2–3 sentence explanation citing specific numbers — e.g. "SYN_R2R degraded
   -8ps because logic levels increased from 30 to 33 between runs. The worst
   endpoint shifted from ARB/DCQARB/ArbSafeRegPh to ARB/DCQARB/dep0/dep_vld,
   suggesting a new critical path opened after restructuring.">
  ────────────────────────────────────────────────────────────────────────────
  ... (one block per group where |ΔWNS| > 10 ps)

  Then apply the full Tuning Recommendations and RTL Fix Analysis sections
  from MODE: ANALYSIS (Steps B, C, D) using Run B as the subject run.
  The context from the comparison (which groups degraded, which improved,
  which paths are new) should inform the recommendations — prioritise fixes
  for groups that degraded in Run B or still have the worst violations.
```

---

## Formatting Rules (all modes)

- **Every data section is a fixed-width table** — headers + separator line + data rows. No bullet lists, no indented prose.
- WNS: always show sign (`+` or `-`), 3 decimal places
- Status column: `OK` when Violations = 0, `!!` otherwise
- Endpoint/startpoint: keep last 2 hierarchy levels + signal name; truncate middle with `…`
- Net names: last 2 hierarchy levels; cell types: strip process/library suffix
- `status_xls.rpt` values in **ns**; all other timing in **ps**
- Numeric columns right-aligned; name columns left-aligned
- Root Cause Summary: only free-text field, 2 sentences max
- If FuncTT0p9v file not found for a group: single-row table `| <group> | file not found |`
- If both umccmd and umcdat requested: print both full blocks separated by a blank line
