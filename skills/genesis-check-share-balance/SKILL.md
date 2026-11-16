---
name: genesis-check-share-balance
description: >
  Use when checking whether a regional allocation adds to one across category pairs in two years.
compatibility: Requires doe-water for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.water_inputs
  doe-mcp-servers: doe-water
  doe-mcp-credentials: none
---

# Check China's coal cooling shares

Hold region and fuel constant. Keep the cooling system and water type as a pair: once-through freshwater and seawater are separate rows. Read all matching rows. Refuse a duplicate pair, a blank share or a share outside zero to one.

Sum each year separately before taking differences. Subtract the earlier share from the later share for each pair. Report the original totals even if they differ from one; dividing by the total would hide a discrepancy. A change of 0.23 is 23 percentage points. The table gives model inputs and has no plant-level water use.

Run `doe-mcp demo china-cooling-balance --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Sum both years without normalizing. | The 2010 and 2020 totals are both 1.000. |
| 2 | Separate the two once-through water types. | The 2020 shares are 0.05 for freshwater and 0.075 for seawater. |
| 3 | Measure the dry cooling change. | The share rises by 0.23. |
