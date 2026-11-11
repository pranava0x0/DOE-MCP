---
name: genesis-score-load-errors
description: >
  Use when comparing a grid load predictor with the simple baseline that repeats the previous measured load.
compatibility: Requires doe-energy-data for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: grid.operations_feed
  doe-mcp-servers: doe-energy-data
  doe-mcp-credentials: none
---

# Score a five-minute persistence baseline

Take consecutive BPA load readings in MW. Predict each target with its immediate predecessor. Only score a pair when both values exist and the elapsed time is five minutes. Keep the prior and target evidence IDs together, including the publisher’s clock labels through a daylight-saving change.

For every pair, subtract the reported load from the prediction. Report the number scored, the number excluded for each reason, mean absolute error and root mean squared error in MW. These scores describe the sampled window; they do not measure uncertainty or establish operational performance.

Run `doe-mcp demo load-baseline --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [uq-metrics-evaluator](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basesafe-skills/uq-metrics-evaluator/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Check the sign of the first prediction error. | The prediction exceeds the target by 62 MW. |
| 2 | Account for the scored pairs. | Eleven pairs qualify. MAE is about 28.91 MW and RMSE about 36.98 MW. |
| 3 | Trace both sides of the first comparison. | The prior and target readings have separate evidence IDs. |
