"""HTTP transport whose sockets use the egress policy's address snapshot."""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpcore
import httpx

from .egress import ResolvedTarget
from .errors import EgressRefused

_target: ContextVar[ResolvedTarget | None] = ContextVar("doe_mcp_target", default=None)


@contextmanager
def pinned_target(target: ResolvedTarget) -> Iterator[None]:
    token = _target.set(target)
    try:
        yield
    finally:
        _target.reset(token)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner if inner is not None else httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        target = _target.get()
        if target is None or (host, port) != (target.host, target.port):
            raise EgressRefused("connection has no matching validated DNS snapshot")
        deadline = time.monotonic() + timeout if timeout is not None else None
        last: Exception | None = None
        for index, address in enumerate(target.addresses):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise httpcore.ConnectTimeout("validated address connection timed out")
            # Reserve a share for every address still to try. Otherwise a
            # blackholed IPv6 address can consume the entire IPv4 budget.
            attempt_timeout = (None if remaining is None else
                               remaining / (len(target.addresses) - index))
            try:
                # Canonical numeric addresses bypass AnyIO's hostname lookup.
                return await self._inner.connect_tcp(
                    address, port, timeout=attempt_timeout, local_address=local_address,
                    socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as err:
                last = err
        if last is not None:
            raise last
        raise EgressRefused("connection has an empty validated DNS snapshot")


_ERRORS = {
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.WriteTimeout: httpx.WriteTimeout,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.WriteError: httpx.WriteError,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
    httpcore.LocalProtocolError: httpx.LocalProtocolError,
    httpcore.UnsupportedProtocol: httpx.UnsupportedProtocol,
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.ProtocolError: httpx.ProtocolError,
}


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except tuple(_ERRORS) as err:
        mapped = next(value for key, value in _ERRORS.items() if isinstance(err, key))
        raise mapped(str(err)) from err


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with _http_errors():
            async for chunk in self._stream:
                yield chunk

    async def aclose(self) -> None:
        with _http_errors():
            await self._stream.aclose()


class PinnedHTTPTransport(httpx.AsyncBaseTransport):
    """Keep the original origin in the pool; pin only the TCP destination.

    HTTP/2 is disabled so distinct origins cannot share a connection. No
    proxy is configured: a proxy's independent DNS lookup would undo the pin.
    Both libraries are exact-pinned and only their public interfaces are used.
    """

    def __init__(self, network_backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(), max_connections=100,
            max_keepalive_connections=20, keepalive_expiry=5.0,
            http1=True, http2=False, retries=0,
            network_backend=PinnedNetworkBackend(network_backend))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        target = _target.get()
        origin = (request.url.scheme, request.url.raw_host.decode("ascii"),
                  request.url.port or (443 if request.url.scheme == "https" else 80))
        if target is None or origin != (target.scheme, target.host, target.port):
            raise EgressRefused("request has no matching validated DNS snapshot")
        if request.method != "GET":
            raise EgressRefused("the pinned fetch transport accepts GET only")
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = target.host
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=headers.raw, content=request.stream, extensions=extensions)
        with _http_errors():
            response = await self._pool.handle_async_request(core_request)
        return httpx.Response(response.status, headers=response.headers,
                              stream=_ResponseStream(response.stream),
                              extensions=response.extensions)

    async def aclose(self) -> None:
        with _http_errors():
            await self._pool.aclose()
