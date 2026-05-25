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
8. **All 3 stages must change** — verify md5 of each PostEco stage differs from `.bak_<TAG>_round<N>`. Only-Synth = partial ECO = FM fail. [Rule 12]
9. **Sub-agents write JSON only; orchestrator writes RPTs** — sub-agent context pressure must not block the RPT. [Rule 14]
10. **FM ABORT → next ROUND_ORCHESTRATOR, never self-fix** — write `eco_fm_verify.json` → EXIT. Don't re-submit FM, don't patch inline, don't loop. [Rule 26]

**Forbidden (NEVER, under any pressure):**
- NEVER modify `EcoChange.svf` — AI flow is permanently prohibited from SVF updates. [Rule 27]
- NEVER set `manual_only` — abolished. Always prescribe a progressive action across 10 rounds. [Rule 35]
- NEVER skip backup before PostEco edit — `cp .v.gz .v.gz.bak_<TAG>_round<N>`. [Rule 6]
- NEVER modify a validator script to suppress or weaken a check — fix the study/netlist, not the validator.
