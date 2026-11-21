---
name: genesis-separate-retirement-parameters
description: >
  Use when documenting generator retirement parameters whose time scales have different meanings.
compatibility: Requires doe-power for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.power_inputs
  doe-mcp-servers: doe-power
  doe-mcp-credentials: none
---

# Separate two retirement parameters

Read the publisher’s definitions before comparing the fields. Lifetime is the maximum age of a cohort. Half-life is the age at which half the cohort has retired under the specified S-curve function. Keep both values in years without treating them as interchangeable lifespans.

Keep the period label with each technology: a historical parameter and a future one are separate rows. Leave a blank half-life unresolved. The parameter file alone does not show which retirement function runs or predict when a plant will close. Include the field definitions and unresolved values when preparing a dataset card; this worksheet is not a complete Genesis datacard.

Run `doe-mcp demo world-retirement-parameters --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [datacard-generator](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basedata-skills/datacard-generator/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Count rows with both fields. | Six of 35 technology-period rows have both parameters. |
| 2 | Distinguish the two ages. | The first coal row gives 60 years for lifetime and 30 for half-life. |
| 3 | Keep blank half-lives unresolved. | 29 rows leave half-life blank, including the final offshore wind row with a 25-year lifetime. |
