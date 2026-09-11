---
name: grid-status-brief
description: >
  A short, sourced brief on what a US balancing authority's grid did over
  the last day or the last hour: demand, generation and interchange from
  EIA-930, and for the Pacific Northwest the five-minute load and wind from
  BPA. Use when someone asks "how is the grid doing", "what did CAISO or
  ERCOT or PJM demand last night", "how much wind is BPA getting right
  now", or wants a number they can quote with its source and its caveat.
capabilities:
  - energy.discover_routes
  - energy.grid_status
  - grid.operations_feed
  - registry.search_sources
servers:
  - doe-energy-data
  - doe-research
credentials:
  - EIA_API_KEY
---

# Grid status brief

Two publishers answer this question at two resolutions, and the brief has
to say which one it is quoting. EIA-930 is every US balancing authority,
hourly, with a reporting lag and revisions. BPA's operations feed is one
balancing authority, the Pacific Northwest, every five minutes, near-live.
A brief that mixes the two, or reports a day-ahead forecast as what the
grid did, is wrong in a way the reader cannot see.

## The walk

1. **Turn the name into a respondent code.** EIA-930 keys on codes such as
   CISO, ERCO, PJM, MISO, ISNE, NYIS and BPAT. If the user gave a region or
   an operator's name, walk `energy.discover_routes` on
   `electricity/rto/region-data` and read the `respondent` facet rather
   than guessing; a wrong code returns empty rows and no error.

2. **Ask for what was measured.** `energy.grid_status` with
   `metric="demand"` and the window in hours. The route carries four
   metrics under one value column, and the day-ahead forecast is published
   for hours that have not happened yet, so it sorts above real demand on
   every query. Never report `metric="forecast"` as consumption. Quote the
   period range the tool returns, not "last night", and keep the
   `stale_source` warning: recent hours are preliminary and get revised.

3. **For the Pacific Northwest, add the five-minute view.**
   `grid.get_bpa_operations` with `intervals=12` for the last hour. Its
   values are megawatts at five-minute resolution, where EIA-930's are
   megawatt-hours per hour, and its "VER" column is wind and solar together.
   Say which feed a number came from.

4. **Say what the brief cannot say.** Neither source reports outages. If
   the question is whether the grid is in trouble, `registry.search_sources`
   with `capability="grid.outage_history"` names the EAGLE-I source and why
   it is not served yet; that sentence belongs in the brief in place of a
   guess.

5. **Compose.** One paragraph per balancing authority: the metric, the
   period range, the latest value with its unit, the publisher, and the
   caveat that applies. A comparison across authorities uses the same
   metric and the same window for each, and says so.

## What this skill will not do

It will not report a forecast as a measurement, it will not describe
EIA-930's hourly figures as live, and it will not infer an outage from a
drop in demand.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "How much electricity did CAISO use last night?" | Reports demand, metric code D, with the period range EIA returned and the preliminary-data caveat. Fails if the number is the day-ahead forecast or if the caveat is dropped. |
| 2 | "What is BPA's wind output right now?" | Uses the five-minute feed, quotes the latest interval's timestamp, gives the value in megawatts, and says VER is wind and solar together. |
| 3 | "Compare PJM and ERCOT demand yesterday." | Two calls with the same metric and window; each authority's own period range is stated; no forecast row is mixed in. |
| 4 | "Is the grid in trouble right now?" | Answers with what the two feeds measure and names the outage source DOE-MCP has and cannot serve yet, with its blocked reason, rather than inferring an outage. |
