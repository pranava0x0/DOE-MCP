"""Prepared monthly PVWatts v8 contract; its source remains proposed."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.credentials import Credentials
from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import register_adapter_params
from .base import Fetched, HttpFetcher, egress_policy_for, shared_cache
from .replay import redact_secrets


class PvwattsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


register_adapter_params("pvwatts_v8", PvwattsParams)


class MonthlyInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    system_capacity: float = Field(ge=0.05, le=500000)
    module_type: int = Field(ge=0, le=2)
    losses: float = Field(ge=-5, le=99)
    array_type: int = Field(ge=0, le=4)
    tilt: float = Field(ge=0, le=90)
    azimuth: float = Field(ge=0, lt=360)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    dataset: Literal["nsrdb", "tmy2", "tmy3", "intl"] = "nsrdb"
    dc_ac_ratio: float = Field(default=1.2, gt=0)
    gcr: float = Field(default=0.4, ge=0.01, le=0.99)
    inv_eff: float = Field(default=96, ge=90, le=99.5)


def validate_inputs(inputs: dict) -> dict:
    try:
        return MonthlyInputs.model_validate(inputs).model_dump() | {"timeframe": "monthly"}
    except ValidationError:
        raise InvalidQuery("PVWatts requires valid monthly system/location inputs; credential, hourly and arbitrary parameters are not accepted") from None


def validate_output(payload, inputs, secret: str) -> dict:
    # Never expose the publisher's echoed api_key, including outside inputs.
    payload = redact_secrets(payload, frozenset({secret}))
    if not isinstance(payload, dict):
        raise SourceSchemaChanged("PVWatts returned no response object")
    for key in ("errors", "warnings"):
        if not isinstance(payload.get(key), list) or any(not isinstance(item, str) for item in payload[key]):
            raise SourceSchemaChanged("PVWatts errors or warnings have an unexpected shape")
    if payload["errors"]:
        raise InvalidQuery("PVWatts refused the model inputs: " + "; ".join(payload["errors"]))
    echoed = payload.get("inputs")
    if not isinstance(echoed, dict):
        raise SourceSchemaChanged("PVWatts omitted its input echo")
    for key, value in inputs.items():
        actual = echoed.get(key)
        if isinstance(value, (int, float)):
            try:
                equal = not isinstance(actual, bool) and math.isfinite(float(actual)) and math.isclose(float(actual), value, rel_tol=1e-9, abs_tol=1e-9)
            except (ValueError, TypeError):
                equal = False
        else:
            equal = actual == value
        if not equal:
            raise SourceSchemaChanged("PVWatts input echo differs from the submitted model parameters")
    outputs = payload.get("outputs")
    monthly = ("ac_monthly", "dc_monthly", "poa_monthly", "solrad_monthly")
    annual = ("ac_annual", "solrad_annual", "capacity_factor")
    def valid(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    if (not isinstance(outputs, dict) or any(not valid(outputs.get(key)) for key in annual)
            or any(not isinstance(outputs.get(key), list) or len(outputs[key]) != 12
                   or not all(valid(value) for value in outputs[key]) for key in monthly)):
        raise SourceSchemaChanged("PVWatts monthly output requires twelve finite values per series and finite annual summaries")
    if not math.isclose(sum(outputs["ac_monthly"]), outputs["ac_annual"], rel_tol=1e-5, abs_tol=0.01):
        raise SourceSchemaChanged("PVWatts annual AC output differs from its monthly sum")
    if (not isinstance(payload.get("version"), str) or not payload["version"].startswith("8.")
            or not isinstance(payload.get("ssc_info"), dict) or payload["ssc_info"].get("module") != "pvwattsv8"
            or not isinstance(payload.get("station_info"), dict)
            or not isinstance(payload["station_info"].get("weather_data_source"), str)
            or not payload["station_info"]["weather_data_source"]):
        raise SourceSchemaChanged("PVWatts model version or weather-source metadata is missing or changed")
    station_fields = {"lat", "lon", "elev", "tz", "location", "city", "state", "solar_resource_file", "distance", "weather_data_source"}
    return {"inputs": dict(inputs), "outputs": {key: outputs[key] for key in (*monthly, *annual)},
            "warnings": payload["warnings"], "version": payload["version"],
            "ssc_info": {key: payload["ssc_info"][key] for key in ("version", "build", "module") if key in payload["ssc_info"]},
            "station_info": {key: value for key, value in payload["station_info"].items() if key in station_fields},
            "units": {"ac_monthly": "kWhac", "ac_annual": "kWhac", "dc_monthly": "kWhdc",
                      "poa_monthly": "kWh/m2", "solrad_monthly": "kWh/m2/day", "solrad_annual": "kWh/m2/day", "capacity_factor": "%"},
            "interpretation": "Modelled typical-weather production, not observed generation or a forecast for a named calendar year."}


class PvwattsAdapter:
    version = "1"

    def __init__(self, fetcher=None, cache=None, credentials: Credentials | None = None):
        self._fetcher = fetcher
        self._cache = cache or shared_cache()
        self._credentials = credentials

    async def model(self, manifest, inputs: dict):
        parameters = validate_inputs(inputs)
        if self._credentials is None:
            raise InvalidQuery("PVWatts requires an explicitly scoped credential store after activation")
        secret = self._credentials.require("API_DATA_GOV_KEY")
        result = self._cache.get(manifest.id, manifest.adapter.base_url, parameters, manifest.freshness.ttl_hint_seconds)
        if result is None:
            params = PvwattsParams.model_validate(manifest.adapter.model_dump(exclude={"type"}))
            fetcher = self._fetcher or HttpFetcher(egress_policy_for(manifest, params.base_url))
            response = await fetcher.fetch_json(params.base_url, {**parameters, "api_key": secret})
            safe = validate_output(response.payload, parameters, secret)
            result = self._cache.put(manifest.id, params.base_url, parameters, safe, response.headers)
        return Fetched.of(result, result.payload)
