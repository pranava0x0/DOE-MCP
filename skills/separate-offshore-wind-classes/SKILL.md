---
name: separate-offshore-wind-classes
description: >
  Use when working with GCAM inputs on this question: What does GCAM assume for wind class c7 and, separately, for deep-water capital cost?
compatibility: Requires the DOE-MCP servers listed below.
metadata:
  doe-mcp-capabilities: model.power_inputs
  doe-mcp-servers: doe-power
  doe-mcp-credentials: none
---

# Two offshore wind assumptions

Read a capacity factor by wind class and a capital cost by depth class. Keep their separate sources and units. Applying both to a site would require evidence for its wind and depth classes.

Wind class and depth class are separate dimensions. Neither table locates a project or predicts its output. Cite the release and the file behind each row. If there is no matching row, report the missing input.

## Bench tasks

| # | Task | Check |
|---|---|---|
| 1 | What does GCAM assume for wind class c7 and, separately, for deep-water capital cost? | Follow the published rows and retain the units or category. |
| 2 | Compare capital cost for the shallow depth class. | Report the value from the comparison row. |
| 3 | Try an unknown value for the wind_class field. | The table was read and no row matched. |
