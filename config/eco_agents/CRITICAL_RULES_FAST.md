# ECO Flow — CRITICAL RULES (Fast Reference)

**Read this file before any work. These 10 rules are non-negotiable — each maps to a real run that failed.**
**For full rule definitions (Rules 0–36), see `config/eco_agents/CRITICAL_RULES.md`.**

---

1. **Read `config/eco_agents/` ONLY** — never `config/analyze_agents/` (different flow). [Rule 0]
2. **Every TAG is fresh** — never reuse files from `AI_ECO_FLOW_<OLDER_TAG>/`. Step 2 always submits fresh. [Rule 1]
3. **Spawn then HARD STOP** — after Step 6, write `round_handoff.json`, spawn next agent, EXIT. Never run Steps 7-8 yourself. [Rule 2]
4. **Write `round_handoff.json` BEFORE spawning** — verify `ls -la` shows it on disk. No file → no spawn. [Rule 3]
5. **Never skip a step** — context/token pressure is NOT a valid reason. Each step writes its file → checkpoint → only then next step. [Rule 4]
6. **Instance names, not module names** — all paths use instance hierarchy (e.g. `ARB/DCQARB`), never module-type names. Wrong → FM-036 on every query. [Rule 7]
7. **DFF naming convention** — instance = `<target_register>_reg`, Q output net = `<target_register>`. FM auto-matches by name; any other naming breaks `FmEqvEcoSynthesizeVsSynRtl`. [Rule 10b]
8. **All active stages must change** — verify md5 of each active PostEco stage differs from `.bak_<TAG>_round<N>`. Missing an active stage = partial ECO = FM fail. [Rule 12]
9. **Sub-agents write JSON only; orchestrator writes RPTs** — sub-agent context pressure must not block the RPT. [Rule 14]
10. **FM ABORT → next ROUND_ORCHESTRATOR, never self-fix** — write `eco_fm_verify.json` → EXIT. Don't re-submit FM, don't patch inline, don't loop. [Rule 26]
11. **Script-bug (not FM abort) fail-close → self-fix in `/tmp`, never edit the shared repo mid-run.** If a deterministic script aborts on its OWN limitation (not genuine data ambiguity), copy it to `/tmp/<script>_<TAG>`, make the minimal evidence-backed fix, re-run the `/tmp` copy, and log `SCRIPT-SELF-FIX: <script> — <bug> → <fix>` in the step RPT. Does NOT apply to FM aborts (Rule 26 still governs those). [Rule 38]
12. **`confirmed: true` requires a functional proof, not a structural one.** "This signal can reach this pin" / "this is the sole entry point" is a candidate, not a confirmation. Only exhaustive truth-table enumeration, a carried-through algebraic equivalence, or real external equivalence data (Formality/rename-map) earns `confirmed: true`. For combinational term-folds, "exhaustive truth-table enumeration" means running `script/eco_scripts/eco_verify_boolean_fold.py` (real cell truth tables, real brute force) — NEVER hand-decoding the gates' Liberty functions yourself; the same by-eye skill that builds the fold can misread it the same way while "verifying" it, letting a wrong operator (e.g. AND-with-complement where OR was required) survive its own proof step. If your first idea fails that check, escalate to a deeper reconstruction and re-verify — do not drop it and reach for a different unverified shortcut. Disclose which cell-data tier backed the check (real vendor cache vs. bundled approximate fallback — see `eco_netlist_studier.md` step 5) in the entry's `notes` when it's the lower tier; this doesn't block `confirmed: true`, it's disclosure. Can't prove it? Leave it `UNRESOLVABLE`/`polarity_undetermined`. [Rule 39]
13. **`UNRESOLVABLE` means every independent method failed, not just the first one you tried.** Rename map / fenets lookup, direct RTL-level alias trace, structural driver trace, cofactor derivation — each is a separate path. Disproving one (even rigorously) says nothing about the others. Before writing `UNRESOLVABLE:<net>`, your notes must show which other methods you tried and why each failed — "no alternative candidate exists" is a claim that needs evidence, not a stopping point. [Rule 40]

**Forbidden (NEVER, under any pressure):**
- NEVER modify `EcoChange.svf` — AI flow is permanently prohibited from SVF updates. [Rule 27]
- NEVER set `manual_only` — abolished. Always prescribe a progressive action across 10 rounds. [Rule 35]
- NEVER skip backup before PostEco edit — `cp .v.gz .v.gz.bak_<TAG>_round<N>`. [Rule 6]
- NEVER modify a validator script to suppress or weaken a check — fix the study/netlist, not the validator.
