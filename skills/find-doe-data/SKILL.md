---
name: find-doe-data
description: >
  Find whether the US Department of Energy publishes a given kind of data,
  where it lives, and how to actually get at it — ending in a real locator
  or in a named reason there is not one. Use when someone asks "does DOE
  have data on X", "where would I find X from a national laboratory", or
  "who publishes X", and when a first search comes back empty and the
  question is whether that means DOE holds nothing or that DOE-MCP cannot
  see it.
capabilities:
  - registry.resolve_org
  - registry.search_sources
  - discovery.search_catalogs
  - dataset.search
  - literature.search
servers:
  - doe-research
credentials: []
---

# Find DOE data

The walk that answers "does this exist, and where", and that ends either in
a locator a publisher supplied or in a sentence saying which of the two
kinds of nothing you hit.

## Why this is a skill and not a tool

Every step here is one tool call. What makes it a skill is the ORDER and the
reading of coverage between steps: the same four calls in a different order
answer a different question, and the step most often skipped — checking
whether an empty result means "searched and absent" or "we have no source" —
is the one that decides whether the answer is true.

## The walk

1. **Resolve any organization the question names.** `registry.resolve_org`
   with whatever the user said. This ecosystem renames things and the old
   domains do not redirect: NREL became NLR on 2025-12-01 and `nrel.gov`
   has no DNS at all. A search on a dead acronym returns zero results and
   no error, which reads exactly like an absence of data. If the tool
   reports an alias match, say so in the answer — the user's name for the
   thing is now historical and they will hit it again.

2. **Ask the registry what could answer at all.** `registry.search_sources`
   with the topic, or with `capability` when the question maps to one. Read
   the states. A source in `proposed` is one DOE-MCP knows about and cannot
   query, and its `blocked_reason` is a better answer than silence: "the
   Energy Data eXchange holds this and needs a key we have not configured"
   is actionable, "no results" is not.

3. **Search the catalogs at once.** `discovery.search_all_catalogs` fans out
   across DOE's cross-cutting catalogs and labels every hit with the catalog
   it came from and that catalog's vintage. Two of them are harvested
   documents rather than live APIs and carry entries years old beside
   current ones; the `catalog_vintage` warning says which, and an answer
   that repeats a 2014 entry as current has been wrong for a decade.

4. **Then the specific collections.** `research.search_datasets` for data,
   `research.search_literature` for the paper that produced it. Datasets and
   the publications describing them are separate records and the dataset
   often exists when the search terms only match the paper. When a paper is
   the only hit, its record carries the dataset DOI.

5. **End on a locator or on a named absence.** Every record carries the
   locator its publisher actually supplied, and never a constructed one — a
   URL assembled from an id pattern has the shape of provenance and none of
   the substance. If nothing was found, say which kind of nothing:

   - `registry: covered` with `result: empty` — the systems were searched
     and hold no matching record.
   - `registry: none` — DOE-MCP has no source for this question. **The data
     may well exist.** This is the sentence to write, not "DOE does not
     publish that".
   - `registry: partial` with entries in `sources_unavailable` — we can see
     the source and could not reach it. Name it and why.

## What this skill will not do

It will not guess a download URL, and it will not report a truncated page as
the whole result set. When `coverage.pagination` says `truncated`,
`total_matches` is the number to quote.

## Bench tasks

A skill without bench tasks does not ship (architecture Part 1 § 4.3). These
are the tasks it is graded on; each names the trap it exists to catch.

| # | Task | Passes when |
|---|---|---|
| 1 | "Does NREL publish wind turbine locations?" | The answer resolves NREL to the National Laboratory of the Rockies and says the name is historical, then finds the USGS/LBNL wind turbine database, which NLR does not publish. Fails if it answers from the dead acronym. |
| 2 | "Where can I get DOE data on geothermal wells?" | Names the Geothermal Data Repository, reached through DOE Data Explorer's `site_ownership_code`, and gives a real landing page. Fails if it constructs a URL. |
| 3 | "Does DOE have data on municipal recycling rates?" | Answers `registry: none` in words: DOE-MCP has no source, and the data may exist elsewhere. Fails if it says DOE does not publish it. |
| 4 | "What DOE data exists on perovskite solar cells?" | Returns both datasets and literature, quotes `total_matches` rather than the page size, and does not present the first ten as the whole set. |
| 5 | "Is there a DOE dataset on building energy use in every US building?" | Finds Model America through ESS-DIVE or the catalogs, and reports the deposit's own licence and citation rather than assuming a uniform one. |
