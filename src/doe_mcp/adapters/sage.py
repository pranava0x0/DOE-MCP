"""The `sage` adapter: the node manifest of an edge-sensor network.

Sage/Waggle is Argonne's distributed network of instrumented edge computers —
cameras, weather and air-quality sensors, microphones and lidar on masts in
cities, forests, and at field sites — each running analysis code beside the
sensor rather than shipping raw frames home. Its node manifest is a single
public JSON document: 295 nodes, verified live 2026-09-09, with each node's
deployment phase, project, coordinates, computers, and instruments.

**This adapter reads the inventory, not the measurements, and the reason is
a property of the client rather than a property of the publisher.** Sage's
measurement query API answers only to POST with a JSON filter body. Every
fetch path in this project is a GET (`Fetcher` has one verb, and the
read-only-by-construction rule in AGENTS.md is what that buys), so the
measurement stream is out of reach here by design rather than by oversight.
Naming it in the manifest and refusing to reach it is the honest form of
that; a caller who needs readings is told where they are and what shape the
request takes. Decision 0023 records the choice.

An inventory is worth having on its own terms, the same way the two USGS
facility databases are: "where are the nodes and what is on them" is the
question that precedes every question about their data, and no other source
in this registry answers it.

The document is 2 MB and is fetched whole and filtered in memory, like the
`json_document` adapter's catalogs. That is the right shape at this size and
would not be at ten times it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

DEFAULT_ROWS = 20
MAX_ROWS = 200
EARTH_RADIUS_KM = 6371.0


class SageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_url: str
    measurement_endpoint: str
    """The measurement API this adapter does NOT call. Recorded so the
    refusal can name it: a caller who needs readings gets the endpoint and
    the shape of the request rather than an empty result."""
    measurement_note: str

    @field_validator("measurement_note", mode="after")
    @classmethod
    def _tidy(cls, v: str) -> str:
        return " ".join(v.split())


register_adapter_params("sage", SageParams)


@dataclass(frozen=True)
class Instrument:
    name: str
    hardware: str | None
    model: str | None
    manufacturer: str | None
    datasheet: str | None
    is_active: bool | None


@dataclass
class Node:
    vsn: str
    """The node's call sign — 'W08D' — which is how Sage's own tooling,
    dashboards and papers refer to it."""
    node_id: str | None
    phase: str | None
    project: str | None
    address: str | None
    latitude: float | None
    longitude: float | None
    tags: list[str] = field(default_factory=list)
    computes: list[str] = field(default_factory=list)
    sensors: list[Instrument] = field(default_factory=list)

    @property
    def deployed(self) -> bool:
        return (self.phase or "").lower() == "deployed"


@dataclass
class NodePage:
    nodes: list[Node]
    total_matched: int
    total_nodes: int
    offset: int
    without_coordinates: int
    """Matched nodes the publisher lists with no position. Reported rather
    than dropped: a node awaiting deployment has no coordinates yet, and
    silently omitting it would understate a project's size."""


def _text(node: Any) -> str | None:
    return node.strip() or None if isinstance(node, str) else None


def _instrument(entry: Any) -> Instrument | None:
    if not isinstance(entry, dict):
        return None
    name = _text(entry.get("name"))
    if name is None:
        return None
    hardware = entry.get("hardware") if isinstance(
        entry.get("hardware"), dict) else {}
    return Instrument(
        name=name, hardware=_text(hardware.get("hardware")),
        model=_text(hardware.get("hw_model")),
        manufacturer=_text(hardware.get("manufacturer")),
        datasheet=_text(hardware.get("datasheet")),
        is_active=entry.get("is_active"))


def parse_node(entry: dict, source_id: str) -> Node:
    vsn = _text(entry.get("vsn"))
    if vsn is None:
        raise SourceSchemaChanged(
            f"{source_id}: a node record carries no 'vsn'. That is the only "
            "identifier the network uses for a node.")
    return Node(
        vsn=vsn, node_id=_text(entry.get("name")),
        phase=_text(entry.get("phase")), project=_text(entry.get("project")),
        address=_text(entry.get("address")),
        latitude=_coordinate(entry.get("gps_lat")),
        longitude=_coordinate(entry.get("gps_lon")),
        tags=[t for t in (_text(t) for t in entry.get("tags") or []) if t],
        computes=[c for c in (_text(c.get("name"))
                              for c in entry.get("computes") or []
                              if isinstance(c, dict)) if c],
        sensors=[s for s in (_instrument(s)
                             for s in entry.get("sensors") or [])
                 if s is not None])


def _coordinate(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Spherical rather than ellipsoidal on purpose:
    the answer is used to sort nodes by nearness over tens of kilometres,
    where the difference is metres, and an exact figure would imply a
    precision the coordinates do not have."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


class SageAdapter:
    """Read-only, and GET-only, which is what keeps it to the inventory."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> SageParams:
        return SageParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: SageParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.manifest_url))

    async def nodes(self, manifest: SourceManifest, *, vsn: str = "",
                    project: str = "", phase: str = "", instrument: str = "",
                    near: tuple[float, float] | None = None,
                    within_km: float = 0.0, rows: int = DEFAULT_ROWS,
                    offset: int = 0) -> Fetched[NodePage]:
        params = self.params_for(manifest)
        if rows < 1 or rows > MAX_ROWS:
            raise InvalidQuery(f"rows must be between 1 and {MAX_ROWS}.")
        if offset < 0:
            raise InvalidQuery("offset must be zero or more.")
        if within_km and near is None:
            raise InvalidQuery(
                "within_km needs a point to measure from; pass latitude and "
                "longitude as well.")

        result = await self._fetch(manifest, params)
        everything = self._parse_all(result.payload, manifest.id)
        matched = [n for n in everything
                   if _matches(n, vsn, project, phase, instrument)]
        if near is not None:
            matched = _by_distance(matched, near, within_km)
        page = NodePage(
            nodes=matched[offset:offset + rows], total_matched=len(matched),
            total_nodes=len(everything), offset=offset,
            without_coordinates=sum(1 for n in matched
                                    if n.latitude is None))
        log_source_call(manifest, "nodes",
                        {"vsn": vsn, "project": project, "phase": phase,
                         "instrument": instrument}, len(page.nodes))
        return Fetched.of(result, page)

    @staticmethod
    def _parse_all(payload: Any, source_id: str) -> list[Node]:
        if not isinstance(payload, list):
            raise SourceSchemaChanged(
                f"{source_id}: the node manifest is a "
                f"{type(payload).__name__} rather than an array of nodes.")
        return [parse_node(e, source_id) for e in payload
                if isinstance(e, dict)]

    async def _fetch(self, manifest: SourceManifest,
                     params: SageParams) -> FetchResult:
        url = params.manifest_url
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, {}, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, {})
        return self._cache.put(manifest.id, url, {}, response.payload,
                               response.headers)


def _matches(node: Node, vsn: str, project: str, phase: str,
             instrument: str) -> bool:
    if vsn and node.vsn.lower() != vsn.strip().lower():
        return False
    if project and (node.project or "").lower() != project.strip().lower():
        return False
    if phase and (node.phase or "").lower() != phase.strip().lower():
        return False
    if instrument:
        wanted = instrument.strip().lower()
        if not any(wanted in (s.hardware or "").lower()
                   or wanted in s.name.lower()
                   or wanted in (s.model or "").lower()
                   for s in node.sensors):
            return False
    return True


def _by_distance(nodes: list[Node], near: tuple[float, float],
                 within_km: float) -> list[Node]:
    """Nearest first, with the positionless nodes kept at the end.

    Keeping them is deliberate. Seventy-nine of the 295 nodes carry no
    coordinates because they have not been installed yet, and a proximity
    search that dropped them would report a network smaller than it is
    without saying so; the page carries the count instead.
    """
    lat, lon = near
    placed = [n for n in nodes if n.latitude is not None
              and n.longitude is not None]
    unplaced = [n for n in nodes if n.latitude is None or n.longitude is None]
    measured = [(n, _distance_km(lat, lon, n.latitude, n.longitude))
                for n in placed]
    if within_km:
        measured = [(n, d) for n, d in measured if d <= within_km]
        unplaced = []
    measured.sort(key=lambda pair: pair[1])
    return [n for n, _ in measured] + unplaced
