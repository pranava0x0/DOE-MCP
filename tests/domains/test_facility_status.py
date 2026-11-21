"""`compute.facility_status`: NERSC's public status board.

What this tool must not do is turn one facility's board into a statement
about DOE computing, convert timestamps into a zone the publisher never
stated, or answer a misspelled system name with an empty board.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
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
    adapter = FacilityStatusAdapter(cache=TTLCache(), fetcher=_Fixed({"detail": "moved"}))
    with pytest.raises(SourceSchemaChanged):
        await adapter.board(manifest)


async def test_a_row_without_a_name_is_a_schema_change(ctx):
    manifest = ctx.sources.get("nersc-status")
    adapter = FacilityStatusAdapter(cache=TTLCache(), fetcher=_Fixed([{"status": "active"}]))
    with pytest.raises(SourceSchemaChanged):
        await adapter.board(manifest, include_planned=False)


async def test_a_planned_outage_without_a_system_is_a_schema_change(ctx):
    manifest = ctx.sources.get("nersc-status")

    class _Two:
        async def fetch_json(self, url, params):
            body = ([{"name": "perlmutter", "status": "active"}]
                    if url.endswith("/status") else [[{"start_at": "x"}]])
            return JsonResponse(url=url, payload=body, headers={})

    with pytest.raises(SourceSchemaChanged, match="planned-outage"):
        await FacilityStatusAdapter(cache=TTLCache(), fetcher=_Two()).board(manifest)


async def test_every_planned_outage_can_be_cited(ctx):
    env = await discovery.facility_status(ctx, system="perlmutter")
    ids = {e.record_id for e in env.evidence}
    for o in env.data["planned_outages"]:
        assert f"{o['system']}@{o['start_at']}" in ids


@pytest.mark.parametrize("warm_status", [False, True])
async def test_status_and_outages_keep_separate_fetch_histories(
        ctx, replay, monkeypatch, warm_status):
    from doe_mcp.adapters import base
    from doe_mcp.core.envelope import AccessPath

    status_time = "2026-11-21T10:00:00Z"
    outage_time = "2026-11-21T10:05:00Z"
    times = iter((status_time, outage_time))
    monkeypatch.setattr(base, "utc_now_iso", lambda: next(times))
    if warm_status:
        status_only = await discovery.facility_status(ctx, include_planned=False)
        assert len(status_only.provenance) == 1
        assert status_only.provenance[0].retrieved_at == status_time

    env = await discovery.facility_status(ctx)
    assert len(replay.calls) == 2
    status, planned = env.provenance
    assert status.source_id == planned.source_id == "nersc-status"
    assert status.id != planned.id
    assert status.dataset != planned.dataset
    assert status.retrieved_at == status_time
    assert status.access_path is (AccessPath.cache if warm_status else AccessPath.live)
    assert planned.retrieved_at == outage_time
    assert planned.access_path is AccessPath.live
    assert planned.cache_age_seconds == 0
    by_record = {e.record_id: e for e in env.evidence}
    for row in env.data["systems"]:
        evidence = by_record[row["name"]]
        assert evidence.source_ref == status.id
        assert evidence.retrieved_at == status_time
        assert evidence.locator is None
    assert env.data["planned_outages"]
    for row in env.data["planned_outages"]:
        evidence = by_record[f"{row['system']}@{row['start_at'] or 'unscheduled'}"]
        assert evidence.source_ref == planned.id
        assert evidence.retrieved_at == outage_time
        assert evidence.locator is None

    cached = await discovery.facility_status(ctx)
    assert len(replay.calls) == 2
    assert [p.access_path for p in cached.provenance] == [AccessPath.cache] * 2
    assert [p.retrieved_at for p in cached.provenance] == [status_time, outage_time]


async def test_adapter_keeps_each_component_request_url(ctx):
    manifest = ctx.sources.get("nersc-status")
    params = ctx.facility_status.params_for(manifest)
    fetched = await ctx.facility_status.board(manifest)
    assert fetched.request_url == params.base_url.rstrip("/") + params.status_path
    assert fetched.value.planned_fetch is not None
    assert fetched.value.planned_fetch.request_url == (
        params.base_url.rstrip("/") + params.planned_path)
    assert fetched.value.planned == fetched.value.planned_fetch.value
