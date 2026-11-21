"""Refusal and request-shape tests; positive contract replay awaits capture."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from doe_mcp.adapters import regulations_gov as adapter_module
from doe_mcp.adapters.base import HttpFetcher, TTLCache
from doe_mcp.core.credentials import Credentials
from doe_mcp.core.egress import EgressPolicy
from doe_mcp.core.errors import (CredentialMissing, InvalidQuery, SourceNotActivated,
                                 SourceSchemaChanged, SourceUnavailable)
from doe_mcp.core.registry import DeclaredState
from doe_mcp.domains import docs

DOCKET = "EERE-2025-OT-0031"


@pytest.mark.parametrize("tool,kwargs", [
    (docs.get_docket, {"docket_id": DOCKET}),
    (docs.search_docket_documents, {"docket_id": DOCKET}),
    (docs.get_docket_document, {"document_id": "untrusted-test-input"}),
])
async def test_each_docket_tool_checks_activation_before_any_fetch(ctx, replay, tool, kwargs):
    ctx.sources.get("regulations-gov-doe").lifecycle.declared_state = DeclaredState.proposed
    with pytest.raises(SourceNotActivated):
        await tool(ctx, **kwargs)
    assert replay.calls == []


@pytest.mark.parametrize("key", [None, "DEMO_KEY"])
async def test_docket_credentials_are_required_before_even_a_cache_lookup(ctx, replay, key):
    manifest = ctx.sources.get("regulations-gov-doe")
    creds = Credentials({"API_DATA_GOV_KEY": key} if key else {}, Path("/nonexistent"), False)
    cache = TTLCache()
    params = ctx.regulations_gov.params_for(manifest)
    cache.put(manifest.id, params.base_url + "/dockets/" + DOCKET, {}, {}, {})
    adapter = adapter_module.RegulationsGovAdapter(fetcher=replay, cache=cache, credentials=creds)
    with pytest.raises(CredentialMissing):
        await adapter.get(manifest, DOCKET, kind="dockets")
    assert replay.calls == []


@pytest.mark.parametrize("kwargs", [
    {"rows": 1}, {"rows": 21}, {"rows": True}, {"rows": 5.5},
    {"page": 0}, {"page": 21}, {"page": True}, {"page": 1.5},
    {"agency_id": "EPA"}, {"docket_id": ""}, {"docket_id": "EPA-2000-0001"},
    {"docket_id": "EERE-2025-OT-0031/../other"},
    {"since": "2026-13-01"}, {"since": "20260101"},
    {"since": "2026-11-21", "until": "2026-11-20"},
])
async def test_invalid_search_is_refused_before_fetch(keyed_ctx, replay, kwargs):
    manifest = keyed_ctx.sources.get("regulations-gov-doe")
    with pytest.raises(InvalidQuery):
        await keyed_ctx.regulations_gov.search_documents(
            manifest, **({"docket_id": DOCKET} | kwargs))
    assert replay.calls == []


async def test_search_sends_exact_supported_filters(keyed_ctx):
    seen = []

    class Observe:
        async def fetch_json(self, url, params):
            seen.append((url, params))
            raise SourceUnavailable("request observed; no response fabricated")

    keyed_ctx.regulations_gov._fetcher = Observe()
    manifest = keyed_ctx.sources.get("regulations-gov-doe")
    with pytest.raises(SourceUnavailable, match="observed"):
        await keyed_ctx.regulations_gov.search_documents(
            manifest, docket_id=DOCKET, query="equipment", since="2026-01-01",
            until="2026-11-21", rows=10, page=2)
    params = keyed_ctx.regulations_gov.params_for(manifest)
    assert seen == [(params.base_url + "/documents", {
        "filter[agencyId]": "EERE", "filter[docketId]": DOCKET,
        "filter[searchTerm]": "equipment", "filter[postedDate][ge]": "2026-01-01",
        "filter[postedDate][le]": "2026-11-21", "page[size]": 10,
        "page[number]": 2, "sort": "-lastModifiedDate,documentId"})]


async def test_live_key_uses_header_and_is_redacted_from_publisher_errors(keyed_ctx, monkeypatch):
    secret = keyed_ctx.credentials.require("API_DATA_GOV_KEY")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["x-api-key"] == secret
        assert "api_key" not in request.url.params
        return httpx.Response(403, json={"error": {"message": f"key refused: {secret}"}})

    def fetcher(*, policy, extra_headers):
        return HttpFetcher(policy=EgressPolicy(
            allowed_hosts=policy.allowed_hosts,
            resolver=lambda host, _: [(0, 0, 0, "", ("93.184.216.34", 0))]),
            extra_headers=extra_headers, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(adapter_module, "HttpFetcher", fetcher)
    keyed_ctx.regulations_gov._fetcher = None
    manifest = keyed_ctx.sources.get("regulations-gov-doe")
    with pytest.raises(SourceUnavailable) as exc:
        await keyed_ctx.regulations_gov.get(manifest, DOCKET, kind="dockets")
    assert secret not in str(exc.value)
    assert len(requests) == 1


@pytest.mark.parametrize("item", [None, [], {}, {"type": "dockets", "id": "x"}])
def test_malformed_metadata_is_a_schema_change(item):
    with pytest.raises(SourceSchemaChanged):
        adapter_module._record(item, "dockets", ["EERE"])
