---
name: trace-plant-cooling
description: >
  Use when working with GCAM inputs on this question: Which fresh-water cooling share applies to coal in Germany's legacy model region?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.economy_inputs model.water_inputs
  doe-mcp-servers: doe-economy doe-water
  doe-mcp-credentials: none
---

# Power plant cooling in Western Europe

Use the legacy region label from the country mapping. Fix the plant type, cooling system and water type before reading a year column. A single share cannot establish total withdrawals.

The table covers Western Europe as a group. It cannot establish the share at German plants. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | Which fresh-water cooling share applies to coal in Germany's legacy model region? | Follow the published rows and retain the units or category. |
| 2 | Read the once-through freshwater share for the same plant class. | Report the value from the comparison row. |
| 3 | Try an unknown value for the plant_type field. | The table was read and no row matched. |
