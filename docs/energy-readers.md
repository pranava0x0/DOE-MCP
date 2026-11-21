# Energy technology assumptions readers

The `doe-energy-tech` MCP server reads two pinned Annual Technology
Baseline electricity releases. Its local application exports qualified
assumptions with their evidence. PVWatts v8 has a prepared monthly adapter
and an activation checklist, but remains proposed and refuses model calls.
No user key is configured or read for this server or the ATB application.

From a source checkout, install the Parquet reader and start the server:

```bash
python -m pip install -e '.[energy-tech]'
doe-energy-tech
```

PyArrow 26.0.1 is pinned in the optional extra. Base research installations
do not import PyArrow or read binary fixtures at context construction.
Calling an ATB tool without that extra gives an installation error.

## Pinned releases

| Edition | Release | Asset size | Data rows | Monetary basis |
|---|---|---:|---:|---|
| 2024 | v4.0.0 | 5,131,651 bytes | 585,630 | 2022 USD unless noted otherwise |
| 2025 | v2.0.0 | 9,193,709 bytes | 1,161,588 | 2023 USD unless noted otherwise |

The public assets were captured on 2026-11-21 from the publisher-listed
OEDI downloads. The reader checks the recorded size and SHA256 before
parsing. Each response identifies the exact edition, release, asset and
hash; retrieval metadata comes from the fetch or its cache.

Publisher sources are the [2025 data page](https://atb.nlr.gov/electricity/2025/data),
[2024 monetary-basis note](https://atb.nlr.gov/electricity/2024/changes_in_2024),
[2025 definitions](https://atb.nlr.gov/electricity/2025/definitions) and
[ATB disclaimer](https://atb.nlr.gov/electricity/2025/disclaimer).
These data contain assumptions and projections supplied for reference.
Their use does not imply publisher endorsement of a result.

Correction dated 2026-11-21: the older registry described ATB 2026 without
a verified release asset. Its data page returned HTTP 404 in this review.
Only the two releases above are supported. There is no `latest` alias,
silent edition substitution or currency conversion. The much larger CSV
downloads exceed the existing response cap and are not used.

## Explore before selecting

Use `tech.describe_atb` with an explicit `edition` and a dimension such as
`technology`, `core_metric_parameter`, `core_metric_case`, `techdetail`,
`scenario`, `core_metric_variable` or `units`. It returns exact distinct
values and a count, with bounded pagination. Optional filters restrict the
values to a particular technology or scenario.

Then call `tech.read_atb` with `edition`, exact `filters`, `rows` and
`offset`. Filter values are publisher text spellings or JSON null; integer
dimensions use their text spelling, such as `"2035"`. Matching is case
sensitive. Unknown column names are refused. `rows` accepts 1–50 and
`offset` is a nonnegative integer.

Every row preserves technology and resource detail, metric, scenario,
projection year, units, financial case, tax-credit case and capital recovery
period. Null values remain missing. The 2025 release also retains the
publisher's reference summary and evidence type. Multiple matches are
reported as multiple matching rows; the reader never chooses an ambiguous
first row as the answer. A truncated page is not the complete selection.

Values with different units or financial qualifiers are separate assumptions.
The edition-wide monetary note is retained alongside the row's units and
publisher qualifiers. It does not override a publisher exception. The app
does not combine the 2024 and 2025 currency bases or convert them into a
single estimate.

## Export a local assumptions bundle

```bash
doe-mcp app energy-assumptions --output ./recorded-atb-2024
doe-mcp app energy-assumptions --edition 2025 \
  --filters '{"technology":"UtilityPV","core_metric_parameter":"CAPEX","scenario":"Moderate","core_metric_variable":"2035"}' \
  --rows 10 --offset 0 --output ./recorded-atb-2025
```

Both commands replay the real pinned release assets. The preset selects
UtilityPV, CAPEX, Moderate and projection year 2035. Other technology,
scenario, financial and resource qualifiers remain explicit; it does not
pick one silently. `--live` fetches the same hash-pinned release through
the guarded GET path. It cannot select a newer release.

The bundle contains `assumptions.csv`, `assumptions.json`, the original
tool envelopes in `report.json` and file checksums in `manifest.json`.
Evidence points to the exact asset and one-based data-row number within
its hash-pinned bytes. The JSON includes pointers into `report.json`.
CSV expression-like text is prefixed with an apostrophe; JSON preserves
the publisher text. Existing output directories are refused.

The reader scans 4,096-row batches and retains only the requested page or
bounded dimension values. It caches release bytes for a day and up to 64
bounded query results. It does not materialize the full table in memory.
Recorded assets are binary sidecars: their small JSON manifests load with
the normal context, while binary reads and hash checks happen only on use.

## PVWatts status

`solar.estimate_production` currently refuses with the proposed source's
activation reason before credential access. No successful model recording
or production output is supplied. The ATB bundle records this status with
null model output. It does not substitute an ATB assumption for a PVWatts
simulation. See the [PVWatts activation checklist](pvwatts-activation.html).
