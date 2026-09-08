---
name: genie_eco_sync
description: Synchronize Genie AI ECO flow changes from the master repo (/home/abinbaba/eco_flow) to a target OSS workspace (<refdir>/src/meta/skills/oss-eco) and to the shared production repo (genie_agent with git push).
argument-hint: [<refdir>]
---

# /genie_eco_sync — Genie ECO Sync Engine

Synchronizes updates from the centralized master repository (`/home/abinbaba/eco_flow`) to:
1. **Target OSS Workspace**: `<refdir>/src/meta/skills/oss-eco` (ports over all updated files — **does NOT run `p4 submit`**).
2. **Shared Production Repo**: `/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent` (ports over Complete-mode MDs, main scripts, CSH wrappers, commits, and runs `git push origin main`).

---

## Usage

```bash
# Pass the OSS workspace refdir directly:
/genie_eco_sync /proj/rtg_oss_er_feint1/abinbaba/oss8_0_grimlock_Sep8141410

# Or run bare to be prompted for the refdir:
/genie_eco_sync
```

---

## What to do

1. **Get the target OSS workspace `refdir`:**
   - If provided in `$ARGUMENTS`, use that path.
   - If not provided, ask the user via `AskUserQuestion`:
     *"Please provide the target OSS workspace root directory (e.g. `/proj/rtg_oss_er_feint1/abinbaba/oss8_0_grimlock_Sep8141410`):"*

2. **Execute the sync script:**
   ```bash
   python3 /home/abinbaba/eco_flow/script/eco_scripts/genie_eco_sync.py --refdir <refdir>
   ```

3. **Relay the sync summary:**
   - Files ported over to `<refdir>/src/meta/skills/oss-eco/`
   - Files synced to `/proj/rtg_oss_feint1/FEINT_AI_AGENT/genie_agent`
   - Git commit hash and push status for `genie_agent`
