---
name: genesis-build-bibliography
description: >
  Use when preparing citations from DOE public-access literature and deciding which author lists still need checking.
compatibility: Requires doe-research for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: literature.search
  doe-mcp-servers: doe-research
  doe-mcp-credentials: none
---

# Build a traceable bibliography

Search for the subject in DOE PAGES and state the number of records returned and the total the publisher reports. Keep the OSTI identifier, title, DOI, journal and publication date from each record. Attach its evidence ID before sorting or exporting the rows.

Check the count of omitted authors. Mark those rows for a full-record check before generating the final bibliography. An absent DOI is an empty field; do not infer one from a title. A catalog entry establishes what was indexed, not what the authors found.

Run `doe-mcp demo bibliography --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [literature-search](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/literature-search/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Which collection supplied the page? | DOE PAGES returned three records from a truncated search. |
| 2 | Which record needs an author check? | The first summary omits four authors. |
| 3 | Trace its citation to the publisher record. | The evidence entry names OSTI record 3409109. |
