---
name: genesis-document-model-columns
description: >
  Use when documenting which of two regional parameter columns a model reads and where their values differ.
compatibility: Requires doe-land for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.land_inputs
  doe-mcp-servers: doe-land
  doe-mcp-credentials: none
---

# Compare regional bioenergy settings

Read the release notes in the table header before assigning a role to either column. Retain the note that identifies the column the model uses. If the publisher does not identify it, leave that role unresolved. The column names alone do not establish it.

Pair the two values within each regional row and count differing pairs. A blank or repeated region ID invalidates the comparison. Keep matching rows in the worksheet as well as differences so a reader can verify the denominator. This documents one table for a dataset card; a full Genesis datacard needs other fields and validation.

Run `doe-mcp demo world-bioenergy-columns --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [datacard-generator](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/basedata-skills/datacard-generator/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Check the denominator. | The worksheet covers 32 region IDs. |
| 2 | Count different settings. | Four regions have different entries. |
| 3 | Show a difference and a match. | Canada has Low and High; Brazil has High in both columns. |
