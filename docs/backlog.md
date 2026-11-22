# Development backlog

Updated 2026-11-22 after PRs 19–30 and the BPA observation import addition. This is the public development backlog.
Items below are plans, not claims that a proposed integration works.

A **server** hosts tools for a domain. A **tool** performs one bounded read.
A **skill** explains how to combine tools and interpret their evidence.
An **application** produces an output bundle; a **demo** shows an example.
Extend an existing server when its data domain fits. Add a new server only
when its dependency, credential or operating boundary requires one.

## Completed

| Status | Change | Evidence |
|---|---|---|
| Done | DNS connection pinning and credential safeguards; current EIA recording sanitized. Historical key ownership and revocation remain open. | [PR 19](https://github.com/pranava0x0/DOE-MCP/pull/19) |
| Done | Search metadata, llms.txt, site journeys and CI checks. | [PR 20](https://github.com/pranava0x0/DOE-MCP/pull/20) |
| Done | Forecast benchmark, weather packages and Federal Register notice tracker. Regulations.gov remained gated. | [PR 21](https://github.com/pranava0x0/DOE-MCP/pull/21) |
| Done | GCAM comparison receipts across fields and region conventions. | [PR 22](https://github.com/pranava0x0/DOE-MCP/pull/22) |
| Done | Paper, dataset and software inventory with explicit publisher relationships. | [PR 23](https://github.com/pranava0x0/DOE-MCP/pull/23) |
| Done | Offline quality, token and cost comparison of supplied evaluation reports. No real model ranking was measured. | [PR 24](https://github.com/pranava0x0/DOE-MCP/pull/24) |
| Done | Multi-region forecast comparisons on common periods, with a labelled simulation and file imports. | [PR 25](https://github.com/pranava0x0/DOE-MCP/pull/25) |
| Done | Pinned ATB 2024/2025 tools, server and application. PVWatts remains proposed. | [PR 26](https://github.com/pranava0x0/DOE-MCP/pull/26) |
| Done | Direct homepage copy, desktop/mobile section navigation and automated accessibility checks. Manual screen-reader testing remains future work. | [PR 27](https://github.com/pranava0x0/DOE-MCP/pull/27) |
| Done | ATB assumptions skill, five recorded checks and a two-edition demo covering exact qualifiers, ambiguity, nulls, empty selections and currency bases. Evaluation tasks are prepared; no model-quality result is claimed. | [PR 28](https://github.com/pranava0x0/DOE-MCP/pull/28) · [Demo](demos.html#atb-assumptions) |
| Done | Offline profile preflight with versioned JSON, installation and source-gate diagnostics, and guarded base/extra wheel checks. Credentials and network remain explicitly unchecked. | [PR 29](https://github.com/pranava0x0/DOE-MCP/pull/29) · [Profile preflight](profile-preflight.html) |
| Done | Bounded, one-hop DOI resolution across four OSTI catalogs, retaining publisher assertions, source evidence, ambiguity and request limits. The recorded EAGLE-I example has two source-scoped candidates for one DOI. | [PR 30](https://github.com/pranava0x0/DOE-MCP/pull/30) · [Resolution rules](research-relationships.html) · [Application](applications.html#research-artifact-linkage-explorer) |
| Done | BPA observation imports with original bytes, hashes, row receipts and explicit timezone assumptions. Unknown availability stays unknown; the recorded example creates no forecasts or baseline candidates. | [Recorded import](applications.html#bpa-observations-for-forecast-panels) · [Import contract](apps.html) |

The [applications](applications.html), [demos](demos.html) and
[evaluation example](evaluation-example.html) retain their evidence and
limitations. Recorded examples and simulations are labelled separately.

## Keyless batch completed

All four items shipped: the ATB assumptions skill, offline profile preflight,
one-hop publisher relationship resolution and BPA observation imports.
Each retains the source gates and runs replay, browser and accessibility
checks on PRs and main.

Use Sol for implementation and focused tests;
use Astra for scientific contracts and final review. Reserve larger-model
reviews for substantive risks rather than routine copy or formatting.

## Next work without keys

- **Medium — Tool and application:** bounded complete ATB exports. Keep all
  qualifiers and per-page evidence; stop with explicit incomplete status
  when a declared row or request budget is exhausted. Never relabel a
  ten-row page as the complete result.
- **Medium — Weather application:** multiple-point package plans with a
  per-point manifest, units, calendar checks and total request budget.
  Keep the existing single-point package format usable.
- **Medium — Skill evaluation:** import human reviews of existing answers
  into the matrix. Bind reviews to exact task, rubric and answer hashes.
  Missing grades stay unknown; no provider account is needed to review
  supplied answers or compare existing reports.
- **Medium — Accessibility:** repeat manual VoiceOver and NVDA checks for
  material navigation changes. Automated scans and browser accessibility
  trees are useful regression checks, but do not replace assistive-technology
  testing or establish full WCAG conformance.

## Anonymous candidates that still need evidence

These do not have a key blocker, but they are not ready to activate.

| Candidate | Existing boundary | Work required before shipping |
|---|---|---|
| GDR-scoped discovery | Extend the research server and DOE Data Explorer adapter. | Verify the publisher's repository filter and capture real responses. Preserve incomplete repository coverage; do not repeat the old dated coverage percentage as a current fact. No separate scraper or server. |
| Cambium edition reader | The energy-tech server now exists. | Verify a supported public download, edition/schema, terms and response budget. Define scenario/year/region/hour semantics and capture real data. Undocumented website routes alone do not justify activation. |
| Battery cycling packages | Registry entry remains proposed. | Confirm per-dataset download and redistribution terms, schema and bounded access before choosing an adapter. Public web visibility does not establish a supported API. |

## Gated work

| Priority | Work | Gate | What can proceed without the gate |
|---|---|---|---|
| High | PVWatts monthly production tool | User-owned key, authorized bounded recording, verified publisher response and credential-redaction checks. | Maintain the prepared contract, negative tests and [activation checklist](pvwatts-activation.html). Keep model output null and the source proposed. |
| High | Regulations.gov docket metadata | User-owned key plus a complete real recording within the publisher's quota. The earlier exploration hit HTTP 429. | Improve the working Federal Register tracker and retain the [activation checklist](regulations-activation.html). A key alone is not activation evidence. |
| Medium | Live model/skill quality and cost study | Separately authorized provider access, budget and grading plan. | Prepare tasks, import existing reports and human reviews, and test the offline matrix. Simulation token counts are not provider usage or model-quality measurements. |
| High maintenance | Historical EIA credential exposure | Owner assessment and revocation; any history rewrite needs a coordinated maintenance decision. | Keep current redaction and value-free regression checks. Do not test, print, reuse or republish the historical value. |

No new key is requested by this backlog. Do not use public demonstration
keys to work around a gate or retry a quota refusal through another identity.
Hosted MCP, job submission and paid execution remain separate operating
and authorization decisions.
