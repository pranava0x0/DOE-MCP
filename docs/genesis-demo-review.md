# Genesis demo review — October 20, 2026

PR #7 now combines Agent Skills format compatibility with two no-cost
workflow demonstrations and a revised landing page. It does not install
or execute the upstream Genesis catalog.

## What can be tried

- `doe-mcp demo dataset-card`: two geothermal catalog records, draft
  metadata and an explicit owner-review list. No assertion of Genesis or
  Croissant schema validity, file integrity, reuse rights or AI suitability.
- `doe-mcp demo microbiome`: a shale-study search followed by five samples
  from a returned study ID. Sequence files are not retrieved.
- The existing literature walkthrough remains a third starting point.

The default is recorded replay. Both new commands also completed with
`--live` on October 20 without credentials or a model. A public metadata
record does not grant access to the underlying dataset. Separate AI
clients may charge for inference.

## Research boundaries

The [DOE release](https://www.energy.gov/science/articles/genesis-agent-skills-release-support-innovation-across-genesis-mission)
and [PNNL announcement](https://www.pnnl.gov/news-media/pnnl-and-berkeley-lab-lead-genesis-mission-skills-release)
establish the October 15 public release. The existing research archive and
upstream source review informed the integration choice. Indexed LinkedIn
announcements from PNNL and PPPL were found; no individual post permalink
was verified. Targeted X searches yielded no relevant verifiable posts.
Social content was not used to establish technical capability.

Facility operations, data movement and metered model services are excluded.
The facility-status demo is deferred: its existing adapter combines status
and planned-outage fetches under the status fetch metadata. That needs a
separate provenance review before presenting a combined status demo.

## Review and validation

Initial code review read PR #7 at `69d332f`, including the shared skill
reader, its consumers, conformance tests and record truncation changes.
No new blocking finding in that implementation. The final follow-up review
checked data dependencies, empty-result refusal, publisher-text escaping,
source references, cost claims and generated output. It corrected the
microbiome summary so a missing total is shown as unknown.

- Full replay suite: 709 tests passed. New tests cover draft evidence,
  sample-to-study linkage, empty results and HTML escaping.
- Ruff clean; 83 manifests validated; envelope schema generated.
- Site build repeated with identical bytes; writing check has zero FAIL.
- Both new recorded CLI commands and both live commands completed.
- JSON and CSV downloads returned HTTP 200 with bytes equal to generated
  artifacts. The in-app browser's download event timed out; native download
  completion was not confirmed by that instrument.
- Browser checks: 768×1024 tablet, 375×812 phone, 1280×800 desktop.
  Three landing links and two dataset drafts render; the picker changes
  the visible walkthrough. No page-level horizontal overflow in checked
  views. Desktop cards use three columns; tablet and phone use one.
  Keyboard focus has a visible 3px outline. Mobile and desktop screenshots
  were inspected.

The inherited CI liveness failure is an OSTI timeout also reported on
main. Replay tests do not establish future publisher uptime or model
answer accuracy. No paid service was used and the PR is left unmerged.

## Additional demos — October 20 follow-up

Added `rulemaking` and `outage-history`, bringing the walkthrough count to
twelve. The rulemaking demo follows a returned document number and keeps
actions, dates, docket IDs and FERC exclusions visible. The outage finder
separates releases, documentation and denominator metadata; it calculates
no outage rate. Both support recorded replay and completed live keyless
reads during verification. The landing page now highlights six workflows.

All 717 tests pass, including empty-result refusal, rulemaking dependency,
unknown-date preservation, denominator-vintage visibility and safe links.
Ruff and registry validation pass. Schema and site regenerated. Tablet,
phone and desktop browser checks cover the new picker entries and links.
No new dependency, paid service or upstream code was introduced.

## Follow-up review, 2026-10-20

The latest Claude parent-session notes and PR review threads were checked.
The catalog-kind count and microbiome follow-up findings were already fixed.
The deployed guide and reference HTML pages both returned HTTP 200; the
static local preview does not run the GitHub Pages Markdown renderer.

VIPS search returned HTTP 401, "Valid API key required". Its manifest now
records API-key access and proposed status with a named blocker; live tool
calls refuse before fetching. The site no longer advertises VIPS as an
active source. September adapter recordings remain covered by explicit
historical replay tests, with no change to the default runtime gate.
No credentials, paid services or new dependencies were added.
