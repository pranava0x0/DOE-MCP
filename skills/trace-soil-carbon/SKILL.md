---
name: trace-soil-carbon
description: >
  Use when working with GCAM inputs on this question: Which recovery time does GCAM assign to Kenya's model region?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.economy_inputs model.land_inputs
  doe-mcp-servers: doe-economy doe-land
  doe-mcp-credentials: none
---

# Soil carbon recovery in Kenya

Match the country code to the numeric region ID. Read the recovery period for that ID. Report years after land-use change and name the model region; a plot measurement would require a field study.

The regional parameter describes recovery after land-use change. It is not a measurement from a Kenyan field. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | Which recovery time does GCAM assign to Kenya's model region? | Follow the published rows and retain the units or category. |
| 2 | Compare the USA regional recovery time. | Report the value from the comparison row. |
| 3 | Try an unknown value for the GCAM_region_ID field. | The table was read and no row matched. |
