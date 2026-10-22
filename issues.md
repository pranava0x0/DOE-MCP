# UAT issues

Last updated: 2026-10-22.

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
