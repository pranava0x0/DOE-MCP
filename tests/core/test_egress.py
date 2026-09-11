"""Egress refusals. Every rule needs a known-bad case that is refused —
an egress rule without its refusal test is prose, not policy."""
from __future__ import annotations

import pytest

from doe_mcp.core.egress import EgressPolicy, hosts_from_url
from doe_mcp.core.errors import EgressRefused


def policy(**kw) -> EgressPolicy:
    """A policy whose resolver is stubbed to a public address, so these tests
    check the POLICY and not the developer's DNS."""
    kw.setdefault("allowed_hosts", frozenset({"www.osti.gov"}))
    kw.setdefault("resolver", lambda host, _: [(0, 0, 0, "", ("93.184.216.34", 0))])
    return EgressPolicy(**kw)


def test_plain_http_is_refused_unless_the_manifest_declares_it():
    with pytest.raises(EgressRefused, match="plain http refused"):
        policy().validate_url("http://www.osti.gov/api")


def test_declared_insecure_transport_permits_http():
    policy(insecure_transport=True).validate_url("http://www.osti.gov/api")


def test_non_http_schemes_are_refused():
    for url in ("file:///etc/passwd", "ftp://www.osti.gov/x",
                "gopher://www.osti.gov/"):
        with pytest.raises(EgressRefused, match="refused"):
            policy().validate_url(url)


def test_an_unregistered_host_is_refused():
    """The allowlist is per source, not global, because a record returned by
    one publisher can carry a URL pointing anywhere."""
    with pytest.raises(EgressRefused, match="not in this source's registered"):
        policy().validate_url("https://evil.example.com/x")


def test_ip_literal_hosts_are_refused():
    with pytest.raises(EgressRefused, match="IP-literal host"):
        policy(allowed_hosts=frozenset({"127.0.0.1"})).validate_url(
            "https://127.0.0.1/x")


@pytest.mark.parametrize("address,label", [
    ("127.0.0.1", "loopback"),
    ("169.254.169.254", "link-local"),   # cloud metadata
    ("10.0.0.1", "private range"),
    ("192.168.1.1", "private range"),
    ("100.64.0.1", "shared CGNAT"),
])
def test_a_name_resolving_into_a_blocked_range_is_refused(address, label):
    """DNS rebinding: the hostname is allowlisted and the ADDRESS is not."""
    p = policy(resolver=lambda h, _: [(0, 0, 0, "", (address, 0))])
    with pytest.raises(EgressRefused, match="refusing"):
        p.validate_url("https://www.osti.gov/api")


def test_a_non_default_port_is_refused():
    with pytest.raises(EgressRefused, match="port 8080 refused"):
        policy().validate_url("https://www.osti.gov:8080/api")


def test_redirects_are_revalidated_against_the_same_allowlist():
    p = policy()
    with pytest.raises(EgressRefused, match="not in this source's registered"):
        p.validate_redirect("https://www.osti.gov/a",
                            "https://elsewhere.example.com/b", 1)


def test_a_same_host_redirect_is_allowed():
    """This ecosystem redirects constantly — energy.gov/code.json 302s to a
    dated static file on the same host."""
    target = policy(allowed_hosts=frozenset({"www.energy.gov"})).validate_redirect(
        "https://www.energy.gov/code.json",
        "https://www.energy.gov/sites/default/files/2025-05/code.json", 1)
    assert target.endswith("code.json")


def test_redirect_chains_are_capped():
    with pytest.raises(EgressRefused, match="redirect chain exceeded"):
        policy().validate_redirect("https://www.osti.gov/a",
                                   "https://www.osti.gov/b", 9)


def test_dns_failure_is_a_refusal_not_a_silent_pass():
    def boom(host, _):
        raise OSError("nope")
    with pytest.raises(EgressRefused, match="DNS resolution failed"):
        policy(resolver=boom).validate_url("https://www.osti.gov/x")


def test_hosts_from_url():
    assert hosts_from_url("https://WWW.OSTI.GOV/api") == frozenset({"www.osti.gov"})
