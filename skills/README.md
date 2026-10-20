# Skills

A skill is a named walk over the capability vocabulary: which tools, in what
order, and what to read between them. It is not a tool and it does not add
one. It exists because the order and the reading of `coverage` between steps
are where most wrong answers come from, and neither is expressible in a tool
description.

Each skill is a directory holding `SKILL.md`, whose frontmatter names the
capabilities it composes, the servers that serve them, and the credentials
it needs. The body is the walk. The last section is bench tasks, and a skill
without them does not ship (architecture Part 1 § 4.3) — a walk nobody grades
is a walk nobody has checked.

The file follows the [Agent Skills](https://agentskills.io) format, the one
the MCP Skills extension delegates to and DOE's
[Genesis Agent Skills](https://github.com/AI-ModCon/genesis-skills) catalog
enforces. That format allows six top-level keys, so this project's three
live under `metadata`, each as one line of space-separated ids with `none`
for an empty list:

```yaml
---
name: doe-literature-review
description: >
  What the skill answers. Use when someone asks ...
compatibility: Needs the DOE-MCP server doe-research configured in an MCP client. Read-only over public data. No credentials.
metadata:
  doe-mcp-capabilities: literature.search literature.get_record
  doe-mcp-servers: doe-research
  doe-mcp-credentials: none
---
```

One line per key is deliberate: the strictest reader in that ecosystem
parses a metadata child as a single `key: value` line. `tests/test_skill_spec.py`
holds the format, `src/doe_mcp/core/skills.py` is the only parser of it, and
no skill here sets `allowed-tools`, because a skill should not pre-approve
a tool call on a user's behalf.

The frontmatter names capabilities; the walk may name the tools that serve
them today, because an agent calls tools. A test holds every backticked
name in a body to a real tool or a capability id, so a tool that is merged
away cannot leave a walk pointing at nothing.

The capability ids in the frontmatter are the controlled vocabulary in
`sources/capabilities.yaml`, and a test fails on one that is not in it. That
is what lets a skill route across built-in and plugged-in servers alike
(decision 0018): a skill names capabilities, not tools, so a federated
server that maps its own tools into the vocabulary is reachable from the
same walk.

Beside each `SKILL.md` sits `bench.yaml`, which splits the table's tasks
into `checked`, whose steps `tests/test_skill_bench.py` runs against
recorded publisher responses, and `reader`, which a person grades because
no recording covers the call. Every task is in exactly one list, and at
least one is checked; the suite holds both rules.

| Skill | What it answers | Servers |
|---|---|---|
| [find-doe-data](find-doe-data/SKILL.md) | Does DOE publish this, where is it, and how do I get it — ending in a locator or in a named reason there is not one | `doe-research` |
| [grid-status-brief](grid-status-brief/SKILL.md) | What a balancing authority's grid did over the last day or hour, from EIA-930 and BPA's five-minute feed with the caveat each carries, and which EAGLE-I release holds a past year's outages | `doe-energy-data`, `doe-research` |
| [energy-project-site-screen](energy-project-site-screen/SKILL.md) | What is already built around a candidate wind or solar site, the grid it would join, and the named inputs a full screen needs that DOE-MCP cannot serve yet | `doe-energy-data`, `doe-research` |
| [materials-structure-and-code](materials-structure-and-code/SKILL.md) | Computed crystal structures from the Materials Project and basis sets from the Basis Set Exchange | `doe-materials`, `doe-research` |
| [environmental-study-brief](environmental-study-brief/SKILL.md) | Environmental datasets and bounded modelled weather, with geography and dates kept explicit | `doe-earth` |
| [doe-literature-review](doe-literature-review/SKILL.md) | What DOE has published on a topic, as an evidence table that says how much of the result set it covers and where each record can be read | `doe-research` |
| [doe-rulemaking-brief](doe-rulemaking-brief/SKILL.md) | DOE's final rules, proposed rules and notices on a topic, with dockets, dates and the FERC documents dropped by scope | `doe-research` |
| [microbiome-data-finder](microbiome-data-finder/SKILL.md) | Microbiome studies and samples for an environment or a place, and the DOIs that lead to their sequence data | `doe-bio`, `doe-earth` |
