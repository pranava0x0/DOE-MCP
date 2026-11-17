---
name: genesis-compare-fallback-rates
description: >
  Use when comparing fallback crop improvement assumptions across model years.
compatibility: Requires doe-land for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.land_inputs
  doe-mcp-servers: doe-land
  doe-mcp-credentials: none
---

# Compare crop yield assumptions

Read the same commodity at two published year columns. Use the recorded decimal rate and label the later value lower, higher or unchanged. Count all three directions over the complete table. A zero is an explicit rate; a blank makes the comparison incomplete.

Check the header for when the fallback applies. This table gives assumptions about maximum yield improvement. Neither the area harvested nor the starting yield appears here, so these rates cannot establish a change in tonnes harvested.

Run `doe-mcp demo world-crop-rates --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Check the denominator. | 18 commodities have both rates. |
| 2 | Count changed assumptions. | 11 rates differ between 2050 and 2095. |
| 3 | Trace corn’s change. | Corn falls from 0.0025 to 0.0015. |
