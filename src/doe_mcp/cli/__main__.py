"""`doe-mcp` — doctor, configure, sources, tools.

The UX is NEPA-MCP's, which is the one shipped DOE-native precedent for this
shape: a doctor that tells you what is wrong in the order you can fix it, a
configure that writes the client config for you, and a sources group for
working on the registry itself.

`doctor` never prints a credential value. It reports presence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..adapters import ADAPTER_VERSIONS
from ..adapters.base import (HttpFetcher, close_shared_client,
                             egress_policy_for)
from ..adapters.replay import record_through
from ..core.credentials import CREDENTIAL_SPECS, Credentials, default_path
from ..core.errors import DoeMcpError
from ..core.organizations import OrganizationTable
from ..core.registry import (SourceRegistry)
from ..core.toolreg import PROFILES, expand_profile
from ..runtime import SOURCES_DIR, RuntimeContext, load_context
from ..servers.build import registries
from .configure import SERVERS, client_targets, configure

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def _load_ctx(sources_dir: Path | None = None) -> RuntimeContext:
    import doe_mcp.adapters  # noqa: F401  (registers adapter params)
    return load_context(sources_dir)


def _fail(msg: str) -> int:
    print(f"[{BAD}] {msg}", file=sys.stderr)
    return 1


def _run(coro):
    """`asyncio.run` with the shared HTTP client closed at the end, so no
    connection pool outlives the loop a CLI command ran on."""
    async def wrapped():
        try:
            return await coro
        finally:
            await close_shared_client()
    return asyncio.run(wrapped())


# --- doctor ---------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    problems = 0
    print(f"doe-mcp {__version__}")
    print(f"python  {sys.version.split()[0]}")
    print()

    print("registry")
    try:
        ctx = _load_ctx()
    except Exception as err:                       # noqa: BLE001
        print(f"[{BAD}] registry failed to load: {err}")
        print("\nThis is fatal: the servers load the same registry at "
              "startup, so they will not run either.")
        return 1
    manifests = list(ctx.sources.manifests.values())
    active = [m for m in manifests if m.is_active()]
    print(f"[{OK}] {len(manifests)} source manifests, {len(active)} active")
    print(f"[{OK}] {len(ctx.organizations.orgs)} organizations, "
          f"{len(ctx.organizations.labs())} national laboratories")
    print(f"[{OK}] {len(ctx.catalog.all())} sub-MCP catalog entries")
    print(f"[{OK}] registry revision {ctx.sources.revision}")
    if len(ctx.organizations.labs()) != 17:
        print(f"[{WARN}] expected 17 national laboratories, found "
              f"{len(ctx.organizations.labs())}")
        problems += 1
    print()

    print("adapters")
    for name, version in sorted(ADAPTER_VERSIONS.items()):
        used = sum(1 for m in manifests if m.adapter.type == name)
        print(f"[{OK}] {name} v{version} ({used} manifests)")
    print()

    print("servers")
    for profile in sorted(PROFILES):
        try:
            specs = expand_profile(profile, registries())
        except ValueError as err:
            print(f"[{BAD}] {profile}: {err}")
            problems += 1
            continue
        print(f"[{OK}] {profile}: {len(specs)} tools")
    print()

    print("credentials")
    creds = ctx.credentials
    print(f"       file: {creds.path}"
          + ("" if creds.file_exists else "  (not created yet)"))
    if creds.insecure_mode:
        print(f"[{WARN}] the credentials file is readable by group or other. "
              f"Run: chmod 600 {creds.path}")
        problems += 1
    for name, spec in sorted(CREDENTIAL_SPECS.items()):
        if creds.has(name):
            # Presence only. This tool has no code path that prints a value.
            state = "demo key" if creds.is_demo(name) else "set"
            mark = WARN if creds.is_demo(name) else OK
            print(f"[{mark}] {name}: {state}")
        else:
            print(f"[{WARN}] {name}: not set — {spec['label']}; free at "
                  f"{spec['register_url']}")
    keyless = [m.id for m in active if not m.access.credential_ref]
    print(f"       {len(keyless)} active sources need no credential at all")
    print()

    print("clients")
    for key, target in sorted(client_targets().items()):
        if target.path.exists():
            doc = json.loads(target.path.read_text() or "{}")
            node: Any = doc
            for part in target.key_path:
                node = node.get(part, {}) if isinstance(node, dict) else {}
            ours = sorted(k for k in node if k.startswith("doe-"))
            if ours:
                print(f"[{OK}] {target.name}: {', '.join(ours)}")
            else:
                print(f"[{WARN}] {target.name}: config found, no DOE-MCP "
                      f"servers registered. Run: doe-mcp configure {key}")
        else:
            print(f"       {target.name}: no config at {target.path}")
    print()

    if args.online:
        problems += _doctor_online(ctx)

    if problems:
        print(f"{problems} thing(s) to look at above.")
    else:
        print("Everything checks out. Run `doe-mcp configure claude-code` "
              "to register the servers.")
    return 0


def _doctor_online(ctx: RuntimeContext) -> int:
    """Live reachability for the keyless active sources. Off by default:
    doctor should work on a plane, and a network failure is not an install
    problem."""
    print("live probes (keyless active sources)")
    problems = 0
    for manifest in sorted((m for m in ctx.sources.manifests.values()
                            if m.is_active() and not m.access.credential_ref),
                           key=lambda m: m.id):
        try:
            count = _run(_probe(ctx, manifest.id))
        except DoeMcpError as err:
            print(f"[{WARN}] {manifest.id}: {err}")
            problems += 1
            continue
        except Exception as err:                   # noqa: BLE001
            print(f"[{WARN}] {manifest.id}: {err.__class__.__name__}: {err}")
            problems += 1
            continue
        floor = manifest.health.expect.get("min_records")
        if floor is not None and count is not None and count < floor:
            print(f"[{WARN}] {manifest.id}: {count} records, under the "
                  f"manifest's floor of {floor}")
            problems += 1
        else:
            detail = f"{count} records" if count is not None else "reachable"
            print(f"[{OK}] {manifest.id}: {detail}")
    print()
    return problems


# Oak Ridge, on the Daymet grid and inside every version of it. A probe
# needs a point the service will answer for; picking one at request time
# would make the probe depend on the caller.
PROBE_LATITUDE, PROBE_LONGITUDE = 35.9313, -84.3104


async def _probe(ctx: RuntimeContext, source_id: str) -> int | None:
    manifest = ctx.sources.get(source_id)
    assert manifest is not None
    kind = manifest.adapter.type
    if kind == "osti_family":
        page = (await ctx.osti.search(manifest, filters={}, rows=1)).value
        return page.total_matches
    if kind == "opendatasoft":
        page = (await ctx.opendatasoft.search_datasets(manifest,
                                                       limit=1)).value
        return page.total_count
    if kind == "json_document":
        result = (await ctx.json_document.search(manifest, limit=1)).value
        return result.document_total
    if kind == "curated":
        return len(ctx.curated.read(manifest).value.entries)
    if kind == "fueleconomy":
        return len((await ctx.fueleconomy.menu(manifest, "year")).value)
    if kind == "text_feed":
        # Intervals that actually carry values. The trailing run of empty
        # ones is the rest of today, so counting rows in the file would make
        # the feed look healthy at 03:00 on a morning it had stopped
        # publishing.
        return len((await ctx.text_feed.read_feed(manifest)).value.rows)
    if kind == "vips":
        page = (await ctx.vips.search(manifest, rows=1)).value
        return page.total
    if kind == "federal_register":
        # The matching count, which the API caps at 10,000. A floor is a
        # perfectly good liveness signal; the manifest's floor is far below
        # the cap.
        page = (await ctx.federal_register.search(manifest, rows=2)).value
        return page.count
    if kind == "postgrest":
        # The matching total rather than the rows, so the floor in the
        # manifest is checked against the size of the table and a probe
        # costs one small request.
        page = (await ctx.postgrest.query(manifest, rows=1)).value
        return page.total
    if kind == "eia_v2":
        # Keyed, so never in the keyless probe set; reachable through
        # `sources sample` and `sources probe <id>` once a key is configured.
        node = (await ctx.eia.describe_route(manifest)).value
        return len(node.children)
    if kind == "daymet":
        # A fixed recent window at a fixed point, so a probe is one small
        # request and its record count is stable rather than a function of
        # today's date.
        series = (await ctx.daymet.point_series(
            manifest, latitude=PROBE_LATITUDE, longitude=PROBE_LONGITUDE,
            variables=["tmax"], start="2023-06-01",
            end="2023-06-05")).value
        return len(series.rows)
    if kind == "essdive":
        # The matching total rather than the rows: the floor in the manifest
        # is a statement about the repository's size, and a page of ten would
        # not check it.
        page = (await ctx.essdive.search(manifest, rows=1)).value
        return page.total
    if kind == "esgf":
        schema = (await ctx.esgf.describe(manifest)).value
        page = (await ctx.esgf.search(manifest, schema=schema,
                                      filters={"project": "CMIP6"},
                                      rows=1)).value
        return page.num_found
    if kind == "sage":
        page = (await ctx.sage.nodes(manifest, rows=1)).value
        return page.total_nodes
    if kind == "optimade":
        # The provider's own count of what it holds, which is what the
        # floor in the manifest is a statement about. One unfiltered page
        # of one entry costs a small request and carries the total.
        schema = (await ctx.optimade.describe(manifest)).value
        page = (await ctx.optimade.search(manifest, schema=schema,
                                          rows=1)).value
        return page.data_available
    if kind == "basis_sets":
        return (await ctx.basis_sets.catalog(manifest)).value.total
    if kind == "self_registry":
        return len(ctx.sources.manifests)
    return None


# --- sources --------------------------------------------------------------

def cmd_sources_validate(args: argparse.Namespace) -> int:
    import doe_mcp.adapters  # noqa: F401
    root = Path(args.sources) if args.sources else SOURCES_DIR
    try:
        orgs = OrganizationTable.load(root / "organizations.yaml")
        registry = SourceRegistry.load(root, orgs)
    except Exception as err:                       # noqa: BLE001
        return _fail(str(err))
    print(f"{len(registry.manifests)} manifests valid "
          f"({sum(1 for m in registry.manifests.values() if m.is_active())} "
          f"active). Revision {registry.revision}.")
    return 0


def cmd_sources_stats(args: argparse.Namespace) -> int:
    """Coverage debt, printed rather than claimed. The point is that a
    registry with many more rows than active sources says so out loud."""
    ctx = _load_ctx()
    manifests = list(ctx.sources.manifests.values())
    by_state: dict[str, int] = {}
    by_domain: dict[str, dict[str, int]] = {}
    for m in manifests:
        state = m.lifecycle.declared_state.value
        by_state[state] = by_state.get(state, 0) + 1
        row = by_domain.setdefault(m.domain, {"total": 0, "active": 0})
        row["total"] += 1
        row["active"] += 1 if m.is_active() else 0

    print(f"{len(manifests)} source manifests")
    for state in sorted(by_state):
        print(f"  {state:10s} {by_state[state]}")
    print()
    print(f"{'domain':16s} {'total':>6s} {'active':>7s}")
    for domain in sorted(by_domain):
        row = by_domain[domain]
        print(f"{domain:16s} {row['total']:6d} {row['active']:7d}")
    print()

    covered = {c for m in manifests if m.is_active()
               for c in m.capability_ids()}
    uncovered = sorted(ctx.sources.capability_vocab - covered)
    print(f"capabilities: {len(covered)} served, {len(uncovered)} declared "
          f"but unserved")
    for cap in uncovered:
        waiting = [m.id for m in manifests if cap in m.planned_capabilities]
        note = f"  <- {', '.join(waiting[:3])}" if waiting else ""
        print(f"  {cap}{note}")
    print()

    labs = ctx.organizations.labs()
    bare = [o.id for o in labs if not ctx.sources.for_lab(o.id)]
    print(f"lab crosswalk: {len(labs) - len(bare)}/{len(labs)} laboratories "
          "have at least one registered source")
    if bare:
        print(f"  no sources: {', '.join(bare)}")
    return 0


def cmd_sources_probe(args: argparse.Namespace) -> int:
    ctx = _load_ctx()
    ids = ([args.source_id] if args.source_id
           else sorted(m.id for m in ctx.sources.manifests.values()
                       if m.is_active() and not m.access.credential_ref))
    failures = 0
    for source_id in ids:
        try:
            count = _run(_probe(ctx, source_id))
            print(f"[{OK}] {source_id}: "
                  + (f"{count} records" if count is not None else "reachable"))
        except Exception as err:                   # noqa: BLE001
            print(f"[{WARN}] {source_id}: {err}")
            failures += 1
    return 1 if failures else 0


def cmd_sources_sample(args: argparse.Namespace) -> int:
    """Call a source for real and print (or record) what came back.

    `--record` writes a fixture. Recording rather than hand-writing is the
    whole point: a hand-written fixture agrees with the code that consumes it
    by construction, which makes it useless as a check on either.
    """
    ctx = _load_ctx()
    manifest = ctx.sources.get(args.source_id)
    if manifest is None:
        return _fail(f"no source {args.source_id!r} in the registry")
    if not ctx.sources.selectable(manifest):
        return _fail(f"{args.source_id} is {manifest.lifecycle.declared_state.value} "
                     f"({ctx.sources.selection_block(manifest)}), not "
                     f"queryable: {manifest.lifecycle.blocked_reason}")

    recorder = None
    if args.record:
        base = getattr(manifest.adapter, "base_url", None) or \
            getattr(manifest.adapter, "document_url", "")
        # One factory for every adapter, credentials redacted at record time.
        recorder = record_through(
            ctx, HttpFetcher(policy=egress_policy_for(manifest, base)))

    try:
        count = _run(_probe(ctx, args.source_id))
    except Exception as err:                       # noqa: BLE001
        return _fail(f"{args.source_id}: {err}")
    print(f"{args.source_id}: {count if count is not None else 'reachable'}")

    if recorder is not None:
        out = Path(args.out or f"tests/fixtures/{args.source_id}.json")
        recorder.write(out, note=(
            f"Recorded from {manifest.name} by `doe-mcp sources sample "
            f"--record`. Publisher content; see THIRD_PARTY_DATA.yml."))
        print(f"recorded {len(recorder.interactions)} interaction(s) -> {out}")
    return 0


# --- configure ------------------------------------------------------------

def cmd_configure(args: argparse.Namespace) -> int:
    if args.client == "credentials":
        if args.servers:
            # Almost always the key itself, typed after --set. Say so without
            # repeating the value: it is already in the shell history, and
            # echoing it puts it in the scrollback too.
            target = args.set or "NAME"
            return _fail(
                "the credential value does not go on the command line — it "
                "would be written into your shell history. Use one of:\n"
                f"    pbpaste | doe-mcp configure credentials --set {target}"
                "        (from the clipboard)\n"
                f"    doe-mcp configure credentials --set {target} "
                "< keyfile      (from a file)\n"
                f"    doe-mcp configure credentials --set {target}"
                "                (hidden prompt)\n"
                "The value you just typed is in your shell history now. "
                "Clear that entry, and regenerate the key if it may have "
                "been seen.")
        return _configure_credentials(args)
    unknown = [s for s in args.servers if s not in SERVERS]
    if unknown:
        return _fail(f"unknown server(s) {sorted(unknown)}; known: "
                     f"{sorted(SERVERS)}")
    servers = sorted(SERVERS) if args.all else (args.servers or ["research"])
    path, doc = configure(args.client, servers, dry_run=args.dry_run)
    node: Any = doc
    for part in client_targets()[args.client].key_path:
        node = node[part]
    print(("would write" if args.dry_run else "wrote") + f" {path}")
    for server in servers:
        print(f"  doe-{server}: {SERVERS[server]['description']}")
        needs = SERVERS[server]["needs_credentials"]
        if needs:
            print(f"    needs {', '.join(needs)} — run `doe-mcp configure "
                  "credentials`; do NOT put keys in this file")
        else:
            print("    keyless")
    if not args.dry_run:
        print("\nRestart the client to pick up the change.")
    return 0


def _configure_credentials(args: argparse.Namespace) -> int:
    path = default_path()
    if not args.set:
        creds = Credentials.load()
        print(f"credentials file: {path}")
        print("\nknown credentials:")
        for name, spec in sorted(CREDENTIAL_SPECS.items()):
            state = ("set" if creds.has(name) else "not set")
            if creds.is_demo(name):
                state = "DEMO_KEY (shared budget)"
            print(f"  {name:20s} {state}")
            print(f"    {spec['label']} — {spec['register_url']}")
            print(f"    {spec['note']}")
        print("\nSet one with: doe-mcp configure credentials --set NAME")
        print("Keys go here, never in an MCP client's config file.")
        return 0

    name = args.set
    if name not in CREDENTIAL_SPECS:
        return _fail(f"unknown credential {name!r}; known: "
                     f"{sorted(CREDENTIAL_SPECS)}")
    spec = CREDENTIAL_SPECS[name]
    try:
        value, route = _credential_value(name, spec, args)
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled")
        return 1
    if not value:
        return _fail("no value entered")

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        lines = [ln for ln in path.read_text().splitlines()
                 if not ln.strip().startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    print(f"\nwrote {name} to {path} (mode 600), read from {route}. The "
          "value is not echoed and is never written into a client config.")
    return 0


def _credential_value(name: str, spec: dict,
                      args: argparse.Namespace) -> tuple[str, str]:
    """The credential, by whichever route avoids the shell history.

    Three, in the order they are tried. An environment variable already in
    the environment; a value piped in, which is what makes
    `pbpaste | doe-mcp configure credentials --set NAME` work and is the
    least effortful correct route on a machine where the key is on the
    clipboard; and otherwise a hidden prompt.

    What is deliberately NOT here is a command-line argument. That is the
    one route that writes the key into shell history, and decision 0021 is
    the record of it being tried in real use and echoed back by an error
    message. Making the correct routes easy is the other half of that fix:
    an instruction that only says "do not do it that way" leaves the person
    doing it that way.
    """
    if args.from_env:
        value = os.environ.get(args.from_env, "").strip()
        if not value:
            raise SystemExit(_fail(
                f"environment variable {args.from_env} is unset or empty"))
        return value, f"${args.from_env}"
    if not sys.stdin.isatty():
        return sys.stdin.read().strip(), "standard input"
    import getpass
    print(f"{spec['label']}")
    print(f"Register (free): {spec['register_url']}")
    print(spec["note"])
    print("\nPaste the value at the prompt — it is not echoed and does not "
          "enter your shell history.")
    print(f"Or pipe it in:  pbpaste | doe-mcp configure credentials "
          f"--set {name}")
    return getpass.getpass(f"\n{name}: ").strip(), "the prompt"


# --- tools ----------------------------------------------------------------

def cmd_tools_list(args: argparse.Namespace) -> int:
    specs = expand_profile(args.profile, registries())
    for spec in specs:
        print(f"{spec.name}  [{spec.toolset}] v{spec.contract_version}")
        if args.verbose:
            print(f"    {' '.join(spec.description.split())}\n")
    print(f"\n{len(specs)} tools in profile {args.profile!r}")
    return 0


def cmd_tools_call(args: argparse.Namespace) -> int:
    ctx = _load_ctx()
    all_regs = registries()
    target_profile = args.profile

    if target_profile is None:
        # Auto-detect profile: first check research:all, then check each server's :all profile.
        candidate_profiles = ["research:all", "energy:all", "earth:all", "materials:all"]
        for cand in candidate_profiles:
            specs = {s.name: s for s in expand_profile(cand, all_regs)}
            if args.tool in specs:
                target_profile = cand
                break
        if target_profile is None:
            # Fall back to checking any profile in PROFILES
            for cand in sorted(PROFILES):
                specs = {s.name: s for s in expand_profile(cand, all_regs)}
                if args.tool in specs:
                    target_profile = cand
                    break
        if target_profile is None:
            target_profile = "research:all"

    specs = {s.name: s for s in expand_profile(target_profile, all_regs)}
    spec = specs.get(args.tool)
    if spec is None:
        return _fail(f"no tool {args.tool!r} in profile {target_profile!r}; "
                     f"available: {sorted(specs)}")
    kwargs = json.loads(args.args) if args.args else {}
    try:
        envelope = _run(spec.fn(ctx, **kwargs))
    except DoeMcpError as err:
        return _fail(err.model_message())
    print(json.dumps(envelope.model_dump(mode="json"), indent=2))
    return 0


# --- serve ----------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from ..servers.build import build_server
    ctx = _load_ctx()
    build_server(ctx, args.profile).run()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="doe-mcp",
        description="MCP servers over public DOE and national-laboratory "
                    "data. Independent project; not affiliated with the US "
                    "Department of Energy.")
    ap.add_argument("--version", action="version",
                    version=f"doe-mcp {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check the install, registry, "
                                      "credentials, and client configs")
    d.add_argument("--online", action="store_true",
                   help="also probe the keyless active sources live")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("configure", help="register servers with an MCP "
                                         "client, or set a credential")
    c.add_argument("client", choices=sorted(client_targets()) + ["credentials"])
    # `choices` is not used here even though the values are constrained,
    # because argparse rejects an unknown positional by printing it back in
    # the error. On `configure credentials --set EIA_API_KEY <key>` that
    # would echo the caller's API key into the terminal and their shell
    # history, which is the exact thing decision 0012 exists to prevent. The
    # value is validated in `cmd_configure` instead, where the message can
    # say what went wrong without quoting it.
    c.add_argument("servers", nargs="*",
                   help="which servers to register (default: research); "
                        "not used with the 'credentials' target")
    c.add_argument("--all", action="store_true", help="register every server")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--set", metavar="NAME",
                   help="with the 'credentials' target: set this credential. "
                        "The VALUE is read from a pipe, from --from-env, or "
                        "from a hidden prompt — never from the command line")
    c.add_argument("--from-env", metavar="VAR",
                   help="read the credential value from this environment "
                        "variable instead of prompting")
    c.set_defaults(func=cmd_configure)

    s = sub.add_parser("sources", help="work on the source registry")
    ssub = s.add_subparsers(dest="sources_command", required=True)
    sv = ssub.add_parser("validate", help="validate every manifest")
    sv.add_argument("--sources", help="registry directory to validate")
    sv.set_defaults(func=cmd_sources_validate)
    sst = ssub.add_parser("stats", help="coverage debt: what is registered, "
                                        "what is active, what is not served")
    sst.set_defaults(func=cmd_sources_stats)
    sp = ssub.add_parser("probe", help="live-check active keyless sources")
    sp.add_argument("source_id", nargs="?")
    sp.set_defaults(func=cmd_sources_probe)
    sa = ssub.add_parser("sample", help="call one source and show the result")
    sa.add_argument("source_id")
    sa.add_argument("--record", action="store_true",
                    help="write a replay fixture from the live response")
    sa.add_argument("--out", help="fixture path")
    sa.set_defaults(func=cmd_sources_sample)

    t = sub.add_parser("tools", help="list or call tools")
    tsub = t.add_subparsers(dest="tools_command", required=True)
    tl = tsub.add_parser("list")
    tl.add_argument("--profile", default="research:default",
                    choices=sorted(PROFILES))
    tl.add_argument("-v", "--verbose", action="store_true")
    tl.set_defaults(func=cmd_tools_list)
    tc = tsub.add_parser("call")
    tc.add_argument("tool")
    tc.add_argument("--args", help="JSON object of arguments")
    tc.add_argument("--profile", default=None,
                    choices=sorted(PROFILES))
    tc.set_defaults(func=cmd_tools_call)

    sv2 = sub.add_parser("serve", help="run an MCP server on stdio")
    sv2.add_argument("--profile", default="research:default",
                     choices=sorted(PROFILES))
    sv2.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
