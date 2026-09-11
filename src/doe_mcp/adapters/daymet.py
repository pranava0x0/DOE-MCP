"""The `daymet` adapter: a point time series delivered as CSV with a preamble.

ORNL DAAC's Daymet single-pixel service answers one question — what was the
daily surface weather at this spot — for every 1 km cell in North America
from 1980 onward. It is the best-shaped endpoint in this ecosystem's earth
domain and one of the worst-behaved when a request is slightly wrong, which
is why this adapter is mostly validation.

The response, verified live 2026-09-09:

    Latitude: 35.9313  Longitude: -84.3104
    X & Y on Lambert Conformal Conic: 1352953.24 -569846.6
    Tile: 11208
    Elevation: 261 meters
    All years; all variables; Daymet Software Version 4.0
    How to cite: Thornton; M.M.; ... https://doi.org/10.3334/ORNLDAAC/2129
    year,yday,prcp (mm/day),tmax (deg c),tmin (deg c)
    2023,152,0.00,28.62,16.90

Four properties decide the shape of everything below, and three of them
produce a confidently wrong answer if they are not handled:

1. **An unknown variable name is ignored, not refused.** `vars=notavar`
    returns all eight variables with HTTP 200 and no message. A caller who
    typed `tmean` instead of `tmax` gets eight columns of real data and no
    sign that the request was not the one that ran. So the variable set is a
    manifest field and every name is checked against it before a request is
    sent.

2. **A start date before the record is clamped, silently.** Asking for 1970
    returns 1980 — real numbers, under the caller's own year. Nothing in the
    response says the window moved. The requested range and the returned
    range are therefore both carried on the result, and the tool compares
    them.

3. **The calendar is 365 days, always.** Daymet keeps 29 February and drops
    31 December in leap years, so `yday` runs 1-365 in every year and
    `date(year, 1, 1) + yday - 1` is the correct conversion in both. The
    missing day is real: a leap-year annual total from this service is a
    365-day total, and the result says so rather than leaving the gap to be
    found in the arithmetic.

4. **A point outside the grid is a 400 carrying its own message** ("Daymet
    Tile was not found with input lat and lon"), which the fetch path turns
    into `InvalidQuery` with the publisher's own words. That is the one
    error here that is genuinely the caller's rather than the publisher's.

The values are modelled 1 km grid estimates interpolated from weather
stations, not station observations. Nothing in the response says so, and it
is the difference between "the temperature at my field site" and "the
temperature the model puts on the kilometre my field site is in" — so it is
said on the result instead.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, FetchResult, HttpFetcher, TextFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

MAX_DAYS = 3660
"""Ten years per request, this client's ceiling rather than the publisher's.
The service will return the whole record for a point — 45 years of daily
rows across eight variables — and the egress cap would stop that read partway
and report it as an outage, which is a confusing way to learn that a request
was too broad."""

# "prcp (mm/day)" -> ("prcp", "mm/day"). The units live in the column header
# and nowhere else, so they are parsed rather than assumed: srad is W/m^2 and
# swe is kg/m^2, and a series relabelled by a hardcoded table would be wrong
# in the units rather than in the numbers.
_COLUMN = re.compile(r"^(?P<name>[A-Za-z0-9_]+)\s*(?:\((?P<units>[^)]*)\))?$")

_PROJECTION_KEY = "X & Y"
_PREAMBLE_KEYS = ("Latitude:", _PROJECTION_KEY, "Tile:", "Elevation:",
                  "How to cite:")
_HEADER_PREFIX = "year,yday"


class DaymetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    variables: dict[str, str]
    """Variable name -> what it is, in the publisher's terms. A manifest
    field because the service treats an unknown name as no name at all: this
    table is the only thing standing between a typo and eight columns of
    data the caller did not ask for."""
    first_year: int
    """First year of the record. The service clamps an earlier request
    rather than refusing it."""
    domain_note: str
    calendar_note: str
    grid_note: str

    @field_validator("domain_note", "calendar_note", "grid_note",
                     mode="after")
    @classmethod
    def _tidy(cls, v: str) -> str:
        return " ".join(v.split())


register_adapter_params("daymet", DaymetParams)


@dataclass(frozen=True)
class Column:
    name: str
    units: str | None


@dataclass(frozen=True)
class DayRow:
    date: str
    """ISO date, derived from year and yday. Derived rather than published:
    the service reports a day number and the calendar it belongs to is the
    one property of this dataset a caller is most likely to get wrong."""
    year: int
    yday: int
    values: dict[str, float | None]


@dataclass
class PointSeries:
    latitude: float
    longitude: float
    elevation_meters: float | None
    tile: str | None
    projected_xy: str | None
    software_version: str | None
    citation: str | None
    columns: list[Column]
    rows: list[DayRow] = field(default_factory=list)
    requested_start: str = ""
    requested_end: str = ""

    @property
    def returned_start(self) -> str | None:
        return self.rows[0].date if self.rows else None

    @property
    def returned_end(self) -> str | None:
        return self.rows[-1].date if self.rows else None

    def missing_leap_days(self) -> list[str]:
        """The 31 December dates the caller asked for that this dataset does
        not have.

        Measured against the REQUESTED range, not the returned one: the
        whole point is that the date is absent, so it can never fall inside
        the span that came back. Reported rather than interpolated — a year
        with 365 rows is Daymet's calendar working as designed, and a caller
        summing a leap year should be told the total is a 365-day total
        rather than discover it in the arithmetic.
        """
        absent = []
        for year in sorted({row.year for row in self.rows}):
            if not _is_leap(year):
                continue
            last = f"{year}-12-31"
            if self.requested_start <= last <= self.requested_end:
                absent.append(last)
        return absent


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _number(cell: str) -> float | None:
    text = cell.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(iso: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(iso.strip())
    except ValueError as err:
        raise InvalidQuery(
            f"{label} must be an ISO date such as 2023-06-01; got "
            f"{iso!r}.") from err


def parse_series(text: str, source_id: str, *, requested_start: str,
                 requested_end: str) -> PointSeries:
    lines = [ln.rstrip("\r") for ln in text.replace("\r\n", "\n").split("\n")]
    head = next((i for i, ln in enumerate(lines)
                 if ln.startswith(_HEADER_PREFIX)), None)
    if head is None:
        raise SourceSchemaChanged(
            f"{source_id}: no {_HEADER_PREFIX!r} column-header row in the "
            "response. The single-pixel service's layout has changed, or "
            "what came back is an error page rather than a series.")
    preamble = [ln for ln in lines[:head] if ln.strip()]
    header = [c.strip() for c in lines[head].split(",")]
    columns = [_column(cell, source_id) for cell in header[2:]]

    rows: list[DayRow] = []
    for line in lines[head + 1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        if len(cells) != len(header):
            raise SourceSchemaChanged(
                f"{source_id}: a data row has {len(cells)} field(s) against "
                f"{len(header)} header column(s). The column set changed "
                "mid-response, which no column mapping can survive.")
        year, yday = int(cells[0]), int(cells[1])
        rows.append(DayRow(
            date=(dt.date(year, 1, 1)
                  + dt.timedelta(days=yday - 1)).isoformat(),
            year=year, yday=yday,
            values={col.name: _number(cell)
                    for col, cell in zip(columns, cells[2:],
                                         strict=True)}))

    fields = _preamble_fields(preamble)
    lat, lon = _coordinates(fields.get("Latitude:"), source_id)
    return PointSeries(
        latitude=lat, longitude=lon,
        elevation_meters=_elevation(fields.get("Elevation:")),
        tile=fields.get("Tile:"), projected_xy=fields.get("X & Y"),
        software_version=fields.get("version"),
        citation=fields.get("How to cite:"), columns=columns, rows=rows,
        requested_start=requested_start, requested_end=requested_end)


def _column(cell: str, source_id: str) -> Column:
    match = _COLUMN.match(cell.strip())
    if match is None:
        raise SourceSchemaChanged(
            f"{source_id}: column header {cell!r} is not in the "
            "'name (units)' form every Daymet column uses.")
    return Column(name=match.group("name"), units=match.group("units"))


def _preamble_fields(preamble: list[str]) -> dict[str, str]:
    """The labelled preamble lines, by label.

    The version line carries no label of its own ("All years; all variables;
    Daymet Software Version 4.0"), so it is keyed separately rather than
    guessed at by position.
    """
    fields: dict[str, str] = {}
    for line in preamble:
        for key in _PREAMBLE_KEYS:
            if not line.startswith(key):
                continue
            value = line[len(key):].strip()
            # The projection line names its projection between the key and
            # the numbers ("X & Y on Lambert Conformal Conic: 1352953.24
            # -569846.6"), so the key's length alone leaves a label in front
            # of the value. Only this line is split on its colon: the
            # citation line holds several, and the coordinate line's colon
            # sits between the two numbers rather than before them.
            if key == _PROJECTION_KEY and ":" in value:
                fields[key] = value.split(":", 1)[1].strip()
            else:
                fields[key] = value
            break
        else:
            if "Version" in line:
                fields["version"] = line.strip()
    return fields


def _coordinates(value: str | None, source_id: str) -> tuple[float, float]:
    """The echoed coordinates, from "35.9313  Longitude: -84.3104".

    These are the coordinates the caller sent, not the centre of the cell
    that answered — the service reports no cell centre at all. They are
    still read from the response rather than carried over from the request,
    because a response that echoed something else would be a changed service
    and the whole point of reading it here is to find that out.
    """
    if not value:
        raise SourceSchemaChanged(
            f"{source_id}: the response preamble carries no Latitude line.")
    parts = value.replace("Longitude:", " ").split()
    try:
        return float(parts[0]), float(parts[1])
    except (IndexError, ValueError) as err:
        raise SourceSchemaChanged(
            f"{source_id}: could not read a latitude and longitude pair out "
            f"of the preamble line {value!r}.") from err


def _elevation(value: str | None) -> float | None:
    if not value:
        return None
    return _number(value.split()[0])


class DaymetAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: TextFetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> DaymetParams:
        return DaymetParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: DaymetParams) -> TextFetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def point_series(self, manifest: SourceManifest, *,
                           latitude: float, longitude: float,
                           variables: list[str], start: str,
                           end: str) -> Fetched[PointSeries]:
        params = self.params_for(manifest)
        names = self._checked_variables(variables, params)
        first, last = self._checked_range(start, end, params)
        query = {"lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}",
                 "vars": ",".join(names), "start": first.isoformat(),
                 "end": last.isoformat()}
        result = await self._fetch(manifest, params, query)
        series = parse_series(result.payload, manifest.id,
                              requested_start=first.isoformat(),
                              requested_end=last.isoformat())
        log_source_call(manifest, "point_series", query, len(series.rows))
        return Fetched.of(result, series)

    @staticmethod
    def _checked_variables(variables: list[str],
                           params: DaymetParams) -> list[str]:
        """Every requested name, checked against the published set.

        The check is the whole reason this method exists: the service
        answers an unknown name by returning every variable it has, at HTTP
        200, so a typo reaches the caller as more data rather than as an
        error. Refusing here is the only place that difference can still be
        seen.
        """
        names = [v.strip() for v in variables if v.strip()]
        if not names:
            return sorted(params.variables)
        unknown = [n for n in names if n not in params.variables]
        if unknown:
            raise InvalidQuery(
                f"unknown Daymet variable(s): {', '.join(sorted(unknown))}. "
                f"The service has {', '.join(sorted(params.variables))} and "
                "answers a name it does not know by returning ALL of them "
                "with no error, so this is refused here rather than passed "
                "on.")
        return names

    @staticmethod
    def _checked_range(start: str, end: str,
                       params: DaymetParams) -> tuple[dt.date, dt.date]:
        first, last = _parse_date(start, "start"), _parse_date(end, "end")
        if first > last:
            raise InvalidQuery(
                f"start {first.isoformat()} is after end {last.isoformat()}.")
        if first.year < params.first_year:
            raise InvalidQuery(
                f"Daymet begins in {params.first_year} and the service "
                f"answers an earlier start by silently returning "
                f"{params.first_year} data under the year you asked for. "
                f"Ask for {params.first_year}-01-01 or later.")
        span = (last - first).days + 1
        if span > MAX_DAYS:
            raise InvalidQuery(
                f"the requested span is {span} days; this client asks for at "
                f"most {MAX_DAYS} (ten years) in one call. Narrow the range "
                "or make several calls.")
        return first, last

    async def _fetch(self, manifest: SourceManifest, params: DaymetParams,
                     query: dict[str, str]) -> FetchResult:
        url = params.base_url
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_text(
            url, query)
        return self._cache.put(manifest.id, url, query, response.text,
                               response.headers)
