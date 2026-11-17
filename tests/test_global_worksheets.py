import pytest

from doe_mcp.core.errors import SourceSchemaChanged, SourceUnavailable
from doe_mcp.global_worksheets import WALKS, make_sheet
from doe_mcp.workflows import tool_spec


async def source(ctx, case):
    w = WALKS[case]
    return await tool_spec(w.tool).fn(ctx, filters=w.filters, rows=50)


async def test_regional_comparison_refuses_partial_page(ctx):
    w = WALKS["latin-america-regions"]
    env = await tool_spec(w.tool).fn(ctx, filters=w.filters, rows=10)
    with pytest.raises(SourceUnavailable, match="whole filtered table"):
        make_sheet("latin-america-regions", env)


async def test_share_check_exposes_an_unbalanced_total(ctx):
    case = "china-cooling-balance"
    env = await source(ctx, case)
    records = env.data["records"]
    records[0]["values"]["2010"] = "0.2"
    sheet, _ = make_sheet(case, env)
    assert sheet["summary"] == {"sum_2010": "1.100", "sum_2020": "1.000"}
    records[0]["values"]["water_type"] = records[1]["values"]["water_type"]
    with pytest.raises(SourceSchemaChanged, match="duplicate"):
        make_sheet(case, env)


@pytest.mark.parametrize("value", [None, "NaN", "1.25", "-0.2", "unknown"])
async def test_share_check_refuses_invalid_values(ctx, value):
    env = await source(ctx, "china-cooling-balance")
    env.data["records"][0]["values"]["2010"] = value
    with pytest.raises(SourceSchemaChanged):
        make_sheet("china-cooling-balance", env)


async def test_column_role_requires_publisher_note(ctx):
    env = await source(ctx, "world-bioenergy-columns")
    env.data["source_notes"] = []
    with pytest.raises(SourceSchemaChanged, match="which preference column"):
        make_sheet("world-bioenergy-columns", env)


async def test_sorted_grades_keep_the_source_for_every_row(ctx):
    case = "world-geothermal-grades"
    env = await source(ctx, case)
    sheet, _ = make_sheet(case, env)
    by_ref = {r["evidence_ref"]: r["values"] for r in env.data["records"]}
    for region, grade, heat, cost, ref in sheet["rows"]:
        recorded = by_ref[ref]
        assert [region, grade, heat, cost] == [recorded[k] for k in ("region_GCAM3", "grade", "available", "extractioncost")]


async def test_capture_complements_include_endpoints_and_reject_invalid_fraction(ctx):
    case = "world-capture-residual"
    env = await source(ctx, case)
    v = env.data["records"][0]["values"]
    v.update({"initial-future-year": "1", "2100": "0"})
    sheet, _ = make_sheet(case, env)
    assert sheet["rows"][0][4:6] == ["0", "1"]
    v["2100"] = "1.1"
    with pytest.raises(SourceSchemaChanged):
        make_sheet(case, env)


async def test_response_schedule_refuses_ambiguous_numeric_levels(ctx):
    case = "world-industry-response"
    env = await source(ctx, case)
    env.data["records"][1]["values"]["pc.output_GJ"] = "0.00"
    with pytest.raises(SourceSchemaChanged, match="same numeric"):
        make_sheet(case, env)
