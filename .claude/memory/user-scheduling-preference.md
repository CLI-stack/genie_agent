---
name: user-scheduling-preference
description: User scheduling preference - use Claude Code in-session cron jobs only without daemons or auto-renewal
metadata:
  type: feedback
---

Scheduling preferences for Azman:
- Use **Claude Code in-session cron jobs only** (`CronCreate` with `durable: true`).
- **Do not** run Linux background daemons (`weekly_scheduler.py` / `weekly_scheduler.csh` via `nohup`).
- **Do not** add auto-renewal prompt logic in cron tasks; the user prefers to set and manage the schedule directly each week.

**Why:** Keeps task execution strictly inside Claude Code's native interface under direct user control.
**How to apply:** Whenever scheduling recurring tasks, create a standard `CronCreate` job without spawning external background scripts or attaching auto-renewal chains.
