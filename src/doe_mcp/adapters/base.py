"""Adapter plumbing: egress-checked fetching, TTL cache, politeness.

Read-only by construction (this module has no write verbs to call), typed
errors at the boundary, TTL caching keyed by (source, request), per-host
politeness budgets, and the egress policy as the only outbound path. The
`Fetcher` seam exists so tests replay recorded fixtures instead of
hand-written shapes.

Politeness is not decoration here. DOE endpoints are shared public
infrastructure with, in most cases, no published rate limit — which is an
absence of a stated ceiling, not permission. The concurrency cap, the honest
User-Agent, and the single-retry budget are what make this a client a
publisher would not want to block. Five DOE hosts are already confirmed
dropping plain scripted clients; behaving well is how the rest stay
reachable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar
from urllib.parse import urlparse

import httpx

from ..core.egress import (DECOMPRESSION_RATIO_FLOOR_BYTES,
                           MAX_DECOMPRESSION_RATIO, MAX_RESPONSE_BYTES,
                           EgressPolicy)
from ..core.envelope import utc_now_iso
from ..core.errors import InvalidQuery, RateLimited, SourceUnavailable
from ..core.registry import SourceManifest

log = logging.getLogger("doe_mcp.adapters")

PER_HOST_CONCURRENCY = 2
RETRY_BUDGET = 1
REQUEST_TIMEOUT_SECONDS = 30.0

# An honest User-Agent, with a contact path. A publisher who wants to talk to
# us — or block us — should not have to guess who this is.
#
# This is a stated requirement, not a courtesy. EIA's security policy
# (eia.gov/about/privacy_security_policy.php, read 2026-09-08) says it
# "reserves the right to block robots that do not contain contact information
# that can be used to contact the owner." The contact path here must therefore
# resolve to something a human at a publisher can actually reach, which is a
# publishing dependency rather than a cosmetic one — a test asserts the shape
# and decision 0019 records the obligation.
USER_AGENT = ("DOE-MCP/0.1 (+https://github.com/pranava0x0/DOE-MCP; "
              "public-data MCP servers; not affiliated with US DOE)")

JSON_ACCEPT = "application/json"
TEXT_ACCEPT = "text/plain, text/*;q=0.9"

_host_semaphores: dict[str, asyncio.Semaphore] = {}

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# The request reached the API, the API understood it as HTTP, and refused it
# as a query. That is the caller's problem rather than the publisher's, and
# reporting it as `SourceUnavailable` — "outage, not an empty result" — sends
# a caller to check a service that is running perfectly. Daymet answers a
# point outside its grid with 400 and a sentence saying so; the ESGF bridge
# answers an unknown facet with 422 and names the parameter. Both are worth
# repeating verbatim.
_REFUSED_QUERY_STATUSES = frozenset({400, 422})


def _semaphore_for(host: str) -> asyncio.Semaphore:
    if host not in _host_semaphores:
        _host_semaphores[host] = asyncio.Semaphore(PER_HOST_CONCURRENCY)
    return _host_semaphores[host]


# One HTTP client per event loop, shared by every fetcher running on it. A
# client per request opened a fresh TLS connection to the same publisher
# every time, so a four-catalog fan-out paid four handshakes and a paginated
# walk paid one per page. The pool is keyed by loop rather than held on an
# adapter because the CLI runs a new loop per command, and a connection pool
# bound to a closed loop cannot be reused.
_clients: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


def shared_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    for stale in [known for known in _clients if known.is_closed()]:
        del _clients[stale]
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(follow_redirects=False,
                                   timeout=REQUEST_TIMEOUT_SECONDS)
        _clients[loop] = client
    return client


async def close_shared_client() -> None:
    """Release the running loop's client. The CLI calls this at the end of
    each command so no pool outlives its loop; a server never needs to."""
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


def _declared_length(response: httpx.Response) -> int:
    declared = response.headers.get("content-length")
    return int(declared) if declared and declared.isdigit() else 0


@dataclass
class _Body:
    """What survives a capped read. The httpx Response is closed as soon as
    the body is read, so nothing downstream may hold one."""

    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str
    encoding: str | None


class Fetcher(Protocol):
    async def fetch_json(self, url: str,
                         params: dict[str, Any]) -> "JsonResponse": ...


class TextFetcher(Protocol):
    """The same seam for publishers who answer in text rather than JSON.

    A separate protocol rather than a second method on `Fetcher` because the
    two are different content contracts, and most adapters here want only the
    first. `HttpFetcher`, `ReplayFetcher`, and `RecordingFetcher` satisfy
    both; a JSON-only test stub still satisfies `Fetcher` alone.
    """

    async def fetch_text(self, url: str,
                         params: dict[str, Any]) -> "TextResponse": ...


@dataclass
class JsonResponse:
    """Body plus the headers that carry meaning.

    OSTI returns its total-match count in `X-Total-Count` rather than in the
    body, and the body itself is a bare JSON array. A fetcher that returned
    only a dict would throw away the one number every pagination answer needs
    — which is why this type exists instead of `dict`.
    """

    payload: Any
    headers: dict[str, str]
    url: str

    def header_int(self, name: str) -> int | None:
        raw = self.headers.get(name.lower())
        if raw is None or not raw.strip().lstrip("-").isdigit():
            return None
        return int(raw.strip())


@dataclass
class TextResponse:
    """A body that is text rather than JSON, with the headers that carry
    meaning.

    Not every publisher in this ecosystem answers in JSON. BPA's balancing-
    authority feeds are tab-delimited text files, a long-standing utility
    convention that predates REST, and `Last-Modified` on a static file is
    the publisher's own statement of when it last wrote one — which is worth
    more than the time this client happened to read it.
    """

    text: str
    headers: dict[str, str]
    url: str


@dataclass
class HttpFetcher:
    """The only network path. Redirects are followed manually so every hop
    passes the egress policy; no default header carries a credential (a test
    pins that), so the cross-host credential-stripping rule holds by
    construction."""

    policy: EgressPolicy
    extra_headers: dict[str, str] | None = None
    transport: httpx.AsyncBaseTransport | None = None
    """Test seam. An `httpx.MockTransport` here runs the whole fetch path —
    status handling, the capped read, 403 classification — against a canned
    response and no network. Unset in production, where the per-loop shared
    client is used."""

    async def fetch_json(self, url: str,
                         params: dict[str, Any]) -> JsonResponse:
        body, host = await self._fetch(url, params, JSON_ACCEPT)
        return JsonResponse(payload=self._decode_json(body, host),
                            headers={k.lower(): v
                                     for k, v in body.headers.items()},
                            url=body.url)

    async def fetch_text(self, url: str,
                         params: dict[str, Any]) -> TextResponse:
        body, host = await self._fetch(url, params, TEXT_ACCEPT)
        return TextResponse(text=self._decode_text(body, host),
                            headers={k.lower(): v
                                     for k, v in body.headers.items()},
                            url=body.url)

    def _headers(self, accept: str) -> dict[str, str]:
        # Accept matters: OSTI's APIs return XML to a client that does not
        # ask for JSON, which is the single most common way to get a
        # confusing parse failure out of them. A text feed asks for text for
        # the same reason — so that what comes back is what was requested
        # rather than whatever the server guessed.
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        headers.update(self.extra_headers or {})
        return headers

    async def _fetch(self, url: str, params: dict[str, Any],
                     accept: str = JSON_ACCEPT) -> tuple[_Body, str]:
        current = url
        for hop in range(4):  # initial request + MAX_REDIRECTS
            self.policy.validate_url(current)
            host = urlparse(current).hostname or ""
            async with _semaphore_for(host):
                response = await self._request_with_retry(current, params,
                                                          accept)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise SourceUnavailable(
                        f"redirect from {host} without a Location header")
                current = self.policy.validate_redirect(current, location,
                                                        hop + 1)
                params = {}  # params were consumed by the first URL
                continue
            return response, host
        raise SourceUnavailable("redirect chain did not settle")

    async def _request_with_retry(self, url: str, params: dict[str, Any],
                                  accept: str) -> _Body:
        last: Exception | None = None
        for attempt in range(RETRY_BUDGET + 1):
            try:
                body = await self._read_capped(url, params, accept)
            except httpx.HTTPError as err:
                last = err
                if attempt < RETRY_BUDGET:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise SourceUnavailable(
                    f"request to {urlparse(url).hostname} failed after "
                    f"{RETRY_BUDGET + 1} attempts "
                    f"({err.__class__.__name__}). This is an outage or "
                    "network problem, not an empty result.") from err
            if body.status_code == 429 or body.status_code >= 500:
                if attempt < RETRY_BUDGET:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if body.status_code == 429:
                    retry_after = body.headers.get("retry-after")
                    raise RateLimited(
                        f"{urlparse(url).hostname} is rate-limiting "
                        "(HTTP 429); respect the politeness budget",
                        int(retry_after) if retry_after
                        and retry_after.isdigit() else None)
                raise SourceUnavailable(
                    f"{urlparse(url).hostname} returned HTTP "
                    f"{body.status_code} after retry. Outage, not an empty "
                    "result.")
            return body
        raise SourceUnavailable("unreachable") from last

    async def _read_capped(self, url: str, params: dict[str, Any],
                           accept: str) -> _Body:
        if self.transport is not None:
            async with httpx.AsyncClient(
                    transport=self.transport, follow_redirects=False,
                    timeout=REQUEST_TIMEOUT_SECONDS) as client:
                return await self._stream(client, url, params, accept)
        return await self._stream(shared_client(), url, params, accept)

    async def _stream(self, client: httpx.AsyncClient, url: str,
                      params: dict[str, Any], accept: str) -> _Body:
        """Stream the response, stopping as soon as it breaks a limit.

        The byte cap is checked during the read rather than on the finished
        body, so an oversized response costs the bytes up to the limit rather
        than all of them. The expansion ratio is the second half: httpx
        decodes gzip transparently, so a response that decodes to far more
        than it transferred is refused whatever its final size, because being
        small on the wire is the point of that attack.

        Error bodies are read under the same caps, not dropped. A 403 from
        EIA carries the JSON that says API_KEY_MISSING, and a 403 from a WAF
        carries an HTML challenge page; the classifier below needs the bytes
        to tell them apart. Reading only 200 bodies, as this once did, left
        every 403 classified as a WAF block. Redirects are the exception: the
        Location header is the whole message.
        """
        host = urlparse(url).hostname or ""
        request = client.build_request("GET", url, params=params,
                                       headers=self._headers(accept))
        response = await client.send(request, stream=True)
        chunks: list[bytes] = []
        decoded = 0
        declared = _declared_length(response)
        try:
            if response.status_code not in _REDIRECT_STATUSES:
                async for chunk in response.aiter_bytes():
                    decoded += len(chunk)
                    if decoded > MAX_RESPONSE_BYTES:
                        raise SourceUnavailable(
                            f"{host} response exceeded the "
                            f"{MAX_RESPONSE_BYTES}-byte egress cap; transfer "
                            "stopped")
                    raw = response.num_bytes_downloaded or declared
                    if (decoded > DECOMPRESSION_RATIO_FLOOR_BYTES and raw > 0
                            and decoded / raw > MAX_DECOMPRESSION_RATIO):
                        raise SourceUnavailable(
                            f"{host} response expanded {decoded // raw}x on "
                            f"decompression, over the "
                            f"{MAX_DECOMPRESSION_RATIO}x egress limit; "
                            "transfer stopped")
                    chunks.append(chunk)
            return _Body(status_code=response.status_code,
                         headers=response.headers,
                         content=b"".join(chunks), url=str(response.url),
                         encoding=response.encoding)
        finally:
            await response.aclose()

    def _decode_text(self, body: _Body, host: str) -> str:
        """Decode a text body under the declared encoding, or say so.

        The encoding is the publisher's statement, not a guess: falling back
        to a lenient decode would turn a changed encoding into mojibake in
        the middle of a data table, which reads as bad data rather than as a
        broken read. A BOM is stripped because a leading zero-width
        character in the first header line is invisible in the file and
        breaks a prefix match on it.
        """
        raw = self._checked_body(body, host)
        encoding = body.encoding or "utf-8"
        try:
            return raw.decode(encoding).lstrip("\ufeff")
        except (UnicodeDecodeError, LookupError) as err:
            raise SourceUnavailable(
                f"{host} returned bytes that are not valid {encoding}, which "
                "is the encoding it declared. That is a changed feed or an "
                "error page, not data.") from err

    def _decode_json(self, body: _Body, host: str) -> Any:
        raw = self._checked_body(body, host)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise SourceUnavailable(
                f"{host} returned non-JSON where JSON was expected. On the "
                "OSTI family this usually means the Accept header did not "
                "reach the server, which makes it answer in XML; it can also "
                "be a bot challenge or an outage page.") from err

    @staticmethod
    def _checked_body(body: _Body, host: str) -> bytes:
        if body.status_code == 403:
            raise SourceUnavailable(_forbidden_message(host, body.content))
        if body.status_code in _REFUSED_QUERY_STATUSES:
            raise InvalidQuery(_refused_query_message(host, body.status_code,
                                                     body.content))
        if body.status_code != 200:
            detail = _machine_readable_detail(body.content)
            raise SourceUnavailable(
                f"{host} returned HTTP {body.status_code}"
                + (f": {detail}" if detail else ""), body.status_code)
        return body.content



def _refused_query_message(host: str, status: int,
                           content: bytes) -> str:
    """The publisher's own account of what was wrong with the request.

    Quoted rather than summarized. These endpoints are specific — "Daymet
    Tile was not found with input lat and lon", "Extra inputs are not
    permitted" against a named parameter — and a generic message would throw
    away the one part of the response that says how to fix the call.
    """
    detail = _machine_readable_detail(content)
    if detail:
        return (f"{host} refused this request (HTTP {status}) and said why: "
                f"{detail}")
    return (f"{host} refused this request (HTTP {status}) with no "
            "machine-readable reason. The service is reachable and did not "
            "accept the query as sent.")


def _machine_readable_detail(content: bytes) -> str | None:
    """A message out of an error body, across the shapes these APIs use.

    Four of them appear in this registry: a bare `{"message": ...}` from
    Daymet and the Basis Set Exchange, a `{"error": {...}}` from EIA,
    FastAPI's `{"detail": [{"loc": [...], "msg": ...}]}` from the ESGF
    bridge, and JSON:API's `{"errors": [{"detail": ...}]}` from OPTIMADE.
    Parsed here rather than in each adapter, because the fetch path is where
    the bytes are and by the time an adapter sees the exception they are
    gone.
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("message", "detail", "error", "errors"):
        node = parsed.get(key)
        if isinstance(node, str) and node.strip():
            return node.strip()
        if isinstance(node, dict):
            joined = " ".join(str(node[k]) for k in ("code", "message", "msg")
                              if node.get(k))
            if joined:
                return joined
        if isinstance(node, list):
            parts = [_list_entry(item) for item in node
                     if isinstance(item, dict)]
            joined = "; ".join(p for p in parts if p)
            if joined:
                return joined
    return None


def _list_entry(item: dict) -> str | None:
    """One entry of an error array, in either of the two shapes met here.

    FastAPI names the offending parameter in `loc` and the problem in `msg`;
    JSON:API, which OPTIMADE follows, puts the whole sentence in `detail`.
    """
    if item.get("msg"):
        location = ".".join(str(p) for p in item.get("loc", []))
        return f"{location}: {item['msg']}" if location else str(item["msg"])
    if isinstance(item.get("detail"), str):
        return item["detail"].strip() or None
    return None


def _forbidden_message(host: str, content: bytes) -> str:
    """Two very different things arrive as HTTP 403 in this ecosystem, and
    conflating them produces a wrong claim in opposite directions.

    An API refusing a request it understood answers in its own JSON — EIA's
    403 carries `{"error": {"code": "API_KEY_MISSING", ...}}` and means
    exactly what it says. A WAF dropping a non-browser client answers with an
    HTML challenge page or nothing at all, and means only that this client
    shape was refused: five DOE hosts are confirmed doing that while serving
    browsers normally.

    So the body decides the message. Recording the second case as "gated"
    without a real-browser check is the mistake decision 0007 names; sixty-
    three cited URLs sat in that state at the last sweep.
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        detail = err if isinstance(err, str) else None
        if isinstance(err, dict):
            detail = " ".join(str(err[k]) for k in ("code", "message")
                              if err.get(k))
        if detail:
            return (f"{host} refused this request (HTTP 403) and said why: "
                    f"{detail}")
    return (f"{host} refused this request (HTTP 403) with no machine-readable "
            "reason. For DOE hosts that is usually a WAF dropping non-browser "
            "clients rather than a real access gate: record it as "
            "blocked_probe and check it in a browser before describing the "
            "source as gated.")


@dataclass
class FetchResult:
    payload: Any
    headers: dict[str, str]
    retrieved_at: str          # when the bytes left the publisher's server
    cache_age_seconds: int
    request_url: str
    # Explicit, not inferred from cache_age_seconds: a genuine cache hit
    # under a second after insertion also reports age 0.
    from_cache: bool = False

    def header_int(self, name: str) -> int | None:
        raw = self.headers.get(name.lower())
        if raw is None or not raw.strip().lstrip("-").isdigit():
            return None
        return int(raw.strip())


T = TypeVar("T")


@dataclass
class Fetched(Generic[T]):
    """An adapter's answer together with the provenance of the fetch behind
    it.

    Every adapter operation returns either one of these or a page type that
    carries the same four fields, so a domain tool never has to invent a
    `retrieved_at` or report a cache hit as a live read. Before this type
    existed, the vehicle menus, the single-record lookups, and the EIA route
    walk returned bare values, and the tools stamped "now" and age zero on
    answers that had come out of the cache.
    """

    value: T
    retrieved_at: str
    cache_age_seconds: int
    request_url: str
    from_cache: bool

    @classmethod
    def of(cls, result: FetchResult, value: T) -> "Fetched[T]":
        return cls(value=value, retrieved_at=result.retrieved_at,
                   cache_age_seconds=result.cache_age_seconds,
                   request_url=result.request_url,
                   from_cache=result.from_cache)


class TTLCache:
    """Response cache keyed by (source_id, url, params)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str, Any, dict[str, str]]] = {}

    @staticmethod
    def _key(source_id: str, url: str, params: dict[str, Any]) -> str:
        return json.dumps([source_id, url, sorted(params.items())],
                          separators=(",", ":"), default=str)

    def get(self, source_id: str, url: str, params: dict[str, Any],
            ttl_seconds: int) -> FetchResult | None:
        key = self._key(source_id, url, params)
        hit = self._store.get(key)
        if hit is None:
            return None
        stored_monotonic, retrieved_at, payload, headers = hit
        age = int(time.monotonic() - stored_monotonic)
        if age > ttl_seconds:
            del self._store[key]
            return None
        return FetchResult(payload=payload, headers=headers,
                           retrieved_at=retrieved_at, cache_age_seconds=age,
                           request_url=url, from_cache=True)

    def put(self, source_id: str, url: str, params: dict[str, Any],
            payload: Any, headers: dict[str, str]) -> FetchResult:
        retrieved_at = utc_now_iso()
        self._store[self._key(source_id, url, params)] = (
            time.monotonic(), retrieved_at, payload, headers)
        return FetchResult(payload=payload, headers=headers,
                           retrieved_at=retrieved_at, cache_age_seconds=0,
                           request_url=url, from_cache=False)

    def clear(self) -> None:
        self._store.clear()


_shared_cache = TTLCache()


def shared_cache() -> TTLCache:
    return _shared_cache


def log_source_call(manifest: SourceManifest, operation: str,
                    params: dict[str, Any], record_count: int | None) -> None:
    """Query text is user content — an unpublished research idea can sit in a
    literature search — so parameter NAMES are logged and values are not."""
    log.info("source=%s op=%s params=%s records=%s", manifest.id, operation,
             sorted(params), record_count if record_count is not None else "-")


def total_or_none(value: Any) -> int | None:
    """A publisher's count of matching records, or None when it sent none.

    The distinction this preserves is the envelope's third pagination state,
    `unknown`. Five adapters once read a missing total as zero, and the tools
    over them reported a page as complete where the truth was that nobody
    knew. An integer or a string of digits is a count; anything else,
    a bool or a float included, is the absence of one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def egress_policy_for(manifest: SourceManifest,
                      service_url: str) -> EgressPolicy:
    host = urlparse(service_url).hostname
    if not host:
        raise ValueError(f"manifest {manifest.id}: service_url has no host")
    return EgressPolicy(allowed_hosts=frozenset({host.lower()}),
                        insecure_transport=manifest.access.insecure_transport)
