---
name: genesis-compare-resource-grades
description: >
  Use when comparing regional quantities on the same geothermal supply grade while keeping heat input separate from extraction cost.
compatibility: Requires doe-power for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.power_inputs
  doe-mcp-servers: doe-power
  doe-mcp-credentials: none
---

# Compare geothermal heat across regions

Fix the resource, subresource and grade before comparing regions. Keep one row per legacy region and confirm the filtered table is complete. Sort available heat numerically from highest to lowest. Equal values remain equal; the region label orders tied rows for display.

Use thermal EJ per year for available heat and 1975 US dollars per GJ for the separate cost column. Keep the original evidence ID when a row moves. No conversion efficiency or plant utilization appears in this comparison, so do not turn heat into predicted electricity.

Run `doe-mcp demo world-geothermal-grades --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Check the comparison covers one grade. | Fourteen regions have a grade 2 row. |
| 2 | Locate both ends of the range. | Latin America has 10.23454 thermal EJ/year and Korea has 0.001. |
| 3 | Trace the first sorted row. | The highest heat input retains evidence_10 and its cost of 0.569 in 1975 USD/GJ. |
