"""Regulations.gov JSON:API docket and document metadata, GET only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.credentials import Credentials
from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, shared_cache, total_or_none)


class RegulationsGovParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    credential_ref: str = "API_DATA_GOV_KEY"
    agency_ids: list[str]


register_adapter_params("regulations_gov", RegulationsGovParams)


@dataclass
class RegulationsPage:
    records: list[dict[str, Any]]
    total: int | None
    more: bool | None
    page: int
    rows: int


FIELDS = {
    "agencyId": "agency_id", "docketId": "docket_id", "title": "title",
    "docketType": "docket_type", "documentType": "document_type",
    "postedDate": "posted_date", "commentStartDate": "comment_start_date",
    "commentEndDate": "comment_end_date", "lastModifiedDate": "last_modified_date",
    "frDocNum": "fr_document_number", "openForComment": "open_for_comment",
    "withdrawn": "withdrawn", "rin": "rin",
}


def _links(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    links = {}
    for name, item in value.items():
        href = item.get("href") if isinstance(item, dict) else item
        if isinstance(href, str) and href.startswith("https://"):
            links[name] = href
    return links


def _record(item: Any, kind: str, allowed: list[str], *,
            docket_id: str = "", agency_id: str = "", record_id: str = "") -> dict:
    if (not isinstance(item, dict) or item.get("type") != kind
            or not isinstance(item.get("attributes"), dict)
            or not isinstance(item.get("id"), str) or not item["id"]):
        raise SourceSchemaChanged("Regulations.gov returned a malformed metadata record.")
    attrs = item["attributes"]
    if attrs.get("agencyId") not in allowed:
        raise SourceSchemaChanged("Regulations.gov returned an agency outside this source's scope.")
    if agency_id and attrs.get("agencyId") != agency_id:
        raise SourceSchemaChanged("Regulations.gov returned a different agency than requested.")
    if not isinstance(attrs.get("title"), str) or not attrs["title"].strip():
        raise SourceSchemaChanged("Regulations.gov returned no document or docket title.")
    for name in ("openForComment", "withdrawn"):
        if attrs.get(name) is not None and not isinstance(attrs[name], bool):
            raise SourceSchemaChanged(f"Regulations.gov changed the {name} boolean field.")
    for name in ("postedDate", "commentStartDate", "commentEndDate", "lastModifiedDate"):
        value = attrs.get(name)
        if value is not None:
            try:
                if not isinstance(value, str):
                    raise ValueError("date is not text")
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise SourceSchemaChanged(f"Regulations.gov changed the {name} date field.") from exc
    actual_docket = item["id"] if kind == "dockets" else attrs.get("docketId")
    if not isinstance(actual_docket, str) or not actual_docket:
        raise SourceSchemaChanged("Regulations.gov returned no docket identifier.")
    if docket_id and actual_docket != docket_id:
        raise SourceSchemaChanged("Regulations.gov returned a different docket than requested.")
    if record_id and item["id"] != record_id:
        raise SourceSchemaChanged("Regulations.gov returned a different identifier than requested.")
    result = {target: attrs[name] for name, target in FIELDS.items()
              if name in attrs and attrs[name] is not None}
    result.update(id=item["id"], links=_links(item.get("links")))
    if kind == "dockets":
        result["docket_id"] = item["id"]
    return result


class RegulationsGovAdapter:
    version = "1"

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None,
                 credentials: Credentials | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()
        self._credentials = credentials

    @staticmethod
    def params_for(manifest: SourceManifest) -> RegulationsGovParams:
        return RegulationsGovParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    @staticmethod
    def _agency(params: RegulationsGovParams, agency_id: str) -> str:
        if agency_id not in params.agency_ids:
            raise InvalidQuery("agency_id must be one of this source's verified codes: "
                               + ", ".join(params.agency_ids))
        return agency_id

    @staticmethod
    def _identifier(params: RegulationsGovParams, identifier: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9]*-[A-Z0-9]+(?:-[A-Z0-9]+)+", identifier):
            raise InvalidQuery("give the publisher's complete docket or document identifier.")
        if identifier.split("-", 1)[0] not in params.agency_ids:
            raise InvalidQuery("identifier is outside this source's verified agency scope.")
        return identifier

    async def _fetch(self, manifest: SourceManifest, path: str,
                     query: dict[str, Any]) -> FetchResult:
        params = self.params_for(manifest)
        credentials = self._credentials or Credentials.load()
        key = credentials.require(params.credential_ref)
        url = params.base_url.rstrip("/") + path
        cached = self._cache.get(manifest.id, url, query,
                                  manifest.freshness.ttl_hint_seconds)
        if cached is not None:
            return cached
        fetcher = self._fetcher or HttpFetcher(
            policy=egress_policy_for(manifest, params.base_url),
            extra_headers={"X-Api-Key": key})
        response = await fetcher.fetch_json(url, query)
        return self._cache.put(manifest.id, url, query, response.payload, response.headers)

    async def search_documents(self, manifest: SourceManifest, *,
                     docket_id: str, agency_id: str = "EERE", query: str = "",
                     since: str = "", until: str = "",
                     rows: int = 5, page: int = 1) -> Fetched[RegulationsPage]:
        params = self.params_for(manifest)
        kind = "documents"
        agency_id = self._agency(params, agency_id)
        if type(rows) is not int or not 5 <= rows <= 20:
            raise InvalidQuery("rows must be between 5 and 20.")
        if type(page) is not int or not 1 <= page <= 20:
            raise InvalidQuery("page must be between 1 and 20; narrow the search beyond that.")
        if not docket_id:
            raise InvalidQuery("document searches require an exact docket_id.")
        filters: dict[str, Any] = {
            "filter[agencyId]": agency_id, "page[size]": rows, "page[number]": page,
            "sort": "-lastModifiedDate,documentId"}
        if docket_id:
            filters["filter[docketId]"] = self._identifier(params, docket_id)
        if query.strip():
            filters["filter[searchTerm]"] = query.strip()
        for value, modifier in ((since, "ge"), (until, "le")):
            if value:
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as exc:
                    raise InvalidQuery("posted date filters must be YYYY-MM-DD.") from exc
                if parsed.isoformat() != value:
                    raise InvalidQuery("posted date filters must be YYYY-MM-DD.")
                filters[f"filter[postedDate][{modifier}]"] = value
        if since and until and since > until:
            raise InvalidQuery("since must be on or before until.")
        fetched = await self._fetch(manifest, "/" + kind, filters)
        payload = fetched.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise SourceSchemaChanged("Regulations.gov search no longer carries a data array.")
        records = [_record(item, kind, params.agency_ids, docket_id=docket_id,
                            agency_id=agency_id) for item in payload["data"]]
        if len(records) > rows:
            raise SourceSchemaChanged("Regulations.gov returned more rows than requested.")
        for record in records:
            posted = record.get("posted_date")
            if ((since or until) and (not isinstance(posted, str) or len(posted) < 10)
                    or since and posted[:10] < since
                    or until and posted[:10] > until):
                raise SourceSchemaChanged("Regulations.gov returned a posted date outside the request.")
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        more = meta.get("hasNextPage")
        return Fetched.of(fetched, RegulationsPage(
            records=records, total=total_or_none(meta.get("totalElements")),
            more=more if isinstance(more, bool) else None, page=page, rows=rows))

    async def get(self, manifest: SourceManifest, identifier: str, *,
                  kind: str) -> Fetched[dict[str, Any]]:
        params = self.params_for(manifest)
        if kind not in ("dockets", "documents"):
            raise InvalidQuery("only docket and document metadata can be read.")
        self._identifier(params, identifier)
        fetched = await self._fetch(manifest, "/" + kind + "/" + identifier, {})
        payload = fetched.payload
        if not isinstance(payload, dict):
            raise SourceSchemaChanged("Regulations.gov metadata is no longer an object.")
        record = _record(payload.get("data"), kind, params.agency_ids,
                          record_id=identifier)
        return Fetched.of(fetched, record)
