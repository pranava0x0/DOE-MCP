---
name: genesis-package-weather-data
description: >
  Use when a recipient needs a Daymet CSV, its field definitions and a way to verify the bytes received.
compatibility: Requires doe-earth for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: earth.point_weather
  doe-mcp-servers: doe-earth
  doe-mcp-credentials: none
---

# Package a weather extract with its checksum

Request one point, date range and variable. Compare the returned dates against Daymet’s 365-day calendar: December 31 is omitted in leap years. Check the published unit and citation and distinguish modelled grid estimates from station readings.

Export the recorded example. Keep the temperature CSV beside its Croissant JSON. Compute SHA-256 over the CSV bytes and compare it with the distribution checksum; then load the field mapping and check the dates and temperatures. A digest for this extract says nothing about a different download.

Run `doe-mcp demo daymet-calendar --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [croissant-validator](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basedata-skills/croissant-validator/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Which requested day is absent? | December 31 is absent; five daily rows remain. |
| 2 | Identify the variable and its unit. | The publisher returned tmax in degrees Celsius. |
| 3 | Recover the first packaged observation. | The December 26 record has a modelled maximum of 14.51 °C. |
