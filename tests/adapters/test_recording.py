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

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
from doe_mcp.adapters.eia_v2 import EiaV2Adapter
from doe_mcp.adapters.replay import (REDACTED_SECRET, RecordingFetcher,
                                     ReplayFetcher, record_through)
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


def test_record_through_reaches_every_adapter_with_a_fetcher_seam(tmp_path):
    ctx = _ctx(tmp_path, None)
    recorder = record_through(ctx, object())
    seams = [getattr(ctx, f.name) for f in dataclasses.fields(ctx)
             if hasattr(getattr(ctx, f.name), "_fetcher")]
    assert len(seams) >= 5
    assert all(a._fetcher is recorder for a in seams)   # noqa: SLF001
    assert ctx.eia in seams, "the keyed adapter is the one that matters"
