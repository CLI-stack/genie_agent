#!/usr/bin/env python3
"""
genie_eco_sync.py - Synchronize Genie AI ECO flow changes from the centralized master repo
(/home/abinbaba/eco_flow) to:
  1. Target OSS workspace (<refdir>/src/meta/skills/oss-eco)
  2. Production shared repo (/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent) + Git Push

Usage:
  python3 genie_eco_sync.py [--refdir <path>] [--commit-msg "msg"]
"""

import os
import sys
import shutil
import hashlib
import argparse
import subprocess
from pathlib import Path

MASTER_DIR = Path("/home/abinbaba/eco_flow")
GENIE_AGENT_DIR = Path("/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent")

def file_md5(p: Path) -> str:
    if not p.is_file():
        return ""
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def sync_tree(src_dir: Path, dst_dir: Path, rel_paths: list) -> tuple:
    """Sync specific relative paths or trees from src to dst. Return (synced_files, identical_files)."""
    synced, identical = [], []
    for rel in rel_paths:
        s_path = src_dir / rel
        d_path = dst_dir / rel

        if s_path.is_file():
            d_path.parent.mkdir(parents=True, exist_ok=True)
            if file_md5(s_path) != file_md5(d_path):
                shutil.copy2(s_path, d_path)
                synced.append(str(rel))
            else:
                identical.append(str(rel))
        elif s_path.is_dir():
            for root, _, files in os.walk(s_path):
                for file in files:
                    s_file = Path(root) / file
                    r_file = s_file.relative_to(src_dir)
                    d_file = dst_dir / r_file
                    d_file.parent.mkdir(parents=True, exist_ok=True)
                    if file_md5(s_file) != file_md5(d_file):
                        shutil.copy2(s_file, d_file)
                        synced.append(str(r_file))
                    else:
                        identical.append(str(r_file))
    return synced, identical

def sync_to_genie_agent(commit_msg: str) -> dict:
    """Sync Complete mode & shared scripts to genie_agent and git push."""
    rel_paths = [
        "config/eco_agents",
        "script/eco_scripts",
        "script/rtg_oss_feint/supra/eco_analyze.csh",
        "script/rtg_oss_feint/supra/find_equivalent_nets.csh",
        "script/rtg_oss_feint/supra/post_eco_formality.csh",
        "script/genie_cli.py"
    ]

    synced, identical = sync_tree(MASTER_DIR, GENIE_AGENT_DIR, rel_paths)

    git_status_out = ""
    git_commit_hash = ""
    git_pushed = False

    if synced:
        # Check git status
        st = subprocess.run(["git", "status", "--porcelain"], cwd=GENIE_AGENT_DIR, capture_output=True, text=True)
        git_status_out = st.stdout.strip()

        if git_status_out:
            # Stage changed files
            subprocess.run(["git", "add"] + [p for p in synced if (GENIE_AGENT_DIR / p).exists()], cwd=GENIE_AGENT_DIR)

            # Commit
            msg = commit_msg or f"Sync ECO flow updates from master repo: {len(synced)} files updated"
            msg += "\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
            cm = subprocess.run(["git", "commit", "-m", msg], cwd=GENIE_AGENT_DIR, capture_output=True, text=True)

            # Get commit hash
            rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=GENIE_AGENT_DIR, capture_output=True, text=True)
            git_commit_hash = rev.stdout.strip()

            # Push
            ps = subprocess.run(["git", "push", "origin", "main"], cwd=GENIE_AGENT_DIR, capture_output=True, text=True)
            git_pushed = (ps.returncode == 0)

    return {
        "synced": synced,
        "identical": identical,
        "commit_hash": git_commit_hash,
        "pushed": git_pushed
    }

def sync_to_oss_workspace(refdir: str) -> dict:
    """Sync self-contained oss-eco skill to <refdir>/src/meta/skills/oss-eco."""
    ref_path = Path(refdir)
    if not ref_path.is_dir():
        return {"error": f"Directory not found: {refdir}"}

    dst_skill_dir = ref_path / "src" / "meta" / "skills" / "oss-eco"
    dst_skill_dir.mkdir(parents=True, exist_ok=True)

    rel_paths = [
        "config/eco_agents",
        "config/eco_agents_simple",
        "script/eco_scripts",
        "script/rtg_oss_feint",
        "script/genie_cli.py",
        "script/genie_env.csh",
        "script/read_csv.py",
        "csh",
        "arguement.csv",
        "assignment.csv",
        "instruction.csv",
        "keyword.csv",
        "patterns.csv",
        "src/meta/skills/oss-eco/SKILL.md"
    ]

    synced, identical = [], []
    for rel in rel_paths:
        s_path = MASTER_DIR / rel
        if rel == "src/meta/skills/oss-eco/SKILL.md":
            d_path = dst_skill_dir / "SKILL.md"
            if file_md5(s_path) != file_md5(d_path):
                shutil.copy2(s_path, d_path)
                synced.append("SKILL.md")
            else:
                identical.append("SKILL.md")
            continue

        d_path = dst_skill_dir / rel
        if s_path.is_file():
            d_path.parent.mkdir(parents=True, exist_ok=True)
            if file_md5(s_path) != file_md5(d_path):
                shutil.copy2(s_path, d_path)
                synced.append(str(rel))
            else:
                identical.append(str(rel))
        elif s_path.is_dir():
            for root, _, files in os.walk(s_path):
                for file in files:
                    s_file = Path(root) / file
                    r_file = s_file.relative_to(MASTER_DIR)
                    d_file = dst_skill_dir / r_file
                    d_file.parent.mkdir(parents=True, exist_ok=True)
                    if file_md5(s_file) != file_md5(d_file):
                        shutil.copy2(s_file, d_file)
                        synced.append(str(r_file))
                    else:
                        identical.append(str(r_file))

    return {
        "dest": str(dst_skill_dir),
        "synced": synced,
        "identical": identical
    }

def main():
    parser = argparse.ArgumentParser(description="Synchronize Genie AI ECO flow changes across repos/workspaces.")
    parser.add_argument("--refdir", help="Path to target OSS workspace (e.g. /proj/.../oss8_0_...)")
    parser.add_argument("--commit-msg", help="Custom commit message for genie_agent repo")
    args = parser.parse_args()

    print("================================================================================")
    print("GENIE ECO SYNC — Centralized Repo Master (/home/abinbaba/eco_flow)")
    print("================================================================================")

    # 1. Sync to genie_agent
    print("\n1. Syncing to Shared Production Repo (/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent)...")
    ga_res = sync_to_genie_agent(args.commit_msg)
    if ga_res["synced"]:
        print(f"   [UPDATED] {len(ga_res['synced'])} files synced:")
        for f in ga_res["synced"][:10]:
            print(f"     + {f}")
        if len(ga_res["synced"]) > 10:
            print(f"     ... and {len(ga_res['synced']) - 10} more files")
        if ga_res["commit_hash"]:
            print(f"   [GIT COMMIT] {ga_res['commit_hash']}")
            print(f"   [GIT PUSH] {'SUCCESS (origin/main)' if ga_res['pushed'] else 'FAILED'}")
    else:
        print("   [CLEAN] genie_agent is already in sync with master repo.")

    # 2. Sync to OSS workspace (if provided)
    if args.refdir:
        print(f"\n2. Syncing to Target OSS Workspace ({args.refdir})...")
        oss_res = sync_to_oss_workspace(args.refdir)
        if "error" in oss_res:
            print(f"   [ERROR] {oss_res['error']}")
        else:
            if oss_res["synced"]:
                print(f"   [UPDATED] {len(oss_res['synced'])} files synced to {oss_res['dest']}:")
                for f in oss_res["synced"][:10]:
                    print(f"     + {f}")
                if len(oss_res["synced"]) > 10:
                    print(f"     ... and {len(oss_res['synced']) - 10} more files")
            else:
                print(f"   [CLEAN] {oss_res['dest']} is already in sync with master repo.")
    else:
        print("\n2. No --refdir provided. (To sync a specific OSS tree, pass --refdir <path>)")

    print("\n================================================================================")
    print("SYNC COMPLETE")
    print("================================================================================")

if __name__ == "__main__":
    main()
