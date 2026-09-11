"""The `json_document` adapter: one big JSON file, fetched and indexed.

Two of DOE's most useful cross-cutting catalogs are not APIs at all. They are
single documents:

  energy.gov/data.json   483 datasets, Project Open Data v1.1
  energy.gov/code.json   5,879 software releases, code.gov 2.0.0

Both are harvest-shaped, and both carry the measured-staleness problem that
made `catalog_vintage` a warning code. data.json holds entries last modified
in 2014 and 2015 alongside current ones; code.json's URL says "code.json"
while a 302 lands on `code-05-01-2025.json`, a file dated over a year before
this was written. Every answer from this adapter names the vintage it read,
because a catalog that looks live and is not is worse than one that admits
its age.

The document is fetched once per TTL and searched in memory. That is the
right shape at these sizes (874 KB and 7.5 MB) and would not be at ten times
them; a manifest whose document outgrows this gets a real index, which is
Gate E work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.errors import SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"


class JsonDocumentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_url: str
    collection_key: str
    """Where the list of entries lives in the document: 'dataset' for Project
    Open Data, 'releases' for code.gov."""
    title_key: str = "title"
    description_key: str = "description"
    modified_key: str = "modified"
    landing_key: str = "landingPage"
    identifier_key: str = "identifier"
    schema_note: str | None = None


register_adapter_params("json_document", JsonDocumentParams)


@dataclass
class CatalogEntry:
    entry_id: str
    title: str
    description: str | None = None
    modified: str | None = None
    landing_page: str | None = None
    keywords: list[str] = field(default_factory=list)
    publisher: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CatalogResult:
    entries: list[CatalogEntry]
    total_matches: int
    document_total: int
    document_vintage: str | None
    """The newest `modified` in the document, which is the honest answer to
    'how current is this catalog?' — not the time we fetched it."""


def _terms(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def _modified(value: Any) -> str | None:
    """Project Open Data puts a date string in `modified`; code.gov puts an
    OBJECT in `date` holding created / lastModified / metadataLastUpdated.

    Reading the object with str() produced entries whose date was the literal
    text "{'created'" — a truthy dict slipping past a falsy-check fallback.
    Handling both shapes here is the fix, and preferring metadataLastUpdated
    is deliberate: it is when the catalog entry was touched, which is what a
    catalog's vintage means, rather than when the software was written.
    """
    if isinstance(value, dict):
        for key in ("metadataLastUpdated", "lastModified", "created"):
            if value.get(key):
                return str(value[key])
        return None
    return str(value) if value else None


def _publisher_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    if isinstance(value, str):
        return value
    return None


class JsonDocumentAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> JsonDocumentParams:
        return JsonDocumentParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: JsonDocumentParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.document_url))

    async def search(self, manifest: SourceManifest, *, text: str = "",
                     limit: int = 10) -> Fetched[CatalogResult]:
        params = self.params_for(manifest)
        result = await self._fetch(manifest, params)
        rows = self._rows(manifest, params, result.payload)
        entries = [self._entry(params, r) for r in rows]
        vintage = max((e.modified for e in entries if e.modified),
                      default=None)

        terms = _terms(text)
        if terms:
            matched = [e for e in entries if self._matches(terms, e)]
        else:
            matched = entries
        matched.sort(key=lambda e: (e.modified or "", e.title), reverse=True)
        log_source_call(manifest, "search", {"text": text}, len(matched))
        return Fetched.of(result, CatalogResult(
            entries=matched[:limit], total_matches=len(matched),
            document_total=len(entries), document_vintage=vintage))

    @staticmethod
    def _matches(terms: list[str], entry: CatalogEntry) -> bool:
        """Every term must match a word by prefix. AND rather than OR, so
        extra words narrow the search instead of widening it, and prefix
        rather than substring so 'grid' does not match 'hybrid'."""
        haystack = " ".join(filter(None, [
            entry.title, entry.description or "", " ".join(entry.keywords),
            entry.publisher or ""]))
        words = _terms(haystack)
        return all(any(w.startswith(t) for w in words) for t in terms)

    def _rows(self, manifest: SourceManifest, params: JsonDocumentParams,
              payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            raise SourceSchemaChanged(
                f"{manifest.id} returned {type(payload).__name__} where a "
                "catalog document object was expected.")
        rows = payload.get(params.collection_key)
        if not isinstance(rows, list):
            raise SourceSchemaChanged(
                f"{manifest.id} has no {params.collection_key!r} array; the "
                "catalog document's schema has changed.")
        return [r for r in rows if isinstance(r, dict)]

    @staticmethod
    def _entry(params: JsonDocumentParams,
               raw: dict[str, Any]) -> CatalogEntry:
        keywords = raw.get("keyword") or raw.get("tags") or []
        return CatalogEntry(
            entry_id=str(raw.get(params.identifier_key)
                         or raw.get("name") or ""),
            title=str(raw.get(params.title_key) or raw.get("name") or ""),
            description=raw.get(params.description_key),
            modified=_modified(raw.get(params.modified_key)),
            landing_page=(raw.get(params.landing_key)
                          or raw.get("repositoryURL")),
            keywords=[str(k) for k in keywords]
            if isinstance(keywords, list) else [],
            publisher=(_publisher_name(raw.get("publisher"))
                       or raw.get("organization")),
            raw=raw)

    async def _fetch(self, manifest: SourceManifest,
                     params: JsonDocumentParams) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        url = params.document_url
        cached = self._cache.get(manifest.id, url, {}, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, {})
        return self._cache.put(manifest.id, url, {}, response.payload,
                               response.headers)
