"""The `federal_register` adapter: the Office of the Federal Register's API.

A source-specific adapter, like `eia_v2` and `fueleconomy`, because this is
one API rather than a platform genre: nothing else in the inventory speaks
`conditions[...]` query parameters over the daily journal of record. What it
covers is DOE's own rulemakings — the official text of them, from the office
that publishes them, with docket ids, CFR references, RINs, and a link to the
full text of each.

Four properties of the API, all verified live 2026-09-08, decide what
follows:

1. **`per_page=1` silently returns twenty.** Every other value is honoured;
   one is not, and the response says nothing about it. A client that asked
   for one document and paged on the assumption it got one would skip
   nineteen documents per page. The adapter refuses the value rather than
   quietly correcting it, because a caller who asked for one row and got
   twenty deserves to be told which happened.

2. **`count` stops at 10,000.** Both the DOE query and the FERC query report
   exactly 10,000, which is the cap rather than the total. Above it the count
   is a floor, and the adapter says so instead of passing off a ceiling as a
   sum.

3. **`total_pages` stops at 50.** Page-based paging therefore reaches
   50 × per_page documents and no further; the API's own answer to that is a
   `search_after` cursor, carried on every response as `next_page_url`.

4. **An unknown agency slug is an HTTP 400** whose body — `{"errors":
   {"agencies": "invalid value"}}` — the fetch path does not surface. So the
   agency list comes from the API's own agency record and is checked here,
   the same way the PostgREST adapter checks a column name.

The FERC question is a scope decision rather than an API property, and it
lives in the domain tool. What belongs here is the measurement behind it:
of everything the Federal Register tags as a Department of Energy document
since 2024, FERC is 20% of rules and 26% of proposed rules but 87% of
notices — 4,268 of 4,915 — because FERC's daily combined filing notices are
published through the same agency tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 10
MAX_ROWS = 100
COUNT_CAP = 10000
"""What `count` reports once the real total passes it. The API states no
larger number, so above this the count is a floor."""

MAX_PAGES = 50
"""`total_pages` never exceeds this, whatever `per_page` is."""

# The document types the API takes, in the API's own spelling. Checked here
# because a wrong one is an HTTP 400 with a body the fetch path drops.
DOCUMENT_TYPES = {
    "RULE": "a final rule",
    "PRORULE": "a proposed rule",
    "NOTICE": "a notice",
    "PRESDOCU": "a presidential document",
}

# What a search asks the API to return. Named explicitly rather than left to
# the default, because the default omits every field that makes one of these
# documents traceable: the docket it belongs to, the CFR parts it changes,
# its regulatory identifier, and where its full text is.
SEARCH_FIELDS = ("document_number", "title", "type", "abstract", "action",
                 "publication_date", "effective_on", "comments_close_on",
                 "agencies", "docket_ids", "regulation_id_numbers",
                 "cfr_references", "html_url", "pdf_url", "raw_text_url")


class FederalRegisterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    agency_slug: str
    """The parent agency this source is scoped to. Its child agencies come
    from the API's own record of it rather than from a list kept here."""
    excluded_child_slugs: list[str] = []
    """Child agencies whose documents this source does not serve, by slug.
    A scope decision recorded as data: the tool reports what it dropped and
    why, and cannot drop anything this list does not name."""
    exclusion_note: str | None = None

    @field_validator("exclusion_note", mode="after")
    @classmethod
    def _tidy(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v is not None else None


register_adapter_params("federal_register", FederalRegisterParams)


@dataclass
class AgencyTree:
    """The parent agency and its children, as the API describes them."""

    slug: str
    name: str
    agency_id: int
    child_slugs: list[str] = field(default_factory=list)

    def require(self, slug: str) -> str:
        known = [self.slug, *self.child_slugs]
        if slug not in known:
            raise InvalidQuery(
                f"agency {slug!r} is not {self.name} or one of its "
                f"{len(self.child_slugs)} child agencies. Known: "
                f"{', '.join(known)}.")
        return slug


@dataclass
class Document:
    document_number: str
    title: str
    type: str
    publication_date: str | None
    agencies: list[str]
    raw: dict[str, Any]

    @property
    def agency_slugs(self) -> set[str]:
        return {str(a.get("slug")) for a in self.raw.get("agencies") or []
                if isinstance(a, dict) and a.get("slug")}


@dataclass
class DocumentPage:
    documents: list[Document]
    count: int | None
    count_is_capped: bool
    page: int
    per_page: int
    pages_available: int
    pages_reachable: int
    """`total_pages`, which the API holds at 50 however many documents
    match. Kept beside the count so a caller can be told that the rest of a
    large result set is not reachable by paging."""


def _document(raw: dict[str, Any]) -> Document:
    agencies = [str(a.get("name") or a.get("raw_name") or "")
                for a in raw.get("agencies") or [] if isinstance(a, dict)]
    return Document(
        document_number=str(raw.get("document_number") or ""),
        title=str(raw.get("title") or ""),
        type=str(raw.get("type") or ""),
        publication_date=raw.get("publication_date"),
        agencies=[a for a in agencies if a], raw=raw)


class FederalRegisterAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> FederalRegisterParams:
        return FederalRegisterParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: FederalRegisterParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def agencies(self, manifest: SourceManifest
                       ) -> Fetched[AgencyTree]:
        """The agency and its children, from the API's own record.

        Read rather than listed so that a department reorganization shows up
        as a changed answer instead of as an HTTP 400. DOE's tree has
        fourteen children, and which of them are in scope is a separate
        question the manifest answers.
        """
        params = self.params_for(manifest)
        url = (params.base_url.rstrip("/") + "/agencies/"
               + params.agency_slug)
        result = await self._fetch(manifest, params, url, {})
        body = result.payload
        if not isinstance(body, dict) or "slug" not in body:
            raise SourceSchemaChanged(
                f"{manifest.id} returned no agency record at {url}.")
        children = body.get("child_slugs")
        tree = AgencyTree(
            slug=str(body["slug"]), name=str(body.get("name") or body["slug"]),
            agency_id=int(body.get("id") or 0),
            child_slugs=[str(c) for c in children]
            if isinstance(children, list) else [])
        log_source_call(manifest, "agencies", {}, len(tree.child_slugs))
        return Fetched.of(result, tree)

    async def search(self, manifest: SourceManifest, *,
                     agency: str | None = None, term: str = "",
                     document_types: list[str] | None = None,
                     docket_id: str = "", published_since: str = "",
                     published_until: str = "", rows: int = DEFAULT_ROWS,
                     page: int = 1) -> Fetched[DocumentPage]:
        params = self.params_for(manifest)
        if rows < 2 or rows > MAX_ROWS:
            raise InvalidQuery(
                f"rows must be between 2 and {MAX_ROWS}; got {rows}. One is "
                "excluded because the API answers per_page=1 with twenty "
                "documents and says nothing about having done so.")
        if page < 1:
            raise InvalidQuery(f"page starts at 1; got {page}")
        if page > MAX_PAGES:
            raise InvalidQuery(
                f"the API serves at most {MAX_PAGES} pages of any result "
                f"set, so page {page} does not exist. Narrow the search with "
                "a date range or a document type rather than paging further.")
        for value in document_types or []:
            if value not in DOCUMENT_TYPES:
                raise InvalidQuery(
                    f"document type {value!r} is not one the API takes. "
                    + "; ".join(f"{k} is {v}"
                               for k, v in DOCUMENT_TYPES.items()) + ".")

        query: dict[str, Any] = {
            "conditions[agencies][]": agency or params.agency_slug,
            "fields[]": list(SEARCH_FIELDS),
            "per_page": rows, "page": page, "order": "newest",
        }
        if term.strip():
            query["conditions[term]"] = term.strip()
        if document_types:
            query["conditions[type][]"] = list(document_types)
        if docket_id.strip():
            query["conditions[docket_id]"] = docket_id.strip()
        if published_since.strip():
            query["conditions[publication_date][gte]"] = published_since.strip()
        if published_until.strip():
            query["conditions[publication_date][lte]"] = published_until.strip()

        url = params.base_url.rstrip("/") + "/documents.json"
        result = await self._fetch(manifest, params, url, query)
        body = result.payload
        if not isinstance(body, dict) or "results" not in body:
            raise SourceSchemaChanged(
                f"{manifest.id} returned no 'results' array; the Federal "
                "Register document search response shape has changed.")
        documents = [_document(r) for r in body["results"]
                     if isinstance(r, dict)]
        count = body.get("count")
        count = count if isinstance(count, int) else None
        pages = body.get("total_pages")
        pages = pages if isinstance(pages, int) else 0
        log_source_call(manifest, "search", query, len(documents))
        return Fetched.of(result, DocumentPage(
            documents=documents, count=count,
            count_is_capped=count is not None and count >= COUNT_CAP,
            page=page, per_page=rows,
            pages_available=(-(-count // rows) if count else 0),
            pages_reachable=pages))

    async def get_document(self, manifest: SourceManifest,
                           document_number: str) -> Fetched[Document]:
        params = self.params_for(manifest)
        number = document_number.strip()
        if not number:
            raise InvalidQuery(
                "a document number is required, in the API's own form: "
                "'2026-17979', which is the year and the sequence within it.")
        url = (params.base_url.rstrip("/") + "/documents/" + number
               + ".json")
        result = await self._fetch(manifest, params, url,
                                   {"fields[]": list(SEARCH_FIELDS)})
        body = result.payload
        if not isinstance(body, dict) or not body.get("document_number"):
            raise SourceSchemaChanged(
                f"{manifest.id} returned no document record for {number!r}.")
        log_source_call(manifest, "get_document", {"document_number": ""}, 1)
        return Fetched.of(result, _document(body))

    async def _fetch(self, manifest: SourceManifest,
                     params: FederalRegisterParams, url: str,
                     query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
