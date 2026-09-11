"""The third pagination state, computed in one place and checked at every
tool that pages.

`pagination_coverage` is the one computation. The sweep below strips the
publisher's total out of REAL recorded responses, one publisher at a time,
and expects `unknown` from the tool over it: the bytes stay the publisher's
own, and only the count is gone, which is the schema change under test.
Before 2026-09-11 five of the nine call sites read a missing total as zero
and reported the page as complete (WORKLOG round 14, gap 4).
"""
from __future__ import annotations

import copy

import pytest

from doe_mcp.adapters.base import total_or_none
from doe_mcp.adapters.replay import ReplayFetcher
from doe_mcp.core.assemble import pagination_coverage
from doe_mcp.core.credentials import Credentials
from doe_mcp.core.envelope import PaginationCoverage
from doe_mcp.domains import docs, earth, energy, materials, research, tech
from tests.conftest import _context, merged_replay

P = PaginationCoverage


@pytest.mark.parametrize("total,seen,offset,more,expected", [
    (100, 10, 0, None, P.truncated),
    (10, 10, 0, None, P.complete),
    (12, 2, 10, None, P.complete),
    (13, 2, 10, None, P.truncated),
    (0, 0, 0, None, P.complete),
    (None, 10, 0, None, P.unknown),
    (None, 10, 0, True, P.truncated),
    (None, 10, 0, False, P.complete),
    # A has-more flag outranks a total that says the page is the end.
    (10, 10, 0, True, P.truncated),
    # A total that says more exists outranks a flag that says it does not.
    (100, 10, 0, False, P.truncated),
])
def test_pagination_coverage(total, seen, offset, more, expected):
    assert pagination_coverage(total, seen, offset=offset,
                               more=more) is expected


@pytest.mark.parametrize("value,expected", [
    (35, 35), ("4451332", 4451332), (" 7 ", 7), (0, 0),
    (None, None), (True, None), (False, None), (3.0, None), (-1, None),
    ("", None), ("abc", None), ("3.5", None), ([], None), ({}, None),
])
def test_total_or_none(value, expected):
    assert total_or_none(value) == expected


def _without(replay: ReplayFetcher, url_part: str, *paths, header=None
             ) -> ReplayFetcher:
    """The recorded interactions with one field removed from every response
    whose URL contains `url_part`. A path is a tuple of keys, and a list
    index where the body is a list."""
    out = {}
    for key, interaction in replay.interactions.items():
        if url_part in interaction["url"]:
            interaction = copy.deepcopy(interaction)
            if header:
                interaction["headers"] = {
                    k: v for k, v in (interaction.get("headers") or {}).items()
                    if k.lower() != header}
            for path in paths:
                node = interaction.get("body")
                for step in path[:-1]:
                    if isinstance(node, dict):
                        node = node.get(step)
                    elif (isinstance(node, list) and isinstance(step, int)
                          and step < len(node)):
                        node = node[step]
                    else:
                        node = None
                        break
                if isinstance(node, dict):
                    node.pop(path[-1], None)
        out[key] = interaction
    return ReplayFetcher(interactions=out)


def _credentials(tmp_path, keyed: bool) -> Credentials:
    if keyed:
        return Credentials(values={"EIA_API_KEY": "sentinel-not-a-real-key"},
                           path=tmp_path / "credentials.env",
                           file_exists=True)
    return Credentials(values={}, path=tmp_path / "credentials.env",
                       file_exists=False)


# (adapter, URL fragment, body paths to drop, header to drop, keyed, call)
MISSING_TOTAL = [
    ("eia_v2", "region-data/data", [("response", "total")], None, True,
     lambda c: energy.grid_status(c, "CISO", hours=3)),
    ("essdive", "ess-dive.lbl.gov/packages", [("total",)], None, False,
     lambda c: earth.search_datasets(c, text="permafrost", rows=5)),
    ("esgf", "esgf-1-5-bridge", [("response", "numFound")], None, False,
     lambda c: earth.search_cmip(c, model="CanESM5", experiment="historical",
                                 variable="tas", rows=5)),
    # The cursor is a has-more signal of its own, so it goes too: no total
    # AND no cursor is the case where nobody knows.
    ("vips", "vips.pnnl.gov/api/v1/ip/search",
     [("total",), ("next_cursor",)], None, False,
     lambda c: tech.find_licensable_ip(c, lab="PNNL", rows=5)),
    ("optimade", "/v1/structures",
     [("meta", "data_returned"), ("meta", "more_data_available")], None,
     False,
     lambda c: materials.search_structures(c, elements="Ga,N", rows=5)),
    ("osti_family", "osti.gov", [], "x-total-count", False,
     lambda c: research.search_literature(c, query="perovskite solar",
                                          rows=3)),
    ("postgrest", "uswtdb/v1/turbines", [(0, "count")], None, False,
     lambda c: energy.find_wind_turbines(c, state="RI", rows=5)),
    ("federal_register", "documents.json", [("count",)], None, False,
     lambda c: docs.search_rulemakings(c, document_type="RULE", rows=5)),
]


@pytest.mark.parametrize("adapter,url_part,paths,header,keyed,call",
                         MISSING_TOTAL, ids=[c[0] for c in MISSING_TOTAL])
async def test_a_missing_total_is_unknown_never_complete(
        adapter, url_part, paths, header, keyed, call, tmp_path):
    replay = _without(merged_replay(), url_part, *paths, header=header)
    ctx = _context(replay, _credentials(tmp_path, keyed))
    env = await call(ctx)
    assert env.coverage.pagination is P.unknown, (
        f"{adapter}: with the publisher's total stripped from its recorded "
        f"response the tool reported pagination="
        f"{env.coverage.pagination.value}; the only true answer is unknown.")
    if "total_matches" in env.data:
        assert env.data["total_matches"] is None


@pytest.mark.parametrize("adapter,url_part,paths,header,keyed,call",
                         MISSING_TOTAL, ids=[c[0] for c in MISSING_TOTAL])
async def test_the_recorded_total_is_still_read(adapter, url_part, paths,
                                                header, keyed, call,
                                                tmp_path):
    """The control: the same call over the untouched recording reports a
    definite state, so the sweep above is stripping something real."""
    ctx = _context(merged_replay(), _credentials(tmp_path, keyed))
    env = await call(ctx)
    assert env.coverage.pagination is not P.unknown
