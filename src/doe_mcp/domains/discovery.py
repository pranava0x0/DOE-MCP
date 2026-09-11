"""Discovery tools: the catalog fan-out and DOE's own PuRe directory.

decision 0009-B in code. The finding behind it: at least four overlapping
catalogs-of-catalogs exist across DOE, with measured staleness in one of them
— data.json holds entries last modified in 2014 and 2015 beside current ones,
and its ARPA-E entry dates from 2022 while ARPA-E's own endpoint serves 1,721
live records.

So discovery fans out rather than picking a winner, DOE Data Explorer is
queried first-class because it is federated by construction, and every result
is labelled with the catalog it came from AND that catalog's vintage. A
caller can then discount the stale one themselves, which is better than a
tool discounting it silently or trusting it silently.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..core.assemble import failure, gap, result_dim
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RecipeKind, RegistryCoverage,
                             SourceClaimCoverage, WarningCode)
from ..core.errors import InvalidQuery, SourceUnavailable
from ..core.registry import SourceManifest
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

DISCOVERY_TOOLS = ToolRegistry(package="discovery")

# Ordered. DOE Data Explorer goes first because it already indexes the
# repositories the others only describe.
FANOUT = ("osti-data-explorer", "ornl-openenergyhub", "doe-open-data-catalog",
          "doe-code-json")

# The vintage of a live API is "whatever it holds right now", which is not a
# date. Reporting the newest entry_date across one sampled page as though it
# were the catalog's currency was worse than saying nothing: it made a live
# 1.03-million-record index look six years stale because two returned records
# happened to be old. Only harvested documents get a date, and only they can
# raise catalog_vintage — which is exactly what that warning code means.
LIVE = "live"


async def search_all_catalogs(ctx: RuntimeContext, query: str,
                              per_catalog: int = 5,
                              catalogs: str = "") -> Envelope:
    b = builder(ctx, "discovery.search_all_catalogs", contract_version="1")
    if not query.strip():
        raise InvalidQuery(
            "give a search term. An unfiltered fan-out across four catalogs "
            "returns the first page of each and answers nothing.")
    if per_catalog < 1 or per_catalog > 20:
        raise InvalidQuery("per_catalog must be between 1 and 20; the data "
                           "budget is shared across every catalog searched.")

    wanted = [c.strip() for c in catalogs.split(",") if c.strip()] or list(FANOUT)
    unknown = [c for c in wanted if c not in FANOUT]
    if unknown:
        raise InvalidQuery(f"unknown catalogs {unknown}; available: "
                           f"{list(FANOUT)}")

    results: list[dict[str, Any]] = []
    per_catalog_rows: list[dict[str, Any]] = []
    failures = []
    gaps = []
    seen_titles: set[str] = set()

    # The same gate as every other route into a source. A catalog that is
    # not active is named in sources_unavailable rather than skipped in
    # silence, so a four-catalog answer that came from three says so.
    queried: list[tuple[str, SourceManifest]] = []
    for source_id in wanted:
        manifest = ctx.sources.get(source_id)
        if manifest is None:
            continue
        block = ctx.sources.selection_block(manifest)
        if block is not None:
            gaps.append(gap(source_id, block))
            continue
        queried.append((source_id, manifest))
    searched = [source_id for source_id, _ in queried]

    # The catalogs live on different hosts, so they are asked at once; the
    # per-host politeness budget still governs each. Results are handled in
    # FANOUT order whatever the arrival order, so the duplicate-of-earlier-
    # catalog marker is deterministic.
    outcomes = await asyncio.gather(
        *(_query_catalog(ctx, source_id, query, per_catalog)
          for source_id, _ in queried),
        return_exceptions=True)

    for (source_id, manifest), outcome in zip(queried, outcomes,
                                              strict=True):
        if isinstance(outcome, SourceUnavailable):
            failures.append(failure(source_id, "SourceUnavailable",
                                    str(outcome)))
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        rows, total, vintage, retrieved, cache_age, cached = outcome

        ref = add_manifest_source(b, ctx, manifest, retrieved_at=retrieved,
                                  cache_age_seconds=cache_age,
                                  from_cache=cached)
        for row in rows:
            row["catalog"] = source_id
            row["catalog_vintage"] = vintage
            key = (row.get("title") or "").strip().lower()
            if key and key in seen_titles:
                row["duplicate_of_earlier_catalog"] = True
            elif key:
                seen_titles.add(key)
            b.add_evidence(source_ref=ref, record_id=str(row.get("id") or ""),
                           retrieved_at=retrieved, transformations=["normalize"])
            if row.get("landing_page"):
                b.add_recipe(RecipeKind.landing_page, row["landing_page"],
                             label=(row.get("title") or "")[:80] or None,
                             instructions="Catalog-supplied URL, never "
                                          "fetched by this server and not "
                                          "checked for liveness.")
            results.append(row)
        per_catalog_rows.append({"catalog": source_id,
                                 "system": manifest.name,
                                 "vintage": vintage,
                                 "total_matches": total,
                                 "returned": len(rows)})

        # The vintage disclosure, for harvested catalogs only. Saying WHICH
        # catalog is stale is more useful than a blanket caveat on all four.
        if vintage and vintage != LIVE and vintage[:4] < "2026":
            b.warn(WarningCode.catalog_vintage,
                   f"{manifest.name} answered from a harvested catalog whose "
                   f"newest entry is dated {vintage}. Entries in it may name "
                   "program offices that have since been renamed or "
                   "dismantled, and its links may be dead.", source_id)

    data: dict[str, Any] = {
        "results": results,
        "record_count": len(results),
        "per_catalog": per_catalog_rows,
        "note": "Four DOE catalogs overlap and disagree; none is a superset. "
                "Each result names its catalog and that catalog's vintage so "
                "you can weigh them. Catalog-supplied links are returned "
                "unverified — a dead link in a catalog is common and is not "
                "evidence the dataset is gone.",
    }
    if not results and not failures:
        data["note"] = (
            f"None of the {len(searched)} catalogs searched holds a match for "
            f"{query!r}. These are catalogs of DOE-funded data; a gap here is "
            "about registration and cataloguing rather than about whether the "
            "data exists.")

    if failures and not results:
        execution = ExecutionCoverage.failed
    elif failures:
        execution = ExecutionCoverage.partial
    else:
        execution = ExecutionCoverage.complete

    return b.build(data, Coverage(
        registry=RegistryCoverage.covered, execution=execution,
        pagination=PaginationCoverage.truncated,
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(len(results)), sources_searched=searched,
        sources_unavailable=gaps, source_failures=failures))


async def _query_catalog(ctx: RuntimeContext, source_id: str, query: str,
                         limit: int):
    """One catalog, normalized to a common row shape.

    Normalizing here rather than in each adapter keeps the adapters honest:
    each one returns its publisher's shape, and only this fan-out — which
    exists to compare catalogs — flattens them.
    """
    manifest = ctx.sources.get(source_id)
    assert manifest is not None
    if source_id == "osti-data-explorer":
        fetched = await ctx.osti.search(manifest, filters={"q": query},
                                        rows=limit, page=1)
        page = fetched.value
        rows = [{"id": r.record_id, "title": r.title, "doi": r.doi,
                 "landing_page": r.site_url or r.biblio_url,
                 "date": (r.publication_date or "")[:10] or None,
                 "orgs": r.research_orgs[:2]}
                for r in page.records]
        return (rows, page.total_matches, LIVE, fetched.retrieved_at,
                fetched.cache_age_seconds, fetched.from_cache)

    if source_id == "ornl-openenergyhub":
        fetched = await ctx.opendatasoft.search_datasets(
            manifest, text=query, limit=limit)
        page = fetched.value
        rows = [{"id": d.dataset_id, "title": d.title,
                 "landing_page": (f"https://openenergyhub.ornl.gov/explore/"
                                  f"dataset/{d.dataset_id}/"),
                 "date": (d.modified or "")[:10] or None,
                 "themes": d.theme[:2]}
                for d in page.datasets]
        return (rows, page.total_count, LIVE, fetched.retrieved_at,
                fetched.cache_age_seconds, fetched.from_cache)

    fetched = await ctx.json_document.search(manifest, text=query,
                                             limit=limit)
    result = fetched.value
    rows = [{"id": e.entry_id, "title": e.title,
             "landing_page": e.landing_page,
             "date": (e.modified or "")[:10] or None,
             "publisher": e.publisher}
            for e in result.entries]
    return (rows, result.total_matches,
            (result.document_vintage or "")[:10] or None,
            fetched.retrieved_at, fetched.cache_age_seconds,
            fetched.from_cache)


async def list_pure_resources(ctx: RuntimeContext) -> Envelope:
    b = builder(ctx, "discovery.list_pure_resources", contract_version="1")
    manifest = require_active_source(ctx, "doe-pure-resources")
    fetched = ctx.curated.read(manifest)
    result = fetched.value
    now = fetched.retrieved_at
    ref = add_manifest_source(b, ctx, manifest, retrieved_at=now,
                              cache_age_seconds=fetched.cache_age_seconds,
                              dataset_version=result.transcribed_at)

    rows = []
    for entry in result.entries:
        rows.append({k: entry.get(k) for k in
                     ("id", "name", "abbreviation", "steward", "what", "url",
                      "doe_mcp_status")
                     if entry.get(k)} | {
            "doe_mcp_note": " ".join((entry.get("doe_mcp_note") or "").split())
            or None})
        b.add_evidence(source_ref=ref, record_id=str(entry.get("id")),
                       retrieved_at=now,
                       transformations=["transcribed_from_html"])
        if entry.get("url"):
            b.add_recipe(RecipeKind.landing_page, entry["url"],
                         label=entry.get("name"))
    rows = [{k: v for k, v in row.items() if v} for row in rows]

    covered = sum(1 for e in result.entries
                  if e.get("doe_mcp_status") in ("active", "depend_and_wrap"))
    return b.build(
        {"pure_resources": rows, "record_count": len(rows),
         "doe_mcp_covered": covered,
         "transcribed_from": result.transcribed_from,
         "transcribed_at": result.transcribed_at,
         "note": "DOE's own designation of its most durable public data "
                 "resources. The doe_mcp_status on each entry is THIS "
                 "project's assessment of its own coverage, not a DOE "
                 "statement — and it is deliberately unflattering: most of "
                 "these are still inventory here."},
        Coverage(registry=RegistryCoverage.covered,
                 execution=ExecutionCoverage.complete,
                 pagination=PaginationCoverage.complete,
                 source_claim=SourceClaimCoverage.complete,
                 result=result_dim(len(rows)),
                 sources_searched=[manifest.id]))


DISCOVERY_TOOLS.register(ToolSpec(
    name="discovery.search_all_catalogs",
    description=(
        "Search four DOE dataset catalogs at once — DOE Data Explorer, "
        "ORNL's Open Energy Data Hub, energy.gov/data.json, and "
        "energy.gov/code.json — labelling each result with its catalog and "
        "that catalog's vintage. Use when the user is looking for a dataset "
        "and does not know which DOE system holds it. The catalogs overlap "
        "and disagree, and two of them are measurably stale: data.json holds "
        "entries last touched in 2014 beside current ones. Weigh the vintage "
        "rather than treating all four as equally current."),
    toolset="default", contract_version="1", fn=search_all_catalogs))

DISCOVERY_TOOLS.register(ToolSpec(
    name="discovery.list_pure_resources",
    description=(
        "DOE's seven designated Public Reusable Research (PuRe) data "
        "resources — the department's own list of its most durable public "
        "data holdings: ATcT, Materials Project, ARM, JGI, KBase, the "
        "Particle Data Group, and NNDC. Use when a user asks what DOE "
        "considers its flagship public data, or wants an authoritative "
        "starting point for a scientific domain."),
    toolset="default", contract_version="1", fn=list_pure_resources))
