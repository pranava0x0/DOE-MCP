"""Exploration transport guards run against socket-free status responses."""
from __future__ import annotations

import httpx
import pytest

from doe_mcp.core.egress import EgressPolicy
from doe_mcp.core.errors import SourceUnavailable
from tools.record_fixtures import _ExplorationFetcher

HOST = "api.regulations.gov"


def _fetcher(handler):
    return _ExplorationFetcher(
        policy=EgressPolicy(allowed_hosts=frozenset({HOST}),
                            resolver=lambda host, _: [(0, 0, 0, "", ("93.184.216.34", 0))]),
        transport=httpx.MockTransport(handler))


async def test_exploration_missing_quota_header_keeps_success_and_hard_budget():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"transport_test": True})

    fetcher = _fetcher(handler)
    for _ in range(3):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    with pytest.raises(SourceUnavailable, match="budget is closed"):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    assert len(calls) == 3


@pytest.mark.parametrize("status", [301, 403, 429, 500])
async def test_exploration_never_retries_or_follows_a_refused_response(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": f"https://{HOST}/v4/other"})

    fetcher = _fetcher(handler)
    with pytest.raises(SourceUnavailable, match=f"HTTP {status}; no retry"):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    with pytest.raises(SourceUnavailable, match="budget is closed"):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    assert len(calls) == 1


async def test_exploration_stops_when_publisher_states_insufficient_quota():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"transport_test": True},
                              headers={"x-ratelimit-remaining": "1"})

    fetcher = _fetcher(handler)
    with pytest.raises(SourceUnavailable, match="insufficient stated quota"):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    with pytest.raises(SourceUnavailable, match="budget is closed"):
        await fetcher.fetch_json(f"https://{HOST}/v4/dockets", {})
    assert len(calls) == 1
