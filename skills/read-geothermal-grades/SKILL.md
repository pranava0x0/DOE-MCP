---
name: read-geothermal-grades
description: >
  Use when working with GCAM inputs on this question: How much heat input does the model assign to Japan's second hydrothermal grade?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.economy_inputs model.power_inputs
  doe-mcp-servers: doe-economy doe-power
  doe-mcp-credentials: none
---

# Japan's geothermal supply curve

Carry the legacy region label into the resource curve. Select the grade and read both available heat and extraction cost. The energy unit refers to heat entering the plant; never label it electricity generated.

The quantity is heat input. Electrical output would also require a conversion efficiency. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | How much heat input does the model assign to Japan's second hydrothermal grade? | Follow the published rows and retain the units or category. |
| 2 | Check the first grade, where available heat is zero. | Report the value from the comparison row. |
| 3 | Try an unknown value for the grade field. | The table was read and no row matched. |
