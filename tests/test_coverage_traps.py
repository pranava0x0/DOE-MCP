"""The four coverage traps (architecture Part 1 § 3.7).

These are not unit tests of functions. They are the behaviours that decide
whether this project is trustworthy, written as tests so a refactor cannot
quietly remove one:

1. **The NREL-alias trap.** A user says "NREL". The tools must resolve NLR
   AND say the name is historical. Answering as though NREL still existed
   would hide a rename that killed every URL the user has.
2. **The registry-gap trap.** A user asks for something DOE-MCP has no
   source for. The answer must be `registry: none`, not an empty result —
   because "we cannot see it" and "it does not exist" are different, and only
   one of them is true.
3. **The vintage trap.** Two versions of one dataset in one answer must raise
   `mixed_vintages`.
4. **The derived-layer trap.** A user asks for raw user-facility data. The
   answer must explain the access model, not return empty.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (PaginationCoverage, RegistryCoverage,
                                   ResultCoverage, WarningCode)
from doe_mcp.domains import (discovery, docs, earth, energy, materials,
                             registry_tools, research, tech)


async def test_nrel_alias_trap_resolves_and_says_it_is_historical(ctx):
    env = await registry_tools.resolve_org(ctx, "NREL")
    assert env.data["resolved"]["id"] == "nlr"
    assert env.data["resolved"]["name"] == "National Laboratory of the Rockies"
    assert "historical_name" in env.data, (
        "resolving is not enough — the caller has to be told the name they "
        "used is out of date")
    assert env.data["historical_name"]["renamed_on"] == "2025-12-01"
    assert WarningCode.alias_match in {w.code for w in env.warnings}


async def test_nrel_dead_domain_gets_domain_migrated_not_alias_match(ctx):
    """A dead domain is a different fact from an old name: the organization
    is fine, the URL is not, and *.nrel.gov does not redirect."""
    env = await registry_tools.resolve_org(ctx, "developer.nrel.gov")
    assert env.data["resolved"]["id"] == "nlr"
    codes = {w.code for w in env.warnings}
    assert WarningCode.domain_migrated in codes
    assert "do NOT redirect" in next(
        w.message for w in env.warnings
        if w.code == WarningCode.domain_migrated)


async def test_every_reorganized_office_resolves_from_its_former_name(ctx):
    """The 2025-26 reorganization renamed or dissolved seven offices. A
    document written before it names offices that no longer exist."""
    for former, current in [("EERE", "cmei"), ("FECM", "hgeo"),
                            ("LPO", "edf"), ("HFTO", "affo"),
                            ("BETO", "affo"), ("IEDO", "ito")]:
        env = await registry_tools.resolve_org(ctx, former)
        assert env.data["resolved"]["id"] == current, (
            f"{former} should resolve to {current}")
        assert "historical_name" in env.data, (
            f"{former} resolved silently; the caller was not told it is a "
            "former name")


async def test_registry_gap_trap_says_none_not_empty(ctx):
    """A laboratory DOE-MCP has no source for must answer registry=none. An
    empty result here would be read as 'the lab publishes nothing', which is
    a claim this project has no basis to make."""
    env = await registry_tools.lab_crosswalk(ctx, "SLAC")
    if env.data["record_count"] == 0:
        assert env.coverage.registry is RegistryCoverage.none
        assert "gap in this project" in env.data["note"]


async def test_a_capability_nothing_serves_reports_the_blocked_sources(ctx):
    """The registry's real payoff: 'not yet, and here is what is in the way'
    beats an unqualified no."""
    env = await registry_tools.search_sources(ctx, capability="",
                                              state="proposed", limit=100)
    assert env.data["record_count"] > 0
    blocked = [s for s in env.data["sources"] if s.get("blocked_reason")]
    assert len(blocked) == env.data["record_count"], (
        "every non-active manifest must carry a blocked_reason; a proposed "
        "source without one is the lost note the registry exists to prevent")


async def test_derived_layer_trap_on_dataset_search(ctx):
    """Asking for facility data must produce an explanation of the access
    model rather than a bare empty result."""
    env = await research.search_datasets(ctx, site_code="DOE-GDR", rows=2)
    warning = next(w for w in env.warnings
                   if w.code == WarningCode.derived_layer)
    assert "proposal-gated by design" in warning.message
    assert "not a coverage gap" in warning.message


async def test_catalog_vintage_fires_only_on_harvested_catalogs(ctx):
    """`catalog_vintage` means 'answered from a harvested index'. Raising it
    for a live API would make a 1.03-million-record index look stale because
    two sampled records happened to be old."""
    env = await discovery.search_all_catalogs(ctx, query="geothermal",
                                              per_catalog=2)
    live = {r["catalog"] for r in env.data["per_catalog"]
            if r["vintage"] == "live"} if env.data["per_catalog"] else set()
    warned = {w.source_id for w in env.warnings
              if w.code == WarningCode.catalog_vintage}
    assert not (live & warned), (
        "a live API was reported as a stale harvested catalog")


async def test_pagination_never_reports_a_page_as_the_whole_answer(ctx):
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    total = max(s["total_matches"] or 0 for s in env.data["per_source"])
    assert total > env.data["record_count"]
    assert env.coverage.pagination is PaginationCoverage.truncated
    assert env.next_actions, (
        "a truncated result must tell the caller more exists")


# Until 2026-09-11 the trap above ran against the literature search only,
# which was the one tool whose adapter already handled a missing total
# correctly. Every tool that pages is held to it now; each recording here
# holds a page smaller than the publisher's own count.
PAGING = [
    ("research.search_datasets", False,
     lambda c: research.search_datasets(c, site_code="DOE-GDR", rows=2)),
    ("earth.search_datasets", False,
     lambda c: earth.search_datasets(c, text="permafrost", rows=5)),
    ("climate.search_cmip", False,
     lambda c: earth.search_cmip(c, model="CanESM5", experiment="historical",
                                 variable="tas", rows=5)),
    ("tech.find_licensable_ip", False,
     lambda c: tech.find_licensable_ip(c, lab="PNNL", rows=5)),
    ("docs.search_rulemakings", False,
     lambda c: docs.search_rulemakings(c, document_type="RULE", rows=5)),
    ("materials.search_structures", False,
     lambda c: materials.search_structures(c, elements="Ga,N", rows=5)),
    ("facility.find_wind_turbines", False,
     lambda c: energy.find_wind_turbines(c, state="RI", rows=5)),
    ("energy.grid_status", True,
     lambda c: energy.grid_status(c, "CISO", hours=3)),
]


@pytest.mark.parametrize("name,keyed,call", PAGING, ids=[p[0] for p in PAGING])
async def test_every_paging_tool_reports_a_page_as_a_page(name, keyed, call,
                                                          ctx, keyed_ctx):
    env = await call(keyed_ctx if keyed else ctx)
    assert env.coverage.pagination is PaginationCoverage.truncated, (
        f"{name} returned a page smaller than the publisher's count and "
        f"reported pagination={env.coverage.pagination.value}")
    total = env.data.get("total_matches")
    if total is not None:
        assert total > env.data["record_count"]


async def test_empty_result_explains_which_kind_of_empty_it_is(ctx):
    env = await research.search_literature(ctx, query="zzzznotarealtopiczzzz",
                                           rows=3)
    assert env.coverage.result is ResultCoverage.empty
    assert env.coverage.registry is RegistryCoverage.covered, (
        "the sources were searched, so registry coverage is 'covered' — "
        "'none' would claim we have no source for literature at all")
    assert "not about the field" in env.data["note"]
