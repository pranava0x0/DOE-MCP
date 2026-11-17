---
name: genesis-read-signed-response
description: >
  Use when locating zero and negative values in a published model response schedule.
compatibility: Requires doe-economy for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.economy_inputs
  doe-mcp-servers: doe-economy
  doe-mcp-credentials: none
---

# Read industrial demand elasticity

Sort the table by its numeric output level. Reject duplicate numeric levels even if they use different decimal spellings. Classify a reported elasticity by sign. Keep a zero coefficient separate from an absent row or a blank cell.

List the published levels with zero and negative coefficients and state the unit of the output axis. Do not interpolate a crossing between levels. A negative elasticity is a response parameter; it does not mean the quantity demanded is negative.

Run `doe-mcp demo world-industry-response --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Check the number of output levels. | Eleven numeric levels are distinct. |
| 2 | Locate the zero. | The row at 42 GJ per person per year has zero elasticity. |
| 3 | Retain the negative coefficient. | At 46 the coefficient is −0.05. |
