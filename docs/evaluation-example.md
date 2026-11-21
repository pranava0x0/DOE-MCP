# Evaluation matrix demonstration

**Simulation only. No language model, reviewer or provider was called.** This example exercises the driver protocol against recorded tools. Token counts are simulated protocol values. They do not measure a model or its cost.

[Download the complete comparison bundle](data/evaluation-matrix/bundle.zip) · [Read the JSON report](data/evaluation-matrix/report.json) · [Compare your own reports](evaluation-matrix.html)

The same task and fixture hashes appear in every configuration. Each configuration has a budget that permits one final answer, one unfinished attempt and one unrun repetition. They all stay in the denominator.

| Tool exposure | Skills | Scheduled | Attempted | Final answers | Reference checks passed | Reviewed quality | Estimated cost |
|---|---|---:|---:|---:|---:|---|---|
| all | Off | 3 | 2 | 1 | 1 | Unknown: no reviews | Unknown: no prices |
| all | On | 3 | 2 | 1 | 1 | Unknown: no reviews | Unknown: no prices |
| focused | Off | 3 | 2 | 1 | 1 | Unknown: no reviews | Unknown: no prices |
| focused | On | 3 | 2 | 1 | 1 | Unknown: no reviews | Unknown: no prices |

A passed reference check means citation identifiers resolve to returned evidence. It does not establish a correct claim, safe refusal, scientific accuracy or model quality. There are no reviewed quality scores or supplied model prices in this example.

## Inspect or reproduce

The bundle contains source driver reports, `matrix.csv`, `runs.csv`, a blank grade template, the comparison JSON and checksums. Unzip it, then compare the supplied reports without running a driver:

```bash
doe-mcp evaluate-matrix \
  --report focused-skills-off.json --report focused-skills-on.json \
  --report all-skills-off.json --report all-skills-on.json \
  --output ./offline-comparison
```

[Matrix CSV](data/evaluation-matrix/matrix.csv) · [Run-level CSV](data/evaluation-matrix/runs.csv) · [Grade template](data/evaluation-matrix/grades-template.json)

Site execution clocks and identifiers are normalized for repeatable builds. They are not fixture capture times. The source fixture hashes remain in the bundle.
