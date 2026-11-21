"""Catalog selection and per-publisher pagination under incomplete answers."""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (ExecutionCoverage, PaginationCoverage,
                                   RegistryCoverage, ResultCoverage)
from doe_mcp.core.errors import SourceNotActivated, SourceUnavailable
from doe_mcp.core.registry import DeclaredState
from doe_mcp.domains import discovery


def _block(ctx, source_id):
    ctx.sources.get(source_id).lifecycle.declared_state = DeclaredState.proposed


async def test_all_blocked_catalogs_refuse_without_a_fetch(ctx, replay):
    for source_id in discovery.FANOUT:
        _block(ctx, source_id)
    with pytest.raises(SourceNotActivated):
        await discovery.search_all_catalogs(ctx, "solar")
    assert replay.calls == []


async def test_a_blocked_requested_catalog_also_refuses(ctx, replay):
    source_id = discovery.FANOUT[0]
    _block(ctx, source_id)
    with pytest.raises(SourceNotActivated):
        await discovery.search_all_catalogs(ctx, "solar", catalogs=source_id)
    assert replay.calls == []


def _query_with(monkeypatch, outcomes):
    calls = []

    async def query(ctx, source_id, query, limit):
        calls.append(source_id)
        outcome = outcomes[source_id]
        if isinstance(outcome, Exception):
            raise outcome
        count, total = outcome
        rows = [{"id": f"{source_id}-{i}", "title": f"row {i}"}
                for i in range(count)]
        return rows, total, discovery.LIVE, "2026-11-21T10:00:00Z", 0, False

    monkeypatch.setattr(discovery, "_query_catalog", query)
    return calls


@pytest.mark.parametrize("count,total,expected", [
    (0, 0, PaginationCoverage.complete),
    (1, 1, PaginationCoverage.complete),
    (1, 2, PaginationCoverage.truncated),
    (0, 2, PaginationCoverage.truncated),
    (0, None, PaginationCoverage.unknown),
    (1, None, PaginationCoverage.unknown),
])
async def test_single_catalog_uses_its_total(ctx, monkeypatch, count, total,
                                           expected):
    source_id = discovery.FANOUT[0]
    _query_with(monkeypatch, {source_id: (count, total)})
    env = await discovery.search_all_catalogs(ctx, "solar", catalogs=source_id)
    assert env.coverage.pagination is expected
    assert env.data["per_catalog"][0]["total_matches"] == total
    assert env.coverage.execution is ExecutionCoverage.complete
    assert env.coverage.registry is RegistryCoverage.covered
    if expected is not PaginationCoverage.complete:
        assert "holds a match" not in env.data["note"]


@pytest.mark.parametrize("left,right,expected", [
    ((0, 0), (1, 1), PaginationCoverage.complete),
    ((0, 0), (0, None), PaginationCoverage.unknown),
    ((0, None), (0, None), PaginationCoverage.unknown),
    ((1, 2), (0, None), PaginationCoverage.truncated),
    ((0, None), (1, 2), PaginationCoverage.truncated),
])
async def test_mixed_catalog_pagination_retains_each_dimension(
        ctx, monkeypatch, left, right, expected):
    sources = discovery.FANOUT[:2]
    _query_with(monkeypatch, dict(zip(sources, (left, right), strict=True)))
    env = await discovery.search_all_catalogs(ctx, "solar",
                                               catalogs=",".join(sources))
    assert env.coverage.pagination is expected


@pytest.mark.parametrize("outcome,expected", [
    ((0, 0), PaginationCoverage.unknown),
    ((1, 1), PaginationCoverage.unknown),
    ((1, 2), PaginationCoverage.truncated),
    ((0, None), PaginationCoverage.unknown),
])
async def test_blocked_catalogs_make_selection_and_execution_partial(
        ctx, monkeypatch, outcome, expected):
    blocked, live = discovery.FANOUT[:2]
    _block(ctx, blocked)
    calls = _query_with(monkeypatch, {live: outcome})
    env = await discovery.search_all_catalogs(ctx, "solar",
                                               catalogs=f"{blocked},{live}")
    assert calls == [live]
    assert env.coverage.registry is RegistryCoverage.partial
    assert env.coverage.execution is ExecutionCoverage.partial
    assert env.coverage.pagination is expected
    assert env.coverage.sources_searched == [live]
    assert [(g.source_id, g.reason) for g in env.coverage.sources_unavailable] == [
        (blocked, "source_not_activated")]
    assert "holds a match" not in env.data["note"]


@pytest.mark.parametrize("successful,expected", [
    ((0, 0), PaginationCoverage.unknown),
    ((1, 1), PaginationCoverage.unknown),
    ((1, 2), PaginationCoverage.truncated),
    ((0, None), PaginationCoverage.unknown),
])
async def test_a_failed_catalog_does_not_hide_a_successful_empty_search(
        ctx, monkeypatch, successful, expected):
    live, failed = discovery.FANOUT[:2]
    _query_with(monkeypatch, {live: successful,
                              failed: SourceUnavailable("publisher unavailable")})
    env = await discovery.search_all_catalogs(ctx, "solar",
                                               catalogs=f"{live},{failed}")
    assert env.coverage.execution is ExecutionCoverage.partial
    assert env.coverage.registry is RegistryCoverage.covered
    assert env.coverage.pagination is expected
    assert [s.source_id for s in env.coverage.source_failures] == [failed]
    assert "holds a match" not in env.data["note"]


async def test_all_catalog_requests_failed_cannot_claim_complete_pagination(
        ctx, monkeypatch):
    sources = discovery.FANOUT[:2]
    _query_with(monkeypatch, {sid: SourceUnavailable("publisher unavailable")
                              for sid in sources})
    env = await discovery.search_all_catalogs(ctx, "solar",
                                               catalogs=",".join(sources))
    assert env.coverage.execution is ExecutionCoverage.failed
    assert env.coverage.pagination is PaginationCoverage.unknown
    assert env.provenance == []


async def test_only_a_completed_empty_search_gets_the_no_match_note(
        ctx, monkeypatch):
    source_id = discovery.FANOUT[0]
    _query_with(monkeypatch, {source_id: (0, 0)})
    env = await discovery.search_all_catalogs(ctx, "solar", catalogs=source_id)
    assert env.coverage.result is ResultCoverage.empty
    assert "None of the 1 catalogs searched holds a match" in env.data["note"]
