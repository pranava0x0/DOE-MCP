"""Describe the recorded Daymet CSV that DOE-MCP distributes."""
from __future__ import annotations

import hashlib

from .core.errors import SourceSchemaChanged
from .demo_worksheets import csv_text

CASE = "daymet-calendar"
CSV_NAME = CASE + "-temperatures.csv"
METADATA_NAME = CASE + "-croissant.json"
DATA_USE = "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance"


def package(data: dict) -> tuple[bytes, dict]:
    """Serialize first: the checksum describes this CSV, not the ORNL archive."""
    columns = data["columns"]
    if len(columns) != 1 or (columns[0]["name"], columns[0]["units"]) != ("tmax", "deg c"):
        raise SourceSchemaChanged("Daymet did not return daily maximum temperature in degrees Celsius")
    if not data["publisher_citation"]:
        raise SourceSchemaChanged("Daymet returned no citation for the temperature extract")
    fields = [
        ("date", "Date", "Calendar date derived from Daymet's year and day number."),
        ("yday", "Integer", "Day number in the publisher's 365-day calendar."),
        ("tmax_deg_c", "Float", "Modelled daily maximum air temperature in degrees Celsius."),
    ]
    rows = [(r["date"], r["yday"], r["values"]["tmax"]) for r in data["days"]]
    payload = csv_text([f[0] for f in fields], rows).encode()
    description = (f"Recorded Daymet temperatures for the 1 km cell at {data['latitude']}, "
                   f"{data['longitude']}. Requested {data['requested_range']['from']} through "
                   f"{data['requested_range']['to']}. Daymet omits December 31 in leap years.")
    context = {
        "@language": "en", "@vocab": "https://schema.org/", "sc": "https://schema.org/",
        "cr": "http://mlcommons.org/croissant/", "dct": "http://purl.org/dc/terms/",
        "rai": "http://mlcommons.org/croissant/RAI/",
        "data": {"@id": "cr:data", "@type": "@json"},
        "examples": {"@id": "cr:examples", "@type": "@json"},
        "conformsTo": "dct:conformsTo", "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
        **{k: "cr:" + k for k in ("citeAs", "column", "equivalentProperty", "extract", "field", "fileObject",
                                   "fileProperty", "fileSet", "format", "includes", "isLiveDataset",
                                   "jsonPath", "key", "md5", "parentField", "path", "recordSet",
                                   "references", "regex", "repeated", "replace", "samplingRate",
                                   "separator", "source", "subField", "transform")},
    }
    metadata = {
        "@context": context, "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "name": CASE, "description": description, "version": "1.0",
        "url": "https://pranava0x0.github.io/DOE-MCP/demos.html#" + CASE,
        "creator": {"@type": "Organization", "name": "DOE-MCP"},
        "datePublished": "2026-11-03", "license": DATA_USE,
        "citation": data["publisher_citation"],
        "citeAs": "DOE-MCP. The missing Daymet date: recorded Oak Ridge temperature extract. 2026.",
        "distribution": [{
            "@type": "cr:FileObject", "@id": "temperatures", "name": CSV_NAME,
            "contentUrl": CSV_NAME, "encodingFormat": "text/csv",
            "contentSize": str(len(payload)), "sha256": hashlib.sha256(payload).hexdigest(),
        }],
        "recordSet": [{
            "@type": "cr:RecordSet", "@id": "days", "name": "days", "description": description,
            "field": [{"@type": "cr:Field", "@id": "days/" + name, "name": name,
                       "description": desc, "dataType": "sc:" + dtype,
                       "source": {"fileObject": {"@id": "temperatures"}, "extract": {"column": name}}}
                      for name, dtype, desc in fields],
        }],
    }
    return payload, metadata
