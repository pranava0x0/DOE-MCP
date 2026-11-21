"""Recorded GCAM applications retain row and field identity through export."""
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

from doe_mcp.applications import gcam
from doe_mcp.applications.bundle import Run, export_bundle, json_bytes
from doe_mcp.applications.samples import recorded_sample
from doe_mcp.core.envelope import PaginationCoverage
from doe_mcp.core.errors import InvalidQuery, SourceNotActivated
from doe_mcp.core.registry import DeclaredState
from doe_mcp.replay import load_replay_context
from doe_mcp.workflows import freeze, tool_spec

ROOT = Path(__file__).resolve().parents[1]


async def test_preset_preserves_original_cells_file_notes_and_receipt_evidence(ctx, tmp_path):
    run = Run(ctx, gcam.NAME, "recorded")
    files = await gcam.build(run)
    report = run.report
    assert report["status"] == "receipts_ready" and report["arithmetic"] == "not_performed"
    assert report["plan_sha256"] == hashlib.sha256(files["plan.json"]).hexdigest()
    receipts = {r["id"]: r for r in report["receipts"]}
    assert receipts["japan-geothermal"]["selected_values"] == {"available": "0.56582"}
    assert receipts["indonesia-geothermal"]["selected_values"] == {"available": "5.65548"}
    assert receipts["desalination-energy"]["selected_values"] == {"2100": "0.0063"}
    assert receipts["desalination-cost"]["selected_values"] == {"2100": "0.38"}
    for receipt in receipts.values():
        step = report["steps"][receipt["step_number"] - 1]
        data = step["envelope"]["data"]
        row, = data["records"]
        assert receipt["row_values"] == row["values"]
        assert receipt["publisher_columns"] == data["columns"]
        assert receipt["source_notes"] == data["source_notes"]
        evidence, = step["envelope"]["evidence"]
        assert receipt["evidence_ref"] == evidence["id"]
        assert receipt["source_ref"] == evidence["source_ref"]
        assert receipt["file_url"] == evidence["locator"]
        assert step["envelope"]["_execution"]["request_id"] != "recorded-workflow"
    output = tmp_path / "receipts"
    export_bundle(output, report, files)
    rows = list(csv.DictReader(io.StringIO(files["receipts.csv"].decode())))
    assert len(rows) == 6 and rows[0]["value"] == "0.56582"
    assert json.loads((output / "receipts.json").read_bytes()) == report["receipts"]


async def test_country_selection_follows_each_tables_returned_region_convention(ctx):
    run = Run(ctx, gcam.NAME, "recorded")
    await gcam.build(run)
    receipts = {r["id"]: r for r in run.report["receipts"]}
    for name, key, value in [("indonesia-geothermal", "region_GCAM3", "Southeast Asia"),
                             ("kenya-soil", "GCAM_region_ID", "2"), ("canada-soil", "GCAM_region_ID", "8")]:
        receipt = receipts[name]
        mapping = receipt["region_mapping"]
        assert mapping["region_key"] == key and mapping["join_value"] == value
        assert receipt["resolved_filters"][key] == mapping["receipt"]["row_values"][key]
        assert mapping["receipt"]["evidence_ref"]
        assert {"region_GCAM3", "GCAM_region_ID"} <= set(mapping["receipt"]["row_values"])


async def test_same_year_different_concepts_and_unknown_units_never_produce_arithmetic(ctx):
    run = Run(ctx, gcam.NAME, "recorded")
    await gcam.build(run)
    geothermal, soil, desalination = run.report["comparisons"]
    assert geothermal["relationship"] == "same_table_field"
    assert soil["relationship"] == "units_unspecified"
    assert desalination["relationship"] == "different_table_or_field"
    assert desalination["checks"]["same_column"] and not desalination["checks"]["same_units_statements"]
    assert all(pair["arithmetic"] == "not_performed" for pair in run.report["comparisons"])
    assert not any(set(pair) & {"sum", "difference", "ratio", "rank", "converted_value"} for pair in run.report["comparisons"])


async def test_publisher_null_preserved_in_receipts_and_pair_status(ctx):
    selection = {"id": "cement", "tool": "emissions.pollutant_floors", "filters": {"supplysector": "cement"}, "columns": ["SO2"]}
    plan = {"selections": [selection], "pairs": [{"left": "cement", "left_column": "SO2", "right": "cement", "right_column": "SO2"}]}
    run = Run(ctx, gcam.NAME, "recorded")
    await gcam.build(run, plan=plan)
    assert run.report["receipts"][0]["selected_values"]["SO2"] is None
    assert run.report["comparisons"][0]["relationship"] == "missing_value"
    assert run.report["comparisons"][0]["values"] == {"left": None, "right": None}


@pytest.mark.parametrize("defect", ["duplicate_id", "non_gcam_tool", "unknown_column", "wrong_filter_type", "bad_pair", "too_many", "global_country"])
async def test_invalid_plan_fails_before_source_reads(ctx, replay, defect):
    plan = copy.deepcopy(gcam.PRESET)
    if defect == "duplicate_id":
        plan["selections"][1]["id"] = plan["selections"][0]["id"]
    elif defect == "non_gcam_tool":
        plan["selections"][0]["tool"] = "registry.search_sources"
    elif defect == "unknown_column":
        plan["selections"][0]["filters"]["invented"] = "value"
    elif defect == "wrong_filter_type":
        plan["selections"][0]["filters"]["grade"] = 2
    elif defect == "bad_pair":
        plan["pairs"][0]["left_column"] = "not-selected"
    elif defect == "too_many":
        plan["selections"] *= 3
    else:
        plan["selections"][4]["country_iso"] = "jpn"
    with pytest.raises(InvalidQuery):
        await gcam.build(Run(ctx, gcam.NAME, "recorded"), plan=plan)
    assert not replay.calls


@pytest.mark.parametrize("filters", [{}, {"grade": "not-a-grade"}])
async def test_ambiguous_and_empty_selection_refused_without_partial_export(ctx, tmp_path, filters):
    plan = copy.deepcopy(gcam.PRESET)
    plan["selections"][0]["filters"] = filters
    output = tmp_path / "not-produced"
    with pytest.raises(InvalidQuery, match="exactly one"):
        files = await gcam.build(Run(ctx, gcam.NAME, "recorded"), plan=plan)
        export_bundle(output, {}, files)
    assert not output.exists()


async def test_conflicting_country_region_and_incomplete_page_refused(ctx):
    plan = copy.deepcopy(gcam.PRESET)
    plan["selections"][0]["filters"]["region_GCAM3"] = "Canada"
    with pytest.raises(InvalidQuery, match="conflicts"):
        await gcam.build(Run(ctx, gcam.NAME, "recorded"), plan=plan)
    env = await tool_spec("power.wind_capacity_factors").fn(ctx, filters={"wind_class": "c7"}, rows=2)
    env.coverage.pagination = PaginationCoverage.unknown
    with pytest.raises(InvalidQuery, match="complete pagination"):
        gcam._receipt(env, step_number=1, columns=["CF"])


@pytest.mark.parametrize("source", ["gcam-power", "gcam-land", "gcam-water", "gcam-economy"])
async def test_comparison_routes_honor_source_activation(ctx, source):
    manifest = ctx.sources.get(source)
    manifest.lifecycle.declared_state = DeclaredState.proposed
    manifest.lifecycle.blocked_reason = "test source gate"
    with pytest.raises(SourceNotActivated):
        await gcam.build(Run(ctx, gcam.NAME, "recorded"))


async def test_recorded_preset_science_and_site_normalization_are_repeatable():
    outputs = []
    for _ in range(2):
        ctx = load_replay_context(ROOT / "tests/fixtures")
        report, files = await recorded_sample(ctx, gcam.NAME)
        outputs.append((json_bytes(freeze(report, "recorded-reference")), files))
    assert outputs[0] == outputs[1]


def test_real_cli_preset_and_reused_downloadable_plan(tmp_path):
    output = tmp_path / "sample"
    command = [sys.executable, "-m", "doe_mcp.cli", "app", gcam.NAME]
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for args in [["--output", str(output)], ["--plan", str(output / "plan.json")]]:
        result = subprocess.run(command + args, cwd=ROOT, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "receipts_ready" and len(report["receipts"]) == 6


def test_explicit_null_cli_plan_cannot_silently_run_preset(tmp_path):
    plan = tmp_path / "null.json"
    plan.write_text("null")
    output = tmp_path / "not-produced"
    result = subprocess.run([sys.executable, "-m", "doe_mcp.cli", "app", gcam.NAME,
                             "--plan", str(plan), "--output", str(output)], cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "must contain selections and pairs" in result.stderr
    assert not output.exists()
