"""Applications replay real publisher bytes; edge cases mutate returned data."""
from __future__ import annotations

import copy
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

from doe_mcp.applications import dockets, forecast, weather
from doe_mcp.applications.bundle import Run, export_bundle
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged
from doe_mcp.domains.earth import get_daymet_point
from doe_mcp.domains.energy import get_bpa_operations

ROOT = Path(__file__).resolve().parents[1]
WEATHER = dict(latitude=35.9313, longitude=-84.3104, start="2024-12-26", end="2024-12-31", variables="tmax")
DOCKET = dict(query="", docket="", since="", until="", document_type="NOTICE", rows=5, page_budget=2)


async def test_forecast_sample_metrics_and_original_envelope(ctx):
    run = Run(ctx, "forecast-benchmark", "recorded")
    files = await forecast.build(run, payload=None, timezone_name=None, intervals=24)
    assert run.report["retrospective_demo"]
    assert run.report["status"] == "scored"
    assert run.report["scoring"]["matched"] == run.report["scoring"]["submitted"] == 46
    assert len(run.report["metrics"]) == 2
    assert all(m["matched"] == 23 and m["persistence"]["matched"] == 23 for m in run.report["metrics"])
    assert run.report["input_sha256"] == hashlib.sha256(files["forecasts.csv"]).hexdigest()
    assert run.report["steps"][0]["envelope"]["_execution"]["request_id"] != "recorded-workflow"
    assert all(r["actual_evidence_id"] and r["persistence_evidence_id"] for r in run.report["rows"])


@pytest.mark.parametrize("replacement", [
    "m,2026-09-03T00:00:00,2026-09-03T00:05:00Z,1",
    "m,2026-09-03T00:05:00Z,2026-09-03T00:05:00Z,1",
    "m,2026-09-03T00:00:00Z,2026-09-03T00:05:00Z,nan",
    "m,2026-09-03T00:00:00Z,2026-09-03T00:05:00Z,inf",
])
def test_forecast_rejects_invalid_inputs(replacement):
    with pytest.raises(InvalidQuery):
        forecast.parse_forecasts((",".join(forecast.FIELDS) + "\n" + replacement).encode())


async def test_forecast_duplicate_identity_and_offset_equivalence(ctx):
    obs = forecast.actuals(await get_bpa_operations(ctx, intervals=24), "America/Los_Angeles")
    payload = forecast.sample_forecasts(obs)
    line = payload.decode().splitlines()[1]
    with pytest.raises(InvalidQuery, match="duplicate"):
        forecast.parse_forecasts(payload + (line + "\n").encode())


async def test_persistence_does_not_use_future_and_excludes_gaps(ctx):
    obs = forecast.actuals(await get_bpa_operations(ctx, intervals=24), "America/Los_Angeles")
    forecasts = forecast.parse_forecasts(forecast.sample_forecasts(obs))
    row = forecasts[0]
    row["issued"] = obs[0]["clock"] - timedelta(minutes=1)
    result = forecast.score([row], obs)
    assert result["rows"][0]["baseline_excluded_reason"] == "no_observation_by_issuance"
    assert result["metrics"][0]["persistence"]["matched"] == 0
    row["issued"] = obs[0]["clock"] + timedelta(minutes=1)
    row["target"] = obs[2]["clock"]
    result = forecast.score([row], [obs[0], *obs[2:]])
    assert result["rows"][0]["baseline_excluded_reason"] == "observation_gap"
    result = forecast.score([row], obs)
    assert result["rows"][0]["persistence_mw"] == obs[0]["load"]


async def test_forecast_timezone_is_explicit_and_ambiguous_clock_refused(ctx):
    env = await get_bpa_operations(ctx, intervals=24)
    with pytest.raises(InvalidQuery, match="timezone"):
        forecast.actuals(env, None)
    env.data["intervals"][0]["timestamp"] = "11/01/2026 01:30"
    with pytest.raises(SourceSchemaChanged, match="ambiguous"):
        forecast.actuals(env, "America/Los_Angeles")


async def test_intervals_and_unmatched_actual_counts(ctx):
    obs = forecast.actuals(await get_bpa_operations(ctx, intervals=24), "America/Los_Angeles")
    forecasts = forecast.parse_forecasts(forecast.sample_forecasts(obs))[:2]
    forecasts[0].update(lower_mw=-1e9, upper_mw=1e9, nominal_coverage=0.9)
    forecasts[1]["target"] += timedelta(days=1)
    result = forecast.score(forecasts, obs)
    assert result["metrics"][0]["bands"]["0.9"]["empirical_coverage"] == 1
    assert result["metrics"][1]["excluded"] == {"target_not_returned": 1}
    assert result["status"] == "partial"
    assert result["scoring"]["matched"] == result["scoring"]["excluded"] == 1
    assert [group["status"] for group in result["metrics"]] == ["scored", "unscored"]


@pytest.mark.parametrize("case", ["outside_window", "missing_actual"])
async def test_forecast_without_usable_actuals_is_explicitly_unscored(ctx, case):
    obs = forecast.actuals(await get_bpa_operations(ctx, intervals=24), "America/Los_Angeles")
    forecasts = forecast.parse_forecasts(forecast.sample_forecasts(obs))
    if case == "outside_window":
        for row in forecasts:
            row["target"] += timedelta(days=30)
        reason = "target_not_returned"
    else:
        for observation in obs:
            observation["load"] = None
        reason = "missing_actual"
    result = forecast.score(forecasts, obs)
    assert result["status"] == "unscored"
    assert result["scoring"]["matched"] == 0
    assert result["scoring"]["excluded"] == len(forecasts)
    for group in result["metrics"]:
        assert group["status"] == group["persistence"]["status"] == "unscored"
        assert group["mae_mw"] is group["rmse_mw"] is group["bias_mw"] is None
        assert group["excluded"] == {reason: group["submitted"]}


async def test_missing_actual_can_leave_a_partial_forecast_score(ctx):
    obs = forecast.actuals(await get_bpa_operations(ctx, intervals=24), "America/Los_Angeles")
    forecasts = forecast.parse_forecasts(forecast.sample_forecasts(obs))[::2][:2]
    obs[1]["load"] = None
    result = forecast.score(forecasts, obs)
    assert result["status"] == result["metrics"][0]["status"] == "partial"
    assert result["scoring"]["matched"] == result["scoring"]["excluded"] == 1
    assert result["metrics"][0]["excluded"] == {"missing_actual": 1}


def test_unscored_cli_warns_and_still_exports_evidence(tmp_path):
    inputs = tmp_path / "forecasts.csv"
    inputs.write_text("model_id,issued_at,target_at,prediction_mw\n"
                      "outside-window,1900-01-01T00:00:00Z,1900-01-01T00:05:00Z,5000\n")
    output = tmp_path / "unscored"
    result = subprocess.run([sys.executable, "-m", "doe_mcp.cli", "app", "forecast-benchmark",
                             "--forecasts", str(inputs), "--actual-timezone", "America/Los_Angeles",
                             "--output", str(output)], cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "status: unscored (0 of 1 forecasts matched)" in result.stderr
    assert "Warning: no forecasts matched usable actuals" in result.stderr
    report = json.loads((output / "report.json").read_bytes())
    assert report["status"] == "unscored" and report["steps"][0]["envelope"]["evidence"]
    assert report["rows"][0]["excluded_reason"] == "target_not_returned"
    assert (output / "manifest.json").is_file() and (output / "scores.csv").is_file()


async def test_weather_response_units_calendar_and_bundle_integrity(ctx, tmp_path):
    run = Run(ctx, "weather-package", "recorded")
    files = await weather.build(run, **WEATHER)
    output = tmp_path / "package"
    export_bundle(output, run.report, files)
    rows = list(csv.DictReader(io.StringIO(files["weather.csv"].decode())))
    assert len(rows) == 5 and rows[-1]["date"] == "2024-12-30"
    assert "tmax (deg c)" in rows[0]
    manifest = json.loads((output / "manifest.json").read_bytes())
    for resource in manifest["files"]:
        assert hashlib.sha256((output / resource["file"]).read_bytes()).hexdigest() == resource["sha256"]
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        export_bundle(output, run.report, files)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


@pytest.mark.parametrize("defect", ["missing_day", "missing_value", "wrong_point", "wrong_range", "wrong_variables", "missing_units"])
async def test_weather_refuses_incomplete_or_mismatched_extract(ctx, defect):
    data = copy.deepcopy((await get_daymet_point(ctx, **WEATHER)).data)
    if defect == "missing_day":
        data["days"].pop(1)
    elif defect == "missing_value":
        data["days"][0]["values"]["tmax"] = None
    elif defect == "wrong_point":
        data["latitude"] += 1
    elif defect == "wrong_range":
        data["returned_range"]["from"] = "2024-12-25"
    elif defect == "wrong_variables":
        data["columns"][0]["name"] = "prcp"
    else:
        data["columns"][0]["units"] = ""
    with pytest.raises(SourceSchemaChanged):
        weather.package(data, **WEATHER)


async def test_weather_multiple_variables_uses_publisher_columns(ctx):
    params = {**WEATHER, "start": "2023-06-01", "end": "2023-06-05", "variables": "prcp,tmax,tmin"}
    data = (await get_daymet_point(ctx, **params)).data
    payload, metadata = weather.package(data, **params)
    assert "prcp (mm/day)" in payload.decode().splitlines()[0]
    assert len(metadata["recordSet"][0]["field"]) == 5


async def test_docket_follows_all_ferc_page_and_discloses_budget(ctx):
    run = Run(ctx, "docket-tracker", "recorded")
    await dockets.build(run, **DOCKET)
    assert run.report["pagination"]["pages_read"] == [1, 2]
    assert run.report["steps"][1]["envelope"]["data"]["record_count"] == 0
    assert run.report["pagination"]["excluded_ferc_documents"] == 9
    assert run.report["pagination"]["gaps"] == ["page_budget_exhausted"]
    assert not run.report["pagination"]["complete"]
    assert run.report["documents"][0]["comments_close_on"] is None


async def test_docket_grouping_and_incomplete_snapshot_diff(ctx):
    run = Run(ctx, "docket-tracker", "recorded")
    await dockets.build(run, **DOCKET)
    previous = copy.deepcopy(run.report)
    previous["documents"].append({"document_number": "prior-only"})
    changes = dockets.compare(run.report, previous)
    assert changes["not_returned"] == ["prior-only"]
    assert not changes["both_searches_complete"]
    previous["parameters"]["query"] = "different"
    with pytest.raises(InvalidQuery):
        dockets.compare(run.report, previous)


@pytest.mark.parametrize("name", ["forecast-benchmark", "weather-package", "docket-tracker", "energy-assumptions"])
def test_actual_cli_samples_export_offline(name, tmp_path):
    result = subprocess.run([sys.executable, "-m", "doe_mcp.cli", "app", name, "--output", str(tmp_path / name)],
                            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / name / "report.json").read_bytes())
    assert report["mode"] == "recorded"
    if name == "docket-tracker":
        assert report["source"] == "federal-register"
        assert report["label"] == "Federal Register notice tracker"
        assert [step["tool"] for step in report["steps"]] == ["docs.search_rulemakings"] * 2


def _application_args(*args):
    import argparse
    from doe_mcp.cli.applications import register
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["app", "docket-tracker", *args])


async def test_default_notice_app_never_reads_credentials(monkeypatch):
    from doe_mcp.cli.applications import execute
    from doe_mcp.core.credentials import Credentials

    def refuse_load(*args, **kwargs):
        raise AssertionError("recorded applications must not read user credentials")

    monkeypatch.setattr(Credentials, "load", refuse_load)
    report, files = await execute(_application_args())
    assert report["label"] == "Federal Register notice tracker"
    assert report["source"] == "federal-register"
    assert "notices.csv" in files
    assert all(step["tool"] == "docs.search_rulemakings" for step in report["steps"])


@pytest.mark.parametrize("live", [False, True])
async def test_explicit_regulations_selection_is_gated_without_loading_keys(monkeypatch, live):
    from doe_mcp.cli.applications import execute
    from doe_mcp.core.credentials import Credentials
    from doe_mcp.core.errors import SourceNotActivated

    def refuse_load(*args, **kwargs):
        raise AssertionError("the proposed source gate must precede reading user credentials")

    monkeypatch.setattr(Credentials, "load", refuse_load)
    args = _application_args("--source", "regulations", *(["--live"] if live else []))
    with pytest.raises(SourceNotActivated, match="no fixture was written"):
        await execute(args)


@pytest.mark.parametrize("live", [False, True])
def test_regulations_cli_refusal_writes_no_bundle(tmp_path, live):
    output = tmp_path / "gated-docket"
    result = subprocess.run(
        [sys.executable, "-m", "doe_mcp.cli", "app", "docket-tracker", "--source",
         "regulations", "--output", str(output), *(["--live"] if live else [])],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "regulations-gov-doe" in result.stderr
    assert "no fixture was written" in result.stderr
    assert not output.exists()


async def test_site_docket_sample_is_a_recorded_notice_tracker_without_credentials(monkeypatch):
    from doe_mcp.applications.samples import recorded_sample
    from doe_mcp.core.credentials import Credentials
    from doe_mcp.replay import load_replay_context

    def refuse_load(*args, **kwargs):
        raise AssertionError("site examples must not read user credentials")

    monkeypatch.setattr(Credentials, "load", refuse_load)
    ctx = load_replay_context(ROOT / "tests" / "fixtures")
    report, files = await recorded_sample(ctx, "docket-tracker")
    assert report["label"] == "Federal Register notice tracker"
    assert report["source"] == "federal-register"
    assert "notices.csv" in files
    assert all(step["tool"] == "docs.search_rulemakings" for step in report["steps"])
