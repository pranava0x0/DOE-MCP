"""The `postgrest` adapter: a PostgreSQL table published as REST.

PostgREST turns a database schema into an HTTP API with no code in between,
which is why two USGS-hosted, LBNL co-produced, DOE-funded databases — the
US Wind Turbine Database and the US Large-Scale Solar Photovoltaic Database —
speak exactly the same dialect. Filters are `column=operator.value` query
parameters, ordering is `order=column.direction`, paging is `limit`/`offset`,
and the service publishes its own OpenAPI document at the base path. One
genre, two sources today, and any further PostgREST endpoint is a manifest
rather than code.

Three properties of this endpoint, all verified live 2026-09-08, decide the
shape of what follows:

1. **Both databases are one PostgreSQL schema behind two path aliases.**
   `/api/uswtdb/v1/` and `/api/uspvdb/v1/` return the SAME OpenAPI document
   and both serve BOTH tables: `uswtdb/v1/projects` answers with 6,611 solar
   facilities rather than with wind projects. A client that inferred its
   table from the path would return solar rows under a wind source's
   provenance and be wrong in a way no status code reports. The table is a
   manifest field, read from `adapter.table` and never derived, and a source
   whose declared table is missing from the published schema fails loudly.

2. **A query with no `limit` returns the whole table.** There is no server
   ceiling, and the turbine table is 75,727 rows. Every request this adapter
   sends carries an explicit limit.

3. **`Content-Range` carries no total unless the request asks for one.**
   Without a `Prefer: count=exact` header the header reads `0-1/*`, and the
   fetch seam that fixtures are recorded through sends query parameters
   rather than per-request headers. So the total comes from the publisher's
   own documented alternative — the same filters with `select=count` — which
   costs one small extra request, replays like any other, and is what lets
   `coverage.pagination` say `truncated` from a number instead of a guess.

Filters are structured, never concatenated. A caller names a column, an
operator from a fixed list, and a value; the column is checked against the
schema the service published, so the reserved parameter names PostgREST uses
for boolean grouping (`or`, `and`, `not`, `select`, `columns`) are
unreachable — none of them is a column in either table — and the value only
ever lands in the value position of a parameter httpx encodes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 20
MAX_ROWS = 500
"""A ceiling this client imposes, not one the publisher does. The service
returns all 75,727 turbines to a request that omits `limit`; the egress cap
would stop that read partway through and report it as an outage, which is a
confusing way to learn that a query was too broad."""

# The operators the USWTDB and USPVDB documentation lists, minus the boolean
# grouping forms (`and`, `or`, `not`), which take a nested expression rather
# than a value and are the one part of the dialect that would need a parser.
# A caller who needs those is describing a query this adapter does not take.
OPERATORS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike",
                       "in", "is"})

# PostgREST reads these query parameters as instructions rather than as
# column filters. None of them is a column in either database and every
# filter column is checked against the published schema, so a caller cannot
# reach one — but the set is named so the defence is a stated rule rather
# than an accident of these two tables' column names.
RESERVED_PARAMS = frozenset({"select", "order", "limit", "offset", "and",
                             "or", "not", "columns", "on_conflict"})


class PostgrestParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    table: str
    """The table this source is. Read from here and never inferred from
    `base_url`, because both USGS path aliases serve both tables."""
    id_column: str
    default_order: str
    state_column: str | None = None
    county_column: str | None = None
    name_column: str | None = None
    latitude_column: str | None = None
    longitude_column: str | None = None
    confidence_columns: dict[str, int] = Field(default_factory=dict)
    """Column -> the value that means full confidence. Both databases score
    every record for how sure the compilers are of it, and the scores are
    what separate a survey from an estimate. Held as data so the adapter can
    count the records below the top score in an answer rather than leaving a
    caller to notice."""
    confidence_note: str | None = None
    units_note: str | None = None
    dataset_doi: str | None = None
    """The release DOI. Held here rather than assembled in a tool because a
    source's facts live in one place, and it is what puts the publisher's
    suggested citation into the envelope: the citation block is attached
    when a citation is required or a DOI is known, and on these two it is
    suggested rather than required."""

    @field_validator("confidence_note", "units_note", mode="after")
    @classmethod
    def _tidy(cls, v: str | None) -> str | None:
        return " ".join(v.split()) if v is not None else None


register_adapter_params("postgrest", PostgrestParams)


@dataclass(frozen=True)
class Filter:
    """One `column=operator.value` clause, already checked."""

    column: str
    operator: str
    value: str

    def as_param(self) -> tuple[str, str]:
        if self.operator == "in":
            values = ",".join(v.strip() for v in self.value.split(",")
                              if v.strip())
            return self.column, f"in.({values})"
        return self.column, f"{self.operator}.{self.value}"

    def __str__(self) -> str:
        name, expression = self.as_param()
        return f"{name}={expression}"


@dataclass
class TableSchema:
    """What the service says its own tables are."""

    table: str
    columns: dict[str, str]        # column -> declared type
    other_tables: list[str] = field(default_factory=list)
    """The tables served alongside this one under the same base path. Carried
    because on this endpoint the sibling is a different database's data and
    the aliasing is otherwise invisible."""

    def require(self, column: str, role: str) -> str:
        if column not in self.columns:
            raise InvalidQuery(
                f"{role} {column!r} is not a column of {self.table!r}. This "
                f"table has: {', '.join(sorted(self.columns))}.")
        return column


@dataclass
class RowPage:
    rows: list[dict[str, Any]]
    columns: list[str]
    total: int | None
    limit: int
    offset: int
    filters: list[str]
    """The clauses actually sent, as the publisher's own syntax, so an answer
    can show the query that produced it."""
    low_confidence_rows: int = 0


def parse_filters(text: str, schema: TableSchema) -> list[Filter]:
    """Read `column=op.value` clauses out of one semicolon-separated string.

    The form is the publisher's own, which means a caller can lift an example
    straight out of the USWTDB or USPVDB documentation. It is parsed rather
    than passed through: an unknown column reaches the service as an HTTP 400
    whose body this project's fetch path does not surface, so it is caught
    here where the message can name the columns that do exist.
    """
    out: list[Filter] = []
    for clause in text.split(";"):
        if not clause.strip():
            continue
        column, _, expression = clause.partition("=")
        column = column.strip()
        operator, _, value = expression.strip().partition(".")
        if not column or not expression.strip():
            raise InvalidQuery(
                f"filter clause {clause!r} is not 'column=operator.value'. "
                "Use 'p_year=gte.2020;t_state=eq.CO'.")
        if operator not in OPERATORS:
            raise InvalidQuery(
                f"filter clause {clause!r} uses operator {operator!r}. "
                f"Available: {', '.join(sorted(OPERATORS))}. The boolean "
                "grouping forms (and, or, not) are not taken here.")
        if not value:
            raise InvalidQuery(
                f"filter clause {clause!r} has an operator and no value.")
        schema.require(column, "filter column")
        out.append(Filter(column=column, operator=operator, value=value))
    return out


def parse_order(text: str, schema: TableSchema) -> str:
    """`column` or `column.direction`, checked the same way as a filter."""
    column, _, direction = text.strip().partition(".")
    schema.require(column.strip(), "order column")
    direction = direction.strip() or "asc"
    if direction not in ("asc", "desc", "asc.nullslast", "desc.nullslast",
                         "asc.nullsfirst", "desc.nullsfirst"):
        raise InvalidQuery(
            f"order direction {direction!r} is not one of asc, desc, or "
            "either with .nullsfirst or .nullslast appended.")
    return f"{column.strip()}.{direction}"


class PostgrestAdapter:
    """Read-only. GET is the only verb this class knows how to send."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> PostgrestParams:
        return PostgrestParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: PostgrestParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def describe(self, manifest: SourceManifest
                       ) -> Fetched[TableSchema]:
        """The columns of this source's table, from the service's own OpenAPI
        document rather than from a list kept here.

        Reading it costs one request a day at the manifest's TTL and buys
        two things: filter columns can be checked before a request is sent,
        and a column added or renamed upstream shows up in the tool's own
        answer instead of as an unexplained HTTP 400.
        """
        params = self.params_for(manifest)
        url = params.base_url.rstrip("/") + "/"
        result = await self._fetch(manifest, params, url, {})
        body = result.payload
        definitions = (body.get("definitions")
                       if isinstance(body, dict) else None)
        if not isinstance(definitions, dict):
            raise SourceSchemaChanged(
                f"{manifest.id} served no OpenAPI 'definitions' block at "
                f"{url}; this is no longer a PostgREST service, or the base "
                "URL now points somewhere else.")
        if params.table not in definitions:
            raise SourceSchemaChanged(
                f"{manifest.id} declares table {params.table!r}, which the "
                f"service no longer publishes. It serves: "
                f"{', '.join(sorted(definitions))}.")
        properties = definitions[params.table].get("properties") or {}
        schema = TableSchema(
            table=params.table,
            columns={name: str(spec.get("format") or spec.get("type") or "")
                     for name, spec in properties.items()},
            other_tables=sorted(t for t in definitions
                                if t != params.table))
        log_source_call(manifest, "describe", {}, len(schema.columns))
        return Fetched.of(result, schema)

    async def query(self, manifest: SourceManifest, *,
                    filters: list[Filter] | None = None,
                    order: str | None = None, rows: int = DEFAULT_ROWS,
                    offset: int = 0,
                    schema: TableSchema | None = None) -> Fetched[RowPage]:
        """Rows of this source's table, with the matching total beside them.

        `schema` is taken as an argument rather than fetched here so a caller
        that has already described the table — which every tool has, because
        that is where its filters were checked — does not pay for the
        document twice.
        """
        params = self.params_for(manifest)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(
                f"rows must be between 1 and {MAX_ROWS}; got {rows}. This "
                "endpoint has no ceiling of its own and answers a request "
                "without one by returning the entire table.")
        if offset < 0:
            raise InvalidQuery(f"offset cannot be negative; got {offset}")
        clauses = list(filters or [])
        query = _as_query(clauses)
        url = params.base_url.rstrip("/") + "/" + params.table

        total = await self._count(manifest, params, url, query)
        page_query = dict(query, limit=rows, offset=offset,
                          order=order or f"{params.default_order}.asc")
        result = await self._fetch(manifest, params, url, page_query)
        body = result.payload
        if not isinstance(body, list):
            raise SourceSchemaChanged(
                f"{manifest.id} returned {type(body).__name__} where "
                "PostgREST returns an array of rows.")
        records = [r for r in body if isinstance(r, dict)]
        log_source_call(manifest, "query", page_query, len(records))
        return Fetched.of(result, RowPage(
            rows=records,
            columns=sorted(schema.columns) if schema
            else sorted({k for r in records for k in r}),
            total=total, limit=rows, offset=offset,
            filters=[str(f) for f in clauses],
            low_confidence_rows=_below_full_confidence(records, params)))

    async def _count(self, manifest: SourceManifest, params: PostgrestParams,
                     url: str, query: dict[str, Any]) -> int | None:
        """The number of rows matching these filters, unpaged.

        `select=count` is the publisher's documented way to ask, and the
        answer is unaffected by `limit`. A count that fails to parse returns
        None rather than raising: an answer without a total is worth serving
        with `pagination: unknown` on it, and refusing the rows because the
        second request came back odd would be the worse trade.
        """
        result = await self._fetch(manifest, params, url,
                                   dict(query, select="count"))
        body = result.payload
        if isinstance(body, list) and body and isinstance(body[0], dict):
            value = body[0].get("count")
            if isinstance(value, int):
                return value
        return None

    async def _fetch(self, manifest: SourceManifest, params: PostgrestParams,
                     url: str, query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)


def _as_query(clauses: list[Filter]) -> dict[str, Any]:
    """Filter clauses as query parameters, grouping by column.

    Two clauses on one column is the ordinary case rather than a mistake — a
    bounding box is four of them across two columns — and PostgREST reads a
    repeated parameter as a conjunction. Collapsing them into a dict of
    single values, which is the obvious way to write this, would silently
    drop one side of every range.
    """
    grouped: dict[str, list[str]] = {}
    for clause in clauses:
        name, expression = clause.as_param()
        grouped.setdefault(name, []).append(expression)
    return {name: values[0] if len(values) == 1 else values
            for name, values in grouped.items()}


def _below_full_confidence(rows: list[dict[str, Any]],
                           params: PostgrestParams) -> int:
    """How many returned records the compilers were not fully sure of.

    Counted rather than filtered. A turbine whose imagery showed only a
    concrete pad is a real record and dropping it would understate the
    inventory; presenting it beside a visually confirmed one with no
    distinction is what turns an estimate into a survey.
    """
    if not params.confidence_columns:
        return 0
    count = 0
    for row in rows:
        for column, full in params.confidence_columns.items():
            value = row.get(column)
            if isinstance(value, int) and value < full:
                count += 1
                break
    return count
