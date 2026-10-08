---
name: doe-literature-review
description: >
  A short evidence table of DOE-funded publications on a topic: what was
  found in OSTI.GOV and DOE PAGES, which records were read in full, where
  each can be read, and how much of the result set the table covers. Use
  when someone asks "what has DOE published on X", wants the national-lab
  reports behind a claim, or needs citations with a stated coverage rather
  than a list of links.
capabilities:
  - literature.search
  - literature.get_record
  - literature.fulltext_link
  - registry.resolve_org
servers:
  - doe-research
credentials: []
---

# DOE literature review

A literature search here answers a narrower question than it appears to.
OSTI.GOV and DOE PAGES index work DOE funded or received, so an empty
result says DOE has no record of it, not that the work does not exist.
DOE PAGES holds accepted manuscripts after a 12-month embargo, so the
newest journal versions are missing from it by design. And the first page
is never the result set: a topic that matches eight thousand records and
returns ten has been sampled, not reviewed.

## The walk

1. **Resolve any lab or office first.** If the user names a laboratory or
   a program office, call `registry.resolve_org` and use the current name
   in `research_org` or `sponsor_org`. NREL became the National
   Laboratory of the Rockies on 2025-12-01, and records filed under each
   name differ.

2. **Search both collections at once.** `research.search_literature` with
   the topic, a year range when the question has one, and `rows` no larger
   than the table you intend to write. Read `per_source`: each collection's
   own total and how many it returned, and `deduplicated_by_doi`, which is
   how many journal articles appeared in both. Report the totals, not the
   page length.

3. **Read the records the table will rest on.** For each record the
   summary will make a claim about, `research.get_record` with its id. The
   search row is an abstract-free summary; the record carries the
   description, sponsors and the full-text answer. Do not summarise a
   paper's finding from its title.

4. **Say where each can be read.** The record's `fulltext` block is either
   a link OSTI serves or an explicit "no link exposed". The second is not a
   paywall finding: the DOI may still resolve to a readable version. Never
   build a full-text URL from an id.

5. **Compose the table.** One row per record: title, year, product type,
   performing lab, sponsor, DOI, and whether full text was found. Under
   it, one sentence of coverage: how many matched in each collection, how
   many were read, and the date range searched. A record dated in the
   future is a scheduled publication; say so rather than sorting it first
   as the newest result.

## What this skill will not do

It will not describe a truncated page as the literature, report a missing
full-text link as a paywall, claim a finding from a record it did not read,
or treat an empty search as evidence that nobody has worked on the topic.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "What has DOE published on perovskite solar cells?" | Reports both collections' totals and the duplicates removed, states that the table is a sample of thousands, and cites DOIs from the returned records. |
| 2 | "Can I read report 3413920, and is it new?" | Gives the OSTI full-text link the record supplies, names the sponsor, and notes that its 2027 publication date is a scheduled release rather than a typo. |
| 3 | "Is record 3389573 behind a paywall?" | Says OSTI exposes no full-text link and that this is not a paywall finding, and points to the DOI or DOE PAGES as the next check. |
| 4 | "What did NREL publish on this last year?" | Resolves NREL to the National Laboratory of the Rockies before searching and says which name the records use. |
| 5 | "Has DOE published anything on zzzznotarealtopiczzzz?" | Reports an empty result as no DOE record of the topic, not as proof that no such work exists. |
