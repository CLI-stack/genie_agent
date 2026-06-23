# Godavari Synthesis Timing Report Skill

Report FxSynthesize timing and QoR for Godavari tiles (umccmd / umcdat).
Two modes: **simple** (quick timing snapshot) and **analysis** (full root-cause).

## Trigger
`/godavari-syn-timing`

## Usage
```
/godavari-syn-timing                                   # simple mode, latest umccmd + umcdat
/godavari-syn-timing --simple                          # explicit simple mode
/godavari-syn-timing --analysis                        # full analysis mode
/godavari-syn-timing umccmd                            # simple, latest umccmd only
/godavari-syn-timing umcdat --analysis                 # analysis, latest umcdat only
/godavari-syn-timing /path/to/umccmd_Jun01_new         # simple, specific run dir
/godavari-syn-timing /path/to/umccmd_Jun01_new --analysis
```

Default mode when no flag given: **simple**.

## Tiles Base Directory
`/proj/rtg_oss_er_feint2/abinbaba/GODAVARI_SYN/main/pd/tiles`

---

## Execution Model

**Do NOT perform the analysis in the main session context.**

1. Resolve `TILE_DIR` inline in the main session (a quick `ls` + mtime check — not analysis).
2. Determine `MODE` from user args (`simple` or `analysis`).
3. Spawn a `general-purpose` subagent with `TILE_DIR` and `MODE` in the prompt.
4. Wait for the agent to complete. Print its output verbatim — no post-processing.

```python
Agent(
  description="Godavari FxSynthesize timing — <MODE> — <tile> <run_dir_name>",
  subagent_type="general-purpose",
  prompt="""
You are a timing analysis agent for Godavari synthesis.

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
whichever exist) — for each pass and each path group extract:
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
  Path Group                Pass    WNS (ps)    TNS (ps)   Violations   Levels
  ─────────────────────────────────────────────────────────────────────────────
  <name padded to 24>       1       <val>       <val>      <val>        <val>
  <name padded to 24>       2       <val>       <val>      <val>        <val>
  <name padded to 24>       3       <val>       <val>      <val>        <val>
  ...  (one row per pass per group; repeat for every group found)
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

**B. `FxSynthesize.pass_3.proc_qor.rpt.gz`** (or highest available pass) — for each group:
- `Timing Path Group`, `Levels of Logic`, `Critical Path Length` (ps),
  `Critical Path Slack` (ps), `Critical Path Clk Period` (ps),
  `Total Negative Slack` (ps), `No. of Violating Paths`

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
  Path Group                Pass    WNS (ps)    TNS (ps)   Violations   Levels
  ─────────────────────────────────────────────────────────────────────────────
  <name padded to 24>       1       <val>       <val>      <val>        <val>
  <name padded to 24>       2       <val>       <val>      <val>        <val>
  <name padded to 24>       3       <val>       <val>      <val>        <val>
  ...

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
