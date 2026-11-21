"""Synthetic application inputs test fairness; these are not publisher fixtures."""
from __future__ import annotations

import csv
from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from doe_mcp.applications import forecast_panel as panel
from doe_mcp.applications.bundle import export_bundle, json_bytes
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.demo_worksheets import csv_text

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    _, files = panel.simulation_sample()
    return json.loads(files["plan.json"]), list(csv.DictReader(io.StringIO(files["actuals.csv"].decode()))), list(csv.DictReader(io.StringIO(files["forecasts.csv"].decode())))


def build(plan, actuals, forecasts):
    return panel.build(json_bytes(plan), csv_text(panel.ACTUAL_FIELDS, [[r[k] for k in panel.ACTUAL_FIELDS] for r in actuals]).encode(),
                       csv_text(panel.FORECAST_FIELDS, [[r[k] for k in panel.FORECAST_FIELDS] for r in forecasts]).encode())


def test_intersection_scores_every_model_and_baseline_on_identical_targets():
    report, _ = panel.simulation_sample()
    assert report["mode"] == "simulation" and report["status"] == "partial"
    assert report["coverage"] == {"included_region_lead_targets": 42, "expected_region_lead_targets": 48}
    for cohort in report["cohorts"]:
        assert len(cohort["included_targets"]) == (11 if cohort["lead_seconds"] == 300 else 10)
        for model in [m["id"] for m in report["plan"]["models"]]:
            selected = [r for r in report["rows"] if r["region_id"] == cohort["region_id"] and r["lead_seconds"] == cohort["lead_seconds"] and r["model_id"] == model and r["included"]]
            assert [r["target_at"] for r in selected] == cohort["included_targets"]
        metrics = [m for m in report["metrics"] if m["region_id"] == cohort["region_id"] and m["lead_seconds"] == cohort["lead_seconds"]]
        assert {m["count"] for m in metrics} == {len(cohort["included_targets"])}
        assert {m["kind"] for m in metrics} == {"model", "persistence"}
        other = next(c for c in report["cohorts"] if c["region_id"] != cohort["region_id"] and c["lead_seconds"] == cohort["lead_seconds"])
        assert cohort["included_targets"] == other["included_targets"]


def test_within_region_mode_retains_different_region_cohorts_explicitly():
    plan, actuals, forecasts = inputs()
    plan["cohort_mode"] = "within_region"
    report, _ = build(plan, actuals, forecasts)
    east = next(c for c in report["cohorts"] if c["region_id"] == "sim-east" and c["lead_seconds"] == 300)
    west = next(c for c in report["cohorts"] if c["region_id"] == "sim-west" and c["lead_seconds"] == 300)
    assert len(east["included_targets"]) == 12 and len(west["included_targets"]) == 11
    assert report["ranking"] == "not_performed" and "aggregate_metrics" not in report


@pytest.mark.parametrize("missing", ["region", "model", "availability"])
def test_empty_common_cohort_retains_all_declared_regions_models_and_null_metrics(missing):
    plan, actuals, forecasts = inputs()
    if missing == "region":
        actuals = [r for r in actuals if r["region_id"] == "sim-east"]
        forecasts = [r for r in forecasts if r["region_id"] == "sim-east"]
    elif missing == "model":
        forecasts = [r for r in forecasts if r["model_id"] == "sim-trend"]
    else:
        for row in actuals:
            row["available_at"] = "2026-01-02T00:00:00Z"
    report, files = build(plan, actuals, forecasts)
    assert report["status"] == "unscored" and report["ranking"] == "not_performed"
    assert len(report["cohorts"]) == 4 and len(report["metrics"]) == 12
    assert all(m["count"] == 0 and m["mae"] is m["rmse"] is m["bias"] is None for m in report["metrics"])
    assert {m["region_id"] for m in report["metrics"]} == {"sim-east", "sim-west"}
    assert len(report["rows"]) == 96 and all(not r["included"] and r["reasons"] for r in report["rows"])
    assert len(list(csv.DictReader(io.StringIO(files["included-targets.csv"].decode())))) == 0


def test_delayed_observation_and_future_values_never_set_persistence():
    plan, actuals, forecasts = inputs()
    report, _ = build(plan, actuals, forecasts)
    row = next(r for r in report["rows"] if r["region_id"] == "sim-west" and r["target_at"] == "2026-01-01T00:25:00+00:00" and r["lead_seconds"] == 300)
    assert row["included"] and row["persistence"] == 209
    assert row["baseline_target_at"] == "2026-01-01T00:15:00+00:00"
    for item in actuals:
        if item["region_id"] == "sim-west" and panel.timestamp(item["target_at"]) >= panel.timestamp("2026-01-01T00:20:00Z"):
            item["value"] = "9999"
    changed, _ = build(plan, actuals, forecasts)
    same = next(r for r in changed["rows"] if all(r[k] == row[k] for k in ["region_id", "model_id", "target_at", "lead_seconds"]))
    assert same["persistence"] == row["persistence"]
    for item in changed["rows"]:
        if item["baseline_target_at"]:
            assert panel.timestamp(item["baseline_target_at"]) <= panel.timestamp(item["issued_at"])
            assert panel.timestamp(item["baseline_available_at"]) <= panel.timestamp(item["issued_at"])


def test_missing_peer_forecast_excludes_other_models_and_regions_with_reason():
    report, _ = panel.simulation_sample()
    affected = [r for r in report["rows"] if r["lead_seconds"] == 600 and r["target_at"] == "2026-01-01T00:30:00+00:00"]
    assert len(affected) == 4 and all(not r["included"] and r["error"] is None for r in affected)
    assert all(r["blocking_regions"] == ["sim-east"] for r in affected)
    assert any("missing_forecast:sim-offset" in r["reasons"] for r in affected)
    assert any("outside_cross_region_intersection" in r["reasons"] for r in affected)


@pytest.mark.parametrize("field,value", [("unit", "MWh"), ("interval_semantics", "interval_mean"), ("interval_seconds", "3600"), ("quantity", "electric_energy")])
def test_measurement_mismatch_is_refused_without_conversion(field, value):
    plan, actuals, forecasts = inputs()
    actuals[0][field] = value
    with pytest.raises(InvalidQuery, match="semantics differ"):
        build(plan, actuals, forecasts)


@pytest.mark.parametrize("defect", ["undeclared_model", "undeclared_region", "undeclared_lead", "duplicate_actual", "duplicate_forecast", "availability_before_effective", "naive_time", "nan", "incompatible_plan"])
def test_invalid_imports_fail_explicitly(defect):
    plan, actuals, forecasts = inputs()
    if defect == "undeclared_model":
        forecasts[0]["model_id"] = "not-declared"
    elif defect == "undeclared_region":
        actuals[0]["region_id"] = "not-declared"
    elif defect == "undeclared_lead":
        forecasts[0]["issued_at"] = "2025-12-01T00:00:00Z"
    elif defect == "duplicate_actual":
        actuals.append({**actuals[0], "target_at": "2025-12-31T19:00:00-05:00"})
    elif defect == "duplicate_forecast":
        forecasts.append(dict(forecasts[0]))
    elif defect == "availability_before_effective":
        actuals[0]["available_at"] = "2025-12-31T23:59:00Z"
    elif defect == "naive_time":
        forecasts[0]["target_at"] = "2026-01-01T00:10:00"
    elif defect == "nan":
        forecasts[0]["prediction"] = "NaN"
    else:
        plan["measurement"]["unit"] = "MWh"
    with pytest.raises(InvalidQuery):
        build(plan, actuals, forecasts)


def test_outside_and_off_grid_input_rows_are_exported_as_exclusions():
    plan, actuals, forecasts = inputs()
    for shift in [timedelta(days=1), timedelta(seconds=1)]:
        forecasts.append({**forecasts[0], "target_at": panel.stamp(panel.timestamp(forecasts[0]["target_at"]) + shift),
                          "issued_at": panel.stamp(panel.timestamp(forecasts[0]["issued_at"]) + shift)})
    report, _ = build(plan, actuals, forecasts)
    assert [r["reason"] for r in report["input_exclusions"]] == ["outside_declared_window", "target_off_declared_grid"]
    assert report["coverage"]["included_region_lead_targets"] == 42


def test_import_provenance_raw_hashes_and_bundle_integrity(tmp_path):
    plan, actuals, forecasts = inputs()
    plan["data_kind"] = "user_supplied"
    report, files = build(plan, actuals, forecasts)
    assert report["mode"] == "user_import"
    assert report["provenance"]["verification"] == "not_publisher_verified"
    assert not report["steps"] and not report["fixtures"]
    export_bundle(tmp_path / "panel", report, files)
    for entry in report["inputs"]:
        assert hashlib.sha256(files[entry["file"]]).hexdigest() == entry["sha256"]
        assert (tmp_path / "panel" / entry["file"]).read_bytes() == files[entry["file"]]
    assert len(list(csv.DictReader(io.StringIO(files["row-exclusions.csv"].decode())))) == 12


def test_demo_is_repeatable_and_not_a_recorded_publisher_fixture():
    first, second = panel.simulation_sample(), panel.simulation_sample()
    assert first == second
    report, files = first
    assert report["mode"] == "simulation" and not report["fixtures"] and not report["steps"]
    assert all("Fictitious" in region["attribution"] for region in report["plan"]["regions"])
    reimported, _ = panel.build(files["plan.json"], files["actuals.csv"], files["forecasts.csv"])
    assert reimported["mode"] == "simulation"


def test_real_cli_simulation_and_reimport_preserve_mode_and_bytes(tmp_path):
    command = [sys.executable, "-m", "doe_mcp.cli", "app", panel.NAME]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    output = tmp_path / "simulation"
    first = subprocess.run(command + ["--output", str(output)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert first.returncode == 0 and "mode: simulation" in first.stderr
    args = ["--plan", str(output / "plan.json"), "--actuals", str(output / "actuals.csv"), "--forecasts", str(output / "forecasts.csv")]
    second = subprocess.run(command + args, cwd=ROOT, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["mode"] == "simulation"
    rejected = subprocess.run(command + ["--live"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert rejected.returncode != 0 and "does not fetch live publishers" in rejected.stderr


def test_unscored_cli_warns_without_dropping_region(tmp_path):
    _, files = panel.simulation_sample()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    (tmp_path / "actuals.csv").write_text(",".join(panel.ACTUAL_FIELDS) + "\n")
    result = subprocess.run([sys.executable, "-m", "doe_mcp.cli", "app", panel.NAME,
                             "--plan", str(tmp_path / "plan.json"), "--actuals", str(tmp_path / "actuals.csv"),
                             "--forecasts", str(tmp_path / "forecasts.csv")], cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert result.returncode == 0 and "no common eligible targets" in result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "unscored" and len(report["cohorts"]) == 4
