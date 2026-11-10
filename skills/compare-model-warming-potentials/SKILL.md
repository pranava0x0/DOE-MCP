---
name: compare-model-warming-potentials
description: >
  Use when working with GCAM inputs on this question: Which 100-year warming potentials did this release use for HFC134a and CF4?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.emissions_inputs
  doe-mcp-servers: doe-emissions
  doe-mcp-credentials: none
---

# Compare two fluorinated gases

Match each gas exactly. Read the numeric factors with the time horizon and assessment in the source notes. To express emissions in carbon dioxide equivalents you would also need a mass of each gas.

The source identifies the 2007 IPCC assessment. These factors alone do not quantify emissions. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | Which 100-year warming potentials did this release use for HFC134a and CF4? | Follow the published rows and retain the units or category. |
| 2 | Read the model factor for HFC32. | Report the value from the comparison row. |
| 3 | Try an unknown value for the Gas field. | The table was read and no row matched. |
