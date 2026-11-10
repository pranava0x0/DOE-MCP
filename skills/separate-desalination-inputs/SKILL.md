---
name: separate-desalination-inputs
description: >
  Use when working with GCAM inputs on this question: Which electricity input and non-energy cost does GCAM use for reverse osmosis?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.water_inputs
  doe-mcp-servers: doe-water
  doe-mcp-credentials: none
---

# The energy cost of reverse osmosis

Match technology and input together. A reverse osmosis row can describe seawater or electricity. For the same year, look up the separate non-energy cost and retain its currency year. Do not add dollars to gigajoules.

These inputs have different units. Converting them to a total cost requires an electricity price. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | Which electricity input and non-energy cost does GCAM use for reverse osmosis? | Follow the published rows and retain the units or category. |
| 2 | Check the electricity coefficient for irrigation abstraction. | Report the value from the comparison row. |
| 3 | Try an unknown value for the technology field. | The table was read and no row matched. |
