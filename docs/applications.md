# Applications built on DOE-MCP

These are local command-line applications built on existing read-only tools. The examples below are recorded demonstrations, not live services. Download a complete sample bundle or run a command to create your own.

A server hosts tools. A tool returns data and provenance. A skill describes a tested workflow. An application combines tools and writes a result bundle. [Installation and application options](apps.html) · [Servers](index.html#servers-list) · [Tool reference](reference.html) · [Scientific skills](guide.html#connect-a-client-and-provide-a-skill)

Site examples normalize replay retrieval clocks and request IDs for repeatable builds. The normalized clock is not a capture date. Source data and fixture hashes are preserved. CLI runs preserve their original tool envelopes.

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
