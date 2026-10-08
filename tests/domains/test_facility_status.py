"""`compute.facility_status`: NERSC's public status board.

What this tool must not do is turn one facility's board into a statement
about DOE computing, convert timestamps into a zone the publisher never
stated, or answer a misspelled system name with an empty board.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse
from doe_mcp.adapters.facility_status import FacilityStatusAdapter
from doe_mcp.core.envelope import RegistryCoverage, ResultCoverage
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged
from doe_mcp.domains import discovery


async def test_the_whole_board_comes_back_with_planned_outages(ctx):
    env = await discovery.facility_status(ctx)
    assert env.data["facility"] == "NERSC"
    assert env.data["record_count"] == env.data["systems_on_board"] == 19
    assert "perlmutter" in {s["name"] for s in env.data["systems"]}
    starts = [o["start_at"] for o in env.data["planned_outages"]]
    assert starts == sorted(starts)
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.coverage.result == ResultCoverage.hit


async def test_one_system_narrows_both_the_board_and_the_outages(ctx):
    env = await discovery.facility_status(ctx, system="Perlmutter")
    assert [s["name"] for s in env.data["systems"]] == ["perlmutter"]
    assert env.data["planned_outages"]
    assert {o["system"] for o in env.data["planned_outages"]} == {
        "perlmutter"}


async def test_a_misspelled_system_is_refused_with_the_real_names(ctx):
    with pytest.raises(InvalidQuery, match="perlmutter"):
        await discovery.facility_status(ctx, system="perlmuter")


async def test_timestamps_are_passed_through_and_the_zone_is_unstated(ctx):
    env = await discovery.facility_status(ctx, system="perlmutter")
    assert env.data["systems"][0]["updated_at"].count(":") == 2
    assert "+" not in env.data["systems"][0]["updated_at"]
    assert "does not state a zone" in env.data["timestamps"]


async def test_the_unreadable_facilities_are_named_not_implied_up(ctx):
    env = await discovery.facility_status(ctx, include_planned=False)
    named = {f["source_id"] for f in env.data["other_facilities"]}
    assert named == {"alcf-status", "olcf-status"}
    assert all(f["why_not_read"] for f in env.data["other_facilities"])
    assert "planned_outages" not in env.data


class _Fixed:
    def __init__(self, payload):
        self.payload = payload

    async def fetch_json(self, url, params):
        return JsonResponse(url=url, payload=self.payload, headers={})


async def test_a_board_that_is_not_an_array_is_a_schema_change(ctx):
    manifest = ctx.sources.get("nersc-status")
    adapter = FacilityStatusAdapter(fetcher=_Fixed({"detail": "moved"}))
    with pytest.raises(SourceSchemaChanged):
        await adapter.board(manifest)


async def test_a_row_without_a_name_is_a_schema_change(ctx):
    manifest = ctx.sources.get("nersc-status")
    adapter = FacilityStatusAdapter(fetcher=_Fixed([{"status": "active"}]))
    with pytest.raises(SourceSchemaChanged):
        await adapter.board(manifest, include_planned=False)
