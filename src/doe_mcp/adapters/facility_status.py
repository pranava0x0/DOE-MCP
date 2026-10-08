"""The `facility_status` adapter: a computing facility's public status board.

NERSC's Superfacility API answers two status questions without
authentication: what state each system is in now, and which outages are
scheduled. Everything else on that API (jobs, files, allocations) needs a
NERSC account and is out of scope; this adapter has no route to it.

The shapes, verified live 2026-10-08:

  /status                 a bare array of systems, each with `name`,
                          `full_name`, `system_type`, `status`,
                          `description`, `notes` and `updated_at`
  /status/outages/planned an array of arrays: one inner array per system,
                          each entry with `name`, `start_at`, `end_at`,
                          `description`, `notes` and `status`

Two properties of the board shape every answer:

1. **The timestamps carry no UTC offset.** `2026-10-21T06:00:00` is printed
   as the publisher wrote it. Converting it would require a zone the
   response does not state, so the adapter keeps the string and the tool
   says the zone is unstated.
2. **`updated_at` is when the row last changed, not when it was checked.**
   A filesystem that has been fine since August reads `2026-08-03`. An old
   timestamp on an active system is a quiet system, not a stale feed.

One facility answers today. ALCF's status page refuses a scripted client
and OLCF publishes no machine interface that could be found (2026-09-08);
both stay registered as proposed sources, so a question about them reports
a registry gap rather than an empty board.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"


class FacilityStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    facility: str
    """The facility's own short name, 'NERSC', used in answers."""
    status_path: str = "/status"
    planned_path: str = "/status/outages/planned"
    timezone_note: str
    """What the publisher says, or does not say, about its timestamps."""

    @field_validator("timezone_note", mode="after")
    @classmethod
    def _tidy(cls, v: str) -> str:
        return " ".join(v.split())


register_adapter_params("facility_status", FacilityStatusParams)


@dataclass(frozen=True)
class SystemStatus:
    name: str
    full_name: str | None
    system_type: str | None
    status: str | None
    description: str | None
    updated_at: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return (self.status or "").lower() == "active"


@dataclass(frozen=True)
class PlannedOutage:
    system: str
    start_at: str | None
    end_at: str | None
    description: str | None
    notes: str | None
    status: str | None


@dataclass
class StatusBoard:
    facility: str
    systems: list[SystemStatus]
    planned: list[PlannedOutage]
    systems_on_board: int
    """Every system the board lists, before a caller's filter."""


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _system(entry: Any, source_id: str) -> SystemStatus:
    if not isinstance(entry, dict) or not _text(entry.get("name")):
        raise SourceSchemaChanged(
            f"{source_id}: a status row carries no 'name'. That is the only "
            "key the board uses for a system.")
    notes = entry.get("notes")
    return SystemStatus(
        name=_text(entry["name"]) or "",
        full_name=_text(entry.get("full_name")),
        system_type=_text(entry.get("system_type")),
        status=_text(entry.get("status")),
        description=_text(entry.get("description")),
        updated_at=_text(entry.get("updated_at")),
        notes=[n for n in (_text(x) for x in notes) if n]
        if isinstance(notes, list) else [])


def _outage(entry: Any, source_id: str) -> PlannedOutage:
    # Refused rather than skipped: a dropped row would leave a shorter
    # maintenance schedule reported as complete, which is the answer
    # someone planning around an outage can least afford.
    if not isinstance(entry, dict) or not _text(entry.get("name")):
        raise SourceSchemaChanged(
            f"{source_id}: a planned-outage row carries no system 'name'.")
    return PlannedOutage(
        system=_text(entry["name"]) or "",
        start_at=_text(entry.get("start_at")),
        end_at=_text(entry.get("end_at")),
        description=_text(entry.get("description")),
        notes=_text(entry.get("notes")),
        status=_text(entry.get("status")))


class FacilityStatusAdapter:
    """Read-only, and limited by construction to the two public routes."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> FacilityStatusParams:
        return FacilityStatusParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: FacilityStatusParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def board(self, manifest: SourceManifest, *, system: str = "",
                    include_planned: bool = True) -> Fetched[StatusBoard]:
        params = self.params_for(manifest)
        base = params.base_url.rstrip("/")
        status = await self._fetch(manifest, params,
                                   base + params.status_path)
        if not isinstance(status.payload, list):
            raise SourceSchemaChanged(
                f"{manifest.id}: the status board is a "
                f"{type(status.payload).__name__} rather than an array of "
                "systems.")
        systems = [_system(e, manifest.id) for e in status.payload]
        wanted = system.strip().lower()
        if wanted:
            known = {s.name.lower() for s in systems}
            if wanted not in known:
                raise InvalidQuery(
                    f"{params.facility} lists no system named {system!r}. "
                    f"Its systems: {sorted(s.name for s in systems)}.")
        chosen = [s for s in systems if not wanted or s.name.lower() == wanted]

        planned: list[PlannedOutage] = []
        if include_planned:
            outages = await self._fetch(manifest, params,
                                        base + params.planned_path)
            planned = self._planned(outages.payload, manifest.id)
            if wanted:
                planned = [o for o in planned if o.system.lower() == wanted]
        board = StatusBoard(facility=params.facility, systems=chosen,
                            planned=planned, systems_on_board=len(systems))
        log_source_call(manifest, "board", {"system": system}, len(chosen))
        return Fetched.of(status, board)

    @staticmethod
    def _planned(payload: Any, source_id: str) -> list[PlannedOutage]:
        if not isinstance(payload, list):
            raise SourceSchemaChanged(
                f"{source_id}: the planned-outage list is a "
                f"{type(payload).__name__} rather than an array.")
        flat: list[Any] = []
        for group in payload:
            flat.extend(group if isinstance(group, list) else [group])
        out = [_outage(e, source_id) for e in flat]
        out.sort(key=lambda o: (o.start_at or "", o.system))
        return out

    async def _fetch(self, manifest: SourceManifest,
                     params: FacilityStatusParams, url: str) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, {}, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, {})
        return self._cache.put(manifest.id, url, {}, response.payload,
                               response.headers)
