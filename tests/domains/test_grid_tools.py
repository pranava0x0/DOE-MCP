"""`grid.get_bpa_operations`: what the envelope has to disclose.

The tool serves a feed whose most dangerous property is that it looks
complete when it is not — the file lists intervals that have not happened
yet — so the disclosures here are the point of the tool, not decoration
around it.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (AccessPath, PaginationCoverage,
                                   RegistryCoverage, ResultCoverage,
                                   WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import energy


async def test_it_answers_with_the_most_recent_intervals(ctx):
    env = await energy.get_bpa_operations(ctx, intervals=6)
    assert env.data["record_count"] == 6
    assert len(env.data["intervals"]) == 6
    assert env.data["latest"]["timestamp"] == env.data["intervals"][-1]["timestamp"]
    assert env.data["units"] == "MW"
    assert env.coverage.result == ResultCoverage.hit
    assert env.coverage.registry == RegistryCoverage.covered


async def test_the_unpopulated_intervals_are_disclosed_rather_than_returned(ctx):
    """A caller must be able to tell 'the day has not finished' from 'the
    grid stopped'. The count is reported and the rows are not."""
    env = await energy.get_bpa_operations(ctx, intervals=200)
    assert env.data["pending_intervals"] > 0
    assert "pending_note" in env.data
    assert "have not happened yet" in env.data["pending_note"]
    for interval in env.data["intervals"]:
        assert any(v is not None for v in interval["values"].values())


async def test_every_answer_says_the_readings_are_not_settled(ctx):
    """Five-minute SCADA is situational awareness. Presenting it as a
    system-of-record number is the misuse this warning exists to stop."""
    env = await energy.get_bpa_operations(ctx)
    codes = {w.code for w in env.warnings}
    assert WarningCode.screening_only in codes
    text = " ".join(w.message for w in env.warnings)
    assert "settled" in text


async def test_provenance_names_bpa_and_reports_a_cache_hit_as_one(ctx):
    first = await energy.get_bpa_operations(ctx)
    again = await energy.get_bpa_operations(ctx)
    assert [p.source_id for p in first.provenance] == ["bpa-operations"]
    assert first.provenance[0].steward == "Bonneville Power Administration"
    assert first.provenance[0].access_path == AccessPath.live
    assert again.provenance[0].access_path == AccessPath.cache
    assert again.provenance[0].retrieved_at == first.provenance[0].retrieved_at


async def test_asking_for_less_than_the_window_reports_truncation(ctx):
    """`pagination: complete` on a slice of a seven-day window would be a
    claim the answer cannot back."""
    slice_env = await energy.get_bpa_operations(ctx, intervals=3)
    assert slice_env.coverage.pagination == PaginationCoverage.truncated
    whole = await energy.get_bpa_operations(
        ctx, intervals=slice_env.data["window_intervals"])
    assert whole.coverage.pagination == PaginationCoverage.complete


async def test_the_time_range_covers_the_intervals_returned(ctx):
    env = await energy.get_bpa_operations(ctx, intervals=5)
    stamps = [i["timestamp"] for i in env.data["intervals"]]
    assert env.coverage.time_range is not None
    assert env.coverage.time_range.from_ == min(stamps)
    assert env.coverage.time_range.to == max(stamps)


async def test_evidence_points_at_the_registered_source(ctx):
    env = await energy.get_bpa_operations(ctx, intervals=4)
    refs = {p.id for p in env.provenance}
    assert len(env.evidence) == 4
    for item in env.evidence:
        assert item.source_ref in refs
        assert item.effective_at


async def test_an_out_of_range_interval_count_is_refused(ctx):
    for bad in (0, 2017):
        with pytest.raises(InvalidQuery, match="intervals must be"):
            await energy.get_bpa_operations(ctx, intervals=bad)


async def test_it_points_at_eia_930_for_the_rest_of_the_country(ctx):
    """The tool covers one balancing authority. Saying so, and naming the
    tool that covers the others, is the difference between a coverage gap
    and a dead end."""
    env = await energy.get_bpa_operations(ctx)
    assert "energy.grid_status" in env.data["note"]
    assert "BPAT" in env.data["note"]


# --- energy.grid_status and EIA-930's four metrics -------------------------
#
# EIA-930's hourly route carries demand, day-ahead forecast, net generation
# and total interchange under one `value` column and one period axis. The
# forecast is published for hours that have not happened yet, so it always
# sorts first on a newest-first query. Until 2026-09-08 this tool sent no
# type filter and answered "how much power did CISO use last night" with
# tomorrow's forecast. These are the tests that would have caught it.

async def test_grid_status_answers_with_demand_by_default(keyed_ctx):
    """'How much power did X use' means demand, not a prediction of it."""
    env = await energy.grid_status(keyed_ctx, "CISO", hours=3)
    assert env.data["metric_code"] == "D"
    assert env.data["metric"] == "Demand"
    assert {r["type"] for r in env.data["observations"]} == {"D"}


async def test_the_forecast_never_arrives_under_a_demand_query(keyed_ctx):
    """The regression. A single DF row in a demand answer is a prediction
    presented as a measurement, and its period is in the future."""
    env = await energy.grid_status(keyed_ctx, "CISO", hours=3)
    assert all(r["type"] != "DF" for r in env.data["observations"])
    assert all("forecast" not in (r.get("type-name") or "").lower()
               for r in env.data["observations"])


async def test_asking_for_the_forecast_says_it_has_not_happened(keyed_ctx):
    env = await energy.grid_status(keyed_ctx, "CISO", hours=3, metric="forecast")
    assert env.data["metric_code"] == "DF"
    assert "has not happened yet" in env.data["forecast_note"]
    codes = {w.code for w in env.warnings}
    assert WarningCode.screening_only in codes
    assert WarningCode.stale_source not in codes


async def test_a_measurement_is_stale_where_a_forecast_is_not(keyed_ctx):
    """`stale_source` says the newest row is behind now. On a forecast that
    is backwards — the newest row is ahead of now — so the two metrics carry
    different warnings."""
    demand = await energy.grid_status(keyed_ctx, "CISO", hours=3)
    assert WarningCode.stale_source in {w.code for w in demand.warnings}
    assert "forecast_note" not in demand.data


async def test_the_answer_names_the_metric_and_the_three_it_is_not(keyed_ctx):
    env = await energy.grid_status(keyed_ctx, "CISO", hours=3)
    assert env.data["units"] == "megawatthours"
    for other in ("forecast", "generation", "interchange"):
        assert other in env.data["note"]


async def test_an_unknown_metric_is_refused(keyed_ctx):
    with pytest.raises(InvalidQuery, match="different measurements"):
        await energy.grid_status(keyed_ctx, "CISO", metric="nonsense")


async def test_eias_own_codes_are_accepted_too(keyed_ctx):
    env = await energy.grid_status(keyed_ctx, "CISO", hours=3, metric="D")
    assert env.data["metric_code"] == "D"
