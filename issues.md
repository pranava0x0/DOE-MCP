# UAT issues

Last updated: 2026-11-21.

## Review findings, 2026-11-21

### REVIEW-001: Credential input and storage

Status: resolved 2026-11-21. Category: config. Complexity: medium.
Credential writes follow links and apply private permissions after writing.
Multiline values can add assignments, and an unknown credential name is echoed.
The fix must reject unsafe input without printing it and write privately.

### REVIEW-002: Credentials in publisher errors and request logs

Status: resolved 2026-11-21. Category: backend. Complexity: medium.
Publisher error messages and HTTP request logs can contain the query key.
The fix must redact the value before either reaches a caller or log handler.

### REVIEW-003: Catalog coverage after a blocked search

Status: resolved 2026-11-21. Category: data. Complexity: medium.
Discovery reports an empty, covered search when every catalog is blocked.
It also reports truncated pagination for both zero and unknown totals.

### REVIEW-004: Separate retrieval metadata for facility outages

Status: resolved 2026-11-21. Category: data. Complexity: medium.
A cached system board and a fresh planned-outage response share the board's
retrieval metadata. Outage evidence must retain its own fetch metadata.

### REVIEW-005: Homepage keyboard navigation and deep links

Status: resolved 2026-11-21. Category: frontend. Complexity: low.
Section links scroll without moving keyboard focus. The page lacks a main
landmark and a skip link. A malformed URL fragment raises an uncaught error.

### REVIEW-006: Finding a workflow in the expanded demo catalog

Status: resolved 2026-11-21. Category: frontend. Complexity: low.
The homepage presents every demo without a search control. Add a text filter,
a result count and a visible empty state while keeping all links available
when JavaScript is disabled.

### REVIEW-007: Dependency advisory ranges

Status: resolved 2026-11-21. Category: config. Complexity: low.
The MCP and Pydantic pins matched publisher advisory ranges. Updated to
2.2.0 and 2.14.2, respectively, with version-floor regression tests.
The reviewed application paths do not demonstrate exploitation of those
advisories. SDK contracts and the local stdio client pass with the new pins.

### REVIEW-008: Nested credential fields in recordings

Status: current recording and recorder resolved 2026-11-21.
Category: data. Complexity: medium.
One EIA response contained an unredacted credential-shaped value under
`body.request.params.api_key`. The recorder now discovers named credentials
recursively and removes their values from the full response. Its helper
sanitized that field in the existing recording; no response was invented or
re-recorded. A fixture scan checks named credential fields without printing
values. Prior repository history was not changed. Credential ownership and
whether the value remains valid were not tested; a maintainer must assess
rotation and any historical cleanup.

### REVIEW-009: GCAM table discovery

Status: resolved 2026-11-21. Category: backend. Complexity: low.
Source descriptions now list table tools and exact filter columns from the
existing manifests. Metadata inspection makes no publisher request, and a
proposed source still refuses a table read. New replay tests cover both paths.

### REVIEW-010: Semantic skill evaluation coverage

Status: preparation resolved 2026-11-21; model runs remain future work.
Category: tests. Complexity: low.
Added optional tasks and human rubrics for region conventions, blank cells,
units and model inputs versus observations. Preparation and profile tests
pass. These tasks do not establish model accuracy until answers are graded.

## Resolved

### UAT-001: Repeated scrolling to results and demo selector

Severity: high. Resolved 2026-10-22.
The microbiome samples section started 1,823 px below the viewport top.
Added persistent demo selection and section buttons. Chrome verified all
78 jumps across 14 demos and three viewport sizes; focused sections remain
below the controls. The samples section now takes one tap to reach.

### UAT-002: Wide tables require horizontal movement on phones

Severity: low. Resolved 2026-10-22.
Tables with more than two columns become labeled rows below 600 px. Column
headers remain in the accessibility tree. The sample table has no horizontal
overflow. Record-source cards duplicating a displayed result are expandable.

### UAT-003: Unnecessary favicon request

Severity: low. Resolved 2026-10-22.
An inline empty favicon removes the request for a nonexistent favicon file.
Chrome recorded no secondary resource requests on the demos page.
