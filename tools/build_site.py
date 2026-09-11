#!/usr/bin/env python3
"""Generate the GitHub Pages site from the source registry.

Two rules, both from decision 0017:

1. **The site is generated from the registry the servers actually run on.**
   Every count on the page is derived, not typed, so the page cannot claim
   coverage the code does not have. A CI test fails the build when
   docs/data/site.json and the registry disagree.
2. **Generated data is committed to main.** Static, no build framework. The
   named upgrade path — a `github-pages` deploy branch — is what to reach for
   when generated-HTML diffs start hurting review, and not before.

The worked example is captured from a REAL tool call at build time
(`--fixtures`), replayed from recorded responses so the build is offline and
deterministic. An invented example on a provenance project would be a
particularly bad joke.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from doe_mcp import __version__  # noqa: E402
from doe_mcp.adapters import ADAPTER_CLASSES  # noqa: E402
from doe_mcp.adapters.base import TTLCache  # noqa: E402
from doe_mcp.adapters.replay import ReplayFetcher  # noqa: E402
from doe_mcp.core.credentials import Credentials  # noqa: E402
from doe_mcp.core.organizations import OrganizationTable  # noqa: E402
from doe_mcp.core.registry import SourceRegistry  # noqa: E402
from doe_mcp.core.catalog import SubMcpCatalog  # noqa: E402
from doe_mcp.core.envelope import ExecutionCoverage, ResultCoverage  # noqa: E402
from doe_mcp.core.toolreg import PROFILES, expand_profile  # noqa: E402
from doe_mcp.runtime import load_context  # noqa: E402
from doe_mcp.servers.build import build_server, registries  # noqa: E402
from doe_mcp.servers.lineup import SERVER_LINEUP, shipping  # noqa: E402

DOCS = ROOT / "docs"
SOURCES = ROOT / "sources"
FIXTURES = ROOT / "tests" / "fixtures"
README = ROOT / "README.md"
REFERENCE = DOCS / "reference.md"
STATUS_BEGIN = "<!-- status:begin -->"
STATUS_END = "<!-- status:end -->"

# Question -> tool. Written by hand because it is the one part of the page
# that has to be phrased the way a person would ask, not the way the registry
# describes itself.
QUESTIONS = [
    ("What DOE-funded work exists on perovskite tandem cells?",
     "research.search_literature", "doe-research"),
    ("Where is the dataset behind this paper, and what is its DOI?",
     "research.search_datasets", "doe-research"),
    ("Has a national laboratory released Python code for lattice QCD?",
     "research.search_software", "doe-research"),
    ("Can I actually read the full text of this report?",
     "research.get_record", "doe-research"),
    ("What does Oak Ridge publish?",
     "registry.lab_crosswalk", "doe-research"),
    ("I have an old NREL URL that 404s. What happened?",
     "registry.resolve_org", "doe-research"),
    ("Which DOE catalog holds the EAGLE-I outage data?",
     "discovery.search_all_catalogs", "doe-research"),
    ("What data does DOE itself call durable?",
     "discovery.list_pure_resources", "doe-research"),
    ("Does DOE-MCP cover geothermal, and if not, why not?",
     "registry.search_sources", "doe-research"),
    ("How much electricity did CAISO demand last night?",
     "energy.grid_status", "doe-energy-data"),
    ("What does this car actually get?",
     "fuel.find_vehicle", "doe-energy-data"),
    ("Which wind turbines and solar farms are in this county?",
     "facility.find_wind_turbines", "doe-energy-data"),
    ("What was the weather at these coordinates every day last summer?",
     "earth.get_daymet_point", "doe-earth"),
    ("What data came out of the NGEE Arctic campaign?",
     "earth.search_datasets", "doe-earth"),
    ("Which climate models ran this experiment for surface temperature?",
     "climate.search_cmip", "doe-earth"),
    ("Where are the air-quality sensors near this city?",
     "sensors.find_nodes", "doe-earth"),
    ("Which computed structures contain gallium and nitrogen?",
     "materials.search_structures", "doe-materials"),
    ("Which basis sets cover uranium, and what do they look like in NWChem?",
     "chemistry.search_basis_sets", "doe-materials"),
    ("What is BPA's wind output right now?",
     "grid.get_bpa_operations", "doe-energy-data"),
    ("What has DOE proposed on appliance standards this year?",
     "docs.search_rulemakings", "doe-research"),
    ("What can I license from Oak Ridge?",
     "tech.find_licensable_ip", "doe-research"),
]

# (name, default profile, status, description), read from the one lineup
# table the CLI and the server assembly also read.
SERVERS = [(sv.name, sv.default_profile, sv.status, sv.description)
           for sv in SERVER_LINEUP]


def replay_context():
    """Every adapter with a fetcher seam replays the recorded responses, and
    no credential is read: the build never touches the network or the
    developer's keys. The first version of this wired three adapters by
    hand, which is why the page carried examples from one server only."""
    merged: dict = {}
    for path in sorted(FIXTURES.glob("*.json")):
        merged.update(ReplayFetcher.from_file(path).interactions)
    fetcher = ReplayFetcher(interactions=merged)
    cache = TTLCache()
    creds = Credentials(values={}, path=Path("/nonexistent"),
                        file_exists=False)
    adapters = {}
    for kind, (field_name, cls) in ADAPTER_CLASSES.items():
        if kind == "curated":
            continue
        kwargs = {"fetcher": fetcher, "cache": cache}
        if kind == "eia_v2":
            kwargs["credentials"] = creds
        adapters[field_name] = cls(**kwargs)
    return load_context(SOURCES, credentials=creds, **adapters)


async def worked_examples(ctx) -> list[dict]:
    """Real envelopes from real recorded responses."""
    from doe_mcp.domains import (discovery, earth, energy, materials,
                                 registry_tools, research)
    out = []
    # One captured answer per shipping server after the first three, so the
    # page shows each server's envelope and not only the research one.
    plans = [
        ("research.search_literature", "doe-research",
         "Find DOE-funded work on perovskite solar cells, across both "
         "collections at once.",
         research.search_literature(ctx, query="perovskite solar", rows=3)),
        ("registry.resolve_org", "doe-research",
         "A user says “NREL”. The laboratory was renamed on "
         "2025-12-01 and every old domain went dark with no redirect.",
         registry_tools.resolve_org(ctx, "NREL")),
        ("discovery.search_all_catalogs", "doe-research",
         "Four DOE catalogs at once, each labelled with its own vintage.",
         discovery.search_all_catalogs(ctx, query="geothermal",
                                       per_catalog=2)),
        ("facility.find_wind_turbines", "doe-energy-data",
         "Every wind turbine the USGS/LBNL inventory places in Rhode "
         "Island, five to a page, with the publisher's own count and the "
         "confidence codes on each row.",
         energy.find_wind_turbines(ctx, state="RI", rows=5)),
        ("earth.get_daymet_point", "doe-earth",
         "Five days of modelled surface weather at Oak Ridge, from the "
         "single-pixel service, with the returned range reported beside "
         "the requested one.",
         earth.get_daymet_point(ctx, 35.9313, -84.3104, "2023-06-01",
                                "2023-06-05", variables="prcp,tmax,tmin")),
        ("materials.search_structures", "doe-materials",
         "Computed structures containing gallium and nitrogen from the "
         "Materials Project's OPTIMADE endpoint, marked as computed rather "
         "than measured.",
         materials.search_structures(ctx, elements="Ga,N", rows=5)),
    ]
    for tool, server, why, coro in plans:
        try:
            env = await coro
        except Exception as err:                   # noqa: BLE001
            raise SystemExit(
                f"worked example {tool} raised: {err}\n"
                "The site's annotated example is this project's central "
                "claim. Fix the call or re-record the fixture; do not "
                "publish the page without it.") from err
        # A failed envelope is a valid envelope and a terrible advertisement.
        # This guard exists because the first build shipped one: the example
        # asked for rows=2 while the fixture held rows=3, and the flagship
        # demonstration on a provenance project rendered as two source
        # failures. Loud is the only acceptable behaviour here.
        if env.coverage.execution is not ExecutionCoverage.complete:
            raise SystemExit(
                f"worked example {tool} came back with "
                f"execution={env.coverage.execution.value} and "
                f"{len(env.coverage.source_failures)} source failure(s). "
                "That usually means the recorded fixture does not cover this "
                "call's parameters. Re-record with "
                "`python tools/record_fixtures.py`.")
        if env.coverage.result is ResultCoverage.empty:
            raise SystemExit(
                f"worked example {tool} returned an empty result. An empty "
                "answer is a legitimate response and a poor demonstration; "
                "pick a query the fixtures actually cover.")
        out.append({"tool": tool, "server": server, "why": why,
                    "envelope": _frozen(env.model_dump(mode="json"),
                                        ctx.sources.revision)})
        print(f"  + worked example: {tool}")
    return out


# The two fields a replay cannot make meaningful. `retrieved_at` on a replayed
# fixture is the moment the build ran, and the request id is random; both
# changed on every build, so the CI check that the committed site matches a
# fresh build could never pass. They are pinned to the registry revision date
# and a fixed id, and the page says so beside the example.
FROZEN_REQUEST_ID = "worked-example"


def _frozen(node, revision: str):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "retrieved_at" and isinstance(value, str):
                out[key] = f"{revision}T00:00:00Z"
            elif key == "request_id" and isinstance(value, str):
                out[key] = FROZEN_REQUEST_ID
            else:
                out[key] = _frozen(value, revision)
        return out
    if isinstance(node, list):
        return [_frozen(v, revision) for v in node]
    return node


def build_data(with_fixtures: bool) -> dict:
    orgs = OrganizationTable.load(SOURCES / "organizations.yaml")
    registry = SourceRegistry.load(SOURCES, orgs)
    catalog = SubMcpCatalog.load(SOURCES / "catalog")
    manifests = list(registry.manifests.values())
    active = [m for m in manifests if m.is_active()]

    tools_by_server: dict[str, list[dict]] = {}
    for name, profile, _, _ in SERVERS:
        if profile and profile in PROFILES:
            tools_by_server[name] = [
                {"name": s.name, "toolset": s.toolset,
                 "description": s.description}
                for s in expand_profile(profile, registries())]

    sources = []
    for m in sorted(manifests, key=lambda m: (not m.is_active(), m.id)):
        sources.append({
            "id": m.id, "name": m.name, "domain": m.domain,
            "state": m.lifecycle.declared_state.value,
            "steward": orgs.get(m.publisher.steward).name
            if orgs.get(m.publisher.steward) else m.publisher.steward,
            "funder": (orgs.get(m.publisher.funder).name
                       if m.publisher.funder and orgs.get(m.publisher.funder)
                       else None),
            "host": (orgs.get(m.publisher.host).name
                     if m.publisher.host and orgs.get(m.publisher.host)
                     else None),
            "authority": m.publisher.authority_level.value,
            "automation_status": m.access.automation_status.value,
            "tier": m.access.tier.value,
            "adapter": m.adapter.type,
            "fixture": (FIXTURES / f"{m.id}.json").exists(),
            "scope": m.coverage.scope,
            "record_count": m.coverage.record_count,
            "capabilities": sorted(m.capability_ids()),
            "planned_capabilities": sorted(m.planned_capabilities),
            "blocked_reason": m.lifecycle.blocked_reason,
            "labs": m.labs,
            "terms_url": m.access.terms_url,
        })

    # Laboratories, program offices and power marketing administrations,
    # not just the seventeen labs. An office appears on a source as its
    # steward or its funder rather than in the `labs` list, so the lookup
    # differs by kind — and leaving the offices out made the department look
    # like a federation of laboratories with nothing above them.
    KINDS = [("national_lab", "National laboratories"),
             ("program_office", "Program offices"),
             ("power_marketing_administration",
              "Power marketing administrations"),
             ("headquarters", "Headquarters")]
    crosswalk = []
    for kind, label in KINDS:
        for org in sorted((o for o in orgs.orgs.values()
                           if o.kind.value == kind), key=lambda o: o.id):
            rows = sorted(
                {m.id: m for m in registry.for_lab(org.id)}
                | {m.id: m for m in registry.manifests.values()
                   if org.id in (m.publisher.steward, m.publisher.funder,
                                 m.publisher.host)},
                key=str)
            found = [registry.manifests[i] for i in rows]
            crosswalk.append({
                "id": org.id, "name": org.name, "kind": kind,
                "kind_label": label,
                "former_names": org.aliases.former_names,
                "renamed_on": org.renamed_on,
                "note": org.note,
                "sources": [{"id": m.id, "name": m.name,
                             "state": m.lifecycle.declared_state.value,
                             "domain": m.domain} for m in found],
                "active": sum(1 for m in found if m.is_active()),
            })

    neighbors = [{
        "id": e.id, "name": e.name, "maintainer": e.maintainer,
        "status": e.status.value, "compliance": e.compliance.value,
        "why_not_absorbed": e.why_not_absorbed,
        "install": e.install.command if e.install else None,
        "repository": e.repository, "tool_count": e.tool_count,
    } for e in catalog.all()]

    total_tools = len({t["name"] for tools in tools_by_server.values()
                       for t in tools})
    data = {
        "version": __version__,
        "registry_revision": registry.revision,
        "counts": {
            "sources": len(manifests),
            "active": len(active),
            "organizations": len(orgs.orgs),
            "labs": len(orgs.labs()),
            "capabilities": len(registry.capability_vocab),
            "capabilities_served": len({c for m in active
                                        for c in m.capability_ids()}),
            # Counted from the tree rather than written down, so a new
            # skill shows up on the site without anyone remembering to
            # edit a number.
            "skills": len(list((ROOT / "skills").glob("*/SKILL.md")))
            if (ROOT / "skills").exists() else 0,
            "program_offices": sum(1 for o in orgs.orgs.values()
                                   if o.kind.value == "program_office"),
            "pmas": sum(1 for o in orgs.orgs.values()
                        if o.kind.value == "power_marketing_administration"),
            "servers_shipping": sum(1 for s in SERVERS if s[2] == "shipping"),
            "servers_planned": sum(1 for s in SERVERS if s[2] == "planned"),
            "tools": total_tools,
            "adapters": len({m.adapter.type for m in manifests}
                            - {"none"}),
            "neighbors": len(neighbors),
            "records_reachable": sum(m.coverage.record_count or 0
                                     for m in active),
        },
        "questions": [{"question": q, "tool": t, "server": s}
                      for q, t, s in QUESTIONS],
        "servers": [{"name": n, "profile": p, "status": st,
                     "description": d, "tools": tools_by_server.get(n, [])}
                    for n, p, st, d in SERVERS],
        "sources": sources,
        "crosswalk": crosswalk,
        "neighbors": neighbors,
        "examples": [],
    }
    if with_fixtures:
        data["examples"] = asyncio.run(worked_examples(replay_context()))
    return data


def render_readme_status(data: dict) -> str:
    """The README's status table, generated so it cannot drift from the
    registry. The first hand-written version was one revision behind the
    runtime within a day."""
    c = data["counts"]
    tools = ", ".join(
        f"`{s['name']}` ({len(s['tools'])} tools, `{s['profile']}`)"
        for s in data["servers"] if s["status"] == "shipping")
    adapters = ", ".join(f"`{a}`" for a in sorted(
        {src["adapter"] for src in data["sources"]} - {"none"}))
    # "Verified live" means a fixture was recorded from the publisher through
    # the redacting recorder, which is the only evidence this build has that
    # the adapter and the endpoint agree. A keyed source qualifies the same
    # way: EIA was recorded with a real key on 2026-09-08. Until then this
    # list named adapters by hand and called EIA unverified for six days
    # after it was not. The registry's own adapter and transcribed tables
    # have no publisher to record from and are left out.
    queried = [src for src in data["sources"]
               if src["state"] == "active"
               and src["adapter"] not in ("none", "self_registry", "curated")]
    verified = ", ".join(src["name"] for src in queried if src["fixture"])
    unverified = ", ".join(
        f"{src['name']} (code complete; no fixture recorded yet)"
        for src in queried if not src["fixture"])
    rows = [
        ("Servers shipping", tools),
        ("Registry", f"{c['sources']} source manifests, {c['active']} active, "
                     f"{c['organizations']} organizations ({c['labs']} "
                     f"national laboratories), {c['neighbors']} sub-MCP "
                     "catalog entries"),
        ("Capabilities", f"{c['capabilities_served']} of {c['capabilities']} "
                         "in the vocabulary served by an active source"),
        ("Adapters", adapters),
        ("Verified live", verified),
        ("Not verified", unverified or "none"),
        ("Records reachable with no credential",
         f"{c['records_reachable']:,}"),
        ("Registry revision", data["registry_revision"]),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return (f"{STATUS_BEGIN}\n"
            "<!-- Generated by tools/build_site.py from the registry the "
            "servers run on. Edit the registry, not this block. -->\n"
            f"| | |\n|---|---|\n{table}\n{STATUS_END}")


def write_readme_status(data: dict) -> None:
    text = README.read_text()
    if STATUS_BEGIN not in text or STATUS_END not in text:
        raise SystemExit(f"{README} has no {STATUS_BEGIN} / {STATUS_END} "
                         "markers; the status table cannot be regenerated.")
    head, rest = text.split(STATUS_BEGIN, 1)
    _, tail = rest.split(STATUS_END, 1)
    README.write_text(head + render_readme_status(data) + tail)
    print(f"wrote status block in {README}")


def _schema_type(prop: dict) -> str:
    if "type" in prop:
        return prop["type"] if isinstance(prop["type"], str) \
            else " or ".join(prop["type"])
    options = prop.get("anyOf") or prop.get("oneOf") or []
    names = [o.get("type", "object") for o in options if isinstance(o, dict)]
    return " or ".join(names) if names else "any"


async def _tool_rows(ctx) -> dict[str, list[dict]]:
    """What a client sees: the tools and input schemas each profile serves,
    read back from the assembled MCP server rather than from ToolSpec."""
    out: dict[str, list[dict]] = {}
    for profile in sorted(PROFILES):
        server = build_server(ctx, profile)
        rows = []
        for tool in await server.list_tools():
            schema = tool.input_schema or {}
            required = set(schema.get("required") or [])
            args = [{"name": name, "type": _schema_type(prop),
                     "required": name in required,
                     "default": prop.get("default")}
                    for name, prop in (schema.get("properties") or {}).items()]
            rows.append({"name": tool.name,
                         "description": " ".join(
                             (tool.description or "").split()),
                         "args": args})
        out[profile] = rows
    return out


def render_reference(tools_by_profile: dict[str, list[dict]]) -> str:
    lines = [
        "# Tool reference",
        "",
        "Generated by `tools/build_site.py` from the assembled MCP servers: "
        "every tool below is read back from `tools/list`, so the names, "
        "arguments, and defaults are what a client sees. Regenerate after "
        "changing a tool; CI fails when this file is stale.",
        "",
    ]
    for spec in shipping():
        lines += [f"## {spec.name}", "", spec.description, ""]
        profiles = [p for p in sorted(PROFILES)
                    if p.split(":", 1)[0] == spec.key]
        lines += ["Profiles: " + ", ".join(
            f"`{p}`" + (" (default)" if p == spec.default_profile else "")
            for p in profiles), ""]
        if spec.needs_credentials:
            lines += ["Credentials: " + ", ".join(
                f"`{c}`" for c in spec.needs_credentials)
                + " (set with `doe-mcp configure credentials`).", ""]
        for profile in profiles:
            rows = tools_by_profile[profile]
            lines += [f"### Profile `{profile}` — {len(rows)} tools", ""]
            lines += ["| Tool | Arguments |", "|---|---|"]
            for row in rows:
                args = ", ".join(
                    f"`{a['name']}`" + ("" if a["required"] else "?")
                    for a in row["args"]) or "none"
                lines.append(f"| `{row['name']}` | {args} |")
            lines.append("")
        seen: set[str] = set()
        for profile in profiles:
            for row in tools_by_profile[profile]:
                if row["name"] in seen:
                    continue
                seen.add(row["name"])
                lines += [f"#### `{row['name']}`", "", row["description"], ""]
                if row["args"]:
                    lines += ["| Argument | Type | Required | Default |",
                              "|---|---|---|---|"]
                    for a in row["args"]:
                        default = ("" if a["default"] is None
                                   else f"`{json.dumps(a['default'])}`")
                        lines.append(
                            f"| `{a['name']}` | {a['type']} | "
                            f"{'yes' if a['required'] else 'no'} | "
                            f"{default} |")
                    lines.append("")
    lines += ["A `?` after an argument in the profile tables marks it "
              "optional. Every tool is read-only and returns the provenance "
              "envelope described in `design/architecture.md` Part 1 § 3.3.",
              ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", action="store_true",
                    help="capture worked examples by replaying recorded "
                         "responses (offline, deterministic)")
    args = ap.parse_args()

    data = build_data(args.fixtures)
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    out = DOCS / "data" / "site.json"
    existing = json.loads(out.read_text()) if out.exists() else {}
    if not args.fixtures and existing.get("examples"):
        # Do not silently drop worked examples on a metadata-only rebuild.
        data["examples"] = existing["examples"]
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {out}")

    from render_site import render                 # noqa: E402
    html_out = DOCS / "index.html"
    html_out.write_text(render(data))
    print(f"wrote {html_out}")

    write_readme_status(data)
    REFERENCE.write_text(render_reference(
        asyncio.run(_tool_rows(replay_context()))))
    print(f"wrote {REFERENCE}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
