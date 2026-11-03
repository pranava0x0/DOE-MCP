import csv
import io

import pytest

from doe_mcp.core.envelope import Envelope
from doe_mcp.core.errors import SourceUnavailable
from doe_mcp.demo_worksheets import bibliography, csv_text, soil_metadata
from doe_mcp.workflows import run_workflow
from tools.render_workflows import render


@pytest.mark.parametrize("case,build,collection,id_key", [
    ("bibliography", bibliography, "records", "id"),
    ("soil-metadata", soil_metadata, "biosamples", "sample_id"),
])
async def test_each_row_points_back_to_its_record(ctx, case, build, collection, id_key):
    report = await run_workflow(ctx, case)
    env = Envelope.model_validate(report["steps"][0]["envelope"])
    rows = report["worksheet"]["rows"]
    refs = {ev.id: ev.record_id for ev in env.evidence}
    assert len(rows) == len(env.data[collection]) > 0
    for row, record in zip(rows, env.data[collection], strict=True):
        assert refs[row[-1]] == row[0] == record[id_key]
    env.evidence.pop()
    with pytest.raises(SourceUnavailable, match="matching publisher evidence"):
        build(env)
    env.data[collection] = []
    with pytest.raises(SourceUnavailable, match="no "):
        build(env)


async def test_bibliography_keeps_author_omissions_and_does_not_invent_doi(ctx):
    report = await run_workflow(ctx, "bibliography")
    env = Envelope.model_validate(report["steps"][0]["envelope"])
    assert env.coverage.sources_searched == ["osti-doe-pages"]
    record = env.data["records"][0]
    record.pop("doi")
    record["title"] = '=HYPERLINK("x")'
    sheet = bibliography(env)
    row = next(csv.DictReader(io.StringIO(csv_text(sheet["columns"], sheet["rows"]))))
    assert row["Listed authors"] == "; ".join(record["authors"])
    assert int(row["Authors omitted by DOE-MCP"]) == record["authors_truncated"] > 0
    assert row["DOI"] == "" and row["Title"] == "'" + record["title"]


async def test_soil_audit_handles_zero_missing_and_ranged_measurements(ctx):
    report = await run_workflow(ctx, "soil-metadata")
    env = Envelope.model_validate(report["steps"][0]["envelope"])
    assert env.coverage.pagination.value == "truncated"
    samples = env.data["biosamples"][:2]
    env.data["biosamples"] = samples
    samples[0].update(latitude=0.0, longitude=-1.0, depth_m={"min": 0.0, "max": 1.0})
    samples[1].update(latitude=None, depth_m=None, collected=None, place='<img src=x>')
    sheet = soil_metadata(env)
    assert sheet["missing"] == {"collection date": 1, "coordinate pair": 1, "depth in metres": 1}
    row = next(csv.DictReader(io.StringIO(csv_text(sheet["columns"], sheet["rows"]))))
    assert (row["Latitude"], row["Longitude"], row["Depth (m)"], row["Minimum depth (m)"]) == ("0.0", "-1.0", "", "0.0")
    report["worksheet"] = sheet
    page = render([report])
    assert '<img src=x>' not in page and '&lt;img src=x&gt;' in page


@pytest.mark.parametrize("bad", [False, float("nan"), float("inf"), 10**400])
def test_nmdc_does_not_count_a_malformed_number_as_a_measurement(bad):
    import json

    from doe_mcp.adapters.nmdc import parse_biosample
    from pathlib import Path

    recording = json.loads((Path(__file__).parent / "fixtures/nmdc-runtime.json").read_text())
    entry = next(e for e in recording["interactions"] if "washington" in e["params"].get("filter", ""))
    raw = entry["body"]["results"][0]
    raw["lat_lon"] = {"latitude": bad, "longitude": 0}
    raw["depth"] = {"has_unit": "m", "has_numeric_value": bad}
    sample = parse_biosample(raw, "nmdc-runtime")
    assert sample.latitude is None and sample.longitude == 0
    assert sample.depth_m is None


async def test_downloaded_worksheet_identifies_run_outside_the_page(ctx, tmp_path):
    from doe_mcp.workflows import write_report
    report = await run_workflow(ctx, "bibliography")
    write_report(tmp_path, report)
    with (tmp_path / "bibliography-worksheet.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {r["Run mode"] for r in rows} == {"recorded"}
    assert {r["Registry revision"] for r in rows} == {report["registry_revision"]}
    assert rows[0]["Step 1 evidence ID"] == report["steps"][0]["envelope"]["evidence"][0]["id"]


async def test_soil_page_shows_unknown_total_beside_the_sample(ctx):
    from tools.render_workflows import scope_line
    report = await run_workflow(ctx, "soil-metadata")
    report["steps"][0]["envelope"]["data"]["total_matches"] = None
    assert "5 returned, total unknown" in scope_line(report)
