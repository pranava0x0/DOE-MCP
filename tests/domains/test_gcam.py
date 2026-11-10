"""Release tables keep their schema, file identity, source notes and blank cells."""
import csv
import io
import json
from pathlib import Path

import pytest

from doe_mcp.adapters.gcam import parse_table
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged
from doe_mcp.domains.gcam import GCAM_TOOLS
from doe_mcp.gcam_examples import EXAMPLES
from doe_mcp.workflows import run_workflow, tool_spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize("domain", GCAM_TOOLS)
async def test_all_table_tools_replay_distinct_published_inputs(ctx, domain):
    manifest = ctx.sources.require_active("gcam-" + domain)
    tables = ctx.gcam.params_for(manifest).tables
    urls = {t.url for t in tables.values()}
    interactions = json.loads((FIXTURES / (manifest.id + ".json")).read_text())["interactions"]
    assert {i["url"] for i in interactions} == urls
    assert len(urls) == len(tables)
    specs = GCAM_TOOLS[domain].tools()
    assert {s.name for s in specs} == {f"{domain}.{name}" for name in tables}
    for spec in specs:
        env = await spec.fn(ctx, rows=1)
        table = tables[spec.name.split(".")[1]]
        assert env.evidence[0].locator == table.url
        assert env.data["release"] == "GCAM v9.1"
        assert env.data["source_notes"] and env.data["record_count"] == 1
        assert list(env.data["records"][0]["values"]) == table.columns
        assert env.data["records"][0]["evidence_ref"] == env.evidence[0].id
        assert env.execution.tool == spec.name


async def test_filter_before_paging_with_source_row_and_cached_provenance(ctx):
    call = tool_spec("power.geothermal_curves").fn
    first = await call(ctx, filters={"region_GCAM3": "Japan"}, rows=1)
    second = await call(ctx, filters={"region_GCAM3": "Japan"}, rows=1, offset=1)
    assert first.data["total_matches"] == second.data["total_matches"] == 2
    assert first.coverage.pagination.value == "truncated" and second.coverage.pagination.value == "complete"
    a, b = (e.data["records"][0] for e in (first, second))
    assert a["values"]["available"] == "0" and b["values"]["available"] == "0.56582"
    assert b["row_number"] > a["row_number"]
    assert second.provenance[0].access_path.value == "cache"
    assert first.provenance[0].retrieved_at == second.provenance[0].retrieved_at
    with pytest.raises(InvalidQuery):
        await call(ctx, filters={"region_GCAM_ID": "19"})
    with pytest.raises(InvalidQuery):
        await call(ctx, rows=51)


async def test_publisher_blank_is_null_not_zero(ctx):
    env = await tool_spec("emissions.pollutant_floors").fn(ctx, filters={"supplysector": "cement"})
    assert env.data["records"][0]["values"]["SO2"] is None


def test_schema_drift_fails_closed_on_a_changed_release_table():
    fixture = json.loads((FIXTURES / "gcam-water.json").read_text())
    item = fixture["interactions"][0]
    raw = item["body"]
    start = next(line for line in raw.splitlines() if not line.startswith("#"))
    cols = next(csv.reader(io.StringIO(start)))
    for broken in [raw.replace(start, "changed,header", 1), raw + "\nunexpected,row\n", start + "\n"]:
        with pytest.raises(SourceSchemaChanged):
            parse_table(broken, cols)


async def test_country_join_follows_returned_classification(ctx):
    # Use a different country in the same recorded lookup. The next request
    # must change to its region rather than reuse the demo's original ID.
    from dataclasses import replace
    from doe_mcp.gcam_examples import run_example
    calls = []

    async def call(tool, **args):
        calls.append((tool, args["filters"]))
        return await tool_spec(tool).fn(ctx, **args)

    await run_example(replace(EXAMPLES["kenya-soil"], iso="can"), call)
    assert calls[-1] == ("land.soil_timescales", {"GCAM_region_ID": "8"})


async def test_climate_example_returns_catalog_metadata(ctx):
    report = await run_workflow(ctx, "global-climate-archive")
    first = report["steps"][0]["envelope"]["data"]["datasets"][0]
    assert first["model"] == "CanESM5" and first["variable_units"] == ["K"]
    assert first["variant_label"] in report["answer"]
