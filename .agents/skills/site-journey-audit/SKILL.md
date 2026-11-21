---
name: site-journey-audit
description: Measure recorded-site journeys, useful content readiness and repeated navigation when reviewing DOE-MCP's generated site or changing its controls, downloads or page delivery.
---

# Site journey audit

Use this engineering skill for the generated site in `docs/`. Its scripts
exercise recorded content. They do not call DOE publishers or a model.

Read `uat.md` and `issues.md` first. Preserve historical measurements and
append dated corrections. Change the renderers or source content when a
generated page is wrong, then rebuild it.

## Run

Build with `python tools/build_site.py --fixtures`. Install the exact browser
tooling with `npm ci --ignore-scripts` and
`npx --no-install playwright install chromium` if it is not already present.
An existing matching Playwright installation can be supplied through
`NODE_PATH`; report its version. An installed browser can be selected with
`UAT_BROWSER_EXECUTABLE_PATH`; report that override.

Serve `docs/` with:

```sh
python -m http.server 4192 --bind 127.0.0.1 --directory docs
```

In another process, run:

```sh
UAT_URL=http://127.0.0.1:4192 npm run uat
UAT_URL=http://127.0.0.1:4192 npm run uat:journeys
```

The first check exercises all demo selectors, section jumps and selected
downloads. The second follows landing-page search through samples, evidence,
worksheets and the Daymet download pair at phone, tablet and desktop sizes.
It follows the Install, Source coverage and Local guide shortcuts too.
It also cycles all demos repeatedly, checks internal link targets and records
useful content readiness under fourfold CPU slowdown.

Results and screenshots go to ignored `uat-screenshots/`. Set
`UAT_OUTPUT_DIR` to change that destination. `UAT_STABILITY_ROUNDS` accepts
2–10; the default is 3. CI runs the same checks on pull requests and pushes
to main.

## Read the results

Use `journeys.json` for action counts and paths. Clicks come from trusted
browser events. Wheel actions count issued wheel events; scroll pixels also
include focus and anchor movement. Document navigation counts include the
initial load; same-document history events are separate. Native select changes in the stability check are scripted
state transitions, with their own count. Do not convert any of these into
assumed human taps or swipes.

Each measured journey must end at useful content or a verified download.
Adjust the explicit readiness assertion when that content changes. A page
load event, empty container or arbitrary delay is insufficient.

Compare DOM checkpoints only after returning to the same selected panel.
Inspect heap and long tasks as diagnostics; approximate Chromium heap values
alone do not establish a memory leak. Repeated state changes must preserve
the selected demo, section access and browser history behavior.

CPU slowdown is a local browser diagnostic. The harness does not emulate a
phone network. External resource requests are blocked and listed. Raw and
calculated gzip sizes describe local files, not production transfer sizes.
Report the browser, viewport, readiness condition and these limits beside
timing claims. This is a scripted benchmark, not a human usability study or
field Core Web Vitals measurement.

Inspect screenshots from major transitions and any failure. Reproduce a
suspected issue against its target state before logging it in `issues.md`.
After a documentation edit, run the repository writing checker immediately.
Keep screenshots, downloaded files and browser packages out of commits.
