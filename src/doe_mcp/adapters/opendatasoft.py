"""The `opendatasoft` adapter: the Explore v2.1 API.

ORNL's Open Energy Data Hub runs OpenDataSoft, and so do a number of other
public energy portals, which is the whole argument for a genre adapter: one
client, 185 datasets today at openenergyhub.ornl.gov alone, and any future
OpenDataSoft portal is a manifest rather than code.

Two levels, both verified live 2026-09-02:

  /api/explore/v2.1/catalog/datasets
      `{"total_count": N, "results": [...]}` — the catalog. Each result
      carries `dataset_id`, `metas` (title, theme, publisher, modified), and
      `fields` (the record schema).
  /api/explore/v2.1/catalog/datasets/{id}/records
      the rows of one dataset, same envelope shape.

The portal accepts ODSQL in `where`, which is a small query language, not
SQL. It is passed through rather than constructed here: building it from
caller input would be an injection surface, and the tools that use this
adapter take structured filters instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_LIMIT = 10
MAX_LIMIT = 100  # the portal's own ceiling for the catalog endpoint


class OpenDataSoftParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    portal_url: str | None = None


register_adapter_params("opendatasoft", OpenDataSoftParams)


@dataclass
class OdsDataset:
    dataset_id: str
    title: str
    description: str | None = None
    theme: list[str] = field(default_factory=list)
    publisher: str | None = None
    modified: str | None = None
    records_count: int | None = None
    keywords: list[str] = field(default_factory=list)
    license_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OdsPage:
    datasets: list[OdsDataset]
    total_count: int | None
    limit: int
    offset: int


def _meta(metas: dict[str, Any], group: str, key: str) -> Any:
    section = metas.get(group)
    if isinstance(section, dict):
        return section.get(key)
    return None


class OpenDataSoftAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> OpenDataSoftParams:
        return OpenDataSoftParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: OpenDataSoftParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def search_datasets(self, manifest: SourceManifest, *,
                              text: str = "", limit: int = DEFAULT_LIMIT,
                              offset: int = 0) -> Fetched[OdsPage]:
        params = self.params_for(manifest)
        if limit < 1 or limit > MAX_LIMIT:
            raise InvalidQuery(f"limit must be between 1 and {MAX_LIMIT}; "
                               f"got {limit}")
        query: dict[str, Any] = {"limit": limit, "offset": offset}
        if text.strip():
            # `where` with a search() call is the portal's own full-text
            # form. The caller's text is passed as a quoted literal, never
            # concatenated into a larger expression.
            escaped = text.strip().replace('"', '\\"')
            query["where"] = f'search(*, "{escaped}")'
        url = params.base_url.rstrip("/") + "/catalog/datasets"
        result = await self._fetch(manifest, params, url, query)
        body = result.payload
        if not isinstance(body, dict) or "results" not in body:
            raise SourceSchemaChanged(
                f"{manifest.id} returned no 'results' array; the "
                "OpenDataSoft Explore v2.1 response shape has changed.")
        datasets = [self._dataset(r) for r in body["results"]
                    if isinstance(r, dict)]
        total = body.get("total_count")
        log_source_call(manifest, "search_datasets", query, len(datasets))
        return Fetched.of(result, OdsPage(
            datasets=datasets,
            total_count=int(total) if isinstance(total, int) else None,
            limit=limit, offset=offset))

    @staticmethod
    def _dataset(raw: dict[str, Any]) -> OdsDataset:
        metas = raw.get("metas") or {}
        default = metas.get("default") if isinstance(metas, dict) else {}
        default = default if isinstance(default, dict) else {}
        theme = default.get("theme") or []
        keywords = default.get("keyword") or []
        return OdsDataset(
            dataset_id=str(raw.get("dataset_id") or ""),
            title=str(default.get("title") or raw.get("dataset_id") or ""),
            description=default.get("description"),
            theme=[str(t) for t in theme] if isinstance(theme, list) else [],
            publisher=default.get("publisher"),
            modified=default.get("modified"),
            records_count=default.get("records_count"),
            keywords=[str(k) for k in keywords] if isinstance(keywords, list)
            else [],
            license_name=default.get("license"),
            raw=raw)

    async def _fetch(self, manifest: SourceManifest,
                     params: OpenDataSoftParams, url: str,
                     query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
