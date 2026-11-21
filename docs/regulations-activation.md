# Regulations.gov activation

The integration is prepared but gated. `regulations-gov-doe` remains
proposed, `doe-docs` remains planned, and no Regulations.gov fixture ships.
The default application reads Federal Register notices and labels that
scope. An explicit `--source regulations` refuses at the source gate.

The November exploration capture made one HTTP 200 request whose response
was discarded by an overly strict local quota-header check. After that
check was corrected, one request received HTTP 429. Both attempts stopped
without retry. No complete recording was written. The missing fixture
cannot be replaced with a constructed response.

Before activation:

1. Re-read the publisher's GET API and key policies, and record their URLs
   and verdicts in the local evidence ledger. Check the applicable quota;
   do not retry a rate-limit refusal by changing keys, hosts or networks.
2. Obtain the user's authorization to configure their own api.data.gov key.
   Use the hidden credential prompt, a pipe or `--from-env`. Never place a
   credential in a client configuration, fixture, log or command argument.
   The normal credential path rejects the public exploration key.
3. Record the canonical plan through `tools/record_fixtures.py` and
   `record_through`: read docket `EERE-2025-OT-0031`, search its exact
   EERE document page, then read a document id returned by that search.
   The docket id comes from the existing Federal Register recording.
   Stop on a failed interaction and preserve the whole-file write guards.
4. Inspect the captured metadata, dates, boolean fields, filter results and
   publisher links. Add its factual metadata entry to `THIRD_PARTY_DATA.yml`.
   Do not infer a licence for linked documents or record comment bodies.
5. Add positive replay tests for all prepared operations, cache provenance,
   totals, pagination and returned agency and docket validation. Extend the
   activation sabotage matrix. Use `keyed_ctx` and isolated replay keys.
   Run the full suite immediately after recording.
6. Activate the manifest and its docket-metadata capability only after those
   checks pass. Promote the docs lineup, add its profiles and executable,
   and scope that server to `API_DATA_GOV_KEY`. Retain the Federal Register
   tools in the existing research profiles and keep those profiles within
   their tool ceilings. Every server must carry the third-party text rule.
7. Run the source validator, schema and site builders, Ruff, the full replay
   suite and the writing checker. Demonstrate the default recorded notice
   app and the explicit recorded docket app with credentials loading disabled.

The prepared scope is EERE docket and document metadata. Docket search,
other agency codes, comments and attachment retrieval require their own
recorded evidence before they are exposed.
