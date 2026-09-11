"""The `osti_family` adapter: one client, four APIs.

This adapter is the proof of the adapter layer. OSTI runs four public search
APIs — OSTI.GOV (4.38M records), DOE PAGES (260K), DOE Data Explorer (1.03M),
and DOE CODE (7.7K) — and three of them are the same API with different base
paths and record schemas. One client covers all four; the fourth differs
enough to need its own response reader, not its own adapter.

The shapes, verified live 2026-09-02:

  /records            bare JSON array; the total-match count is in the
                      `X-Total-Count` response HEADER, not the body
  /records/{osti_id}  a single-element array, not an object
  doecodeapi/search   `{"num_found": N, "start": 0, "docs": [...]}`
  doecodeapi/search/{code_id}
                      `{"metadata": {...}}`

Three quirks are handled here rather than left to callers, because each one
has already produced a wrong answer somewhere:

1. **No Accept header means XML.** A plain client gets XML from every one of
   these endpoints and a JSON parse failure downstream. The base fetcher
   always sends `Accept: application/json`.
2. **The count lives in a header.** A caller reading `len(payload)` sees the
   page size (20 by default) and concludes a query matched 20 things when it
   matched 4,381,076. Pagination coverage is computed from the header.
3. **DOE PAGES DOIs arrive as full URLs** while OSTI.GOV's arrive bare. Both
   are normalized to a bare DOI, and the URL form is kept as the locator.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

# Page sizes. The ceiling is ours, not OSTI's: a tool that can be asked for
# 1,000 records will be, and 1,000 bibliographic records is far past the
# envelope's 2K-token data budget however generous the publisher is.
DEFAULT_ROWS = 10
MAX_ROWS = 50


class OstiFlavor(str, enum.Enum):
    records = "records"
    """The /records shape: OSTI.GOV, DOE PAGES, DOE Data Explorer."""
    doecode = "doecode"
    """The Solr shape: DOE CODE."""


class OstiFamilyParams(BaseModel):
    """The manifest's `adapter:` block for this adapter type."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    flavor: OstiFlavor = OstiFlavor.records
    record_path: str = "/records"
    biblio_url_template: str | None = None
    """Where a human-readable record lives, `{id}`-templated. Emitted as the
    evidence locator only when the API itself did not supply one."""
    supported_filters: list[str] = Field(default_factory=list)
    """Query parameters this API accepts beyond the shared set. Validated
    against, so a filter that silently does nothing fails loudly instead of
    returning an unfiltered result the caller believes was filtered."""


register_adapter_params("osti_family", OstiFamilyParams)


# Filters every /records-shaped API accepts (osti.gov/api/v1/docs and the
# pages/dataexplorer variants, checked 2026-09-02).
SHARED_FILTERS = {
    "q", "title", "author", "doi", "osti_id", "identifier", "fulltext",
    "biblio", "sponsor_org", "research_org", "contributing_org", "source_id",
    "publication_date_start", "publication_date_end", "entry_date_start",
    "entry_date_end", "language", "country", "site_ownership_code", "subject",
    "has_fulltext", "product_type", "sort", "order",
}

# DOE CODE is Solr-backed and takes a different vocabulary.
DOECODE_FILTERS = {
    "all_fields", "software_title", "developers", "sort", "order",
    "project_type", "programming_languages", "licenses",
    "research_organization", "sponsoring_organization",
}


@dataclass
class OstiRecord:
    """A normalized record. Deliberately thin: the raw payload is kept
    alongside so nothing is lost, and only the fields tools actually read are
    lifted into named attributes."""

    record_id: str
    title: str
    raw: dict[str, Any]
    doi: str | None = None
    doi_url: str | None = None
    authors: list[str] = field(default_factory=list)
    publication_date: str | None = None
    product_type: str | None = None
    research_orgs: list[str] = field(default_factory=list)
    sponsor_orgs: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    description: str | None = None
    journal_name: str | None = None
    report_number: str | None = None
    site_ownership_code: str | None = None
    site_url: str | None = None
    biblio_url: str | None = None
    fulltext_url: str | None = None
    repository_link: str | None = None
    licenses: list[str] = field(default_factory=list)
    programming_languages: list[str] = field(default_factory=list)
    entry_date: str | None = None


@dataclass
class OstiPage:
    records: list[OstiRecord]
    total_matches: int | None
    """None when the API did not report one. Not zero, and not the page
    length: 'we do not know how many matched' is a different fact from
    'twenty matched'."""
    page: int
    rows: int


def _norm_doi(raw: str | None) -> tuple[str | None, str | None]:
    """Return (bare_doi, doi_url). DOE PAGES emits
    'https://doi.org/10.1016/...' where OSTI.GOV emits '10.2172/...'."""
    if not raw:
        return None, None
    value = raw.strip()
    if not value:
        return None, None
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.lower().startswith(prefix):
            bare = value[len(prefix):]
            return bare, f"https://doi.org/{bare}"
    return value, f"https://doi.org/{value}"


def _links(raw: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for link in raw.get("links") or []:
        if isinstance(link, dict) and link.get("rel") and link.get("href"):
            out[str(link["rel"])] = str(link["href"])
    return out


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                name = (item.get("organization_name") or item.get("name")
                        or item.get("last_name"))
                if name:
                    first = item.get("first_name")
                    out.append(f"{name}, {first}" if first else str(name))
            elif item is not None:
                out.append(str(item))
        return out
    return [str(value)]


class OstiFamilyAdapter:
    """Read-only. There is no write verb on this class to call."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    def _fetcher_for(self, manifest: SourceManifest,
                     params: OstiFamilyParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    @staticmethod
    def params_for(manifest: SourceManifest) -> OstiFamilyParams:
        return OstiFamilyParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def validate_filters(self, params: OstiFamilyParams,
                         filters: dict[str, Any]) -> None:
        allowed = (DOECODE_FILTERS if params.flavor is OstiFlavor.doecode
                   else SHARED_FILTERS) | set(params.supported_filters)
        unknown = sorted(set(filters) - allowed - {"rows", "page"})
        if unknown:
            raise InvalidQuery(
                f"filters {unknown} are not accepted by this API. Sending "
                "them would return an UNFILTERED result that looks filtered, "
                f"which is worse than an error. Accepted here: "
                f"{sorted(allowed)}")

    async def search(self, manifest: SourceManifest, *,
                     filters: dict[str, Any], rows: int = DEFAULT_ROWS,
                     page: int = 1) -> Fetched[OstiPage]:
        params = self.params_for(manifest)
        self.validate_filters(params, filters)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}; "
                               f"got {rows}")
        if page < 1:
            raise InvalidQuery(f"page starts at 1; got {page}")

        query = {k: v for k, v in filters.items()
                 if v is not None and v != ""}
        query["rows"] = rows
        query["page"] = page
        url = params.base_url.rstrip("/") + params.record_path
        result = await self._fetch(manifest, params, url, query)
        page_out = self._read_page(manifest, params, result, rows, page)
        log_source_call(manifest, "search", query, len(page_out.records))
        return Fetched.of(result, page_out)

    async def get_record(self, manifest: SourceManifest,
                         record_id: str) -> Fetched[OstiRecord | None]:
        params = self.params_for(manifest)
        if not record_id.strip():
            raise InvalidQuery("record_id is required")
        url = (params.base_url.rstrip("/") + params.record_path + "/"
               + record_id.strip())
        result = await self._fetch(manifest, params, url, {})
        records = self._read_records(manifest, params, result.payload)
        log_source_call(manifest, "get_record", {"record_id": record_id},
                        len(records))
        return Fetched.of(result, records[0] if records else None)

    async def _fetch(self, manifest: SourceManifest, params: OstiFamilyParams,
                     url: str, query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)

    def _read_page(self, manifest: SourceManifest, params: OstiFamilyParams,
                   result: FetchResult, rows: int, page: int) -> OstiPage:
        records = self._read_records(manifest, params, result.payload)
        if params.flavor is OstiFlavor.doecode:
            body = result.payload if isinstance(result.payload, dict) else {}
            total = body.get("num_found")
            total = int(total) if isinstance(total, int) else None
        else:
            # The count is in a header, not the body. Reading the body's
            # length instead is how a 4.38-million-record match gets reported
            # as 20.
            total = result.header_int("x-total-count")
        return OstiPage(records=records, total_matches=total, page=page,
                        rows=rows)

    def _read_records(self, manifest: SourceManifest,
                      params: OstiFamilyParams,
                      payload: Any) -> list[OstiRecord]:
        if params.flavor is OstiFlavor.doecode:
            raw_records = self._doecode_rows(manifest, payload)
            return [self._doecode_record(params, r) for r in raw_records]
        if not isinstance(payload, list):
            raise SourceSchemaChanged(
                f"{manifest.id} returned {type(payload).__name__} where the "
                "OSTI /records shape is a JSON array. The API changed, or a "
                "bot challenge is standing in for it; either way this is not "
                "an empty result.")
        return [self._records_record(params, r) for r in payload
                if isinstance(r, dict)]

    @staticmethod
    def _doecode_rows(manifest: SourceManifest, payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            raise SourceSchemaChanged(
                f"{manifest.id} returned a non-object body where DOE CODE's "
                "Solr shape was expected.")
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            return [payload["metadata"]]      # single-record GET
        docs = payload.get("docs")
        if docs is None:
            raise SourceSchemaChanged(
                f"{manifest.id} response has neither 'docs' nor 'metadata'; "
                "DOE CODE's response shape has changed.")
        return [d for d in docs if isinstance(d, dict)]

    @staticmethod
    def _records_record(params: OstiFamilyParams,
                        raw: dict[str, Any]) -> OstiRecord:
        links = _links(raw)
        record_id = str(raw.get("osti_id") or raw.get("id") or "")
        doi, doi_url = _norm_doi(raw.get("doi"))
        biblio = links.get("citation")
        if not biblio and params.biblio_url_template and record_id:
            biblio = params.biblio_url_template.format(id=record_id)
        # The publisher's own link or nothing. A `fulltext_url_template`
        # field used to synthesize one from the record id when the API
        # supplied none, and it was removed on 2026-09-08 because it was
        # wrong in both directions: where the API DOES supply a link its
        # href is byte-identical to what the template produced, so the
        # template added nothing; and where the API supplies none, the
        # synthesized purl 404s. Two of three checked did. The tool was
        # therefore telling callers "the full text is here" about a URL
        # nobody had claimed existed, and stamping it as a fulltext_pdf
        # access recipe. Absence of a link is a fact to report, not a gap
        # to fill by guessing.
        fulltext = links.get("fulltext")
        return OstiRecord(
            record_id=record_id,
            title=str(raw.get("title") or "").strip(),
            raw=raw, doi=doi, doi_url=doi_url,
            authors=_as_list(raw.get("authors")),
            publication_date=raw.get("publication_date"),
            product_type=raw.get("product_type"),
            research_orgs=_as_list(raw.get("research_orgs")),
            sponsor_orgs=_as_list(raw.get("sponsor_orgs")),
            subjects=_as_list(raw.get("subjects")),
            description=raw.get("description"),
            journal_name=raw.get("journal_name"),
            report_number=raw.get("report_number"),
            site_ownership_code=raw.get("site_ownership_code"),
            site_url=raw.get("site_url"),
            biblio_url=biblio, fulltext_url=fulltext,
            entry_date=raw.get("entry_date"))

    @staticmethod
    def _doecode_record(params: OstiFamilyParams,
                        raw: dict[str, Any]) -> OstiRecord:
        links = _links(raw)
        record_id = str(raw.get("code_id") or "")
        doi, doi_url = _norm_doi(raw.get("doi"))
        biblio = links.get("citation")
        if not biblio and params.biblio_url_template and record_id:
            biblio = params.biblio_url_template.format(id=record_id)
        return OstiRecord(
            record_id=record_id,
            title=str(raw.get("software_title") or "").strip(),
            raw=raw, doi=doi, doi_url=doi_url,
            authors=_as_list(raw.get("developers")),
            publication_date=raw.get("release_date"),
            product_type="Software",
            research_orgs=_as_list(raw.get("research_organizations")),
            sponsor_orgs=_as_list(raw.get("sponsoring_organizations")),
            subjects=_as_list(raw.get("project_keywords")),
            description=raw.get("description"),
            site_ownership_code=raw.get("site_ownership_code"),
            biblio_url=biblio,
            repository_link=raw.get("repository_link"),
            licenses=_as_list(raw.get("licenses")),
            programming_languages=_as_list(raw.get("programming_languages")),
            entry_date=raw.get("date_record_updated"))
