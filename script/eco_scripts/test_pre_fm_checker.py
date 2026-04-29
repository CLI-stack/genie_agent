#!/usr/bin/env python3
"""
test_pre_fm_checker.py — Simulate all Pass/Fail scenarios for eco_check8.sh
and validate_verilog_netlist.py

Usage:
    python3 script/eco_scripts/test_pre_fm_checker.py \
        --ref-dir <REF_DIR> \
        --base-dir <BASE_DIR> \
        --tag <TAG>

Runs all scenarios and prints PASS/FAIL for each.
"""

import argparse, gzip, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path


def run_validator(script, stages_gz, modules=None, strict=True):
    """Run validate_verilog_netlist.py and return (exit_code, stdout)."""
    cmd = ["python3", script]
    if strict:
        cmd.append("--strict")
    if modules:
        cmd += ["--modules"] + modules
    cmd.append("--")
    cmd += stages_gz
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def run_check8(script, base_dir, ref_dir, tag, round_n, applied_json):
    """Run eco_check8.sh and return (exit_code, output)."""
    cmd = ["bash", script, base_dir, ref_dir, tag, str(round_n), applied_json]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def decompress(gz_path):
    with gzip.open(gz_path, 'rt', errors='replace') as f:
        return f.readlines()


def compress(lines, gz_path):
    with gzip.open(gz_path, 'wt') as f:
        f.writelines(lines)


def find_eco_module_line(lines, module_keyword="umcarbctrlsw"):
    """Find a module in the netlist for injection."""
    for i, line in enumerate(lines):
        if re.match(rf'^module.*{module_keyword}', line):
            return i
    return None


def find_instance_close(lines, start, keyword):
    """Find an instance close ') ;' near keyword."""
    for i in range(start, min(start + 500, len(lines))):
        if keyword in lines[i]:
            # find ) ; nearby
            for j in range(i, min(i + 100, len(lines))):
                if re.match(r'^\s*\)\s*;', lines[j]):
                    return j
    return None


GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
results = []


def report(name, passed, detail=""):
    icon = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    results.append((name, passed))
    print(f"  {icon}  {name}" + (f" — {detail}" if detail else ""))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ref-dir", required=True)
    p.add_argument("--base-dir", required=True)
    p.add_argument("--tag", default="TEST_PREFM")
    args = p.parse_args()

    REF_DIR  = args.ref_dir
    BASE_DIR = args.base_dir
    TAG      = args.tag

    VALIDATOR = f"{BASE_DIR}/script/eco_scripts/validate_verilog_netlist.py"
    CHECK8    = f"{BASE_DIR}/script/eco_scripts/eco_check8.sh"
    SYNTH_GZ  = f"{REF_DIR}/data/PostEco/Synthesize.v.gz"
    SYNTH_BAK = f"{REF_DIR}/data/PostEco/Synthesize.v.gz.bak_test"
    PRE_SYNTH = f"{REF_DIR}/data/PreEco/Synthesize.v.gz"

    # Write minimal applied JSON for check8
    applied_json = f"{BASE_DIR}/data/{TAG}_eco_applied_round1.json"
    study_json   = f"{BASE_DIR}/data/{TAG}_eco_preeco_study.json"
    Path(f"{BASE_DIR}/data").mkdir(parents=True, exist_ok=True)
    Path(applied_json).write_text(json.dumps({
        "summary": {"applied": 1, "inserted": 1, "skipped": 0, "verify_failed": 0},
        "Synthesize": [{"change_type": "new_logic_gate", "instance_name": "eco_test_1",
                        "status": "INSERTED", "module_name": "ddrss_umccmd_t_umcarbctrlsw"}],
        "PrePlace": [], "Route": []
    }))
    Path(study_json).write_text(json.dumps({
        "Synthesize": [{"change_type": "new_logic_gate", "instance_name": "eco_test_1",
                        "confirmed": True, "module_name": "ddrss_umccmd_t_umcarbctrlsw"}],
        "PrePlace": [], "Route": []
    }))

    # Backup original PostEco (may already be clean or contain ECO changes)
    if os.path.exists(SYNTH_GZ):
        shutil.copy2(SYNTH_GZ, SYNTH_BAK)
    print(f"\n{'='*60}")
    print("ECO Pre-FM Checker — Test Suite")
    print(f"{'='*60}\n")

    # Synthetic clean netlist for pattern tests — avoids loading 300MB PreEco
    # IMPORTANT: no explicit wire decl for nets that appear in port connections (avoids F2)
    CLEAN_MODULE = (
        "module test_clean_mod ( A , B , C ) ;\n"
        "  input A;\n  input B;\n  output C;\n"
        "  ANDA2BWP inst1 ( .A1(A), .A2(B), .Z(C) ) ;\n"
        "endmodule\n"
    )

    # ── SCENARIO 1: PASS — clean synthetic netlist ───────────────
    print("SCENARIO 1 — PASS: Clean netlist (validator returns 0)")
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(CLEAN_MODULE)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: clean netlist returns 0", rc == 0, f"exit={rc}" + (f" errors={out[:80]}" if rc else ""))
    print()

    # ── SCENARIO 2: FAIL — SVR9_dup_wire (F1) ────────────────────
    print("SCENARIO 2 — FAIL: SVR9_dup_wire (duplicate wire declaration)")
    content = (
        "module test_dup_wire ( A , B ) ;\n"
        "  input A;\n  output B;\n"
        "  wire eco_dup_net ;\n"
        "  wire eco_dup_net ;\n"   # duplicate
        "  INVD1BWP inst1 ( .I(A), .ZN(eco_dup_net) ) ;\n"
        "  INVD1BWP inst2 ( .I(eco_dup_net), .ZN(B) ) ;\n"
        "endmodule\n"
    )
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: F1_dup_wire detected", rc != 0 and "F1_dup_wire" in out)
    print()

    # ── SCENARIO 3: FAIL — SVR4_bare_paren (F7) ──────────────────
    print("SCENARIO 3 — FAIL: SVR4_bare_paren (bare ) without ;)")
    # Create synthetic module with bare ) to test detection
    syn_content = "module test_bare_paren ( A , B\n)\n  input A;\n  input B;\nendmodule\n"
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(syn_content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: SVR4_bare_paren detected", rc != 0 and "SVR4_bare_paren" in out,
           out[:120] if "SVR4_bare_paren" not in out else "")
    print()

    # ── SCENARIO 4: FAIL — SVR4_trailing_comma ───────────────────
    print("SCENARIO 4 — FAIL: SVR4_trailing_comma (trailing , before );)")
    syn_content = (
        "module test_trailing_comma ( A , B ) ;\n"
        "  input A;\n  input B;\n"
        "  CELLA inst1 (\n"
        "    .pin1 ( A ) ,\n"
        "    .pin2 ( B ) ,\n"    # trailing comma before ) ;
        "    ) ;\n"
        "endmodule\n"
    )
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(syn_content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: SVR4_trailing_comma detected", rc != 0 and "SVR4_trailing_comma" in out,
           out[:120] if "SVR4_trailing_comma" not in out else "")
    print()

    # ── SCENARIO 5: FAIL — SVR4_double_comma ─────────────────────
    print("SCENARIO 5 — FAIL: SVR4_double_comma (, , in port connection)")
    content = (
        "module test_double_comma ( A , B ) ;\n"
        "  input A;\n  output B;\n"
        "  CELLA inst1 ( .pin1(A) , , .pin2(B) ) ;\n"  # double comma
        "endmodule\n"
    )
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: SVR4_double_comma detected", rc != 0 and "SVR4_double_comma" in out)
    print()

    # ── SCENARIO 6: FAIL — SVR4_missing_cell_type ────────────────
    print("SCENARIO 6 — FAIL: SVR4_missing_cell_type (eco_ instance without cell type)")
    content = (
        "module test_missing_ct ( A , B ) ;\n"
        "  input A;\n  output B;\n"
        "  wire net1 ;\n"
        "  eco_9899_pm0 ( .I(A), .ZN(net1) ) ;\n"  # no cell type prefix
        "  INVD1BWP inst2 ( .I(net1), .ZN(B) ) ;\n"
        "endmodule\n"
    )
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: SVR4_missing_cell_type detected", rc != 0 and "SVR4_missing_cell_type" in out)
    print()

    # ── SCENARIO 7: VERIFY — pre-existing F2 should NOT fail check8 ─
    print("SCENARIO 7 — VERIFY: F2_implicit_wire_conflict (pre-existing F2 should NOT fail check8)")
    # Use actual PreEco (which has 150 pre-existing F2s) as PostEco for this check
    with gzip.open(PRE_SYNTH, 'rb') as fin, gzip.open(SYNTH_GZ, 'wb') as fout:
        fout.write(fin.read())
    rc8, out8 = run_check8(CHECK8, BASE_DIR, REF_DIR, TAG, 1, applied_json)
    check8_json = Path(f"{BASE_DIR}/data/{TAG}_eco_check8_round1.json")
    if check8_json.exists():
        r = json.loads(check8_json.read_text())
        pre_f2 = r.get("f2_preexisting_count", 0)
        all_pass = all(r[s]=="PASS" for s in ["Synthesize","PrePlace","Route"])
        report("check8: pre-existing F2s do NOT cause FAIL", all_pass, f"f2_preexisting={pre_f2}")
    print()

    # ── SCENARIO 8: FAIL — F2 NEW conflict (ECO introduced) ──────
    print("SCENARIO 8 — FAIL: F2 NEW implicit_wire_conflict (ECO introduced)")
    content = (
        "module test_f2_conflict ( A , B ) ;\n"
        "  input A;\n  output B;\n"
        "  wire eco_conflict_net ;\n"          # explicit wire decl
        "  CELLA inst1 ( .pin1(eco_conflict_net) ) ;\n"  # also implicit from port conn
        "  INVD1BWP inst2 ( .I(eco_conflict_net), .ZN(B) ) ;\n"
        "endmodule\n"
    )
    with gzip.open(SYNTH_GZ, 'wt') as f:
        f.write(content)
    rc, out = run_validator(VALIDATOR, [SYNTH_GZ], strict=True)
    report("validator: F2_implicit_wire_conflict (ECO-new) detected", rc != 0 and "F2_implicit_wire_conflict" in out)
    print()

    # ── Restore ───────────────────────────────────────────────────
    shutil.copy2(SYNTH_BAK, SYNTH_GZ)
    os.remove(SYNTH_BAK)
    # cleanup test data files
    for f in [applied_json, study_json, f"{BASE_DIR}/data/{TAG}_eco_check8_round1.json",
              f"{BASE_DIR}/data/{TAG}_eco_check8_round1_marker.txt"]:
        try: os.remove(f)
        except FileNotFoundError: pass

    # ── Summary ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, p in results if p)
    failed = sum(1 for _, p in results if not p)
    for name, ok in results:
        icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"  {icon} {name}")
    print(f"\n  Total: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
