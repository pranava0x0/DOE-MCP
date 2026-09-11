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
| [grid-status-brief](grid-status-brief/SKILL.md) | What a balancing authority's grid did over the last day or hour, from EIA-930 and BPA's five-minute feed, with the caveat each carries | `doe-energy-data`, `doe-research` |
| [energy-project-site-screen](energy-project-site-screen/SKILL.md) | What is already built around a candidate wind or solar site, the grid it would join, and the named inputs a full screen needs that DOE-MCP cannot serve yet | `doe-energy-data`, `doe-research` |
| [materials-structure-and-code](materials-structure-and-code/SKILL.md) | Computed crystal structures from the Materials Project and basis sets from the Basis Set Exchange | `doe-materials`, `doe-research` |
