# Profile preflight

## Run a check

Inspect an installed profile without opening a credential store or MCP client
configuration:

```sh
doe-mcp doctor --profile energy-tech:default --json
doe-mcp doctor --profile energy:default --json
```

`--profile` selects this offline preflight. `--json` returns a JSON object and
defaults to `research:default` when no profile is supplied. Omit `--json` for a
short text report. The existing `doe-mcp doctor` command still checks credentials
and client configuration; `--online` cannot be combined with preflight options.

Preflight reads the installed distribution metadata and packaged registries. It
checks the CLI and selected server entry point targets, dependency versions and
imports, required extras, profile tool registration, and source manifest gates.
It never constructs adapters or a server, reads key environment values, prompts,
or sends a network request. Dependency versions come from the installed wheel's
exact pins. Unsupported dependency metadata fails the check instead of being
skipped.

## Read the report

The JSON contract has `schema_version: 1` and these fields:

| Field | Meaning |
| --- | --- |
| `profile`, `server` | Requested profile and its shipping server, including the `energy` to `doe-energy-data` mapping. |
| `entry_points` | Expected CLI and server commands, targets and local import status. No server function is called. |
| `required_extras` | Extras the selected server requires. Energy technology requires `energy-tech`; research works with the base install. |
| `dependencies` | Required and installed versions, with `ready`, `missing`, `version_mismatch` or `import_failed` status. |
| `registered_tools` | Ordered tool names exposed by the selected profile, including any deprecated aliases. Registration does not activate a source. |
| `sources` | Registry-wide source states, automation status, selection blocks, manifest reasons and credential requirements. `server_domain` marks sources in the selected server's domain. |
| `credentials` | Declared key requirements with `status: not_checked`. Keys can be required by active or proposed sources; preflight never infers whether a user has them. |
| `readiness` | Local installation status; credentials, network and server startup are always `not_checked`. |
| `problems` | Installation failures with stable codes and repair messages. |

Source gates cover the whole registry because shared registry and discovery tools
can describe sources across domains. They are an inventory, not an inferred list
of sources each registered tool can query. An active source's network status is
also `not_checked`.

Each source includes its `access_mode`, a boolean `credential_required`, and its
nullable `credential_ref`. A proposed keyed source can require credentials before
it has a configured credential name; its status is still `not_checked`.

## Exit codes and source gates

Exit codes are `0` for a ready local installation, `1` for a broken installation
or missing required extra, and `2` for invalid arguments. A required key whose
presence was not checked does not fail preflight. A proposed or restricted source
also does not fail the installation check: its existing gate and reason remain
in the report. PVWatts therefore stays proposed even when its tool is registered
and the energy technology dependencies are installed.

A base install reports missing `pyarrow` and exits `1` for an energy technology
profile. Install the required extra with `pip install 'doe-mcp[energy-tech]'`.
The same base installation can pass the research and energy profiles. Passing
preflight verifies local prerequisites; it makes no claim that a live keyed
request will succeed.

## Test installed wheels

To verify a built wheel in fresh base and energy technology environments:

```sh
python -m pip wheel --no-deps --wheel-dir /tmp/doe-wheel .
python tools/check_preflight_wheel.py /tmp/doe-wheel/doe_mcp-0.1.0.dev0-py3-none-any.whl
```

The smoke harness installs dependencies using pip, then runs both the console
entry point and an intercepted CLI outside the checkout. It checks that runtime
loading, credential and client discovery, prompts, key environment reads and
network calls are never attempted during preflight.
