"""The `essdive` adapter: a schema.org dataset catalog behind a search API.

ESS-DIVE is DOE's repository for Environmental System Science data — the
observational and experimental record from BER's terrestrial ecosystem
programmes, held at LBNL. Its package API answers in JSON-LD: every record
is a schema.org `Dataset` with creators, funders, licence, keywords, and
spatial and temporal coverage already typed, which is more structure than
any other catalog in this registry publishes.

Two properties of the service, both verified live 2026-09-09, decide the
shape of what follows:

1. **An unknown query parameter is ignored in silence.** `notaparam=x`
    returns the unfiltered 1,571 records at HTTP 200. The service does say
    what it did, though: every response echoes the query it actually ran in
    a `query` block. So this adapter sends only names it knows, and then
    checks the echo — a filter that goes out and does not come back is a
    changed service, and finding out from the echo is the difference
    between an alarm and a wrong answer under a filter that stopped
    existing.

2. **`rowStart` is 1-based and validated.** `rowStart=0` is an HTTP 400
    naming the field, which makes it the one paging mistake here that
    cannot pass silently. Offsets are converted at this boundary so nothing
    above has to hold two conventions at once.

3. **An empty result set arrives as HTTP 404**, carrying
    `{"detail": "No datasets were found."}`. That is the inversion this
    project's coverage dimensions exist to prevent — an outage and an empty
    result are different answers, and the fetch path reports a 404 as the
    first. So a search translates it back into an empty page. A package
    lookup does not: there, 404 means the id is wrong, which is exactly what
    it says.

Creator and editor records carry email addresses. They are dropped here
rather than passed on: a publisher exposing a researcher's address through
its own API is not the same act as this project copying it into an answer,
and nothing a caller asks of a dataset catalog needs one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.envelope import utc_now_iso
from ..core.errors import (InvalidQuery, SourceSchemaChanged,
                           SourceUnavailable)
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   total_or_none,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 10
MAX_ROWS = 100


class EssDiveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    packages_path: str = "/packages"
    filters: dict[str, str] = Field(default_factory=dict)
    """Query-parameter name -> what it matches, in the publisher's terms.
    Held as data because the service ignores a name it does not know, so
    this table is what separates a supported filter from a typo that
    returns the whole catalog."""
    sorts: list[str] = Field(default_factory=list)
    """The sort expressions this source has been verified against. A caller
    cannot invent one: an unknown sort is ignored like any other unknown
    parameter, and the result would silently be in date order."""


register_adapter_params("essdive", EssDiveParams)


@dataclass(frozen=True)
class Person:
    name: str
    affiliation: str | None = None
    orcid: str | None = None


@dataclass
class Package:
    package_id: str
    doi: str | None
    name: str
    description: str | None
    view_url: str | None
    date_uploaded: str | None
    date_modified: str | None
    date_published: str | None
    citation: str | None
    creators: list[Person] = field(default_factory=list)
    funders: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    project: str | None = None
    license_url: str | None = None
    temporal_coverage: dict[str, str] = field(default_factory=dict)
    places: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


@dataclass
class PackagePage:
    packages: list[Package]
    total: int | None
    """The publisher's count of matches, or None when the response carried
    none. None reaches the caller as `pagination: unknown`, never as zero."""
    row_start: int
    """1-based, as the publisher counts."""
    page_size: int
    applied_query: dict[str, Any]
    """The service's own echo of the query it ran. Carried through to the
    caller so that what was searched is visible next to what came back."""


def _text(node: Any) -> str | None:
    if isinstance(node, str):
        return node.strip() or None
    return None


def _as_list(node: Any) -> list[Any]:
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _person(node: Any) -> Person | None:
    """One creator, without the email address the record carries."""
    if not isinstance(node, dict):
        return None
    given = _text(node.get("givenName")) or ""
    family = _text(node.get("familyName")) or ""
    name = _text(node.get("name")) or " ".join(p for p in (given, family) if p)
    if not name:
        return None
    identifier = _text(node.get("@id")) or ""
    return Person(name=name, affiliation=_text(node.get("affiliation")),
                  orcid=identifier if "orcid.org" in identifier else None)


def _places(node: Any) -> list[str]:
    out = []
    for place in _as_list(node):
        if isinstance(place, dict):
            described = _text(place.get("description")) or _text(
                place.get("name"))
            if described:
                out.append(described)
    return out


def parse_package(entry: dict, source_id: str) -> Package:
    dataset = entry.get("dataset")
    if not isinstance(dataset, dict):
        raise SourceSchemaChanged(
            f"{source_id}: a package carries no 'dataset' object. The "
            "response is no longer the JSON-LD shape this reads.")
    identifier = _text(dataset.get("@id"))
    name = _text(dataset.get("name"))
    if name is None:
        raise SourceSchemaChanged(
            f"{source_id}: a dataset record carries no name.")
    provider = dataset.get("provider")
    temporal = dataset.get("temporalCoverage")
    return Package(
        package_id=_text(entry.get("id")) or identifier or name,
        doi=identifier,
        name=name,
        description=_text(dataset.get("description")),
        view_url=_text(entry.get("viewUrl")),
        date_uploaded=_text(entry.get("dateUploaded")),
        date_modified=_text(entry.get("dateModified")),
        date_published=_text(dataset.get("datePublished")),
        citation=_text(entry.get("citation")),
        creators=[p for p in (_person(c)
                              for c in _as_list(dataset.get("creator")))
                  if p is not None],
        funders=[n for n in (_text(f.get("name"))
                             for f in _as_list(dataset.get("funder"))
                             if isinstance(f, dict)) if n],
        keywords=[k for k in (_text(k)
                              for k in _as_list(dataset.get("keywords"))) if k],
        project=(_text(provider.get("name"))
                 if isinstance(provider, dict) else None),
        license_url=_text(dataset.get("license")),
        temporal_coverage={k: str(v) for k, v in (temporal or {}).items()
                           if k in ("startDate", "endDate")
                           } if isinstance(temporal, dict) else {},
        places=_places(dataset.get("spatialCoverage")),
        methods=[m for m in (_text(m) for m in _as_list(
            dataset.get("measurementTechnique"))) if m])


class EssDiveAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> EssDiveParams:
        return EssDiveParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: EssDiveParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def search(self, manifest: SourceManifest, *,
                     filters: dict[str, str] | None = None,
                     sort: str = "", rows: int = DEFAULT_ROWS,
                     offset: int = 0) -> Fetched[PackagePage]:
        params = self.params_for(manifest)
        query = self._checked_filters(filters or {}, params)
        if sort:
            if sort not in params.sorts:
                raise InvalidQuery(
                    f"sort {sort!r} is not one of {sorted(params.sorts)}. An "
                    "unknown sort is ignored by this service rather than "
                    "refused, so the answer would come back in date order "
                    "looking like the order you asked for.")
            query["sort"] = sort
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}.")
        if offset < 0:
            raise InvalidQuery("offset must be zero or more.")
        # The publisher counts rows from 1 and rejects 0 outright; callers
        # here pass an offset like everywhere else in this project.
        query |= {"pageSize": str(rows), "rowStart": str(offset + 1)}

        url = f"{params.base_url.rstrip('/')}{params.packages_path}"
        try:
            result = await self._fetch(manifest, params, url, query)
        except SourceUnavailable as refusal:
            if refusal.status != 404:
                raise
            # This service reports an empty result set as HTTP 404 with
            # {"detail": "No datasets were found."}. Left alone that reaches
            # a caller as SourceUnavailable — "outage, not an empty result" —
            # which is the exact inversion the coverage dimensions exist to
            # prevent: it would send someone to check a service that is
            # running and answered correctly.
            log_source_call(manifest, "search", query, 0)
            return Fetched(value=PackagePage(packages=[], total=0,
                                             row_start=offset + 1,
                                             page_size=rows,
                                             applied_query=dict(query)),
                           retrieved_at=utc_now_iso(), cache_age_seconds=0,
                           request_url=url, from_cache=False)
        page = self._parse_page(result.payload, manifest.id, query)
        log_source_call(manifest, "search", query, len(page.packages))
        return Fetched.of(result, page)

    async def get_package(self, manifest: SourceManifest,
                          package_id: str) -> Fetched[Package]:
        params = self.params_for(manifest)
        identifier = package_id.strip()
        if not identifier:
            raise InvalidQuery("a package id is required.")
        if "/" in identifier or identifier.startswith("."):
            raise InvalidQuery(
                f"{identifier!r} is not an ESS-DIVE package id. Ids look "
                "like 'ess-dive-<hash>-<timestamp>' and come from "
                "earth.search_datasets.")
        url = (f"{params.base_url.rstrip('/')}{params.packages_path}/"
               f"{identifier}")
        result = await self._fetch(manifest, params, url, {})
        if not isinstance(result.payload, dict):
            raise SourceSchemaChanged(
                f"{manifest.id}: a package lookup returned "
                f"{type(result.payload).__name__}, not an object.")
        package = parse_package(result.payload, manifest.id)
        log_source_call(manifest, "get_package", {"id": identifier}, 1)
        return Fetched.of(result, package)

    @staticmethod
    def _checked_filters(filters: dict[str, str],
                         params: EssDiveParams) -> dict[str, str]:
        query = {}
        for name, value in filters.items():
            text = str(value).strip()
            if not text:
                continue
            if name not in params.filters:
                raise InvalidQuery(
                    f"{name!r} is not a filter this source takes. It has "
                    f"{', '.join(sorted(params.filters))}, and it ignores a "
                    "parameter it does not know rather than refusing it — "
                    "which would return the whole catalog under your "
                    "filter.")
            query[name] = text
        return query

    @staticmethod
    def _parse_page(payload: Any, source_id: str,
                    sent: dict[str, str]) -> PackagePage:
        if not isinstance(payload, dict) or "result" not in payload:
            raise SourceSchemaChanged(
                f"{source_id}: the package search returned no 'result' "
                "array. The response shape has changed.")
        echoed = payload.get("query")
        if not isinstance(echoed, dict):
            raise SourceSchemaChanged(
                f"{source_id}: the response carries no 'query' echo. That "
                "echo is how a filter this service silently dropped becomes "
                "visible, so an answer without it cannot be trusted to be "
                "the answer that was asked for.")
        dropped = [name for name in sent
                   if name not in ("pageSize", "rowStart")
                   and echoed.get(name) in (None, [], "")]
        if dropped:
            raise SourceSchemaChanged(
                f"{source_id}: filter(s) {', '.join(sorted(dropped))} were "
                "sent and do not appear in the service's own echo of the "
                "query it ran. It ignores parameters it does not "
                "understand, so this answer would be the unfiltered catalog "
                "presented as a filtered search.")
        return PackagePage(
            packages=[parse_package(e, source_id)
                      for e in payload["result"] if isinstance(e, dict)],
            total=total_or_none(payload.get("total")),
            row_start=int(payload.get("rowStart") or 1),
            page_size=int(payload.get("pageSize") or 0),
            applied_query={k: v for k, v in echoed.items() if v is not None})

    async def _fetch(self, manifest: SourceManifest, params: EssDiveParams,
                     url: str, query: dict[str, str]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
