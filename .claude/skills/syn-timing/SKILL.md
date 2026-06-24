# Synthesis Timing Report Skill

Report FxSynthesize timing and QoR for UMC project tiles.
Two modes: **simple** (quick timing snapshot) and **analysis** (full root-cause).

## Trigger
`/syn-timing`

## Usage
```
/syn-timing                                            # simple mode, latest umccmd + umcdat
/syn-timing --simple                                   # explicit simple mode
/syn-timing --analysis                                 # full analysis mode
/syn-timing umccmd                                     # simple, latest umccmd only
/syn-timing umcdat --analysis                          # analysis, latest umcdat only
/syn-timing /path/to/umccmd_Jun01_new                  # simple, specific run dir
/syn-timing /path/to/umccmd_Jun01_new --analysis
```

Default mode when no flag given: **simple**.

## Tiles Base Directory
The user must supply the tiles directory path, or it can be inferred from the
tile run path they provide. There is no hardcoded default — accept whatever
path the user gives (e.g. `/proj/<project>/main/pd/tiles`).

---

## Execution Model

**Do NOT perform the analysis in the main session context.**

1. Resolve `TILE_DIR` inline in the main session (a quick `ls` + mtime check — not analysis).
2. Determine `MODE` from user args (`simple` or `analysis`).
3. Spawn a `general-purpose` subagent with `TILE_DIR` and `MODE` in the prompt.
4. Wait for the agent to complete. Print its output verbatim — no post-processing.

```python
Agent(
  description="FxSynthesize timing report — <MODE> — <tile> <run_dir_name>",
  subagent_type="general-purpose",
  prompt="""
You are a timing analysis agent for UMC synthesis.

TILE_DIR = <resolved absolute path>
MODE     = <simple | analysis>

Follow the instructions for MODE exactly. Return only the formatted report —
no preamble, no explanation.

=== INSTRUCTIONS ===
<paste the full MODE-SIMPLE and MODE-ANALYSIS sections below>
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

  File purposes:
    pre_setup.tcl            — app_options, host settings, compile switches
    pre_opt.tcl              — register replication, hierarchy flattening
    post_initial_map.tcl     — post-map replication, placement bounds
    post_opt.tcl             — post-opt fixes
    post_opt_path_margin.tcl — clock gating check margins
    group_paths.tcl          — path group weights and priorities

  Step B — Derive FC commands from the actual timing data
  ─────────────────────────────────────────────────────────
  Use ONLY the findings from the analysis above (WNS, TNS, NVP per group,
  logic depth, fanout values, cap values, which endpoints/startpoints are
  repeating, pass progression). Do NOT use fixed ranges or hardcoded values.
  Scale every parameter to what the data shows.

  Derive commands from this FC toolkit (not exhaustive — use judgement):

  PATH GROUP PRIORITIZATION
    group_path -name <group> -weight <W> -critical_range <CR> ...
      W  : scale with severity — mild violation → 2, moderate → 5, severe → 10+
      CR : scale with |WNS| — set to ~3–5× |WNS| so paths near slack boundary
           are also captured; wider for TNS-heavy groups
    remove_path_group <name>    — clean up before redefining
    set_boundary_optimization <hier_cells> all  — expose cross-boundary paths

  FANOUT & NET LOADING
    set_max_fanout <N> <cells>  — derive N from actual fanout found; set to
                                  50–70% of current fanout to force buffering
    set_max_transition <T> <nets>  — derive T from trans values in path trace
    set_max_capacitance <C> <nets> — derive C from cap values in path trace
    set_dont_touch <cells> false   — unlock protected cells blocking opt

  LOGIC RESTRUCTURING & EFFORT
    set_app_options -name opt.common.advanced_logic_restructuring_mode \
        -value timing              — when logic depth is dominant cause
    set_app_options -name opt.common.advanced_logic_restructuring_mode \
        -value area_timing         — when TNS is spread across many paths
    set_app_options -name compile.flow.enable_restructure -value true
    set_app_options -name compile.flow.allow_duplication -value true
    set_app_options -name opt.timing.effort -value ultra
    set_app_options -name compile.timing.area_recovery -value false
                                   — disable when timing must not be traded
    set_app_options -name compile.timing.prioritize_tns -value true
                                   — when NVP is high across many paths
    set_app_options -name opt.timing.slack_based_tns_optimization -value true
    set_app_options -name opt.timing.tns_optimization_paths_per_endpoint \
        -value <N>                 — derive N from NVP (e.g. NVP/10, min 5)

  RETIMING (when logic depth is the bottleneck)
    set_app_options -name compile.register_retiming.mode -value full
    set_app_options -name compile.retiming.optimization_priority \
        -value setup_timing
    set_app_options -name compile.retiming.enable_forward_retiming -value true
    set_app_options -name compile.retiming.enable_backward_retiming -value true
    set_app_options -name compile.seqmap.register_replication_placement_effort \
        -value high

  REGISTER REPLICATION (when same startpoint drives many endpoints)
    set_register_replication -num_copies <N> [get_cells -hier <pattern>]
      N : scale from actual fanout — replicate so each copy drives ≤ fanout/N
    set_app_options -name compile.timing.buffer_replication -value true

  BUFFERING & SIZING
    set_max_fanout <N> [current_design]
    set_app_options -name opt.common.max_fanout -value <N>
    size_cell -all_instances <cell_collection>
    set_size_only <cells>   — when logic change is not desired, only upsizing

  PLACEMENT DENSITY (when congestion is contributing to wire delay)
    set_app_options -name place.coarse.max_density -value <D>
      D : reduce from current value by 0.05–0.10 based on how much wire delay
          dominates (large cap values in path trace = congested placement)
    set_app_options -name compile.initial_place.buffering_aware_placement_effort \
        -value ultra

  CONDITIONAL ARCS (when timing appears pessimistic)
    set_app_options -name time.enable_cond_default_arcs -value true

  Always pair targeted set_app_options with reset_app_options after the
  relevant compile phase to avoid polluting subsequent optimization stages.

  Step C — Output format
  ───────────────────────

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

## Formatting Rules (both modes)

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
