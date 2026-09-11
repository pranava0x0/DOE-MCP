"""The `eia_v2` adapter: EIA's self-describing route tree.

EIA API v2 is shaped unlike anything else in this inventory, and the shape is
what makes it worth a dedicated adapter rather than a pile of per-dataset
tools. Every path is self-describing: `GET /v2/electricity` lists its child
routes, `GET /v2/electricity/rto/region-data` returns that dataset's
facets, frequencies, and data columns, and appending `/data` with a chosen
frequency and column set returns rows. So four tools cover the whole tree —
`discover` / `list_facets` / `get_data` / `get_series` — which is DECISIONS
0014's canonical shape for a self-describing API family, and the reason EIA
does not need twenty.

Two limits are the publisher's and are surfaced rather than worked around:

- **5,000 rows per response.** A caller who asks for a decade of hourly
  grid data gets the first 5,000 rows and a truncated pagination dimension,
  never a silent prefix presented as the whole answer.
- **A key is required, and it is EIA's own.** Not an api.data.gov key —
  EIA runs a separate registration. The error path says which, because
  supplying the wrong one produces a 403 that reads like an outage.

Verified live 2026-09-08: the request/response shapes below were written
from EIA's published v2 documentation on 2026-09-02 and confirmed against a
real key six days later, when the fixtures were recorded. That first live
call is also where decision 0022 came from. EIA-930's hourly route carries
four measurements under one `value` column, and a query that does not send
the `type` facet gets tomorrow's forecast sorted above last night's demand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.credentials import Credentials
from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   total_or_none,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

# EIA's own ceiling, not ours.
MAX_ROWS_PER_RESPONSE = 5000
DEFAULT_ROWS = 24


class EiaV2Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    credential_ref: str = "EIA_API_KEY"
    browse_url: str | None = None


register_adapter_params("eia_v2", EiaV2Params)


@dataclass
class EiaRoute:
    """A node in the route tree: either a branch with children or a leaf
    dataset with facets and data columns."""

    route_id: str
    name: str
    description: str | None = None
    children: list[dict[str, Any]] = field(default_factory=list)
    frequencies: list[dict[str, Any]] = field(default_factory=list)
    facets: list[dict[str, Any]] = field(default_factory=list)
    data_columns: dict[str, Any] = field(default_factory=dict)
    start_period: str | None = None
    end_period: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return bool(self.data_columns) and not self.children


@dataclass
class EiaRows:
    rows: list[dict[str, Any]]
    total: int | None
    truncated: bool


class EiaV2Adapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None,
                 credentials: Credentials | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()
        self._credentials = credentials

    @staticmethod
    def params_for(manifest: SourceManifest) -> EiaV2Params:
        return EiaV2Params.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: EiaV2Params) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    def _api_key(self, params: EiaV2Params) -> str:
        creds = self._credentials or Credentials.load()
        return creds.require(params.credential_ref)

    def uses_demo_key(self, params: EiaV2Params) -> bool:
        creds = self._credentials or Credentials.load()
        return creds.is_demo(params.credential_ref)

    async def describe_route(self, manifest: SourceManifest,
                             route: str = "") -> Fetched[EiaRoute]:
        """Walk the tree. An empty route is the root, which lists the top
        level (electricity, natural-gas, petroleum, coal, nuclear-outages,
        international, and so on)."""
        params = self.params_for(manifest)
        path = "/" + route.strip("/") if route.strip("/") else ""
        url = params.base_url.rstrip("/") + path
        result = await self._fetch(manifest, params, url, {})
        body = self._response_body(manifest, result.payload)
        node = EiaRoute(
            route_id=route.strip("/"),
            name=str(body.get("name") or route or "EIA API v2"),
            description=body.get("description"),
            children=[c for c in (body.get("routes") or [])
                      if isinstance(c, dict)],
            frequencies=[f for f in (body.get("frequency") or [])
                         if isinstance(f, dict)],
            facets=[f for f in (body.get("facets") or [])
                    if isinstance(f, dict)],
            data_columns=body.get("data") or {},
            start_period=body.get("startPeriod"),
            end_period=body.get("endPeriod"),
            raw=body)
        log_source_call(manifest, "describe_route", {"route": route},
                        len(node.children) or (1 if node.is_leaf else 0))
        return Fetched.of(result, node)

    async def get_data(self, manifest: SourceManifest, route: str, *,
                       frequency: str | None = None,
                       data_columns: list[str] | None = None,
                       facets: dict[str, list[str]] | None = None,
                       start: str | None = None, end: str | None = None,
                       rows: int = DEFAULT_ROWS, offset: int = 0,
                       sort_column: str | None = None,
                       sort_direction: str = "desc") -> Fetched[EiaRows]:
        params = self.params_for(manifest)
        if not route.strip("/"):
            raise InvalidQuery(
                "get_data needs a dataset route, e.g. "
                "'electricity/rto/region-data'. Use discover to walk the "
                "tree first; the root has no data of its own.")
        if rows < 1 or rows > MAX_ROWS_PER_RESPONSE:
            raise InvalidQuery(
                f"rows must be between 1 and EIA's own ceiling of "
                f"{MAX_ROWS_PER_RESPONSE}; got {rows}")

        query: dict[str, Any] = {"length": rows, "offset": offset}
        if frequency:
            query["frequency"] = frequency
        for i, column in enumerate(data_columns or []):
            # EIA's bracket encoding. Sending `data=value` instead returns
            # rows with no value column and no error, which reads as a
            # dataset with no data in it.
            query[f"data[{i}]"] = column
        for name, values in (facets or {}).items():
            for i, value in enumerate(values):
                query[f"facets[{name}][{i}]"] = value
        if start:
            query["start"] = start
        if end:
            query["end"] = end
        if sort_column:
            query["sort[0][column]"] = sort_column
            query["sort[0][direction]"] = sort_direction

        url = params.base_url.rstrip("/") + "/" + route.strip("/") + "/data"
        result = await self._fetch(manifest, params, url, query)
        body = self._response_body(manifest, result.payload)
        data = body.get("data")
        if not isinstance(data, list):
            raise SourceSchemaChanged(
                f"{manifest.id} returned no 'data' array for route "
                f"{route!r}; the v2 response shape has changed.")
        total = total_or_none(body.get("total"))
        log_source_call(manifest, "get_data", query, len(data))
        return Fetched.of(result, EiaRows(
            rows=[r for r in data if isinstance(r, dict)],
            total=total,
            truncated=bool(total is not None
                           and total > offset + len(data))))

    async def _fetch(self, manifest: SourceManifest, params: EiaV2Params,
                     url: str, query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        # The cache key deliberately excludes the api_key: two users with
        # different keys asking the same question are asking the same
        # question, and a key has no business in a cache key.
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        with_key = dict(query)
        with_key["api_key"] = self._api_key(params)
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, with_key)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)

    @staticmethod
    def _response_body(manifest: SourceManifest, payload: Any) -> dict:
        """v2 wraps everything in a `response` object."""
        if not isinstance(payload, dict):
            raise SourceSchemaChanged(
                f"{manifest.id} returned {type(payload).__name__} where an "
                "EIA v2 object was expected.")
        body = payload.get("response")
        if not isinstance(body, dict):
            raise SourceSchemaChanged(
                f"{manifest.id} response has no 'response' object; the v2 "
                "envelope has changed.")
        return body
