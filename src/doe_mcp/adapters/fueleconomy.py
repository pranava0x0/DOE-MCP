"""The `fueleconomy` adapter: the DOE/EPA vehicle fuel-economy web service.

A small, keyless, stable REST service ORNL runs for DOE and EPA jointly —
which is why its manifest's publisher triple has three different values and
why the envelope displays all three. Calling this "DOE data" without naming
EPA would be an overclaim; calling it EPA's would drop the operator.

Two shapes, both verified live 2026-09-02:

  /ws/rest/vehicle/menu/{year|make|model|options}
      `{"menuItem": [...]}` — a drill-down. The `options` step is what
      turns a year/make/model into the vehicle ids everything else needs.
  /ws/rest/vehicle/{id}           one vehicle, ~140 fields
  /ws/rest/fuelprices             current national average prices

The single-item quirk: when a drill-down step has exactly one answer,
`menuItem` is an OBJECT rather than a one-element array. A caller iterating
the value gets the dict's keys — "text", "value" — as if they were two
results. Normalized here, once.
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

# The fields worth lifting out of ~140. Everything else stays in `raw`.
VEHICLE_FIELDS = (
    "make", "model", "year", "VClass", "trany", "drive", "cylinders",
    "displ", "fuelType", "fuelType1", "fuelType2", "atvType",
    "city08", "highway08", "comb08", "cityA08", "highwayA08", "combA08",
    "cityE", "highwayE", "combE", "range", "rangeCity", "rangeHwy",
    "co2TailpipeGpm", "co2", "barrels08", "fuelCost08", "youSaveSpend",
    "ghgScore", "phevBlended", "battery",
)


class FuelEconomyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


register_adapter_params("fueleconomy", FuelEconomyParams)


@dataclass
class MenuOption:
    text: str
    value: str


@dataclass
class Vehicle:
    vehicle_id: str
    fields: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


def _menu_items(payload: Any, url: str) -> list[MenuOption]:
    if not isinstance(payload, dict) or "menuItem" not in payload:
        raise SourceSchemaChanged(
            f"fueleconomy.gov returned no 'menuItem' for {url}; the web "
            "service's response shape has changed.")
    items = payload["menuItem"]
    # One result arrives as an object, not a one-element list.
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise SourceSchemaChanged(
            "fueleconomy.gov 'menuItem' was neither an object nor a list.")
    return [MenuOption(text=str(i.get("text", "")),
                       value=str(i.get("value", "")))
            for i in items if isinstance(i, dict)]


class FuelEconomyAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> FuelEconomyParams:
        return FuelEconomyParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: FuelEconomyParams) -> Fetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def menu(self, manifest: SourceManifest, step: str,
                   **selectors: str) -> Fetched[list[MenuOption]]:
        if step not in ("year", "make", "model", "options"):
            raise InvalidQuery(
                f"menu step {step!r} is not one of year/make/model/options. "
                "The service is a drill-down: year, then make, then model, "
                "then options — and only the options step yields the vehicle "
                "ids the detail lookup needs.")
        params = self.params_for(manifest)
        url = f"{params.base_url.rstrip('/')}/vehicle/menu/{step}"
        query = {k: v for k, v in selectors.items() if v}
        result = await self._fetch(manifest, params, url, query)
        options = _menu_items(result.payload, url)
        log_source_call(manifest, f"menu.{step}", query, len(options))
        return Fetched.of(result, options)

    async def get_vehicle(self, manifest: SourceManifest,
                          vehicle_id: str) -> Fetched[Vehicle | None]:
        params = self.params_for(manifest)
        if not vehicle_id.strip().isdigit():
            raise InvalidQuery(
                f"vehicle_id must be numeric; got {vehicle_id!r}. Get one "
                "from the options menu step, not by guessing.")
        url = f"{params.base_url.rstrip('/')}/vehicle/{vehicle_id.strip()}"
        result = await self._fetch(manifest, params, url, {})
        raw = result.payload
        if not isinstance(raw, dict) or not raw:
            log_source_call(manifest, "get_vehicle", {"vehicle_id": vehicle_id},
                            0)
            return Fetched.of(result, None)
        log_source_call(manifest, "get_vehicle", {"vehicle_id": vehicle_id}, 1)
        return Fetched.of(result, Vehicle(
            vehicle_id=str(raw.get("id") or vehicle_id),
            fields={k: raw[k] for k in VEHICLE_FIELDS if k in raw},
            raw=raw))

    async def fuel_prices(self, manifest: SourceManifest
                          ) -> Fetched[dict[str, str]]:
        params = self.params_for(manifest)
        url = f"{params.base_url.rstrip('/')}/fuelprices"
        result = await self._fetch(manifest, params, url, {})
        raw = result.payload
        if not isinstance(raw, dict):
            raise SourceSchemaChanged(
                "fueleconomy.gov fuel prices returned a non-object body.")
        log_source_call(manifest, "fuel_prices", {}, len(raw))
        return Fetched.of(result, {k: str(v) for k, v in raw.items()})

    async def _fetch(self, manifest: SourceManifest,
                     params: FuelEconomyParams, url: str,
                     query: dict[str, Any]) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, query)
        return self._cache.put(manifest.id, url, query, response.payload,
                               response.headers)
