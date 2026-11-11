---
name: genesis-audit-sample-fields
description: >
  Use when deciding whether a page of soil sample records has usable dates, coordinates and depth metadata.
compatibility: Requires doe-bio for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: bio.biosamples
  doe-mcp-servers: doe-bio
  doe-mcp-credentials: none
---

# Audit environmental sample metadata

Select the ecosystem and place, then record the publisher’s filter and pagination. NMDC place matches submitted text; a match does not establish a political boundary. Keep one row per sample ID.

Count missing dates, complete latitude/longitude pairs and depth fields separately. Preserve zero as a reported number. Distinguish a single depth from a minimum or maximum, keep metres explicit and cite the row evidence. Report the denominator as the inspected page, even if the catalog reports a much larger total.

Run `doe-mcp demo soil-metadata --output ./evidence` for a recorded example.
This DOE-MCP skill adapts a step from the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Bound the audit to the returned page. | Inspect five records and report that the page is truncated. |
| 2 | Which field is missing across that page? | All five lack depth in metres; all five have coordinate pairs. |
| 3 | Recover the evidence for the first missing depth. | The blank depth links back to the first sample ID. |
