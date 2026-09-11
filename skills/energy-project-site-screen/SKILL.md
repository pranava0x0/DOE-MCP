---
name: energy-project-site-screen
description: >
  A first-pass screen of a candidate site for a wind or solar project from
  what DOE-MCP can see today: the turbines and utility-scale solar already
  built in the area, the balancing authority's recent demand, and a named
  list of what a full screen needs that DOE-MCP does not serve yet, each
  with its reason, plus the hand-off to NEPA-MCP for the environmental
  review. Use when someone asks "what is already built near here", "is this
  a good place for a wind farm", or "what would I need to check before
  siting a project".
capabilities:
  - facility.locations
  - energy.grid_status
  - registry.search_sources
servers:
  - doe-energy-data
  - doe-research
credentials:
  - EIA_API_KEY
---

# Energy project site screen

A screen, not a verdict. What DOE-MCP serves today is the inventory of what
is built and the grid's recent demand; the resource, the tariffs, the
stations and the interconnection queue are in the registry as sources it
cannot query yet. The honest screen reports the first two and names the
rest with the reason each is missing, which is more useful than a score
built on half the inputs.

## The walk

1. **Frame the area.** A state and county, or a bounding box as
   `west,south,east,north`. The facility tools take either; a place name
   alone is not a filter.

2. **What is already built.** `facility.find_wind_turbines` and
   `facility.find_solar_facilities` over the same area. Quote
   `total_matches`, never the page size, and keep the `screening_only`
   warning when it fires: rows with a confidence code below the top score
   are estimates the compilers could not confirm from imagery. These are
   inventories of where things are, not what they produce.

3. **The grid the site would join.** `energy.grid_status` for the
   balancing authority, `metric="demand"`, over a day. If no EIA key is
   configured the tool refuses with a typed error; say so rather than
   leaving the grid out silently.

4. **What a full screen needs that this one cannot give.**
   `registry.search_sources` with each of `solar.get_resource`,
   `rates.find_tariffs`, `stations.find_alt_fuel` and
   `grid.interconnection_queue`. Each returns the proposed source that
   would serve it and its `blocked_reason`, most of them one credential
   away. List them by name with the reason; "not covered" is not an answer.

5. **Hand off the environmental review.** `registry.list_neighbors` names
   PNNL's NEPA-MCP, which serves the NEPA document corpus this project does
   not. The screen says that an environmental review is a separate step
   with its own server, and does not pretend to have started it.

## What this skill will not do

It will not rank sites, it will not estimate production, and it will not
present an inventory count as a capacity figure.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "What is already built in Rhode Island?" | Reports the wind-turbine and solar-facility counts from `total_matches`, names both inventories as the source, and keeps the confidence-code caveat. Fails if it reports the page size as the count. |
| 2 | "Is Kern County a good site for a wind farm?" | Declines to rank; reports what is built there and the grid's demand, then lists the resource, tariff and queue inputs it cannot supply with each reason. |
| 3 | "What would a full screen need that you can't give me?" | Names the four proposed sources with their blocked reasons, most of them a credential away, rather than saying the data does not exist. |
| 4 | "Do I need an environmental review?" | Hands off to NEPA-MCP by name from the neighbours list and says DOE-MCP does not serve NEPA documents. |
