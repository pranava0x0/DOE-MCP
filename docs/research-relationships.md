# One-hop publisher relationship resolution

The research linkage application can follow the original publisher's typed DOI
assertions with `--resolve-relationships`. It searches catalog metadata only.

```bash
doe-mcp app research-linkage --dataset-query EAGLE-I --resolve-relationships --output ./relationships
```

This recorded example retains an EAGLE-I `IsPartOf` assertion targeting
`10.13139/ornlnccs/1975203`. Anonymous responses captured on 2026-11-22 contain
two exact DOI candidates: `osti-gov-records:1975203` and
`osti-data-explorer:1975203`. The result is **ambiguous** because these are two
source records. They share a DOI; this does not establish two different artifacts.
The original assertion, both candidates and their evidence pointers remain in
`report.json` and `linkage.json`.

`research.resolve_doi` searches the fixed active OSTI.GOV, DOE PAGES, DOE Data
Explorer and DOE CODE collections. The first three accept the documented `doi`
filter. DOE CODE uses its supported `all_fields` search; only an exact normalized
DOI in a returned record is accepted. Search text alone never establishes a match.
The [official OSTI API documentation](https://www.osti.gov/api/v1/docs) describes
the DOI filter, record arrays and page parameters. The real fixture retains the
actual request URL, parameters, total headers and capture clock for every response.

The application snapshots the original typed assertion frontier. It searches each
distinct target once, up to eight targets, with a shared follow-up budget of four
HTTP attempts by default. `--relationship-request-budget` accepts 1–24 attempts.
Retries and redirects each consume an attempt; cache hits consume none. Recorded
replays count recorded fetch operations. The tool also accepts `rows` 1–10 and
`page_budget` 1–3; the application uses five rows and one page per collection.
These limits bound search work and returned output separately.

A single exact candidate is `resolved` only when all four selected collections
completed with known, consistent totals and no missing pages. Multiple distinct
source/record candidates are `ambiguous`, even when their DOI agrees. Unknown
coverage remains `unresolved`; exhausted request or page budgets are
`budget_limited`; source failures remain visible in coverage. With fewer than
two candidates, a source failure produces `failed`. Ambiguous candidates can
coexist with incomplete search scope. A failed follow-up retains its partial envelope and leaves the discovery inventory available. These statuses
describe this bounded catalog search and never establish global absence.

Returned relationships do not expand the frontier. Titles, topic similarity,
grants, contracts and untyped identifiers never create links. DOI URLs, repository
URLs and artifact files are never fetched. `matched_returned_record` describes
matches already present in the discovery inventory; the separate `followup`
field describes the bounded publisher lookup.

The recording tool makes only anonymous requests and stops after a failed source:

```bash
python tools/record_research_relationships.py --output /tmp/relationships-new.json
```

It refuses to overwrite an existing recording. Partial recordings support only
the interactions they actually contain. The default CLI example remains the
original inventory preset; the public application bundle explicitly selects the
EAGLE-I query and enables one-hop resolution.
