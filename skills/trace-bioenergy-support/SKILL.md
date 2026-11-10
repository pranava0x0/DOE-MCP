---
name: trace-bioenergy-support
description: >
  Use when working with GCAM inputs on this question: Which supply preference does the model apply in Brazil?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.economy_inputs model.land_inputs
  doe-mcp-servers: doe-economy doe-land
  doe-mcp-credentials: none
---

# Brazil's bioenergy preference

Resolve the country to its numeric model region. Read the preference column and check the publisher note about the alternative column. Describe the preference as an input to supply weighting; it does not measure a payment.

The table adjusts model share weights. Its category does not identify a subsidy or its size. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | Which supply preference does the model apply in Brazil? | Follow the published rows and retain the units or category. |
| 2 | Compare the preference assigned to Pakistan. | Report the value from the comparison row. |
| 3 | Try an unknown value for the GCAM_region_ID field. | The table was read and no row matched. |
