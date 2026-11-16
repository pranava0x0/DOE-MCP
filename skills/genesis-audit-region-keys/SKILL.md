---
name: genesis-audit-region-keys
description: >
  Use when joining country or territory codes to regional model inputs. Check whether the same region label can map to several IDs.
compatibility: Requires doe-economy for publisher reads. The recorded example needs no account or key.
metadata:
  doe-mcp-capabilities: model.economy_inputs
  doe-mcp-servers: doe-economy
  doe-mcp-credentials: none
---

# Map Latin American country codes

Select one legacy label and keep every matching country or territory code. Check that each code occurs once. Count the distinct numeric region IDs, preserving each code’s row and evidence ID. If the search stopped before the last match, do not report a region count.

Before joining another table, inspect its header. A table keyed by numeric ID needs the ID from the matching country row. Joining on the legacy name alone can assign Argentina’s input to Brazil. The codes reflect the release geography.

Run `doe-mcp demo latin-america-regions --output ./evidence` for the worksheet and source rows.
This DOE-MCP skill adapts the Genesis [data-exploration](https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/skills/baseeval-skills/lm-eval-harness-skills/data-exploration/SKILL.md) guide.

## Bench tasks

| # | Task | Recorded example |
|---|---|---|
| 1 | Count the codes under the legacy label. | 49 codes match Latin America. |
| 2 | Test whether the label determines the numeric key. | The rows contain nine distinct numeric region IDs. |
| 3 | Distinguish the keys for Argentina and Brazil. | Argentina maps to 31 and Brazil to 7. |
