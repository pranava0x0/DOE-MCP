"""The `optimade` adapter: one query grammar, twenty-nine databases.

OPTIMADE is a specification rather than a service — a common REST API and
filter language that materials databases implement so that one client can
query all of them. The federation's own index listed 29 providers on
2026-09-09. DOE's member is the Materials Project, which LBNL runs and which
is one of the department's designated PuRe data resources; its OPTIMADE
endpoint serves 154,387 structures with no key, alongside the keyed
`mp-api` route that decision 0011 reserves for depend-and-wrap.

This is a genre in the strongest sense the registry has: the grammar, the
endpoint names, the pagination fields, and the error shape are all in a
published standard, so a second provider is a manifest rather than code. The
adapter is written to that standard and not to the Materials Project.

Three properties decide its shape, all verified live 2026-09-09:

1. **The provider publishes its own property list.** `/v1/info/structures`
    returns every queryable field with a description, including the
    provider-specific ones the standard reserves an underscore prefix for —
    `_mp_stability` and `_mp_chemical_system` here, which no caller would
    guess and which carry the formation energies. That document is read
    rather than copied into this repository, for the reason the PostgREST
    and ESGF adapters read theirs: a field list that lives here is a field
    list that goes stale without anything failing.

2. **A filter on an unknown property is refused, precisely.** HTTP 400 with
    `{"errors": [{"detail": "'nosuchprop' is not a known or searchable
    quantity"}]}`, and a malformed filter comes back with the parser's own
    traceback. That makes this the second well-behaved endpoint in the
    registry, and the fetch path now repeats both messages verbatim.

3. **The keyless route is narrower than the keyed one.** OPTIMADE at the
    Materials Project serves structures and formulas; the energetics,
    electronic structure and phase diagrams are behind `mp-api`. A caller
    who reads "Materials Project" and assumes the whole database is reading
    the publisher's name rather than this route's coverage, so the manifest
    says so and the tool repeats it.

Filters go out in the publisher's own grammar because it IS the published
interface, and the property names in them are checked against the schema
first. Values reach the service inside a filter string, so the named
arguments that build one quote and escape what they are given; a caller who
needs more writes the filter themselves, which is the same bargain the
PostgREST adapter offers.
"""
from __future__ import annotations

import re
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

# A property name in the grammar: letters, digits and underscores, with the
# leading underscore the standard reserves for provider extensions. Used to
# find the names inside a caller's own filter so they can be checked against
# the published schema before the filter is sent.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Words of the filter language itself, which are not property names.
_KEYWORDS = frozenset({
    "AND", "OR", "NOT", "HAS", "ALL", "ANY", "ONLY", "LENGTH", "IS", "KNOWN",
    "UNKNOWN", "CONTAINS", "STARTS", "ENDS", "WITH", "true", "false"})


class OptimadeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    entry_type: str = "structures"
    """Which entry endpoint this source serves. `structures` everywhere in
    this registry; the standard also defines `references`, and a provider
    that served one would be a manifest rather than code."""
    provider_prefix: str = ""
    """The underscore prefix this provider puts on its own extension fields
    (`_mp_` at the Materials Project). Held so an answer can say which
    fields are the standard's and which are one database's."""
    keyed_route_note: str | None = None
    """What this keyless route does NOT carry that the publisher's keyed API
    does. A manifest field because it is a coverage fact, and because a
    caller reading the publisher's name will assume the whole database."""
    sortable: list[str] = Field(default_factory=list)


register_adapter_params("optimade", OptimadeParams)


@dataclass
class EntrySchema:
    properties: dict[str, str]
    """Property name -> the provider's own description, first line only."""
    api_version: str | None
    provider_name: str | None
    formats: list[str] = field(default_factory=list)
    entry_types: list[str] = field(default_factory=list)

    def extensions(self, prefix: str) -> list[str]:
        return sorted(n for n in self.properties
                      if prefix and n.startswith(prefix))

    def check(self, names: list[str]) -> None:
        unknown = [n for n in names if n not in self.properties]
        if unknown:
            raise InvalidQuery(
                f"unknown OPTIMADE property: {', '.join(sorted(unknown))}. "
                f"This provider publishes {len(self.properties)} queryable "
                "fields; call materials.describe_structure_fields for the "
                "list, including the provider's own extensions, which carry "
                "what the standard does not.")


@dataclass
class Structure:
    entry_id: str
    formula_reduced: str | None
    formula_descriptive: str | None
    formula_hill: str | None
    elements: list[str]
    element_ratios: list[float]
    nelements: int | None
    nsites: int | None
    space_group_number: int | None
    space_group_symbol: str | None
    dimensionality: int | None
    last_modified: str | None
    immutable_id: str | None
    lattice_vectors: list[list[float]] | None
    structure_features: list[str]
    provider_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructurePage:
    structures: list[Structure]
    data_returned: int | None
    """Matching entries, which the provider counts for the whole query, or
    None when the response carried no count."""
    data_available: int | None
    more_data_available: bool | None
    """The specification's own has-more flag, or None when the provider
    omitted it. A flag the provider did not send is not a flag that says
    no."""
    offset: int
    query_sent: str
    provider_name: str | None


def quote(value: str) -> str:
    """A string literal in the filter grammar.

    The grammar takes double-quoted strings with backslash escapes, so a
    value carrying a quote or a backslash is escaped rather than refused —
    and a value can then never end the literal early and be read as
    grammar. Callers who want to write filter syntax pass `filter`, where
    the bargain is explicit.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_filter(*, elements: list[str], formula: str, nelements: str,
                 extra: str) -> str:
    """The named arguments, assembled into one filter expression."""
    clauses: list[str] = []
    if elements:
        joined = ",".join(quote(e) for e in elements)
        clauses.append(f"elements HAS ALL {joined}")
    if formula:
        clauses.append(f"chemical_formula_reduced={quote(formula)}")
    if nelements:
        clauses.append(_comparison("nelements", nelements))
    if extra:
        clauses.append(f"({extra})")
    return " AND ".join(clauses)


def _comparison(name: str, value: str) -> str:
    """`nelements=3` or `nelements<=4`, from a value that may carry its own
    operator. A bare number means equality."""
    text = value.strip()
    match = re.fullmatch(r"(<=|>=|!=|<|>|=)?\s*(\d+)", text)
    if match is None:
        raise InvalidQuery(
            f"{name} takes a number, optionally with a comparison operator: "
            f"'3', '<=4', '>2'. Got {value!r}.")
    operator, number = match.group(1) or "=", match.group(2)
    return f"{name}{operator}{number}"


def property_names(expression: str) -> list[str]:
    """Every identifier in a filter that is not a keyword or inside a string.

    String literals are removed first, so a value like `"HAS"` or an element
    symbol is never mistaken for a property name and reported as unknown.
    """
    without_strings = re.sub(r'"(?:[^"\\]|\\.)*"', " ", expression)
    found = [m.group(0) for m in _IDENTIFIER.finditer(without_strings)]
    return sorted({n for n in found if n not in _KEYWORDS})


def _first_line(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def parse_structure(entry: dict, source_id: str,
                    prefix: str) -> Structure:
    entry_id = entry.get("id")
    attributes = entry.get("attributes")
    if not entry_id or not isinstance(attributes, dict):
        raise SourceSchemaChanged(
            f"{source_id}: an entry carries no id or no attributes object. "
            "That is the JSON:API shape the standard requires.")
    return Structure(
        entry_id=str(entry_id),
        formula_reduced=attributes.get("chemical_formula_reduced"),
        formula_descriptive=attributes.get("chemical_formula_descriptive"),
        formula_hill=attributes.get("chemical_formula_hill"),
        elements=list(attributes.get("elements") or []),
        element_ratios=list(attributes.get("elements_ratios") or []),
        nelements=attributes.get("nelements"),
        nsites=attributes.get("nsites"),
        space_group_number=attributes.get("space_group_it_number"),
        space_group_symbol=attributes.get(
            "space_group_symbol_hermann_mauguin"),
        dimensionality=attributes.get("nperiodic_dimensions"),
        last_modified=attributes.get("last_modified"),
        immutable_id=attributes.get("immutable_id"),
        lattice_vectors=attributes.get("lattice_vectors"),
        structure_features=list(attributes.get("structure_features") or []),
        provider_fields={k: v for k, v in attributes.items()
                         if prefix and k.startswith(prefix)})


class OptimadeAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> OptimadeParams:
        return OptimadeParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: OptimadeParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def describe(self,
                       manifest: SourceManifest) -> Fetched[EntrySchema]:
        """The provider's own property list for its entry type."""
        params = self.params_for(manifest)
        url = (f"{params.base_url.rstrip('/')}/info/{params.entry_type}")
        result = await self._fetch(manifest, params, url, {})
        payload = result.payload
        if not isinstance(payload, dict) or "data" not in payload:
            raise SourceSchemaChanged(
                f"{manifest.id}: the info endpoint returned no 'data' "
                "object. The provider is no longer answering the standard's "
                "shape at this path.")
        data = payload["data"]
        properties = data.get("properties") if isinstance(data, dict) else None
        if not isinstance(properties, dict) or not properties:
            raise SourceSchemaChanged(
                f"{manifest.id}: the info endpoint lists no properties, so "
                "there is nothing to check a filter against.")
        meta = payload.get("meta") or {}
        provider = meta.get("provider") or {}
        log_source_call(manifest, "describe", {}, len(properties))
        return Fetched.of(result, EntrySchema(
            properties={name: _first_line(body.get("description"))
                        for name, body in properties.items()
                        if isinstance(body, dict)},
            api_version=meta.get("api_version"),
            provider_name=provider.get("name"),
            formats=list(data.get("formats") or []),
            entry_types=list(data.get("output_fields_by_format", {}))))

    async def search(self, manifest: SourceManifest, *, schema: EntrySchema,
                     filter_expression: str = "", rows: int = DEFAULT_ROWS,
                     offset: int = 0) -> Fetched[StructurePage]:
        params = self.params_for(manifest)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}.")
        if offset < 0:
            raise InvalidQuery("offset must be zero or more.")
        expression = filter_expression.strip()
        if expression:
            schema.check(property_names(expression))

        query = {"page_limit": str(rows), "page_offset": str(offset)}
        if expression:
            query["filter"] = expression
        url = f"{params.base_url.rstrip('/')}/{params.entry_type}"
        result = await self._fetch(manifest, params, url, query)
        page = self._parse_page(result.payload, manifest.id, params, offset,
                                expression)
        log_source_call(manifest, "search", query, len(page.structures))
        return Fetched.of(result, page)

    async def get_entry(self, manifest: SourceManifest,
                        entry_id: str) -> Fetched[Structure]:
        params = self.params_for(manifest)
        identifier = entry_id.strip()
        if not identifier or "/" in identifier or identifier.startswith("."):
            raise InvalidQuery(
                f"{entry_id!r} is not an entry id. Ids come from "
                "materials.search_structures and look like 'mp-1244984'.")
        url = (f"{params.base_url.rstrip('/')}/{params.entry_type}/"
               f"{identifier}")
        result = await self._fetch(manifest, params, url, {})
        payload = result.payload
        entry = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        if not isinstance(entry, dict):
            raise SourceSchemaChanged(
                f"{manifest.id}: a single-entry lookup returned no entry "
                "object.")
        log_source_call(manifest, "get_entry", {"id": identifier}, 1)
        return Fetched.of(result, parse_structure(entry, manifest.id,
                                                  params.provider_prefix))

    @staticmethod
    def _parse_page(payload: Any, source_id: str, params: OptimadeParams,
                    offset: int, expression: str) -> StructurePage:
        if not isinstance(payload, dict) or "data" not in payload:
            raise SourceSchemaChanged(
                f"{source_id}: the response carries no 'data' array. The "
                "standard requires one on every entry endpoint.")
        meta = payload.get("meta") or {}
        entries = payload["data"]
        if not isinstance(entries, list):
            raise SourceSchemaChanged(
                f"{source_id}: 'data' is not an array on a search response.")
        provider = meta.get("provider") or {}
        flag = meta.get("more_data_available")
        return StructurePage(
            structures=[parse_structure(e, source_id, params.provider_prefix)
                        for e in entries if isinstance(e, dict)],
            data_returned=total_or_none(meta.get("data_returned")),
            data_available=total_or_none(meta.get("data_available")),
            more_data_available=flag if isinstance(flag, bool) else None,
            offset=offset, query_sent=expression,
            provider_name=provider.get("name"))

    async def _fetch(self, manifest: SourceManifest, params: OptimadeParams,
                     url: str, query: dict[str, str]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
