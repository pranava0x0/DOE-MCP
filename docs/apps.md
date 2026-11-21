# Local applications

These applications compose the existing source tools. They replay recorded
publisher responses by default. `--live` makes a publisher request with the
same source activation gates as the MCP servers.

Each `--output` writes a new directory containing `report.json`, CSV files
and `manifest.json` with the SHA256 and byte count of each exported file.
The report preserves original tool envelopes, including evidence and
coverage. Replay retrieval clocks describe the replay execution. They are
not publisher capture times; available fixture capture times are listed
separately. Existing output directories are refused.

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

## Docket tracker

```bash
doe-mcp app docket-tracker --output ./recorded-docket
doe-mcp app docket-tracker --previous ./recorded-docket/report.json \
  --output ./compared-docket
doe-mcp app docket-tracker --live --docket EERE-2025-OT-0031 \
  --rows 5 --page-budget 2 --output ./live-docket
```

The default selects Regulations.gov docket `EERE-2025-OT-0031` and one
page of document metadata. Live calls require a user-owned api.data.gov
key configured as `API_DATA_GOV_KEY`. The public demo key is not accepted
for live application runs. The verified agency scope is EERE.

`--query` searches publisher text, and `--since` and `--until` bound posted
dates. These are publisher search filters. `--rows` accepts 5 through 20;
`--page-budget` accepts 1 through 20. Unread pages and unknown totals are
reported. The bundle preserves the publisher's document links and Federal
Register cross references without constructing URLs from identifiers.

The application reads docket and document metadata. It does not download
public comment bodies, attachments or supporting file contents. Posted,
comment-start, comment-end and last-modified dates remain separate. A
previous snapshot must use the same mode and search parameters. Changes
are reported as added, changed or not returned; an absent record is never
labelled deleted or withdrawn.

## Federal Register notice snapshots

```bash
doe-mcp app docket-tracker --source federal-register --output ./recorded-notices
doe-mcp app docket-tracker --source federal-register \
  --previous ./recorded-notices/report.json \
  --output ./compared-notices
```

The recorded preset reads two pages of DOE notice metadata, including a
page containing only FERC records excluded by the existing source scope.
`--query`, `--docket`, `--since`, `--until`, `--document-type`, `--rows`
and `--page-budget` bound a live search. The report names unread pages and
unknown totals, groups records under every returned docket identifier,
and retains records with no docket identifier.

Publication, effective and comment-close dates remain separate. These
snapshots do not contain Regulations.gov comments or supporting materials.
A previous snapshot must use the same mode and search parameters. Changes
are reported as added, changed or not returned; an absent record is never
labelled deleted or withdrawn.
