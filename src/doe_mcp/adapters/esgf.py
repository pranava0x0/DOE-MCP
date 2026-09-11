"""The `esgf` adapter: the ESGF 1.5 bridge over the CMIP climate archive.

The Earth System Grid Federation is how the world's climate-model output is
published, and CMIP6 alone is 14.7 million dataset records. DOE's stake is
direct: LLNL ran the US federation node for two decades and ORNL runs it now.
The index this reads is a search bridge in front of Globus Search that speaks
the old `esg-search` Solr dialect, which is what every existing ESGF client
knows.

Three findings from the 2026-09-09 probe shape this adapter, and the first
is a host move that a naive check would have got backwards:

1. **The endpoint moved from LLNL to ORNL and the paths do not agree.**
    `esgf-node.llnl.gov/esg-search/search` answers 302 to
    `esgf-node.ornl.gov/esgf-1-5-bridge`, which is the live service. But
    `esgf-node.ornl.gov/esg-search/search` — the same path on the new host —
    answers **HTTP 200 with the React shell of the web portal**. A probe
    that recorded status codes would file the working old host as a
    redirect and the broken new path as healthy. This is the case AGENTS.md
    names: a 200 carrying an HTML shell says nothing at all, because a
    single-page application serves its index for any path.

2. **Paging stops at 9,999.** The bridge's own OpenAPI document caps
    `offset` there and says why: Globus Search allows no more. Against
    14.7 million records that is not a paging limit, it is a statement that
    the archive is searched by narrowing rather than by walking, and the
    adapter refuses a deeper offset with that sentence rather than letting
    the service refuse it later.

3. **One fact, two field names.** A dataset's last covered timestamp is
    `datetime_stop` on some records and `datetime_end` on others — CanESM5
    and GFDL-ESM4 respectively, in the same index on the same day. Both are
    read.

4. **The default search returns retracted and superseded datasets first.**
    An unqualified CMIP6 query for one model, experiment, and variable
    returned 456 records whose first two were `retracted: true`; the same
    query with the publisher's `latest` flag returned 366 and none. A
    retraction in CMIP6 is a modelling centre withdrawing output, often for
    a science error, so `latest` defaults to on here and every row carries
    both flags. The count of what the flag removed is reported rather than
    quietly dropped.

The bridge publishes its own parameter list at an OpenAPI path, 110 names of
which about a hundred are facets. That document is read rather than copied
into a table here, for the reason the PostgREST adapter reads its schema: a
facet list that lives in this repository is a facet list that goes stale
without anything failing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   total_or_none,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 10
MAX_ROWS = 100
SOLR_JSON = "application/solr+json"


class EsgfParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    schema_url: str
    """Where the bridge publishes its own parameter list. A manifest field
    rather than `base_url` plus a guessed suffix: decision 0020 is that a
    locator a publisher did not give is not provenance, and this one was
    read from the running service before it was written down."""
    control_params: list[str] = Field(default_factory=list)
    """Parameters that steer the request rather than filter the archive
    (`limit`, `format`, `facets`...). Everything else the schema lists is a
    facet a caller may search on, so the two are separated once here."""
    project_facet: str = "project"
    max_offset: int = 9999
    retired_endpoint: str | None = None
    """The `esg-search` URL this service replaced, kept so a caller holding
    it is told where it went rather than left with a redirect to follow."""


register_adapter_params("esgf", EsgfParams)


@dataclass
class BridgeSchema:
    facets: dict[str, str]
    """Facet name -> the publisher's own description of it."""
    controls: dict[str, str]
    version: str | None

    def check(self, names: list[str]) -> None:
        unknown = [n for n in names if n not in self.facets]
        if unknown:
            raise InvalidQuery(
                f"unknown ESGF facet(s): {', '.join(sorted(unknown))}. The "
                f"bridge publishes {len(self.facets)} searchable facets; "
                "call climate.discover_facets to list them, or to see the "
                "values one of them takes.")


@dataclass
class ClimateDataset:
    dataset_id: str
    title: str
    project: str | None
    source_id: str | None
    experiment_id: str | None
    experiment_title: str | None
    variables: list[str]
    variable_long_names: list[str]
    variable_units: list[str]
    frequency: str | None
    realm: str | None
    nominal_resolution: str | None
    institution_id: str | None
    variant_label: str | None
    grid_label: str | None
    version: str | None
    datetime_start: str | None
    datetime_stop: str | None
    number_of_files: int | None
    size_bytes: int | None
    data_node: str | None
    access_methods: list[str]
    citation_url: str | None
    further_info_url: str | None
    latest: bool | None
    retracted: bool | None
    replica: bool | None


@dataclass
class SearchPage:
    datasets: list[ClimateDataset]
    num_found: int | None
    """Solr's own count for the query, or None when the response carried
    none. None reaches the caller as `pagination: unknown`, never as zero."""
    offset: int
    applied_filters: list[str]
    """The service's own `fq` echo: the filter clauses it actually ran."""
    facet_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def retracted_count(self) -> int:
        return sum(1 for d in self.datasets if d.retracted)


def _first(node: Any) -> Any:
    """ESGF's Solr documents hold most fields as single-element lists."""
    if isinstance(node, list):
        return node[0] if node else None
    return node


def _strings(node: Any) -> list[str]:
    if node is None:
        return []
    values = node if isinstance(node, list) else [node]
    return [str(v) for v in values if v is not None]


def _int(node: Any) -> int | None:
    value = _first(node)
    return int(value) if isinstance(value, (int, float)) else None


def _str(node: Any) -> str | None:
    value = _first(node)
    return str(value) if value is not None else None


def parse_dataset(doc: dict, source_id: str) -> ClimateDataset:
    identifier = doc.get("instance_id") or doc.get("id")
    if not identifier:
        raise SourceSchemaChanged(
            f"{source_id}: a search hit carries neither instance_id nor id. "
            "Nothing else in the record identifies a dataset.")
    return ClimateDataset(
        dataset_id=str(identifier),
        title=str(doc.get("title") or identifier),
        project=_str(doc.get("project")),
        source_id=_str(doc.get("source_id")),
        experiment_id=_str(doc.get("experiment_id")),
        experiment_title=_str(doc.get("experiment_title")),
        variables=_strings(doc.get("variable_id")) or _strings(
            doc.get("variable")),
        variable_long_names=_strings(doc.get("variable_long_name")),
        variable_units=_strings(doc.get("variable_units")),
        frequency=_str(doc.get("frequency")),
        realm=_str(doc.get("realm")),
        nominal_resolution=_str(doc.get("nominal_resolution")),
        institution_id=_str(doc.get("institution_id")),
        variant_label=_str(doc.get("variant_label")),
        grid_label=_str(doc.get("grid_label")),
        version=_str(doc.get("version")),
        datetime_start=_str(doc.get("datetime_start")),
        # Two field names for one fact. CanESM5's records end in
        # `datetime_stop` and GFDL-ESM4's in `datetime_end`, in the same
        # index on the same day, and reading only the first left the end of
        # the covered period silently null for a large share of the archive.
        # A fixture recorded from one model could not have caught it, which
        # is why one is recorded from each.
        datetime_stop=_str(doc.get("datetime_stop")
                           or doc.get("datetime_end")),
        number_of_files=_int(doc.get("number_of_files")),
        size_bytes=_int(doc.get("size")),
        data_node=_str(doc.get("data_node")),
        access_methods=_strings(doc.get("access")),
        citation_url=_str(doc.get("citation_url")),
        further_info_url=_str(doc.get("further_info_url")),
        latest=_first(doc.get("latest")),
        retracted=_first(doc.get("retracted")),
        replica=_first(doc.get("replica")))


class EsgfAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> EsgfParams:
        return EsgfParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: EsgfParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def describe(self,
                       manifest: SourceManifest) -> Fetched[BridgeSchema]:
        """The bridge's own parameter list, split into facets and controls."""
        params = self.params_for(manifest)
        result = await self._fetch(manifest, params, params.schema_url, {})
        document = result.payload
        if not isinstance(document, dict) or "paths" not in document:
            raise SourceSchemaChanged(
                f"{manifest.id}: the schema URL did not return an OpenAPI "
                "document. On this host a wrong path answers 200 with the "
                "web portal's HTML shell, so a non-document here is a moved "
                "endpoint rather than an outage.")
        entries = self._parameters(document, manifest.id)
        controls = {n: d for n, d in entries.items()
                    if n in set(params.control_params)}
        facets = {n: d for n, d in entries.items() if n not in controls}
        if not facets:
            raise SourceSchemaChanged(
                f"{manifest.id}: the published parameter list holds no "
                "facets once the control parameters are removed.")
        version = (document.get("info") or {}).get("version")
        log_source_call(manifest, "describe", {}, len(facets))
        return Fetched.of(result, BridgeSchema(
            facets=facets, controls=controls,
            version=str(version) if version else None))

    @staticmethod
    def _parameters(document: dict, source_id: str) -> dict[str, str]:
        for _path, operations in (document.get("paths") or {}).items():
            get = (operations or {}).get("get") or {}
            listed = get.get("parameters") or []
            if len(listed) > 1:
                return {p["name"]: (p.get("description") or "").strip()
                        for p in listed if p.get("name")}
        raise SourceSchemaChanged(
            f"{source_id}: no operation in the published schema declares a "
            "parameter list, so there is nothing to validate a facet "
            "against.")

    async def search(self, manifest: SourceManifest, *,
                     schema: BridgeSchema,
                     filters: dict[str, str] | None = None,
                     facets: list[str] | None = None,
                     rows: int = DEFAULT_ROWS, offset: int = 0,
                     latest: bool | None = True,
                     replica: bool | None = None) -> Fetched[SearchPage]:
        params = self.params_for(manifest)
        clauses = {k: v.strip() for k, v in (filters or {}).items()
                   if v and v.strip()}
        schema.check(list(clauses))
        wanted = [f.strip() for f in (facets or []) if f.strip()]
        schema.check(wanted)
        if rows < 0 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 0 and {MAX_ROWS}.")
        if offset < 0 or offset > params.max_offset:
            raise InvalidQuery(
                f"offset must be between 0 and {params.max_offset}, which is "
                "the bridge's own ceiling — the search index behind it "
                "allows no deeper page. With millions of datasets in the "
                "archive, a question that runs past it is answered by "
                "narrowing the facets rather than by paging.")

        query: dict[str, Any] = dict(clauses)
        query |= {"limit": str(rows), "offset": str(offset),
                  "format": SOLR_JSON}
        # Both flags are three-valued and the third value is the parameter's
        # absence. `latest=false` does not mean "no filter" — it means "only
        # the versions that are NOT current", which for one model, experiment
        # and variable was 90 records where absent was 456 and true was 366.
        # A boolean argument mapped onto two of the three states would make
        # "include superseded output" mean "return nothing but superseded
        # output".
        if latest is not None:
            query["latest"] = str(latest).lower()
        if replica is not None:
            query["replica"] = str(replica).lower()
        if wanted:
            query["facets"] = ",".join(wanted)
        result = await self._fetch(manifest, params, params.base_url, query)
        page = self._parse_page(result.payload, manifest.id, offset)
        log_source_call(manifest, "search", query, len(page.datasets))
        return Fetched.of(result, page)

    @staticmethod
    def _parse_page(payload: Any, source_id: str, offset: int) -> SearchPage:
        if not isinstance(payload, dict) or "response" not in payload:
            raise SourceSchemaChanged(
                f"{source_id}: the search returned no 'response' block. On "
                "this host that is usually the web portal's HTML shell "
                "answering a path the API no longer serves.")
        body = payload["response"]
        header = payload.get("responseHeader") or {}
        applied = header.get("params", {}).get("fq")
        return SearchPage(
            datasets=[parse_dataset(d, source_id)
                      for d in body.get("docs", []) if isinstance(d, dict)],
            num_found=total_or_none(body.get("numFound")),
            offset=int(body.get("start") if body.get("start") is not None
                       else offset),
            applied_filters=_strings(applied),
            facet_counts=_facet_counts(payload))

    async def _fetch(self, manifest: SourceManifest, params: EsgfParams,
                     url: str, query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)


def _facet_counts(payload: dict) -> dict[str, dict[str, int]]:
    """Solr's flat [value, count, value, count] lists, paired up."""
    fields = ((payload.get("facet_counts") or {}).get("facet_fields") or {})
    out: dict[str, dict[str, int]] = {}
    for name, flat in fields.items():
        if not isinstance(flat, list):
            continue
        out[name] = {str(flat[i]): int(flat[i + 1])
                     for i in range(0, len(flat) - 1, 2)
                     if isinstance(flat[i + 1], int)}
    return out
