"""Recording never writes a credential, and what it writes replays under any
key.

The sabotage here is a sentinel key configured on a context whose EIA adapter
is routed through the recorder. EIA is the adapter whose requests carry a
credential, and it was missing from both recorder lists before the factory
replaced them; adding it without redaction would have written the key into a
public fixture.
"""
from __future__ import annotations

import dataclasses
import json
from urllib.parse import quote

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
from doe_mcp.adapters.eia_v2 import EiaV2Adapter
from doe_mcp.adapters.replay import (REDACTED_SECRET, RecordingFetcher,
                                     ReplayFetcher, record_through, redact_secrets,
                                     is_credential_param)
from doe_mcp.core.credentials import Credentials
from doe_mcp.runtime import load_context
from tests.conftest import SOURCES

SENTINEL = "sentinel-eia-key-7f3a9c2e"


class EchoingEia:
    """Stands in for EIA, which — like the real v2 API — echoes the request
    back inside the response body."""

    async def fetch_json(self, url, params):
        assert params.get("api_key") == SENTINEL, (
            "the live request must carry the real key; redaction is about "
            "what is written, not what is sent")
        return JsonResponse(
            payload={"response": {
                "routes": [{"id": "electricity", "name": "Electricity"}],
                "request": {"command": "/v2/", "params": dict(params)}}},
            headers={"content-type": "application/json"},
            url=f"{url}?api_key={SENTINEL}")


def _ctx(tmp_path, key: str | None):
    creds = Credentials(values={"EIA_API_KEY": key} if key else {},
                        path=tmp_path / "credentials.env", file_exists=False)
    return load_context(SOURCES,
                        eia=EiaV2Adapter(cache=TTLCache(), credentials=creds),
                        credentials=creds)


async def test_recording_the_eia_adapter_never_writes_the_key(tmp_path):
    ctx = _ctx(tmp_path, SENTINEL)
    recorder = record_through(ctx, EchoingEia())
    manifest = ctx.sources.get("eia-api-v2")

    fetched = await ctx.eia.describe_route(manifest)
    assert fetched.value.children, "the live call itself must work"

    out = tmp_path / "eia-api-v2.json"
    recorder.write(out, note="sabotage test")
    text = out.read_text()
    assert SENTINEL not in text
    # The parameter is recorded as present and redacted, not dropped, so a
    # reader can see the request carried a key.
    assert f'"api_key": "{REDACTED_SECRET}"' in text


async def test_the_redacted_fixture_replays_under_a_different_key(tmp_path):
    ctx = _ctx(tmp_path, SENTINEL)
    recorder = record_through(ctx, EchoingEia())
    manifest = ctx.sources.get("eia-api-v2")
    await ctx.eia.describe_route(manifest)
    out = tmp_path / "eia-api-v2.json"
    recorder.write(out, note="sabotage test")

    other = Credentials(values={"EIA_API_KEY": "someone-elses-key"},
                        path=tmp_path / "other.env", file_exists=False)
    replaying = EiaV2Adapter(fetcher=ReplayFetcher.from_file(out),
                             cache=TTLCache(), credentials=other)
    node = (await replaying.describe_route(manifest)).value
    assert [c["id"] for c in node.children] == ["electricity"]


def test_write_refuses_when_a_secret_survived_redaction(tmp_path):
    recorder = RecordingFetcher(inner=None, secrets=frozenset({SENTINEL}))
    recorder.interactions.append({"url": "u", "params": {}, "headers": {},
                                  "body": {"echo": SENTINEL}})
    out = tmp_path / "leak.json"
    with pytest.raises(ValueError, match="survived redaction"):
        recorder.write(out, note="t")
    assert not out.exists()


def test_write_refuses_an_unknown_named_credential_that_survived_redaction(tmp_path):
    recorder = RecordingFetcher(inner=None)
    recorder.interactions.append({"url": "u", "params": {}, "headers": {},
                                  "body": {"request": {"api_key": "unknown-key"}}})
    out = tmp_path / "leak.json"
    with pytest.raises(ValueError, match="named credential field survived"):
        recorder.write(out, note="t")
    assert not out.exists()


def test_record_through_reaches_every_adapter_with_a_fetcher_seam(tmp_path):
    ctx = _ctx(tmp_path, None)
    recorder = record_through(ctx, object())
    seams = [getattr(ctx, f.name) for f in dataclasses.fields(ctx)
             if hasattr(getattr(ctx, f.name), "_fetcher")]
    assert len(seams) >= 5
    assert all(a._fetcher is recorder for a in seams)   # noqa: SLF001
    assert ctx.eia in seams, "the keyed adapter is the one that matters"


async def test_recording_redacts_a_different_key_echoed_by_the_publisher(tmp_path):
    other_key = "different-cached-key-5a43cf61"

    class CachedEia:
        async def fetch_json(self, url, params):
            return JsonResponse(
                payload={"response": {"routes": [], "request": {
                    "params": {"api_key": other_key}}},
                    "message": f"cached request used {other_key}"},
                headers={}, url=url)

    recorder = RecordingFetcher(inner=CachedEia(), secrets=frozenset({SENTINEL}))
    response = await recorder.fetch_json("https://api.eia.gov/v2", {"api_key": SENTINEL})
    assert response.payload["response"]["request"]["params"]["api_key"] == other_key
    out = tmp_path / "recorded.json"
    recorder.write(out, note="cached request test")
    assert other_key not in out.read_text()
    assert SENTINEL not in out.read_text()


def test_structural_redaction_works_without_configured_credentials():
    other_key = "cached-key-with+/encoding"
    payload = {"records": [{"API_KEY": other_key}],
               "url": "https://api.eia.gov/v2?api_key=" + quote(other_key, safe=""),
               "key": "a-scientific-join-key"}
    redacted = redact_secrets(payload, frozenset())
    assert other_key not in json.dumps(redacted)
    assert quote(other_key, safe="") not in json.dumps(redacted)
    assert redacted["key"] == "a-scientific-join-key"
    assert payload["records"][0]["API_KEY"] == other_key


def test_recorded_fixtures_have_no_unredacted_named_credentials():
    from pathlib import Path

    def check(node, field):
        if isinstance(node, dict):
            for name, value in node.items():
                if is_credential_param(name) and isinstance(value, str):
                    if value not in {REDACTED_SECRET, "DEMO_KEY", ""}:
                        pytest.fail(f"unredacted credential field: {field}.{name}")
                check(value, f"{field}.{name}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                check(value, f"{field}[{index}]")

    fixtures = Path(__file__).parent.parent / "fixtures"
    for path in fixtures.glob("*.json"):
        check(json.loads(path.read_text()), path.name)
