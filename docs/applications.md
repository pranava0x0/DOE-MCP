# Applications built on DOE-MCP

These local command-line applications use read-only tools or declared input files. Examples use recorded publisher responses or clearly labelled synthetic simulations. They are not live services. Download a complete sample bundle or run a command to create your own.

A server hosts tools. A tool returns data and provenance. A skill describes a tested workflow. An application combines tools or declared inputs and writes a result bundle. [Installation and application options](apps.html) · [Servers](index.html#servers-list) · [Tool reference](reference.html) · [Scientific skills](guide.html#connect-a-client-and-provide-a-skill)

Recorded site examples normalize replay retrieval clocks and request IDs for repeatable builds. The normalized clock is not a capture date. Source data and fixture hashes are preserved. CLI runs preserve their original tool envelopes.

## Grid forecast benchmark lab

**Recorded example. Runs locally.**

```bash
doe-mcp app forecast-benchmark --output ./forecast-benchmark-run
```

[Download complete bundle](data/apps/forecast-benchmark/bundle.zip) · [View report and source evidence](data/apps/forecast-benchmark/report.json) · [File checksums](data/apps/forecast-benchmark/manifest.json)

Compare uploaded forecasts with BPA timestamped load samples. The preset creates retrospective forecasts for illustration; it does not measure prospective skill.

Run status: **scored**. Matched 46 of 46 forecasts.

| Model | Lead seconds | Matched | MAE MW | RMSE MW | Bias MW |
|---|---:|---:|---:|---:|---:|
| retrospective-persistence | 300.000 | 23 | 33.522 | 40.689 | 12.043 |
| retrospective-plus-10 | 300.000 | 23 | 36.739 | 44.682 | 22.043 |

Table metrics are rounded to three decimals; the report retains full precision.

[Forecast input CSV](data/apps/forecast-benchmark/forecasts.csv) · [Scored rows](data/apps/forecast-benchmark/scores.csv)

For your forecasts, pass `--forecasts path/to/forecasts.csv` and an explicit `--actual-timezone` when the publisher timestamps lack offsets. An unscored run retains exclusions and does not claim calculated metrics.

### Scope and limits

- Actuals are BPA timestamped five-minute sample MW readings, not interval means.
- Issuance times are supplied assertions; this application cannot prove forecasts existed before their targets.
- Persistence uses only observations at or before issuance; gaps and unavailable targets are excluded with reasons.
- Scores compare each model and lead on its own matched rows; differing match sets are not a controlled model comparison.
- The sample forecasts were created retrospectively from recorded BPA readings; they are not publisher forecasts or evidence of prospective performance.
- Unlabelled BPA Pacific clock readings use the supplied IANA timezone assumption; the publisher did not supply an offset for each reading.

## Weather package builder

**Recorded example. Runs locally.**

```bash
doe-mcp app weather-package --output ./weather-package-run
```

[Download complete bundle](data/apps/weather-package/bundle.zip) · [View report and source evidence](data/apps/weather-package/report.json) · [File checksums](data/apps/weather-package/manifest.json)

Build a point-weather CSV and Croissant metadata with a verified CSV checksum. Coordinates, date range and variables are CLI parameters.

This preset contains **5 days**. Daymet keeps February 29 and omits December 31 in leap years; it reports modelled grid-cell weather.

[Weather CSV](data/apps/weather-package/weather.csv) · [Croissant metadata](data/apps/weather-package/weather-croissant.json)

### Scope and limits

- Daymet reports modelled weather for a 1 km grid cell, not station observations.
- Leap day is retained; December 31 is omitted in leap years. No absent or missing values are imputed.
- Croissant metadata describes this exported CSV; its checksum does not identify the publisher&#x27;s full archive.

## DOE docket notice tracker

**Recorded example. Runs locally.**

```bash
doe-mcp app docket-tracker --output ./docket-tracker-run
```

[Download complete bundle](data/apps/docket-tracker/bundle.zip) · [View report and source evidence](data/apps/docket-tracker/report.json) · [File checksums](data/apps/docket-tracker/manifest.json)

Track Federal Register DOE notices, retain separate publication/effective/comment dates, group publisher-supplied docket IDs, and compare saved snapshots. Regulations.gov document metadata is a **gated integration**, awaiting a real recording after the public exploration key returned HTTP 429. No comment bodies or attachments are collected.

This bounded snapshot contains **1 notices**. Complete pagination: **false**. Absence in a snapshot never means deletion or withdrawal.

| Notice | Title | Publication | Comment close |
|---|---|---|---|
| 2026-18370 | Securing the United States Bulk-Power System | 2026-09-09 | Unknown |

[Notice CSV](data/apps/docket-tracker/notices.csv)

### Scope and limits

- Federal Register DOE notice metadata only; Regulations.gov docket comments and supporting materials were not retrieved.
- Publication, effective and comment-close dates are separate publisher fields; absent dates remain unknown.
- Search totals include FERC records excluded by the existing DOE source scope. Pagination continues through pages containing only excluded records.
- Multiple docket identifiers retain separate group memberships; records with no docket ID remain explicitly ungrouped.
- A snapshot records what the bounded search returned; absence does not establish deletion, withdrawal or current legal effect.

## GCAM comparison receipts

**Recorded example. Runs locally.**

```bash
doe-mcp app gcam-comparison --output ./gcam-comparison-run
```

[Download complete bundle](data/apps/gcam-comparison/bundle.zip) · [View report and source evidence](data/apps/gcam-comparison/report.json) · [File checksums](data/apps/gcam-comparison/manifest.json)

[Selection plan JSON](data/apps/gcam-comparison/plan.json) · [Receipt CSV](data/apps/gcam-comparison/receipts.csv) · [Receipts with source notes](data/apps/gcam-comparison/receipts.json) · [Pair assessments](data/apps/gcam-comparison/comparisons.json)

Select exact rows from released GCAM input tables and place their values side by side. Each value retains a file, row, column, source note and evidence reference. No arithmetic or unit conversion is performed.

The preset follows the publisher's country mappings for Japan, Indonesia, Kenya and Canada. Indonesia maps to the legacy **Southeast Asia** region; that value is not an Indonesia observation. The soil table uses numeric region IDs and prints `Units: NA`; its source description explains recovery in years. The 2100 desalination energy and cost fields are different inputs with different units.

| Selection | Column | Original value | Region convention and value | Publisher unit statement |
|---|---|---|---|---|
| japan-geothermal | available | 0.56582 | region\_GCAM3: Japan | Units: available in Thermal EJ/yr (i.e. heat input to geothermal power plants); extractioncost in 1975$/GJ |
| indonesia-geothermal | available | 5.65548 | region\_GCAM3: Southeast Asia | Units: available in Thermal EJ/yr (i.e. heat input to geothermal power plants); extractioncost in 1975$/GJ |
| kenya-soil | soilTimeScale | 25 | GCAM\_region\_ID: 2 | Units: NA |
| canada-soil | soilTimeScale | 75 | GCAM\_region\_ID: 8 | Units: NA |
| desalination-energy | 2100 | 0.0063 | No country mapping requested | Units: GJ of energy per cubic meter of water (GJ/m3) |
| desalination-cost | 2100 | 0.38 | No country mapping requested | Units: 1975$ per cubic meter of desalinated water (1975$/m3) |

| Left selection | Right selection | Receipt relationship | Arithmetic |
|---|---|---|---|
| japan-geothermal | indonesia-geothermal | same\_table\_field | not\_performed |
| kenya-soil | canada-soil | units\_unspecified | not\_performed |
| desalination-energy | desalination-cost | different\_table\_or\_field | not\_performed |

A relationship describes table and field identity, not scientific interchangeability. The report retains each full row and its original envelope. Blank publisher values stay null.

Download the plan, edit its exact filters and selected columns, then run:

```bash
doe-mcp app gcam-comparison --plan ./plan.json --output ./gcam-custom
```

### Scope and limits

- These are released GCAM configuration inputs, not observations, current prices, forecasts or scenario outputs.
- Every selector must match exactly one row. Original decimal strings, categories and null cells are preserved.
- Country mapping uses the region convention present in the selected table. A country&#x27;s membership does not make a regional input a country measurement.
- Unit statements are retained verbatim from publisher table notes. NA or absent statements remain unspecified; source descriptions may give further context.
- Pairs are side-by-side receipts only. No addition, subtraction, ranking, conversion or aggregation is performed, including for matching table fields.
- Different tables can use the same year column for unrelated concepts. Read full row qualifiers, release, source notes and region mappings before interpreting a pair.

## Research artifact linkage explorer

**Recorded example. Runs locally.**

```bash
doe-mcp app research-linkage --output ./research-linkage-run
```

[Download complete bundle](data/apps/research-linkage/bundle.zip) · [View report and source evidence](data/apps/research-linkage/report.json) · [File checksums](data/apps/research-linkage/manifest.json)

Search literature, dataset and software catalogs, then inspect source-linked inventory, shared DOI identity and typed publisher relationship assertions separately. The preset uses three different recorded queries; it does not claim that these records form a research chain.

Returned **10 records**, **0 shared identities** and **0 explicit relationship assertions**. **10 records** have no resolved match within this bounded result.

No explicit relationships in a recorded result is a valid outcome. Generic OSTI identifiers include grants and contracts and never create edges. Unresolved publisher targets remain visible in the receipt.

[Inventory CSV](data/apps/research-linkage/inventory.csv) · [Linkage receipt](data/apps/research-linkage/linkage.json)

Use `--live --query YOUR_TOPIC` for one topic across all collections, or set separate `--literature-query`, `--dataset-query` and `--software-query` values. `--rows` and `--page-budget` bound each collection search.

### Scope and limits

- Each collection is searched independently. The recorded preset uses different recorded topics; co-returned records are not relationship evidence.
- Only exact normalized DOIs establish shared identity. Shared identity does not assert that a paper uses a dataset or software.
- Explicit links require a typed DOI assertion in the publisher related\_identifiers field. Grants, awards, journal prose, titles and authors never create links. Unparsed relationship values are counted.
- Unresolved targets remain publisher assertions, without invented metadata, URLs or follow-up requests. No target artifact or repository content is downloaded.
- Search scope retains every page&#x27;s coverage and publisher totals. Unmatched means unmatched within these bounded returned records, not proof that a relationship does not exist.
- The literature tool deduplicates its two collections by DOI; this inventory preserves its returned rows and original envelope evidence, rather than reconstructing discarded rows.
- Source text is third-party data. CSV cells that could be spreadsheet expressions are prefixed with an apostrophe; JSON retains original titles.

## Forecast panel with common cohorts

**Synthetic simulation. Fictitious regions. Runs locally.**

```bash
doe-mcp app forecast-panel --output ./forecast-panel-run
```

[Download complete bundle](data/apps/forecast-panel/bundle.zip) · [View report and source evidence](data/apps/forecast-panel/report.json) · [File checksums](data/apps/forecast-panel/manifest.json)

This example generates two fictitious regions, two illustrative forecast series and two lead times. It is a deterministic synthetic simulation, not publisher data or a real-world performance result. The measurement basis is five-minute instantaneous electric-power samples in MW.

Each model and persistence uses exactly the same eligible targets. In this preset's cross-region intersection mode, both regions also use the same target timestamps per lead. Missing peer forecasts and missing actuals remain explicit exclusions; persistence only sees observations available by issuance.

Status: **partial**. Included **42** of **48** declared region/lead/target slots. No ranking is produced.

- [Input plan JSON](data/apps/forecast-panel/plan.json)
- [Simulated actuals CSV](data/apps/forecast-panel/actuals.csv)
- [Simulated forecasts CSV](data/apps/forecast-panel/forecasts.csv)
- [Included targets CSV](data/apps/forecast-panel/included-targets.csv)
- [Row exclusions CSV](data/apps/forecast-panel/row-exclusions.csv)
- [Metrics CSV](data/apps/forecast-panel/metrics.csv)
- [Cohorts JSON](data/apps/forecast-panel/cohorts.json)

| Region | Lead seconds | Series | Common targets | MAE MW | RMSE MW | Bias MW |
|---|---:|---|---:|---:|---:|---:|
| sim-east | 300 | sim-trend | 11 | 4.091 | 4.167 | -4.091 |
| sim-east | 300 | sim-offset | 11 | 0.636 | 0.798 | -0.091 |
| sim-east | 300 | persistence | 11 | 3.182 | 3.451 | -3.182 |
| sim-east | 600 | sim-trend | 10 | 4.200 | 4.266 | -4.200 |
| sim-east | 600 | sim-offset | 10 | 0.600 | 0.775 | -0.200 |
| sim-east | 600 | persistence | 10 | 6.200 | 6.372 | -6.200 |
| sim-west | 300 | sim-trend | 11 | 4.091 | 4.167 | -4.091 |
| sim-west | 300 | sim-offset | 11 | 0.636 | 0.798 | -0.091 |
| sim-west | 300 | persistence | 11 | 3.636 | 4.134 | -3.636 |
| sim-west | 600 | sim-trend | 10 | 4.200 | 4.266 | -4.200 |
| sim-west | 600 | sim-offset | 10 | 0.600 | 0.775 | -0.200 |
| sim-west | 600 | persistence | 10 | 6.300 | 6.504 | -6.300 |

Display metrics are rounded to three decimals. The report retains full precision and every scored or excluded slot.

Import your own declared CSVs with `--plan`, `--actuals` and `--forecasts`. The import preserves raw input hashes and attribution but cannot establish that supplied observations, issuance times or availability times are historically true. A simulation declaration remains simulation on reimport. [Input format and measurement rules](apps.html#forecast-panel-with-common-cohorts).

### Scope and limits

- This panel compares declared inputs, not publisher-verified observations. A simulation run does not measure real-world model performance.
- Every compared model and persistence use the identical eligible target set within each region and lead. Cross-region intersection mode additionally uses the same targets in every declared region.
- Within-region mode permits different region cohorts. No cross-region aggregate score or ranking is produced in either mode.
- Availability and issuance timestamps are assertions in the supplied files; their historical truth cannot be established from a CSV.
- Persistence uses only observations effective and available at or before issuance, within the declared maximum age. Later observations never set its prediction.
- Quantity, unit, cadence, interval meaning and timestamp meaning must match the declaration. Power samples, interval means and energy totals cannot be mixed; no unit conversion or temporal aggregation is performed.
- Missing declarations are refused. Missing values, forecasts and observations remain explicit cohort exclusions; a region with no common targets stays in the report with null metrics.
