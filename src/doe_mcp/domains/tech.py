"""Technology transfer: what a national laboratory has patented or released.

One tool over PNNL's VIPS index, which is the only active source that
answers "what has laboratory X produced" for all seventeen laboratories at
once — every other lab-specific holding in the registry is still inventory.
It ships in `doe-research` rather than in the `doe-projects` server
architecture Part 1 § 4.1 sketches, for the same reason as the Federal
Register slice: one source is not a server (§ 3.4), and a patent record is a
document with an abstract, a subject taxonomy, and named authors, which is
the question that server already answers.

Search and single-record lookup are one tool rather than two. They return
the same record shape and differ in one field — the filed text of the patent
or the software's README, which is ten kilobytes on the first patent tried
and is dropped from a search page. Splitting them would have bought a second
tool description and spent the last slot under decision 0014's ceiling for
this server's default profile.
"""
from __future__ import annotations

from typing import Any

from ..adapters.vips import (DEFAULT_ROWS, FULL_TEXT_FIELD, RECORD_TYPES)
from ..core.assemble import pagination_coverage
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, ResultCoverage,
                             SourceClaimCoverage, WarningCode)
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

TECH_TOOLS = ToolRegistry(package="tech")

VIPS_SOURCE = "pnnl-vips"


async def find_licensable_ip(ctx: RuntimeContext, query: str = "",
                             lab: str = "", record_type: str = "",
                             inventor: str = "", taxonomy: str = "",
                             since: str = "", until: str = "",
                             record_id: str = "", rows: int = DEFAULT_ROWS,
                             cursor: str = "") -> Envelope:
    b = builder(ctx, "tech.find_licensable_ip", contract_version="1")
    manifest = require_active_source(ctx, VIPS_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "tech.licensable_ip",
                                            [manifest])

    if record_id.strip():
        return await _one_record(ctx, b, manifest, registry_dim, gaps,
                                 record_id)

    resolved = ""
    if lab.strip():
        index = (await ctx.vips.labs(manifest)).value
        resolved = _resolve_lab(ctx, b, index, lab.strip(), manifest.id)

    fetched = await ctx.vips.search(
        manifest, text=query, lab=resolved, record_type=record_type,
        inventor=inventor, taxonomy=taxonomy, date_start=since,
        date_end=until, rows=rows, cursor=cursor)
    page = fetched.value

    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for record in page.records:
        b.add_evidence(source_ref=ref, record_id=record.id,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=record.raw.get("published_date"),
                       transformations=["full text omitted"]
                       if record.full_text_chars else [])

    withheld = sum(r.full_text_chars for r in page.records)
    data: dict[str, Any] = {
        "records": [r.raw for r in page.records],
        "record_count": len(page.records),
        "total_matches": page.total,
        "next_cursor": page.next_cursor,
        "filters_applied": page.filters,
        "note": (
            "Patents held and software released by the national laboratories, "
            "from PNNL's technology-transfer index. `id` is the patent number "
            "for a patent and an internal id for software; pass it back as "
            "`record_id` for the whole record including its filed text. "
            "Being listed is a published record of what exists, NOT an offer "
            "to license — the laboratory's own technology-transfer office is "
            "the party to any licence. For the authoritative text of a "
            "granted patent, the patent number is the key into USPTO."),
    }
    if page.next_cursor:
        data["paging"] = (
            "Pass next_cursor back as `cursor` with NO other filters: this "
            "service ignores every filter sent alongside a cursor, and the "
            "cursor already carries this search's filters.")
    if withheld:
        b.warn(WarningCode.truncated_inline,
               f"The {FULL_TEXT_FIELD} field — the filed text of a patent or "
               f"a software project's full README — is omitted from these "
               f"{len(page.records)} records, {withheld:,} characters in all. "
               "Everything else is whole. Ask for a single record by "
               "`record_id` to get it.", manifest.id)
    if not page.records:
        data["note"] = (
            "No record in this index matches those filters. The laboratory "
            "acronym and record type were both checked against the service's "
            "own lists before the search, so this is an empty result rather "
            "than a mistyped filter — which on this service would look "
            "exactly the same.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(
            page.total, len(page.records),
            more=True if page.next_cursor else None),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(page.records)), sources_searched=[manifest.id],
        sources_unavailable=gaps))


async def _one_record(ctx: RuntimeContext, b, manifest, registry_dim, gaps,
                      record_id: str) -> Envelope:
    fetched = await ctx.vips.get_record(manifest, record_id)
    record = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=record.title)
    b.add_evidence(source_ref=ref, record_id=record.id,
                   retrieved_at=fetched.retrieved_at,
                   effective_at=record.raw.get("published_date"),
                   transformations=[])
    data = dict(record.raw)
    data["note"] = (
        "One record whole, including the filed text. Being listed is a "
        "published record of what exists, not an offer to license.")
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


def _resolve_lab(ctx: RuntimeContext, b, index, requested: str,
                 source_id: str) -> str:
    """A laboratory acronym this service will actually match.

    This is the one filter on this source where getting it wrong is
    invisible: an unknown acronym returns zero results with HTTP 200, so
    `NREL` reads as a laboratory that holds no patents rather than as a
    laboratory that was renamed. The organization table is consulted second
    precisely because it is the thing that knows about the rename, and a
    match on a historical name is disclosed rather than silently applied.
    """
    wanted = requested.upper()
    if wanted in index.names:
        return wanted
    # Matches come back best-basis-first, so an exact id or current name
    # outranks a former one and the first that this service indexes wins.
    for match in ctx.organizations.resolve(requested):
        org = match.org
        for candidate in [org.id.upper(),
                          *(a.upper() for a in
                            (getattr(org.aliases, "abbreviations", None)
                             or []))]:
            if candidate not in index.names:
                continue
            if match.historical:
                b.warn(WarningCode.alias_match,
                       f"{requested!r} is a former name of {org.name}, which "
                       f"this service indexes as {candidate!r}. Searching "
                       "the old acronym directly returns zero results and no "
                       "error, which would have read as the laboratory "
                       "holding nothing.", source_id)
            return candidate
    raise InvalidQuery(
        f"{requested!r} is not a laboratory this database indexes and does "
        "not resolve to one. It answers an unknown acronym with zero results "
        "rather than an error, which would read as the laboratory holding "
        f"nothing. Known: {', '.join(index.acronyms)}.")


TECH_TOOLS.register(ToolSpec(
    name="tech.find_licensable_ip",
    description=(
        "Patents held and software released by the US national laboratories, "
        "from PNNL's technology-transfer index — the one place a single "
        "laboratory's own output is searchable. Use for 'what has Idaho "
        "patented on X', 'what software has PNNL released', 'who invented "
        "this'. `lab` takes an acronym or a laboratory name and is resolved "
        "against the alias table first, because this service answers an "
        "unknown acronym with zero results and no error. `record_type` is "
        + " or ".join(RECORD_TYPES) + "; `query`, `inventor`, `taxonomy`, "
        "`since` and `until` narrow further. Pass `record_id` for one record "
        "whole, including the filed patent text a search page omits. Page "
        "with `cursor` ALONE — filters sent with a cursor are ignored by the "
        "service. Being listed is not an offer to license."),
    toolset="default", contract_version="1", fn=find_licensable_ip))
