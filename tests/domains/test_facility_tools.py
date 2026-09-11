"""`facility.find_wind_turbines` and `facility.find_solar_facilities`.

Two tools over one PostgreSQL schema. What the envelope has to disclose here
is not freshness but certainty: every record carries the compilers' own
confidence score, and an answer that prints a visually confirmed turbine and
an unconfirmed concrete pad in the same list, with no distinction, has turned
an estimate into a survey.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (AccessPath, PaginationCoverage,
                                   RegistryCoverage, ResultCoverage,
                                   WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import energy


async def test_a_state_filter_answers_with_that_state(ctx):
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert env.data["record_count"] == 5
    assert {r["t_state"] for r in env.data["records"]} == {"RI"}
    assert env.data["query"] == ["t_state=eq.RI"]
    assert env.coverage.result == ResultCoverage.hit
    assert env.coverage.registry == RegistryCoverage.covered


async def test_the_page_is_reported_against_the_matching_total(ctx):
    """35 turbines match and five come back. `pagination: complete` on that
    would be a claim the answer cannot back."""
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert env.data["total_matches"] == 35
    assert env.coverage.pagination == PaginationCoverage.truncated
    assert "35 records match" in env.data["more"]


async def test_records_the_compilers_were_unsure_of_are_disclosed(ctx):
    """Counted and marked, never dropped: a turbine whose imagery showed only
    a pad is a real record, and removing it would understate the inventory."""
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    codes = {w.code for w in env.warnings}
    assert WarningCode.screening_only in codes
    text = " ".join(w.message for w in env.warnings)
    assert "confidence code below" in text
    assert env.data["confidence_codes"]
    assert any(r["t_conf_loc"] < 3 or r["t_conf_atr"] < 3
               for r in env.data["records"])


async def test_the_two_tools_read_different_tables(ctx):
    """The path aliases serve the same schema, so this is the check that the
    solar tool is not quietly answering from the wind table."""
    wind = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    solar = await energy.find_solar_facilities(ctx, state="RI", rows=5)
    assert "t_manu" in wind.data["records"][0]
    assert "p_cap_ac" in solar.data["records"][0]
    assert wind.data["total_matches"] != solar.data["total_matches"]
    assert [p.source_id for p in solar.provenance] == ["usgs-uspvdb"]


async def test_units_that_differ_between_adjacent_columns_are_stated(ctx):
    """`t_cap` is one turbine in kilowatts and `p_cap` is its whole project
    in megawatts. Two capacity columns, two units, one row."""
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert "kilowatts" in env.data["units"]
    assert "megawatts" in env.data["units"]


async def test_provenance_carries_the_release_and_its_doi(ctx):
    env = await energy.find_solar_facilities(ctx, state="RI", rows=5)
    entry = env.provenance[0]
    assert entry.source_id == "usgs-uspvdb"
    assert entry.steward == "US Geological Survey"
    assert entry.dataset_version == "USPVDB v4.0 (April 2026)"
    assert entry.citation.doi == "10.5066/P9IA3TUS"
    assert entry.citation.required is False


async def test_a_cache_hit_is_reported_as_one(ctx):
    first = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    again = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert first.provenance[0].access_path == AccessPath.live
    assert again.provenance[0].access_path == AccessPath.cache
    assert again.provenance[0].retrieved_at == first.provenance[0].retrieved_at


async def test_the_answer_says_it_is_an_inventory_and_where_output_lives(ctx):
    """The commonest wrong use of this data is reading it as generation."""
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert "not how much they generated" in env.data["note"]
    assert "energy.get_data" in env.data["note"]
    assert "eia_id" in env.data["note"]


async def test_the_version_is_the_registrys_record_because_the_api_states_none(
        ctx):
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    assert env.data["dataset_version"] == "USWTDB V9.0 (26 June 2026)"
    assert "states no release version of its own" in env.data["note"]


async def test_evidence_points_at_each_record_by_its_stable_id(ctx):
    env = await energy.find_wind_turbines(ctx, state="RI", rows=5)
    refs = {p.id for p in env.provenance}
    assert len(env.evidence) == 5
    ids = {r["case_id"] for r in env.data["records"]}
    for item in env.evidence:
        assert item.source_ref in refs
        assert int(item.record_id.removeprefix("turbines#")) in ids


async def test_a_bad_bounding_box_is_refused_rather_than_matching_nothing(ctx):
    """A swapped pair is a valid query that matches nothing, which reads as
    'there are no turbines there'."""
    with pytest.raises(InvalidQuery, match="minimum at or above"):
        await energy.find_wind_turbines(ctx, bbox="-105.0,39.0,-105.5,40.0")
    with pytest.raises(InvalidQuery, match="four comma-separated"):
        await energy.find_wind_turbines(ctx, bbox="-105.0,39.0")
    with pytest.raises(InvalidQuery, match="must be numbers"):
        await energy.find_wind_turbines(ctx, bbox="west,39.0,-105.0,40.0")
