"""Build a complete Daymet point CSV with response-derived units and metadata."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import math

from ..core.errors import SourceSchemaChanged
from ..daymet_package import DATA_USE, croissant_context
from ..demo_worksheets import csv_text
from .bundle import json_bytes


def package(data: dict, *, latitude: float, longitude: float, start: str, end: str,
            variables: str) -> tuple[bytes, dict]:
    # The service prints coordinates to four decimals, not at input precision.
    if (not math.isclose(data["latitude"], latitude, abs_tol=0.000051, rel_tol=0)
            or not math.isclose(data["longitude"], longitude, abs_tol=0.000051, rel_tol=0)):
        raise SourceSchemaChanged("Daymet coordinate echo differs from the requested point")
    if data["requested_range"] != {"from": start, "to": end}:
        raise SourceSchemaChanged("Daymet request range differs from package inputs")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    expected = [d.isoformat() for d in dates if d.timetuple().tm_yday != 366]
    if not expected or [r["date"] for r in data["days"]] != expected:
        raise SourceSchemaChanged("Daymet package requires every requested date in its 365-day calendar")
    if data["returned_range"] != {"from": expected[0], "to": expected[-1]}:
        raise SourceSchemaChanged("Daymet returned range differs from the complete package calendar")
    columns = data["columns"]
    names = [c["name"] for c in columns]
    wanted = [v.strip() for v in variables.split(",") if v.strip()]
    if not columns or len(names) != len(set(names)) or (wanted and set(names) != set(wanted)):
        raise SourceSchemaChanged("Daymet returned columns differ from the requested variables")
    if not data["publisher_citation"] or not data["calendar"] or any(not c["units"] for c in columns):
        raise SourceSchemaChanged("Daymet package needs publisher citation, calendar and response units")
    for row in data["days"]:
        if row["yday"] != date.fromisoformat(row["date"]).timetuple().tm_yday:
            raise SourceSchemaChanged("Daymet day number differs from the calendar date")
        if set(row["values"]) != set(names) or any(type(row["values"][n]) not in (int, float) or not math.isfinite(row["values"][n]) for n in names):
            raise SourceSchemaChanged("Daymet package contains a missing or non-finite measurement")
    # Names and unit annotations come from the returned CSV header.
    headers = ["date", "yday", *[f"{c['name']} ({c['units']})" for c in columns]]
    payload = csv_text(headers, [[r["date"], r["yday"], *[r["values"][n] for n in names]] for r in data["days"]]).encode()
    context = croissant_context()
    fields = [("date", "Date", "Calendar date in the Daymet calendar."),
              ("yday", "Integer", "Day number, from 1 through 365.")]
    fields += [(h, "Float", f"{c.get('description') or c['name']}. Units: {c['units']}.") for h, c in zip(headers[2:], columns, strict=True)]
    description = (f"Daymet modelled daily weather at the returned point {data['latitude']}, {data['longitude']}, "
                   f"requested {start} through {end}. {data['calendar']}")
    metadata = {"@context": context, "@type": "sc:Dataset", "conformsTo": "http://mlcommons.org/croissant/1.0",
                "name": "weather-package", "description": description, "version": "1.0", "license": DATA_USE,
                "creator": {"@type": "Organization", "name": "DOE-MCP"}, "citation": data["publisher_citation"],
                "citeAs": data["publisher_citation"],
                "distribution": [{"@type": "cr:FileObject", "@id": "weather", "name": "weather.csv",
                                  "contentUrl": "weather.csv", "encodingFormat": "text/csv",
                                  "contentSize": str(len(payload)), "sha256": hashlib.sha256(payload).hexdigest()}],
                "recordSet": [{"@type": "cr:RecordSet", "@id": "days", "name": "days", "description": description,
                               "field": [{"@type": "cr:Field", "@id": "days/" + (names[i - 2] if i >= 2 else name),
                                          "name": name, "description": desc, "dataType": "sc:" + kind,
                                          "source": {"fileObject": {"@id": "weather"}, "extract": {"column": name}}}
                                         for i, (name, kind, desc) in enumerate(fields)]}]}
    validate_metadata(payload, metadata, headers)
    return payload, metadata


def validate_metadata(payload: bytes, metadata: dict, headers: list[str]):
    distribution, = metadata["distribution"]
    fields = metadata["recordSet"][0]["field"]
    if (distribution["sha256"] != hashlib.sha256(payload).hexdigest()
            or int(distribution["contentSize"]) != len(payload)
            or [f["source"]["extract"]["column"] for f in fields] != headers
            or len({f["@id"] for f in fields}) != len(fields)):
        raise SourceSchemaChanged("Croissant metadata does not describe the exported CSV")


async def build(run, **params):
    env = await run.call("earth.get_daymet_point", **params)
    payload, metadata = package(env.data, **params)
    run.report.update(parameters=params, row_count=len(env.data["days"]), columns=env.data["columns"],
                      metadata_validation="CSV checksum, byte count, field IDs and response-derived columns checked")
    run.report["limitations"] += ["Daymet reports modelled weather for a 1 km grid cell, not station observations.",
        "Leap day is retained; December 31 is omitted in leap years. No absent or missing values are imputed.",
        "Croissant metadata describes this exported CSV; its checksum does not identify the publisher's full archive."]
    return {"weather.csv": payload, "weather-croissant.json": json_bytes(metadata)}
