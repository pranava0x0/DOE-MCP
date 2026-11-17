---
name: genesis-compute-residual-shares
description: >
  Use when converting a model capture fraction into the portion that remains uncaptured.
compatibility: Requires doe-power for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.power_inputs
  doe-mcp-servers: doe-power
  doe-mcp-credentials: none
---

# Account for uncaptured carbon

Select the same fuel and technology in both periods. Check that the reported capture fractions lie between zero and one. For each cell subtract the captured share from one. Keep the publisher’s period label: the initial future year is not a calendar date in this file.

Retain each technology’s source row. A smaller residual share need not imply fewer tonnes: the amount entering the capture system could differ. This worksheet has no throughput or lifecycle inventory.

Run `doe-mcp demo world-capture-residual --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Count the technology rows. | Six complete rows qualify. |
| 2 | Compute a complement. | A capture share of 0.85 leaves 0.15 uncaptured. |
| 3 | Compare the later period. | The first row leaves 0.05 in 2100 and all six residuals decline. |
