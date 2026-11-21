"""The real fetch path against canned transports.

`HttpFetcher.transport` takes an `httpx.MockTransport`, so status handling,
the capped read, redirect revalidation, and 403 classification run end to end
with no network. The classifier alone was tested before this file existed,
and the fetch path never handed it a body: only 200 responses were read, so
every 403 arrived as `b''` and was reported as a WAF block.
"""
from __future__ import annotations

import httpx
import pytest

import doe_mcp.adapters.base as base
from doe_mcp.adapters.base import HttpFetcher
from doe_mcp.core.egress import EgressPolicy
from doe_mcp.core.errors import DoeMcpError, RateLimited, SourceUnavailable

HOST = "api.eia.gov"


def fetcher(handler) -> HttpFetcher:
    policy = EgressPolicy(
        allowed_hosts=frozenset({HOST}),
        resolver=lambda host, _: [(0, 0, 0, "", ("93.184.216.34", 0))])
    return HttpFetcher(policy=policy, transport=httpx.MockTransport(handler))


async def test_an_api_403_body_reaches_the_classifier_through_the_fetch_path():
    def handler(request):
        return httpx.Response(403, json={"error": {
            "code": "API_KEY_MISSING", "message": "No api_key was supplied."}})

    with pytest.raises(SourceUnavailable) as err:
        await fetcher(handler).fetch_json(f"https://{HOST}/v2", {})
    assert "API_KEY_MISSING" in str(err.value)
    assert "blocked_probe" not in str(err.value)


async def test_a_waf_403_is_reported_as_a_blocked_probe():
    def handler(request):
        return httpx.Response(403, text="<html>Access Denied</html>")

    with pytest.raises(SourceUnavailable) as err:
        await fetcher(handler).fetch_json(f"https://{HOST}/v2", {})
    assert "blocked_probe" in str(err.value)


async def test_an_oversized_body_is_refused_during_the_read(monkeypatch):
    monkeypatch.setattr(base, "MAX_RESPONSE_BYTES", 1_000)

    def handler(request):
        return httpx.Response(200, content=b"x" * 5_000,
                              headers={"content-type": "application/json"})

    with pytest.raises(SourceUnavailable, match="egress cap"):
        await fetcher(handler).fetch_json(f"https://{HOST}/v2", {})


async def test_requests_carry_the_honest_user_agent_and_ask_for_json():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    result = await fetcher(handler).fetch_json(f"https://{HOST}/v2",
                                               {"a": "1"})
    assert result.payload == {"ok": True}
    assert seen["user-agent"].startswith("DOE-MCP/")
    assert "not affiliated" in seen["user-agent"]
    assert seen["accept"] == "application/json"


async def test_a_same_host_redirect_is_followed_and_the_body_read():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302,
                                  headers={"location": f"https://{HOST}/b"})
        return httpx.Response(200, json={"landed": request.url.path})

    result = await fetcher(handler).fetch_json(f"https://{HOST}/a", {})
    assert result.payload == {"landed": "/b"}


async def test_a_429_becomes_rate_limited_with_the_retry_after(monkeypatch):
    monkeypatch.setattr(base, "RETRY_BUDGET", 0)

    def handler(request):
        return httpx.Response(429, headers={"retry-after": "7"})

    with pytest.raises(RateLimited) as err:
        await fetcher(handler).fetch_json(f"https://{HOST}/v2", {})
    assert err.value.retry_after_seconds == 7


async def test_a_text_fetch_asks_for_text_and_returns_the_body():
    """The Accept header used to be hardcoded to JSON for every request. A
    static file server will usually ignore it, but asking for what you want
    is the difference between a response you requested and one the server
    guessed at."""
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text="a\tb\r\n1\t2\r\n",
                              headers={"content-type": "text/plain",
                                       "last-modified": "Wed, 02 Sep 2026 "
                                                        "16:41:22 GMT"})

    result = await fetcher(handler).fetch_text(f"https://{HOST}/feed.txt", {})
    assert result.text == "a\tb\r\n1\t2\r\n"
    assert seen["accept"].startswith("text/plain")
    assert seen["user-agent"].startswith("DOE-MCP/")
    assert result.headers["last-modified"].endswith("GMT")


async def test_a_text_403_is_classified_like_any_other():
    """The text path shares the status handling rather than reimplementing
    it, so a WAF page arriving as text/plain is still a blocked probe and not
    an empty feed."""
    def handler(request):
        return httpx.Response(403, text="<html>Access Denied</html>")

    with pytest.raises(SourceUnavailable) as err:
        await fetcher(handler).fetch_text(f"https://{HOST}/feed.txt", {})
    assert "blocked_probe" in str(err.value)


async def test_bytes_that_are_not_the_declared_encoding_are_refused():
    """A lenient decode would turn a changed encoding into mojibake in the
    middle of a data table, which reads as bad data rather than a bad read."""
    def handler(request):
        return httpx.Response(
            200, content=b"Date/Time\tLoad\r\n\xff\xfe\x00bad\r\n",
            headers={"content-type": "text/plain; charset=utf-8"})

    with pytest.raises(SourceUnavailable, match="not valid utf-8"):
        await fetcher(handler).fetch_text(f"https://{HOST}/feed.txt", {})


async def test_the_shared_client_is_one_per_loop():
    first = base.shared_client()
    second = base.shared_client()
    assert first is second
    await base.close_shared_client()
    assert first.is_closed


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
@pytest.mark.parametrize("operation", ["fetch_json", "fetch_text"])
async def test_publisher_error_echoes_do_not_disclose_the_request_key(status, operation):
    secret = "do-not-print-this-api-key"

    def handler(request):
        return httpx.Response(status, json={"error": {"code": "REFUSED",
                              "message": f"Invalid api_key: {secret}"}})

    with pytest.raises(DoeMcpError) as err:
        await getattr(fetcher(handler), operation)(f"https://{HOST}/v2", {"api_key": secret})
    assert secret not in str(err.value)
    assert "<redacted>" in str(err.value)


async def test_request_logs_omit_keys_and_private_query_text(caplog):
    secret = "do-not-print-this-api-key"
    query = "unpublished-research-idea"

    def handler(request):
        assert request.url.params["api_key"] == secret
        return httpx.Response(200, json={"ok": True})

    with caplog.at_level("INFO", logger="httpx"):
        result = await fetcher(handler).fetch_json(f"https://{HOST}/v2",
                                                  {"api_key": secret, "query": query})
    assert "HTTP Request:" in caplog.text
    assert secret not in caplog.text
    assert query not in caplog.text
    assert secret not in result.url


async def test_the_httpx_log_filter_leaves_unrelated_client_logs_alone(caplog):
    with caplog.at_level("INFO", logger="httpx"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={}))) as client:
            await client.get("https://unrelated.example/data", params={"page": 2})
    assert "page=2" in caplog.text


async def test_invalid_json_encoding_is_a_typed_source_failure():
    def handler(request):
        return httpx.Response(200, content=b'\xff{"message": "bad encoding"}')

    with pytest.raises(SourceUnavailable, match="non-JSON"):
        await fetcher(handler).fetch_json(f"https://{HOST}/v2", {})


async def test_relative_redirects_resolve_against_the_actual_request_query():
    def handler(request):
        if "redirected" not in request.url.fragment:
            return httpx.Response(302, headers={"location": "#redirected"})
        return httpx.Response(200, json={"page": request.url.params["page"]})

    result = await fetcher(handler).fetch_json(f"https://{HOST}/v2", {"page": 2})
    assert result.payload == {"page": "2"}
