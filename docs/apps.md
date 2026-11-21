# Local applications

These applications compose source tools or declared input files. Publisher
applications replay recorded responses by default; the forecast panel runs
a labelled simulation or imports CSVs. Where supported, `--live` queries
publishers through the same source activation gates as the MCP servers.

Each `--output` writes a new directory containing `report.json`, CSV files
and `manifest.json` with the SHA256 and byte count of each exported file.
The report preserves original tool envelopes, including evidence and
coverage. Replay retrieval clocks describe the replay execution. They are
not publisher capture times; available fixture capture times are listed
separately. Existing output directories are refused.

## GCAM comparison receipts

```bash
doe-mcp app gcam-comparison --output ./gcam-receipts
doe-mcp app gcam-comparison --plan ./gcam-receipts/plan.json --output ./gcam-custom
```

[Download the recorded preset and receipts](applications.html#gcam-comparison-receipts).
The preset selects Japan and Indonesia's regional geothermal inputs, Kenya
and Canada's regional soil parameters, and separate desalination energy
and cost inputs. Every value retains its full row, selected column, source
file, release, source notes and evidence reference. Decimal spellings and
null cells stay as the publisher supplied them.

Edit `plan.json` to select other rows and columns from existing GCAM tools.
A plan contains 1–12 `selections` and 1–12 side-by-side `pairs`. Each
selection has a unique `id`, a GCAM `tool`, exact text `filters`, and 1–8
`columns`. Pair endpoints name a selection and one of its selected columns:

```json
{
  "selections": [
    {"id": "wind-c7", "tool": "power.wind_capacity_factors",
     "filters": {"wind_class": "c7"}, "columns": ["CF"]},
    {"id": "deep-cost", "tool": "power.offshore_depth_costs",
     "filters": {"depth_class": "deep"}, "columns": ["capital.overnight"]}
  ],
  "pairs": [
    {"left": "wind-c7", "left_column": "CF",
     "right": "deep-cost", "right_column": "capital.overnight"}
  ]
}
```

Both example tables have recordings. `--live` reads the selected files
from the pinned release; it does not switch to a newer release. No key is
needed. A selector must return exactly one row with complete pagination.
Empty, ambiguous or incomplete selections are refused before export.

For a regional table, optional `country_iso` resolves a lowercase
three-letter country code through the publisher's country table. The app
uses the region column present in the target table: `region_GCAM3` or
`GCAM_region_ID`. It retains both original country classifications in the
mapping receipt. An explicit regional filter that conflicts with the
mapping is refused. Country selection cannot be applied to a global table.

Indonesia maps to Southeast Asia in the geothermal table; a regional
input does not become an Indonesia observation. The soil table prints
`Units: NA`, while its description explains recovery in years. The
receipt preserves both statements and marks the units line unspecified.

`receipts.csv` indexes the selected cells. `receipts.json` contains full
rows, original unit statements and mapping receipts. `comparisons.json`
describes whether paired cells share a table and column, use different
fields or releases, have unspecified units, or contain a null. These are
structural checks, not proof that two scientific quantities are
interchangeable. No arithmetic, ranking, aggregation or unit conversion
is performed. A shared `2100` column cannot turn desalination energy and
monetary inputs into a total. These are model inputs, not scenario output
or measurements of current conditions.

## Forecast benchmark

```bash
doe-mcp app forecast-benchmark --output ./recorded-benchmark
doe-mcp app forecast-benchmark \
  --forecasts ./recorded-benchmark/forecasts.csv \
  --actual-timezone America/Los_Angeles --output ./submitted-benchmark
```

The first command creates two retrospective sample forecasts from recorded
BPA readings. These are application inputs derived for a demonstration,
not recorded publisher forecasts. The second scores that exported input.
Use your own UTF-8 CSV with these columns:

```csv
model_id,issued_at,target_at,prediction_mw
example,2026-09-03T07:00:00+00:00,2026-09-03T07:05:00+00:00,5000
```

Timestamps must carry UTC offsets, predictions must be finite, and
issuance must precede the target. A model, target and lead combination
must be unique. Issuance is the submitter's assertion; the application
cannot prove when a prediction was made. The example above illustrates
the input format; a target outside the returned BPA window is excluded.

The report gives mean absolute error, root mean squared error, signed
bias and matched and excluded counts separately for each model and lead.
Actuals are BPA timestamped sample readings in MW, not interval means.
Different models may have different matched rows.

The report and CLI show a scoring status: `scored` means every submitted
forecast has a usable actual, `partial` means some do, and `unscored`
means none do. Each model and lead group has the same status field.
Persistence has its own status because it also needs a usable prior
observation. An unscored run still exports evidence and exclusion reasons,
with null metrics and a CLI warning that no forecasts were scored.

Persistence uses the latest observation at or before issuance. A missing
prior, stale prior or gap along the five-minute observation path prevents
a persistence score. It never uses an observation after issuance to set
the prediction. Every exclusion has a reason in the output rows.

Optional `lower_mw`, `upper_mw` and `nominal_coverage` columns must occur
together on an interval row. The report separates empirical coverage by
model, lead and stated nominal coverage. It does not fit intervals or
guarantee future coverage.

For live scoring, supply `--forecasts`, add `--live` and choose
`--intervals` within BPA's rolling window. An explicit
`--actual-timezone` is required when BPA omits per-reading offsets.
The recorded sample uses `America/Los_Angeles` as a disclosed assumption.
Ambiguous or nonexistent local timestamps are refused.

## Forecast panel with common cohorts

```bash
doe-mcp app forecast-panel --output ./panel-simulation
doe-mcp app forecast-panel --plan ./panel-simulation/plan.json \
  --actuals ./panel-simulation/actuals.csv \
  --forecasts ./panel-simulation/forecasts.csv --output ./panel-reimport
```

The default is a deterministic synthetic simulation with two fictitious
regions, two forecast series and two leads. Its missing forecast and blank
actual exercise exclusions. It contains no publisher fixtures or real
region observations and does not measure real-world model performance.
[Download its plan, inputs, cohorts and metrics](applications.html#forecast-panel-with-common-cohorts).
Reimporting a plan declared as simulation keeps that label.

To compare your own inputs, provide all three files. The plan declares:

- `data_kind`: `user_supplied` or `simulation`.
- `cohort_mode`: `within_region` or `cross_region_intersection`.
- `models`: 2–12 entries, each with a unique `id`, display `label` and source `attribution`.
- `regions`: 1–8 entries with the same fields. A region stays in the output even when it has no eligible targets.
- `lead_seconds`: 1–8 positive integer leads, each a measurement-cadence multiple, at most seven days.
- `window`: inclusive `start` and `end` with UTC offsets, separated by whole measurement intervals.
- `measurement`: `quantity`, `unit`, `interval_seconds`, `interval_semantics` and `timestamp_semantics`.
- `baseline_max_age_seconds`: the oldest effective observation that persistence may use at issuance, at most seven days.

The supported measurement declarations are:

| Quantity | Unit | Interval semantics | Timestamp semantics |
|---|---|---|---|
| `electric_power` | `MW` | `instantaneous_sample` | `sample_time` |
| `electric_power` | `MW` | `interval_mean` | `interval_end` |
| `electric_energy` | `MWh` | `interval_total` | `interval_end` |

One panel has one measurement basis. Every input row repeats those labels
and its `interval_seconds` must match the plan. The application performs no
unit conversion or temporal aggregation. BPA's five-minute MW samples
cannot be combined with EIA's hourly MWh totals in a panel. Sample power,
mean power and interval energy also remain distinct.

Actual CSV columns are:

```csv
region_id,target_at,available_at,value,quantity,unit,interval_seconds,interval_semantics,timestamp_semantics
```

Forecast CSV columns are:

```csv
model_id,region_id,issued_at,target_at,prediction,quantity,unit,interval_seconds,interval_semantics,timestamp_semantics
```

Use UTF-8, aware ISO timestamps and finite numeric values. A blank `value`
or `prediction` means missing. Effective and availability times must be
separate: `available_at` states when that observation was available to the
forecast system and must be at or after its sample time or interval end.
Issuance, availability and source attribution remain caller assertions;
the application cannot prove their historical truth. Imported bytes are
preserved with hashes and marked `not_publisher_verified`.

Persistence selects the latest effective finite observation available at
or before issuance, within the declared maximum age. Later values and
delayed publications never set the prediction. Include historical actual
rows before the scoring window when they are needed to supply a baseline.

Within each region and lead, a target is eligible only if its actual,
every declared model prediction and persistence are usable. All series
are scored on that same set. `cross_region_intersection` further takes
the intersection across every declared region for each lead. A missing
region can therefore make the entire common cohort empty.
`within_region` permits different cohorts between regions. Neither mode
produces an aggregate cross-region score or ranking.

The report retains all expected model/region/lead/target slots. Each
excluded slot names the missing input or blocking region. Forecast rows
outside the window or off its grid are listed separately; undeclared
models, regions and leads are refused. Duplicate identities are refused
after converting timestamps to UTC. Availability rows are indexed by
their original CSV record number.

The bundle preserves all three raw input files and adds `cohorts.json`,
`included-targets.csv`, `scored-rows.csv`, `row-exclusions.csv`,
`input-exclusions.csv` and `metrics.csv`. Empty cohorts retain null metrics
and an `unscored` status, with a CLI warning. A partially populated panel
is `partial`; all expected targets eligible is `scored`. No region is
silently dropped. A run is bounded to 2,000 target times and 100,000 model
slots; each input file is at most 20 MB and each CSV at most 100,000 rows.

This application reads local files only. Its `--live` and `--fixtures`
options are refused. Existing publisher tools keep their own source and
credential gates; this application does not configure or acquire a key.

## Weather package

```bash
doe-mcp app weather-package --output ./recorded-weather
doe-mcp app weather-package --latitude 35.9313 --longitude -84.3104 \
  --start 2023-06-01 --end 2023-06-05 --variables prcp,tmax,tmin \
  --output ./recorded-weather-variables
doe-mcp app weather-package --live --latitude 35.9313 --longitude -84.3104 \
  --start 2024-06-01 --end 2024-06-05 --variables prcp,tmax,tmin \
  --output ./live-weather
```

Both recorded commands use existing recordings. Other points, dates and
variable combinations require `--live` or matching recordings.

The package includes `weather.csv` and `weather-croissant.json`. Variable
names, units and citation come from the response. Export checks the
coordinate echo, requested and returned ranges, dates, populated values,
CSV checksum and Croissant field mappings before writing files.

Daymet supplies modelled daily weather for a 1 km grid cell. It retains
February 29 and omits December 31 in leap years. The package keeps that
calendar and never imputes missing values. An incomplete extract is
refused. The CSV checksum identifies the exported bytes, not the full
publisher archive.

## Federal Register notice tracker

```bash
doe-mcp app docket-tracker --output ./recorded-notices
doe-mcp app docket-tracker --previous ./recorded-notices/report.json \
  --output ./compared-notices
doe-mcp app docket-tracker --source federal-register --live \
  --docket EERE-2025-OT-0031 --rows 5 --page-budget 2 --output ./live-notices
```

The default source is `federal-register`. The report labels the result
“Federal Register notice tracker.” The command name is retained for the
planned docket integration. Published notices do not establish a complete
Regulations.gov docket, and this source does not retrieve public comments
or supporting docket documents.

The recorded preset reads two pages of DOE notice metadata, including a
page containing only FERC records excluded by the existing source scope.
`--query`, `--docket`, `--since`, `--until`, `--document-type`, `--rows`
and `--page-budget` bound a live search. The report names unread pages and
unknown totals, groups records under every returned docket identifier,
and retains records with no docket identifier.

Publication, effective and comment-close dates remain separate. A previous
snapshot must use the same mode and search parameters. Changes are reported
as added, changed or not returned; an absent record is never labelled
deleted or withdrawn.

## Regulations.gov integration: gated

```bash
doe-mcp app docket-tracker --source regulations --docket EERE-2025-OT-0031
```

This explicit source selection currently refuses with the source's
activation reason in both recorded and live mode. There is no
Regulations.gov recording, and the application does not substitute Federal
Register notices for a docket. The source remains proposed and `doe-docs`
remains planned. The exploration capture stopped at HTTP 429 without retry.

The prepared integration reads one docket and bounded pages of document
metadata under agency code EERE. It has no comment submission route and
retrieves no public comment text, attachments or document bodies. A live
run will require a user-owned `API_DATA_GOV_KEY`; the public demonstration
key is refused by the normal credential path.

Activation still requires recorded publisher responses and the checks in
[Regulations.gov activation](regulations-activation.md). No broader DOE
agency scope is inferred from EERE or from a current office name.

## Research artifact linkage explorer

```bash
doe-mcp app research-linkage --output ./recorded-research
doe-mcp app research-linkage --live --query geothermal \
  --rows 10 --page-budget 2 --output ./geothermal-research
```

The explorer searches literature, dataset and software catalogs through
the existing research tools. It writes `inventory.csv` and `linkage.json`,
alongside the original envelopes in `report.json` and file checksums in
`manifest.json`. Every inventory record and relationship assertion points
to its record and evidence within `report.json`. IDs include the source
and publisher record ID, so numbers from different catalogs do not collide.

The recorded preset uses the existing `perovskite solar` literature,
`geothermal` dataset and `machine learning` software recordings. These are
three separate searches, not a claim that their results are related.
It returns ten records and no explicit relationship assertions. This is a
valid result: every record remains visible as unmatched. Different live
searches can use `--query` for all three catalogs, or separate
`--literature-query`, `--dataset-query` and `--software-query` arguments.
Unrecorded parameter combinations require `--live` or matching recordings.

The receipt separates three outcomes:

- An exact normalized DOI shared by records establishes shared identity.
  It does not establish that a paper uses a dataset or software.
- An explicit relationship requires a publisher-supplied relation type,
  target identifier and DOI namespace in `related_identifiers`. The tool
  recognizes the recorded OSTI `CURATED; relation; JSON` format and retains
  the field, original value and source evidence. Unknown formats are counted
  as unparsed and create no edges.
- An unmatched record has no resolved explicit relationship or shared DOI
  within the returned results. An explicit target missing from the returned
  inventory remains an unresolved publisher assertion; the app invents no
  target metadata, locator or additional request.

Titles, authors, shared awards, generic OSTI identifiers and co-occurrence
in a query never establish a relationship. The publisher's journal prose
in `relation` is not an artifact link. Source text remains third-party data.
CSV cells that could be spreadsheet expressions receive an apostrophe;
JSON preserves the original titles.

`--rows` accepts 1–50 records per source and page, and `--page-budget`
accepts 1–5 pages per collection. The preset uses the recorded page sizes.
The receipt retains every page's coverage, publisher totals and stopping
reason. A budget stop or unknown total cannot establish exhaustive coverage.
The literature tool deduplicates overlapping collections by DOI; the app
does not reconstruct discarded rows. No repository, dataset payload or
relationship target is downloaded.
