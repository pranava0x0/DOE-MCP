"""Tables derived from one publisher page. Each row retains its evidence ID."""
from __future__ import annotations

import csv
import io

from .core.assemble import strip_inline_markup
from .core.envelope import Envelope
from .core.errors import SourceUnavailable

GENESIS_SKILLS = {
    "bibliography": ("literature-search", "skills/literature-search"),
    "soil-metadata": ("data-exploration", "skills/baseeval-skills/lm-eval-harness-skills/data-exploration"),
}
GENESIS_TREE = "https://github.com/AI-ModCon/genesis-skills/blob/70dce5aca946e2804f9b8579da01910cd2861858/"


def _evidence_ids(env: Envelope, source_id: str) -> dict[str, str]:
    refs = {s.id for s in env.provenance if s.source_id == source_id}
    return {e.record_id: e.id for e in env.evidence if e.source_ref in refs}


def _ref(index, record_id):
    if record_id not in index:
        raise SourceUnavailable("worksheet record has no matching publisher evidence")
    return index[record_id]


def bibliography(env: Envelope) -> dict:
    evidence = _evidence_ids(env, "osti-doe-pages")
    rows = []
    for record in env.data["records"]:
        rid = record["id"]
        rows.append([rid, strip_inline_markup(record.get("title") or "") or None,
                     "; ".join(record.get("authors") or []) or None,
                     record.get("authors_truncated", 0), record.get("publication_date"),
                     record.get("journal"), record.get("doi"), _ref(evidence, rid)])
    if not rows:
        raise SourceUnavailable("no DOE PAGES records for the bibliography")
    return {"caption": "Bibliography worksheet", "columns": [
        "Record ID", "Title", "Listed authors", "Authors omitted by DOE-MCP",
        "Publication date", "Journal", "DOI", "Step 1 evidence ID"], "rows": rows}


def soil_metadata(env: Envelope) -> dict:
    evidence = _evidence_ids(env, "nmdc-runtime")
    rows = []
    missing = {"collection date": 0, "coordinate pair": 0, "depth in metres": 0}
    for sample in env.data["biosamples"]:
        sid = sample["sample_id"]
        lat, lon = sample.get("latitude"), sample.get("longitude")
        depth = sample.get("depth_m") or {}
        missing["collection date"] += not sample.get("collected")
        missing["coordinate pair"] += lat is None or lon is None
        missing["depth in metres"] += not depth
        rows.append([sid, sample.get("place"), sample.get("collected"), lat, lon,
                     depth.get("value"), depth.get("min"), depth.get("max"), _ref(evidence, sid)])
    if not rows:
        raise SourceUnavailable("no NMDC samples for the field check")
    return {"caption": "Soil sample field check", "columns": [
        "Sample ID", "Place", "Collected", "Latitude", "Longitude", "Depth (m)",
        "Minimum depth (m)", "Maximum depth (m)", "Step 1 evidence ID"], "rows": rows,
        "missing": missing}


def csv_text(headers, rows) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                         else v for v in row])
    return stream.getvalue()
