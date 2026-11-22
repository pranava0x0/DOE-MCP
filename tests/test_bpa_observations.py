"""Recorded BPA imports preserve provenance; mutations exercise rejection paths."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from doe_mcp.applications import bpa_observations as bpa, forecast_panel as panel
from doe_mcp.applications.bundle import export_bundle, json_bytes
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged
from doe_mcp.domains.energy import get_bpa_operations

ROOT = Path(__file__).resolve().parents[1]
ZONE = "America/Los_Angeles"


async def envelope(ctx):
    return (await get_bpa_operations(ctx, intervals=24)).model_dump(mode="json", by_alias=True)


async def test_retains_exact_input_row_evidence_and_unknown_availability(ctx, tmp_path):
    raw = await envelope(ctx)
    # Whitespace is part of the input checksum; the importer must not reserialize it.
    payload = (" \n" + json.dumps(raw, separators=(",", ":")) + "\n").encode()
    report, files = bpa.prepare([payload], timezone_name=ZONE)
    assert files["bpa-envelope-01.json"] == payload
    assert report["inputs"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["availability"] == dict(known_caller_declared=0, unknown=24, issuance_time_baseline_candidates=0)
    assert report["forecast_rows"] == 0 and "forecasts.csv" not in files
    rows = list(csv.DictReader(io.StringIO(files["actuals.csv"].decode())))
    assert all(r["available_at"] == "" for r in rows)
    assert all({k: r[k] for k in panel.MEASUREMENT} == {k: str(v) for k, v in bpa.MEASUREMENT.items()} for r in rows)
    receipt = json.loads(files["observation-receipts.json"])[0]
    assert receipt["raw_timestamp"] == raw["data"]["intervals"][0]["timestamp"]
    assert receipt["target_at"] == "2026-09-03T07:00:00+00:00" and receipt["timezone_assumption"] == ZONE
    assert receipt["record_id"] == raw["evidence"][0]["record_id"]
    assert receipt["row_sha256"] == bpa.digest(json_bytes(raw["data"]["intervals"][0]))
    export_bundle(tmp_path / "prepared", report, files)
    for entry in json.loads((tmp_path / "prepared/manifest.json").read_text())["files"]:
        assert bpa.digest((tmp_path / "prepared" / entry["file"]).read_bytes()) == entry["sha256"]
    with pytest.raises(ValueError, match="already exists"):
        export_bundle(tmp_path / "prepared", report, files)


@pytest.mark.parametrize("defect", ["tool", "contract", "envelope_version", "coverage", "source", "source_ref", "record", "duplicate_evidence", "duplicate_source", "count", "unit", "columns", "missing_evidence", "duplicate_row", "off_grid"])
async def test_inconsistent_envelope_binding_refused(ctx, defect):
    raw = await envelope(ctx)
    if defect == "tool":
        raw["_execution"]["tool"] = "energy.grid_status"
    elif defect == "contract":
        raw["_execution"]["tool_contract_version"] = "2"
    elif defect == "envelope_version":
        raw["_execution"]["envelope_version"] = "future"
    elif defect == "coverage":
        raw["coverage"]["sources_searched"] = ["other-source"]
    elif defect == "source":
        raw["provenance"][0]["source_id"] = "other-source"
    elif defect == "source_ref":
        raw["evidence"][0]["source_ref"] = "absent"
    elif defect == "record":
        raw["evidence"][0]["record_id"] = "other-row"
    elif defect == "duplicate_evidence":
        raw["evidence"][1]["id"] = raw["evidence"][0]["id"]
    elif defect == "duplicate_source":
        raw["provenance"].append(copy.deepcopy(raw["provenance"][0]))
    elif defect == "count":
        raw["data"]["record_count"] += 1
    elif defect == "unit":
        raw["data"]["units"] = "MWh"
    elif defect == "columns":
        raw["data"]["intervals"][0]["values"].pop("Hydro")
    elif defect == "missing_evidence":
        raw["evidence"].pop()
    elif defect == "duplicate_row":
        raw["data"]["intervals"][1] = copy.deepcopy(raw["data"]["intervals"][0])
    else:
        raw["data"]["intervals"][0]["timestamp"] = "09/03/2026 00:01"
        raw["evidence"][0].update(effective_at="09/03/2026 00:01", record_id=raw["data"]["feed"] + "#09/03/2026 00:01")
    with pytest.raises((InvalidQuery, SourceSchemaChanged)):
        bpa.prepare([json_bytes(raw)], timezone_name=ZONE)


@pytest.mark.parametrize("stamp", ["11/01/2026 01:30", "03/08/2026 02:30"])
async def test_dst_ambiguous_or_nonexistent_local_time_is_refused(ctx, stamp):
    raw = await envelope(ctx)
    raw["data"]["intervals"][0]["timestamp"] = stamp
    raw["evidence"][0].update(effective_at=stamp, record_id=raw["data"]["feed"] + "#" + stamp)
    with pytest.raises(SourceSchemaChanged, match="ambiguous or nonexistent"):
        bpa.prepare([json_bytes(raw)], timezone_name=ZONE)


async def test_duplicate_envelopes_timezone_and_json_limits(ctx, monkeypatch):
    payload = json_bytes(await envelope(ctx))
    with pytest.raises(InvalidQuery, match="duplicate BPA UTC"):
        bpa.prepare([payload, payload], timezone_name=ZONE)
    for zone in [None, "Not/A_Zone"]:
        with pytest.raises(InvalidQuery, match="IANA"):
            bpa.prepare([payload], timezone_name=zone)
    with pytest.raises(InvalidQuery, match="duplicate keys"):
        bpa.prepare([b'{"data": {}, "data": {}}'], timezone_name=ZONE)
    with pytest.raises(InvalidQuery, match="1 through 8"):
        bpa.prepare([payload] * 9, timezone_name=ZONE)
    monkeypatch.setattr(panel, "MAX_BYTES", 10)
    with pytest.raises(InvalidQuery, match="20 MB"):
        bpa.prepare([payload], timezone_name=ZONE)


async def test_availability_requires_preserved_row_binding_and_honest_status(ctx):
    payload = json_bytes(await envelope(ctx))
    report, files = bpa.prepare([payload], timezone_name=ZONE)
    receipt = json.loads(files["observation-receipts.json"])[0]
    row = {k: receipt[k] for k in ["envelope_sha256", "evidence_id", "target_at"]}
    row.update(available_at="2026-09-03T07:01:00Z", evidence="Caller log line 17: delivered to forecast system")
    sidecar = json_bytes({"rows": [row]})
    declared, exported = bpa.prepare([payload], timezone_name=ZONE, availability_payload=sidecar)
    assert exported["availability-evidence.json"] == sidecar
    assert declared["availability"] == dict(known_caller_declared=1, unknown=23, issuance_time_baseline_candidates=1)
    asserted = json.loads(exported["observation-receipts.json"])[0]
    assert asserted["availability_status"] == "caller_declared_unverified"
    assert "not_publisher_authenticated" == declared["provenance"]["verification"]
    for field, value in [("envelope_sha256", "bad"), ("evidence_id", "absent"), ("target_at", "2026-09-04T07:00:00Z"),
                         ("available_at", "2026-09-03T06:59:00Z"), ("evidence", "")]:
        with pytest.raises(InvalidQuery):
            bpa.prepare([payload], timezone_name=ZONE, availability_payload=json_bytes({"rows": [{**row, field: value}]}))
    with pytest.raises(InvalidQuery, match="uniquely"):
        bpa.prepare([payload], timezone_name=ZONE, availability_payload=json_bytes({"rows": [row, row]}))


def test_unknown_availability_scores_only_separate_ex_post_cohort():
    _, files = panel.simulation_sample()
    rows = list(csv.DictReader(io.StringIO(files["actuals.csv"].decode())))
    for row in rows:
        row["available_at"] = ""
    payload = panel._table(rows, panel.ACTUAL_FIELDS)
    report, files = panel.build(files["plan.json"], payload, files["forecasts.csv"])
    assert report["status"] == "unscored" and all(m["count"] == 0 for m in report["metrics"])
    assert all(r["persistence"] is None and r["baseline_available_at"] is None for r in report["rows"])
    ex_post = report["ex_post_model_only"]
    assert all(m["count"] > 0 for m in ex_post["metrics"])
    assert all(r["actual_available_at"] is None for r in ex_post["rows"])
    assert "Different cohort" in ex_post["comparison_note"]
    assert "ex-post-model-only.json" in files


def test_real_cli_prepares_recorded_and_raw_input_without_network(tmp_path):
    command = [sys.executable, "-m", "doe_mcp.cli", "app", panel.NAME]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(command + ["--prepare-bpa", "--output", str(tmp_path / "recorded")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "unknown availability: 24" in result.stderr
    original = tmp_path / "recorded/bpa-envelope-01.json"
    assert json.loads(original.read_text())["_execution"]["request_id"] != "recorded-workflow"
    result = subprocess.run(command + ["--bpa-envelope", str(original), "--actual-timezone", ZONE, "--output", str(tmp_path / "imported")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert original.read_bytes() == (tmp_path / "imported/bpa-envelope-01.json").read_bytes()
    assert json.loads((tmp_path / "imported/report.json").read_text())["mode"] == "observation_import"
    for flags in [["--prepare-bpa", "--live"], ["--bpa-envelope", str(original)], ["--prepare-bpa", "--actual-timezone", ZONE]]:
        result = subprocess.run(command + flags, cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode != 0


async def test_gaps_and_null_load_are_preserved_without_fill_or_aggregation(ctx):
    raw = await envelope(ctx)
    # Keep separated genuine rows, with one missing Load reading.
    raw["data"]["intervals"] = [raw["data"]["intervals"][0], raw["data"]["intervals"][2]]
    raw["evidence"] = [raw["evidence"][0], raw["evidence"][2]]
    raw["data"]["record_count"] = 2
    raw["data"]["intervals"][1]["values"]["Load"] = None
    report, files = bpa.prepare([json_bytes(raw)], timezone_name=ZONE)
    rows = list(csv.DictReader(io.StringIO(files["actuals.csv"].decode())))
    assert report["observation_rows"] == len(rows) == 2
    assert rows[0]["target_at"] == "2026-09-03T07:00:00+00:00"
    assert rows[1]["target_at"] == "2026-09-03T07:10:00+00:00"
    assert rows[1]["value"] == "" and rows[1]["available_at"] == ""
    assert report["source_envelopes"][0]["coverage"] == raw["coverage"]


async def test_envelope_hash_cost_is_per_envelope_not_per_row(ctx, monkeypatch):
    payload = json_bytes(await envelope(ctx))
    original = bpa.digest
    envelope_hash_calls = []
    def tracked(body):
        if body is payload:
            envelope_hash_calls.append(1)
        return original(body)
    monkeypatch.setattr(bpa, "digest", tracked)
    report, files = bpa.prepare([payload], timezone_name=ZONE)
    # One envelope receipt and one input manifest entry; never once per row.
    assert len(envelope_hash_calls) == 2
    assert report["observation_rows"] == 24
    with pytest.raises(InvalidQuery, match="UTF-8"):
        bpa.prepare([payload.decode().encode("utf-16")], timezone_name=ZONE)
