"""Egress policy: the only outbound path.

Adapters are the only code that reaches the network, and every request URL
passes through `EgressPolicy.validate_url` immediately before use. Its
immutable address snapshot is the transport's only set of TCP destinations;
the HTTP origin and TLS hostname remain the publisher's hostname.

Every rule has a known-bad fixture in tests/core/test_egress.py that must be
refused — an egress rule without its refusal test is prose, not policy.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .errors import EgressRefused

MAX_RESPONSE_BYTES = 20_000_000
MAX_REDIRECTS = 3

# A gzipped response is small on the wire and large once decoded, so a body
# well under the byte cap can still expand past any memory budget. The cap
# and the ratio are checked separately. 50x is far above what real
# government JSON achieves; OSTI's own responses measure well under it.
MAX_DECOMPRESSION_RATIO = 50

# Below this the ratio is meaningless: a 200-byte response from a 4-byte
# compressed frame is 50x and entirely ordinary.
DECOMPRESSION_RATIO_FLOOR_BYTES = 64_000

_SHARED_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_ip(ip.ipv4_mapped)
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (includes cloud metadata)"
    if ip.is_private:
        return "private range"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return "non-unicast/reserved"
    if ip.version == 4 and ip in _SHARED_CGNAT:
        return "shared CGNAT range"
    return None


@dataclass(frozen=True)
class ResolvedTarget:
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.addresses:
            raise EgressRefused("validated DNS snapshot has no addresses")
        for address in self.addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as err:
                raise EgressRefused("validated DNS snapshot contains a nonnumeric address") from err
            if str(ip) != address or _blocked_ip(ip):
                raise EgressRefused("validated DNS snapshot contains an unsafe address")


@dataclass
class EgressPolicy:
    """One policy instance per source manifest.

    The allowlist is per source rather than global on purpose: a record
    returned by OSTI can carry a `site_url` pointing anywhere on the
    internet, and a global allowlist would let one source's content steer a
    request to another source's host.
    """

    allowed_hosts: frozenset[str]
    insecure_transport: bool = False
    allowed_ports: frozenset[int] = field(default_factory=frozenset)
    resolver: object = None  # test seam; defaults to socket.getaddrinfo

    def validate_url(self, url: str) -> ResolvedTarget:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError as err:
            raise EgressRefused("malformed URL authority or port refused") from err

        if parsed.username is not None or parsed.password is not None:
            raise EgressRefused("URL user information refused; credentials belong outside URLs")

        if parsed.scheme == "http":
            if not self.insecure_transport:
                raise EgressRefused(
                    f"plain http refused for {parsed.hostname!r}; the source "
                    "manifest does not declare insecure_transport")
        elif parsed.scheme != "https":
            raise EgressRefused(f"scheme {parsed.scheme!r} refused; only "
                                "https (or manifest-declared http) is allowed")

        host = parsed.hostname
        if not host:
            raise EgressRefused("URL has no host")

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise EgressRefused(
                f"IP-literal host {host!r} refused; register a hostname in "
                "the source manifest")

        if host.lower() not in self.allowed_hosts:
            raise EgressRefused(
                f"host {host!r} is not in this source's registered host set "
                f"{sorted(self.allowed_hosts)}")

        default_ports = {443} | ({80} if self.insecure_transport else set())
        permitted = default_ports | set(self.allowed_ports)
        if port is not None and port not in permitted:
            raise EgressRefused(f"port {port} refused; permitted: "
                                f"{sorted(permitted)}")

        return ResolvedTarget(parsed.scheme, host, port or (
            443 if parsed.scheme == "https" else 80), self._check_dns(host))

    def _check_dns(self, host: str) -> tuple[str, ...]:
        resolver = self.resolver or socket.getaddrinfo
        try:
            infos = resolver(host, None)
        except OSError as err:
            raise EgressRefused(f"DNS resolution failed for {host!r}: "
                                f"{err.__class__.__name__}") from err
        addrs = tuple(dict.fromkeys(info[4][0] for info in infos))
        if not addrs:
            raise EgressRefused(f"DNS returned no addresses for {host!r}")
        checked = []
        for raw in addrs:
            try:
                ip = ipaddress.ip_address(raw.split("%")[0])
            except ValueError as err:
                raise EgressRefused(f"DNS returned an invalid address for {host!r}") from err
            reason = _blocked_ip(ip)
            if reason:
                raise EgressRefused(
                    f"{host!r} resolves to {ip} ({reason}); refusing")
            checked.append(str(ip))
        return tuple(dict.fromkeys(checked))

    def validate_redirect(self, from_url: str, location: str,
                          hop_count: int) -> str:
        """Validate one redirect hop; returns the absolute target URL.

        This ecosystem redirects constantly — nine host moves in a single
        2026-09-01 sweep — so redirects are followed, but every hop is
        re-validated against the same per-source allowlist. A source that
        has genuinely moved gets a manifest change, not a policy hole.
        """
        if hop_count > MAX_REDIRECTS:
            raise EgressRefused(f"redirect chain exceeded {MAX_REDIRECTS} hops")
        target = urljoin(from_url, location)
        self.validate_url(target)
        return target


def hosts_from_url(url: str) -> frozenset[str]:
    host = urlparse(url).hostname
    if not host:
        raise ValueError(f"cannot derive host from {url!r}")
    return frozenset({host.lower()})
