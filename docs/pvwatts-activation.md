# PVWatts v8 activation checklist

Status on 2026-11-21: prepared contract, proposed source, no user key and
no successful model recording. No API request, DEMO_KEY request or
credential configuration was performed. The current server and ATB app
use empty credential stores. Unit validation examples are synthetic tests,
not evidence that a live model call succeeded.

## Publisher contract

Review the publisher's [v8 API documentation](https://developer.nlr.gov/docs/solar/pvwatts/v8/),
[key policy](https://developer.nlr.gov/docs/api-key/),
[rate policy](https://developer.nlr.gov/docs/rate-limits/) and
[terms](https://developer.nlr.gov/terms/) before activation. These pages
were reviewed on 2026-11-21. A future recording must recheck them and
respect the key's shared budget.

The prepared adapter uses GET with a strict monthly subset of documented
system and coordinate parameters. It refuses arbitrary parameters, hourly
output, credentials supplied in tool inputs and file IDs. Required numeric
inputs have finite range checks. Defaults for dataset, DC/AC ratio, ground
coverage ratio and inverter efficiency become explicit submitted inputs.

Successful responses must retain input echoes for those submitted fields,
twelve finite values in each monthly series, finite annual summaries and
an annual AC total consistent with the monthly sum. Version metadata must
identify v8 and the SSC module, and weather-source metadata must be present.
Warnings remain visible. The publisher's echoed `api_key` is excluded from
runtime envelopes and caches; known credential values are redacted from
other retained response text too.

## Evidence required before changing the gate

- Obtain explicit authorization for a user-owned key and a bounded model
  recording plan. Do not use the public demonstration key as a substitute.
- Scope the key to the activated server and credential-backed adapter.
  The current lineup declares no credentials and the server constructs an
  empty store; both must change deliberately before a keyed path can work.
- Record actual publisher responses through the standard recorder,
  exercising input echoes, monthly summaries, warnings, version and weather
  metadata. Refuse empty or partial recording plans. A synthetic unit test
  cannot satisfy this requirement.
- Verify that the recording, runtime envelope, logs, errors, caches and
  client configuration contain no credential value. Preserve the existing
  source gate, redirect checks, DNS pinning, timeouts and response caps.
- Add publisher content and attribution terms to `THIRD_PARTY_DATA.yml`,
  a health probe and the activation sabotage matrix. Keep unrelated APIs
  in the broader developer-network inventory proposed.
- Only after those checks pass, update the source lifecycle and capability
  declaration. Run the full replay suite, schema and site builds, source
  validation and writing checker. Recheck the application and site status
  so they describe the actual activated scope.

Modelled typical-weather production must remain separate from observed
generation and a forecast for a named calendar year. This integration has
no POST, job submission, hourly data, billing calculation or financial
optimization route.
