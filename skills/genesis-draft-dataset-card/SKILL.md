---
name: genesis-draft-dataset-card
description: >
  Use when a dataset owner needs an initial metadata card and a list of questions that catalog records cannot answer.
compatibility: Requires doe-research for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: dataset.search
  doe-mcp-servers: doe-research
  doe-mcp-credentials: none
---

# Draft a dataset card from catalog evidence

Start with a named catalog or collection. Make a separate draft for each returned dataset. Copy the publisher’s ID, title, organization, date and DOI where supplied. Link each draft to the evidence for that record.

Ask the owner to confirm access conditions, file formats, sizes, checksums, license, sensitivity and intended use. Keep these fields open until someone inspects the distribution and its terms. The handoff is a catalog-based draft; it has not passed Genesis schema validation.

Run `doe-mcp demo dataset-card --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [datacard-generator](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basedata-skills/datacard-generator/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Identify the collection and extent of the search. | The two returned DOE-GDR records are one page of a larger search. |
| 2 | Match the first draft to its evidence. | The metadata and evidence both identify record 2541598. |
| 3 | Determine whether the draft establishes reuse rights. | The card remains a draft and lists permission to reuse among the owner questions. |
