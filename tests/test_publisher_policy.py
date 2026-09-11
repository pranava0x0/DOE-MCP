"""Obligations publishers state in their own policies, checked in CI.

Decision 0019. Terms review is a human act recorded in a manifest, but three
of the obligations found in that review are mechanical, and a mechanical
obligation that is only written down drifts. These are the ones a test can
hold.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from doe_mcp.adapters.base import USER_AGENT

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources"

# Paths every active source's adapter is configured to request, against the
# Disallow rules its host publishes for `User-agent: *`. Recorded from the
# hosts' own robots.txt on 2026-09-08 rather than fetched, because a test
# that reaches the network fails on somebody else's uptime — and because a
# CHANGE upstream should be a reviewed manifest edit, which is what the
# liveness job in CI is for.
ROBOTS_DISALLOW = {
    "www.osti.gov": [
        "/account", "/dataexplorer/account", "/dataexplorer/search",
        "/doepatents/search", "/etdeweb/results", "/opennet/document",
        "/opennet/search-results", "/opennetadmin/", "/pages/account",
        "/pages/search", "/sciencecinema/account", "/sciencecinema/search",
        "/search/", "/includes",
    ],
    "openenergyhub.ornl.gov": ["/logout", "/login"],
    "www.federalregister.gov": [],
    "www.energy.gov": [
        "/core/", "/profiles/", "/admin/", "/comment/reply/", "/filter/tips",
        "/node/add/", "/search/", "/search?", "/user/register",
        "/user/password", "/user/login", "/user/logout", "/media/oembed",
    ],
}

# Sources that send no request at all: the curated PuRe table is transcribed
# into the repository and the registry answers about itself. A politeness
# note on either would describe traffic that does not exist.
NO_ENDPOINT_ADAPTERS = {"curated", "self_registry", "none"}


def _manifests():
    for path in sorted(SOURCES.rglob("*.yaml")):
        if path.name in ("organizations.yaml", "capabilities.yaml"):
            continue
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("id"):
            yield path, doc


def _active():
    return [(p, d) for p, d in _manifests()
            if (d.get("lifecycle") or {}).get("declared_state") == "active"]


def test_the_user_agent_carries_a_reachable_contact_path():
    """EIA's security policy reserves the right to block robots whose
    User-Agent carries no contact information. The shape is checked here; that
    the URL actually resolves is a publishing task (decision 0019 note)."""
    assert "DOE-MCP/" in USER_AGENT
    assert re.search(r"\(\+https://\S+;", USER_AGENT), (
        "the User-Agent must carry a '+<url>' contact path as its first "
        "comment field; publishers block robots that do not")
    assert "not affiliated with US DOE" in USER_AGENT


def test_no_active_source_requests_a_path_its_host_disallows():
    """The four OSTI API routes sit outside every Disallow rule on
    www.osti.gov, while the human search UIs (/search/, /pages/search,
    /dataexplorer/search) are disallowed for every agent. That is the
    difference between using a publisher's API and scraping its website, and
    it is worth holding rather than assuming."""
    offenders = []
    for _path, doc in _active():
        adapter = doc.get("adapter") or {}
        base = adapter.get("base_url") or adapter.get("document_url") or ""
        match = re.match(r"https?://([^/]+)(/.*)?$", base)
        if not match:
            continue
        host, route = match.group(1), match.group(2) or "/"
        for rule in ROBOTS_DISALLOW.get(host, []):
            if route.startswith(rule):
                offenders.append(f"{doc['id']}: {route} is under "
                                 f"Disallow: {rule} on {host}")
    assert not offenders, "\n".join(offenders)


def test_every_active_source_records_what_its_publisher_says_about_robots():
    """A source queried on an unstated rate budget is a source whose
    politeness nobody has thought about. `rate_limit_note` is where the
    thinking goes, including 'nothing is published'."""
    missing = [doc["id"] for _, doc in _active()
               if (doc.get("adapter") or {}).get("type")
               not in NO_ENDPOINT_ADAPTERS
               and not (doc.get("access") or {}).get("rate_limit_note")]
    assert not missing, (
        "active sources with no rate_limit_note: " + ", ".join(missing))
