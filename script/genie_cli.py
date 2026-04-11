#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genie CLI - Direct instruction interface for Claude Code
Bypasses email flow, executes agent scripts directly

Usage:
    python genie_cli.py --instruction "run cdc_rdc at /proj/xxx/tile1"
    python genie_cli.py --instruction "monitor supra run at /proj/xxx" --execute
    python genie_cli.py --list  # List available instructions

Author: Generated for Claude Code integration
"""

import argparse
import csv
import datetime
import getpass
import os
import re
import shutil
import subprocess
import sys


def setup_user_directory(base_dir=None, user_email=None, user_disk=None):
    """
    Setup user-specific directory for multi-user environment.
    Creates: users/$USER/ with data/, runs/, assignment.csv, and symlinks to shared resources.

    Args:
        base_dir: Base directory for genie_agent (default: auto-detect)
        user_email: User's email address (required, will prompt if not provided)
        user_disk: User's disk path for runs (required, will prompt if not provided)
    """
    # Determine base directory
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    username = getpass.getuser()
    user_dir = os.path.join(base_dir, 'users', username)

    print("=" * 70)
    print("Genie CLI - User Setup")
    print("=" * 70)
    print(f"Username: {username}")
    print(f"Base directory: {base_dir}")
    print(f"User directory: {user_dir}")
    print()

    # Prompt for email if not provided
    if user_email is None:
        print("Please enter your AMD email address for notifications.")
        print("(e.g., Firstname.Lastname@amd.com)")
        print()
        while True:
            user_email = input("Email address: ").strip()
            if not user_email:
                print("ERROR: Email address is required.")
                continue
            if not re.match(r'^[^@]+@amd\.com$', user_email, re.IGNORECASE):
                print("ERROR: Please enter a valid @amd.com email address.")
                continue
            break
        print()

    # Prompt for disk path if not provided
    if user_disk is None:
        print("Please enter your disk path for storing runs and outputs.")
        print("(e.g., /proj/rtg_oss_er_feint1/your_username)")
        print()
        while True:
            user_disk = input("Disk path: ").strip()
            if not user_disk:
                print("ERROR: Disk path is required.")
                continue
            if not user_disk.startswith('/'):
                print("ERROR: Please enter an absolute path starting with /")
                continue
            if not os.path.isdir(user_disk):
                print(f"WARNING: Path does not exist: {user_disk}")
                confirm = input("Continue anyway? [y/N]: ").strip().lower()
                if confirm != 'y':
                    continue
            break
        print()

    # Check if already setup
    if os.path.exists(user_dir):
        print(f"User directory already exists: {user_dir}")
        response = input("Do you want to reinitialize? This will NOT delete existing data. [y/N]: ").strip().lower()
        if response != 'y':
            print("Setup cancelled.")
            return

    # Create user directory structure
    dirs_to_create = [
        user_dir,
        os.path.join(user_dir, 'data'),
        os.path.join(user_dir, 'runs'),
        os.path.join(user_dir, 'params_centre'),
        os.path.join(user_dir, 'log_centre'),
        os.path.join(user_dir, 'tune_centre'),
    ]

    for d in dirs_to_create:
        if not os.path.exists(d):
            os.makedirs(d)
            print(f"✓ Created: {d}")
        else:
            print(f"  Exists: {d}")

    # Create symlinks to shared resources
    symlinks = {
        'script': os.path.join(base_dir, 'script'),
        'csh': os.path.join(base_dir, 'csh'),
        'py': os.path.join(base_dir, 'py'),
    }

    for link_name, target in symlinks.items():
        link_path = os.path.join(user_dir, link_name)
        if os.path.islink(link_path):
            os.unlink(link_path)
        if not os.path.exists(link_path) and os.path.exists(target):
            os.symlink(target, link_path)
            print(f"✓ Symlink: {link_name} -> {target}")

    # Copy shared CSV files (instruction, keyword, arguement, patterns)
    csv_files = ['instruction.csv', 'keyword.csv', 'arguement.csv', 'patterns.csv']
    for csv_file in csv_files:
        src = os.path.join(base_dir, csv_file)
        dst_link = os.path.join(user_dir, csv_file)
        if os.path.exists(src):
            if os.path.islink(dst_link):
                os.unlink(dst_link)
            if not os.path.exists(dst_link):
                os.symlink(src, dst_link)
                print(f"✓ Symlink: {csv_file}")

    # Create assignment.csv template if not exists
    assignment_file = os.path.join(user_dir, 'assignment.csv')
    if not os.path.exists(assignment_file):
        # User-specific paths
        user_log = os.path.join(user_dir, 'log_centre')
        user_params = os.path.join(user_dir, 'params_centre')
        user_tune = os.path.join(user_dir, 'tune_centre')

        template = f"""manager,{user_email}
librarian,{user_email}
flowLead,{user_email}
debugger,{user_email}
vto,Genie{username.capitalize()}
log,{user_log}
params,{user_params}
tune,{user_tune}
mail,/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/mail_centre/tasksMail.csv
project,
disk,{user_disk}
ip,oss8_0
ip,oss7_2
ip,umc14_2
ip,umc14_0
ip,umc9_2
ip,umc9_3
ip,umc17_0
tile,osssys
tile,hdp
tile,lsdma0
tile,sdma0_gc
tile,sdma1_gc
tile,sdma2_gc
tile,sdma3_gc
tile,ih_top
tile,ih_sem_share
tile,dma_body_gc
tile,lsdma0_body
tile,umccmd
tile,umcdat
tile,umc_top
gpt,GPT0
llmKey,
hGrid,3.648
vGrid,10.868
hOffset,2.784
vOffset,-1.144
vdciXoffset,0
vdciYoffset,0.195
cpp,0.048
rowH,0.273
lsfProject,rtg-mcip-ver
alertDisk,95
runQuota,50
monitorAction,1
monitorPeriod,1
monitorValidDays,60
maxUtil,0.7
maxRetrace,20
pendingTimeLimit,10
mailErrorReport,0
"""
        with open(assignment_file, 'w') as f:
            f.write(template)
        print(f"✓ Created: assignment.csv (full template)")
        print()
        print("IMPORTANT: Edit assignment.csv to configure your settings:")
        print(f"  {assignment_file}")
    else:
        print(f"  Exists: assignment.csv")

    print()
    print("=" * 70)
    print("Setup complete!")
    print("=" * 70)
    print()
    print("Next steps:")
    print(f"  1. Edit your assignment.csv:")
    print(f"     vi {assignment_file}")
    print()
    print(f"  2. Run commands from your user directory:")
    print(f"     cd {user_dir}")
    print(f"     python3 script/genie_cli.py -i \"run lint at /proj/xxx for umc9_3\" --execute --email")
    print()


class _MultiAgentOrchestrator_DISABLED:
    """
    Spawns LLM sub-agents for violation analysis.

    Supports two backends (auto-detected, AMD gateway takes priority):
      1. AMD Gateway  — https://llm-api.amd.com/azure  (uses llmKey from assignment.csv)
                        OpenAI-compatible, no extra package needed (uses requests)
      2. Anthropic    — direct API (uses ANTHROPIC_API_KEY env var or assignment.csv)
                        Requires: pip install anthropic

    Roles (loaded from config/prompts/{role}.md):
      analyzer — classifies violations, writes ANALYSIS COMPLETE report
      fixer    — takes Analyzer output, generates waiver TCL

    Usage:
        orc = MultiAgentOrchestrator.from_cli(cli)
        summary, waiver_file = orc.orchestrate(cli, report_path, ip, tag, ref_dir, 'cdc')

    For full_static_check: CDC + Lint + SPG_DFT Analyzers run in parallel threads.
    """

    AMD_GATEWAY_URL    = "https://llm-api.amd.com/azure"
    AMD_DEPLOYMENT     = "swe-gpt4o-exp1"
    DEFAULT_MODEL      = "swe-gpt4o-exp1"          # shown in log lines
    MAX_VIOLATIONS_IN_PROMPT = 300

    def __init__(self, base_dir, llm_key='', anthropic_key=''):
        """
        Initialise with either AMD gateway key or Anthropic key.
        AMD gateway takes priority if llm_key is set.
        Raises RuntimeError if neither key is available.
        """
        import requests as _requests
        self._requests = _requests
        self.base_dir  = base_dir

        if llm_key:
            self._backend      = 'amd'
            self._llm_key      = llm_key
            self.model         = self.AMD_DEPLOYMENT
        elif anthropic_key and ANTHROPIC_AVAILABLE:
            self._backend      = 'anthropic'
            self._anthropic    = _anthropic_lib.Anthropic(api_key=anthropic_key)
            self.model         = "claude-sonnet-4-6"
        else:
            raise RuntimeError(
                "No LLM key available. Set llmKey in assignment.csv (AMD gateway) "
                "or ANTHROPIC_API_KEY env var with anthropic package installed."
            )

    @classmethod
    def from_cli(cls, cli_instance):
        """Create orchestrator from a GenieCLI instance (reads keys automatically)."""
        llm_key      = cli_instance.get_llm_key()
        anthropic_key = cli_instance.get_api_key()
        return cls(cli_instance.base_dir, llm_key=llm_key, anthropic_key=anthropic_key)

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    def load_prompt(self, role):
        """Load system prompt from config/prompts/{role}.md."""
        path = os.path.join(self.base_dir, 'config', 'prompts', f'{role}.md')
        if os.path.exists(path):
            with open(path) as fh:
                return fh.read()
        return f"You are the {role} agent in the Genie multi-agent CDC analysis system."

    # ------------------------------------------------------------------
    # Backend API calls
    # ------------------------------------------------------------------

    def _call_amd_gateway(self, system_prompt, user_message, max_tokens=4096):
        """Call AMD LLM gateway (OpenAI-compatible format)."""
        url = f"{self.AMD_GATEWAY_URL}/engines/{self.AMD_DEPLOYMENT}/chat/completions"
        headers = {"Ocp-Apim-Subscription-Key": self._llm_key}
        body = {
            "messages": [
                {"role": "system",    "content": system_prompt},
                {"role": "user",      "content": user_message},
            ],
            "temperature": 0,
            "max_Tokens": max_tokens,
            "stream": False,
        }
        response = self._requests.post(url, json=body, headers=headers, timeout=(30, 90))
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']

    def _call_anthropic(self, system_prompt, user_message, max_tokens=4096):
        """Call Anthropic API directly."""
        response = self._anthropic.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def call_agent(self, role, user_message, max_tokens=4096, tag=None):
        """
        Call the LLM for the given role agent (routes to AMD gateway or Anthropic).
        Saves the raw response to data/<tag>_<role>_output.txt for audit.
        Returns the response text.
        """
        system_prompt = self.load_prompt(role)
        backend_label = f"AMD gateway ({self.model})" if self._backend == 'amd' else f"Anthropic ({self.model})"
        print(f"    [{role.upper()} AGENT] Calling {backend_label} ...", flush=True)

        if self._backend == 'amd':
            text = self._call_amd_gateway(system_prompt, user_message, max_tokens)
        else:
            text = self._call_anthropic(system_prompt, user_message, max_tokens)

        if tag:
            out_file = os.path.join(self.base_dir, 'data', f'{tag}_{role}_output.txt')
            try:
                with open(out_file, 'w') as fh:
                    fh.write(text)
                print(f"    [{role.upper()} AGENT] Saved → {os.path.basename(out_file)}", flush=True)
            except Exception:
                pass

        return text

    # ------------------------------------------------------------------
    # Input formatting helpers
    # ------------------------------------------------------------------

    def _format_cdc_input(self, violations, preconditions, ip, report_path, templates_yaml=''):
        """Build the Analyzer user message for a CDC report."""
        pc = preconditions or {}
        lines = [
            f"IP: {ip}",
            f"Report: {os.path.basename(report_path)}",
            "",
            "=== PRE-CONDITIONS ===",
            f"Inferred Primary Clocks:   {pc.get('inferred_clocks_primary',   0)}",
            f"Inferred Blackbox Clocks:  {pc.get('inferred_clocks_blackbox',  0)}",
            f"Inferred Gated-Mux Clocks: {pc.get('inferred_clocks_gated_mux', 0)}",
            f"Inferred Primary Resets:   {pc.get('inferred_resets_primary',   0)}",
            f"Inferred Blackbox Resets:  {pc.get('inferred_resets_blackbox',  0)}",
            f"Num Blackboxes:            {pc.get('num_blackboxes',            0)}",
            f"Num Unresolved Modules:    {pc.get('num_unresolved',            0)}",
        ]
        bbs = pc.get('empty_blackbox_modules', [])
        if bbs:
            lines.append("Empty Blackbox Modules:    " +
                         ", ".join(b.get('module', '?') for b in bbs))

        lines += ["", f"=== VIOLATIONS ({len(violations)} total) ==="]
        cap = self.MAX_VIOLATIONS_IN_PROMPT
        for v in violations[:cap]:
            lines.append(f"  {v.get('id','?'):25s} | type={v.get('type','?'):20s} | signal={v.get('signal','')}")
        if len(violations) > cap:
            lines.append(f"  ... ({len(violations) - cap} more violations truncated)")

        if templates_yaml:
            lines += ["", "=== FIX_TEMPLATES (apply these patterns) ===",
                      templates_yaml[:4000]]

        return "\n".join(lines)

    def _format_report_input(self, check_type, report_path, ip):
        """Build the Analyzer user message for lint or spg_dft (pass report excerpt)."""
        lines = [f"IP: {ip}", f"Check type: {check_type}",
                 f"Report: {os.path.basename(report_path)}", ""]
        try:
            with open(report_path, errors='replace') as fh:
                content = fh.read(60000)   # first 60 K chars
            lines += [f"=== {check_type.upper()} REPORT (excerpt) ===", content]
        except Exception as e:
            lines.append(f"Could not read report: {e}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Waiver extraction from Fixer output
    # (DEPRECATED: Fixer is no longer called in the main flow as of 2026-03-17.
    #  Retained for potential future use — e.g., auto-applying engineer-approved waivers.)
    # ------------------------------------------------------------------

    def _extract_tcl(self, fixer_output):
        """DEPRECATED: Extract TCL waiver lines from Fixer response text.
        No longer called in main flow — retained for future use."""
        tcl_lines = []
        in_block  = False
        for line in fixer_output.split('\n'):
            stripped = line.strip()
            if stripped.startswith('```tcl'):
                in_block = True
                continue
            if stripped == '```' and in_block:
                in_block = False
                continue
            if in_block:
                tcl_lines.append(line)
            elif (stripped.startswith('cdc report crossing') or
                  stripped.startswith('netlist ') or
                  (stripped.startswith('#') and tcl_lines)):
                tcl_lines.append(line)
        return '\n'.join(tcl_lines)

    def _extract_lint_waiver(self, fixer_output):
        """DEPRECATED: Extract lint waiver block(s) from Fixer response.
        No longer called in main flow — retained for future use."""
        lines = []
        in_block = False
        for line in fixer_output.split('\n'):
            if line.startswith('```lint') or line.startswith('```waiver') or line.startswith('```text'):
                in_block = True
                continue
            if in_block and line.startswith('```'):
                in_block = False
                continue
            if in_block:
                lines.append(line)
        if lines:
            return '\n'.join(lines)
        waiver_lines = []
        for line in fixer_output.split('\n'):
            if re.match(r'^(error|filename|line|code|msg|reason|author)\s*:', line.strip()):
                waiver_lines.append(line)
            elif waiver_lines and line.strip() == '':
                waiver_lines.append('')
        return '\n'.join(waiver_lines)

    def _extract_spg_filter(self, fixer_output):
        """DEPRECATED: Extract SPG_DFT filter patterns from Fixer response.
        No longer called in main flow — retained for future use."""
        lines = []
        in_block = False
        for line in fixer_output.split('\n'):
            if line.startswith('```filter') or line.startswith('```text') or line.startswith('```'):
                in_block = not in_block
                continue
            if in_block:
                lines.append(line)
        if lines:
            return '\n'.join(lines)
        filter_lines = []
        for line in fixer_output.split('\n'):
            stripped = line.strip()
            if re.match(r'^\[.+\]$', stripped) or (stripped and not stripped.startswith('#')):
                filter_lines.append(line)
        return '\n'.join(filter_lines)

    # ------------------------------------------------------------------
    # RTL Analyst step — ALL violations, batched by module
    # ------------------------------------------------------------------

    def _run_rtl_analysis_all(self, violations, ref_dir, ip, check_type, tag):
        """
        Run RTL Analyst on ALL violations, batched by module for efficiency.

        violations: list of dicts. For CDC: {'id', 'type', 'signal'}.
                    For lint: {'code', 'filename', 'line', 'msg'}.
                    For spg_dft: {'raw', 'classification', 'id'}.
        ref_dir: tree root directory (may be None for lint — uses violation filenames directly)
        check_type: 'cdc', 'lint', 'spg_dft'
        tag: task tag

        Returns: dict of {module_name: {'violations': [...], 'rtl_ctx_found': bool, 'analysis': str}}
        """
        from collections import defaultdict

        if not violations:
            return {}

        if RTLSignalTracer is None and check_type != 'lint':
            # No tracer available — still call LLM with empty RTL context
            pass

        tracer = None
        if RTLSignalTracer is not None:
            tracer = RTLSignalTracer(ref_dir or '')

        # --- Group violations by module ---
        module_groups = defaultdict(list)
        for v in violations:
            if check_type == 'cdc':
                sig   = v.get('signal', '')
                parts = sig.split('.')
                module = parts[-2] if len(parts) >= 2 else 'unknown'
            elif check_type == 'lint':
                fname  = v.get('filename', '')
                module = os.path.splitext(os.path.basename(fname))[0] if fname else 'unknown'
            else:  # spg_dft
                raw = v.get('raw', '')
                m   = re.search(r'[a-zA-Z]\w+', raw)
                module = m.group(0)[:30] if m else 'unknown'
            module_groups[module].append(v)

        print(f"    [RTL ANALYST] Analyzing {len(violations)} violation(s) across "
              f"{len(module_groups)} module(s) [parallel]", flush=True)

        all_analyses = {}
        errors       = {}
        # Limit to 8 concurrent LLM calls to avoid AMD gateway rate limiting
        _sem = threading.Semaphore(8)

        def _analyze_module(module_name, group_viols):
            """Worker: load RTL context + call LLM for one module group (runs in thread)."""
            try:
                rtl_ctx = ''
                if tracer:
                    if check_type == 'lint' and group_viols[0].get('filename'):
                        first = group_viols[0]
                        rtl_ctx = tracer.get_port_context(
                            first.get('filename', ''),
                            first.get('line', 1),
                            first.get('msg', '').split()[0] if first.get('msg') else module_name
                        ) or ''
                    else:
                        module_files = tracer.find_module_files(module_name)
                        if module_files:
                            try:
                                with open(module_files[0], errors='replace') as fh:
                                    lines = fh.readlines()
                                rtl_ctx = (f"// --- {module_files[0]} "
                                           f"({len(lines)} lines total, showing first 200) ---\n")
                                rtl_ctx += ''.join(lines[:200])
                            except Exception:
                                rtl_ctx = tracer.get_signal_context(
                                    group_viols[0].get('signal', module_name)
                                ) or ''

                viol_lines = []
                for v in group_viols:
                    if check_type == 'cdc':
                        viol_lines.append(
                            f"  ID={v.get('id','?')}  type={v.get('type','?')}  "
                            f"signal={v.get('signal','')}"
                        )
                    elif check_type == 'lint':
                        viol_lines.append(
                            f"  code={v.get('code','?')}  file={v.get('filename','?')}  "
                            f"line={v.get('line','?')}  msg={v.get('msg','')}"
                        )
                    else:
                        viol_lines.append(f"  {v.get('raw','')[:120]}")

                batch_input = (
                    f"IP: {ip}  Check: {check_type}  Module: {module_name}\n\n"
                    f"RTL SOURCE:\n"
                    f"{rtl_ctx if rtl_ctx else '(RTL not found for this module)'}\n\n"
                    f"VIOLATIONS IN THIS MODULE ({len(group_viols)}):\n"
                    + '\n'.join(viol_lines) + "\n\n"
                    "Analyze each violation using the RTL above. "
                    "For each violation, explain what the signal does in the design and why the "
                    "tool flagged it. Give a clear recommendation for the engineer to review."
                )

                with _sem:  # cap concurrent LLM calls to avoid gateway rate limiting
                    batch_output = self.call_agent('rtl_analyst', batch_input,
                                                   max_tokens=4096, tag=tag)
                print(f"    [RTL ANALYST] ✓ {module_name} ({len(group_viols)} violations)",
                      flush=True)
                all_analyses[module_name] = {
                    'violations':    group_viols,
                    'rtl_ctx_found': bool(rtl_ctx),
                    'analysis':      batch_output,
                }
            except Exception as exc:
                import traceback
                errors[module_name] = traceback.format_exc()
                all_analyses[module_name] = {
                    'violations':    group_viols,
                    'rtl_ctx_found': False,
                    'analysis':      f'[ERROR: {exc}]',
                }

        # Fire all module groups in parallel threads; join with per-thread timeout
        THREAD_TIMEOUT = 180  # seconds — bail on any single module that hangs
        threads = [
            threading.Thread(target=_analyze_module, args=(mn, gv), daemon=True)
            for mn, gv in module_groups.items()
        ]
        for t in threads:
            t.start()
        for t, (mn, _) in zip(threads, module_groups.items()):
            t.join(timeout=THREAD_TIMEOUT)
            if t.is_alive():
                print(f"    [RTL ANALYST] TIMEOUT waiting for module {mn} — skipping",
                      flush=True)
                all_analyses.setdefault(mn, {
                    'violations': module_groups[mn],
                    'rtl_ctx_found': False,
                    'analysis': '[TIMEOUT: LLM call exceeded 180s — gateway rate limited]',
                })

        if errors:
            for mod, tb in errors.items():
                print(f"    [RTL ANALYST] ERROR in {mod}: {tb[:200]}", flush=True)

        return all_analyses

    def _format_analysis_report(self, all_analyses, ip, tag, check_type):
        """
        Assemble RTL Analyst findings into a Markdown engineer feedback report.
        Returns report text string.
        """
        sep = '=' * 60
        lines = [
            f"# RTL Violation Analysis Report",
            f"**IP:** {ip}  **Check:** {check_type.upper()}  **Tag:** {tag}",
            f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"",
            f"> This report is generated by the Agent Team RTL Analyst.",
            f"> All decisions (waive / fix RTL) must be made by the responsible engineer.",
            f"> No waivers have been automatically applied.",
            f"",
            f"---",
            f"",
            f"## Summary",
            f"",
            f"| Module | Violations | RTL Found |",
            f"|--------|-----------|-----------|",
        ]

        total_viols = 0
        for module_name, data in all_analyses.items():
            n          = len(data['violations'])
            total_viols += n
            rtl_status = 'Yes' if data['rtl_ctx_found'] else 'No (not found)'
            lines.append(f"| `{module_name}` | {n} | {rtl_status} |")

        lines += [
            f"",
            f"**Total violations analyzed: {total_viols}**",
            f"",
            f"---",
            f"",
            f"## Per-Module Analysis",
            f"",
        ]

        for module_name, data in all_analyses.items():
            lines += [
                f"### Module: `{module_name}`",
                f"",
                f"**Violations ({len(data['violations'])}):**",
                f"",
            ]
            for v in data['violations']:
                if check_type == 'cdc':
                    lines.append(
                        f"- `{v.get('id','?')}` — type=`{v.get('type','?')}` "
                        f"— signal=`{v.get('signal','')}`"
                    )
                elif check_type == 'lint':
                    lines.append(
                        f"- `{v.get('code','?')}` — `{v.get('filename','?')}` "
                        f"line {v.get('line','?')} — `{v.get('msg','')}`"
                    )
                else:
                    lines.append(f"- `{v.get('raw','')[:100]}`")

            lines += [
                f"",
                f"**RTL Analysis:**",
                f"",
                data['analysis'],
                f"",
                f"---",
                f"",
            ]

        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # Per-check-type flows (new: RTL Analyst for ALL violations, no Fixer)
    # ------------------------------------------------------------------

    def _run_cdc(self, cli_instance, report_path, ip, tag, ref_dir):
        """Team Lead → RTL Analyst (ALL violations, batched by module) → Report.
        Returns (summary, report_file)."""
        violations    = cli_instance._parse_cdc_report(report_path, 'CDC Results')
        preconditions = cli_instance._parse_cdc_preconditions(report_path)

        if not violations:
            return (f"CDC ANALYSIS: No violations found in {report_path}\n", None)

        print(f"    [TEAM LEAD] CDC: {len(violations)} violations → RTL Analyst", flush=True)

        # RTL Analyst: analyze ALL violations, batched by module
        all_analyses = self._run_rtl_analysis_all(violations, ref_dir, ip, 'cdc', tag)

        # Format engineer feedback report
        report_text = self._format_analysis_report(all_analyses, ip, tag, 'cdc')

        # Build pre-condition section
        pc = preconditions or {}
        precond_lines = [
            f"## Pre-conditions",
            f"",
            f"| Item | Count |",
            f"|------|-------|",
            f"| Inferred Primary Clocks | {pc.get('inferred_clocks_primary', 0)} |",
            f"| Inferred Primary Resets | {pc.get('inferred_resets_primary', 0)} |",
            f"| Unresolved Modules | {pc.get('num_unresolved', 0)} |",
            f"| Blackboxes | {pc.get('num_blackboxes', 0)} |",
            f"",
        ]
        if pc.get('num_unresolved', 0) > 0:
            precond_lines.insert(
                0,
                f"> WARNING: {pc['num_unresolved']} unresolved module(s) "
                f"— violations may be unreliable\n"
            )

        # Insert pre-conditions after the summary section
        precond_text = '\n'.join(precond_lines)
        report_text  = report_text.replace(
            '---\n\n## Per-Module', precond_text + '---\n\n## Per-Module', 1
        )

        # Save report file
        report_file = os.path.join(self.base_dir, 'data', f'{tag}_rtl_analysis.md')
        with open(report_file, 'w') as fh:
            fh.write(report_text)
        print(f"    [TEAM LEAD] RTL analysis report → {os.path.basename(report_file)}",
              flush=True)

        sep     = '=' * 50
        summary = (
            f"MULTI-AGENT CDC ANALYSIS  (model: {self.model})\n{sep}\n\n"
            f"Total violations: {len(violations)}\n"
            f"Analysis report:  {report_file}\n\n"
            f"--- EXCERPT (first 3000 chars) ---\n"
            f"{report_text[:3000]}\n"
        )
        return summary, report_file

    def _run_lint(self, cli_instance, report_path, ip, tag, ref_dir=None):
        """Team Lead → RTL Analyst (ALL lint violations) → Report.
        Returns (summary, report_file)."""
        classified = cli_instance.classify_lint_violations(report_path)

        # Collect ALL violations (high + medium + low)
        all_viols = (classified.get('HIGH', []) +
                     classified.get('MEDIUM', []) +
                     classified.get('LOW', []))

        if not all_viols:
            return (f"LINT ANALYSIS: No unwaived violations found.\n", None)

        print(f"    [TEAM LEAD] Lint: {len(all_viols)} violations → RTL Analyst", flush=True)

        all_analyses = self._run_rtl_analysis_all(all_viols, ref_dir, ip, 'lint', tag)
        report_text  = self._format_analysis_report(all_analyses, ip, tag, 'lint')

        report_file = os.path.join(self.base_dir, 'data', f'{tag}_lint_rtl_analysis.md')
        with open(report_file, 'w') as fh:
            fh.write(report_text)
        print(f"    [TEAM LEAD] Lint analysis report → {os.path.basename(report_file)}",
              flush=True)

        sep     = '=' * 50
        summary = (
            f"MULTI-AGENT LINT ANALYSIS  (model: {self.model})\n{sep}\n\n"
            f"Total unwaived violations: {len(all_viols)}\n"
            f"Analysis report: {report_file}\n\n"
            f"--- EXCERPT ---\n{report_text[:3000]}\n"
        )
        return summary, report_file

    def _run_spg_dft(self, cli_instance, report_path, ip, tag, ref_dir=None):
        """Team Lead → RTL Analyst (ALL unfiltered SPG_DFT violations) → Report.
        Returns (summary, report_file)."""
        classified   = cli_instance.classify_spg_dft_violations(report_path, ip)
        unfiltered   = classified.get('unfiltered', [])
        filtered_cnt = len(classified.get('filtered', []))

        if not unfiltered:
            return (
                f"SPG_DFT ANALYSIS: All {filtered_cnt} violations already filtered.\n",
                None
            )

        # Normalise to common dict format
        viols = [
            {
                'raw':            v.get('raw', ''),
                'classification': v.get('classification', ''),
                'id':             v.get('raw', '')[:40],
            }
            for v in unfiltered
        ]

        print(f"    [TEAM LEAD] SPG_DFT: {len(viols)} unfiltered → RTL Analyst "
              f"({filtered_cnt} already filtered)", flush=True)

        all_analyses = self._run_rtl_analysis_all(viols, ref_dir, ip, 'spg_dft', tag)
        report_text  = self._format_analysis_report(all_analyses, ip, tag, 'spg_dft')

        # Prepend filter summary
        header = (
            f"## Filter Status\n\n"
            f"- Already filtered (known patterns): **{filtered_cnt}**\n"
            f"- Unfiltered (analyzed below): **{len(viols)}**\n\n---\n\n"
        )
        report_text = report_text.replace(
            '---\n\n## Per-Module', header + '---\n\n## Per-Module', 1
        )

        report_file = os.path.join(self.base_dir, 'data', f'{tag}_spgdft_rtl_analysis.md')
        with open(report_file, 'w') as fh:
            fh.write(report_text)
        print(f"    [TEAM LEAD] SPG_DFT analysis report → {os.path.basename(report_file)}",
              flush=True)

        sep     = '=' * 50
        summary = (
            f"MULTI-AGENT SPG_DFT ANALYSIS  (model: {self.model})\n{sep}\n\n"
            f"Filtered: {filtered_cnt} | Unfiltered: {len(viols)}\n"
            f"Analysis report: {report_file}\n\n"
            f"--- EXCERPT ---\n{report_text[:3000]}\n"
        )
        return summary, report_file

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def orchestrate(self, cli_instance, report_path, ip, tag, ref_dir, check_type='cdc'):
        """
        Full multi-agent orchestration.

          cdc / cdc_rdc    → RTL Analyst (ALL violations, batched by module) → _rtl_analysis.md
          lint             → RTL Analyst (ALL unwaived violations) → _lint_rtl_analysis.md
          spg_dft          → RTL Analyst (ALL unfiltered violations) → _spgdft_rtl_analysis.md
          full_static_check→ All three in parallel threads

        No auto-waivers are generated. Output is an engineer-readable RTL analysis report.
        Returns: (summary_text, report_file_path_or_None)
        """
        print(f"[TEAM LEAD] Multi-agent orchestration: {check_type}  ip={ip}", flush=True)

        if check_type == 'full_static_check':
            # ---- Parallel branch: spin up one thread per check type ----
            results = {}
            errors  = {}

            def _worker(ct):
                try:
                    rp = cli_instance._find_report_path(ref_dir, ip, ct)
                    if not rp or not os.path.exists(rp):
                        results[ct] = (f"No report found for {ct}\n", None)
                        return
                    sub_tag = f"{tag}_{ct}"
                    if ct == 'cdc':
                        s, wf = self._run_cdc(cli_instance, rp, ip, sub_tag, ref_dir)
                    elif ct == 'lint':
                        s, wf = self._run_lint(cli_instance, rp, ip, sub_tag, ref_dir=ref_dir)
                    else:
                        s, wf = self._run_spg_dft(cli_instance, rp, ip, sub_tag, ref_dir=ref_dir)
                    results[ct] = (s, wf)
                except Exception as exc:
                    import traceback
                    errors[ct]  = traceback.format_exc()
                    results[ct] = (f"{ct} agent failed: {exc}\n", None)

            threads = [threading.Thread(target=_worker, args=(ct,), daemon=True)
                       for ct in ('cdc', 'lint', 'spg_dft')]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            combined_summary = ""
            all_output_files = []
            for ct in ('cdc', 'lint', 'spg_dft'):
                s, wf = results.get(ct, ("no result", None))
                combined_summary += s + "\n\n"
                if wf:
                    all_output_files.append(wf)

            # Return CDC analysis report as primary file (all reports listed in summary)
            primary_report = next(
                (f for f in all_output_files if f.endswith('_rtl_analysis.md')), None
            )
            if not primary_report and all_output_files:
                primary_report = all_output_files[0]

            return combined_summary, primary_report

        elif check_type in ('cdc', 'cdc_rdc'):
            return self._run_cdc(cli_instance, report_path, ip, tag, ref_dir)

        elif check_type == 'lint':
            return self._run_lint(cli_instance, report_path, ip, tag, ref_dir=ref_dir)

        elif check_type in ('spg_dft', 'spyglass_dft'):
            return self._run_spg_dft(cli_instance, report_path, ip, tag, ref_dir=ref_dir)

        else:
            # Fallback for unknown check types — Analyzer only
            user_msg        = self._format_report_input(check_type, report_path, ip)
            analyzer_output = self.call_agent('analyzer', user_msg, max_tokens=4096, tag=tag)
            sep     = '=' * 50
            summary = (
                f"MULTI-AGENT {check_type.upper()} ANALYSIS  (model: {self.model})\n{sep}\n\n"
                f"[ANALYZER]\n{analyzer_output}\n"
            )
            return summary, None


class GenieCLI:
    def __init__(self, base_dir=None):
        if base_dir is None:
            # Default to the main_agent directory
            agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # Auto-detect user directory: if users/$USER exists, use it as base_dir
            username = os.environ.get('USER', os.environ.get('LOGNAME', ''))
            user_dir = os.path.join(agent_dir, 'users', username)
            if username and os.path.isdir(user_dir):
                self.base_dir = user_dir
            else:
                self.base_dir = agent_dir
        else:
            self.base_dir = base_dir

        self.keyword = {}
        self.instruction = {}
        self.instruction_orig = {}
        self.instruction_list = []  # Store all instructions with their vectors for best-match
        self.arguement = {}
        self.arguementInfo = {}
        self.arguement_canonical = {}  # lowercase → original-case key
        self.patterns = []
        self.oneHotDimension = 0
        self.vtoInfo = {'tile': '', 'disk': '', 'project': '', 'ip': '', 'vto': ''}
        self.debugger_emails = []
        self.ip_to_project = {}  # Map IP to project name (e.g., umc17_0 -> grimlock)

        # Load configuration files
        self._load_keyword()
        self._load_instruction()
        self._load_arguement()
        self._load_assignment()
        self._load_patterns()
        self._load_project_list()

    def _load_keyword(self):
        """Load keyword.csv and create one-hot encoding"""
        keyword_file = os.path.join(self.base_dir, 'keyword.csv')
        if not os.path.exists(keyword_file):
            print(f"ERROR: keyword.csv not found at {keyword_file}")
            return

        # First pass: count keywords for dimension
        with open(keyword_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for _ in reader:
                self.oneHotDimension += 1

        # Second pass: create one-hot vectors
        with open(keyword_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            k = 0
            for row in reader:
                oneHot = [0] * self.oneHotDimension
                oneHot[k] = 1
                k += 1
                for word in row:
                    if word:
                        self.keyword[word.lower().strip()] = oneHot

    def _load_instruction(self):
        """Load instruction.csv and map to scripts"""
        instruction_file = os.path.join(self.base_dir, 'instruction.csv')
        if not os.path.exists(instruction_file):
            print(f"ERROR: instruction.csv not found at {instruction_file}")
            return

        with open(instruction_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue

                instruction_text = row[0]
                script = row[1]

                # Parse instruction to one-hot vector
                words = re.sub('[?]', ' ', instruction_text).split()
                oneHotValue = [0] * self.oneHotDimension
                skip = 0

                for i in range(len(words)):
                    if skip == 1:
                        skip = 0
                        continue

                    word = words[i].lower()

                    # Check for phrase (two-word combination)
                    if i + 1 < len(words):
                        phrase = words[i].lower() + " " + words[i+1].lower()
                        if phrase in self.keyword:
                            oneHotValue = [a+b for a, b in zip(oneHotValue, self.keyword[phrase])]
                            skip = 1
                            continue

                    if word in self.keyword:
                        oneHotValue = [a+b for a, b in zip(oneHotValue, self.keyword[word])]

                self.instruction[bytes(oneHotValue)] = script
                self.instruction_orig[bytes(oneHotValue)] = instruction_text

                # Store for best-match lookup
                self.instruction_list.append({
                    'text': instruction_text,
                    'script': script,
                    'vector': list(oneHotValue),
                    'keyword_count': sum(oneHotValue)
                })

    def _load_arguement(self):
        """Load arguement.csv for parameter mapping"""
        arguement_file = os.path.join(self.base_dir, 'arguement.csv')
        if not os.path.exists(arguement_file):
            return

        with open(arguement_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    self.arguement[row[0]] = row[1]
                    self.arguementInfo[row[1]] = row[1]
                    # Store lowercase → original-case for case-insensitive lookup
                    key_lower = row[0].lower()
                    if key_lower not in self.arguement_canonical:
                        self.arguement_canonical[key_lower] = row[0]

    def _load_assignment(self):
        """Load assignment.csv for tile/project info"""
        assignment_file = os.path.join(self.base_dir, 'assignment.csv')
        if not os.path.exists(assignment_file):
            return

        with open(assignment_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                if re.search(r"tile$", row[0]):
                    self.vtoInfo['tile'] = self.vtoInfo['tile'] + ":" + row[1]
                if re.search(r"^disk$", row[0]):
                    self.vtoInfo['disk'] = self.vtoInfo['disk'] + ":" + row[1]
                if re.search(r"^project", row[0]):
                    self.vtoInfo['project'] = row[1]
                if re.search(r"^ip", row[0]):
                    self.vtoInfo['ip'] = row[1]
                if row[0] == 'debugger':
                    self.debugger_emails.append(row[1])
                if row[0] == 'vto' and row[1] != 'all':
                    self.vtoInfo['vto'] = row[1]

    def get_llm_key(self):
        """Return AMD LLM gateway key (llmKey row in assignment.csv)."""
        assignment_file = os.path.join(self.base_dir, 'assignment.csv')
        if os.path.exists(assignment_file):
            with open(assignment_file, encoding='utf-8-sig') as fh:
                reader = csv.reader(fh)
                for row in reader:
                    if len(row) >= 2 and row[0].strip() == 'llmKey':
                        return row[1].strip()
        return ''

    def get_api_key(self):
        """Return Anthropic API key.

        Priority:
          1. ANTHROPIC_API_KEY environment variable
          2. assignment.csv row: anthropic_api_key,<key>
        Returns empty string if not configured.
        """
        key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
        if key and not key.startswith('dummy'):
            return key
        assignment_file = os.path.join(self.base_dir, 'assignment.csv')
        if os.path.exists(assignment_file):
            with open(assignment_file, encoding='utf-8-sig') as fh:
                reader = csv.reader(fh)
                for row in reader:
                    if len(row) >= 2 and row[0].strip().lower() == 'anthropic_api_key':
                        return row[1].strip()
        return ''

    def _load_patterns(self):
        """Load patterns.csv for regex matching"""
        patterns_file = os.path.join(self.base_dir, 'patterns.csv')
        if not os.path.exists(patterns_file):
            return

        with open(patterns_file, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                pattern = row[0].strip()
                pattern_type = row[1].strip()
                flags = 0
                if len(row) > 2 and row[2].strip():
                    flag_str = row[2].strip().upper()
                    for char in flag_str:
                        if hasattr(re, char):
                            flags |= getattr(re, char)
                try:
                    compiled_re = re.compile(pattern, flags)
                    self.patterns.append((pattern, pattern_type, compiled_re))
                except re.error:
                    continue

    def _load_project_list(self):
        """Load project.list to map IP to project name (e.g., umc17_0 -> grimlock)"""
        project_file = os.path.join(self.base_dir, 'script', 'rtg_oss_feint', 'project.list')
        if not os.path.exists(project_file):
            return

        with open(project_file, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    ip = row[0].strip()
                    project_name = row[1].strip()
                    if ip and project_name:
                        self.ip_to_project[ip] = project_name

    def parse_instruction(self, instruction_text):
        """Parse instruction text and return matched script with arguments"""
        # Normalize instruction - but preserve = for params detection
        instruction_text = re.sub('[?]', ' ', instruction_text)

        # Extract PARAM = VALUE patterns and special patterns (config, waiver, constraint) BEFORE splitting
        # This handles multi-word values and preserves the full param line
        params_list = []
        controls_list = []
        config_list = []
        waiver_list = []
        constraint_list = []
        lint_waiver_list = []
        version_list = []
        spg_dft_params_list = []
        p4_file_list = []
        p4_description = ""
        net_name = ""

        # Split by newlines, commas, and " and " to handle multi-line input and inline params
        # Replace " and " with newline (but only when followed by a param pattern)
        # Also split on " with " to separate main instruction from params
        # IMPORTANT: Preserve commas inside curly braces {} (used in CDC waivers like -timestamp {11 March 2026 , 10:00:00})
        # First, temporarily replace commas inside {} with a placeholder
        def preserve_braces(text):
            result = []
            brace_depth = 0
            for char in text:
                if char == '{':
                    brace_depth += 1
                    result.append(char)
                elif char == '}':
                    brace_depth -= 1
                    result.append(char)
                elif char == ',' and brace_depth > 0:
                    result.append('\x00')  # Placeholder for comma inside braces
                else:
                    result.append(char)
            return ''.join(result)

        temp_text = preserve_braces(instruction_text)
        # Preserve commas within NetName: values (e.g. "NetName: A, B, C") before comma split
        temp_text = re.sub(r'(?i)(NetName:\s*[\w/]+)(\s*,\s*[\w/]+)+',
                           lambda m: m.group(0).replace(',', '\x01'), temp_text)
        temp_text = temp_text.replace(',', '\n')
        temp_text = temp_text.replace('\x00', ',')  # Restore commas inside braces
        # Split on " and " or " with " when they precede a PARAM = pattern
        temp_text = re.sub(r'\s+and\s+(?=[A-Z_][A-Z0-9_]*\s*=)', '\n', temp_text, flags=re.IGNORECASE)
        temp_text = re.sub(r'\s+with\s+(?=[A-Z_][A-Z0-9_]*\s*=)', '\n', temp_text, flags=re.IGNORECASE)

        lines = temp_text.split('\n')
        clean_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check for special patterns (config, waiver, constraint) using patterns.csv
            # Config pattern: UPPERCASE_NAME: value (e.g., ENABLE_TECHIND_CDCFEPM: 1)
            # Can appear at start of line OR at end of instruction line
            config_match = re.search(r'([A-Z][A-Z0-9_]+):\s+(\S+.*)$', line)
            if config_match:
                config_entry = config_match.group(0).strip()
                config_list.append(config_entry)
                print(f"# Detected config: {config_entry}")
                # Keep the part before the config for further processing
                remaining = line[:config_match.start()].strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # CDC waiver pattern: cdc report crossing|item ...
            waiver_match = re.search(r'((?:cdc|resetcheck)\s+report\s+(?:crossing|item).*)$', line, re.I)
            if waiver_match:
                waiver_list.append(waiver_match.group(1).strip())
                print(f"# Detected waiver: {waiver_match.group(1).strip()}")
                remaining = line[:waiver_match.start()].strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # CDC constraint pattern: netlist clock|constant|port|blackbox|memory|reset ...
            constraint_match = re.search(r'(netlist\s+(?:clock|constant|port|blackbox|memory|reset).*)$', line, re.I)
            if constraint_match:
                constraint_list.append(constraint_match.group(1).strip())
                print(f"# Detected constraint: {constraint_match.group(1).strip()}")
                remaining = line[:constraint_match.start()].strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # Lint waiver pattern: error:|filename:|code:|msg:|line:|column:|reason:|author:
            lint_match = re.search(r'((?:error|filename|code|msg|line|column|reason|author)\s*:.*)$', line, re.I)
            if lint_match:
                lint_waiver_list.append(lint_match.group(1).strip())
                print(f"# Detected lint waiver: {lint_match.group(1).strip()}")
                remaining = line[:lint_match.start()].strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # Version pattern: CDC_Verif/X.X.X or 0in/X.X.X
            version_match = re.search(r'((?:CDC_Verif|0in)/[\d._]+)(?:\s|$)', line, re.I)
            if version_match:
                version_list.append(version_match.group(1).strip())
                print(f"# Detected version: {version_match.group(1).strip()}")
                remaining = line[:version_match.start()].strip() + ' ' + line[version_match.end():].strip()
                remaining = remaining.strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # SPG_DFT params pattern: SPGDFT_PARAM...
            spg_dft_match = re.search(r'(SPGDFT_[A-Z0-9_]+(?:\s*=\s*\S+)?)', line, re.I)
            if spg_dft_match:
                spg_dft_params_list.append(spg_dft_match.group(1).strip())
                print(f"# Detected SPG_DFT param: {spg_dft_match.group(1).strip()}")
                remaining = line[:spg_dft_match.start()].strip() + ' ' + line[spg_dft_match.end():].strip()
                remaining = remaining.strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # P4 file pattern: src/... or _env/... (can have multiple in one line)
            p4_file_matches = re.findall(r'((?:src|_env)/\S+)', line)
            if p4_file_matches:
                for p4_file in p4_file_matches:
                    p4_file_list.append(p4_file.strip())
                    print(f"# Detected P4 file: {p4_file.strip()}")
                # Remove all matched P4 files from line
                remaining = re.sub(r'(?:src|_env)/\S+', '', line).strip()
                remaining = ' '.join(remaining.split())  # Normalize whitespace
                if remaining:
                    clean_lines.append(remaining)
                continue

            # P4 description pattern: Description: ...
            p4_desc_match = re.search(r'Description:\s*(.+)$', line, re.I)
            if p4_desc_match:
                p4_description = p4_desc_match.group(1).strip()
                print(f"# Detected P4 description: {p4_description}")
                remaining = line[:p4_desc_match.start()].strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # NetName pattern: NetName: <net_suffix>
            net_name_match = re.search(r'NetName:\s*([\w/]+(?:\s*[\x01,]\s*[\w/]+)*)', line, re.I)
            if net_name_match:
                net_name = re.sub(r'\s*[\x01,]\s*', ',', net_name_match.group(1).strip())
                print(f"# Detected NetName: {net_name}")
                remaining = (line[:net_name_match.start()] + ' ' + line[net_name_match.end():]).strip()
                if remaining:
                    clean_lines.append(remaining)
                continue

            # Check if this line contains a PARAM = VALUE pattern
            if '=' in line:
                # Try to extract PARAM = VALUE from the line
                # Handle case where param might be at the end of a line with other text
                param_match = re.search(r'([A-Z_][A-Z0-9_]*)\s*=\s*(\S+.*?)$', line, re.IGNORECASE)
                if param_match:
                    param_name = param_match.group(1).upper()
                    param_value = param_match.group(2).strip()

                    # Strip trailing :> (closing tag from <: ... :> block)
                    if param_value.endswith(':>'):
                        param_value = param_value[:-2].strip()

                    # Check if this is a known param or control
                    is_param = False
                    is_control = False

                    if param_name in self.arguement:
                        param_type = self.arguement[param_name]
                        is_param = (param_type == 'params')
                        is_control = (param_type == 'controls')
                    elif param_name.lower() in self.arguement:
                        param_type = self.arguement[param_name.lower()]
                        is_param = (param_type == 'params')
                        is_control = (param_type == 'controls')

                    if is_param:
                        full_param = f"{param_name} = {param_value}"
                        params_list.append(full_param)
                        print(f"# Detected param: {full_param}")
                        # Remove the param from the line for clean_lines
                        remaining = line[:param_match.start()].strip()
                        if remaining:
                            clean_lines.append(remaining)
                        continue
                    elif is_control:
                        full_control = f"{param_name} = {param_value}"
                        controls_list.append(full_control)
                        print(f"# Detected control: {full_control}")
                        remaining = line[:param_match.start()].strip()
                        if remaining:
                            clean_lines.append(remaining)
                        continue

            clean_lines.append(line)

        # Rejoin cleaned instruction (without param lines)
        instruction_text = ' '.join(clean_lines)

        words = instruction_text.split()
        oneHotValue = [0] * self.oneHotDimension
        skip = 0

        # Initialize argument info (aligned with vtoHybridModel.py)
        arguementInfo = {
            'tile': 'tile', 'file': 'file', 'p4File': 'p4File', 'csh': 'csh', 'perl': 'perl',
            'runDir': 'runDir', 'refDir': 'refDir', 'target': 'target', 'noLsf': 'noLsf',
            'digit': 'digit', 'date': 'date', 'integer': 'integer', 'unit': 'unit', 'repeat': 'repeat',
            'regu': 'regu', 'table': 'table', 'preposition': 'preposition',
            'clk': 'clk', 'pvt': 'pvt', 'block': 'block', 'role': 'role',
            'stage': 'stage', 'cmds': 'cmds', 'snpstcl': 'snpstcl',
            'analogip': 'analogip', 'sram': 'sram', 'std': 'std', 'edatool': 'edatool',
            'checkType': 'checkType', 'updateType': 'updateType',
            'params': 'params', 'controls': 'controls', 'tune': 'tune', 'tag': 'tag',
            'netName': 'netName',
            'disk': self.vtoInfo['disk'], 'project': self.vtoInfo['project'],
            'ownTiles': self.vtoInfo['tile'], 'ip': self.vtoInfo.get('ip', 'ip')
        }

        for i in range(len(words)):
            if skip == 1:
                skip = 0
                continue

            word = words[i]
            word_lower = word.lower()

            # Check for phrase
            if i + 1 < len(words):
                phrase = word_lower + " " + words[i+1].lower()
                if phrase in self.keyword:
                    oneHotValue = [a+b for a, b in zip(oneHotValue, self.keyword[phrase])]
                    skip = 1
                    continue

            # Helper function to check and set argument from arguement.csv
            def check_arguement_csv(w, w_lower):
                # Check arguement.csv for special types (target, params, tune, etc.)
                # Try exact lowercase, exact original, then case-insensitive via canonical
                if w_lower in self.arguement:
                    lookup_key, entry = w_lower, w_lower
                elif w in self.arguement:
                    lookup_key, entry = w, w
                elif w_lower in self.arguement_canonical:
                    # Use canonical (correct-case) key from CSV
                    lookup_key = self.arguement_canonical[w_lower]
                    entry = lookup_key
                else:
                    return False
                value = self.arguement[lookup_key]
                if value in arguementInfo:
                    # For 'ip' type, replace instead of accumulate (only one IP per instruction)
                    if value == 'ip':
                        arguementInfo[value] = value + ":" + entry
                    elif (":" + entry) not in arguementInfo[value]:
                        arguementInfo[value] = arguementInfo[value] + ":" + entry
                    return True
                return False

            # Helper function to check special type keywords
            def check_special_types(w_lower):
                # Check type keywords (cdc_rdc, lint, etc.) - avoid duplicates
                if w_lower in ['cdc_rdc', 'lint', 'spg_dft', 'build_rtl', 'full_static_check']:
                    if (":" + w_lower) not in arguementInfo['checkType']:
                        arguementInfo['checkType'] = arguementInfo['checkType'] + ":" + w_lower
                # Update type keywords (waiver, constraint, etc.)
                if w_lower in ['waiver', 'constraint', 'config', 'version']:
                    if (":" + w_lower) not in arguementInfo['updateType']:
                        arguementInfo['updateType'] = arguementInfo['updateType'] + ":" + w_lower

            # Check for keyword
            if word_lower in self.keyword:
                oneHotValue = [a+b for a, b in zip(oneHotValue, self.keyword[word_lower])]
                # Also check arguement.csv and special types even for keywords
                check_arguement_csv(word, word_lower)
                check_special_types(word_lower)
                continue

            # Check for arguments
            # Directory path
            if re.search(r'^/[a-zA-Z].*', word):
                if os.path.isdir(word) or os.path.isdir(word.rstrip('/')):
                    arguementInfo['refDir'] = arguementInfo['refDir'] + ":" + word.rstrip('/')
                else:
                    arguementInfo['file'] = arguementInfo['file'] + ":" + word
                continue

            # Tune path (any path starting with tune/)
            if re.search(r'^tune/', word, re.I):
                if (":" + word) not in arguementInfo['tune']:
                    arguementInfo['tune'] = arguementInfo['tune'] + ":" + word
                continue

            # P4 depot path (starting with //)
            if re.search(r'^//depot/', word, re.I):
                arguementInfo['p4File'] = arguementInfo['p4File'] + ":" + word
                continue

            # Tile names (check against known tiles)
            for tile in self.vtoInfo['tile'].split(':'):
                if tile and word == tile:
                    arguementInfo['tile'] = arguementInfo['tile'] + ":" + word

            # Check arguement.csv for target, params, tune, etc.
            check_arguement_csv(word, word_lower)

            # Integer
            if re.search(r'^[0-9]+$', word):
                arguementInfo['integer'] = arguementInfo['integer'] + ":" + word

            # Digit (decimal number like 1.5, 2.0)
            if re.search(r'^[0-9]+\.[0-9]+$', word):
                arguementInfo['digit'] = arguementInfo['digit'] + ":" + word

            # Date (YYYY-MM-DD format)
            if re.search(r'^\d{4}-\d{2}-\d{2}$', word):
                arguementInfo['date'] = arguementInfo['date'] + ":" + word

            # Memory pattern (e.g., 64x32)
            if re.search(r'^[0-9]+x[0-9]+$', word):
                if 'mem' not in arguementInfo:
                    arguementInfo['mem'] = 'mem'
                arguementInfo['mem'] = arguementInfo['mem'] + ":" + word

            # Regex/wildcard pattern (contains *)
            if re.search(r'\S*\*\S*', word):
                arguementInfo['regu'] = arguementInfo['regu'] + ":" + word

            # Check patterns.csv for additional pattern matching
            for pattern, pattern_type, compiled_re in self.patterns:
                if compiled_re.fullmatch(word):
                    if pattern_type in arguementInfo:
                        if (":" + word) not in arguementInfo[pattern_type]:
                            arguementInfo[pattern_type] = arguementInfo[pattern_type] + ":" + word

            # Check special type keywords
            check_special_types(word_lower)

        # Look up instruction - first try exact match
        script = None
        matched_instruction = None

        if bytes(oneHotValue) in self.instruction:
            script = self.instruction[bytes(oneHotValue)]
            matched_instruction = self.instruction_orig[bytes(oneHotValue)]
        else:
            # Use best-match: find instruction with highest overlap
            best_match = None
            best_score = 0

            for instr in self.instruction_list:
                # Calculate overlap: how many keywords from instruction are in user input
                instr_vec = instr['vector']
                # Overlap = keywords that are in BOTH user input AND instruction
                overlap = sum(min(a, b) for a, b in zip(oneHotValue, instr_vec))
                # Score = overlap / instruction keywords (how much of instruction is covered)
                if instr['keyword_count'] > 0:
                    coverage = overlap / instr['keyword_count']
                    # Bonus for having more overlapping keywords
                    score = coverage * overlap

                    if score > best_score and coverage >= 0.5:  # At least 50% coverage
                        best_score = score
                        best_match = instr

            if best_match:
                script = best_match['script']
                matched_instruction = best_match['text']

        # Populate netName into arguementInfo if detected
        if net_name:
            arguementInfo['netName'] = 'netName:' + net_name

        # Bundle all special content lists
        special_content = {
            'params': params_list,
            'controls': controls_list,
            'config': config_list,
            'waiver': waiver_list,
            'constraint': constraint_list,
            'lint_waiver': lint_waiver_list,
            'version': version_list,
            'spg_dft_params': spg_dft_params_list,
            'p4_file': p4_file_list,
            'p4_description': p4_description,
            'net_name': net_name
        }
        return script, matched_instruction, arguementInfo, special_content

    def build_command(self, script, arguementInfo):
        """Build the full command with arguments"""
        if not script:
            return None

        # Parse script for argument placeholders
        command_parts = script.split()
        final_command = []

        for part in command_parts:
            if part.startswith('$'):
                arg_name = part[1:]
                if arg_name in arguementInfo and arguementInfo[arg_name] != arg_name:
                    final_command.append(arguementInfo[arg_name])
                else:
                    # Use placeholder with arg name for undefined variables (csh doesn't handle "" well)
                    final_command.append(f'{arg_name}:')
            else:
                final_command.append(part)

        return ' '.join(final_command)

    def generate_tag(self):
        """Generate a unique tag for this task"""
        return datetime.datetime.now().strftime('%Y%m%d%H%M%S')

    def execute(self, instruction_text, dry_run=False, send_email=False, use_xterm=False, analyze_mode=False, fixer_mode=False, email_to=None):
        """Parse instruction and execute the corresponding script"""
        print(f"=" * 70)
        print(f"Genie CLI - Processing Instruction")
        print(f"=" * 70)
        print(f"Input: {instruction_text}")
        print()

        script, matched_instruction, arguementInfo, special_content = self.parse_instruction(instruction_text)

        # Extract from special_content dict
        params_list = special_content.get('params', [])
        controls_list = special_content.get('controls', [])
        config_list = special_content.get('config', [])
        waiver_list = special_content.get('waiver', [])
        constraint_list = special_content.get('constraint', [])
        lint_waiver_list = special_content.get('lint_waiver', [])
        version_list = special_content.get('version', [])
        spg_dft_params_list = special_content.get('spg_dft_params', [])
        p4_file_list = special_content.get('p4_file', [])
        p4_description = special_content.get('p4_description', '')
        net_name = special_content.get('net_name', '')

        if not script:
            print("ERROR: Could not match instruction to any known command")
            print("\nTry '/agent --list' to see available instructions")
            return None

        print(f"Matched: {matched_instruction}")
        print(f"Script: {script}")
        print()

        # Generate tag
        tag = self.generate_tag()
        arguementInfo['tag'] = tag

        # Special handler: analyze_fixer_only — no script to run, just create _analyze and signal (fixer mode)
        if script.startswith('analyze_fixer_only'):
            ref_dir_raw = arguementInfo.get('refDir', '')
            ref_dir = ref_dir_raw.replace('refDir:', '').strip(':') if ref_dir_raw != 'refDir' else ''
            ip_raw = arguementInfo.get('ip', '')
            ip = ip_raw.replace('ip:', '').strip(':') if ip_raw != 'ip' else ''
            check_type_raw = arguementInfo.get('checkType', '')
            check_type = check_type_raw.replace('checkType:', '').strip(':') if check_type_raw != 'checkType' else 'cdc_rdc'

            analyze_flag_file = os.path.join(self.base_dir, 'data', f'{tag}_analyze')
            os.makedirs(os.path.join(self.base_dir, 'data'), exist_ok=True)
            with open(analyze_flag_file, 'w') as f:
                f.write(f"check_type={check_type}\n")
                f.write(f"ref_dir={ref_dir}\n")
                f.write(f"ip={ip}\n")
                f.write(f"log_file={self.base_dir}/runs/{tag}.log\n")
                f.write(f"spec_file={self.base_dir}/data/{tag}_spec\n")
                f.write(f"fixer_mode=true\n")

            return {
                'tag': tag,
                'analyze_only': True,
                'analyze_fixer_only': True,
                'args': arguementInfo,
            }

        # Special handler: analyze_only — no script to run, just create _analyze and signal
        if script.startswith('analyze_only'):
            ref_dir_raw = arguementInfo.get('refDir', '')
            ref_dir = ref_dir_raw.replace('refDir:', '').strip(':') if ref_dir_raw != 'refDir' else ''
            ip_raw = arguementInfo.get('ip', '')
            ip = ip_raw.replace('ip:', '').strip(':') if ip_raw != 'ip' else ''
            check_type_raw = arguementInfo.get('checkType', '')
            check_type = check_type_raw.replace('checkType:', '').strip(':') if check_type_raw != 'checkType' else 'full_static_check'

            analyze_flag_file = os.path.join(self.base_dir, 'data', f'{tag}_analyze')
            os.makedirs(os.path.join(self.base_dir, 'data'), exist_ok=True)
            with open(analyze_flag_file, 'w') as f:
                f.write(f"check_type={check_type}\n")
                f.write(f"ref_dir={ref_dir}\n")
                f.write(f"ip={ip}\n")
                f.write(f"log_file={self.base_dir}/runs/{tag}.log\n")
                f.write(f"spec_file={self.base_dir}/data/{tag}_spec\n")

            return {
                'tag': tag,
                'analyze_only': True,
                'args': arguementInfo,
            }

        # Build command
        command = self.build_command(script, arguementInfo)
        print(f"Command: source script/{command}")
        print(f"Tag: {tag}")
        print()

        # Show extracted arguments
        print("Extracted Arguments:")
        for key, value in arguementInfo.items():
            if value != key:  # Only show arguments with values
                print(f"  {key}: {value}")

        # Show detected special content
        if params_list:
            print(f"  params_file: data/{tag}.params ({len(params_list)} entries)")
            for p in params_list:
                print(f"    - {p}")
        if controls_list:
            print(f"  controls_file: data/{tag}.controls ({len(controls_list)} entries)")
            for c in controls_list:
                print(f"    - {c}")
        if config_list:
            print(f"  config_file: data/{tag}.cdc_rdc_config ({len(config_list)} entries)")
            for c in config_list:
                print(f"    - {c}")
        if waiver_list:
            print(f"  waiver_file: data/{tag}.cdc_rdc_waiver ({len(waiver_list)} entries)")
            for w in waiver_list:
                print(f"    - {w}")
        if constraint_list:
            print(f"  constraint_file: data/{tag}.cdc_rdc_constraint ({len(constraint_list)} entries)")
            for c in constraint_list:
                print(f"    - {c}")
        if lint_waiver_list:
            print(f"  lint_waiver_file: data/{tag}.lint_waiver ({len(lint_waiver_list)} entries)")
            for l in lint_waiver_list:
                print(f"    - {l}")
        if version_list:
            print(f"  version_file: data/{tag}.cdc_rdc_version ({len(version_list)} entries)")
            for v in version_list:
                print(f"    - {v}")
        if spg_dft_params_list:
            print(f"  spg_dft_params_file: data/{tag}.spg_dft_params ({len(spg_dft_params_list)} entries)")
            for s in spg_dft_params_list:
                print(f"    - {s}")
        if p4_file_list:
            print(f"  p4_file_list: data/{tag}.p4_files ({len(p4_file_list)} entries)")
            for p in p4_file_list:
                print(f"    - {p}")
        if p4_description:
            print(f"  p4_description: {p4_description}")
        if net_name:
            print(f"  net_name: {net_name}")
        print()

        if dry_run:
            print("[DRY RUN] Command not executed")
            return {'tag': tag, 'command': command, 'script': script, 'args': arguementInfo, 'special_content': special_content}

        # Create data directory for this tag
        data_dir = os.path.join(self.base_dir, 'data', tag)
        os.makedirs(data_dir, exist_ok=True)

        # Write params file if we have params
        if params_list:
            params_file = os.path.join(self.base_dir, 'data', f'{tag}.params')
            with open(params_file, 'w') as f:
                for param in params_list:
                    f.write(param + '\n')
            print(f"Created params file: {params_file}")

        # Write config file if we have config entries
        if config_list:
            config_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_config')
            with open(config_file, 'w') as f:
                for config in config_list:
                    f.write(config + '\n')
            print(f"Created config file: {config_file}")

        # Write waiver file if we have waiver entries
        if waiver_list:
            waiver_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_waiver')
            with open(waiver_file, 'w') as f:
                for waiver in waiver_list:
                    f.write(waiver + '\n')
            print(f"Created waiver file: {waiver_file}")

        # Write constraint file if we have constraint entries
        if constraint_list:
            constraint_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_constraint')
            with open(constraint_file, 'w') as f:
                for constraint in constraint_list:
                    f.write(constraint + '\n')
            print(f"Created constraint file: {constraint_file}")

        # Write lint waiver file if we have lint waiver entries
        if lint_waiver_list:
            lint_waiver_file = os.path.join(self.base_dir, 'data', f'{tag}.lint_waiver')
            with open(lint_waiver_file, 'w') as f:
                for lint_waiver in lint_waiver_list:
                    f.write(lint_waiver + '\n')
            print(f"Created lint waiver file: {lint_waiver_file}")

        # Write controls file if we have controls
        if controls_list:
            controls_file = os.path.join(self.base_dir, 'data', f'{tag}.controls')
            with open(controls_file, 'w') as f:
                for control in controls_list:
                    f.write(control + '\n')
            print(f"Created controls file: {controls_file}")

        # Write version file if we have version entries
        if version_list:
            version_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_version')
            with open(version_file, 'w') as f:
                for version in version_list:
                    f.write(version + '\n')
            print(f"Created version file: {version_file}")

        # Write SPG_DFT params file if we have spg_dft_params entries
        if spg_dft_params_list:
            spg_dft_file = os.path.join(self.base_dir, 'data', f'{tag}.spg_dft_params')
            with open(spg_dft_file, 'w') as f:
                for spg_param in spg_dft_params_list:
                    f.write(spg_param + '\n')
            print(f"Created SPG_DFT params file: {spg_dft_file}")

        # Write P4 files list if we have p4_file entries
        if p4_file_list:
            p4_files_file = os.path.join(self.base_dir, 'data', f'{tag}.p4_files')
            with open(p4_files_file, 'w') as f:
                for p4_file in p4_file_list:
                    f.write(p4_file + '\n')
            print(f"Created P4 files list: {p4_files_file}")

        # Write P4 description if we have one
        if p4_description:
            p4_desc_file = os.path.join(self.base_dir, 'data', f'{tag}.p4_description')
            with open(p4_desc_file, 'w') as f:
                f.write(p4_description + '\n')
            print(f"Created P4 description file: {p4_desc_file}")

        # Create spec file (empty - will be populated by script)
        spec_file = os.path.join(self.base_dir, 'data', f'{tag}_spec')
        with open(spec_file, 'w') as f:
            pass  # Create empty file, script will populate with results

        # Create metadata file for email subject
        metadata_file = os.path.join(self.base_dir, 'data', f'{tag}_metadata')
        with open(metadata_file, 'w') as f:
            # Extract meaningful info for email subject
            # Handle cases where value equals the key name (placeholder)
            check_type_raw = arguementInfo.get('checkType', '')
            check_type = check_type_raw.replace('checkType:', '').strip(':') if check_type_raw != 'checkType' else ''

            update_type_raw = arguementInfo.get('updateType', '')
            update_type = update_type_raw.replace('updateType:', '').strip(':') if update_type_raw != 'updateType' else ''

            ref_dir_raw = arguementInfo.get('refDir', '')
            ref_dir = ref_dir_raw.replace('refDir:', '').strip(':') if ref_dir_raw != 'refDir' else ''

            tile_raw = arguementInfo.get('tile', '')
            tile = tile_raw.replace('tile:', '').strip(':') if tile_raw != 'tile' else ''

            target_raw = arguementInfo.get('target', '')
            target = target_raw.replace('target:', '').strip(':') if target_raw != 'target' else ''

            # Determine task type for subject based on script name
            task_type = ''
            script_lower = script.lower()

            if 'summary' in script_lower or 'summarize' in script_lower:
                task_type = 'static_check_summary'
            elif 'static_check' in script_lower:
                task_type = check_type or 'static_check'
            elif 'update_cdc' in script_lower or 'update_lint' in script_lower:
                task_type = f"{check_type}_{update_type}" if check_type and update_type else update_type or check_type or 'update'
            elif 'update_spg_dft' in script_lower:
                task_type = 'spg_dft_update'
            elif 'tilebuilder' in script_lower or 'make_tilebuilder' in script_lower:
                task_type = target or 'TileBuilder'
            elif 'tb_branch' in script_lower or 'branch' in script_lower:
                task_type = 'branch'
            elif 'formality' in script_lower:
                task_type = 'formality_report'
            elif 'timing' in script_lower:
                task_type = 'timing_report'
            elif 'utilization' in script_lower:
                task_type = 'utilization_report'
            elif 'monitor' in script_lower:
                task_type = 'monitor'
            elif 'check_cl' in script_lower:
                task_type = 'check_changelist'
            elif 'sync_tree' in script_lower:
                task_type = 'sync_tree'
            elif 'submit' in script_lower:
                task_type = 'p4_submit'
            elif 'clock_reset_analyzer' in script_lower or 'clock_reset' in script_lower:
                task_type = 'clock_reset_analysis'
            elif 'find_equivalent_nets' in script_lower:
                task_type = 'find_equivalent_nets'
            else:
                task_type = check_type or update_type or 'task'

            # Get short directory name
            dir_name = os.path.basename(ref_dir) if ref_dir else ''

            # Get IP name and project name
            ip_raw = arguementInfo.get('ip', '')
            ip = ip_raw.replace('ip:', '').strip(':') if ip_raw != 'ip' else ''
            project_name = self.ip_to_project.get(ip, '') if ip else ''

            f.write(f"task_type={task_type}\n")
            f.write(f"tile={tile}\n")
            f.write(f"dir_name={dir_name}\n")
            f.write(f"ref_dir={ref_dir}\n")
            f.write(f"ip={ip}\n")
            f.write(f"project_name={project_name}\n")
            f.write(f"instruction={matched_instruction}\n")

        # Create run script
        run_script = os.path.join(self.base_dir, 'runs', f'{tag}.csh')
        os.makedirs(os.path.dirname(run_script), exist_ok=True)

        # Detect if this is a TileBuilder/supra command that needs different environment
        is_tilebuilder_cmd = any(keyword in script.lower() for keyword in [
            'tilebuilder', 'supra', 'tb_branch', 'make_tilebuilder',
            'synthesis_timing', 'report_utilization', 'monitor_tilebuilder'
        ])

        with open(run_script, 'w') as f:
            f.write("#!/bin/tcsh -f\n")
            f.write(f"# Genie CLI generated script\n")
            f.write(f"# Tag: {tag}\n")
            # Handle multi-line instructions - comment each line
            instruction_commented = instruction_text.replace('\n', '\n# ')
            f.write(f"# Instruction: {instruction_commented}\n\n")
            f.write(f"cd {self.base_dir}\n")

            if is_tilebuilder_cmd:
                # TileBuilder commands need cpd.cshrc environment (conflicts with cbwa)
                f.write(f"# Using TileBuilder-compatible environment (cpd.cshrc)\n")
                f.write(f"source /tool/aticad/1.0/src/sysadmin/cpd.cshrc\n")
            else:
                f.write(f"source csh/env.csh\n")

            f.write(f"set tag = {tag}\n")
            f.write(f"set tasksModelFile = tasksModelCLI.csv\n")

            # Execute main script in subshell to prevent exit on failure
            # Using ( ) subshell preserves environment and catches exit
            f.write(f"\n# Execute main script in subshell (continues even on failure)\n")
            f.write(f"( source script/{command} )\n")

            f.write(f"set script_status = $status\n")
            f.write(f"echo 'Script exit status:' $script_status\n")
            f.write(f"\n# Always send email if flag file exists (even on failure)\n")
            f.write(f"if (-f {self.base_dir}/data/{tag}_email) then\n")
            f.write(f"    python3 {self.base_dir}/script/genie_cli.py --send-completion-email {tag}\n")
            f.write(f"endif\n")
            # Note: finishing_task.csh is called by individual scripts internally, not from here

        # Create email flag file if email is requested
        if send_email and self.debugger_emails:
            email_flag_file = os.path.join(self.base_dir, 'data', f'{tag}_email')
            with open(email_flag_file, 'w') as f:
                f.write(','.join(self.debugger_emails))
            print(f"Email will be sent to: {', '.join(self.debugger_emails)}")
            # Also save analysis email recipients separately — _email is deleted after
            # completion email is sent, so _analysis_email persists for --send-analysis-email
            if analyze_mode:
                analysis_email_file = os.path.join(self.base_dir, 'data', f'{tag}_analysis_email')
                with open(analysis_email_file, 'w') as f:
                    f.write(','.join(self.debugger_emails))

        # Create analyze flag file if analyze mode is requested
        if analyze_mode:
            analyze_flag_file = os.path.join(self.base_dir, 'data', f'{tag}_analyze')
            check_type_raw = arguementInfo.get('checkType', '')
            check_type = check_type_raw.replace('checkType:', '').strip(':') if check_type_raw != 'checkType' else ''
            ref_dir_raw = arguementInfo.get('refDir', '')
            ref_dir = ref_dir_raw.replace('refDir:', '').strip(':') if ref_dir_raw != 'refDir' else ''
            ip_raw = arguementInfo.get('ip', '')
            ip = ip_raw.replace('ip:', '').strip(':') if ip_raw != 'ip' else ''
            with open(analyze_flag_file, 'w') as f:
                f.write(f"check_type={check_type or 'full_static_check'}\n")
                f.write(f"ref_dir={ref_dir}\n")
                f.write(f"ip={ip}\n")
                f.write(f"log_file={self.base_dir}/runs/{tag}.log\n")
                f.write(f"spec_file={self.base_dir}/data/{tag}_spec\n")
                if fixer_mode:
                    f.write(f"fixer_mode=true\n")

            # Write fixer state file if in fixer mode
            if fixer_mode:
                fixer_state_file = os.path.join(self.base_dir, 'data', f'{tag}_fixer_state')
                with open(fixer_state_file, 'w') as f:
                    f.write(f"original_ref_dir={ref_dir}\n")
                    f.write(f"original_ip={ip}\n")
                    f.write(f"original_check_type={check_type or 'full_static_check'}\n")
                    f.write(f"original_instruction={instruction_text}\n")
                    f.write(f"round=1\n")
                    f.write(f"max_rounds=5\n")
                    f.write(f"parent_tag=\n")
                    f.write(f"use_xterm={'true' if use_xterm else 'false'}\n")
                    f.write(f"use_email={'true' if send_email else 'false'}\n")
                    f.write(f"email_to={email_to or ''}\n")
            print(f"Analyze mode enabled: Claude Code will monitor and analyze results")

        # Write debug info file — records all execution parameters for diagnosis
        debug_file = os.path.join(self.base_dir, 'data', f'{tag}_debug')
        with open(debug_file, 'w') as f:
            f.write(f"=== Genie CLI Debug Info ===\n")
            f.write(f"timestamp={datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"tag={tag}\n")
            f.write(f"\n--- Instruction ---\n")
            instruction_oneline = instruction_text.replace('\n', ' | ')
            f.write(f"raw_instruction={instruction_oneline}\n")
            f.write(f"matched_instruction={matched_instruction}\n")
            f.write(f"script={script}\n")
            f.write(f"command={command}\n")
            f.write(f"\n--- Execution Mode ---\n")
            f.write(f"xterm={'true' if use_xterm else 'false'}\n")
            f.write(f"dry_run=false\n")
            f.write(f"\n--- Email ---\n")
            if not send_email:
                f.write(f"email=disabled\n")
            elif email_to:
                f.write(f"email=override\n")
                f.write(f"email_to={email_to}\n")
            else:
                f.write(f"email=to_debuggers\n")
                f.write(f"email_to={','.join(self.debugger_emails)}\n")
            f.write(f"\n--- Analyze / Fixer ---\n")
            f.write(f"analyze_mode={'true' if analyze_mode else 'false'}\n")
            f.write(f"fixer_mode={'true' if fixer_mode else 'false'}\n")
            f.write(f"\n--- Extracted Arguments ---\n")
            for key, value in arguementInfo.items():
                if value and value != key:
                    f.write(f"{key}={value}\n")
            if params_list:
                f.write(f"params_count={len(params_list)}\n")
            if waiver_list:
                f.write(f"waiver_count={len(waiver_list)}\n")
            if constraint_list:
                f.write(f"constraint_count={len(constraint_list)}\n")
            if net_name:
                f.write(f"net_name={net_name}\n")

        print(f"Created run script: {run_script}")
        print(f"Created data directory: {data_dir}")

        if dry_run:
            print()
            print("To execute:")
            print(f"  cd {self.base_dir} && source {run_script}")
            print()
            print("Or with xterm:")
            print(f"  xterm -e 'cd {self.base_dir} && source {run_script}' &")
        elif use_xterm:
            # Execute in xterm popup window
            log_file = os.path.join(self.base_dir, 'runs', f'{tag}.log')
            pid_file = os.path.join(self.base_dir, 'data', f'{tag}_pid')

            # Make script executable
            os.chmod(run_script, 0o755)

            # Launch xterm with script logging
            # Use tcsh explicitly to run the script since default shell might be zsh
            xterm_cmd = f"xterm -title 'Genie Task: {tag}' -e \"script -a -q {log_file} -c 'tcsh -f {run_script}'\""
            process = subprocess.Popen(
                xterm_cmd,
                shell=True,
                start_new_session=True
            )

            # Save PID to file
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))

            print()
            print(f"Task launched in xterm popup...")
            print(f"PID: {process.pid}")
            print(f"Log file: {log_file}")
            print()
            print("To kill:")
            print(f"  python3 script/genie_cli.py --kill {tag}")
        else:
            # Execute the script in background and capture PID
            log_file = os.path.join(self.base_dir, 'runs', f'{tag}.log')
            pid_file = os.path.join(self.base_dir, 'data', f'{tag}_pid')

            # Start the process and get PID
            # For TileBuilder commands, use env -i to start with completely clean environment
            # This avoids conflicts with cbwa modules loaded in parent shell
            if is_tilebuilder_cmd:
                # Use env -i to clear ALL environment variables, then use csh -f to skip .cshrc
                # This ensures no cbwa modules are loaded
                # Include DISPLAY for X11 (required by TileBuilderTerm)
                display = os.environ.get('DISPLAY', ':0')
                xauthority = os.environ.get('XAUTHORITY', '')
                xauth_env = f"XAUTHORITY={xauthority}" if xauthority else ""
                cmd = f"env -i HOME={os.environ.get('HOME', '')} USER={os.environ.get('USER', '')} TERM=xterm DISPLAY={display} {xauth_env} /bin/tcsh -f {run_script} >& {log_file}"
                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    start_new_session=True
                )
            else:
                process = subprocess.Popen(
                    f"cd {self.base_dir} && tcsh -f {run_script} >& {log_file}",
                    shell=True, executable='/bin/tcsh',
                    start_new_session=True  # Create new process group for easy killing
                )

            # Save PID to file
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))

            print()
            print(f"Task executing in background...")
            print(f"PID: {process.pid}")
            print(f"Log file: {log_file}")
            print()
            print("To monitor:")
            print(f"  tail -f {log_file}")
            print()
            print("To kill:")
            print(f"  python3 script/genie_cli.py --kill {tag}")

        return {'tag': tag, 'command': command, 'script': script, 'args': arguementInfo, 'run_script': run_script}

    def list_instructions(self):
        """List all available instructions"""
        print("=" * 70)
        print("Available Instructions")
        print("=" * 70)
        print()

        instruction_file = os.path.join(self.base_dir, 'instruction.csv')
        with open(instruction_file, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    print(f"  {row[0]}")
                    print(f"    -> {row[1]}")
                    print()

    def spec_to_html(self, spec_content):
        """Convert spec format (#table#, #text#, #list#, #img#) to HTML with beautiful styling

        Supported tags:
        - #text# - Normal text section
        - #title# - Title text (bold, colored)
        - #bold# - Bold text
        - #table# ... #table end# - CSV table
        - #list# - Bulleted list items
        - #img# - Image insertion
        - #line# - Horizontal line
        - #html# ... #html end# - Raw HTML content (embedded directly)

        Special cell formats:
        - value::color - Custom background color (e.g., PASS::#28a745)
        - item1;item2;item3 - Multi-item cell rendered as ul/li list
        - /proj/path/file.log - Auto-linked to logviewer
        - mailto:... - Email links with subject extraction
        """
        import re
        import random

        lines = spec_content.split('\n')
        html_parts = []

        # Random table header color for variety
        table_colors = ["#0066cc", "#0077b6", "#005f99", "#004c80"]
        table_color = random.choice(table_colors)

        # HTML header with enhanced styles
        html_parts.append(f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 16px;
    color: #333;
    line-height: 1.6;
    margin: 30px;
}}
h2 {{
    color: #0066cc;
    border-bottom: 2px solid #0066cc;
    padding-bottom: 10px;
    margin-top: 32px;
    font-size: 22px;
}}
h3 {{
    color: #0066cc;
    margin-top: 24px;
    font-size: 18px;
}}
table.gridtable {{
    border-collapse: collapse;
    margin: 18px 0;
    font-size: 15px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    width: auto;
}}
table.gridtable th {{
    background-color: {table_color};
    color: #ffffff;
    padding: 13px 18px;
    text-align: left;
    font-weight: 600;
    font-size: 15px;
    border: 1px solid #004499;
}}
table.gridtable td {{
    padding: 12px 18px;
    border: 1px solid #ddd;
    background-color: #ffffff;
    text-align: left;
    font-size: 15px;
}}
table.gridtable tr:nth-child(even) td {{
    background-color: #f8f9fa;
}}
table.gridtable tr:hover td {{
    background-color: #e8f4fc;
}}
/* Status colors */
.pass, .passed, .success, .completed {{
    color: #28a745;
    font-weight: bold;
}}
.fail, .failed, .error {{
    color: #dc3545;
    font-weight: bold;
}}
.warning {{
    color: #ffc107;
    font-weight: bold;
}}
.running, .in_progress {{
    color: #17a2b8;
    font-weight: bold;
}}
/* Negative numbers in red */
.negative {{
    color: #dc3545;
}}
/* Positive numbers in green */
.positive {{
    color: #28a745;
}}
/* Links */
a {{
    color: #0066cc;
    text-decoration: none;
}}
a:hover {{
    text-decoration: underline;
}}
/* Lists */
ul {{
    margin-top: 8px;
    padding-left: 20px;
}}
li {{
    margin-top: 8px;
    padding-left: 6px;
    font-size: 15px;
    line-height: 1.6;
}}
div {{
    margin-top: 12px;
}}
/* Section divider */
hr {{
    border: none;
    border-top: 2px solid #e0e0e0;
    margin: 28px 0;
}}
/* Title styling */
.title {{
    color: #0066cc;
    font-weight: bold;
    font-size: 17px;
    margin: 15px 0 10px 0;
}}
/* Bold text */
.bold {{
    font-weight: bold;
}}
/* Highlight row */
.highlight {{
    background-color: #fffde7 !important;
}}
/* Footer */
.footer {{
    margin-top: 36px;
    padding-top: 18px;
    border-top: 1px solid #e0e0e0;
    color: #666;
    font-size: 14px;
}}
</style>
</head>
<body>
""")

        def make_link(text):
            """Convert path or URL to clickable link with smart label"""
            text = text.strip()

            # mailto: links - extract subject as label
            mailto_match = re.search(r'mailto:.*subject=([^&]+)', text)
            if mailto_match:
                label = mailto_match.group(1)
                return f'<a href="{text}">{label}</a>'

            # HTTP/HTTPS links
            if text.startswith('http'):
                # Extract filename or use last path component as label
                if re.search(r'\.(log|html|png|rpt|pptx)$', text, re.I):
                    label = text.split('/')[-1].split('.')[0]
                else:
                    label = text.split('/')[-1] or text
                return f'<a href="{text}">{label}</a>'

            # File paths (/proj/, /home/)
            if re.search(r'^/proj/|^/home/', text):
                # Files with extensions get short labels
                if re.search(r'\.(log|html|png|rpt|pptx|txt|csv)$', text, re.I):
                    label = text.split('/')[-1].split('.')[0]
                else:
                    label = text
                return f'<a href="http://logviewer-atl.amd.com{text}">{label}</a>'

            return text

        def get_cell_class(cell_text):
            """Determine CSS class based on cell content"""
            cell_lower = cell_text.strip().lower()

            # Status-based coloring
            if cell_lower in ['pass', 'passed', 'success', 'completed', 'complete']:
                return 'pass'
            elif cell_lower in ['fail', 'failed', 'error']:
                return 'fail'
            elif cell_lower in ['warning']:
                return 'warning'
            elif cell_lower in ['running', 'in_progress']:
                return 'running'

            # Number-based coloring (negative = red for timing violations)
            try:
                num = float(cell_text.strip())
                if num < 0:
                    return 'negative'
            except ValueError:
                pass

            return ''

        def process_cell(cell, is_header=False):
            """Process a single cell with all formatting options"""
            cell = cell.strip()
            style = ''
            cell_class = ''

            # Check for color override (value::color syntax)
            if '::' in cell and len(cell.split('::')) == 2:
                cell_text, color = cell.split('::')
                style = f' style="background-color: {color};"'
                cell = cell_text

            # Multi-item cell (items separated by ;)
            if ';' in cell:
                items = [item.strip() for item in cell.split(';') if item.strip()]
                if items:
                    ul_html = '<ul>'
                    for item in items:
                        item_html = make_link(item) if re.search(r'^/proj/|^/home/|^http|^mailto:', item) else item
                        ul_html += f'<li>{item_html}</li>'
                    ul_html += '</ul>'
                    tag = 'th' if is_header else 'td'
                    return f'<{tag}{style}>{ul_html}</{tag}>'

            # Single item processing
            cell_display = cell

            # Auto-link paths and URLs
            if re.search(r'^/proj/|^/home/|^http|^mailto:', cell):
                cell_display = make_link(cell)
            else:
                # Get cell class for status/number coloring
                cell_class = get_cell_class(cell)
                if cell_class:
                    cell_class = f' class="{cell_class}"'

            tag = 'th' if is_header else 'td'
            return f'<{tag}{cell_class}{style}>{cell_display}</{tag}>'

        # State tracking
        in_table = False
        in_title = False
        in_bold = False
        in_list = False
        in_html = False
        table_rows = []
        list_items = []
        html_block = []

        for line in lines:
            line = line.rstrip()
            line_stripped = line.strip()

            # Tag detection
            if line_stripped == '#table#':
                # Close any open list
                if in_list and list_items:
                    html_parts.append('<ul>')
                    for item in list_items:
                        html_parts.append(f'<li>{item}</li>')
                    html_parts.append('</ul>')
                    list_items = []
                in_list = False
                in_table = True
                table_rows = []
                continue
            elif line_stripped == '#table end#':
                in_table = False
                # Generate table HTML
                if table_rows:
                    html_parts.append('<table class="gridtable"><tbody>')
                    for i, row in enumerate(table_rows):
                        cells = row.split(',')
                        row_class = ''
                        # Highlight rows with important keywords
                        if any(kw in row.lower() for kw in ['design_wns', 'design_tns', 'total', 'summary']):
                            row_class = ' class="highlight"'
                        html_parts.append(f'<tr{row_class}>')
                        for cell in cells:
                            html_parts.append(process_cell(cell, is_header=(i == 0)))
                        html_parts.append('</tr>')
                    html_parts.append('</tbody></table>')
                continue
            elif line_stripped == '#text#':
                # Close any open list
                if in_list and list_items:
                    html_parts.append('<ul>')
                    for item in list_items:
                        html_parts.append(f'<li>{item}</li>')
                    html_parts.append('</ul>')
                    list_items = []
                in_title = False
                in_bold = False
                in_list = False
                continue
            elif line_stripped == '#title#':
                in_title = True
                continue
            elif line_stripped == '#bold#':
                in_bold = True
                continue
            elif line_stripped == '#list#':
                in_list = True
                list_items = []
                continue
            elif line_stripped == '#line#':
                html_parts.append('<hr>')
                continue
            elif line_stripped == '#img#':
                # Next line will be image path
                continue
            elif line_stripped == '#html#':
                # Start raw HTML block
                # Close any open list
                if in_list and list_items:
                    html_parts.append('<ul>')
                    for item in list_items:
                        html_parts.append(f'<li>{item}</li>')
                    html_parts.append('</ul>')
                    list_items = []
                in_list = False
                in_title = False
                in_bold = False
                in_html = True
                html_block = []
                continue
            elif line_stripped == '#html end#':
                # End raw HTML block and append accumulated HTML
                if html_block:
                    html_parts.append('\n'.join(html_block))
                in_html = False
                html_block = []
                continue
            elif line_stripped.startswith('#') and line_stripped.endswith('#'):
                # Unknown tag, skip
                continue

            # Content processing
            if in_html:
                # Accumulate raw HTML content (preserve original line, including empty lines)
                html_block.append(line)
            elif in_table:
                if line_stripped:
                    table_rows.append(line_stripped)
            elif in_list:
                if line_stripped:
                    # Process list item with auto-linking
                    item_html = make_link(line_stripped) if re.search(r'^/proj/|^/home/|^http|^mailto:', line_stripped) else line_stripped
                    list_items.append(item_html)
            elif line_stripped:
                if in_title:
                    html_parts.append(f'<div class="title">{line_stripped}</div>')
                    in_title = False
                elif in_bold:
                    html_parts.append(f'<div class="bold">{line_stripped}</div>')
                    in_bold = False
                elif line_stripped.startswith('='):
                    html_parts.append('<hr>')
                elif line_stripped.startswith('ERROR:') or line_stripped.startswith('Error:'):
                    html_parts.append(f'<div class="fail">{line_stripped}</div>')
                elif line_stripped.startswith('SUCCESS:') or line_stripped.startswith('Success:'):
                    html_parts.append(f'<div class="pass">{line_stripped}</div>')
                elif re.search(r'\.(png|jpg|jpeg|gif)$', line_stripped, re.I):
                    # Skip image files - they are handled as attachments
                    # Don't embed images in email body
                    pass
                else:
                    # Regular text - also check for links
                    if re.search(r'^/proj/|^/home/|^http|^mailto:', line_stripped):
                        html_parts.append(f'{make_link(line_stripped)}<br>')
                    else:
                        html_parts.append(f'{line_stripped}<br>')

        # Close any remaining open list
        if in_list and list_items:
            html_parts.append('<ul>')
            for item in list_items:
                html_parts.append(f'<li>{item}</li>')
            html_parts.append('</ul>')

        # Close any remaining open html block
        if in_html and html_block:
            html_parts.append('\n'.join(html_block))

        html_parts.append("""
<div class="footer">
<p>Sent by Genie Agent (Claude Code)</p>
</div>
</body>
</html>
""")

        return '\n'.join(html_parts)

    # =========================================================================
    # Agent Team — CDC Pre-Condition Check + Low-Risk Classification
    # =========================================================================

    def _parse_cdc_preconditions(self, report_path):
        """Parse CDC report header to extract pre-condition status.
        Reads Section 1 (Clock), Section 2 (Reset), Section 9 (Design Info).
        Returns dict with counts and module lists."""
        result = {
            'inferred_clocks_primary':   0,
            'inferred_clocks_blackbox':  0,
            'inferred_clocks_gated_mux': 0,
            'inferred_resets_primary':   0,
            'inferred_resets_blackbox':  0,
            'num_blackboxes':            0,
            'num_unresolved':            0,
            'empty_blackbox_modules':    [],   # list of {'module': name, 'count': N}
            'inferred_clock_signals':    [],   # signal names if primary inferred > 0
            'inferred_reset_signals':    [],   # signal names if primary inferred > 0
        }
        try:
            with open(report_path, errors='replace') as f:
                content = f.read()
        except Exception:
            return result

        # ---- Numeric summaries ----
        # Clock section: "    2.1 Primary                      : 0"
        # Reset section: "     2.1.1 Primary                   : 0"
        # Different numbering depth distinguishes clock vs reset entries
        num_patterns = [
            ('inferred_clocks_primary',   r'^\s+2\.1\s+Primary\s*:\s*(\d+)',   re.MULTILINE),
            ('inferred_clocks_blackbox',  r'^\s+2\.3\s+Blackbox\s*:\s*(\d+)',  re.MULTILINE),
            ('inferred_clocks_gated_mux', r'^\s+2\.4\s+Gated Mux\s*:\s*(\d+)', re.MULTILINE),
            ('inferred_resets_primary',   r'^\s+2\.1\.1\s+Primary\s*:\s*(\d+)', re.MULTILINE),
            ('inferred_resets_blackbox',  r'^\s+2\.1\.2\s+Blackbox\s*:\s*(\d+)', re.MULTILINE),
            ('num_blackboxes',            r'Number of blackboxes\s*=\s*(\d+)',  0),
            ('num_unresolved',            r'Number of Unresolved Modules\s*=\s*(\d+)', 0),
        ]
        for key, pattern, flags in num_patterns:
            m = re.search(pattern, content, flags)
            if m:
                result[key] = int(m.group(1))

        # ---- Empty Black Boxes table ----
        # Format: "ModuleName   3   /path/to/shell.v ( 10 )"
        # Header line contains "Module" and "Instance Count"
        bb_match = re.search(r'Empty Black Boxes:\s*\n[-\s]+\n.*?Instance Count.*?\n[-\s]+\n(.*?)(?:\n\n|\Z)',
                             content, re.DOTALL)
        if bb_match:
            for line in bb_match.group(1).split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                m = re.match(r'^(\S+)\s+(\d+)\s+', stripped)
                if m and not m.group(1).startswith('-') and m.group(1) != 'Module':
                    result['empty_blackbox_modules'].append({
                        'module': m.group(1),
                        'count':  int(m.group(2)),
                    })

        # ---- Inferred clock signal names (only relevant if primary > 0) ----
        if result['inferred_clocks_primary'] > 0:
            # Find "2.1.1 Primary" subsection under clock section
            # Signal names appear as standalone words before the next section header
            clk_prim_match = re.search(
                r'2\.1\.1\s+Primary\s+\(\d+\)\s*\n[-]+\n(.*?)(?:2\.1\.2|2\.2\s|={3,})',
                content, re.DOTALL)
            if clk_prim_match:
                for line in clk_prim_match.group(1).split('\n'):
                    s = line.strip()
                    if s and re.match(r'^[\w.]+\s*$', s):
                        result['inferred_clock_signals'].append(s)

        # ---- Inferred reset signal names (only relevant if primary > 0) ----
        if result['inferred_resets_primary'] > 0:
            rst_prim_match = re.search(
                r'2\.1\.1\s+Primary\s+\(\d+\)\s*\n[-]+\n(.*?)(?:2\.1\.2|2\.2\s|={3,})',
                content, re.DOTALL)
            if rst_prim_match:
                for line in rst_prim_match.group(1).split('\n'):
                    s = line.strip().split('<')[0].strip()  # strip annotation like <signal:A,L>
                    if s and re.match(r'^[\w.]+$', s) and s != 'None':
                        result['inferred_reset_signals'].append(s)

        return result

    def _is_low_risk_signal(self, signal):
        """Return (True, reason) if signal path is from a known low-risk module.
        Low-risk = only active in test/debug mode: RSMU, DFT, JTAG, TDR modules.
        Violations from these do not need waiver constraints — classify as LOW_RISK."""
        LOW_RISK_PATTERNS = [
            (r'rsmu',            'RSMU debug module — test/debug mode only'),
            (r'RSMU',            'RSMU debug module — test/debug mode only'),
            (r'rdft',            'RSMU RDFT debug module'),
            (r'_tdr[_.]',       'TDR (Test Data Register) — DFT boundary scan'),
            (r'\.TDR[_.]',      'TDR (Test Data Register) — DFT boundary scan'),
            (r'dft_clk_marker', 'DFT clock marker shell — intentional blackbox'),
            (r'jtag',            'JTAG boundary scan — test mode only'),
            (r'JTAG',            'JTAG boundary scan — test mode only'),
            (r'Tdr_Tck',        'TDR test clock — DFT use only'),
        ]
        for pattern, reason in LOW_RISK_PATTERNS:
            if re.search(pattern, signal):
                return True, reason
        return False, None

    def _get_manifest_lib_dirs(self, ref_dir, ip):
        """Read the published RTL manifest to extract unique lib directories.
        These are the golden paths to search for blackbox cell definitions.
        Returns list of unique directory paths from publish_rtl/manifest/*_lib.list."""
        import glob as glob_module

        if ip.startswith('umc'):
            tile = 'umc_top'
        elif ip.startswith('oss'):
            tile = 'osssys'
        elif ip.startswith('gmc'):
            tile = 'gmc_gmcctrl_t'
        else:
            tile = '*'

        # Pattern: out/linux_*.VCS/<ip>/config/*/pub/sim/publish/tiles/tile/<tile>/publish_rtl/manifest/*_lib.list
        manifest_patterns = [
            os.path.join(ref_dir, f'out/linux_*.VCS/{ip}/config/*/pub/sim/publish/tiles/tile/{tile}/publish_rtl/manifest/*_lib.list'),
            os.path.join(ref_dir, f'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/{tile}/publish_rtl/manifest/*_lib.list'),
        ]

        manifest_file = None
        for pat in manifest_patterns:
            matches = sorted(glob_module.glob(pat), key=os.path.getmtime, reverse=True)
            if matches:
                manifest_file = matches[0]
                break

        if not manifest_file:
            return []

        lib_dirs = set()
        try:
            with open(manifest_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and (line.endswith('.lib.gz') or line.endswith('.lib')):
                        lib_dirs.add(os.path.dirname(line))
        except Exception:
            pass

        return list(lib_dirs)

    def _find_lib_for_module(self, module_name, lib_dirs):
        """Search manifest lib directories for a cell matching module_name.
        Uses zgrep on .lib.gz files — the golden paths from the manifest.
        Returns list of lib file paths where the module cell definition was found."""
        import glob as glob_module
        import subprocess

        found = []
        search_pattern = f'cell ({module_name})'

        for lib_dir in lib_dirs:
            if not os.path.isdir(lib_dir):
                continue
            lib_files = (glob_module.glob(os.path.join(lib_dir, '*.lib.gz')) +
                         glob_module.glob(os.path.join(lib_dir, '*.lib')))
            for lib_file in lib_files:
                try:
                    cmd = ['zgrep', '-l', search_pattern, lib_file] if lib_file.endswith('.gz') else \
                          ['grep',  '-l', search_pattern, lib_file]
                    r = subprocess.run(cmd, capture_output=True, timeout=8)
                    if r.returncode == 0:
                        found.append(lib_file)
                except Exception:
                    pass
        return found

    def _parse_cdc_report(self, report_path, section_keyword='CDC Results'):
        """Parse CDC or RDC report file, extract violation IDs and signal names.

        Only reads violations within the named section:
          - CDC: 'CDC Results'  (Section 3 of cdc_report.rpt)
          - RDC: 'RDC Results'  (Section 5 of rdc_report.rpt)

        A real section header is identified by having '=====' on the very next
        non-empty line (distinguishes it from the table-of-contents entry at
        the top of the file which has another Section line next).

        Signal name is extracted from the '... : start : <signal>' line that
        immediately precedes the '... : end : ... (ID:...)' violation line.
        """
        try:
            with open(report_path, errors='replace') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"WARNING: Could not read report: {e}")
            return []

        section_re = re.compile(r'^\s*Section\s+\d+\s*:', re.IGNORECASE)
        target_re  = re.compile(re.escape(section_keyword), re.IGNORECASE)

        # Find the line range for the target section
        section_start = None
        section_end   = len(lines)

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not section_re.match(stripped):
                continue
            # Real section header: next non-empty line is a separator ('=====')
            is_real = False
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j].strip()
                if nxt:
                    is_real = nxt.startswith('===')
                    break
            if not is_real:
                continue
            if target_re.search(stripped):
                section_start = i
            elif section_start is not None:
                section_end = i
                break

        if section_start is None:
            return []

        # Within the section, find the 'Violations' sub-section and stop
        # at the next sub-section (Cautions, Evaluations, Resolved, etc.)
        # A sub-section header is a bare keyword line followed by '====='
        sub_stop_re = re.compile(
            r'^(Cautions|Evaluations|Resolved|Proven|Filtered)\s*$', re.IGNORECASE)
        violations_sub_re = re.compile(r'^Violations\s*$', re.IGNORECASE)

        sub_start = section_start   # default: use whole section
        sub_end   = section_end

        for i in range(section_start, section_end):
            stripped = lines[i].strip()
            # Check if next non-empty line is '====='
            is_subhdr = False
            for j in range(i + 1, min(i + 3, section_end)):
                nxt = lines[j].strip()
                if nxt:
                    is_subhdr = nxt.startswith('===')
                    break
            if not is_subhdr:
                continue
            if violations_sub_re.match(stripped):
                sub_start = i
            elif sub_stop_re.match(stripped) and sub_start != section_start:
                sub_end = i
                break

        id_pattern    = re.compile(r'\(ID:(([a-z_]+)_(\d+))\)')
        start_pattern = re.compile(r':\s*start\s*:\s*(\S+)')

        violations = []
        for i in range(sub_start, sub_end):
            m = id_pattern.search(lines[i])
            if not m:
                continue
            full_id = m.group(1)
            vtype   = m.group(2)   # e.g. 'no_sync', 'multi_bits', 'series_redundant'
            signal  = ''
            # The source signal is on the ': start :' line just above
            for back in range(1, 10):
                if i - back < sub_start:
                    break
                prev = lines[i - back].strip()
                sm = start_pattern.search(prev)
                if sm:
                    signal = sm.group(1)
                    break
            violations.append({'id': full_id, 'type': vtype, 'signal': signal})
        return violations

    def _find_report_path(self, ref_dir, ip, check_type='cdc'):
        """Find the most recent report file using IP_CONFIG.yaml path patterns."""
        import glob as glob_module

        if ip.startswith('umc'):
            ip_family = 'umc'
            tile = 'umc_top'
        elif ip.startswith('oss'):
            ip_family = 'oss'
            tile = 'osssys'
        elif ip.startswith('gmc'):
            ip_family = 'gmc'
            tile = 'gmc_gmcctrl_t'
        else:
            return None

        # Try reading path pattern from IP_CONFIG.yaml
        pattern = ''
        try:
            import yaml
            config_path = os.path.join(self.base_dir, 'config', 'IP_CONFIG.yaml')
            with open(config_path) as f:
                config = yaml.safe_load(f)
            pattern = config.get(ip_family, {}).get('reports', {}).get(check_type, {}).get('path_pattern', '')
        except Exception:
            pass

        # Fallback hardcoded patterns if yaml unavailable or pattern not found
        if not pattern:
            fallback = {
                'cdc': {
                    'umc': 'out/linux_*/*/config/*/pub/sim/publish/tiles/tile/umc_top/cad/rhea_cdc/cdc_*_output/cdc_report.rpt',
                    'oss': 'out/linux_*.VCS/*/config/*_dc_elab/pub/sim/publish/tiles/tile/*/cad/rhea_cdc/cdc_*_output/cdc_report.rpt',
                    'gmc': 'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/rhea_cdc/cdc_*_output/cdc_report.rpt',
                },
                'lint': {
                    'umc': 'out/linux_*/*/config/*/pub/sim/publish/tiles/tile/umc_top/cad/rhea_lint/leda_waiver.log',
                    'oss': 'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/*/cad/rhea_lint/leda_waiver.log',
                    'gmc': 'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/rhea_lint/leda_waiver.log',
                },
                'spg_dft': {
                    'umc': 'out/linux_*/*/config/*/pub/sim/publish/tiles/tile/umc_top/cad/spg_dft/umc_top/moresimple.rpt',
                    'oss': 'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/*/cad/spg_dft/*/moresimple.rpt',
                    'gmc': 'out/linux_*.VCS/*/config/*/pub/sim/publish/tiles/tile/gmc_*/cad/spg_dft/*/moresimple.rpt',
                },
            }
            ct = check_type if check_type in fallback else 'cdc'
            pattern = fallback[ct].get(ip_family, '')

        if not pattern:
            return None

        pattern = pattern.replace('{tile}', tile)
        full_pattern = os.path.join(ref_dir, pattern)
        matches = sorted(glob_module.glob(full_pattern), key=os.path.getmtime, reverse=True)
        return matches[0] if matches else None

    def classify_violations(self, report_path, ref_dir=None, ip=None):
        """Parse CDC report and classify violations.

        Phase A: Pre-condition check (inferred clocks/resets, unresolved blackboxes).
        Phase B: Per-violation classification:
                 - LOW_RISK bucket: RSMU/DFT/JTAG modules (test mode only, no waiver needed)
                 - HIGH/MEDIUM/LOW: pattern match against FIX_TEMPLATES.yaml

        Returns enriched dict:
          {
            'status':        'OK' | 'PRECONDITION_WARN' | 'PRECONDITION_FAIL',
            'preconditions': {counts, blackbox module list, ...},
            'precond_issues': [list of human-readable issue strings],
            'suggestions':   [list of suggested constraint TCL lines],
            'HIGH':     [...],   # auto-waivable
            'MEDIUM':   [...],   # verify first
            'LOW':      [...],   # unmatched, needs human review
            'LOW_RISK': [...],   # RSMU/DFT — categorise as low, no action needed
            'total':    N,
          }
        """
        if not os.path.exists(report_path):
            return None

        templates_path = os.path.join(self.base_dir, 'config', 'FIX_TEMPLATES.yaml')
        cdc_patterns = {}
        try:
            import yaml
            with open(templates_path) as f:
                templates = yaml.safe_load(f)
            cdc_patterns = templates.get('cdc_waiver_patterns', {})
        except Exception as e:
            print(f"WARNING: Could not load FIX_TEMPLATES.yaml ({e})")

        # ---- Phase A: Pre-condition check ----
        precond = self._parse_cdc_preconditions(report_path)

        # Get manifest lib dirs for unknown blackbox resolution hints
        manifest_lib_dirs = []
        if ref_dir and ip:
            try:
                manifest_lib_dirs = self._get_manifest_lib_dirs(ref_dir, ip)
            except Exception:
                pass

        precond_status = 'OK'
        precond_issues = []
        suggestions = []

        # Hard stop: unresolved modules make all violations unreliable
        if precond['num_unresolved'] > 0:
            precond_status = 'PRECONDITION_FAIL'
            precond_issues.append(
                f"FAIL — {precond['num_unresolved']} Unresolved Module(s): "
                f"CDC tool has no RTL/shell for these; violations are unreliable"
            )
            suggestions.append("# Resolve unresolved modules — add netlist blackbox constraints or missing lib to liblist")

        # Warning: inferred primary clocks (tool is guessing clock domains)
        if precond['inferred_clocks_primary'] > 0:
            if precond_status == 'OK':
                precond_status = 'PRECONDITION_WARN'
            precond_issues.append(
                f"WARN — {precond['inferred_clocks_primary']} Inferred Primary Clock(s): "
                f"domain assignments may be incorrect"
            )
            if precond['inferred_clock_signals']:
                suggestions.append("# Add to src/meta/tools/cdc0in/variant/$ip/project.0in_ctrl.v.tcl:")
                for sig in precond['inferred_clock_signals']:
                    suggestions.append(f"netlist clock {sig} -group <CLOCK_GROUP>  # verify period and group name")
            else:
                suggestions.append("# Add netlist clock <signal> -group <GROUP> for each inferred clock")

        # Warning: inferred primary resets (not from blackbox — genuinely missing)
        if precond['inferred_resets_primary'] > 0:
            if precond_status == 'OK':
                precond_status = 'PRECONDITION_WARN'
            precond_issues.append(
                f"WARN — {precond['inferred_resets_primary']} Inferred Primary Reset(s)"
            )
            if precond['inferred_reset_signals']:
                suggestions.append("# Add to project.0in_ctrl.v.tcl:")
                for sig in precond['inferred_reset_signals']:
                    suggestions.append(f"netlist reset {sig} -active_low  # verify polarity (active_low vs active_high)")
            else:
                suggestions.append("# Add netlist reset <signal> -active_low/-active_high for each inferred reset")

        # Gated mux clocks: warn (may indicate unrecognised clock gating cell)
        if precond['inferred_clocks_gated_mux'] > 0:
            if precond_status == 'OK':
                precond_status = 'PRECONDITION_WARN'
            precond_issues.append(
                f"WARN — {precond['inferred_clocks_gated_mux']} Inferred Gated-Mux Clock(s): "
                f"clock gating cell may not be in liblist"
            )
            suggestions.append("# Check umc_top_lib.list — gating cell lib may be missing")

        # Classify blackbox modules: LOW risk (RSMU/DFT) vs unknown
        for bb in precond['empty_blackbox_modules']:
            module = bb['module']
            is_low, lr_reason = self._is_low_risk_signal(module)
            if is_low:
                bb['risk']   = 'LOW_RISK'
                bb['reason'] = lr_reason
            else:
                bb['risk'] = 'UNKNOWN'
                # Try to find in manifest golden lib paths
                if manifest_lib_dirs:
                    try:
                        found_libs = self._find_lib_for_module(module, manifest_lib_dirs)
                        if found_libs:
                            bb['lib_hint'] = found_libs[0]
                            suggestions.append(f"# Blackbox '{module}' found in: {found_libs[0]}")
                            suggestions.append(f"# Add to src/meta/tools/cdc0in/variant/$ip/umc_top_lib.list")
                            suggestions.append(f"# Also add to src/meta/tools/spgdft/variant/$ip/project.params SPGDFT_STD_LIB")
                        else:
                            bb['lib_hint'] = None
                            suggestions.append(f"# Blackbox '{module}' not found in manifest lib dirs — needs investigation")
                    except Exception:
                        bb['lib_hint'] = None

        # ---- Phase B: Per-violation classification ----
        violations = self._parse_cdc_report(report_path)
        classified = {
            'status':        precond_status,
            'preconditions': precond,
            'precond_issues': precond_issues,
            'suggestions':   suggestions,
            'HIGH':     [],
            'MEDIUM':   [],
            'LOW':      [],
            'LOW_RISK': [],   # RSMU/DFT/JTAG — test mode only, no action needed
            'total':    len(violations),
        }

        for v in violations:
            signal = v.get('signal', '')

            # Low-risk check first: RSMU, DFT, JTAG signals
            is_low, lr_reason = self._is_low_risk_signal(signal)
            if is_low:
                v['pattern']          = 'low_risk_module'
                v['template']         = ''
                v['low_risk_reason']  = lr_reason
                classified['LOW_RISK'].append(v)
                continue

            # FIX_TEMPLATES pattern matching
            matched_confidence = 'LOW'
            matched_template   = ''
            matched_pattern    = 'unmatched'

            for pattern_name, pattern_def in cdc_patterns.items():
                match_def = pattern_def.get('match', {})

                req_type = match_def.get('violation_type', '')
                if req_type and v.get('type', '') != req_type:
                    continue

                sig_patterns  = match_def.get('signal_patterns', [])
                path_contains = match_def.get('path_contains', [])
                excl_patterns = pattern_def.get('exclude_patterns', [])

                if sig_patterns:
                    if not any(re.search(sp, signal) for sp in sig_patterns):
                        continue
                elif path_contains:
                    if not any(pc.lower() in signal.lower() for pc in path_contains):
                        continue
                elif not req_type:
                    continue

                if any(re.search(ep, signal) for ep in excl_patterns):
                    continue

                matched_pattern    = pattern_name
                matched_confidence = pattern_def.get('confidence', 'LOW')
                matched_template   = pattern_def.get('waiver_template', '').strip()
                break

            v['pattern']  = matched_pattern
            v['template'] = matched_template
            classified[matched_confidence].append(v)

        return classified

    def generate_waivers(self, classified, tag):
        """Generate TCL waiver file for HIGH confidence violations.
        Returns (waiver_file_path, waiver_count)."""
        high_violations = classified.get('HIGH', [])
        if not high_violations:
            return None, 0

        lines = [
            f"# Auto-generated CDC waivers",
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Violations waived: {len(high_violations)}",
            f"# Source: Genie CLI --agent-team mode",
            f"# Apply: source <this_file> inside Questa CDC session",
            "",
        ]

        # Group by pattern for readability
        by_pattern = {}
        for v in high_violations:
            by_pattern.setdefault(v.get('pattern', 'unknown'), []).append(v)

        for pattern_name, pvlist in by_pattern.items():
            lines.append(f"# --- Pattern: {pattern_name} ({len(pvlist)} violations) ---")
            for v in pvlist:
                tmpl = v.get('template', '')
                if tmpl:
                    waiver = tmpl.replace('{violation_id}', v['id'])
                    lines.append(waiver)
                else:
                    lines.append(f"cdc report crossing -id {v['id']} -status waived")
            lines.append("")

        waiver_content = '\n'.join(lines)
        waiver_file = os.path.join(self.base_dir, 'data', f'{tag}_waivers.tcl')
        with open(waiver_file, 'w') as f:
            f.write(waiver_content)

        return waiver_file, len(high_violations)

    def apply_cdc_waivers(self, classified, tag, ref_dir, ip):
        """Write waivers/constraints to data files and call update_cdc.csh to apply.
        Returns (success, summary_string).
        Called from --send-completion-email when --self-debug was set."""
        if ip.startswith('umc'):
            ip_family = 'umc'
            tile = 'umc_top'
        elif ip.startswith('oss'):
            ip_family = 'oss'
            tile = 'osssys'
        elif ip.startswith('gmc'):
            ip_family = 'gmc'
            tile = 'gmc_gmcctrl_t'
        else:
            return False, f"Unknown ip family for ip={ip} — cannot determine update script path."

        results = []
        update_script_path = f'script/rtg_oss_feint/{ip_family}/update_cdc.csh'
        env_script = os.path.join(self.base_dir, 'csh', 'env.csh')

        # --- Waiver apply (HIGH violations) ---
        high_violations = classified.get('HIGH', [])
        if high_violations:
            waiver_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_waiver')
            lines = []
            for v in high_violations:
                tmpl = v.get('template', '')
                if tmpl:
                    waiver = tmpl.replace('{violation_id}', v['id'])
                    lines.append(waiver)
                else:
                    lines.append(f"cdc report crossing -id {v['id']} -status waived")
                lines.append("")
            with open(waiver_file, 'w') as f:
                f.write('\n'.join(lines))

            cmd = (f"cd {self.base_dir} && source {env_script} && "
                   f"source {update_script_path} {ref_dir} {ip} {tile} {tag} waiver")
            try:
                proc = subprocess.run(
                    ['tcsh', '-f', '-c', cmd],
                    capture_output=True, text=True, timeout=600
                )
                if proc.returncode == 0:
                    results.append(f"  Waivers applied: {len(high_violations)} HIGH violations written to "
                                   f"src/meta/tools/cdc0in/variant/{ip}/umc.0in_waiver")
                else:
                    results.append(f"  Waiver apply FAILED (exit {proc.returncode}):\n"
                                   f"    {proc.stderr[:300] if proc.stderr else '(no stderr)'}")
            except subprocess.TimeoutExpired:
                results.append("  Waiver apply TIMED OUT after 600s")
            except Exception as e:
                results.append(f"  Waiver apply error: {e}")

        # --- Constraint apply (MEDIUM gray_coded_pointer violations) ---
        gray_violations = [v for v in classified.get('MEDIUM', [])
                           if v.get('pattern') == 'gray_coded_pointer']
        if gray_violations:
            constraint_file = os.path.join(self.base_dir, 'data', f'{tag}.cdc_rdc_constraint')
            lines = []
            for v in gray_violations:
                tmpl = v.get('template', '')
                signal = v.get('signal', v['id'])
                if tmpl:
                    constraint = (tmpl.replace('{signal_name}', signal)
                                      .replace('{dest_clock}', 'VERIFY_CLOCK_DOMAIN'))
                    lines.append(constraint)
                else:
                    lines.append(f"# VERIFY: netlist port {signal} -clock_domain <dest_clock> "
                                 f"-comment \"Gray coded pointer - 1 bit change per cycle\"")
                lines.append("")
            with open(constraint_file, 'w') as f:
                f.write('\n'.join(lines))

            cmd = (f"cd {self.base_dir} && source {env_script} && "
                   f"source {update_script_path} {ref_dir} {ip} {tile} {tag} constraint")
            try:
                proc = subprocess.run(
                    ['tcsh', '-f', '-c', cmd],
                    capture_output=True, text=True, timeout=600
                )
                if proc.returncode == 0:
                    results.append(f"  Constraints applied: {len(gray_violations)} gray_coded_pointer "
                                   f"constraints written to "
                                   f"src/meta/tools/cdc0in/variant/{ip}/project.0in_ctrl.v.tcl\n"
                                   f"    NOTE: Verify dest_clock domain before submitting to P4.")
                else:
                    results.append(f"  Constraint apply FAILED (exit {proc.returncode}):\n"
                                   f"    {proc.stderr[:300] if proc.stderr else '(no stderr)'}")
            except subprocess.TimeoutExpired:
                results.append("  Constraint apply TIMED OUT after 600s")
            except Exception as e:
                results.append(f"  Constraint apply error: {e}")

        if not high_violations and not gray_violations:
            return True, "  No HIGH/MEDIUM violations to apply."

        summary = "AUTO-APPLY RESULTS:\n" + "\n".join(results)
        return True, summary

    # -------------------------------------------------------------------------
    # Lint Analysis
    # -------------------------------------------------------------------------

    def classify_lint_violations(self, report_path):
        """Parse leda_waiver.log and classify unwaived lint violations.
        Returns dict with status, HIGH, MEDIUM, LOW, LOW_RISK, total."""
        import yaml

        # Parse the Unwaived section of leda_waiver.log
        violations = []
        in_unwaived = False
        in_table = False
        header_seen = False
        try:
            with open(report_path) as f:
                for line in f:
                    line = line.rstrip()
                    if line.strip() == 'Unwaived':
                        in_unwaived = True
                        continue
                    if in_unwaived and line.startswith('----'):
                        in_table = True
                        continue
                    if in_unwaived and line.startswith('Unused Waivers'):
                        break  # Done with Unwaived section
                    if in_unwaived and 'No unwaived violations' in line:
                        return {
                            'status': 'OK', 'total': 0,
                            'HIGH': [], 'MEDIUM': [], 'LOW': [], 'LOW_RISK': [],
                        }
                    if in_table and '|' in line and not line.strip().startswith('='):
                        # Column headers row: code | error | filename | line | msg | author | reason
                        if 'code' in line.lower() and 'error' in line.lower():
                            header_seen = True
                            continue
                        if header_seen and line.strip().startswith('='):
                            continue
                        cols = [c.strip() for c in line.split('|')]
                        if len(cols) >= 5:
                            violations.append({
                                'code':     cols[0],
                                'error':    cols[1],
                                'filename': cols[2],
                                'line':     cols[3],
                                'msg':      cols[4],
                            })
        except Exception as e:
            return {'status': 'ERROR', 'error': str(e), 'total': 0,
                    'HIGH': [], 'MEDIUM': [], 'LOW': [], 'LOW_RISK': []}

        if not violations:
            return {'status': 'OK', 'total': 0,
                    'HIGH': [], 'MEDIUM': [], 'LOW': [], 'LOW_RISK': []}

        # Load FIX_TEMPLATES.yaml for lint_waiver_patterns
        lint_patterns = {}
        try:
            config_path = os.path.join(self.base_dir, 'config', 'FIX_TEMPLATES.yaml')
            with open(config_path) as f:
                templates = yaml.safe_load(f)
            lint_patterns = templates.get('lint_waiver_patterns', {})
        except Exception:
            pass

        high, medium, low, low_risk = [], [], [], []
        for v in violations:
            signal = v.get('msg', '') + ' ' + v.get('filename', '')
            code   = v.get('code', '').strip('-').strip()
            err    = v.get('error', '').strip()

            # Step 1: LOW_RISK check (RSMU/DFT module)
            is_lr, lr_reason = self._is_low_risk_signal(signal)
            if is_lr:
                low_risk.append({**v, 'reason': lr_reason})
                continue

            # Step 2: lint_waiver_patterns matching
            matched = False
            for pattern_name, pdata in lint_patterns.items():
                match_conf = pdata.get('match', {})
                rule_codes = match_conf.get('rule_codes', [])
                sig_pats   = match_conf.get('signal_patterns', [])
                # Check rule code match
                code_ok = not rule_codes or any(code == rc for rc in rule_codes)
                # Check signal pattern match
                sig_ok = not sig_pats or any(re.search(p, signal, re.IGNORECASE) for p in sig_pats)
                if code_ok and sig_ok and rule_codes:  # require rule_codes for specificity
                    conf = pdata.get('confidence', 'LOW')
                    entry = {**v, 'pattern': pattern_name, 'confidence': conf,
                             'template': pdata.get('waiver_template', '')}
                    if conf == 'HIGH':
                        high.append(entry)
                    elif conf == 'MEDIUM':
                        medium.append(entry)
                    else:
                        low.append(entry)
                    matched = True
                    break

            if not matched:
                low.append({**v, 'pattern': 'unmatched', 'confidence': 'LOW'})

        return {
            'status': 'OK' if violations else 'OK',
            'total': len(violations),
            'HIGH': high, 'MEDIUM': medium, 'LOW': low, 'LOW_RISK': low_risk,
        }

    def generate_lint_hints(self, classified, tag):
        """Write data/$tag.lint_waiver in smart-match hint format for update_lint.csh.
        Returns hint_file_path or None."""
        high = classified.get('HIGH', [])
        if not high:
            return None

        lines = [
            f"# Auto-generated lint waiver hints",
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Apply with: update lint waiver instruction (calls update_lint.csh)",
            "",
        ]
        for v in high:
            tmpl = v.get('template', '')
            if tmpl:
                # Fill in template variables
                hint = (tmpl.replace('{rule_code}', v.get('code', '').strip('-').strip())
                            .replace('{file}', v.get('filename', '.*'))
                            .replace('{signal}', v.get('msg', '.*').split()[0] if v.get('msg') else '.*')
                            .replace('{line}', v.get('line', '.*') or '.*'))
                lines.append(hint.strip())
            else:
                lines.append(f"error: {v.get('code','').strip('-').strip()}")
                lines.append(f"code: {v.get('msg', '').split()[0] if v.get('msg') else ''}")
                lines.append(f"reason: Auto-classified by genie agent team")
                lines.append(f"author: genie_agent_auto")
            lines.append("")

        hint_file = os.path.join(self.base_dir, 'data', f'{tag}.lint_waiver')
        with open(hint_file, 'w') as f:
            f.write('\n'.join(lines))
        return hint_file

    # -------------------------------------------------------------------------
    # SPG_DFT Analysis
    # -------------------------------------------------------------------------

    def _find_spg_dft_filter_file(self, ip):
        """Find spg_dft_error_filter.txt for the given ip family."""
        if ip.startswith('umc'):
            family = 'umc'
        elif ip.startswith('oss'):
            family = 'oss'
        elif ip.startswith('gmc'):
            family = 'gmc'
        else:
            family = 'umc'
        filter_path = os.path.join(self.base_dir, 'script', 'rtg_oss_feint', family,
                                   'spg_dft_error_filter.txt')
        return filter_path if os.path.exists(filter_path) else None

    def classify_spg_dft_violations(self, report_path, ip):
        """Parse moresimple.rpt, apply filter, classify violations.
        Returns dict with status, filtered (LOW_RISK), unfiltered (HIGH/LOW), total_errors."""
        import yaml

        filter_file = self._find_spg_dft_filter_file(ip)
        ip_lower = ip.lower()

        # Load filter patterns (same logic as spg_dft_error_extract.pl)
        patterns = []
        pattern_texts = []
        if filter_file and os.path.exists(filter_file):
            current_section = 'general'
            with open(filter_file) as f:
                for line in f:
                    line = line.rstrip()
                    if not line or line.startswith('#'):
                        continue
                    m = re.match(r'^\s*\[(\w+)\]\s*$', line)
                    if m:
                        current_section = m.group(1).lower()
                        continue
                    if current_section in ('general', ip_lower):
                        try:
                            patterns.append(re.compile(line))
                            pattern_texts.append(f"[{current_section}] {line}")
                        except re.error:
                            pass

        # Load FIX_TEMPLATES spg_dft_waiver_patterns for classification of unfiltered violations
        spg_patterns = {}
        try:
            config_path = os.path.join(self.base_dir, 'config', 'FIX_TEMPLATES.yaml')
            with open(config_path) as f:
                templates = yaml.safe_load(f)
            spg_patterns = templates.get('spg_dft_waiver_patterns', {})
        except Exception:
            pass

        total_errors = 0
        filtered = []     # LOW_RISK — matched by existing filter
        unfiltered = []   # Need action

        try:
            with open(report_path) as f:
                for line in f:
                    if not re.search(r'\s+(Error|ERROR)\s+', line):
                        continue
                    total_errors += 1
                    # Check existing filter patterns
                    matched_pattern = None
                    for i, p in enumerate(patterns):
                        if p.search(line):
                            matched_pattern = pattern_texts[i]
                            break
                    if matched_pattern:
                        filtered.append({'raw': line.strip(), 'filter_pattern': matched_pattern})
                        continue
                    # Unfiltered — classify with _is_low_risk_signal and spg_patterns
                    # Extract rule from line (2nd whitespace-delimited token after [ID])
                    parts = re.split(r'\s{2,}', line.strip())
                    rule = parts[1] if len(parts) > 1 else ''
                    alias = parts[2] if len(parts) > 2 else ''
                    msg = parts[-1] if parts else ''

                    # Check LOW_RISK via signal path in message
                    is_lr, lr_reason = self._is_low_risk_signal(line)
                    if is_lr:
                        # Should have been in filter — suggest adding
                        unfiltered.append({
                            'raw': line.strip(), 'rule': rule, 'alias': alias, 'msg': msg,
                            'classification': 'LOW_RISK_MISSING_FILTER',
                            'suggestion': f"Add to [{ip_lower}] section of spg_dft_error_filter.txt",
                            'lr_reason': lr_reason,
                        })
                        continue

                    # Match against FIX_TEMPLATES spg_dft_waiver_patterns
                    classified_pattern = None
                    for pname, pdata in spg_patterns.items():
                        path_patterns = pdata.get('match', {}).get('path_patterns', [])
                        rule_match = pdata.get('match', {}).get('rule', '')
                        if rule_match and rule_match not in (rule, alias):
                            continue
                        if path_patterns and any(re.search(pp, line, re.IGNORECASE) for pp in path_patterns):
                            classified_pattern = pname
                            break
                    if classified_pattern:
                        pdata = spg_patterns[classified_pattern]
                        action = pdata.get('action', 'add_to_filter')
                        conf   = pdata.get('confidence', 'HIGH')
                        unfiltered.append({
                            'raw': line.strip(), 'rule': rule, 'alias': alias, 'msg': msg,
                            'classification': f'{conf}_KNOWN_PATTERN',
                            'pattern': classified_pattern,
                            'action': action,
                            'filter_template': pdata.get('filter_template', ''),
                            'suggestion': (f"Add pattern '{pdata.get('filter_template','')}' "
                                          f"to [{ip_lower}] section of spg_dft_error_filter.txt"),
                        })
                    else:
                        unfiltered.append({
                            'raw': line.strip(), 'rule': rule, 'alias': alias, 'msg': msg,
                            'classification': 'HUMAN_REVIEW',
                        })
        except Exception as e:
            return {'status': 'ERROR', 'error': str(e), 'total_errors': 0,
                    'filtered': [], 'unfiltered': []}

        return {
            'status': 'OK',
            'total_errors': total_errors,
            'filtered': filtered,
            'unfiltered': unfiltered,
        }

    def generate_spg_dft_filter_suggestions(self, classified_spg, tag, ip):
        """Generate suggested spg_dft_error_filter.txt additions.
        Returns suggestion_file_path or None."""
        unfiltered = classified_spg.get('unfiltered', [])
        if not unfiltered:
            return None

        ip_lower = ip.lower()
        # Group by suggested filter pattern (avoid duplicates)
        seen_templates = set()
        low_risk_missing = []
        known_patterns = []
        human_review = []

        for v in unfiltered:
            cls = v.get('classification', '')
            if cls == 'LOW_RISK_MISSING_FILTER':
                # Extract a useful filter pattern from the raw line
                raw = v.get('raw', '')
                # Try to extract module path from message (last column)
                msg = v.get('msg', '')
                # Suggest regex based on first meaningful path component
                m = re.search(r'umc\w*rsmu_rdft\w*|rsmu\w+|rdft\w+', msg, re.IGNORECASE)
                tmpl = m.group(0) if m else raw[:60]
                if tmpl not in seen_templates:
                    seen_templates.add(tmpl)
                    low_risk_missing.append({'raw': raw, 'lr_reason': v.get('lr_reason',''), 'template': tmpl})
            elif 'KNOWN_PATTERN' in cls:
                tmpl = v.get('filter_template', '')
                if tmpl and tmpl not in seen_templates:
                    seen_templates.add(tmpl)
                    known_patterns.append({
                        'pattern': v.get('pattern', ''),
                        'template': tmpl,
                        'suggestion': v.get('suggestion', ''),
                    })
            else:
                human_review.append(v.get('raw', '')[:120])

        lines = [
            f"# Auto-generated SPG_DFT filter suggestions",
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Add these patterns to spg_dft_error_filter.txt under [{ip_lower}] section",
            f"# Review carefully before applying — verify signals are in test/debug modules only",
            "",
        ]

        if known_patterns:
            lines.append(f"# --- HIGH confidence patterns (known from umc9_3 or FIX_TEMPLATES) ---")
            lines.append(f"[{ip_lower}]")
            for kp in known_patterns:
                lines.append(f"# Pattern: {kp['pattern']}")
                lines.append(kp['template'])
                lines.append("")

        if low_risk_missing:
            lines.append(f"# --- RSMU/DFT signals not yet in filter (should be filtered) ---")
            lines.append(f"[{ip_lower}]")
            for lrm in low_risk_missing:
                lines.append(f"# Reason: {lrm['lr_reason']}")
                lines.append(lrm['template'])
                lines.append("")

        if human_review:
            lines.append(f"# --- HUMAN REVIEW violations (unknown, {len(human_review)} total) ---")
            lines.append(f"# These do NOT match any known pattern. Investigate before filtering.")
            for hr in human_review[:5]:
                lines.append(f"#   {hr}")
            if len(human_review) > 5:
                lines.append(f"#   ... and {len(human_review)-5} more")
            lines.append("")

        hint_file = os.path.join(self.base_dir, 'data', f'{tag}.spg_dft_filter_hints')
        with open(hint_file, 'w') as f:
            f.write('\n'.join(lines))
        return hint_file

    def send_email(self, to_emails, subject, body, use_html=True, attachments=None):
        """Send email with results to multiple recipients, optionally with attachments"""
        import base64
        import mimetypes

        # Handle single email or list
        if isinstance(to_emails, str):
            to_emails = [to_emails]

        # Validate all emails are @amd.com
        for email in to_emails:
            if not email.lower().endswith('@amd.com'):
                print(f"ERROR: Email must be @amd.com (got: {email})")
                return False

        # First email is To, rest are CC
        to_addr = to_emails[0]
        cc_addrs = to_emails[1:] if len(to_emails) > 1 else []

        # Check for attachments
        if attachments is None:
            attachments = []

        # Extract attachments from body if present (#attachment# tags)
        body_lines = body.split('\n')
        clean_body_lines = []
        i = 0
        while i < len(body_lines):
            line = body_lines[i].strip()
            if line == '#attachment#':
                # Next line is attachment path
                i += 1
                if i < len(body_lines):
                    attach_path = body_lines[i].strip()
                    if attach_path and os.path.isfile(attach_path):
                        attachments.append(attach_path)
                        print(f"Adding attachment: {attach_path}")
            else:
                clean_body_lines.append(body_lines[i])
            i += 1

        clean_body = '\n'.join(clean_body_lines)

        if use_html:
            # Convert spec format to HTML
            html_body = self.spec_to_html(clean_body)
            content_type = "text/html"
            email_body = html_body
        else:
            content_type = "text/plain"
            email_body = clean_body + "\n\n--\nSent by Genie Agent (Claude Code)"

        # Build email content
        if attachments:
            # Use MIME multipart for attachments
            from datetime import datetime as dt
            boundary = "----=_Part_0_" + str(int(dt.now().timestamp()))

            vto_name = self.vtoInfo.get('vto', 'GenieAgent')
            headers = f"""From: Genie AI Agent <{vto_name}@atlmail.amd.com>
To: {to_addr}"""
            if cc_addrs:
                headers += f"\nCc: {', '.join(cc_addrs)}"
            headers += f"""
Subject: {subject}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: {content_type}; charset=utf-8
Content-Transfer-Encoding: quoted-printable

{email_body}
"""
            # Add attachments
            for attach_path in attachments:
                if os.path.isfile(attach_path):
                    filename = os.path.basename(attach_path)
                    mime_type, _ = mimetypes.guess_type(attach_path)
                    if mime_type is None:
                        mime_type = 'application/octet-stream'

                    with open(attach_path, 'rb') as f:
                        file_data = base64.b64encode(f.read()).decode('ascii')

                    headers += f"""
--{boundary}
Content-Type: {mime_type}; name="{filename}"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="{filename}"

{file_data}
"""
            headers += f"\n--{boundary}--"
            email_content = headers
        else:
            # Simple email without attachments
            vto_name = self.vtoInfo.get('vto', 'GenieAgent')
            headers = f"""From: Genie AI Agent <{vto_name}@atlmail.amd.com>
To: {to_addr}"""
            if cc_addrs:
                headers += f"\nCc: {', '.join(cc_addrs)}"
            headers += f"""
Subject: {subject}
MIME-Version: 1.0
Content-Type: {content_type}; charset=utf-8"""

            email_content = f"""{headers}

{email_body}
"""
        try:
            # Use sendmail to send (use full path)
            sendmail_path = '/usr/sbin/sendmail'
            if not os.path.exists(sendmail_path):
                sendmail_path = 'sendmail'  # fallback to PATH

            # Send to all recipients
            all_recipients = to_emails
            process = subprocess.Popen(
                [sendmail_path, '-oi'] + all_recipients,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(input=email_content.encode('utf-8'))

            if process.returncode == 0:
                attach_info = f" with {len(attachments)} attachment(s)" if attachments else ""
                print(f"Email sent successfully to {to_addr}{attach_info}" + (f" (CC: {', '.join(cc_addrs)})" if cc_addrs else ""))
                return True
            else:
                print(f"ERROR: Failed to send email: {stderr.decode()}")
                return False
        except Exception as e:
            print(f"ERROR: Failed to send email: {e}")
            return False

    def run_and_capture(self, instruction_text, send_email_flag=False):
        """Run instruction, capture output, optionally email results to debuggers"""
        script, matched_instruction, arguementInfo, special_content = self.parse_instruction(instruction_text)

        if not script:
            return None, "ERROR: Could not match instruction to any known command"

        # Extract common arguments
        refdir = None
        tile = None
        target = None
        for key, val in arguementInfo.items():
            if key == 'refDir' and val != 'refDir':
                refdir = val.replace('refDir:', '').strip(':')
            if key == 'tile' and val != 'tile':
                tile = val.replace('tile:', '').strip(':')
            if key == 'target' and val != 'target':
                target = val.replace('target:', '').strip(':')

        output_lines = []
        tag = self.generate_tag()

        # Helper to filter flatpak warnings
        def clean_output(text):
            return '\n'.join([line for line in text.split('\n') if 'flatpak' not in line])

        # Determine script type and handle accordingly
        script_handled = False

        # 1. Static check summary - inline perl
        if 'static_check_summary' in script and refdir:
            cmd = f"perl {self.base_dir}/script/rtg_oss_feint/umc/static_check_summary.pl {refdir} umc_top {self.base_dir}/script/rtg_oss_feint/umc/spg_dft_error_filter.txt"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
                output_lines.append(clean_output(result.stdout))
            except Exception as e:
                output_lines.append(f"ERROR: {e}")
            script_handled = True

        # 2. Synthesis timing - inline perl
        elif 'synthesis_timing' in script and refdir:
            date_str = datetime.datetime.now().strftime('%d-%b')
            output_lines.append("#text#")
            output_lines.append("------TIMING AND AREA REPORT------")
            cmd = f"perl {self.base_dir}/script/rtg_oss_feint/supra/synthesis_timing_extract_details.pl {refdir} {date_str} --blocks=UCLK,clock_gating_default"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
                output_lines.append(clean_output(result.stdout))
            except Exception as e:
                output_lines.append(f"ERROR: {e}")
            # LOL report (optional)
            output_lines.append("#text#")
            output_lines.append("-------LOL REPORT-------")
            cmd = f"perl {self.base_dir}/script/rtg_oss_feint/supra/lol_extractor.pl {refdir}"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
                stdout_clean = clean_output(result.stdout)
                if stdout_clean.strip():
                    output_lines.append(stdout_clean)
            except:
                pass
            script_handled = True

        # 3. Check changelist - inline
        elif 'check_cl' in script and refdir:
            try:
                cl_file = os.path.join(refdir, 'configuration_id')
                if os.path.exists(cl_file):
                    with open(cl_file) as f:
                        content = f.read()
                    cl_number = content.split('@')[1].strip() if '@' in content else content.strip()
                    output_lines.append("#text#")
                    output_lines.append(f"The configuration id/changelist number is {cl_number}.")
                else:
                    output_lines.append(f"ERROR: configuration_id file not found in {refdir}")
            except Exception as e:
                output_lines.append(f"ERROR: {e}")
            script_handled = True

        # 4. List tilebuilder directories - inline
        elif 'list_tilebuilder_dirs' in script and refdir:
            output_lines.append("#text#")
            output_lines.append("TileBuilder Directories")
            output_lines.append("========================================")
            output_lines.append(f"Tiles Directory: {refdir}")
            output_lines.append("")
            output_lines.append("#table#")
            output_lines.append("Directory,Type,ModifiedDate")
            try:
                found_count = 0
                for item in os.listdir(refdir):
                    item_path = os.path.join(refdir, item)
                    if os.path.isdir(item_path):
                        revrc_path = os.path.join(item_path, 'revrc.main')
                        if os.path.exists(revrc_path):
                            mod_time = os.path.getmtime(item_path)
                            mod_date = datetime.datetime.fromtimestamp(mod_time).strftime('%b %d %H:%M')
                            output_lines.append(f"{item_path},TileBuilder,{mod_date}")
                            found_count += 1
                output_lines.append("#table end#")
                output_lines.append("")
                output_lines.append(f"Total TileBuilder directories found: {found_count}")
            except Exception as e:
                output_lines.append("#table end#")
                output_lines.append(f"ERROR: {e}")
            script_handled = True

        # 5. List instructions - inline
        elif 'list_instruction' in script:
            output_lines.append("#text#")
            output_lines.append("Available Instructions")
            output_lines.append("========================================")
            instruction_file = os.path.join(self.base_dir, 'instruction.csv')
            try:
                with open(instruction_file, encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) >= 2:
                            output_lines.append(f"  {row[0]}")
            except Exception as e:
                output_lines.append(f"ERROR: {e}")
            script_handled = True

        # 6. Long-running commands - execute script and return tag
        if not script_handled:
            # Create and execute the run script
            arguementInfo['tag'] = tag
            command = self.build_command(script, arguementInfo)

            # Create data directory
            data_dir = os.path.join(self.base_dir, 'data', tag)
            os.makedirs(data_dir, exist_ok=True)

            # Create spec file (empty - will be populated by script)
            spec_file = os.path.join(self.base_dir, 'data', f'{tag}_spec')
            with open(spec_file, 'w') as f:
                pass  # Create empty file, script will populate with results

            # If email requested, create email flag file with debugger emails
            if send_email_flag and self.debugger_emails:
                email_flag_file = os.path.join(self.base_dir, 'data', f'{tag}_email')
                with open(email_flag_file, 'w') as f:
                    f.write(','.join(self.debugger_emails))

            # Create run script
            run_script = os.path.join(self.base_dir, 'runs', f'{tag}.csh')
            os.makedirs(os.path.dirname(run_script), exist_ok=True)

            with open(run_script, 'w') as f:
                f.write("#!/bin/tcsh -f\n")
                f.write(f"# Genie CLI generated script\n")
                f.write(f"# Tag: {tag}\n")
                # Handle multi-line instructions - comment each line
                instruction_commented = instruction_text.replace('\n', '\n# ')
                f.write(f"# Instruction: {instruction_commented}\n\n")
                f.write(f"cd {self.base_dir}\n")
                f.write(f"source csh/env.csh\n")
                f.write(f"set tag = {tag}\n")
                f.write(f"set tasksModelFile = tasksModelCLI.csv\n")
                f.write(f"source script/{command}\n")
                f.write(f"# Send email if flag file exists (use hardcoded path to avoid variable issues)\n")
                f.write(f"if (-f {self.base_dir}/data/{tag}_email) then\n")
                f.write(f"    python3 {self.base_dir}/script/genie_cli.py --send-completion-email {tag}\n")
                f.write(f"endif\n")
                f.write(f"source script/rtg_oss_feint/finishing_task.csh\n")

            # Execute in background
            subprocess.Popen(
                f"cd {self.base_dir} && source {run_script} >& runs/{tag}.log &",
                shell=True, executable='/bin/csh'
            )

            log_file = os.path.join(self.base_dir, 'runs', f'{tag}.log')
            results_file = os.path.join(self.base_dir, 'data', f'{tag}_spec')

            output_lines.append("#text#")
            output_lines.append(f"Task Submitted")
            output_lines.append("========================================")
            output_lines.append(f"Instruction: {instruction_text}")
            output_lines.append(f"Matched: {matched_instruction}")
            output_lines.append(f"Tag: {tag}")
            output_lines.append("")
            output_lines.append(f"Log File: {log_file}")
            output_lines.append(f"Results: {results_file}")
            if send_email_flag:
                output_lines.append("")
                output_lines.append(f"Email will be sent to debuggers when task completes.")

            # Don't send email now for long-running tasks - it will be sent on completion
            send_email_flag = False

        output = "\n".join(output_lines)

        # Send email to all debuggers if requested (single email with CC)
        if send_email_flag and self.debugger_emails:
            subject = f"Genie CLI - {matched_instruction[:50]}"
            self.send_email(self.debugger_emails, subject, output)

        return output, None


def main():
    parser = argparse.ArgumentParser(
        description='Genie CLI - Direct instruction interface for Claude Code',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python genie_cli.py --instruction "run cdc_rdc at /proj/xxx/tile1"
  python genie_cli.py --instruction "monitor supra run at /proj/xxx" --execute
  python genie_cli.py --instruction "summarize static check at /proj/xxx" --email user@amd.com
  python genie_cli.py --list
        """
    )

    parser.add_argument('--instruction', '-i', type=str,
                        help='The instruction to parse and execute')
    parser.add_argument('--execute', '-e', action='store_true',
                        help='Actually execute the command (default is dry-run)')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List all available instructions')
    parser.add_argument('--base-dir', '-b', type=str,
                        help='Base directory for the agent (default: auto-detect)')
    parser.add_argument('--email', '-m', action='store_true',
                        help='Send results to debugger emails from assignment.csv')
    parser.add_argument('--to', type=str, metavar='EMAIL',
                        help='Override email recipients (comma-separated). Use with --email')
    parser.add_argument('--send-completion-email', type=str, metavar='TAG',
                        help='Internal: Send completion email for a finished task')
    parser.add_argument('--send-analysis-email', type=str, metavar='TAG',
                        help='Internal: Send analysis HTML report email for a completed analysis')
    parser.add_argument('--send-fixer-round-email', type=str, metavar='TAG',
                        help='Internal: Send per-round fixer HTML report email (use with --round and --check-type)')
    parser.add_argument('--send-fixer-summary-email', type=str, metavar='FIRST_TAG',
                        help='Internal: Send final fixer summary email (use with --check-type and --result)')
    parser.add_argument('--check-type', type=str, metavar='CHECK_TYPE',
                        help='Check type for --send-analysis-email / --send-fixer-round-email / --send-fixer-summary-email (cdc_rdc, lint, spg_dft)')
    parser.add_argument('--round', type=int, metavar='N',
                        help='Round number for --send-fixer-round-email')
    parser.add_argument('--result', type=str, metavar='RESULT',
                        help='Final result for --send-fixer-summary-email (CLEAN, STALLED, MAX_ROUNDS_REACHED)')
    parser.add_argument('--kill', '-k', type=str, metavar='TAG',
                        help='Kill a running background task by tag')
    parser.add_argument('--status', '-s', type=str, metavar='TAG',
                        help='Check status of a task by tag')
    parser.add_argument('--tasks', '-t', type=str, nargs='?', const='today', metavar='FILTER',
                        help='List tasks: running, today, yesterday, or YYYY-MM-DD')
    parser.add_argument('--xterm', '-x', action='store_true',
                        help='Run task in xterm popup window instead of background')
    parser.add_argument('--analyze', '-a', action='store_true',
                        help='Analyze mode: Claude Code monitors and analyzes results (only for cdc_rdc, lint, spg_dft, full_static_check)')
    parser.add_argument('--analyze-only', type=str, metavar='TAG',
                        help='Skip running static check — analyze existing results for TAG directly (no monitoring step)')
    parser.add_argument('--analyze-fixer', action='store_true',
                        help='Analyze-fixer mode: analyze violations, auto-apply constraint fixes, rerun check — loops until clean (max 5 rounds)')
    parser.add_argument('--analyze-fixer-only', type=str, metavar='TAG',
                        help='Skip running check — run analyze-fixer on existing results for TAG directly (no monitoring step)')
    parser.add_argument('--setup-user', action='store_true',
                        help='Setup user-specific directory for multi-user environment')
    parser.add_argument('--user-email', type=str, metavar='EMAIL',
                        help='Email address for --setup-user (required, or will prompt)')
    parser.add_argument('--user-disk', type=str, metavar='PATH',
                        help='Disk path for --setup-user (required, or will prompt)')

    args = parser.parse_args()

    # Handle --setup-user before initializing CLI
    if args.setup_user:
        setup_user_directory(args.base_dir, args.user_email, args.user_disk)
        sys.exit(0)

    # Initialize CLI
    cli = GenieCLI(base_dir=args.base_dir)

    # Handle --analyze-only: emit ANALYZE_MODE_ENABLED for existing results, skip monitoring
    if args.analyze_only:
        tag = args.analyze_only
        analyze_file = os.path.join(cli.base_dir, 'data', f'{tag}_analyze')
        spec_file    = os.path.join(cli.base_dir, 'data', f'{tag}_spec')

        # Read metadata from _analyze file
        check_type = ref_dir = ip = log_file = ''
        if os.path.exists(analyze_file):
            with open(analyze_file) as f:
                for line in f:
                    k, _, v = line.strip().partition('=')
                    if k == 'check_type': check_type = v
                    elif k == 'ref_dir':  ref_dir   = v
                    elif k == 'ip':       ip        = v
                    elif k == 'log_file': log_file  = v

        # Fallback: extract ref_dir from spec file if missing
        if not ref_dir and os.path.exists(spec_file):
            with open(spec_file) as f:
                for line in f:
                    if line.startswith('Tree Path:') or line.startswith('Tree:'):
                        ref_dir = line.split(':', 1)[1].strip()
                        break

        if not tag or (not os.path.exists(analyze_file) and not os.path.exists(spec_file)):
            print(f"ERROR: No analyze or spec file found for tag '{tag}'")
            print(f"  Looked for: {analyze_file}")
            print(f"          or: {spec_file}")
            sys.exit(1)

        if not log_file:
            log_file = os.path.join(cli.base_dir, 'runs', f'{tag}.log')

        print(f"Analyze-only mode for tag: {tag}")
        print(f"  check_type : {check_type or '(unknown)'}")
        print(f"  ref_dir    : {ref_dir or '(unknown)'}")
        print(f"  ip         : {ip or '(unknown)'}")
        print()
        print("=" * 70)
        print("ANALYZE_MODE_ENABLED")
        print(f"TAG={tag}")
        print(f"CHECK_TYPE={check_type}")
        print(f"REF_DIR={ref_dir}")
        print(f"IP={ip}")
        print(f"LOG_FILE={log_file}")
        print(f"SPEC_FILE={spec_file}")
        print("SKIP_MONITORING=true")
        print("=" * 70)
        return

    # Handle --analyze-fixer-only: emit ANALYZE_FIXER_MODE_ENABLED for existing results, skip monitoring
    if args.analyze_fixer_only:
        tag = args.analyze_fixer_only
        analyze_file     = os.path.join(cli.base_dir, 'data', f'{tag}_analyze')
        spec_file        = os.path.join(cli.base_dir, 'data', f'{tag}_spec')
        fixer_state_file = os.path.join(cli.base_dir, 'data', f'{tag}_fixer_state')

        check_type = ref_dir = ip = log_file = ''
        if os.path.exists(analyze_file):
            with open(analyze_file) as f:
                for line in f:
                    k, _, v = line.strip().partition('=')
                    if k == 'check_type': check_type = v
                    elif k == 'ref_dir':  ref_dir   = v
                    elif k == 'ip':       ip        = v
                    elif k == 'log_file': log_file  = v

        if not ref_dir and os.path.exists(spec_file):
            with open(spec_file) as f:
                for line in f:
                    if line.startswith('Tree Path:') or line.startswith('Tree:'):
                        ref_dir = line.split(':', 1)[1].strip()
                        break

        if not tag or (not os.path.exists(analyze_file) and not os.path.exists(spec_file)):
            print(f"ERROR: No analyze or spec file found for tag '{tag}'")
            print(f"  Looked for: {analyze_file}")
            print(f"          or: {spec_file}")
            sys.exit(1)

        if not log_file:
            log_file = os.path.join(cli.base_dir, 'runs', f'{tag}.log')

        # Write fixer_state for round 1
        os.makedirs(os.path.join(cli.base_dir, 'data'), exist_ok=True)
        with open(fixer_state_file, 'w') as f:
            f.write(f"original_ref_dir={ref_dir}\n")
            f.write(f"original_ip={ip}\n")
            f.write(f"original_check_type={check_type}\n")
            f.write(f"original_instruction=run {check_type} at {ref_dir} for {ip}\n")
            f.write(f"round=1\n")
            f.write(f"max_rounds=5\n")
            f.write(f"parent_tag=\n")
            f.write(f"use_email=true\n")
            f.write(f"use_xterm=false\n")
            f.write(f"email_to={getattr(args, 'to', '') or ''}\n")

        print(f"Analyze-fixer-only mode for tag: {tag}")
        print(f"  check_type : {check_type or '(unknown)'}")
        print(f"  ref_dir    : {ref_dir or '(unknown)'}")
        print(f"  ip         : {ip or '(unknown)'}")
        print()
        print("=" * 70)
        print("ANALYZE_FIXER_MODE_ENABLED")
        print(f"TAG={tag}")
        print(f"CHECK_TYPE={check_type}")
        print(f"REF_DIR={ref_dir}")
        print(f"IP={ip}")
        print(f"LOG_FILE={log_file}")
        print(f"SPEC_FILE={spec_file}")
        print("MAX_ROUNDS=5")
        print("FIXER_ROUND=1")
        print("SKIP_MONITORING=true")
        print("=" * 70)
        return

    # Handle completion email (called by run script when task finishes)
    if args.send_completion_email:
        tag = args.send_completion_email
        email_flag_file = os.path.join(cli.base_dir, 'data', f'{tag}_email')
        spec_file = os.path.join(cli.base_dir, 'data', f'{tag}_spec')

        if os.path.exists(email_flag_file) and os.path.exists(spec_file):
            # Read emails from flag file
            with open(email_flag_file) as f:
                emails = f.read().strip().split(',')

            # Read spec content
            with open(spec_file) as f:
                spec_content = f.read()

            # Read metadata for better email subject
            metadata_file = os.path.join(cli.base_dir, 'data', f'{tag}_metadata')
            task_type = 'task'
            tile = ''
            dir_name = ''
            ip = ''
            project_name = ''
            if os.path.exists(metadata_file):
                with open(metadata_file) as f:
                    for line in f:
                        if line.startswith('task_type='):
                            task_type = line.split('=', 1)[1].strip()
                        elif line.startswith('tile='):
                            tile = line.split('=', 1)[1].strip()
                        elif line.startswith('dir_name='):
                            dir_name = line.split('=', 1)[1].strip()
                        elif line.startswith('ip='):
                            ip = line.split('=', 1)[1].strip()
                        elif line.startswith('project_name='):
                            project_name = line.split('=', 1)[1].strip()

            # Determine status from spec content
            # Be smarter about detection - look for actual failure patterns, not column headers
            status = 'Completed'
            spec_lower = spec_content.lower()

            # Patterns that indicate failure (more specific)
            failure_patterns = [
                'status: failed', 'status:failed', 'run_status,failed',
                'task failed', 'script failed', 'execution failed',
                'error:', 'fatal error', 'critical error',
                'status: error', 'exit status: 1', 'exit code: 1'
            ]

            # Patterns that indicate success
            success_patterns = [
                'status: success', 'status:success', 'run_status,complete',
                'task completed successfully', 'completed successfully',
                'status: passed', 'all checks passed', 'success'
            ]

            # Check for failure patterns first
            is_failed = any(pattern in spec_lower for pattern in failure_patterns)

            # Check for success patterns
            is_success = any(pattern in spec_lower for pattern in success_patterns)

            # For static check summary, check if Run_Status shows Complete (not Failed)
            if 'run_status' in spec_lower:
                if ',complete,' in spec_lower or ',complete\n' in spec_lower:
                    is_success = True
                    is_failed = False
                elif ',failed,' in spec_lower or ',failed\n' in spec_lower:
                    is_failed = True
                    is_success = False

            if is_failed and not is_success:
                status = 'Failed'
            elif is_success:
                status = 'Success'

            # Build descriptive subject
            # Format: 25Feb 04:18 - CDC_RDC umc17_0 (TAG) (Completed)
            # Convert tag (YYYYMMDDHHMMSS) to date and time in Malaysia timezone (UTC+8)
            try:
                from datetime import datetime, timezone, timedelta
                # Parse tag as local time (server time, typically EST/UTC-5)
                tag_datetime = datetime.strptime(tag[:12], '%Y%m%d%H%M')
                # Get local timezone offset by comparing local time to UTC
                local_now = datetime.now()
                utc_now = datetime.utcnow()
                local_offset = local_now - utc_now
                local_offset_hours = round(local_offset.total_seconds() / 3600)
                # Assign local timezone to the parsed time
                local_tz = timezone(timedelta(hours=local_offset_hours))
                tag_datetime = tag_datetime.replace(tzinfo=local_tz)
                # Convert to Malaysia timezone (UTC+8)
                malaysia_tz = timezone(timedelta(hours=8))
                tag_datetime_myt = tag_datetime.astimezone(malaysia_tz)
                datetime_str = tag_datetime_myt.strftime('%d%b %H:%M')
            except:
                datetime_str = tag[:12]

            # Include project name and IP for static check tasks
            # Format: 02Mar 04:30 - grimlock umc17_0 LINT (tag) (status)
            static_check_types = ['cdc_rdc', 'lint', 'spg_dft', 'build_rtl', 'full_static_check', 'static_check']
            if any(check in task_type.lower() for check in static_check_types):
                if project_name and ip:
                    subject = f"{datetime_str} - {project_name} {ip} {task_type.upper()} ({tag}) ({status})"
                elif ip:
                    subject = f"{datetime_str} - {ip} {task_type.upper()} ({tag}) ({status})"
                else:
                    subject = f"{datetime_str} - {task_type.upper()} ({tag}) ({status})"
            else:
                subject = f"{datetime_str} - {task_type.upper()} ({tag}) ({status})"

            cli.send_email(emails, subject, spec_content)

            # Remove flag file
            os.remove(email_flag_file)
        return

    # Handle analysis email (called by Claude Code after analysis completes)
    if args.send_analysis_email:
        tag = args.send_analysis_email
        analyze_file = os.path.join(cli.base_dir, 'data', f'{tag}_analyze')
        email_flag_file = os.path.join(cli.base_dir, 'data', f'{tag}_email')
        analysis_email_file = os.path.join(cli.base_dir, 'data', f'{tag}_analysis_email')

        # Determine which HTML file to read based on --check-type
        check_type_arg = getattr(args, 'check_type', None)
        check_suffix_map = {'cdc_rdc': 'cdc', 'lint': 'lint', 'spg_dft': 'spgdft'}
        if check_type_arg and check_type_arg in check_suffix_map:
            suffix = check_suffix_map[check_type_arg]
            analysis_html_file = os.path.join(cli.base_dir, 'data', f'{tag}_analysis_{suffix}.html')
            email_check_type = check_type_arg.upper().replace('_', '/')
        else:
            # Fallback: try legacy file or suffix-less file
            analysis_html_file = os.path.join(cli.base_dir, 'data', f'{tag}_analysis.html')
            email_check_type = None

        # Get email recipients — priority: --to > _analysis_email > _email > assignment.csv
        emails = []
        if args.to:
            emails = [e.strip() for e in args.to.split(',')]
            print(f"Analysis email recipients overridden to: {', '.join(emails)}")
        elif os.path.exists(analysis_email_file):
            with open(analysis_email_file) as f:
                emails = f.read().strip().split(',')
        elif os.path.exists(email_flag_file):
            with open(email_flag_file) as f:
                emails = f.read().strip().split(',')
        elif cli.debugger_emails:
            emails = cli.debugger_emails

        if not emails:
            print(f"Error: No email recipients found for tag {tag}")
            sys.exit(1)

        if not os.path.exists(analysis_html_file):
            print(f"Error: Analysis HTML file not found: {analysis_html_file}")
            sys.exit(1)

        # Read HTML content
        with open(analysis_html_file) as f:
            html_content = f.read()

        # Read metadata for email subject
        ref_dir = ''
        ip = ''
        meta_check_type = 'ANALYSIS'
        if os.path.exists(analyze_file):
            with open(analyze_file) as f:
                for line in f:
                    if line.startswith('check_type='):
                        meta_check_type = line.split('=', 1)[1].strip().upper()
                    elif line.startswith('ref_dir='):
                        ref_dir = line.split('=', 1)[1].strip()
                    elif line.startswith('ip='):
                        ip = line.split('=', 1)[1].strip()

        # Use --check-type label if given, else fall back to metadata
        check_type_label = email_check_type if email_check_type else meta_check_type

        # Get directory name from ref_dir
        dir_name = os.path.basename(ref_dir) if ref_dir else ''

        # Build subject
        # Format: [Analysis] CDC/RDC - umc17_0 @ tree_name (tag)
        if ip and dir_name:
            subject = f"[Analysis] {check_type_label} - {ip} @ {dir_name} ({tag})"
        elif ip:
            subject = f"[Analysis] {check_type_label} - {ip} ({tag})"
        else:
            subject = f"[Analysis] {check_type_label} ({tag})"

        # Send email with HTML content
        cli.send_email(emails, subject, html_content, use_html=True)
        print(f"Analysis email sent to: {', '.join(emails)}")
        print(f"Subject: {subject}")
        # Clean up analysis email file only when last check type email is sent
        # (for full_static_check this is called 3 times — keep file until all 3 are done)
        if check_type_arg == 'spg_dft' or not check_type_arg:
            if os.path.exists(analysis_email_file):
                os.remove(analysis_email_file)
        return

    # Handle --send-fixer-round-email: send per-round fixer HTML report email
    if args.send_fixer_round_email:
        tag = args.send_fixer_round_email
        round_n = getattr(args, 'round', None)
        check_type_arg = getattr(args, 'check_type', None)

        if round_n is None:
            print("Error: --send-fixer-round-email requires --round N")
            sys.exit(1)
        if not check_type_arg:
            print("Error: --send-fixer-round-email requires --check-type (cdc_rdc, lint, spg_dft)")
            sys.exit(1)

        analyze_file      = os.path.join(cli.base_dir, 'data', f'{tag}_analyze')
        fixer_state_file  = os.path.join(cli.base_dir, 'data', f'{tag}_fixer_state')
        email_flag_file   = os.path.join(cli.base_dir, 'data', f'{tag}_email')
        analysis_email_file = os.path.join(cli.base_dir, 'data', f'{tag}_analysis_email')
        round_html_file   = os.path.join(cli.base_dir, 'data', f'{tag}_analysis_fixer_round{round_n}.html')

        if not os.path.exists(round_html_file):
            print(f"Error: Round {round_n} HTML report not found: {round_html_file}")
            sys.exit(1)

        # Read metadata
        ref_dir = ip = ''
        if os.path.exists(analyze_file):
            with open(analyze_file) as f:
                for line in f:
                    if line.startswith('ref_dir='): ref_dir = line.split('=', 1)[1].strip()
                    elif line.startswith('ip='):     ip      = line.split('=', 1)[1].strip()

        max_rounds = 5
        if os.path.exists(fixer_state_file):
            with open(fixer_state_file) as f:
                for line in f:
                    if line.startswith('max_rounds='): max_rounds = int(line.split('=', 1)[1].strip())

        # Get email recipients — priority: _analysis_email > _email > assignment.csv
        emails = []
        if os.path.exists(analysis_email_file):
            with open(analysis_email_file) as f:
                emails = [e.strip() for e in f.read().strip().split(',') if e.strip()]
        elif os.path.exists(email_flag_file):
            with open(email_flag_file) as f:
                emails = [e.strip() for e in f.read().strip().split(',') if e.strip()]
        elif cli.debugger_emails:
            emails = cli.debugger_emails

        if not emails:
            print(f"Error: No email recipients found for tag {tag}")
            sys.exit(1)

        # Build subject
        check_label = check_type_arg.upper().replace('_', '/')
        dir_name = os.path.basename(ref_dir) if ref_dir else ''
        if ip and dir_name:
            subject = f"[Fixer Round {round_n}/{max_rounds}] {check_label} - {ip} @ {dir_name} ({tag})"
        elif ip:
            subject = f"[Fixer Round {round_n}/{max_rounds}] {check_label} - {ip} ({tag})"
        else:
            subject = f"[Fixer Round {round_n}/{max_rounds}] {check_label} ({tag})"

        with open(round_html_file) as f:
            html_content = f.read()

        cli.send_email(emails, subject, html_content, use_html=True)
        print(f"Fixer round {round_n} email sent to: {', '.join(emails)}")
        print(f"Subject: {subject}")
        return

    # Handle --send-fixer-summary-email: send final fixer summary email
    if args.send_fixer_summary_email:
        first_tag = args.send_fixer_summary_email
        check_type_arg = getattr(args, 'check_type', None)
        result_arg = getattr(args, 'result', None) or 'DONE'

        if not check_type_arg:
            print("Error: --send-fixer-summary-email requires --check-type (cdc_rdc, lint, spg_dft)")
            sys.exit(1)

        analyze_file      = os.path.join(cli.base_dir, 'data', f'{first_tag}_analyze')
        fixer_state_file  = os.path.join(cli.base_dir, 'data', f'{first_tag}_fixer_state')
        email_flag_file   = os.path.join(cli.base_dir, 'data', f'{first_tag}_email')
        analysis_email_file = os.path.join(cli.base_dir, 'data', f'{first_tag}_analysis_email')
        summary_html_file = os.path.join(cli.base_dir, 'data', f'{first_tag}_fixer_summary.html')

        if not os.path.exists(summary_html_file):
            print(f"Error: Fixer summary HTML not found: {summary_html_file}")
            sys.exit(1)

        # Read metadata
        ref_dir = ip = ''
        if os.path.exists(analyze_file):
            with open(analyze_file) as f:
                for line in f:
                    if line.startswith('ref_dir='): ref_dir = line.split('=', 1)[1].strip()
                    elif line.startswith('ip='):     ip      = line.split('=', 1)[1].strip()

        # Get email recipients
        emails = []
        if os.path.exists(analysis_email_file):
            with open(analysis_email_file) as f:
                emails = [e.strip() for e in f.read().strip().split(',') if e.strip()]
        elif os.path.exists(email_flag_file):
            with open(email_flag_file) as f:
                emails = [e.strip() for e in f.read().strip().split(',') if e.strip()]
        elif cli.debugger_emails:
            emails = cli.debugger_emails

        if not emails:
            print(f"Error: No email recipients found for tag {first_tag}")
            sys.exit(1)

        # Build subject based on result
        check_label = check_type_arg.upper().replace('_', '/')
        dir_name = os.path.basename(ref_dir) if ref_dir else ''
        base = f"{check_label} - {ip} @ {dir_name}" if (ip and dir_name) else (f"{check_label} - {ip}" if ip else check_label)
        result_upper = result_arg.upper()
        if 'CLEAN' in result_upper:
            subject = f"[Fixer CLEAN] {base} ({first_tag})"
        elif 'STALLED' in result_upper:
            subject = f"[Fixer STALLED] {base} — violations remain, manual fix needed ({first_tag})"
        elif 'MAX' in result_upper:
            subject = f"[Fixer MAX ROUNDS] {base} — violations remain ({first_tag})"
        else:
            subject = f"[Fixer Done] {base} ({first_tag})"

        with open(summary_html_file) as f:
            html_content = f.read()

        cli.send_email(emails, subject, html_content, use_html=True)
        print(f"Fixer summary email sent to: {', '.join(emails)}")
        print(f"Subject: {subject}")
        return

    # Handle kill task
    if args.kill:
        tag = args.kill
        print(f"Killing task: {tag}")

        pid_file = os.path.join(cli.base_dir, 'data', f'{tag}_pid')
        killed = False

        try:
            # First try to use saved PID
            if os.path.exists(pid_file):
                with open(pid_file) as f:
                    pid = int(f.read().strip())

                # Kill the entire process group (all child processes)
                try:
                    os.killpg(pid, 9)  # SIGKILL to process group
                    print(f"  Killed process group: {pid}")
                    killed = True
                except ProcessLookupError:
                    print(f"  Process {pid} already terminated")
                except PermissionError:
                    # Try regular kill
                    subprocess.run(f"kill -9 {pid}", shell=True)
                    print(f"  Killed PID: {pid}")
                    killed = True

                # Remove PID file
                os.remove(pid_file)
                print(f"  Removed PID file")
            else:
                # Fallback: search for processes with tag in command line
                result = subprocess.run(
                    f"ps aux | grep '{tag}' | grep -v grep | grep -v 'genie_cli.py --kill' | awk '{{print $2}}'",
                    shell=True, capture_output=True, text=True
                )
                pids = result.stdout.strip().split('\n')
                pids = [p for p in pids if p]

                if pids:
                    for pid in pids:
                        subprocess.run(f"kill -9 {pid}", shell=True)
                        print(f"  Killed PID: {pid}")
                    killed = True
                else:
                    print(f"No PID file and no running process found for tag {tag}")

            if killed:
                print(f"Task {tag} killed successfully")

            # Also remove email flag if exists
            email_flag = os.path.join(cli.base_dir, 'data', f'{tag}_email')
            if os.path.exists(email_flag):
                os.remove(email_flag)
                print(f"  Removed email flag file")

        except Exception as e:
            print(f"Error killing task: {e}")
        return

    # Handle status check
    if args.status:
        tag = args.status
        print(f"=" * 70)
        print(f"Task Status: {tag}")
        print(f"=" * 70)

        # Check if PID file exists and process is running
        pid_file = os.path.join(cli.base_dir, 'data', f'{tag}_pid')
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = f.read().strip()
            # Check if process is still running
            result = subprocess.run(f"ps -p {pid} -o pid,cmd --no-headers", shell=True, capture_output=True, text=True)
            if result.stdout.strip():
                print(f"Status: RUNNING")
                print(f"PID: {pid}")
            else:
                print(f"Status: COMPLETED (PID {pid} no longer running)")
        else:
            print("Status: COMPLETED or NOT STARTED (no PID file)")

        # Show log file tail
        log_file = os.path.join(cli.base_dir, 'runs', f'{tag}.log')
        if os.path.exists(log_file):
            print()
            print(f"Log file: {log_file}")
            print("-" * 70)
            result = subprocess.run(f"tail -20 {log_file}", shell=True, capture_output=True, text=True)
            print(result.stdout)
        else:
            print(f"Log file not found: {log_file}")

        # Check spec file
        spec_file = os.path.join(cli.base_dir, 'data', f'{tag}_spec')
        if os.path.exists(spec_file):
            print()
            print(f"Spec file: {spec_file}")

        # Check email flag
        email_flag = os.path.join(cli.base_dir, 'data', f'{tag}_email')
        if os.path.exists(email_flag):
            print(f"Email pending: Yes (will send on completion)")
        return

    if args.tasks:
        from datetime import datetime, timedelta
        import glob

        filter_type = args.tasks.lower()
        data_dir = os.path.join(cli.base_dir, 'data')
        runs_dir = os.path.join(cli.base_dir, 'runs')

        # Determine date filter
        target_date = None
        show_running_only = False

        if filter_type == 'running':
            show_running_only = True
            print("=" * 80)
            print("Currently Running Tasks")
            print("=" * 80)
        elif filter_type == 'today':
            target_date = datetime.now().strftime('%Y%m%d')
            print("=" * 80)
            print(f"Tasks Launched Today ({datetime.now().strftime('%Y-%m-%d')})")
            print("=" * 80)
        elif filter_type == 'yesterday':
            target_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
            print("=" * 80)
            print(f"Tasks Launched Yesterday ({(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')})")
            print("=" * 80)
        elif re.match(r'^\d{4}-\d{2}-\d{2}$', filter_type):
            # Date format YYYY-MM-DD
            target_date = filter_type.replace('-', '')
            print("=" * 80)
            print(f"Tasks Launched on {filter_type}")
            print("=" * 80)
        else:
            print(f"Unknown filter: {filter_type}")
            print("Usage: --tasks [running|today|yesterday|YYYY-MM-DD]")
            return

        # Find all PID files (these represent launched tasks)
        pid_files = glob.glob(os.path.join(data_dir, '*_pid'))

        # Also find spec files for completed tasks
        spec_files = glob.glob(os.path.join(data_dir, '*_spec'))

        # Collect all unique tags
        tags = set()
        for f in pid_files + spec_files:
            basename = os.path.basename(f)
            tag = basename.replace('_pid', '').replace('_spec', '')
            if re.match(r'^\d{14}$', tag):  # Valid tag format YYYYMMDDHHMMSS
                tags.add(tag)

        # Filter and display
        tasks_found = []
        for tag in sorted(tags, reverse=True):  # Newest first
            # Filter by date if specified
            if target_date and not tag.startswith(target_date):
                continue

            # Check if running
            pid_file = os.path.join(data_dir, f'{tag}_pid')
            is_running = False
            pid = None
            if os.path.exists(pid_file):
                with open(pid_file) as f:
                    pid = f.read().strip()
                result = subprocess.run(f"ps -p {pid} -o pid= 2>/dev/null", shell=True, capture_output=True, text=True)
                is_running = bool(result.stdout.strip())

            # Filter for running only
            if show_running_only and not is_running:
                continue

            # Get task info from metadata
            metadata_file = os.path.join(data_dir, f'{tag}_metadata')
            task_type = '-'
            tile = '-'
            dir_name = '-'
            instruction = '-'
            if os.path.exists(metadata_file):
                with open(metadata_file) as f:
                    for line in f:
                        if line.startswith('task_type='):
                            task_type = line.split('=', 1)[1].strip()
                        elif line.startswith('tile='):
                            tile = line.split('=', 1)[1].strip()
                        elif line.startswith('dir_name='):
                            dir_name = line.split('=', 1)[1].strip()
                        elif line.startswith('instruction='):
                            instruction = line.split('=', 1)[1].strip()

            # Extract action from instruction (e.g., "could you monitor supra run" -> "monitor")
            action = '-'
            if instruction and instruction != '-':
                instr_lower = instruction.lower()
                if 'monitor' in instr_lower:
                    action = 'monitor'
                elif 'run supra' in instr_lower or 'start supra' in instr_lower:
                    action = 'run_supra'
                elif 'run cdc' in instr_lower or 'run lint' in instr_lower or 'run spg' in instr_lower:
                    action = 'static_check'
                elif 'report timing' in instr_lower:
                    action = 'timing_rpt'
                elif 'report formality' in instr_lower:
                    action = 'formality_rpt'
                elif 'report utilization' in instr_lower:
                    action = 'util_rpt'
                elif 'summarize' in instr_lower:
                    action = 'summarize'
                elif 'branch' in instr_lower:
                    action = 'branch'
                elif 'sync' in instr_lower:
                    action = 'sync_tree'
                elif 'full_static' in instr_lower:
                    action = 'full_static'
                else:
                    # Use first few words
                    words = instruction.replace('could you ', '').split()[:2]
                    action = '_'.join(words)[:12]

            # Format timestamp
            try:
                dt = datetime.strptime(tag, '%Y%m%d%H%M%S')
                time_str = dt.strftime('%H:%M:%S')
            except:
                time_str = tag

            status = 'RUNNING' if is_running else 'DONE'
            tasks_found.append({
                'tag': tag,
                'time': time_str,
                'status': status,
                'action': action,
                'type': task_type[:15],
                'tile': tile[:12] if tile else '-',
                'dir': dir_name[:25] if dir_name else '-',
                'pid': pid if is_running else '-'
            })

        if tasks_found:
            # Print header
            print(f"{'Tag':<16} {'Time':<10} {'Status':<8} {'Action':<12} {'Target':<15} {'Tile':<12} {'PID':<10}")
            print("-" * 90)
            for t in tasks_found:
                print(f"{t['tag']:<16} {t['time']:<10} {t['status']:<8} {t['action']:<12} {t['type']:<15} {t['tile']:<12} {t['pid']:<10}")
            print("-" * 80)
            print(f"Total: {len(tasks_found)} task(s)")
        else:
            print("No tasks found.")
        return

    if args.list:
        cli.list_instructions()
        return

    if not args.instruction:
        parser.print_help()
        return

    # Override debugger emails if --to is specified
    if args.to:
        cli.debugger_emails = [e.strip() for e in args.to.split(',')]
        print(f"Email recipients overridden to: {', '.join(cli.debugger_emails)}")

    # Check if email is requested but no debugger emails found
    if args.email and not cli.debugger_emails:
        print("ERROR: No debugger emails found in assignment.csv (use --to to specify)")
        return

    # If email only (no execute), use run_and_capture mode for immediate results
    if args.email and not args.execute:
        print(f"Will send to: {', '.join(cli.debugger_emails)}")
        output, error = cli.run_and_capture(args.instruction, send_email_flag=True)
        if error:
            print(error)
        else:
            print(output)
        return

    # Validate --analyze and --analyze-fixer flags: only valid for static check commands
    ANALYZE_VALID_CHECKS = ['cdc_rdc', 'cdc', 'rdc', 'lint', 'spg_dft', 'full_static_check']
    analyze_mode = args.analyze
    fixer_mode = args.analyze_fixer
    if analyze_mode or fixer_mode:
        # Quick check if instruction contains a valid check type
        instruction_lower = args.instruction.lower()
        is_valid_analyze = any(check in instruction_lower for check in ANALYZE_VALID_CHECKS)
        if not is_valid_analyze:
            print("WARNING: --analyze/--analyze-fixer flag is only valid for static checks (cdc_rdc, lint, spg_dft, full_static_check)")
            print("         Disabling analyze/fixer mode for this command.")
            analyze_mode = False
            fixer_mode = False
    # fixer_mode implies analyze_mode
    if fixer_mode:
        analyze_mode = True

    # Execute instruction (with optional email on completion)
    result = cli.execute(args.instruction, dry_run=not args.execute, send_email=args.email, use_xterm=args.xterm, analyze_mode=analyze_mode, fixer_mode=fixer_mode, email_to=getattr(args, 'to', None))

    if result:
        print()
        print("=" * 70)

        # analyze_only / analyze_fixer_only instruction: emit signal immediately, skip monitoring
        if result.get('analyze_only'):
            tag = result.get('tag', '')
            check_type = result.get('args', {}).get('checkType', '').replace('checkType:', '').strip(':') or 'cdc_rdc'
            ref_dir = result.get('args', {}).get('refDir', '').replace('refDir:', '').strip(':')
            ip = result.get('args', {}).get('ip', '').replace('ip:', '').strip(':')
            if result.get('analyze_fixer_only'):
                # Write fixer_state for round 1
                fixer_state_file = os.path.join(cli.base_dir, 'data', f'{tag}_fixer_state')
                with open(fixer_state_file, 'w') as f:
                    f.write(f"original_ref_dir={ref_dir}\n")
                    f.write(f"original_ip={ip}\n")
                    f.write(f"original_check_type={check_type}\n")
                    f.write(f"original_instruction=run {check_type} at {ref_dir} for {ip}\n")
                    f.write(f"round=1\n")
                    f.write(f"max_rounds=5\n")
                    f.write(f"parent_tag=\n")
                    f.write(f"use_email={'true' if args.email else 'false'}\n")
                    f.write(f"use_xterm={'true' if args.xterm else 'false'}\n")
                    f.write(f"email_to={getattr(args, 'to', '') or ''}\n")
                print("ANALYZE_FIXER_MODE_ENABLED")
                print(f"TAG={tag}")
                print(f"CHECK_TYPE={check_type}")
                print(f"REF_DIR={ref_dir}")
                print(f"IP={ip}")
                print(f"LOG_FILE={cli.base_dir}/runs/{tag}.log")
                print(f"SPEC_FILE={cli.base_dir}/data/{tag}_spec")
                print("MAX_ROUNDS=5")
                print("FIXER_ROUND=1")
                print("SKIP_MONITORING=true")
            else:
                print("ANALYZE_MODE_ENABLED")
                print(f"TAG={tag}")
                print(f"CHECK_TYPE={check_type}")
                print(f"REF_DIR={ref_dir}")
                print(f"IP={ip}")
                print(f"LOG_FILE={cli.base_dir}/runs/{tag}.log")
                print(f"SPEC_FILE={cli.base_dir}/data/{tag}_spec")
                print("SKIP_MONITORING=true")
            print("=" * 70)
            return

        if args.execute:
            print("Task submitted successfully")
            # Signal for Claude Code to pick up analysis
            if analyze_mode:
                tag = result.get('tag', '')
                check_type = result.get('args', {}).get('checkType', 'checkType').replace('checkType:', '').strip(':')
                ref_dir = result.get('args', {}).get('refDir', 'refDir').replace('refDir:', '').strip(':')
                ip = result.get('args', {}).get('ip', 'ip').replace('ip:', '').strip(':')
                # Validate check_type is in allowed list
                if not check_type or check_type == 'checkType':
                    # Try to detect from instruction
                    for ct in ANALYZE_VALID_CHECKS:
                        if ct in args.instruction.lower():
                            check_type = ct
                            break
                    if not check_type or check_type == 'checkType':
                        check_type = 'full_static_check'
                print()
                print("=" * 70)
                if fixer_mode:
                    print("ANALYZE_FIXER_MODE_ENABLED")
                else:
                    print("ANALYZE_MODE_ENABLED")
                print(f"TAG={tag}")
                print(f"CHECK_TYPE={check_type}")
                print(f"REF_DIR={ref_dir}")
                print(f"IP={ip}")
                print(f"LOG_FILE={cli.base_dir}/runs/{tag}.log")
                print(f"SPEC_FILE={cli.base_dir}/data/{tag}_spec")
                if fixer_mode:
                    print("MAX_ROUNDS=5")
                    print("FIXER_ROUND=1")
                print("=" * 70)
        else:
            print("Dry run complete. Add --execute to run the command.")
        print("=" * 70)


if __name__ == '__main__':
    main()
