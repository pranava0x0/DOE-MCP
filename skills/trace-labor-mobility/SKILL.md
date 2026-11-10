---
name: trace-labor-mobility
description: >
  Use when working with GCAM inputs on this question: How does GCAM parameterize movement of labor between sectors in Pakistan?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.economy_inputs
  doe-mcp-servers: doe-economy
  doe-mcp-credentials: none
---

# Labor mobility in Pakistan

Resolve the country to its numeric region. Read the published exponent and the agricultural productivity group in that row. Keep the category separate from the coefficient; neither gives a worker count.

An exponent controls the model's wage response. It does not count workers who changed jobs. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | How does GCAM parameterize movement of labor between sectors in Pakistan? | Follow the published rows and retain the units or category. |
| 2 | Compare the agricultural productivity group assigned to Brazil. | Report the value from the comparison row. |
| 3 | Try an unknown value for the GCAM_region_ID field. | The table was read and no row matched. |
