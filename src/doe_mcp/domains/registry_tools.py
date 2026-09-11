"""Registry tools: organization resolution, source discovery, the crosswalk.

These answer questions about DOE-MCP itself rather than about DOE's data, and
that distinction is the point of the module. A caller who asks "what does Oak
Ridge publish" is asking what this project knows, not what ORNL says about
itself, and every answer here carries the project's own registry as its
provenance so the difference is visible.

`registry.resolve_org` is the tool that repays the whole organization table.
NREL became the National Laboratory of the Rockies on 2025-12-01 and every
*.nrel.gov domain went dark with NO redirect, so a caller holding an old URL
gets nothing at all — not a redirect, not a 404 page explaining anything.
This tool is how they find out why.
"""
from __future__ import annotations

from typing import Any

from ..core.assemble import result_dim
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RegistryCoverage,
                             ResultCoverage, SourceClaimCoverage, WarningCode,
                             utc_now_iso)
from ..core.errors import InvalidQuery
from ..core.organizations import OrgKind, Organization
from ..core.registry import SourceManifest
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_project_source, builder

REGISTRY_TOOLS = ToolRegistry(package="registry")


def _org_summary(org: Organization) -> dict[str, Any]:
    out: dict[str, Any] = {"id": org.id, "name": org.name,
                           "kind": org.kind.value}
    if org.parent:
        out["parent"] = org.parent
    if org.domains:
        out["domains"] = org.domains
    if org.aliases.abbreviations:
        out["abbreviations"] = org.aliases.abbreviations
    if org.aliases.former_names:
        out["former_names"] = org.aliases.former_names
    if org.aliases.former_domains:
        out["former_domains"] = org.aliases.former_domains
    if org.aliases.github_orgs:
        out["github_orgs"] = org.aliases.github_orgs
    if org.renamed_on:
        out["renamed_on"] = org.renamed_on
    if org.note:
        out["note"] = org.note
    return out


def _source_summary(m: SourceManifest, ctx: RuntimeContext) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": m.id, "name": m.name, "domain": m.domain,
        "steward": m.publisher.steward,
        "authority_level": m.publisher.authority_level.value,
        "declared_state": m.lifecycle.declared_state.value,
        "operational_state": ctx.sources.operational(m.id).value,
        "automation_status": m.access.automation_status.value,
        "tier": m.access.tier.value,
    }
    if m.publisher.funder:
        out["funder"] = m.publisher.funder
    if m.publisher.host:
        out["host"] = m.publisher.host
    if m.capabilities:
        out["capabilities"] = sorted(m.capability_ids())
    if m.planned_capabilities:
        out["planned_capabilities"] = sorted(m.planned_capabilities)
    if m.coverage.record_count is not None:
        out["record_count"] = m.coverage.record_count
    if m.lifecycle.blocked_reason:
        out["blocked_reason"] = m.lifecycle.blocked_reason
    return out


async def resolve_org(ctx: RuntimeContext, query: str) -> Envelope:
    b = builder(ctx, "registry.resolve_org", contract_version="1")
    if not query.strip():
        raise InvalidQuery("give an organization name, abbreviation, former "
                           "name, or domain to resolve.")
    src = add_project_source(b, ctx)
    matches = ctx.organizations.resolve(query)

    if not matches:
        return b.build(
            {"resolved": None, "candidates": [],
             "note": f"No DOE organization matches {query!r}. The table "
                     "covers DOE headquarters, its program offices, the four "
                     "Power Marketing Administrations, all seventeen "
                     "national laboratories, and the non-DOE agencies named "
                     "in provenance. A match failure here does not mean the "
                     "organization does not exist."},
            Coverage(registry=RegistryCoverage.covered,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     source_claim=SourceClaimCoverage.complete,
                     result=ResultCoverage.empty))

    best = matches[0]
    for match in matches:
        b.add_evidence(source_ref=src, record_id=match.org.id,
                       retrieved_at=utc_now_iso(), transformations=[])

    data: dict[str, Any] = {
        "resolved": _org_summary(best.org),
        "basis": best.basis,
        "candidates": [_org_summary(m.org) for m in matches[1:]],
        "source_count": len(ctx.sources.for_lab(best.org.id)),
    }

    if best.historical:
        # The whole reason this tool exists. Answering as though the old name
        # were current would hide a rename that silently killed every URL the
        # caller has.
        data["historical_name"] = {
            "queried": query,
            "resolved_to": best.org.id,
            "renamed_on": best.org.renamed_on,
            "note": f"{query!r} is a former name or domain. The current "
                    f"organization is {best.org.name}"
                    + (f", as of {best.org.renamed_on}"
                       if best.org.renamed_on else "")
                    + ". Tell the user the name they used is historical.",
        }
        code = (WarningCode.domain_migrated
                if best.basis == "former_domain" else WarningCode.alias_match)
        detail = ("Old domains in this ecosystem frequently do NOT redirect: "
                  "every *.nrel.gov URL went dark with no forwarding when "
                  "NREL became NLR, so a stale link returns nothing rather "
                  "than an explanation."
                  if best.basis == "former_domain" else
                  "A record or document using the old name predates the "
                  "change; check its date before treating it as current.")
        b.warn(code, f"Resolved {query!r} to {best.org.id} by "
                     f"{best.basis.replace('_', ' ')}, not by a current one. "
                     f"{detail}")

    if len(matches) > 1:
        data["note"] = ("More than one organization matches. The first is the "
                        "best match by basis; present the candidates rather "
                        "than assuming.")

    return b.build(data, Coverage(
        registry=RegistryCoverage.covered,
        execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit), requires_user_choice=len(matches) > 1)


async def lab_crosswalk(ctx: RuntimeContext, lab: str = "") -> Envelope:
    b = builder(ctx, "registry.lab_crosswalk", contract_version="1")
    src = add_project_source(b, ctx)

    if not lab.strip():
        labs = ctx.organizations.labs()
        rows = [{"id": o.id, "name": o.name,
                 "source_count": len(ctx.sources.for_lab(o.id)),
                 "active_sources": sum(1 for m in ctx.sources.for_lab(o.id)
                                       if m.is_active())}
                for o in labs]
        for row in rows:
            b.add_evidence(source_ref=src, record_id=row["id"],
                           retrieved_at=utc_now_iso(), transformations=[])
        return b.build(
            {"labs": rows, "record_count": len(rows),
             "note": "All seventeen DOE national laboratories, with how many "
                     "registered sources carry each one's data. Servers are "
                     "organized by data domain rather than by laboratory, so "
                     "this crosswalk — not a server per lab — is how the "
                     "lab-oriented view is answered."},
            Coverage(registry=RegistryCoverage.covered,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     source_claim=SourceClaimCoverage.complete,
                     result=result_dim(len(rows))))

    matches = ctx.organizations.resolve(lab)
    if not matches:
        return b.build(
            {"lab": None, "sources": [],
             "note": f"No organization matches {lab!r}. Call without an "
                     "argument to list all seventeen laboratories."},
            Coverage(registry=RegistryCoverage.none,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     result=ResultCoverage.empty))

    org = matches[0].org
    sources = ctx.sources.for_lab(org.id)
    rows = [_source_summary(m, ctx) for m in sources]
    for m in sources:
        b.add_evidence(source_ref=src, record_id=m.id,
                       retrieved_at=utc_now_iso(), transformations=[])

    data: dict[str, Any] = {
        "lab": _org_summary(org),
        "sources": rows,
        "record_count": len(rows),
        "active_count": sum(1 for m in sources if m.is_active()),
    }
    if matches[0].historical:
        b.warn(WarningCode.alias_match,
               f"{lab!r} is a former name for {org.name}. Say so.")
    if not rows:
        data["note"] = (
            f"DOE-MCP has no registered source carrying {org.name}'s data. "
            "That is a gap in this project's registry, not a statement that "
            "the laboratory publishes nothing.")
    elif org.kind == OrgKind.national_lab:
        data["note"] = (
            "Sources carrying this laboratory's data, wherever they are "
            "hosted. Several laboratories' flagship data lives on shared "
            "cross-lab platforms rather than on their own domains, which is "
            "why this is a registry query and not a per-lab server.")

    return b.build(data, Coverage(
        registry=(RegistryCoverage.covered if rows
                  else RegistryCoverage.none),
        execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(rows))))


async def search_sources(ctx: RuntimeContext, text: str = "",
                         domain: str = "", capability: str = "",
                         state: str = "", limit: int = 25) -> Envelope:
    b = builder(ctx, "registry.search_sources", contract_version="1")
    if capability and capability not in ctx.sources.capability_vocab:
        raise InvalidQuery(
            f"capability {capability!r} is not in the vocabulary; known: "
            f"{sorted(ctx.sources.capability_vocab)}")
    if state and state not in ("active", "proposed", "retired"):
        raise InvalidQuery("state must be active, proposed, or retired")

    terms = [w for w in text.lower().replace("-", " ").split() if w]
    hits = []
    for m in ctx.sources.manifests.values():
        if domain and m.domain != domain:
            continue
        # Planned capabilities match too: "does DOE-MCP cover geothermal"
        # is answered by the proposed GDR manifest and its blocked_reason,
        # which is a better answer than nothing.
        if capability and capability not in m.capability_ids() \
                and capability not in m.planned_capabilities:
            continue
        if state and m.lifecycle.declared_state.value != state:
            continue
        if terms:
            haystack = " ".join([m.id, m.name, m.domain,
                                 m.publisher.steward, " ".join(m.labs),
                                 m.coverage.scope]).lower().replace("-", " ")
            words = haystack.split()
            if not all(any(w.startswith(t) for w in words) for t in terms):
                continue
        hits.append(_source_summary(m, ctx))
    hits.sort(key=lambda h: (h["declared_state"] != "active", h["id"]))
    truncated = len(hits) > limit
    shown = hits[:limit]

    src = add_project_source(b, ctx)
    for h in shown:
        b.add_evidence(source_ref=src, record_id=h["id"],
                       retrieved_at=utc_now_iso(), transformations=[])

    data: dict[str, Any] = {
        "sources": shown, "record_count": len(shown),
        "total_matches": len(hits),
        "note": "Active sources can be queried now. Proposed ones are "
                "inventory: DOE-MCP knows they exist and each carries a "
                "blocked_reason saying what stands in the way. That is a "
                "different answer from 'we have never heard of it'.",
    }
    return b.build(data, Coverage(
        registry=RegistryCoverage.covered,
        execution=ExecutionCoverage.complete,
        pagination=(PaginationCoverage.truncated if truncated
                    else PaginationCoverage.complete),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(shown))))


async def describe_source(ctx: RuntimeContext, source_id: str) -> Envelope:
    b = builder(ctx, "registry.describe_source", contract_version="1")
    src = add_project_source(b, ctx)
    m = ctx.sources.get(source_id)
    if m is None:
        return b.build(
            {"source": None,
             "note": f"No source {source_id!r} in the registry. "
                     "registry.search_sources lists what exists."},
            Coverage(registry=RegistryCoverage.covered,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     result=ResultCoverage.empty))
    b.add_evidence(source_ref=src, record_id=m.id,
                   retrieved_at=utc_now_iso(), transformations=[])

    detail: dict[str, Any] = _source_summary(m, ctx)
    detail.update({
        "scope": m.coverage.scope,
        "temporal": m.coverage.temporal,
        "dataset_version": m.coverage.dataset_version,
        "terms_url": m.access.terms_url,
        "terms_notes": m.access.terms_notes,
        "terms_reviewed_at": m.access.terms_reviewed_at,
        "known_limitations": m.coverage.known_limitations,
        "authority_notes": m.authority_notes,
        "expected_cadence": m.freshness.expected_cadence,
        "labs": m.labs,
        "last_verified": m.lifecycle.last_verified,
    })
    if m.access.terms_gap:
        detail["terms_gap"] = m.access.terms_gap
    if m.access.credential_ref:
        detail["credential_ref"] = m.access.credential_ref
    if m.aliases.former_names or m.aliases.former_domains:
        detail["aliases"] = {"former_names": m.aliases.former_names,
                             "former_domains": m.aliases.former_domains}
    detail = {k: v for k, v in detail.items() if v not in (None, [], "")}

    if m.access.citation_required:
        b.warn(WarningCode.citation_required,
               f"This publisher requires citation as a condition of use: "
               f"{m.access.citation_text}", m.id)
    if m.access.terms_gap:
        b.warn(WarningCode.terms_note, m.access.terms_gap, m.id)

    return b.build({"source": detail}, Coverage(
        registry=RegistryCoverage.covered,
        execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit))


async def list_neighbors(ctx: RuntimeContext, capability: str = "") -> Envelope:
    """The federation table (decision 0018), made publicly checkable."""
    b = builder(ctx, "registry.list_neighbors", contract_version="1")
    src = add_project_source(b, ctx)
    entries = (ctx.catalog.for_capability(capability) if capability
               else ctx.catalog.all())
    rows = []
    for e in entries:
        row: dict[str, Any] = {
            "id": e.id, "name": e.name, "maintainer": e.maintainer,
            "status": e.status.value, "compliance": e.compliance.value,
            "why_not_absorbed": " ".join(e.why_not_absorbed.split()),
        }
        if e.capabilities:
            row["capabilities"] = e.capabilities
        if e.tool_count:
            row["tool_count"] = e.tool_count
        if e.install:
            row["install"] = {"method": e.install.method,
                              "command": e.install.command}
        if e.repository:
            row["repository"] = e.repository
        rows.append(row)
        b.add_evidence(source_ref=src, record_id=e.id,
                       retrieved_at=utc_now_iso(), transformations=[])

    return b.build(
        {"neighbors": rows, "record_count": len(rows),
         "note": "MCP has no mechanism for one server to advertise or "
                 "install another (checked against the protocol 2026-09-01), "
                 "so these carry executable install commands rather than "
                 "links. Results from a server listed as 'opaque' are "
                 "unverified external evidence and should be labelled as "
                 "such — being official does not make a server's output "
                 "carry provenance."},
        Coverage(registry=RegistryCoverage.covered,
                 execution=ExecutionCoverage.complete,
                 pagination=PaginationCoverage.complete,
                 source_claim=SourceClaimCoverage.complete,
                 result=result_dim(len(rows))))


REGISTRY_TOOLS.register(ToolSpec(
    name="registry.resolve_org",
    description=(
        "Resolve a DOE organization from a name, abbreviation, FORMER name, "
        "or domain — including dead ones. Use FIRST whenever a user names a "
        "lab or office, because this ecosystem renames things and the old "
        "URLs do not redirect. 'NREL' resolves to the National Laboratory of "
        "the Rockies and warns that the name is historical; a *.nrel.gov "
        "domain resolves the same way and warns that the URL is dead with no "
        "forwarding. Same for EERE (now CMEI), FECM (now HGEO), LPO (now "
        "EDF), HFTO and BETO (merged into AFFO), IEDO (now ITO), and the "
        "dismantled Grid Deployment Office."),
    toolset="discovery-min", contract_version="1", fn=resolve_org))

REGISTRY_TOOLS.register(ToolSpec(
    name="registry.lab_crosswalk",
    description=(
        "What a DOE national laboratory publishes, across every platform "
        "carrying its data. Call with no argument for all seventeen labs and "
        "their source counts. Servers here are organized by data domain, not "
        "by laboratory, so this is how a lab-oriented question gets "
        "answered — and it is the honest way, since several labs' flagship "
        "data lives on shared cross-lab platforms rather than their own "
        "domains. A lab with zero sources is a gap in this project, not a "
        "claim that the lab publishes nothing."),
    toolset="discovery-min", contract_version="1", fn=lab_crosswalk))

REGISTRY_TOOLS.register(ToolSpec(
    name="registry.search_sources",
    description=(
        "Search DOE-MCP's own source registry: which public DOE systems this "
        "project knows about, active or not. Use to answer 'what does "
        "DOE-MCP cover?' BEFORE assuming coverage, and to explain a gap — "
        "every non-active source carries a blocked_reason naming what stands "
        "in the way, which is a real answer where an empty result is not. "
        "Not a data-query tool: use the research.* tools for records."),
    toolset="discovery", contract_version="1", fn=search_sources))

REGISTRY_TOOLS.register(ToolSpec(
    name="registry.describe_source",
    description=(
        "The full registry entry for one source: publisher and funder, "
        "authority level, terms and when they were reviewed, known "
        "limitations, record counts, and why it is not active if it is not. "
        "Use when the user asks where an answer came from, what a source's "
        "caveats are, or whether they may use the data."),
    toolset="discovery", contract_version="1", fn=describe_source))

REGISTRY_TOOLS.register(ToolSpec(
    name="registry.list_neighbors",
    description=(
        "Other MCP servers over DOE-adjacent data — PNNL's NEPA-MCP, the "
        "building-simulation servers, NASA's Earthdata server, the gated "
        "Genesis-lane deployments — with an executable install command and a "
        "stated reason DOE-MCP does not duplicate each one. Use when a "
        "question falls outside this project's coverage and a neighbouring "
        "server serves it."),
    toolset="discovery", contract_version="1", fn=list_neighbors))
