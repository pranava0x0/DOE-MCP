"""Regulatory documents: DOE's own rulemakings, from the Federal Register.

These tools ship in `doe-research` rather than in the `doe-docs` server
architecture Part 1 § 4.1 sketches, for the reason BPA's feed and the two
USGS facility databases ship in `doe-energy-data`: one source is not a
server, § 3.4 prefers merging to splitting, and the rest of `doe-docs` is
crawler work that decision 0013 defers to Phase 5. This slice needs no
crawler — the Office of the Federal Register publishes an API — and 0013's
note of 2026-09-08 is what lets it go first. It sits with the research tools
because a published rule is a document with a full text, which is the
question that server already answers.

The scope decision these tools carry is FERC. The Federal Register files
FERC's documents under the Department of Energy's agency tree, and they are
most of what that tree publishes: since 2024, FERC is 20% of the rules
tagged DOE, 26% of the proposed rules, and 87% of the notices. Decision 0001
stops DOE-MCP's scope at DOE, so a tool here that passed the agency filter
straight through would be, by document count, mostly a FERC tool. They are
dropped — and counted, and explained, on every answer, because a filter a
caller cannot see is indistinguishable from a coverage gap.
"""
from __future__ import annotations

from typing import Any

from ..adapters.federal_register import (DEFAULT_ROWS, DOCUMENT_TYPES,
                                         MAX_PAGES)
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RegistryCoverage,
                             ResultCoverage, SourceClaimCoverage, TimeRange)
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ..core.assemble import pagination_coverage
from ._common import add_manifest_source, builder, require_active_source

DOCS_TOOLS = ToolRegistry(package="docs")

FEDERAL_REGISTER_SOURCE = "federal-register-doe"


def _record(document) -> dict[str, Any]:
    """One document as the tool reports it.

    Built from the fields the search asks for rather than by passing the raw
    body through: the API's default projection omits the docket, the CFR
    parts, and the regulatory identifier, which are what make a rule
    traceable to anything else.
    """
    raw = document.raw
    return {
        "document_number": document.document_number,
        "title": document.title,
        "type": document.type,
        "action": raw.get("action"),
        "abstract": raw.get("abstract"),
        "publication_date": document.publication_date,
        "effective_on": raw.get("effective_on"),
        "comments_close_on": raw.get("comments_close_on"),
        "agencies": document.agencies,
        "docket_ids": raw.get("docket_ids") or [],
        "regulation_id_numbers": raw.get("regulation_id_numbers") or [],
        "cfr_references": [
            f"{c.get('title')} CFR {c.get('part')}"
            for c in raw.get("cfr_references") or [] if isinstance(c, dict)],
        "html_url": raw.get("html_url"),
        "pdf_url": raw.get("pdf_url"),
        "full_text_url": raw.get("raw_text_url"),
    }


async def search_rulemakings(ctx: RuntimeContext, query: str = "",
                             document_type: str = "", agency: str = "",
                             docket: str = "", since: str = "",
                             until: str = "", rows: int = DEFAULT_ROWS,
                             page: int = 1) -> Envelope:
    b = builder(ctx, "docs.search_rulemakings", contract_version="1")
    manifest = require_active_source(ctx, FEDERAL_REGISTER_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "docs.search_rulemakings",
                                            [manifest])
    params = ctx.federal_register.params_for(manifest)
    types = [t.strip().upper() for t in document_type.split(",") if t.strip()]

    if agency.strip():
        tree = (await ctx.federal_register.agencies(manifest)).value
        selected = tree.require(agency.strip())
        if selected in params.excluded_child_slugs:
            raise InvalidQuery(
                f"{selected} is filed under the Department of Energy in this "
                "API, and DOE-MCP's scope stops at DOE. " + str(
                    params.exclusion_note))
    fetched = await ctx.federal_register.search(
        manifest, agency=agency.strip() or None, term=query,
        document_types=types or None, docket_id=docket,
        published_since=since, published_until=until, rows=rows, page=page)
    result = fetched.value

    excluded = frozenset(params.excluded_child_slugs)
    kept = [d for d in result.documents if not (d.agency_slugs & excluded)]
    dropped = len(result.documents) - len(kept)

    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for document in kept:
        b.add_evidence(source_ref=ref, record_id=document.document_number,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=document.publication_date,
                       transformations=[])

    data: dict[str, Any] = {
        "documents": [_record(d) for d in kept],
        "record_count": len(kept),
        "page": result.page,
        "page_size": result.per_page,
        "total_matches": result.count,
        "pages_reachable": result.pages_reachable,
        "note": (
            "The published text of DOE's rules, proposed rules, and notices, "
            "from the Federal Register itself. Each record carries its docket "
            "id, the CFR parts it touches, its regulatory identifier, and a "
            "link to the full text. The docket's comments and supporting "
            "material are not here — those live in regulations.gov, which "
            "DOE-MCP does not yet serve."),
    }
    if result.count_is_capped:
        data["total_is_a_floor"] = (
            f"The API stops counting at {result.count}, so that number is a "
            "floor rather than a total. Narrow the search with a date range "
            "or a document type to get a real count.")
    if result.pages_reachable and result.page >= result.pages_reachable:
        data["end_of_reachable_pages"] = (
            f"This is the last page the API will serve for this search; it "
            f"stops at {MAX_PAGES} pages however large the result set. "
            "Narrow the search rather than paging further.")
    if dropped:
        data["excluded_documents"] = dropped
        data["exclusion_reason"] = params.exclusion_note
        data["total_matches_is_unfiltered"] = (
            f"total_matches is the Federal Register's own count for this "
            f"query and includes the documents dropped above. {dropped} of "
            f"the {len(result.documents)} on this page were dropped, so the "
            "number of DOE documents matching is lower than total_matches "
            "and this tool cannot say by how much without reading every "
            "page.")
    if not kept and dropped:
        data["note"] = (
            f"Every one of the {dropped} documents on this page was FERC's, "
            "so the page is empty after DOE-MCP's scope filter rather than "
            "because the Federal Register had nothing. This is what a broad "
            "notice search looks like; filter by document type, or ask a "
            "narrower question.")

    dates = [d.publication_date for d in kept if d.publication_date]
    seen = result.page * result.per_page
    # `covered` would say the Federal Register held no matching record, and
    # on a page of notices that is flatly untrue: it held five and DOE-MCP's
    # own scope dropped all five. `partial` is the dimension that says the
    # registry covers part of what matched, which is what happened.
    if dropped and registry_dim == RegistryCoverage.covered:
        registry_dim = RegistryCoverage.partial
    pagination = pagination_coverage(
        result.count, seen, more=True if result.count_is_capped else None)
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(kept)), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        time_range=(TimeRange(**{"from": min(dates), "to": max(dates)})
                    if dates else None)))


async def get_rulemaking(ctx: RuntimeContext,
                         document_number: str) -> Envelope:
    b = builder(ctx, "docs.get_rulemaking", contract_version="1")
    manifest = require_active_source(ctx, FEDERAL_REGISTER_SOURCE)
    params = ctx.federal_register.params_for(manifest)
    fetched = await ctx.federal_register.get_document(manifest,
                                                      document_number)
    document = fetched.value

    excluded = frozenset(params.excluded_child_slugs)
    if document.agency_slugs & excluded:
        # Not an error and not an empty result: the document exists, it is
        # published, and DOE-MCP has no source for it. `registry: none` is
        # the dimension that says exactly that, and saying it here is the
        # difference between "there is no such rule" and "we do not cover
        # this agency".
        add_manifest_source(b, ctx, manifest,
                            retrieved_at=fetched.retrieved_at,
                            cache_age_seconds=fetched.cache_age_seconds,
                            from_cache=fetched.from_cache)
        return b.build({
            "document_number": document.document_number,
            "found": True,
            "served": False,
            "agencies": document.agencies,
            "note": (
                f"Document {document.document_number} exists and is "
                "published, but it belongs to an agency DOE-MCP does not "
                "cover. " + str(params.exclusion_note) + " The Federal "
                "Register's own page for it is the place to read it."),
            "html_url": document.raw.get("html_url"),
        }, Coverage(
            registry=RegistryCoverage.none,
            execution=ExecutionCoverage.complete,
            pagination=PaginationCoverage.complete,
            source_claim=SourceClaimCoverage.complete,
            result=ResultCoverage.empty, sources_searched=[manifest.id]))

    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "docs.get_rulemaking", [manifest])
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=document.title)
    b.add_evidence(source_ref=ref, record_id=document.document_number,
                   retrieved_at=fetched.retrieved_at,
                   effective_at=document.publication_date,
                   transformations=[])
    data = dict(_record(document))
    data["note"] = (
        "One published document, as the Federal Register printed it. "
        "`full_text_url` is the plain text of the whole document; "
        "`docket_ids` is how it joins to the rest of its rulemaking, which "
        "lives in regulations.gov.")
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


DOCS_TOOLS.register(ToolSpec(
    name="docs.search_rulemakings",
    description=(
        "DOE's rules, proposed rules, and notices as published in the Federal "
        "Register: the official text, with docket id, CFR parts, regulatory "
        "identifier, and a full-text link on every record. `query` is "
        "full-text; `document_type` takes "
        + ", ".join(sorted(DOCUMENT_TYPES)) + " (comma-separated); `docket`, "
        "`since` and `until` (YYYY-MM-DD) narrow further. FERC is filed under "
        "DOE in this API and is out of DOE-MCP's scope, so its documents are "
        "dropped and the count of dropped ones rides on the answer. Comments "
        "and docket material are NOT here."),
    toolset="default", contract_version="1", fn=search_rulemakings))

DOCS_TOOLS.register(ToolSpec(
    name="docs.get_rulemaking",
    description=(
        "One Federal Register document in full by its document number, in the "
        "publisher's own form ('2026-17979'). Returns the same fields as "
        "docs.search_rulemakings for a single record. A document belonging to "
        "an agency DOE-MCP does not cover is reported as found but not "
        "served, with coverage.registry='none' rather than as a miss."),
    toolset="default", contract_version="1", fn=get_rulemaking))
