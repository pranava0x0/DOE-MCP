---
name: genesis-convert-energy-units
description: >
  Use when converting an energy input per volume of water into electricity units without mixing energy and material flows.
compatibility: Requires doe-water for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.water_inputs
  doe-mcp-servers: doe-water
  doe-mcp-credentials: none
---

# Convert desalination electricity inputs

Select the electricity input for reverse osmosis, keeping the technology and input name together. Require one complete matching row. Retain each published year and the original value in GJ per cubic metre.

One kWh equals 0.0036 GJ. Divide the electricity coefficient by 0.0036 and report kWh per cubic metre beside the original value. Retain the same evidence reference for each year because the values came from one source row. Do not apply that conversion to the seawater row or mix electricity and fuel inputs. The coefficients describe model assumptions, not plant efficiency or drinking-water cost.

Run `doe-mcp demo world-desalination-electricity --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Convert the early input. | 0.0432 GJ/m³ is 12 kWh/m³ in 1971. |
| 2 | Convert the later input. | 0.0063 GJ/m³ is 1.75 kWh/m³ in 2100. |
| 3 | Trace all periods to the source. | Four periods share evidence_01; 2015 is 2.75 kWh/m³. |
