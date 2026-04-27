# ECO Applier — PostEco Netlist Editor Specialist

**MANDATORY FIRST ACTION:** Read `config/eco_agents/CRITICAL_RULES.md` in full before doing anything else.

---

## 1. Overview

**Role:** Read the PreEco study JSON, locate cells in PostEco netlists, verify old nets on expected pins, apply net substitutions, and auto-insert new cells for `new_logic` changes.

**Inputs:** `REF_DIR`, `TAG`, `BASE_DIR`, `JIRA`, `ROUND` (1 = initial, 2+ = surgical patch)

**Outputs:**
- Edited `<REF_DIR>/data/PostEco/{Synthesize,PrePlace,Route}.v.gz`
- `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`

**Working directory:** `<BASE_DIR>` (parent of `runs/`). No hardcoded signal/port/module names anywhere — all `<placeholder>` style.

---

## 2. Pre-Flight Checks

Run ONCE before decompressing any stage. Defends against concurrent agents corrupting PostEco between rounds.

### Round 1 — PostEco must match PreEco

```bash
for stage in Synthesize PrePlace Route; do
    preeco_md5=$(md5sum <REF_DIR>/data/PreEco/${stage}.v.gz | awk '{print $1}')
    posteco_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
    if [ "$preeco_md5" != "$posteco_md5" ]; then
        cp <REF_DIR>/data/PreEco/${stage}.v.gz <REF_DIR>/data/PostEco/${stage}.v.gz
        restored_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
        [ "$restored_md5" != "$preeco_md5" ] && echo "ERROR: Restore failed for ${stage}. ABORT." && exit 1
        PREFLIGHT_RESTORED_STAGES+=("$stage")
    fi
done
```

### Round 2+ — PostEco must match ROUND_ORCHESTRATOR backup

```bash
for stage in Synthesize PrePlace Route; do
    bak=<REF_DIR>/data/PostEco/${stage}.v.gz.bak_<TAG>_round<ROUND>
    posteco_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
    backup_md5=$(md5sum ${bak} | awk '{print $1}')
    if [ "$posteco_md5" != "$backup_md5" ]; then
        cp ${bak} <REF_DIR>/data/PostEco/${stage}.v.gz
        restored_md5=$(md5sum <REF_DIR>/data/PostEco/${stage}.v.gz | awk '{print $1}')
        [ "$restored_md5" != "$backup_md5" ] && echo "ERROR: Restore failed for ${stage}. ABORT." && exit 1
        PREFLIGHT_RESTORED_STAGES+=("$stage")
    fi
done
```

After either loop: set `pre_flight_restore: true` and `pre_flight_restored_stages: [...]` in the applied JSON if any stage was restored. MD5 is used (not grep) because it catches ALL changes from any source — a concurrent agent can corrupt a port list without touching any eco instance.

---

## 3. Global Setup

### 3a — Mode Determination

**Round 1 (Full Apply):** All study JSON changes processed from scratch. PostEco = copy of PreEco (verified by pre-flight). Create backup before editing: `<Stage>.v.gz.bak_<TAG>_round1`.

**Round 2+ (Surgical Patch):** PostEco contains previous rounds' correct changes — do NOT restore from any backup. ROUND_ORCHESTRATOR already backed up as `bak_<TAG>_round<ROUND>` — skip eco_applier's backup step. Read `eco_fm_analysis_round<ROUND-1>.json` → `revised_changes` list. For each study JSON entry:
- NOT in `revised_changes` AND `force_reapply: false` → mark ALREADY_APPLIED (skip)
- In `revised_changes` OR `force_reapply: true` → UNDO then RE-APPLY

### 3b — Global Seq Counter (build ONCE, shared across all 3 stages)

```python
seq_table = {}   # {change_id: eco_instance_name}
seq_counter = 1
for entry in all_confirmed_new_logic_entries:
    change_id = entry["change_id"]
    if change_id not in seq_table:
        seq_table[change_id] = f"eco_{JIRA}_{seq_counter:03d}"
        seq_counter += 1
# NEVER re-derive seq per stage — breaks FM's stage-to-stage matching
```

Instance naming: DFF → use `<target_register>_reg` (instance) / `<target_register>` (Q net) so FM auto-matches without `set_user_match`. Gates → `eco_<jira>_<seq>` (instance) / `n_eco_<jira>_<seq>` (output). D-input chain gates → `eco_<jira>_d<seq>`; condition gates → `eco_<jira>_c<seq>`.

### 3c — UNDO Logic (Surgical Patch Mode Only)

**Prior-round SKIPPED entries are NEVER ALREADY_APPLIED:** In Surgical Patch mode, before marking any entry ALREADY_APPLIED, read its status from `data/<TAG>_eco_applied_round<ROUND-1>.json`. If `prior_status == "SKIPPED"` → the change was never applied → mark as SKIPPED (carry forward the prior reason). Only run the standard ALREADY_APPLIED checks when `prior_status` was APPLIED, INSERTED, or ALREADY_APPLIED.

Before re-applying a `force_reapply: true` entry: check prior status in `data/<TAG>_eco_applied_round<ROUND-1>.json`. If prior status = `SKIPPED` → skip UNDO entirely, go straight to RE-APPLY. If prior status = `APPLIED`/`INSERTED` → verify element exists before removing; if not found → log and skip UNDO, proceed to RE-APPLY.

| change_type | Undo action |
|-------------|-------------|
| `rewire` | Find `.<pin>(<new_net>)` in cell block → replace with `.<pin>(<old_net>)` |
| `new_logic_gate` / `new_logic_dff` / `new_logic` | Find `<cell_type> <instance_name> (...)` block → remove including trailing `;` |
| `port_declaration` / `port_promotion` | Remove duplicate port or incorrect declaration line |
| `port_connection` | Revert `.<port>(<new_net>)` back to prior form |
| `wire_declaration` | Remove the explicit `wire <net_name>;` that caused FM-599 |
| `port_connection_duplicate` | Remove the duplicate `.<pin>(<net>)` line from instance block |

**UNDO step-by-step — per change_type:**

**rewire UNDO:**
1. Find cell block: grep `<cell_name>` in module buffer — not found → skip UNDO.
2. Within cell block: find `.<pin>(<new_net>)` — not found → new_net already gone → skip UNDO.
3. Replace `.<pin>(<new_net>)` with `.<pin>(<old_net>)` (scoped to cell block).
4. Verify: grep `<new_net>` in cell block = 0; grep `<old_net>` on pin = 1. If not → UNDO_FAILED.

**new_logic_gate / new_logic_dff UNDO:**
1. Find instance: grep `<cell_type>\s\+<instance_name>\s*(` in module buffer. Not found → already removed → skip UNDO.
2. Find the full instance block (from instance start to `") ;"` or `");"`).
3. Remove the entire block including the ECO comment line above it (if present: `"// ECO.*"`).
4. Verify: grep `<instance_name>` in module buffer = 0. If > 0 → UNDO_FAILED.
5. Verify output net `n_eco_<jira>_<seq>` has no remaining driver in module buffer (other gates may still reference it as input — that is OK; having no driver is OK for UNDO).

**port_declaration UNDO:**
1. Remove signal from port list: re-run depth tracking, remove `, <signal_name>` from close line.
2. Remove direction declaration: find `  (input|output)\s+<signal_name>\s*;` and remove line.
3. Verify: grep `<signal_name>` in port list range = 0; grep `input|output <signal_name>` = 0.

**port_connection UNDO:**
1. Find instance block of `<submodule_instance>`.
2. Find `.<port_name>(<net_name>)` in instance block — not found → skip UNDO.
3. Remove the line containing `.<port_name>(<net_name>)`.
4. Verify: grep `<port_name>` in instance block = 0.

---

## 4. Pass Order

Process ALL changes for a stage in 4 passes. Never mix order.

| Pass | change_type(s) | Method |
|------|----------------|--------|
| 1 | `new_logic_gate`, `new_logic_dff`, `new_logic` | **Perl script** — batch insert before endmodule via streaming pipe |
| 2 | `port_declaration`, `port_promotion` | Agent text op on decompressed temp file |
| 3 | `port_connection` | Agent text op on decompressed temp file |
| 4 | `rewire` | Agent text op on decompressed temp file |

**Pass 1 runs first via Perl pipe (no decompress needed for gates).** Passes 2–4 decompress once → apply all text ops → recompress once.

**Per-stage setup (S0–S3):**
- **S0 — Netlist type:** `grep -c "^module "` — count > 1 = hierarchical (port_declaration and port_connection mandatory); count = 1 = flat.
- **S1 — Confirmed entries:** If none with `"confirmed": true` → write all SKIPPED, skip to next stage.
- **S2 — Backup:** Round 1: `cp <Stage>.v.gz <Stage>.v.gz.bak_<TAG>_round1`. Round 2+: skip (ROUND_ORCHESTRATOR already backed up).
- **S3 — ALREADY_APPLIED pre-check:** Before building the Perl spec, grep the compressed stage file for each `new_logic` instance_name. If found → ALREADY_APPLIED (skip from Perl spec). For Round 1 entries flagged ALREADY_APPLIED → add `"warning": "UNEXPECTED in Round 1 — concurrent agent suspected"`.

## Phase A — Collect Gate Spec (Agent Reasoning — no file touch)

Before generating the Perl script, decide the status of every `new_logic_gate`, `new_logic_dff`, and `remove_wire_decl` entry for this stage:

```python
perl_spec = {}   # {module_name: {wire_decls: [], wire_removes: [], gates: []}}

for entry in confirmed_new_logic_entries:
    mod = entry["module_name"]

    # ALREADY_APPLIED check (grep compressed file — fast, no decompress)
    inst = entry["instance_name"]
    count = int(bash(f'zcat PostEco/{stage}.v.gz | grep -cw "{inst}"'))
    if count > 0 and not entry.get("force_reapply"):
        record(status="ALREADY_APPLIED", reason=f"instance '{inst}' found (grep count={count})")
        continue

    # SKIPPED checks
    stage_ports = entry.get("port_connections_per_stage", {}).get(stage) or entry["port_connections"]
    for pin, net in stage_ports.items():
        if pin == output_pin_key: continue          # output net — checked separately
        if net in ("1'b0", "1'b1"): continue        # constants always valid
        if net.startswith("NEEDS_NAMED_WIRE:"): continue
        net_count = int(bash(f'zcat PostEco/{stage}.v.gz | grep -cw "{net}"'))
        if net_count == 0:
            record(status="SKIPPED", reason=f"input net '{net}' absent in {stage}")
            goto next_entry

    # wire_decls: OUTPUT net only — AND only if not referenced by any Pass 4 rewire in same module
    # If the output net is used as new_net in a rewire entry for the same module, that rewire
    # reference appears BEFORE the Perl insertion point (endmodule) and creates an implicit wire
    # declaration. Adding an explicit wire decl would then be a DUPLICATE → SVR-9.
    output_net = stage_ports[output_pin_key]
    rewire_nets_in_module = {e.get("new_net") for e in all_entries
                             if e.get("change_type") == "rewire"
                             and e.get("module_name") == mod}
    if entry.get("needs_explicit_wire_decl") and output_net not in rewire_nets_in_module:
        perl_spec[mod]["wire_decls"].append(output_net)
    elif output_net in rewire_nets_in_module:
        log(f"wire_decl_skipped_rewire_ref: true  net={output_net}  "
            f"(referenced by Pass 4 rewire in {mod} — implicit decl already created)")

    # wire_removes: remove_wire_decl entries
    if entry["change_type"] == "remove_wire_decl":
        perl_spec[mod]["wire_removes"].append(entry["signal_name"])
        record(status="APPLIED", reason="remove_wire_decl — added to Perl wire_removes")
        continue

    # Build gate instantiation line
    pins = ", ".join(f".{p}({n})" for p, n in stage_ports.items())
    gate_line = f"  {entry['cell_type']} {inst} ( {pins} ) ;"
    perl_spec[mod]["gates"].append(gate_line)
    record(status="INSERTED", reason=f"gate added to Perl spec for module {mod}")
```

**A module may appear multiple times** (parameterized instances) — the Perl engine handles each occurrence separately (see template below).

---

## 5. Pass 1 — new_logic Insertions (Perl Script Approach)

Pass 1 uses a **Perl streaming script** to apply all gate/DFF insertions and wire decl operations in a single pipe pass. This eliminates endmodule boundary drift (SVR-4) and spurious wire decl duplicates (SVR-9) by design.

### Pre-Pass 1 — Per-Entry Decisions (agent reasoning, no file touch)

Before generating the Perl script, resolve the following for each `new_logic_gate`, `new_logic_dff`, and `remove_wire_decl` entry:

**ALREADY_APPLIED check:**
```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | grep -cw "<instance_name>"
```
If count > 0 AND `force_reapply: false` → mark ALREADY_APPLIED, exclude from Perl spec.
If count > 0 AND `force_reapply: true` → include in Perl spec (Perl will find and remove the old instance, then re-insert).

**SKIPPED checks (for new_logic_gate / new_logic_dff):**

*Input net existence:*
For each input pin in `port_connections_per_stage[Stage]` (or `port_connections` if absent):
- Constants (`1'b0`, `1'b1`): always valid.
- `NEEDS_NAMED_WIRE:<source_net>`: resolve named_wire (see below), then verify named_wire exists.
- `UNRESOLVABLE_IN_<signal>`: grep in compressed stage file — found → use it; not found → SKIPPED.
- All other nets: `zcat PostEco/<Stage>.v.gz | grep -cw "<net>"` >= 1 required.
If any required input absent → SKIPPED. Record reason.

*HFS alias check (Real Net Preference):*
Before encoding any net into the Perl spec, check if it is a P&R alias:
```bash
grep -rw "<net_name>" data/PreEco/SynRtl/   # count=0 → P&R alias
```
If alias → grep the real RTL net (`old_net`/`new_net` from study JSON) in compressed stage file. If count >= 1 → use real net. Record `net_upgraded_from_alias: true`.

*DFF-specific: scan pin derivation (SI/SE) — THREE-STEP before encoding:*
- Step A: `zcat PreEco/<Stage>.v.gz | grep -m1 "<cell_type_prefix>"` in same module scope — extract SI/SE.
- Step B: `zcat PreEco/<Stage>.v.gz | grep -m1 "<cell_type_prefix>"` anywhere in stage — extract SI/SE.
- Step C (Synthesize only): use `1'b0` constants. For PrePlace/Route: if Steps A/B fail → SKIPPED.

*DFF-specific: clock verification:*
`zcat PostEco/<Stage>.v.gz | grep -cw "<clock_net>"` — if count=0 → SKIPPED.

**Cell type resolution (new_logic_gate):**
1. Use `cell_type` from study JSON if provided — verify it exists in this stage's PreEco: `zcat PreEco/<Stage>.v.gz | grep -cm1 "<cell_type>"`.
2. If absent → search for variant with same gate function, different suffix.
3. If still not found → SKIPPED. Never leave `cell_type: "?"`.

**GATE_OUTPUT_PIN verification:**

| Gate function | Expected output pin |
|---------------|---------------------|
| AND2/3/4, OR2/3/4, XOR2, MUX2, BUF | `Z` |
| INV, NAND2/3/4, NOR2/3/4, XNOR2, IND2/3 | `ZN` |
| DFF, SDFF, DFFR | `Q` |

Grep `cell_type` in PreEco to confirm actual output pin — PreEco wins over table. Correct `port_connections` before encoding into Perl spec.

**OUTPUT NET — wire_decls rule (CRITICAL — prevents SVR-9):**
- `needs_explicit_wire_decl: true` → add OUTPUT NET ONLY to `wire_decls` — the net whose key is ZN/Z/Q in `port_connections`.
- NEVER add any INPUT net to `wire_decls`, even if `needs_explicit_wire_decl: true` is set on the entry. Input nets already exist in the netlist.
- DFF output (`target_register`) is an implicit wire — never add to `wire_decls`.
- **REWIRE EXCLUSION (CRITICAL — prevents SVR-9 from Pass 4 cross-reference):**
  Before adding any output net to `wire_decls`, check whether that net also appears as `new_net` in any `rewire` entry for the same module in the study JSON:
  ```python
  rewire_new_nets = {e["new_net"] for e in study_entries
                     if e["change_type"] == "rewire" and e["module_name"] == mod}
  if output_net in rewire_new_nets:
      # SKIP wire_decl — the Pass 4 rewire reference to this net appears BEFORE
      # the Perl insertion point (endmodule) in the file, creating an implicit
      # wire declaration. Adding explicit wire decl here → SVR-9 duplicate.
      log(f"wire_decl_skipped: {output_net} referenced by rewire in {mod}")
  ```
  **Why:** Pass 4 rewires existing cells (e.g., `ctmi_523004.S → n_eco_9868_mux_sel`). That rewired cell appears at its original position in the netlist — BEFORE the Perl-inserted gates at endmodule. FM sees the rewire reference as an implicit wire declaration, then the explicit `wire N;` in the Perl batch as a second declaration → SVR-9.

**NEEDS_NAMED_WIRE inputs:**
For pins starting `NEEDS_NAMED_WIRE:<source_net>`:
- Derive `named_wire = f"eco_{JIRA}_{<signal_alias>}"` — record in applied JSON.
- The port bus rewire (replacing `source_net` → `named_wire`) is a Pass 4 rewire operation, NOT a Perl gate insertion. Encode the port connection with `named_wire` in the gate line directly.

---

### Phase B — Generate Perl Script

After all per-entry decisions, write one Perl script per stage:

**File:** `/tmp/eco_apply_<TAG>_<Stage>.pl`

```perl
#!/usr/bin/perl
# ECO Apply — JIRA <JIRA> — <Stage> stage
# TAG=<TAG>  Round=<ROUND>
# Generated by eco_applier — do NOT edit manually
use strict;
use warnings;

# All changes collected upfront — module_name → {wire_decls, wire_removes, gates}
# wire_decls : output nets of new gates that need explicit wire declaration
# wire_removes: existing wire decls to remove (prevent SVR-9 duplicate)
# gates       : complete instantiation lines for ALL new cells in this module
my %changes = (
  '<module_name_1>' => {
    wire_decls   => ['<output_net_1>', '<output_net_2>'],
    wire_removes => ['<existing_wire_to_remove>'],
    gates        => [
      '  // ECO <JIRA> TAG=<TAG>',
      '  <cell_type> <inst_name> ( .<pin1>(<net1>), .<pin2>(<net2>), .<outpin>(<out_net>) ) ;',
      # ... ALL gates for this module listed here
    ],
  },
  '<module_name_2>' => { wire_decls => [...], wire_removes => [...], gates => [...] },
  # ... one entry per target module
);

my $in_module = '';
my @buf;
my %processed;

while (my $line = <STDIN>) {
    if ($line =~ /^module\s+(\S+?)[\s(;]/) {
        my $mod = $1;
        if (exists $changes{$mod}) {
            $in_module = $mod;
            @buf = ($line);
            next;
        }
    }
    if ($in_module) {
        if ($line =~ /^endmodule\b/) {
            my $spec = $changes{$in_module};
            # Build wire remove set
            my %rm = map { $_ => 1 } @{ $spec->{wire_removes} };
            # Filter buffer: remove matching wire decls
            my @filtered;
            for my $bl (@buf) {
                if (%rm && $bl =~ /^\s*wire\s+(\w+)\s*;/ && $rm{$1}) {
                    print STDERR "REMOVED wire $1 from $in_module\n";
                    next;
                }
                push @filtered, $bl;
            }
            # Add wire decls for new output nets (batch — all before gates)
            push @filtered, "  wire $_ ;\n" for @{ $spec->{wire_decls} };
            # Insert all gates as single batch
            push @filtered, "$_\n" for @{ $spec->{gates} };
            print join('', @filtered);
            print $line;   # endmodule
            $processed{$in_module}++;
            $in_module = '';
            @buf = ();
        } else {
            push @buf, $line;
        }
    } else {
        print $line;
    }
}
# Summary to stderr — agent reads this to verify
print STDERR "\n=== ECO APPLY SUMMARY: <TAG> <Stage> ===\n";
for my $mod (sort keys %changes) {
    if ($processed{$mod}) {
        print STDERR "OK  $mod: ${\scalar @{$changes{$mod}{gates}}} gates, "
                   . "${\scalar @{$changes{$mod}{wire_decls}}} wire_decls, "
                   . "${\scalar @{$changes{$mod}{wire_removes}}} wire_removes\n";
    } else {
        print STDERR "MISSING  $mod — NOT FOUND in netlist\n";
    }
}
print STDERR "=== DONE ===\n";
```

**Perl pipe error handling:**
- If a module in `%changes` is NOT found → printed as `MISSING` in summary → eco_applier reads summary, marks all entries for that module VERIFY_FAILED, restores backup.
- Partial write (pipe interrupted): eco_applier detects via MD5 unchanged or line count anomaly → restore backup.
- Gate ordering within a module: gates in `gates[]` are inserted top-to-bottom before `endmodule`. Dependent gates (gate B uses gate A's output) must appear with gate A before gate B in the list.

**Rules when filling in `%changes`:**
1. One key per target module — ALL gates for that module in the `gates` array.
2. `wire_decls`: output nets only (ZN/Z/Q values) — never input net names.
3. `wire_removes`: only pre-existing explicit `wire <net>;` lines that cause SVR-9.
4. Gate lines must be complete Verilog instantiations including all pins and terminating ` ;`.
5. If a module appears 0 times in the netlist, the MISSING line in summary triggers VERIFY_FAILED.

---

### Phase C — Execute and Verify

**Step C1 — Run:**
```bash
zcat <REF_DIR>/data/PostEco/<Stage>.v.gz \
  | perl /tmp/eco_apply_<TAG>_<Stage>.pl 2>/tmp/eco_apply_<TAG>_<Stage>_summary.txt \
  | gzip > <REF_DIR>/data/PostEco/<Stage>_eco_new.v.gz
```

**Step C2 — Read summary, check for MISSING:**
```bash
cat /tmp/eco_apply_<TAG>_<Stage>_summary.txt
grep "^MISSING" /tmp/eco_apply_<TAG>_<Stage>_summary.txt
```
If any MISSING line → the target module was not found → VERIFY_FAILED for all entries in that module. Do NOT proceed to swap.

**Step C3 — Verify instances are inside module boundary:**
For each inserted instance_name:
```bash
# Get line numbers
zcat <Stage>_eco_new.v.gz | grep -n "<instance_name>\|^endmodule" | \
  awk '/instance_name/{inst=$0} /^endmodule/{if(inst) {print inst; print $0; inst=""; exit}}'
```
Instance line must be < endmodule line. If instance_line >= endmodule_line → BOUNDARY_VIOLATION → VERIFY_FAILED.

**Step C4 — Verify wire decls removed:**
For each net in `wire_removes`:
```bash
zcat <Stage>_eco_new.v.gz | grep -c "^\s*wire <net>\s*;"
```
Count must be 0. If > 0 → wire decl not removed → flag as WARNING (FM may still abort).

**Step C5 — Atomic swap (only if all checks pass):**
```bash
mv <REF_DIR>/data/PostEco/<Stage>_eco_new.v.gz \
   <REF_DIR>/data/PostEco/<Stage>.v.gz
```
If any check failed → delete `<Stage>_eco_new.v.gz`, restore from backup, mark VERIFY_FAILED.

**Step C6 — DFF-specific: verify output net not pre-declared:**
```bash
zcat <Stage>.v.gz | grep -cw "wire <target_register>\|reg <target_register>"
```
If count > 0 → DFF output conflicts with existing declaration → VERIFY_FAILED.

Record final status for each entry: INSERTED (gate in summary OK), SKIPPED (reason from Phase A), ALREADY_APPLIED (from pre-check), VERIFY_FAILED (from C2/C3/C4).

---

## 6. Pass 2 — port_declaration and port_promotion

### Pass 2a — port_declaration

**MANDATORY pre-check:** Hierarchical netlist → always apply regardless of `flat_net_confirmed` flag.

Read `declaration_type`:
- `"input"` or `"output"` → TRUE PORT DECLARATION — apply steps below.
- `"wire"` → SKIP (corresponding `port_connection` implicitly declares the wire; explicit `wire N;` causes FM-599). Record SKIPPED with reason "wire implicitly declared via port connections".

**BATCH all PORT_DECL changes for the same module in ONE modification** to avoid stale line numbers. Deduplicate by `signal_name` — last entry (force_reapply) wins; log which duplicate was discarded.

**Find port list close using parenthesis depth tracking:**
```python
depth = 0
for i in range(mod_idx, endmodule_idx):
    # Strip trailing comments before counting parens — comments may contain ) chars
    line_no_comment = lines[i].split('//')[0]
    for ch in line_no_comment:
        if ch == '(': depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0: port_list_close_idx = i; break
    if port_list_close_idx: break
```

**Validate found close line:** (1) `port_list_close_idx` must not be None; (2) line must NOT contain `.pin(` patterns — if it does, depth tracking hit a cell port connection, not the module port list → SKIPPED; (3) line must contain `)` (`rfind` = -1 would corrupt); (4) line should match `\)\s*;` — if not, advance to find the actual `) ;` on its own line.

**Insert signals before last `)` on close line:**
```python
new_sigs = ''.join(f' , {s}' for s in signal_names)
lines[port_list_close_idx] = close_line[:last_paren] + new_sigs + '\n)' + close_line[last_paren+1:]
```
Then verify port list depth = 0 after insertion.

**Port list format differences by stage:**
- Synthesize: Compact port list, usually fits in 10–30 lines.
- PrePlace/Route: Expanded port list with scan/clock/test ports — may span 200+ lines.
- NEVER assume the close `) ;` is within the first N lines — scan the full range.

**Multi-line port list:** the port list `) ;` may be:
- Case A: `  signal_N) ;` — signal and closing `)` on same line
- Case B: `  signal_N ,` ... `  ) ;` — closing `)` on its own line
- Case C: `  signal_N` ... `  ,` ... `) ;` — comma-separated across many lines

The depth tracking handles all cases — just find depth == 0.

**After insertion, verify the inserted signals appear in the correct port list range:**
Re-run depth tracking after insertion to find `port_list_close_idx_new`. Verify all inserted signal names appear between `mod_idx` and `port_list_close_idx_new`. If any signal not found in port list range → VERIFY_FAILED (inserted in wrong location).

**Direction declaration placement:**
Insert IMMEDIATELY after the port list close line (`port_list_close_idx + 1`). Format: `"  <direction> <signal_name> ;\n"`. Do NOT insert at the end of the module — position matters for FM's port ordering check.

**Insert direction declarations** after port list close (one line per signal). Each insert shifts subsequent indices — update `port_list_close_idx` accordingly.

### Pass 2b — port_promotion

Signal already in module port list — do NOT add it again. Only change declaration keyword:
```python
re.sub(rf'\b(wire|reg)\b', 'output', lines[i], count=1)
```
Use `re.sub` with `\b` — plain `str.replace` matches partial occurrences within net names.

---

## 7. Pass 3 — port_connection

Find instance: `re.search(rf'\b{re.escape(submodule_pattern)}\s+{re.escape(instance_name)}\b', lines[i])`.

Find instance close using depth tracking. Validate close line: must NOT contain `.pin(` (would be an inner cell port connection); if it does, advance to find the actual `) ;` line.

**Instance block scope validation:**
The found `instance_close_idx` must satisfy ALL of:
- (a) Line contains `) ;` or `);` pattern
- (b) Line does NOT contain `.pin(` — would mean we're inside a nested instance
- (c) The number of `(` minus `)` in `[instance_start_idx..instance_close_idx]` = 0 (balanced)
- (d) `instance_close_idx < endmodule_idx` for this module

If (b) fails: advance line-by-line until a line with just `) ;` is found.
If (c) fails after advancing: SKIPPED with reason `"instance block parentheses unbalanced"`.
If (d) fails: SKIPPED with reason `"instance block extends past endmodule — corrupted netlist"`.

**Net existence check before inserting port_connection:**
For the `net_name` being connected: `grep -cw "<net_name>"` in module buffer. If count = 0 AND the net is NOT created by a `port_declaration` in this same round (check if `net_name` matches any `port_declaration` applied/to-be-applied in this stage): → log WARNING `"net_name not yet present — depends on port_declaration order"` → proceed anyway (port_declaration creates it in Pass 2 which runs first). If count = 0 AND net is not from a `port_declaration` → SKIPPED.

**ALREADY_APPLIED:** `re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', instance_block)` — found → ALREADY_APPLIED. If still on `old_net` → set `force_reapply: true`.

**CRITICAL — Check for existing port before inserting (prevents FM-599 duplicate port):**
Before inserting, check whether `<port_name>` already exists in the instance block with ANY net:
```python
existing = re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*(\S+?)\s*\)', instance_block)
```
- If `existing` found AND current_net == `net_name` → ALREADY_APPLIED (no action)
- If `existing` found AND current_net ≠ `net_name` → **REWIRE** the existing connection:
  Replace `.<port_name>(<current_net>)` with `.<port_name>(<net_name>)` (scoped to instance block).
  Record status=APPLIED, reason="rewired existing port connection from `<current_net>` to `<net_name>`".
  **Do NOT insert a second `.port_name(...)` line** — this creates FM-599 duplicate port error.
- If `existing` NOT found → ADD new connection (normal path).

**Insert (only when port does NOT already exist):** `', .<port_name>( <net_name> )'` before last `)` on close line.

**Verify (instance-scoped, flexible whitespace):** `re.search(rf'\.\s*{re.escape(port_name)}\s*\(\s*{re.escape(net_name)}\s*\)', instance_block)` — not found → VERIFY_FAILED.

---

## 8. Pass 4 — rewire

For existing cells where `new_net` already exists in PostEco.

### Real Net Preference Check

Before writing any net name into a rewire port connection, check whether the net from `port_connections_per_stage` is an HFS alias. HFS aliases are P&R-stage artifacts that can be renamed in subsequent operations — hardcoding them causes FM mismatches in later rounds.

**P&R alias detection (generic — no hardcoded patterns):**
A net is a P&R alias if it does NOT exist in the RTL source:
```bash
grep -rw "<net_name>" data/PreEco/SynRtl/   # count = 0 → P&R alias; count > 0 → real RTL net
```

**Check procedure:**
1. `grep -rw "<net_name>" data/PreEco/SynRtl/ → count = 0` → it is a P&R alias.
2. Grep for the real RTL-named net (from the study JSON `old_net` or `new_net` field) in the current stage module buffer:
   ```bash
   grep -cw "<real_net>" <module_buffer>
   ```
3. If real net count >= 1 → use the real net instead of the alias. Record in the entry JSON:
   ```json
   "net_upgraded_from_alias": true,
   "original_alias": "<alias>",
   "real_net_used": "<real_net>"
   ```
4. If real net count = 0 → proceed with the alias as-is (real net not present in this stage).

This check applies to BOTH the `old_net` lookup AND the `new_net` to be written.

**ALREADY_APPLIED:** `re.search(rf'\.\s*{re.escape(pin_name)}\s*\(\s*{re.escape(new_net)}\s*\)', cell_block)` — found → ALREADY_APPLIED. If still on `old_net` in Round 2+ → set `force_reapply: true`. If old_net not found either → SKIPPED (PostEco differs structurally).

Apply scoped replacement within cell instance block only. Never global replace.

---

## 9. Post-Apply Validation (Checks 1–7)

Run on the UNCOMPRESSED temp file BEFORE `gzip`. If ANY check fails: discard temp file, restore from backup, mark ALL affected entries VERIFY_FAILED, do NOT recompress.

**Note:** Checks 1–7 are eco_applier's responsibility. The validate_verilog_netlist.py strict-mode run and cross-stage consistency checks are owned by Step 5 (eco_pre_fm_checker), which runs after eco_applier writes the applied JSON. The calling orchestrator reads the applied JSON and generates the RPT — eco_applier writes JSON only, not RPT.

**Check 1 — No duplicate ports in any module header.** Parse each module's port list `(...)`, collect port names, flag any appearing > 1 time.

**Check 2 — Port list correctly closed.** For each `module`, depth-track through up to 50000 chars — depth must return to 0 exactly once. Also check: any net declared as both explicit `wire N;` AND as `input`/`output N;` → FM-599 conflict → FAIL.

**Check 3 — No signal declared as both input and output.** Grep for `input`/`output` declarations, collect signal names, find duplicates across both directions.

**Check 4 — Module count unchanged (ERROR + hard exit, not warning).**
```bash
preeco_count=$(zcat <REF_DIR>/data/PreEco/<Stage>.v.gz | grep -c "^module ")
posteco_count=$(grep -c "^module " /tmp/eco_apply_<TAG>_<Stage>.v)
[ "$preeco_count" != "$posteco_count" ] && set summary.module_count_mismatch=true && exit 1
```
Mark ALL entries VERIFY_FAILED. Never proceed to recompress with wrong module count.

**Acceptable module count changes:** `count_after == count_before` (PASS). Any increase → FAIL (eco_applier must not create new modules). Any decrease → FAIL (module deleted). Delta ≤ 5 is the only known false-alarm range — verify ECO instances present before overriding.

**Known false alarm condition:** Hierarchical netlists with parameterized or uniquified sub-modules may show a module count that differs by a small amount (≤ 5) after decompress/recompress due to tooling artifacts — not actual module creation/deletion. In this case:
1. Verify all ECO instances are present via grep in the modified module buffer
2. If ECO instances confirmed present AND count delta ≤ 5 → log as `module_count_mismatch_false_alarm: true` in JSON and continue (do NOT VERIFY_FAILED)
3. If count delta > 5 OR ECO instances cannot be confirmed → VERIFY_FAILED + EXIT (original hard rule)

Always record `module_count_mismatch_corrected: true` in the applied JSON when a false alarm is detected and overridden.

**Check 5 — No explicit wire conflicts with implicit port-connection wires.** For each module: collect `wire N;` explicit declarations and all nets appearing in `.anypin(N)` connections. Any overlap → FAIL. eco_applier NEVER adds explicit `wire N;` — every net is implicitly declared via port connections.

**Check 6 — No duplicate port connections in any instance block.** For each `<type> <inst> (...)` block, collect `.pin(` names, flag any appearing > 1 time.

**Check 7 — Every port in module header has a direction declaration in the body.** Parse port list names (excluding Verilog keywords), verify each has an `input`/`output`/`inout` declaration in the module body.

---

## 10. Recompress and Output

**NEVER recompress if ANY entry has VERIFY_FAILED or `module_count_mismatch = true`.** Restore backup to PostEco, delete temp file.

**When all checks pass:**
```bash
gzip -c /tmp/eco_apply_<TAG>_<Stage>.v > <REF_DIR>/data/PostEco/<Stage>.v.gz
pre_lines=$(wc -l < /tmp/eco_apply_<TAG>_<Stage>.v)
post_lines=$(zcat <REF_DIR>/data/PostEco/<Stage>.v.gz | wc -l)
diff=$(( post_lines - pre_lines ))
[ ${diff#-} -gt 5 ] && echo "ERROR: Recompress line count mismatch" && exit 1
rm -f /tmp/eco_apply_<TAG>_<Stage>.v
```

**In-memory verification (before recompress, from module buffers already in memory):**
- rewire: `old_net` must no longer appear on target pin in cell block.
- new_logic: `instance_name` must appear in module buffer.
- port_decl: `signal_name` must appear in port list range.

If any in-memory check fails → do NOT recompress; retry the change on the module buffer first.

---

## 11. ALREADY_APPLIED Detection Rules

Run ALL checks against the ORIGINAL module buffer (pre-snapshot from S4), never against the modified buffer.

| change_type | ALREADY_APPLIED condition |
|-------------|--------------------------|
| Pre-check (ALL types, Surgical Patch mode only) | Read `prior_status` from prior round JSON (`data/<TAG>_eco_applied_round<ROUND-1>.json`). If `"SKIPPED"` → skip ALREADY_APPLIED check; mark SKIPPED with `reason: "Carried from Round <N>: <prior_reason>"`. Only proceed to type-specific ALREADY_APPLIED checks when prior_status ∈ {APPLIED, INSERTED, ALREADY_APPLIED}. |
| `new_logic_dff` / `new_logic_gate` / `new_logic` | **Step 1:** instance exists: `grep -c "^\s*<cell_type>\s*<instance_name>\s*("` >= 1. **Step 2 (MANDATORY):** for each input pin in `port_connections_per_stage[stage]`, verify expected net is on that pin using `\.<pin>\s*\(\s*<expected_net>\s*\)`. Step 1 passes but Step 2 fails for ANY pin → NOT ALREADY_APPLIED; set `force_reapply: true`. |
| `rewire` | `re.search(r'\.<pin>\s*\(\s*<new_net>\s*\)', cell_block)` — found = ALREADY_APPLIED. Still on old_net → `force_reapply: true`. |
| `port_declaration` (`input`/`output`) | Signal in MODULE PORT LIST (not just body). Parse from `mod_idx` to `port_list_close_idx`. Signal only in body as wire/DFF output does NOT count. |
| `port_declaration` (`wire`) | `grep -c "^\s*wire\s+<signal_name>\s*;"` >= 1 in module body. |
| `port_promotion` | `grep -c "output\s+<signal_name>\s*;"` >= 1 in module scope. |
| `port_connection` | `re.search(r'\.<port_name>\s*\(\s*<net_name>\s*\)', instance_block)` — found = ALREADY_APPLIED. Still on old_net → `force_reapply: true`. |

Always record `already_applied_reason` in JSON with exactly what was checked and what was found.

---

## 12. Applied JSON Schema

Write `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. Every entry MUST include `reason` or `already_applied_reason` (used by ORCHESTRATOR to generate RPT).

```json
{
  "Synthesize": [
    {
      "cell_name": "<cell_name>", "cell_type": "<cell_type>",
      "pin": "<pin_name>", "old_net": "<old_net>", "new_net": "<new_net>",
      "change_type": "rewire", "status": "APPLIED",
      "reason": "pin .<pin>(<old_net>) found at line <N>, replaced with .<pin>(<new_net>)",
      "occurrence_count": 1,
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff", "target_register": "<register_signal>",
      "instance_scope": "<inst_path>/<sub_inst>", "cell_type": "<dff_cell_type>",
      "instance_name": "eco_<jira>_<seq>",
      "inv_inst_full_path": "<TILE>/<inst_path>/<sub_inst>/eco_<jira>_<seq>",
      "output_net": "n_eco_<jira>_<seq>",
      "port_connections": {"<clk_pin>": "<clk_net>", "<data_pin>": "<data_net>", "<q_pin>": "n_eco_<jira>_<seq>"},
      "status": "INSERTED",
      "reason": "DFF <cell_type> eco_<jira>_<seq> inserted before endmodule at line <N>",
      "backup": "<REF_DIR>/data/PostEco/Synthesize.v.gz.bak_<TAG>_round<ROUND>",
      "verified": true
    },
    {
      "change_type": "new_logic_dff", "instance_name": "eco_<jira>_<seq>",
      "status": "ALREADY_APPLIED",
      "already_applied_reason": "instance 'eco_<jira>_<seq>' present (grep count=1) AND all input pins verified in instance block"
    },
    // SCHEMA RULE: Every entry — including ALREADY_APPLIED and SKIPPED — MUST include at least one
    // identifier field: instance_name (new_logic types), cell_name (rewire types),
    // signal_name (port_declaration types), or port_name (port_connection types).
    // Copy the identifier from the study JSON entry. An entry that omits ALL identifiers
    // produces "?" in the RPT and is a schema violation.
    {
      "change_type": "port_declaration", "signal_name": "<port_signal>",
      "module_name": "<module>", "declaration_type": "output",
      "status": "APPLIED",
      "reason": "added '<port_signal>' to port list at line <N>; added 'output <port_signal> ;' at line <M>"
    },
    {
      "change_type": "port_connection", "port_name": "<port>", "net_name": "<net>",
      "instance_name": "<submodule_instance>", "status": "SKIPPED",
      "reason": "instance '<submodule_instance>' not found in module '<parent_module>' scope"
    }
  ],
  "PrePlace": [],
  "Route": [],
  "summary": {
    "total": "<count>", "applied": "<count>", "inserted": "<count>",
    "already_applied": "<count>", "skipped": "<count>", "verify_failed": "<count>",
    "module_count_mismatch": false,
    "pre_flight_restore": false,
    "pre_flight_restored_stages": []
  }
}
```

---

## 13. Critical Safety Rules

1. **NEVER edit if occurrence count > 1** — ambiguity; mark SKIPPED + AMBIGUOUS.
2. **NEVER do global search-replace** — scope all changes to the specific cell instance block.
3. **ALWAYS backup before decompressing** — one backup per stage per round with round number in name.
4. **Consistent instance naming across stages** — same seq_table for all 3 stages (never re-assign).
5. **ALWAYS verify from in-memory buffers** — no second decompress; check before recompress.
6. **NEVER recompress with VERIFY_FAILED or module count mismatch** — restore backup.
7. **Keep processing remaining cells if one is SKIPPED** — only skip entries whose `input_from_change` directly points to the SKIPPED entry. If a dependency gate was SKIPPED, substitute `1'b0` as a conservative placeholder rather than skipping the dependent gate.
8. **Use per-stage port_connections for DFF** — always read `port_connections_per_stage[<Stage>]`; fall back to flat `port_connections` only if absent.
9. **Detect netlist type before every stage** — `grep -c "^module "` before processing.
10. **eco_applier NEVER adds `wire N;` declarations** — every net is implicitly declared via port connections; explicit `wire N;` always causes FM-599.
11. **ALREADY_APPLIED for new_logic requires pin verification** — instance existence alone is insufficient; verify each input pin connection matches study JSON; if any pin differs → `force_reapply: true`.
12. **Always use real RTL-named net, not P&R alias, when both exist** — before writing any net name into a port connection or rewire, check if it is a P&R alias: `grep -rw "<net_name>" data/PreEco/SynRtl/ → count = 0` means P&R alias. If alias, grep for the real RTL net from the study JSON; if found (count >= 1) use the real net and record `net_upgraded_from_alias: true`. Prevents P&R aliases from breaking subsequent rounds.
13. **Pass 1 uses Perl streaming — no in-memory buffer for gate insertions.** All `new_logic_gate`, `new_logic_dff`, and `remove_wire_decl` operations are encoded in a Perl script and executed via `zcat | perl | gzip` pipe. This eliminates endmodule boundary drift (SVR-4) by design — the Perl engine buffers each target module and inserts ALL its gates as a single batch before `endmodule`. Never revert to in-memory Python buffer manipulation for gate insertions.
14. **NEVER add `wire N;` for INPUT nets OR for rewire-referenced output nets.** Two cases that cause SVR-9:
    - *Input nets*: `wire_decls` accepts ONLY the gate's OUTPUT net (ZN/Z/Q). Input nets already exist as driven nets — explicit wire decl + pre-existing driver = SVR-9.
    - *Rewire-referenced output nets*: If the eco gate's output net is also used as `new_net` in a Pass 4 rewire entry for the same module, that rewired cell appears BEFORE the Perl insertion point in the file. FM sees the rewire reference as an implicit wire declaration; the explicit `wire N;` in the Perl batch is then a duplicate → SVR-9. Always check `rewire_new_nets` before adding to `wire_decls`.
15. **ALWAYS populate identifier fields in every JSON entry** — every entry (APPLIED, INSERTED, ALREADY_APPLIED, SKIPPED, VERIFY_FAILED) MUST include at least one of: `instance_name`, `cell_name`, `signal_name`, or `port_name`. Copy the identifier from the study JSON. An entry with no identifier produces `?` in the RPT — this is a schema violation.

**Final output:** `<BASE_DIR>/data/<TAG>_eco_applied_round<ROUND>.json`. After writing, verify it is non-empty and contains a `summary` field, then exit. The calling orchestrator reads the applied JSON and generates the RPT — eco_applier writes JSON only, not RPT.
