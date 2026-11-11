---
name: genesis-check-prediction-bands
description: >
  Use when testing how often a fixed band around five-minute load predictions covers later BPA readings.
compatibility: Requires doe-energy-data for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: grid.operations_feed
  doe-mcp-servers: doe-energy-data
  doe-mcp-credentials: none
---

# Check a prediction band on later readings

Use 24 consecutive, populated BPA load readings in MW. Predict the next value with the prior reading. Order the first 15 absolute prediction errors and take the 14th smallest: the nearest-rank 90th percentile. Freeze that value as the half-width before inspecting later errors.

Center the band for each of the next eight targets on the preceding observed load. Count a value on either bound as inside. Show both evidence IDs and the phase for each pair. Stop if a reading is missing or the clock skips an interval. Report hits out of eight; nearby observations are related and the calibration percentile is no promise of future coverage.

Run `doe-mcp demo load-interval-check --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [uncertainty-quantification](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basesafe-skills/uncertainty-quantification/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Fit the width using only earlier errors. | The 14th of 15 ordered absolute errors sets a 62 MW half-width. |
| 2 | Which targets count toward coverage? | The first 15 pairs are calibration rows and have no hit flag. |
| 3 | Report the later hit count with its denominator. | Seven of eight subsequent loads fall inside the band. |
