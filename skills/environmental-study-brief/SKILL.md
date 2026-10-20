---
name: environmental-study-brief
description: >
  Find environmental datasets and read a bounded Daymet weather window for
  one point and date range. Use when someone asks for modelled daily
  weather at a place, or which environmental datasets DOE holds for a
  study area.
compatibility: Needs the DOE-MCP server doe-earth configured in an MCP client. Read-only over public data. No credentials.
metadata:
  doe-mcp-capabilities: earth.search_datasets earth.point_weather
  doe-mcp-servers: doe-earth
  doe-mcp-credentials: none
---

# Environmental study brief

Resolve the study geography and time range before requesting weather.
Search environmental datasets and retain their citations, licences and
coverage. A dataset search and a weather query are independent unless the
returned metadata establishes their geographic and temporal relationship.

Daymet reports modelled values for a grid cell, not station observations.
Report the requested and returned date ranges. Distinguish precipitation
amounts, daily extremes, and any mean you compute yourself. Preserve the
calendar caveat and missing days; do not fill them with zero.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | Read daily maximum temperature at Oak Ridge for 1–5 June 2023. | Uses the specified coordinates and range, identifies modelled values and units, and preserves citations. |
| 2 | Find permafrost datasets and compare their scope with Oak Ridge weather. | Keeps the search results separate and makes no unsupported claim of a geographic or temporal match. |
| 3 | Is a missing Daymet date zero precipitation? | Reports the calendar or coverage gap without filling a missing day with zero. |
