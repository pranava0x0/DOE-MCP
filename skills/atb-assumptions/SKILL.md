---
name: atb-assumptions
description: >-
  Read qualified ATB electricity cost and performance assumptions from pinned
  2024 or 2025 releases. Use when selecting technology assumptions or comparing
  ATB editions while preserving scenario, resource, financial and currency bases.
metadata:
  doe-mcp-capabilities: tech.cost_assumptions
  doe-mcp-servers: doe-energy-tech
  doe-mcp-credentials: none
---

# ATB assumptions

Use the `doe-energy-tech` server with the `energy-tech` extra installed.
ATB reads public data without credentials.

ATB rows are published scenario assumptions and projections. Describe them as
such, retaining their qualifiers; they are not observed generation, bids or a
project-specific cost estimate. This skill uses ATB only. PVWatts remains
proposed and source-gated, with no model output available.

## Select and read

1. Choose an explicit edition, **2024 or 2025**; there is no `latest` alias.
   Use `tech.describe_atb` to inspect exact publisher values for the dimensions
   relevant to the request. Scope discovery with already known filters. Distinct
   values also have bounded pages: check `total_matches`, `returned`, offset and
   `coverage.pagination` before treating the list as exhaustive.
2. Use `tech.read_atb` with exact publisher column names and text values.
   The projection year is `core_metric_variable`, separate from the edition;
   pass numeric qualifiers as text, for example `"2035"` and `"20"`. Confirm
   `technology`, `techdetail`, `techdetail2`, `resourcedetail`,
   `core_metric_parameter`, `scenario`, `core_metric_case`, `tax_credit_case`
   and `crpyears` as applicable. Case and spelling matter. A null filter is an
   actual JSON null, not `"null"`, an empty string, a wildcard or an omitted filter.
3. Read `selection_status` and `total_matches` before selecting an assumption.
   With multiple matches, present the qualified alternatives or refine using
   caller intent and discovered dimensions. Never choose the first row, use a
   publisher `default` flag as caller authorization, or merge distinct rows.
   `returned` is only the page size. Keep the same edition and filters when
   paging with offset; rows are limited to 1–50 per call. If all qualifiers still
   leave multiple rows, retain their row identities and disclose the ambiguity.
   An empty exact match means no matching rows in this release, not that the
   technology is absent. Do not silently change a qualifier to obtain a hit.
4. Keep metric nulls as null/unknown, never zero or an inferred value. Preserve
   blank units and null qualifiers rather than filling them from nearby rows.

## Report the basis and evidence

Return each selected row's value, units and complete qualifier context, with
`row_number` and `evidence_ref`. Cite the returned evidence locator and retain
the edition/release, asset SHA256, source provenance and pagination limits.
Keep 2025 `ref_summary` and `ref_evidence_type` when present; these describe the
publisher's reference basis and do not replace the returned row evidence.

Carry `currency_basis` and `currency_basis_source` beside monetary values.
The 2024 edition uses 2022 USD and 2025 uses 2023 USD, unless the publisher notes
otherwise; nonmonetary metrics keep their own units. Show the published values
in their separate bases. Do not imply inflation adjustment or calculate a
like-for-like monetary difference across editions without a caller-selected,
documented conversion method and source. Also recheck qualifiers in each edition:
financial cases can change, even when a technology label stays the same.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | Read 2024 offshore-wind Class3 Fixed-Bottom fixed O&M for 2035, Moderate, Market, ITC, 20-year CRP. | Discovers financial cases and reports matching rows with qualifiers, units, release/hash and row evidence. |
| 2 | Give the offshore-wind fixed O&M assumption for 2035 Moderate in each edition, using two rows. | Separates total matches from returned counts and declares page coverage; resolves ambiguity before selecting an assumption. |
| 3 | Read 2024 pumped-storage National Class 1 FCR for 2026 with exact financial qualifiers. | Preserves source values, units and row identities; distinguishes actual null filters from literal text without filling missing values. |
| 4 | Use the same Class3 Market/ITC qualifiers in 2025; also try lowercase `offshorewind` in 2024. | Checks exact matches and available financial cases; never silently substitutes a spelling or financial case. |
| 5 | Compare Class3 Fixed-Bottom fixed O&M for 2035 Moderate, R&D, 20-year CRP across 2024 and 2025. | Reports separately qualified values with currency bases and their sources; requires a documented conversion before a like-for-like monetary difference. |
