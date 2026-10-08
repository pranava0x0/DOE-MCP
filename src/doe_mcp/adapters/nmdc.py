"""The `nmdc` adapter: the National Microbiome Data Collaborative's runtime API.

NMDC indexes microbiome studies and their biosamples across a multi-lab
collaboration (LBNL leads; ANL, ORNL and PNNL take part). Its runtime API is
public and keyless for reads. The shapes, verified live 2026-10-08:

  /studies?per_page=N&page=P     {"meta": {..., "count": N}, "results": [...]}
  /studies/{id}                  one study object; HTTP 404 {"detail":
                                 "Not found"} for an unknown id
  /biosamples?filter=...         the same envelope as /studies

Three properties of the dialect shape this adapter:

1. **An unknown filter field is an empty answer, not an error.**
   `filter=nosuchfield:x` returns HTTP 200 with `count: 0`. So filters are
   built here from a fixed table of fields, never from caller text, and the
   `mongo_filter_dict` the API echoes is compared with what was sent. A
   filter that went out and did not come back is a `SourceSchemaChanged`,
   not an unfiltered catalog presented as a filtered one.
2. **`.search:` is a case-sensitive regular expression.** `name.search:Soil`
   and `name.search:soil` match 10 and 17 studies. Caller text is escaped
   and sent with an inline `(?i)`, and the filter grammar's own separator,
   the comma, is refused in a value rather than split on.
3. **The study collection is small and the biosample collection is not.**
   85 studies (327 KB) are fetched whole and searched in memory over name,
   title and description, which no single server-side filter can do.
   27,352 biosamples are filtered by the API.

Study records name their investigators with ORCID, email address and a
profile-image URL. The name and ORCID are scholarly identifiers and are
kept; the email address and the image are dropped here, for the same reason
the ESS-DIVE adapter drops creator emails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.errors import InvalidQuery, SourceSchemaChanged, SourceUnavailable
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache,
                   total_or_none)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 20
MAX_ROWS = 100
STUDY_PAGE = 200
MAX_STUDY_PAGES = 10
_ID = re.compile(r"^nmdc:(?P<kind>[a-z]{2,5})-\d{2}-[a-z0-9]+$")
# NMDC's id prefix for each kind of record. A biosample id passed where a
# study id belongs is refused by name: sent on, it reads as a study that
# does not exist or as a filter that matches nothing.
ID_PREFIX = {"study": "sty", "biosample": "bsm"}


class NmdcParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


register_adapter_params("nmdc", NmdcParams)

# Caller argument -> (API field, match). "exact" sends `field:value`;
# "text" sends `field.search:(?i)<escaped>`.
BIOSAMPLE_FILTERS: dict[str, tuple[str, str]] = {
    "study_id": ("associated_studies", "exact"),
    "ecosystem_type": ("ecosystem_type", "exact"),
    "env_medium": ("env_medium.term.name", "text"),
    "place": ("geo_loc_name.has_raw_value", "text"),
    "collected": ("collection_date.has_raw_value", "prefix"),
}


@dataclass
class Investigator:
    name: str
    orcid: str | None
    roles: list[str] = field(default_factory=list)


@dataclass
class Study:
    study_id: str
    name: str
    title: str | None
    description: str | None
    category: str | None
    ecosystem: list[str]
    funding: list[str]
    dois: list[dict[str, str]]
    gold_ids: list[str]
    part_of: list[str]
    investigators: list[Investigator]
    websites: list[str]


@dataclass
class StudyPage:
    studies: list[Study]
    total_matched: int
    collection_size: int


@dataclass
class Biosample:
    sample_id: str
    name: str | None
    studies: list[str]
    collected: str | None
    latitude: float | None
    longitude: float | None
    place: str | None
    ecosystem: list[str]
    env_broad_scale: str | None
    env_local_scale: str | None
    env_medium: str | None
    depth_m: dict[str, float] | None


@dataclass
class BiosamplePage:
    samples: list[Biosample]
    total: int | None
    page: int
    rows: int
    applied_filter: dict[str, Any]


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _raw(value: Any) -> str | None:
    """NMDC wraps scalar values: {'has_raw_value': ..., 'type': ...}."""
    if isinstance(value, dict):
        return _text(value.get("has_raw_value"))
    return _text(value)


def _term(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("term"), dict):
        term = value["term"]
        return _text(term.get("name")) or _text(term.get("id"))
    return None


def _ecosystem(raw: dict[str, Any]) -> list[str]:
    return [v for v in (_text(raw.get(k)) for k in (
        "ecosystem", "ecosystem_category", "ecosystem_type",
        "ecosystem_subtype", "specific_ecosystem")) if v
        and v != "Unclassified"]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_text(v) for v in value) if s]


def parse_study(raw: Any, source_id: str) -> Study:
    if not isinstance(raw, dict) or not _text(raw.get("id")):
        raise SourceSchemaChanged(
            f"{source_id}: a study record carries no 'id'.")
    people = []
    for credit in raw.get("has_credit_associations") or []:
        if not isinstance(credit, dict):
            continue
        agent = credit.get("applies_to_agent")
        name = _text(agent.get("name")) if isinstance(agent, dict) else None
        if not name:
            continue
        # Email and profile image are dropped on purpose (module docstring).
        people.append(Investigator(
            name=name, orcid=_text(agent.get("orcid")),
            roles=_strings(credit.get("applied_roles"))))
    dois = []
    for doi in raw.get("associated_dois") or []:
        if isinstance(doi, dict) and _text(doi.get("doi_value")):
            dois.append({"doi": doi["doi_value"].removeprefix("doi:"),
                         "category": _text(doi.get("doi_category")) or "",
                         "provider": _text(doi.get("doi_provider")) or ""})
    return Study(
        study_id=raw["id"].strip(), name=_text(raw.get("name")) or "",
        title=_text(raw.get("title")),
        description=_text(raw.get("description")),
        category=_text(raw.get("study_category")),
        ecosystem=_ecosystem(raw), funding=_strings(raw.get("funding_sources")),
        dois=dois, gold_ids=_strings(raw.get("gold_study_identifiers")),
        part_of=_strings(raw.get("part_of")), investigators=people,
        websites=list(dict.fromkeys(
            _strings(_as_list(raw.get("websites")))
            + _strings(_as_list(raw.get("homepage_website"))))))


def parse_biosample(raw: Any, source_id: str) -> Biosample:
    if not isinstance(raw, dict) or not _text(raw.get("id")):
        raise SourceSchemaChanged(
            f"{source_id}: a biosample record carries no 'id'.")
    lat_lon = raw.get("lat_lon") if isinstance(raw.get("lat_lon"), dict) \
        else {}
    return Biosample(
        sample_id=raw["id"].strip(), name=_text(raw.get("name")),
        studies=_strings(raw.get("associated_studies")),
        collected=_raw(raw.get("collection_date")),
        latitude=_number(lat_lon.get("latitude")),
        longitude=_number(lat_lon.get("longitude")),
        place=_raw(raw.get("geo_loc_name")), ecosystem=_ecosystem(raw),
        env_broad_scale=_term(raw.get("env_broad_scale")),
        env_local_scale=_term(raw.get("env_local_scale")),
        env_medium=_term(raw.get("env_medium")),
        depth_m=_depth_in_metres(raw.get("depth")))


def _depth_in_metres(depth: Any) -> dict[str, float] | None:
    """Depth as NMDC states it, kept only when stated in metres. A depth in
    another unit, or with no unit, is dropped rather than converted on a
    guess."""
    if not isinstance(depth, dict) or depth.get("has_unit") != "m":
        return None
    out = {name: float(depth[key]) for name, key in (
        ("min", "has_minimum_numeric_value"),
        ("max", "has_maximum_numeric_value"),
        ("value", "has_numeric_value"))
        if isinstance(depth.get(key), (int, float))}
    return out or None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def check_id(identifier: str, kind: str) -> str:
    value = identifier.strip()
    match = _ID.match(value)
    if match and match.group("kind") != ID_PREFIX[kind]:
        found = next((k for k, v in ID_PREFIX.items()
                      if v == match.group("kind")), match.group("kind"))
        raise InvalidQuery(
            f"{value!r} is an NMDC {found} id, not a {kind} id. A {kind} id "
            f"starts 'nmdc:{ID_PREFIX[kind]}-'; a biosample's studies are "
            "listed in its `studies` field.")
    if not match:
        raise InvalidQuery(
            f"{identifier!r} is not an NMDC {kind} id. Ids look like "
            "'nmdc:sty-11-8fb6t785' for a study and 'nmdc:bsm-11-06qrej20' "
            "for a biosample, and come from bio.search_studies.")
    return value


def _search_terms(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.lower()) if t]


def _study_matches(study: Study, terms: list[str], ecosystem: str,
                   category: str) -> bool:
    if terms:
        haystack = " ".join(filter(None, (study.name, study.title,
                                           study.description))).lower()
        if not all(t in haystack for t in terms):
            return False
    if ecosystem:
        wanted = ecosystem.strip().lower()
        if not any(wanted in e.lower() for e in study.ecosystem):
            return False
    if category and (study.category or "") != category.strip():
        return False
    return True


def build_filter(filters: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """The `filter` parameter and the Mongo filter the API should echo."""
    clauses: list[str] = []
    expected: dict[str, Any] = {}
    for name, value in filters.items():
        text = str(value).strip()
        if not text:
            continue
        if name not in BIOSAMPLE_FILTERS:
            raise InvalidQuery(
                f"{name!r} is not a biosample filter here; accepted: "
                f"{sorted(BIOSAMPLE_FILTERS)}. NMDC answers an unknown "
                "field with zero results and no error, so it is refused "
                "before it is sent.")
        if "," in text:
            raise InvalidQuery(
                f"{name}: a comma separates clauses in NMDC's filter "
                "grammar and cannot appear inside a value. Search on one "
                "word of the phrase instead.")
        api_field, match = BIOSAMPLE_FILTERS[name]
        if match == "exact":
            clauses.append(f"{api_field}:{text}")
            expected[api_field] = text
        else:
            pattern = (f"(?i){re.escape(text)}" if match == "text"
                       else f"^{re.escape(text)}")
            clauses.append(f"{api_field}.search:{pattern}")
            expected[api_field] = {"$regex": pattern}
    return ",".join(clauses), expected


class NmdcAdapter:
    """Read-only. Two collections and one record route."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> NmdcParams:
        return NmdcParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: NmdcParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def studies(self, manifest: SourceManifest, *, text: str = "",
                      ecosystem: str = "", category: str = ""
                      ) -> Fetched[StudyPage]:
        params = self.params_for(manifest)
        url = params.base_url.rstrip("/") + "/studies"
        everything: list[Study] = []
        first: FetchResult | None = None
        for page in range(1, MAX_STUDY_PAGES + 1):
            result = await self._fetch(manifest, params, url,
                                       {"per_page": str(STUDY_PAGE),
                                        "page": str(page)})
            first = first or result
            body = self._envelope(result.payload, manifest.id)
            everything.extend(parse_study(r, manifest.id)
                              for r in body["results"])
            count = total_or_none(body["meta"].get("count"))
            if count is None:
                raise SourceSchemaChanged(
                    f"{manifest.id}: the study collection reports no count, "
                    "so whether it was read whole cannot be known.")
            if len(everything) >= count or not body["results"]:
                break
        assert first is not None
        if len(everything) < (count or 0):
            raise SourceSchemaChanged(
                f"{manifest.id}: the study collection reports {count} "
                f"studies and {MAX_STUDY_PAGES} pages held "
                f"{len(everything)}. Searching a partial collection would "
                "report misses as absences.")
        terms = _search_terms(text)
        matched = [s for s in everything
                   if _study_matches(s, terms, ecosystem, category)]
        log_source_call(manifest, "studies",
                        {"text": text, "ecosystem": ecosystem,
                         "category": category}, len(matched))
        return Fetched.of(first, StudyPage(
            studies=matched, total_matched=len(matched),
            collection_size=len(everything)))

    async def get_study(self, manifest: SourceManifest,
                        study_id: str) -> Fetched[Study]:
        params = self.params_for(manifest)
        identifier = check_id(study_id, "study")
        url = f"{params.base_url.rstrip('/')}/studies/{identifier}"
        try:
            result = await self._fetch(manifest, params, url, {})
        except SourceUnavailable as refusal:
            # A record route answering 404 means the id names no study,
            # which is what NMDC's body says. A search never sees this
            # status: an empty search is HTTP 200 with count 0.
            if refusal.status == 404:
                raise InvalidQuery(
                    f"NMDC has no study {identifier!r} (HTTP 404, 'Not "
                    "found'). Study ids come from bio.search_studies.") \
                    from refusal
            raise
        study = parse_study(result.payload, manifest.id)
        log_source_call(manifest, "get_study", {"id": identifier}, 1)
        return Fetched.of(result, study)

    async def biosamples(self, manifest: SourceManifest, *,
                         filters: dict[str, str], rows: int = DEFAULT_ROWS,
                         page: int = 1) -> Fetched[BiosamplePage]:
        params = self.params_for(manifest)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}.")
        if page < 1:
            raise InvalidQuery("page starts at 1.")
        if filters.get("study_id"):
            filters = {**filters,
                       "study_id": check_id(filters["study_id"], "study")}
        expression, expected = build_filter(filters)
        query = {"per_page": str(rows), "page": str(page)}
        if expression:
            query["filter"] = expression
        url = params.base_url.rstrip("/") + "/biosamples"
        result = await self._fetch(manifest, params, url, query)
        body = self._envelope(result.payload, manifest.id)
        echoed = body["meta"].get("mongo_filter_dict")
        if echoed != expected:
            raise SourceSchemaChanged(
                f"{manifest.id}: sent the filter {expected} and the API "
                f"reports having run {echoed}. Answering would present a "
                "differently filtered catalog as the one asked for.")
        samples = [parse_biosample(r, manifest.id) for r in body["results"]]
        log_source_call(manifest, "biosamples", query, len(samples))
        return Fetched.of(result, BiosamplePage(
            samples=samples, total=total_or_none(body["meta"].get("count")),
            page=page, rows=rows, applied_filter=expected))

    async def count_biosamples(self, manifest: SourceManifest,
                               study_id: str) -> Fetched[int | None]:
        fetched = await self.biosamples(manifest,
                                        filters={"study_id": study_id},
                                        rows=1)
        return Fetched(value=fetched.value.total,
                       retrieved_at=fetched.retrieved_at,
                       cache_age_seconds=fetched.cache_age_seconds,
                       request_url=fetched.request_url,
                       from_cache=fetched.from_cache)

    @staticmethod
    def _envelope(payload: Any, source_id: str) -> dict[str, Any]:
        if (not isinstance(payload, dict)
                or not isinstance(payload.get("meta"), dict)
                or not isinstance(payload.get("results"), list)):
            raise SourceSchemaChanged(
                f"{source_id}: expected NMDC's {{meta, results}} envelope.")
        return payload

    async def _fetch(self, manifest: SourceManifest, params: NmdcParams,
                     url: str, query: dict[str, str]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
