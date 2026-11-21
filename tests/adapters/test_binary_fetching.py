"""Binary content keeps the existing GET boundary and immutable recording bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from doe_mcp.adapters.base import BytesResponse, HttpFetcher
from doe_mcp.adapters.replay import RecordingFetcher, ReplayFetcher
from doe_mcp.core.egress import EgressPolicy, MAX_RESPONSE_BYTES
from doe_mcp.core.errors import SourceUnavailable
from doe_mcp.replay import fixture_manifest, load_replay_context

ROOT = Path(__file__).resolve().parents[2]


async def test_binary_get_reuses_http_transport_and_status_validation():
    methods = []
    def respond(request):
        methods.append(request.method)
        return httpx.Response(200, content=b'PAR1-original-bytes', headers={'content-type': 'application/octet-stream'})
    policy = EgressPolicy(frozenset({'publisher.test'}), resolver=lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    result = await HttpFetcher(policy, transport=httpx.MockTransport(respond)).fetch_bytes('https://publisher.test/a', {})
    assert result.content == b'PAR1-original-bytes' and methods == ['GET']


async def test_binary_get_keeps_response_cap(monkeypatch):
    from doe_mcp.adapters import base
    monkeypatch.setattr(base, 'MAX_RESPONSE_BYTES', 8)
    policy = EgressPolicy(frozenset({'publisher.test'}), resolver=lambda *args, **kwargs: [(2, 1, 6, '', ('93.184.216.34', 443))])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b'x' * 9))
    with pytest.raises(SourceUnavailable, match='egress cap'):
        await HttpFetcher(policy, transport=transport).fetch_bytes('https://publisher.test/a', {})


class BinarySource:
    extra_headers = None
    calls = 0
    async def fetch_bytes(self, url, params):
        self.calls += 1
        return BytesResponse(b'PAR1-unit-test-byte-contract', {}, url)


async def test_recorder_publishes_content_addressed_bytes_and_replay_detects_tampering(tmp_path):
    source = BinarySource()
    recorder = RecordingFetcher(source)
    await recorder.fetch_bytes('https://publisher.test/a', {})
    path = tmp_path / 'recording.json'
    recorder.write(path, 'Unit test of the binary contract, not a publisher fixture.')
    metadata = json.loads(path.read_bytes())['interactions'][0]['body_ref']
    content = (tmp_path / metadata['file']).read_bytes()
    assert hashlib.sha256(content).hexdigest() == metadata['sha256']
    assert (await ReplayFetcher.from_file(path).fetch_bytes('https://publisher.test/a', {})).content == content
    (tmp_path / metadata['file']).write_bytes(content + b'changed')
    with pytest.raises(SourceUnavailable, match='SHA256'):
        await ReplayFetcher.from_file(path).fetch_bytes('https://publisher.test/a', {})


@pytest.mark.parametrize('location', ['store', 'query', 'url', 'header'])
async def test_binary_recording_refuses_credentials_before_fetch(location):
    source = BinarySource()
    url, params = 'https://publisher.test/a', {}
    recorder = RecordingFetcher(source, secrets=frozenset({'sentinel'}) if location == 'store' else frozenset())
    if location == 'query':
        params['api_key'] = 'sentinel'
    if location == 'url':
        url += '?api_key=sentinel'
    if location == 'header':
        source.extra_headers = {'X-Api-Key': 'sentinel'}
    with pytest.raises(ValueError, match='credential-free'):
        await recorder.fetch_bytes(url, params)
    assert source.calls == 0 and recorder.interactions == []


def test_loading_context_and_fixture_manifest_never_reads_binary_assets(monkeypatch):
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path.suffix != '.bin', 'context construction eagerly read binary fixture'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    ctx = load_replay_context(ROOT / 'tests/fixtures', ROOT / 'sources')
    assert ctx.atb is not None and ctx.credentials.values == {}
    metadata = next(row for row in fixture_manifest(ROOT / 'tests/fixtures') if row['file'] == 'nlr-atb.json')
    assert len(metadata['binary_assets']) == 2
    assert all(0 < row['bytes'] < MAX_RESPONSE_BYTES for row in metadata['binary_assets'])


async def test_binary_fixture_symlink_is_refused(tmp_path):
    recorder = RecordingFetcher(BinarySource())
    await recorder.fetch_bytes('https://publisher.test/a', {})
    path = tmp_path / 'recording.json'
    recorder.write(path, 'Binary contract test.')
    doc = json.loads(path.read_bytes())
    blob = tmp_path / doc['interactions'][0]['body_ref']['file']
    original = blob.with_suffix('.original')
    blob.rename(original)
    blob.symlink_to(original)
    with pytest.raises(SourceUnavailable, match='symlink'):
        await ReplayFetcher.from_file(path).fetch_bytes('https://publisher.test/a', {})
