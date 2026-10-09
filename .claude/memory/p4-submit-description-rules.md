---
name: p4-submit-description-rules
description: AMD UMC Perforce commit message guidelines - avoid =Stage= unless using umc_stage
metadata:
  type: feedback
---

When submitting changelists to Perforce in UMC/OSS projects without `umc_stage`:
- **DO NOT** use `=Stage=` or `=STAGE=` in the description unless submitted via `umc_stage` with the `#Submitted using umc_stage` footer.
- Use standard prefix format like `[UMC17_X / Godavari] Description...` to comply with the P4 compliance dashboard.

**Why:** The submission compliance dashboard flags `=STAGE=` descriptions as a compliance error if the `umc_stage` tool footer is missing.
**How to apply:** Always format manual `p4 submit -d` messages with `[<IP_NAME>] <Description>` instead of `=Stage=`.
