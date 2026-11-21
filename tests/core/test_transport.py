"""Real HTTP framing and pooling over socket-free scripted byte streams."""
from __future__ import annotations

import asyncio
import ipaddress
import ssl
from types import SimpleNamespace

import httpcore
import httpx
import pytest

import doe_mcp.adapters.base as base
import doe_mcp.core.transport as transport
from doe_mcp.adapters.base import HttpFetcher
from doe_mcp.core.egress import EgressPolicy, ResolvedTarget
from doe_mcp.core.errors import EgressRefused, SourceUnavailable
from doe_mcp.core.transport import PinnedHTTPTransport, pinned_target

A = "one.publisher.example.invalid"
B = "two.publisher.example.invalid"
PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def answers(*addresses):
    return [(0, 0, 0, "", (address, 0)) for address in addresses]


def response(body=b"{}", status=200, headers=b""):
    return (f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n".encode()
            + b"Content-Type: application/json\r\n" + headers + b"\r\n" + body)


class ScriptedStream(httpcore.AsyncNetworkStream):
    def __init__(self, backend):
        self.backend = backend
        self.pending = b""
        self.outgoing = b""
        self.closed = False
        self.requests = []

    async def write(self, buffer, timeout=None):
        self.outgoing += buffer
        if b"\r\n\r\n" in self.outgoing:
            self.backend.requests.append(self.outgoing)
            self.requests.append(self.outgoing)
            self.outgoing = b""
            self.pending += (self.backend.responses.pop(0)
                             if self.backend.responses else response())

    async def read(self, max_bytes, timeout=None):
        chunk, self.pending = self.pending[:max_bytes], self.pending[max_bytes:]
        return chunk

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.backend.tls.append((server_hostname, ssl_context.check_hostname,
                                 ssl_context.verify_mode, timeout))
        if self.backend.bad_certificate:
            raise httpcore.ConnectError("certificate hostname mismatch")
        return self

    async def aclose(self):
        self.closed = True

    def get_extra_info(self, info):
        return False if info == "is_readable" else None


class ScriptedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, responses=(), fail_addresses=(), bad_certificate=False):
        self.responses = list(responses)
        self.fail_addresses = fail_addresses
        self.bad_certificate = bad_certificate
        self.connects = []
        self.requests = []
        self.tls = []
        self.streams = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        # A hostname would need another resolver lookup. A numeric destination
        # is the decisive boundary: the changing resolver is never consulted.
        assert ipaddress.ip_address(host).is_global
        self.connects.append((host, port, timeout))
        if host in self.fail_addresses:
            raise httpcore.ConnectError("scripted connection failure")
        stream = ScriptedStream(self)
        stream.address = host
        self.streams.append(stream)
        return stream


def client_for(monkeypatch, backend):
    client = httpx.AsyncClient(transport=PinnedHTTPTransport(backend),
                               trust_env=False, follow_redirects=False, timeout=30)
    monkeypatch.setattr(base, "shared_client", lambda: client)
    return client


def policy(resolver, hosts=(A,), insecure=False):
    return EgressPolicy(frozenset(hosts), resolver=resolver,
                         insecure_transport=insecure)


async def test_a_dns_change_after_validation_cannot_change_the_socket_destination(monkeypatch):
    calls = []

    def changing_dns(host, _):
        calls.append(host)
        return answers(PUBLIC if len(calls) == 1 else "127.0.0.1")

    backend = ScriptedBackend()
    async with client_for(monkeypatch, backend):
        result = await HttpFetcher(policy(changing_dns)).fetch_json(f"https://{A}/data", {})
    assert result.payload == {}
    assert calls == [A]
    assert [item[:2] for item in backend.connects] == [(PUBLIC, 443)]
    assert result.url == f"https://{A}/data"
    assert backend.streams[0].closed


async def test_pinned_connections_keep_the_tls_hostname_host_header_and_timeout(monkeypatch):
    backend = ScriptedBackend()
    async with client_for(monkeypatch, backend):
        await HttpFetcher(policy(lambda *_: answers(PUBLIC))).fetch_json(f"https://{A}/data", {})
    assert backend.requests[0].startswith(b"GET /data HTTP/1.1\r\n")
    assert f"Host: {A}\r\n".encode() in backend.requests[0]
    assert backend.tls == [(A, True, ssl.CERT_REQUIRED, 30)]
    assert 0 < backend.connects[0][2] <= 30


async def test_same_origin_reuses_a_socket_but_shared_ip_origins_do_not(monkeypatch):
    backend = ScriptedBackend()
    fetch = HttpFetcher(policy(lambda *_: answers(PUBLIC), hosts=(A, B)))
    async with client_for(monkeypatch, backend):
        for host in (A, B, A):
            await fetch.fetch_json(f"https://{host}/data", {})
    assert len(backend.connects) == 2
    assert [item[0] for item in backend.tls] == [A, B]
    assert len(backend.requests) == 3
    for request, host in zip(backend.requests, (A, B, A), strict=True):
        assert f"Host: {host}\r\n".encode() in request


async def test_concurrent_origins_keep_their_own_dns_snapshots(monkeypatch):
    class InterleavingBackend(ScriptedBackend):
        async def connect_tcp(self, *args, **kwargs):
            await asyncio.sleep(0)
            return await super().connect_tcp(*args, **kwargs)

    backend = InterleavingBackend()
    ips = {A: PUBLIC, B: PUBLIC_V6}
    fetch = HttpFetcher(policy(lambda host, _: answers(ips[host]), hosts=(A, B)))
    async with client_for(monkeypatch, backend):
        await asyncio.gather(*(fetch.fetch_json(f"https://{host}/data", {}) for host in (A, B)))
    assert {item[0] for item in backend.connects} == {PUBLIC, PUBLIC_V6}
    for stream in backend.streams:
        expected = A if stream.address == PUBLIC else B
        assert f"Host: {expected}\r\n".encode() in stream.requests[0]


async def test_host_and_sni_overrides_cannot_change_the_validated_publisher():
    backend = ScriptedBackend()
    target = ResolvedTarget("https", A, 443, (PUBLIC,))
    async with httpx.AsyncClient(transport=PinnedHTTPTransport(backend)) as client:
        with pinned_target(target):
            await client.get(f"https://{A}/data", headers={"Host": B},
                             extensions={"sni_hostname": B})
    assert f"Host: {A}\r\n".encode() in backend.requests[0]
    assert backend.tls[0][0] == A


async def test_fallback_uses_only_the_original_validated_address_set(monkeypatch):
    calls = []

    def changing_dns(host, _):
        calls.append(host)
        return answers(PUBLIC_V6, PUBLIC) if len(calls) == 1 else answers("10.0.0.1")

    backend = ScriptedBackend(fail_addresses={PUBLIC_V6})
    async with client_for(monkeypatch, backend):
        await HttpFetcher(policy(changing_dns)).fetch_json(f"https://{A}/data", {})
    assert [item[0] for item in backend.connects] == [PUBLIC_V6, PUBLIC]
    assert 0 < backend.connects[0][2] <= 15
    assert 0 < backend.connects[1][2] <= 30
    assert calls == [A]


@pytest.mark.parametrize("last_succeeds", [True, False])
async def test_a_full_address_timeout_leaves_a_budget_for_the_next_address(
        monkeypatch, last_succeeds):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(base, "RETRY_BUDGET", 0)

    class BlackholedBackend(ScriptedBackend):
        async def connect_tcp(self, host, port, timeout=None, **kwargs):
            if host == PUBLIC_V6 or not last_succeeds:
                self.connects.append((host, port, timeout))
                clock.now += timeout
                raise httpcore.ConnectTimeout("scripted full address timeout")
            return await super().connect_tcp(host, port, timeout=timeout, **kwargs)

    backend = BlackholedBackend()
    fetch = HttpFetcher(policy(lambda *_: answers(PUBLIC_V6, PUBLIC)))
    async with client_for(monkeypatch, backend):
        if last_succeeds:
            result = await fetch.fetch_json(f"https://{A}/data", {})
            assert result.payload == {}
        else:
            with pytest.raises(SourceUnavailable, match="ConnectTimeout"):
                await fetch.fetch_json(f"https://{A}/data", {})
    assert backend.connects == [(PUBLIC_V6, 443, 15.0), (PUBLIC, 443, 15.0)]
    assert clock.now == (115.0 if last_succeeds else 130.0)


async def test_redirects_get_their_own_validated_public_destinations(monkeypatch):
    backend = ScriptedBackend(responses=[response(status=302,
        headers=f"Location: https://{B}/landed?publisher=1\r\n".encode()), response()])
    ips = {A: PUBLIC, B: PUBLIC_V6}
    fetch = HttpFetcher(policy(lambda host, _: answers(ips[host]), hosts=(A, B)))
    async with client_for(monkeypatch, backend):
        result = await fetch.fetch_json(f"https://{A}/start", {})
    assert [item[:2] for item in backend.connects] == [(PUBLIC, 443), (PUBLIC_V6, 443)]
    assert [item[0] for item in backend.tls] == [A, B]
    assert result.url == f"https://{B}/landed?publisher=1"


async def test_redirect_rebinding_to_a_private_address_never_opens_a_socket(monkeypatch):
    calls = []

    def changing_dns(host, _):
        calls.append(host)
        return answers(PUBLIC if len(calls) == 1 else "169.254.169.254")

    backend = ScriptedBackend(responses=[response(status=302, headers=b"Location: /next\r\n")])
    async with client_for(monkeypatch, backend):
        with pytest.raises(EgressRefused, match="link-local"):
            await HttpFetcher(policy(changing_dns)).fetch_json(f"https://{A}/start", {})
    assert len(backend.connects) == 1


async def test_a_mixed_public_and_private_dns_answer_opens_no_socket(monkeypatch):
    backend = ScriptedBackend()
    async with client_for(monkeypatch, backend):
        with pytest.raises(EgressRefused, match="private range"):
            await HttpFetcher(policy(lambda *_: answers(PUBLIC, "10.0.0.1"))).fetch_json(
                f"https://{A}/data", {})
    assert not backend.connects


async def test_requests_without_a_matching_pin_fail_before_connecting():
    backend = ScriptedBackend()
    async with httpx.AsyncClient(transport=PinnedHTTPTransport(backend)) as client:
        with pytest.raises(EgressRefused, match="no matching"):
            await client.get(f"https://{A}/data")
        with pinned_target(ResolvedTarget("https", A, 443, (PUBLIC,))):
            with pytest.raises(EgressRefused, match="no matching"):
                await client.get(f"https://{B}/data")
            with pytest.raises(EgressRefused, match="GET only"):
                await client.post(f"https://{A}/data")
    assert not backend.connects


@pytest.mark.parametrize("address", ["127.0.0.1", "100.64.0.1", "::ffff:100.64.0.1", A])
def test_invalid_snapshot_addresses_are_refused(address):
    with pytest.raises(EgressRefused):
        ResolvedTarget("https", A, 443, (address,))


async def test_tls_verification_errors_follow_the_existing_typed_failure_path(monkeypatch):
    monkeypatch.setattr(base, "RETRY_BUDGET", 0)
    backend = ScriptedBackend(bad_certificate=True)
    async with client_for(monkeypatch, backend):
        with pytest.raises(SourceUnavailable, match="ConnectError"):
            await HttpFetcher(policy(lambda *_: answers(PUBLIC))).fetch_json(f"https://{A}/data", {})


async def test_pinned_responses_still_use_the_streaming_byte_cap(monkeypatch):
    monkeypatch.setattr(base, "MAX_RESPONSE_BYTES", 100)
    backend = ScriptedBackend(responses=[response(body=b"x" * 500)])
    async with client_for(monkeypatch, backend):
        with pytest.raises(SourceUnavailable, match="egress cap"):
            await HttpFetcher(policy(lambda *_: answers(PUBLIC))).fetch_json(f"https://{A}/data", {})


async def test_declared_http_keeps_its_origin_and_skips_tls(monkeypatch):
    backend = ScriptedBackend()
    async with client_for(monkeypatch, backend):
        await HttpFetcher(policy(lambda *_: answers(PUBLIC), insecure=True)).fetch_json(
            f"http://{A}/data", {})
    assert backend.connects[0][:2] == (PUBLIC, 80)
    assert not backend.tls
