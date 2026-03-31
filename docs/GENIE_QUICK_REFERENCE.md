# Genie CLI Quick Reference

## Usage

**Just ask Claude naturally:**
```
"Run CDC/RDC at /proj/xxx"
"Monitor supra run at /proj/xxx for target FxSynthesize"
```

**Or use CLI directly:**
```bash
python3 script/genie_cli.py --list                        # List commands
python3 script/genie_cli.py -i "run cdc_rdc at /xxx"      # Dry run
python3 script/genie_cli.py -i "run cdc_rdc at /xxx" -e   # Execute
python3 script/genie_cli.py -i "run cdc_rdc at /xxx" -e -m  # Execute + email
python3 script/genie_cli.py --status <tag>                # Check status
python3 script/genie_cli.py --kill <tag>                  # Kill task
```

---

## CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--instruction` | `-i` | Instruction to parse/execute |
| `--execute` | `-e` | Execute (default is dry-run) |
| `--email` | `-m` | Send email on completion |
| `--list` | `-l` | List available commands |
| `--status` | `-s` | Check task status by tag |
| `--kill` | `-k` | Kill running task by tag |

---

## Common Commands

### Static Checks
```
run cdc_rdc at <dir>
run lint at <dir>
run spg_dft at <dir>
run build_rtl at <dir>
run full_static_check at <dir>
summarize static check run at <dir>
```

### TileBuilder
```
monitor supra run at <dir> for target <target>
start supra regression <tile> at <dir>
run supra regression for target <target> at <dir>
branch from <dir>
rerun <target> at <dir>
stop run at <dir>
report timing and area for <dir>
report utilization at <dir>
update status for supra run at <dir>
list tilebuilder directories at <dir>
remove TB dir at <dir>
```

### Params/Tune
```
update params at <dir>
add params at <dir>
update params to params center
update params from params center
update tune to tune center
update tune from tune center
add command to <tune>
```

### Waivers/Updates
```
add cdc_rdc waiver at <dir>
add cdc_rdc constraint at <dir>
update cdc_rdc waiver at <dir>
update cdc_rdc config at <dir>
update cdc_rdc version at <dir>
add lint waiver at <dir>
update lint waiver at <dir>
update spg_dft parameters at <dir>
add spyglass dft parameters at <dir>
```

### P4
```
sync up new tree at <dir>
check changelist number for <dir>
submit files at <dir>
```

---

## Output Files

| Location | Content |
|----------|---------|
| `data/<tag>_spec` | Results summary |
| `data/<tag>_pid` | Process ID (background tasks) |
| `data/<tag>_email` | Email flag file |
| `runs/<tag>.log` | Execution log |
| `runs/<tag>.csh` | Run script |

---

## Task Management

```bash
# Check status
python3 script/genie_cli.py --status 20260210175401

# Kill task
python3 script/genie_cli.py --kill 20260210175401

# View results
cat data/20260210175401_spec
```

---

## Examples

```
run cdc_rdc at /proj/rtg_oss_er_feint1/abinbaba/umc_rosenhorn_Feb9

monitor supra run at /proj/rtg_oss_er_feint2/xxx/umccmd_Jan26 for target FxSynthesize

summarize static check run for umc9_3 at /proj/xxx/tree_dir

report timing and area for /proj/xxx/tile_dir

report utilization at /proj/xxx/tile_dir
```

---

**Version:** 1.1
**Last Updated:** 2026-02-10
