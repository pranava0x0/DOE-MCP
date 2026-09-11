"""The `vips` adapter: PNNL's Visual Intellectual Property Search.

A source-specific adapter, like `fueleconomy`, `eia_v2`, and
`federal_register`: one API, its own query language, no second member to make
a genre out of. What it covers is the technology-transfer side of the
national laboratory system — the patents a lab holds and the software it has
released, with inventors, assignees, claims, licences, and a taxonomy — for
all twenty-one sites in one index. PNNL runs it; the records are every lab's.

The service publishes an OpenAPI document at `/api/openapi.json`, and
`/api/v1/ip/search` is the only search route in it. Four of its behaviours,
verified live 2026-09-08, decide the shape of what follows:

1. **A wrong lab acronym is `{"total": 0, "hits": []}` with HTTP 200.** So is
   a wrong record type. This is the exact failure the whole envelope exists
   to prevent: an empty result that means "you typed it wrong" is
   indistinguishable from one that means "this lab has no such patent". The
   acronyms therefore come from the service's own lab list and are checked
   before a request goes out. It matters here more than anywhere else in
   this project, because `labs=NREL` returns zero and `labs=NLR` returns
   1,536: the rename that decision 0008 is built around is live inside this
   very API.

2. **A cursor silently ignores every filter sent with it.** The OpenAPI
   document says so and the service does it — a cursor from a PNNL search
   replayed with `labs=INL` returns PNNL rows and PNNL's total. The cursor
   already carries the filters of the search that made it, so sending both
   is refused rather than resolved: a caller who thought they had changed
   the filter should be told they had not.

3. **`total` is the count for the filters, not for the page**, and paging is
   by opaque cursor rather than by offset. There is no page number to
   report, so the answer says how many matched and hands back the cursor.

4. **Records carry the whole document.** A patent's `description` is the
   filed text — ten kilobytes on the first one tried — so a page of them is
   megabytes of patent prose. The search view drops that one field and says
   how much it dropped; a caller who wants it asks for the record by id,
   which is the same shape with the field kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   total_or_none,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 10
MAX_ROWS = 50
"""The service allows 1,000. Fifty is this client's ceiling because a record
is a whole patent: even without the filed text, fifty of them is a large
answer, and a thousand would be an answer nobody reads."""

RECORD_TYPES = ("Patent", "Software")

# The field holding the complete filed text of a patent or the whole README
# of a piece of software. Dropped from a search page and kept in a
# single-record lookup; see the module docstring.
FULL_TEXT_FIELD = "description"


class VipsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


register_adapter_params("vips", VipsParams)


@dataclass
class LabIndex:
    """The sites this service indexes, by its own acronyms.

    Read from the service rather than from the registry's seventeen
    laboratories, because it also carries four NNSA production sites that
    are not national laboratories and would be rejected by a check against
    the lab table. The contact addresses the same response carries are not
    kept: this project does not republish government staff contact details.
    """

    acronyms: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)

    def require(self, acronym: str) -> str:
        if acronym not in self.names:
            raise InvalidQuery(
                f"{acronym!r} is not one of the {len(self.acronyms)} sites "
                "this database indexes, and an unknown acronym here returns "
                "zero results rather than an error — which would read as the "
                f"lab holding nothing. Known: {', '.join(self.acronyms)}. If "
                "you meant a laboratory that has been renamed, resolve it "
                "with registry.resolve_org first.")
        return acronym


@dataclass
class IpRecord:
    id: str
    type: str
    title: str
    lab: str
    raw: dict[str, Any]
    full_text_chars: int = 0
    """How much of the record this view is not carrying. Zero on a
    single-record lookup, which keeps the field."""


@dataclass
class IpPage:
    records: list[IpRecord]
    total: int | None
    """The service's count of matches, or None when the response carried
    none. None reaches the caller as `pagination: unknown`, never as zero."""
    page_size: int
    next_cursor: str | None
    filters: dict[str, Any]


def _record(raw: dict[str, Any], *, keep_full_text: bool) -> IpRecord:
    body = dict(raw)
    dropped = 0
    if not keep_full_text:
        text = body.pop(FULL_TEXT_FIELD, None)
        dropped = len(text) if isinstance(text, str) else 0
    return IpRecord(id=str(body.get("id") or ""),
                    type=str(body.get("type") or ""),
                    title=str(body.get("title") or ""),
                    lab=str(body.get("lab") or ""), raw=body,
                    full_text_chars=dropped)


class VipsAdapter:
    """Read-only. The service has write routes behind an admin portal; this
    class knows how to send GET and nothing else."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> VipsParams:
        return VipsParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: VipsParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def labs(self, manifest: SourceManifest) -> Fetched[LabIndex]:
        params = self.params_for(manifest)
        url = params.base_url.rstrip("/") + "/VIPS/LabDetails"
        result = await self._fetch(manifest, params, url, {})
        body = result.payload
        if not isinstance(body, list) or not body:
            raise SourceSchemaChanged(
                f"{manifest.id} served no laboratory list at {url}; without "
                "it a lab filter cannot be checked, and an unchecked one "
                "returns zero results rather than an error.")
        index = LabIndex()
        for entry in body:
            if not isinstance(entry, dict) or not entry.get("acronym"):
                continue
            acronym = str(entry["acronym"])
            index.acronyms.append(acronym)
            index.names[acronym] = str(entry.get("name") or acronym)
        log_source_call(manifest, "labs", {}, len(index.acronyms))
        return Fetched.of(result, index)

    async def search(self, manifest: SourceManifest, *, text: str = "",
                     lab: str = "", record_type: str = "",
                     inventor: str = "", taxonomy: str = "",
                     date_start: str = "", date_end: str = "",
                     rows: int = DEFAULT_ROWS,
                     cursor: str = "") -> Fetched[IpPage]:
        params = self.params_for(manifest)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}; got "
                               f"{rows}")
        filters = {"text_search": text.strip(), "labs": lab.strip(),
                   "type": record_type.strip(), "inventors": inventor.strip(),
                   "taxonomy": taxonomy.strip(),
                   "date_start": date_start.strip(),
                   "date_end": date_end.strip()}
        active = {k: v for k, v in filters.items() if v}
        if cursor.strip() and active:
            raise InvalidQuery(
                "a cursor and filters cannot be sent together: this service "
                "ignores every filter that accompanies a cursor, so the "
                f"answer would silently be the previous search's "
                f"({', '.join(sorted(active))} would have no effect). The "
                "cursor already carries the filters of the search that "
                "produced it; page with the cursor alone, or start a new "
                "search without it.")
        if record_type and record_type not in RECORD_TYPES:
            raise InvalidQuery(
                f"record type {record_type!r} is not one this service takes "
                f"({' or '.join(RECORD_TYPES)}), and it answers a wrong one "
                "with zero results rather than an error.")

        query: dict[str, Any] = {"page_size": rows}
        if cursor.strip():
            query["cursor"] = cursor.strip()
        else:
            query.update({k: v for k, v in active.items()})
        url = params.base_url.rstrip("/") + "/v1/ip/search"
        result = await self._fetch(manifest, params, url, query)
        body = result.payload
        if not isinstance(body, dict) or "hits" not in body:
            raise SourceSchemaChanged(
                f"{manifest.id} returned no 'hits' array; the VIPS search "
                "response shape has changed.")
        records = [_record(h, keep_full_text=False) for h in body["hits"]
                   if isinstance(h, dict)]
        total = body.get("total")
        log_source_call(manifest, "search", query, len(records))
        return Fetched.of(result, IpPage(
            records=records, total=total_or_none(total),
            page_size=rows, next_cursor=body.get("next_cursor") or None,
            filters=active))

    async def get_record(self, manifest: SourceManifest,
                         record_id: str) -> Fetched[IpRecord]:
        """One record whole, including the filed text a search page drops."""
        params = self.params_for(manifest)
        identifier = record_id.strip()
        if not identifier:
            raise InvalidQuery("a record id is required")
        url = params.base_url.rstrip("/") + "/Document/" + identifier
        result = await self._fetch(manifest, params, url, {})
        body = result.payload
        if not isinstance(body, dict) or not body.get("id"):
            raise SourceSchemaChanged(
                f"{manifest.id} returned no record for {identifier!r}.")
        log_source_call(manifest, "get_record", {"record_id": ""}, 1)
        return Fetched.of(result, _record(body, keep_full_text=True))

    async def _fetch(self, manifest: SourceManifest, params: VipsParams,
                     url: str, query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
