"""Select released GCAM input cells with receipts and no cross-cell arithmetic."""
from __future__ import annotations

import copy
import hashlib

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..demo_worksheets import csv_text
from ..domains._common import require_active_source
from ..gcam_catalog import TABLES
from .bundle import json_bytes

NAME = "gcam-comparison"
MAX_SELECTIONS = 12
REGION_KEYS = {"region_GCAM3", "GCAM_region_ID"}
PRESET = {
    "selections": [
        {"id": "japan-geothermal", "tool": "power.geothermal_curves", "country_iso": "jpn",
         "filters": {"resource": "geothermal", "subresource": "hydrothermal", "grade": "grade 2"}, "columns": ["available"]},
        {"id": "indonesia-geothermal", "tool": "power.geothermal_curves", "country_iso": "idn",
         "filters": {"resource": "geothermal", "subresource": "hydrothermal", "grade": "grade 2"}, "columns": ["available"]},
        {"id": "kenya-soil", "tool": "land.soil_timescales", "country_iso": "ken", "filters": {}, "columns": ["soilTimeScale"]},
        {"id": "canada-soil", "tool": "land.soil_timescales", "country_iso": "can", "filters": {}, "columns": ["soilTimeScale"]},
        {"id": "desalination-energy", "tool": "water.desalination_inputs",
         "filters": {"technology": "reverse osmosis", "minicam.energy.input": "elect_td_ind"}, "columns": ["2100"]},
        {"id": "desalination-cost", "tool": "water.desalination_costs",
         "filters": {"technology": "reverse osmosis"}, "columns": ["2100"]},
    ],
    "pairs": [
        {"left": "japan-geothermal", "left_column": "available", "right": "indonesia-geothermal", "right_column": "available"},
        {"left": "kenya-soil", "left_column": "soilTimeScale", "right": "canada-soil", "right_column": "soilTimeScale"},
        {"left": "desalination-energy", "left_column": "2100", "right": "desalination-cost", "right_column": "2100"},
    ],
}


def validate_plan(plan: dict) -> dict:
    """Validate the whole request before any source read or output mutation."""
    if not isinstance(plan, dict) or set(plan) != {"selections", "pairs"}:
        raise InvalidQuery("GCAM plan must contain selections and pairs lists")
    selections, pairs = plan["selections"], plan["pairs"]
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_SELECTIONS:
        raise InvalidQuery("GCAM plan needs 1 through 12 exact row selections")
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 12:
        raise InvalidQuery("GCAM plan needs 1 through 12 side-by-side pairs")
    ids = {}
    for selection in selections:
        required = {"id", "tool", "filters", "columns"}
        if not isinstance(selection, dict) or not required <= set(selection) or set(selection) - required - {"country_iso"}:
            raise InvalidQuery("each GCAM selection needs id, tool, filters and columns; country_iso is optional")
        name, tool, filters, columns = (selection[k] for k in ["id", "tool", "filters", "columns"])
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in ids:
            raise InvalidQuery("GCAM selection IDs must be nonempty, unique and at most 80 characters")
        if not isinstance(tool, str) or tool.count(".") != 1:
            raise InvalidQuery("GCAM selections must name an existing GCAM table tool")
        domain, table = tool.split(".")
        if domain not in TABLES or table not in TABLES[domain]:
            raise InvalidQuery("GCAM selections must name an existing GCAM table tool")
        if not isinstance(filters, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in filters.items()):
            raise InvalidQuery("GCAM filters must map exact publisher column names to text values")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 8 or not all(isinstance(c, str) and c for c in columns) or len(set(columns)) != len(columns):
            raise InvalidQuery("GCAM selections need 1 through 8 unique column names")
        iso = selection.get("country_iso")
        if "country_iso" in selection and (not isinstance(iso, str) or len(iso) != 3 or not iso.isascii() or not iso.islower() or not iso.isalpha()):
            raise InvalidQuery("country_iso must be the publisher's lowercase three-letter country code")
        ids[name] = selection
    for pair in pairs:
        keys = {"left", "left_column", "right", "right_column"}
        if not isinstance(pair, dict) or set(pair) != keys or not all(isinstance(v, str) for v in pair.values()):
            raise InvalidQuery("each pair needs left, left_column, right and right_column")
        if any(pair[side] not in ids or pair[side + "_column"] not in ids[pair[side]]["columns"] for side in ["left", "right"]):
            raise InvalidQuery("pair endpoints must name selected IDs and selected columns")
    return copy.deepcopy(plan)


def _receipt(env, *, step_number: int, columns: list[str]) -> dict:
    data = env.data
    if env.coverage.pagination.value != "complete":
        raise InvalidQuery("GCAM row selection requires complete pagination; no comparison was produced")
    if data["total_matches"] != 1 or len(data["records"]) != 1:
        raise InvalidQuery("GCAM selector must match exactly one row; refine filters or correct a zero-match selector")
    if not set(columns) <= set(data["columns"]):
        raise InvalidQuery("selected GCAM columns must be exact returned publisher column names")
    record = data["records"][0]
    evidence = next((e for e in env.evidence if e.id == record["evidence_ref"]), None)
    if evidence is None or evidence.locator != data["file_url"]:
        raise SourceSchemaChanged("GCAM row receipt lacks evidence for its returned file")
    units = [note for note in data["source_notes"] if note.lower().startswith("units:")]
    unit_status = "stated" if units and all(note.split(":", 1)[1].strip().lower() not in {"", "na", "n/a", "unknown"} for note in units) else "unspecified"
    return {"step_number": step_number, "tool": env.execution.tool, "table": data["table"],
            "release": data["release"], "file_url": data["file_url"], "source_notes": data["source_notes"],
            "publisher_columns": data["columns"], "row_number": record["row_number"],
            "row_values": record["values"], "selected_values": {c: record["values"][c] for c in columns},
            "evidence_ref": record["evidence_ref"], "source_ref": evidence.source_ref,
            "units": {"status": unit_status, "publisher_statements": units,
                      "scope": "Original table notes; a statement may describe several columns. No unit conversion was performed."}}


def compare(pair: dict, receipts: dict[str, dict]) -> dict:
    left, right = (receipts[pair[side]] for side in ["left", "right"])
    checks = {"same_tool": left["tool"] == right["tool"],
              "same_column": pair["left_column"] == pair["right_column"],
              "same_release": left["release"] == right["release"],
              "same_units_statements": left["units"]["publisher_statements"] == right["units"]["publisher_statements"],
              "units_stated": left["units"]["status"] == right["units"]["status"] == "stated"}
    values = {side: receipts[pair[side]]["selected_values"][pair[side + "_column"]] for side in ["left", "right"]}
    if any(value is None for value in values.values()):
        relationship = "missing_value"
    elif not checks["same_tool"] or not checks["same_column"]:
        relationship = "different_table_or_field"
    elif not checks["same_release"]:
        relationship = "different_release"
    elif not checks["units_stated"]:
        relationship = "units_unspecified"
    elif not checks["same_units_statements"]:
        relationship = "different_units_statements"
    else:
        relationship = "same_table_field"
    return {**pair, "values": values, "relationship": relationship, "checks": checks,
            "arithmetic": "not_performed",
            "interpretation": "Side-by-side released model inputs. Matching table fields does not establish country observations, interchangeable regions or a common scientific meaning across row qualifiers."}


async def build(run, *, plan: dict | None = None) -> dict[str, bytes]:
    request = validate_plan(PRESET if plan is None else plan)
    # Metadata checks go through the source gate too. Tools perform their own
    # gate again on every fetch, including country-to-region lookups.
    specifications = {}
    for selection in request["selections"]:
        domain, table = selection["tool"].split(".")
        manifest = require_active_source(run.ctx, "gcam-" + domain)
        spec = run.ctx.gcam.params_for(manifest).tables[table]
        if not set(selection["filters"]) <= set(spec.columns) or not set(selection["columns"]) <= set(spec.columns):
            raise InvalidQuery("GCAM filter and selected columns must occur in the table's publisher schema")
        keys = REGION_KEYS & set(spec.columns)
        if selection.get("country_iso") and len(keys) != 1:
            raise InvalidQuery("country selection needs a table with exactly one explicit GCAM region convention")
        specifications[selection["id"]] = keys
    receipts = {}
    for selection in request["selections"]:
        filters = dict(selection["filters"])
        mapping = None
        if selection.get("country_iso"):
            env = await run.call("economy.country_regions", filters={"iso": selection["country_iso"]}, rows=2)
            mapping_receipt = _receipt(env, step_number=len(run.report["steps"]), columns=env.data["columns"])
            region_key, = specifications[selection["id"]]
            region_value = mapping_receipt["row_values"][region_key]
            if region_value is None:
                raise InvalidQuery("country has no mapping under this table's region convention")
            if region_key in filters and filters[region_key] != region_value:
                raise InvalidQuery("explicit region filter conflicts with the publisher's country mapping")
            filters[region_key] = region_value
            mapping = {"requested_iso": selection["country_iso"], "region_key": region_key,
                       "join_value": region_value, "receipt": mapping_receipt}
        env = await run.call(selection["tool"], filters=filters, rows=2)
        receipt = _receipt(env, step_number=len(run.report["steps"]), columns=selection["columns"])
        receipts[selection["id"]] = {"id": selection["id"], "selector": selection,
                                     "resolved_filters": filters, "region_mapping": mapping, **receipt}
    pairs = [compare(pair, receipts) for pair in request["pairs"]]
    run.report.update(status="receipts_ready", plan=request, receipts=list(receipts.values()), comparisons=pairs,
                      plan_sha256=hashlib.sha256(json_bytes(request)).hexdigest(), arithmetic="not_performed")
    run.report["limitations"] += ["These are released GCAM configuration inputs, not observations, current prices, forecasts or scenario outputs.",
        "Every selector must match exactly one row. Original decimal strings, categories and null cells are preserved.",
        "Country mapping uses the region convention present in the selected table. A country's membership does not make a regional input a country measurement.",
        "Unit statements are retained verbatim from publisher table notes. NA or absent statements remain unspecified; source descriptions may give further context.",
        "Pairs are side-by-side receipts only. No addition, subtraction, ranking, conversion or aggregation is performed, including for matching table fields.",
        "Different tables can use the same year column for unrelated concepts. Read full row qualifiers, release, source notes and region mappings before interpreting a pair."]
    fields = ["selection_id", "tool", "column", "value", "release", "file_url", "row_number", "evidence_ref", "step_number", "region_key", "region_value", "units_status", "units_statements", "resolved_filters"]
    rows = []
    for receipt in receipts.values():
        mapping = receipt["region_mapping"] or {}
        for column, value in receipt["selected_values"].items():
            rows.append([receipt["id"], receipt["tool"], column, value, receipt["release"], receipt["file_url"],
                         receipt["row_number"], receipt["evidence_ref"], receipt["step_number"], mapping.get("region_key"), mapping.get("join_value"),
                         receipt["units"]["status"], json_bytes(receipt["units"]["publisher_statements"]).decode().strip(),
                         json_bytes(receipt["resolved_filters"]).decode().strip()])
    return {"plan.json": json_bytes(request), "receipts.json": json_bytes(run.report["receipts"]),
            "comparisons.json": json_bytes(pairs), "receipts.csv": csv_text(fields, rows).encode()}
