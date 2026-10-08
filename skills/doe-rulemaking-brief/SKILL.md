---
name: doe-rulemaking-brief
description: >
  A sourced brief on what DOE has published in the Federal Register on a
  topic: final rules, proposed rules and notices, each with its docket,
  regulation identifier, CFR parts, dates and a link to the published
  text. Use when someone asks "what has DOE proposed on appliance
  standards", "is this rule final", "when do comments close", or needs the
  docket number behind a regulatory claim.
capabilities:
  - docs.search_rulemakings
  - docs.get_rulemaking
  - registry.search_sources
servers:
  - doe-research
credentials: []
---

# DOE rulemaking brief

The Federal Register is the published record of what DOE has done, and the
docs tools read it directly. Three properties of that record decide how a
brief is written. The register files FERC's documents under DOE's agency
tree and FERC's daily notices are most of what that tree publishes, so the
tools drop them and count what they dropped. A rule's docket, with its
comments and supporting analysis, lives at regulations.gov, which DOE-MCP
does not serve. And the register's count stops at 10,000, so a broad
search reports a floor rather than a total.

## The walk

1. **Ask for the document type the question is about.** "Is it final" is
   a `RULE` search; "what is proposed" is `PRORULE`; meetings, waivers and
   information collections are `NOTICE`. `docs.search_rulemakings` with
   `query`, the type and a `since` date when the question names a period.
   A notice search without a narrow query returns pages that are mostly
   FERC's and therefore mostly empty after the scope filter; the tool says
   when that happened.

2. **Read each document the brief will describe.** `docs.get_rulemaking`
   with its document number returns the action line, the abstract, the
   docket and regulation identifiers, the CFR parts, the effective date and
   the comment deadline. Quote the action line ("Notification of stay.",
   "Final rule.") rather than inferring the status from the title. A
   document that exists but is FERC's comes back as found and not served;
   say that it is FERC's and that FERC's record is its eLibrary.

3. **Report dates as the register states them.** `effective_on` and
   `comments_close_on` are null when the document sets none. Do not compute
   a deadline from the publication date.

4. **Name what is not here.** For comments, the full docket or the
   regulatory analysis, `registry.search_sources` with
   `capability="docs.search_rulemakings"` shows regulations.gov as a
   registered source DOE-MCP cannot read yet and why. Give the docket id
   so the reader can look it up there.

5. **Compose.** One entry per document: type and action, title, published
   date, effective date or comment deadline, docket id, RIN, CFR parts, and
   the Federal Register link. Close with the counts: how many matched, how
   many FERC documents were dropped, and whether the total is a floor.

## What this skill will not do

It will not describe a proposed rule as final, compute a deadline the
document does not state, report a FERC document as a DOE action, or
summarise public comments it has not read.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "What final rules has DOE published recently?" | Lists rules with type, action line, date and docket, reports how many FERC documents were dropped, and states that total_matches includes them. |
| 2 | "What is document 2026-17979 and is it in force?" | Quotes the action line (a stay of a compliance date), gives the docket EERE-2026-FEMP-0067, RIN 1904-AG17 and CFR parts 433 and 435, and says no effective date is stated. |
| 3 | "Summarise Federal Register document 2026-18232." | Says it exists and is FERC's, which DOE-MCP does not cover, and points to FERC's eLibrary rather than summarising it. |
| 4 | "Show me DOE's notices, second page." | Explains that the page is empty because every document on it was FERC's, not because DOE published nothing, and that the 10,000 count is a floor. |
| 5 | "What did commenters say about this rule?" | Says comments are in the regulations.gov docket, which DOE-MCP registers and cannot read yet, and gives the docket id. |
