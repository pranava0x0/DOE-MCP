"""Research tools: literature, datasets, and software over the OSTI family.

The first vertical, and the reason it is first: 5.7 million records across
four APIs that share one adapter, no key, and no MCP server anywhere else in
the world wrapping them.

Two design points recur through this module and are worth stating once.

**Pagination is a coverage dimension, not a footnote.** OSTI returns the
match count in a header and one page in the body. A tool that reports the
page as the answer turns 4,381,671 matches into "20 results", and no amount
of prose downstream repairs that. Every search here reports `total_matches`
in data and sets `pagination` to `truncated` whenever more exists.

**Empty is not the same as absent.** DOE PAGES holds public-access literature
after a 12-month embargo interval; a 2026 paper missing from it has not been
released yet. OSTI.GOV holds no dataset records at all, because those live in
DOE Data Explorer. Each empty result says which of those it is.
"""
from __future__ import annotations

from typing import Any

from ..adapters.osti_family import DEFAULT_ROWS, OstiPage, OstiRecord
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RecipeKind, RegistryCoverage,
                             ResultCoverage, SourceClaimCoverage, WarningCode)
from ..core.errors import InvalidQuery, SourceUnavailable
from ..core.registry import SourceManifest
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ..core.assemble import pagination_coverage
from ._common import (add_manifest_source, builder, merge_gaps,
                      require_active_source, select_fixed)

RESEARCH_TOOLS = ToolRegistry(package="research")

# Which manifest serves which question. Named rather than selected by
# capability alone because the four OSTI APIs answer overlapping questions and
# the caller's phrasing is what distinguishes them.
LITERATURE_SOURCES = ("osti-gov-records", "osti-doe-pages")
DATASET_SOURCE = "osti-data-explorer"
SOFTWARE_SOURCE = "osti-doe-code"


def _pagination(page: OstiPage) -> PaginationCoverage:
    return pagination_coverage(page.total_matches, len(page.records),
                               offset=(page.page - 1) * page.rows)


def _merge_pagination(so_far: PaginationCoverage,
                      page: PaginationCoverage) -> PaginationCoverage:
    """The fan-out's pagination from its collections': truncated if any
    collection is, otherwise unknown if any collection's total was missing,
    otherwise complete. The first version of this merge asked only whether
    a collection was truncated, so two collections that had both lost their
    totals reported the page as complete."""
    if PaginationCoverage.truncated in (so_far, page):
        return PaginationCoverage.truncated
    if PaginationCoverage.unknown in (so_far, page):
        return PaginationCoverage.unknown
    return PaginationCoverage.complete


def _record_summary(record: OstiRecord, *, detailed: bool) -> dict[str, Any]:
    """Concise by default. The 2K-token data budget is real and a
    bibliographic abstract is 200 words; ten of them with full metadata blow
    past it and the model reads a truncated blob instead of ten answers."""
    out: dict[str, Any] = {
        "id": record.record_id,
        "title": record.title,
        "authors": record.authors[:5],
        "publication_date": (record.publication_date or "")[:10] or None,
        "product_type": record.product_type,
    }
    if len(record.authors) > 5:
        out["authors_truncated"] = len(record.authors) - 5
    if record.doi:
        out["doi"] = record.doi
    if record.journal_name:
        out["journal"] = record.journal_name
    if record.report_number:
        out["report_number"] = record.report_number
    if record.research_orgs:
        out["research_orgs"] = record.research_orgs[:3]
    if record.repository_link:
        out["repository"] = record.repository_link
    if record.licenses:
        out["licenses"] = record.licenses
    if record.programming_languages:
        out["languages"] = record.programming_languages
    if record.site_ownership_code:
        out["site_code"] = record.site_ownership_code
    if detailed:
        if record.description:
            out["description"] = record.description
        if record.subjects:
            out["subjects"] = record.subjects[:10]
        if record.sponsor_orgs:
            out["sponsor_orgs"] = record.sponsor_orgs
        if record.site_url:
            out["site_url"] = record.site_url
    out = {k: v for k, v in out.items() if v not in (None, [], "")}
    return out


def _add_records(b, ctx: RuntimeContext, manifest: SourceManifest,
                 page: OstiPage, source_ref: str, *, retrieved_at: str,
                 detailed: bool) -> list[dict[str, Any]]:
    rows = []
    for record in page.records:
        b.add_evidence(source_ref=source_ref, record_id=record.record_id,
                       retrieved_at=retrieved_at,
                       effective_at=record.entry_date,
                       locator=record.biblio_url, transformations=["normalize"])
        rows.append(_record_summary(record, detailed=detailed))
        if record.doi_url:
            b.add_recipe(RecipeKind.doi, record.doi_url,
                         label=record.title[:80] or None)
        if record.fulltext_url:
            b.add_recipe(RecipeKind.fulltext_pdf, record.fulltext_url,
                         label="Full text (OSTI)")
        if record.repository_link:
            b.add_recipe(RecipeKind.repository, record.repository_link,
                         label="Source repository",
                         instructions="Hosted outside this source; returned "
                                      "as data and never fetched by this "
                                      "server.")
        if record.site_url:
            b.add_recipe(RecipeKind.landing_page, record.site_url,
                         label="Dataset landing page",
                         instructions="The repository that actually holds "
                                      "this data. It may require an account "
                                      "the catalog record does not mention.")
    return rows


def _search_filters(*, query: str, title: str, author: str,
                    research_org: str, sponsor_org: str,
                    year_from: int | None, year_to: int | None,
                    product_type: str, extra: dict[str, Any] | None = None
                    ) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if query.strip():
        filters["q"] = query.strip()
    if title.strip():
        filters["title"] = title.strip()
    if author.strip():
        filters["author"] = author.strip()
    if research_org.strip():
        filters["research_org"] = research_org.strip()
    if sponsor_org.strip():
        filters["sponsor_org"] = sponsor_org.strip()
    if product_type.strip():
        filters["product_type"] = product_type.strip()
    if year_from is not None:
        filters["publication_date_start"] = f"01/01/{year_from}"
    if year_to is not None:
        filters["publication_date_end"] = f"12/31/{year_to}"
    filters.update({k: v for k, v in (extra or {}).items() if v})
    if not filters:
        raise InvalidQuery(
            "give at least one of query, title, author, research_org, "
            "sponsor_org, or a year range. An unfiltered search would return "
            "the first page of 4.4 million records, which answers nothing.")
    return filters


async def search_literature(ctx: RuntimeContext, query: str = "",
                            title: str = "", author: str = "",
                            research_org: str = "", sponsor_org: str = "",
                            year_from: int | None = None,
                            year_to: int | None = None,
                            product_type: str = "",
                            public_access_only: bool = False,
                            rows: int = DEFAULT_ROWS, page: int = 1,
                            detailed: bool = False) -> Envelope:
    b = builder(ctx, "research.search_literature")
    filters = _search_filters(query=query, title=title, author=author,
                              research_org=research_org,
                              sponsor_org=sponsor_org, year_from=year_from,
                              year_to=year_to, product_type=product_type)

    source_ids = ((LITERATURE_SOURCES[1],) if public_access_only
                  else LITERATURE_SOURCES)
    # Through the same gate as capability selection: a collection flipped to
    # `proposed` drops out here and is named in sources_unavailable, and the
    # search goes on from whatever is still live.
    selected, skipped = select_fixed(ctx, source_ids)
    registry_dim, gaps = selection_coverage(ctx.sources, "literature.search",
                                            selected)
    gaps = merge_gaps(gaps, skipped)

    results: list[dict[str, Any]] = []
    per_source: list[dict[str, Any]] = []
    failures = []
    pagination = PaginationCoverage.complete
    for manifest in selected:
        try:
            fetched = await ctx.osti.search(manifest, filters=filters,
                                            rows=rows, page=page)
        except SourceUnavailable as err:
            from ..core.assemble import failure
            failures.append(failure(manifest.id, "SourceUnavailable",
                                    str(err)))
            continue
        result = fetched.value
        ref = add_manifest_source(b, ctx, manifest,
                                  retrieved_at=fetched.retrieved_at,
                                  cache_age_seconds=fetched.cache_age_seconds,
                                  from_cache=fetched.from_cache)
        rows_out = _add_records(b, ctx, manifest, result, ref,
                                retrieved_at=fetched.retrieved_at,
                                detailed=detailed)
        results.extend(rows_out)
        per_source.append({"source_id": manifest.id,
                           "system": manifest.name,
                           "total_matches": result.total_matches,
                           "returned": len(rows_out)})
        pagination = _merge_pagination(pagination, _pagination(result))

    if not selected or (failures and not per_source):
        execution = ExecutionCoverage.failed
    elif failures:
        execution = ExecutionCoverage.partial
    else:
        execution = ExecutionCoverage.complete

    # Deduplicate across the two collections by DOI: DOE PAGES and OSTI.GOV
    # index overlapping work, and the same paper appearing twice with
    # different ids reads as two findings.
    deduped, seen_dois = [], set()
    for row in results:
        doi = row.get("doi")
        if doi and doi in seen_dois:
            continue
        if doi:
            seen_dois.add(doi)
        deduped.append(row)
    duplicates = len(results) - len(deduped)

    data: dict[str, Any] = {
        "records": deduped,
        "record_count": len(deduped),
        "per_source": per_source,
    }
    if duplicates:
        data["deduplicated_by_doi"] = duplicates
    if not deduped:
        if failures:
            # Not the same empty. One collection did not answer, so "no
            # matching record" would be a claim about data nobody read.
            data["note"] = (
                f"No records returned, and {len(failures)} of "
                f"{len(selected)} collections could not be reached. This is "
                "not evidence that nothing matches — read "
                "coverage.source_failures before concluding anything.")
        else:
            data["note"] = (
                "The searched collections hold no matching record. That is "
                "an answer about DOE-funded output, not about the field: "
                "work DOE neither funded nor received is not indexed here."
                + (" DOE PAGES applies a 12-month embargo interval, so "
                   "recent papers are systematically absent from it."
                   if public_access_only else ""))

    if public_access_only:
        b.warn(WarningCode.terms_note,
               "Restricted to DOE PAGES, which holds public-access "
               "literature after a 12-month administrative embargo. Recent "
               "work is systematically absent; search without "
               "public_access_only to see the wider index.",
               "osti-doe-pages")
    if pagination is PaginationCoverage.truncated:
        b.next_action(
            finding=f"More matches exist than the {rows} returned.",
            capability="literature.search",
            reason="Call again with a higher page number, or narrow the "
                   "filters. Do not describe these as all the results.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=execution, pagination=pagination,
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(len(deduped)),
        sources_searched=[m.id for m in selected],
        sources_unavailable=gaps, source_failures=failures,
        known_limitations=sorted({lim for m in selected
                                  for lim in m.coverage.known_limitations})))


async def search_datasets(ctx: RuntimeContext, query: str = "",
                          title: str = "", author: str = "",
                          research_org: str = "", sponsor_org: str = "",
                          site_code: str = "",
                          year_from: int | None = None,
                          year_to: int | None = None,
                          rows: int = DEFAULT_ROWS, page: int = 1,
                          detailed: bool = False) -> Envelope:
    b = builder(ctx, "research.search_datasets")
    # site_code counts as a filter in its own right: "every dataset the
    # Geothermal Data Repository registered" is 1,035 records and a real
    # question, and it is the only API route into that repository.
    filters = _search_filters(query=query, title=title, author=author,
                              research_org=research_org,
                              sponsor_org=sponsor_org, year_from=year_from,
                              year_to=year_to, product_type="",
                              extra={"site_ownership_code": site_code.strip()})

    manifest = require_active_source(ctx, DATASET_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "dataset.search",
                                            [manifest])
    fetched = await ctx.osti.search(manifest, filters=filters, rows=rows,
                                    page=page)
    result = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    rows_out = _add_records(b, ctx, manifest, result, ref,
                            retrieved_at=fetched.retrieved_at,
                            detailed=detailed)

    data: dict[str, Any] = {
        "datasets": rows_out,
        "record_count": len(rows_out),
        "total_matches": result.total_matches,
        "note": "These are metadata records pointing at data held elsewhere. "
                "DOE Data Explorer hosts nothing itself: use the DOI or "
                "landing page in access_recipes to reach the actual data, "
                "and expect some repositories to require an account this "
                "record does not mention.",
    }
    if not rows_out:
        data["note"] = (
            "DOE Data Explorer holds no matching dataset record. Coverage "
            "here depends on whether a repository registered its datasets "
            "with OSTI, so this is a statement about registration rather "
            "than about existence.")

    b.warn(WarningCode.derived_layer,
           "Dataset discovery returns catalog metadata, not measurements. "
           "For user-facility raw data — beamlines, reactors, tokamaks — "
           "there is generally nothing to find: that data is proposal-gated "
           "by design and is not a coverage gap here.",
           manifest.id)

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=_pagination(result),
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(len(rows_out)),
        sources_searched=[manifest.id], sources_unavailable=gaps,
        known_limitations=sorted(manifest.coverage.known_limitations)))


async def search_software(ctx: RuntimeContext, query: str = "",
                          title: str = "", developer: str = "",
                          language: str = "", open_source_only: bool = False,
                          rows: int = DEFAULT_ROWS, page: int = 1,
                          detailed: bool = False) -> Envelope:
    b = builder(ctx, "research.search_software")
    filters: dict[str, Any] = {}
    if query.strip():
        filters["all_fields"] = query.strip()
    if title.strip():
        filters["software_title"] = title.strip()
    if developer.strip():
        filters["developers"] = developer.strip()
    if language.strip():
        filters["programming_languages"] = language.strip()
    if open_source_only:
        filters["project_type"] = "OS"
    if not filters:
        raise InvalidQuery(
            "give at least one of query, title, developer, or language. DOE "
            "CODE holds 7,675 records and an unfiltered first page answers "
            "nothing.")

    manifest = require_active_source(ctx, SOFTWARE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "software.search",
                                            [manifest])
    fetched = await ctx.osti.search(manifest, filters=filters, rows=rows,
                                    page=page)
    result = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    rows_out = _add_records(b, ctx, manifest, result, ref,
                            retrieved_at=fetched.retrieved_at,
                            detailed=detailed)

    data: dict[str, Any] = {
        "software": rows_out,
        "record_count": len(rows_out),
        "total_matches": result.total_matches,
    }
    if not rows_out:
        data["note"] = (
            "DOE CODE holds no matching software record. Registration is "
            "voluntary, so DOE-funded software may exist without appearing "
            "here.")
    elif not open_source_only:
        data["note"] = (
            "Records with project_type CS are closed-source and carry no "
            "repository link; that is the publisher's classification, not a "
            "missing field. Set open_source_only to exclude them.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=_pagination(result),
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(len(rows_out)),
        sources_searched=[manifest.id], sources_unavailable=gaps,
        known_limitations=sorted(manifest.coverage.known_limitations)))


async def get_record(ctx: RuntimeContext, record_id: str,
                     collection: str = "literature") -> Envelope:
    b = builder(ctx, "research.get_record")
    routing = {"literature": LITERATURE_SOURCES[0], "pages":
               LITERATURE_SOURCES[1], "dataset": DATASET_SOURCE,
               "software": SOFTWARE_SOURCE}
    if collection not in routing:
        raise InvalidQuery(
            f"collection must be one of {sorted(routing)}; got "
            f"{collection!r}. The four OSTI collections use separate id "
            "spaces, so the same number means different records in each and "
            "guessing would return the wrong one.")
    manifest = require_active_source(ctx, routing[collection])
    fetched = await ctx.osti.get_record(manifest, record_id)
    record = fetched.value

    # The fetch's own provenance, not "now": a record served from the cache
    # says so, with the age of what it is repeating.
    now = fetched.retrieved_at
    if record is None:
        add_manifest_source(b, ctx, manifest, retrieved_at=now,
                            cache_age_seconds=fetched.cache_age_seconds,
                            from_cache=fetched.from_cache)
        return b.build(
            {"record": None,
             "fulltext": None,
             "note": f"No record {record_id!r} in the {collection} "
                     f"collection. The four OSTI collections have separate "
                     f"id spaces; the same id may exist in another one."},
            Coverage(registry=RegistryCoverage.covered,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     result=ResultCoverage.empty,
                     sources_searched=[manifest.id]))

    ref = add_manifest_source(b, ctx, manifest, retrieved_at=now,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache, doi=record.doi)
    b.add_evidence(source_ref=ref, record_id=record.record_id,
                   retrieved_at=now, effective_at=record.entry_date,
                   locator=record.biblio_url, transformations=["normalize"])
    if record.doi_url:
        b.add_recipe(RecipeKind.doi, record.doi_url, label=record.title[:80])
    if record.fulltext_url:
        b.add_recipe(RecipeKind.fulltext_pdf, record.fulltext_url,
                     label="Full text (OSTI)")
    if record.site_url:
        b.add_recipe(RecipeKind.landing_page, record.site_url,
                     label="Dataset landing page")
    if record.repository_link:
        b.add_recipe(RecipeKind.repository, record.repository_link,
                     label="Source repository")
    if record.biblio_url:
        b.add_recipe(RecipeKind.landing_page, record.biblio_url,
                     label="OSTI record page")

    return b.build({"record": _record_summary(record, detailed=True),
                    "fulltext": _fulltext_answer(record, collection)},
                   Coverage(registry=RegistryCoverage.covered,
                            execution=ExecutionCoverage.complete,
                            pagination=PaginationCoverage.complete,
                            source_claim=SourceClaimCoverage.complete,
                            result=ResultCoverage.hit,
                            sources_searched=[manifest.id]))


def _fulltext_answer(record, collection: str) -> dict[str, Any]:
    """Whether this record's full text can actually be read, and how.

    A separate block rather than one more field on the record, because "can
    I read this?" is its own question and it used to be its own tool. That
    tool reported `result: empty` when a record existed but exposed no PDF,
    which said the collection held no matching record when it held one and
    the caller had it in hand. Availability is a fact about the record, so
    it is reported in the record; the result dimension stays a statement
    about the search.
    """
    if collection in ("dataset", "software"):
        return {
            "available": False,
            "url": None,
            "note": ("Datasets and software have landing pages and source "
                     "repositories rather than a full text. Both are in the "
                     "access recipes on this answer."),
        }
    if record.fulltext_url:
        return {"available": True, "url": record.fulltext_url,
                "doi_fallback": record.doi_url,
                "note": "OSTI serves the full text directly at this URL."}
    return {
        "available": False,
        "url": None,
        "doi_fallback": record.doi_url,
        "note": ("OSTI exposes no full-text link for this record. The work "
                 "may still be readable: try the DOI, which resolves to the "
                 "publisher, or check DOE PAGES if this came from the wider "
                 "index. Absence of a link here is NOT a paywall finding."),
    }


RESEARCH_TOOLS.register(ToolSpec(
    name="research.search_literature",
    description=(
        "Search DOE-funded research literature — technical reports, journal "
        "articles, conference papers, patents, theses — across OSTI.GOV "
        "(4.4M records) and DOE PAGES (260K public-access records) at once, "
        "deduplicated by DOI. Use for any question about published DOE or "
        "national-laboratory research. Filter by topic (query), author, "
        "research_org (the lab that did the work), sponsor_org (the office "
        "that paid), or a year range. Set public_access_only to restrict to "
        "work whose full text DOE guarantees — but note DOE PAGES applies a "
        "12-month embargo, so recent papers are systematically absent from "
        "it. Read coverage.pagination: total_matches is often far larger "
        "than what one call returns, and reporting the page as the answer "
        "would be wrong by orders of magnitude."),
    toolset="default", contract_version="1", fn=search_literature))

RESEARCH_TOOLS.register(ToolSpec(
    name="research.search_datasets",
    description=(
        "Search DOE Data Explorer (1.03M records) for DOE-funded research "
        "DATASETS. Returns metadata, DOIs, and landing pages — never the "
        "data, which lives at national labs, user facilities, and "
        "universities. Use when the user wants data rather than papers, or "
        "wants a dataset's DOI to cite. site_code filters to one repository "
        "(DOE-GDR returns the Geothermal Data Repository's 1,035 registered "
        "submissions, which is the only API route into it). For raw "
        "user-facility experimental data — beamline shots, reactor runs — "
        "expect nothing: that is proposal-gated by design, not a gap."),
    toolset="default", contract_version="1", fn=search_datasets))

RESEARCH_TOOLS.register(ToolSpec(
    name="research.search_software",
    description=(
        "Search DOE CODE (7,675 records) for DOE-funded software: "
        "repository links, licences, developers with ORCIDs, and sponsoring "
        "organizations. Use for 'what DOE-funded tool does X'. Filter by "
        "language or developer; set open_source_only to exclude closed-"
        "source records, which carry no repository link by classification "
        "rather than by omission. Registration is voluntary, so absence is "
        "not proof the software does not exist."),
    toolset="default", contract_version="1", fn=search_software))

RESEARCH_TOOLS.register(ToolSpec(
    name="research.get_record",
    description=(
        "Fetch one full record by id from a named OSTI collection: "
        "'literature' (OSTI.GOV), 'pages' (DOE PAGES), 'dataset' (DOE Data "
        "Explorer), or 'software' (DOE CODE). The collections have SEPARATE "
        "id spaces — the same number is a different record in each — so pass "
        "the collection the id came from rather than guessing. The answer "
        "includes a `fulltext` block saying whether the work can actually be "
        "read and where — the OSTI full-text URL when one exists, the DOI as "
        "a fallback to the publisher otherwise. No full-text link is NOT a "
        "paywall finding; it means OSTI exposes none."),
    toolset="default", contract_version="1", fn=get_record))
