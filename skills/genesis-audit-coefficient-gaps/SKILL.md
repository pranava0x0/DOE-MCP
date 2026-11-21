---
name: genesis-audit-coefficient-gaps
description: >
  Use when auditing a model coefficient column that contains blanks and exception references.
compatibility: Requires doe-emissions for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.emissions_inputs
  doe-mcp-servers: doe-emissions
  doe-mcp-credentials: none
---

# Inspect blanks in emissions coefficients

Read every matching technology row. Classify the selected coefficient as blank, explicit zero or nonzero. Keep null values intact: an empty coefficient does not establish zero emissions. A zero supplied by the publisher is a distinct case.

Keep the sector, subsector and technology together as the row key. Carry the exception species and technology beside the value. Do not copy a value from an exception target until the relevant model rule and its source have been checked. Count every row once and report the denominator. If the header gives no physical unit, report that limitation rather than assigning one.

Run `doe-mcp demo world-coefficient-gaps --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Account for the complete table. | There are 39 rows: 18 blank and 21 nonzero CO coefficients. |
| 2 | Separate an absent value from zero. | No CO coefficient is explicitly zero; the last row remains null. |
| 3 | Preserve the exception fields. | The first row has a CH4 exception pointing to coal (conv pul), separate from its populated CO coefficient. |
