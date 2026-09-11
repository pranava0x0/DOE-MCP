"""Shared fixtures. Every test runs against RECORDED publisher responses, and
none reaches the network: `ReplayFetcher` raises on an unknown request rather
than falling through, so a test cannot quietly start passing or failing on
someone else's uptime."""
from __future__ import annotations

import copy
import dataclasses
import functools
from pathlib import Path

import pytest

from doe_mcp.adapters import ADAPTER_CLASSES
from doe_mcp.adapters.base import TTLCache
from doe_mcp.adapters.replay import ReplayFetcher
from doe_mcp.core.credentials import Credentials
from doe_mcp.core.audit import AuditLog
from doe_mcp.runtime import RuntimeContext, load_context

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@functools.lru_cache(maxsize=1)
def _recorded_interactions() -> dict:
    """Every recorded fixture, parsed once per session.

    This used to be parsed per test. The fixtures are 532 KB across sixteen
    files and roughly three hundred tests take one, so a run was re-reading
    and re-decoding about 160 MB of JSON to serve responses that never
    change — which took the suite past two minutes on 2026-09-08 as the
    fixture set grew. Cached here instead; the result is treated as
    immutable and no test writes to it.
    """
    merged: dict = {}
    for path in sorted(FIXTURES.glob("*.json")):
        merged.update(ReplayFetcher.from_file(path).interactions)
    return merged


def merged_replay() -> ReplayFetcher:
    """One fetcher over every recorded fixture. The adapters key on URL, so
    merging is safe and means a test does not have to know which file holds
    which interaction.

    A fresh fetcher per call over the shared interaction table: `calls` is
    per-instance, and tests assert against it.
    """
    return ReplayFetcher(interactions=_recorded_interactions())


@pytest.fixture
def replay() -> ReplayFetcher:
    return merged_replay()


@pytest.fixture
def credentials(tmp_path) -> Credentials:
    """A credentials object with nothing set, pointed at a temp path so a
    test can never read or write the developer's real keys."""
    return Credentials(values={}, path=tmp_path / "credentials.env",
                       file_exists=False)


@functools.lru_cache(maxsize=1)
def _loaded_once() -> RuntimeContext:
    """The registry, organization table and catalog, parsed once per session.

    Reading them is 174 ms — 82 YAML manifests plus the two tables — and
    every test that takes a context was paying it, which was most of the
    suite's runtime. Never handed to a test directly: tests mutate manifests
    (the sabotage tests flip `declared_state`, others set adapter fields),
    so each one gets a deep copy, which measures 57 times faster than
    re-reading the files.
    """
    return load_context(SOURCES, credentials=Credentials(
        values={}, path=Path("/nonexistent"), file_exists=False))


def _context(replay, creds) -> RuntimeContext:
    template = _loaded_once()
    cache = TTLCache()
    # Every adapter with a fetcher seam gets the replay fetcher, from the
    # one table that says what each takes. `curated` reads local tables and
    # keeps the template's instance; `eia_v2` also takes the credentials.
    overrides = {}
    for kind, (field_name, cls) in ADAPTER_CLASSES.items():
        if kind == "curated":
            continue
        kwargs = {"fetcher": replay, "cache": cache}
        if kind == "eia_v2":
            kwargs["credentials"] = creds
        overrides[field_name] = cls(**kwargs)
    return dataclasses.replace(
        template,
        # Only the registry is copied. Tests mutate manifests — the sabotage
        # tests flip `declared_state`, others set adapter fields — so each
        # one needs its own. Nothing mutates the organization table or the
        # sub-MCP catalog, and copying them was 14 ms per test, which at
        # this suite's size is more time than every network-shaped test in
        # it put together.
        sources=copy.deepcopy(template.sources),
        credentials=creds,
        audit=AuditLog(),
        **overrides)


@pytest.fixture
def ctx(replay, credentials):
    return _context(replay, credentials)


@pytest.fixture
def keyed_ctx(replay, tmp_path):
    """A context whose EIA credential is a sentinel.

    The default `ctx` holds no credentials on purpose, so a test can never
    read the developer's real keys — which is also why the EIA tools had no
    domain tests until 2026-09-08, and why a bug in `energy.grid_status`
    survived six days. A recorded fixture is keyed on (url, params) with
    credential parameters stripped, so a recording made with a real key
    replays under this sentinel exactly as it would under any other.
    """
    creds = Credentials(values={"EIA_API_KEY": "sentinel-not-a-real-key"},
                        path=tmp_path / "credentials.env", file_exists=True)
    return _context(replay, creds)


@pytest.fixture
def registry(ctx):
    return ctx.sources
