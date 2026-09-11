"""Sabotage tests for the one activation gate.

A source flipped to `proposed` in memory, an anti-automation posture, or a
runtime outage has to stop every tool that names the source, not only
capability selection. Before the gate, the fixed-source tools checked only
that the manifest existed: a vehicle lookup against a source set to proposed
still answered with 44 rows.

Provenance is checked in the same file because it fails the same way: a tool
that reports its own clock instead of the fetch's says "live, age 0" about an
answer that came out of the cache.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
from doe_mcp.adapters.osti_family import OstiFamilyAdapter
from doe_mcp.core.envelope import AccessPath
from doe_mcp.core.errors import (SourceNotActivated, SourceUnavailable,
                                 TermsRestricted)
from doe_mcp.core.registry import (AutomationStatus, DeclaredState,
                                   OperationalState)
from doe_mcp.domains import (discovery, docs, earth, energy, materials,
                             registry_tools, research, tech)

FIXED_SOURCE_TOOLS = [
    ("osti-data-explorer", "research.search_datasets",
     lambda ctx: research.search_datasets(ctx, site_code="DOE-GDR", rows=2)),
    ("osti-doe-code", "research.search_software",
     lambda ctx: research.search_software(ctx, query="machine learning",
                                          rows=3)),
    ("osti-gov-records", "research.get_record",
     lambda ctx: research.get_record(ctx, "1", collection="literature")),
    ("osti-doe-pages", "research.get_record",
     lambda ctx: research.get_record(ctx, "1", collection="pages")),
    ("eia-api-v2", "energy.discover_routes",
     lambda ctx: energy.discover_routes(ctx)),
    ("eia-api-v2", "energy.get_data",
     lambda ctx: energy.get_data(ctx, "electricity/rto/region-data")),
    ("eia-api-v2", "energy.grid_status",
     lambda ctx: energy.grid_status(ctx, "CISO")),
    ("fueleconomy-ws", "fuel.find_vehicle",
     lambda ctx: energy.find_vehicle(ctx)),
    ("fueleconomy-ws", "fuel.get_prices",
     lambda ctx: energy.get_fuel_prices(ctx)),
    ("bpa-operations", "grid.get_bpa_operations",
     lambda ctx: energy.get_bpa_operations(ctx)),
    ("usgs-uswtdb", "facility.find_wind_turbines",
     lambda ctx: energy.find_wind_turbines(ctx, state="RI", rows=5)),
    ("usgs-uspvdb", "facility.find_solar_facilities",
     lambda ctx: energy.find_solar_facilities(ctx, state="RI", rows=5)),
    ("federal-register-doe", "docs.search_rulemakings",
     lambda ctx: docs.search_rulemakings(ctx, document_type="RULE", rows=5)),
    ("federal-register-doe", "docs.get_rulemaking",
     lambda ctx: docs.get_rulemaking(ctx, "2026-17979")),
    ("pnnl-vips", "tech.find_licensable_ip",
     lambda ctx: tech.find_licensable_ip(ctx, lab="PNNL", rows=5)),
    ("ornl-daac", "earth.get_daymet_point",
     lambda ctx: earth.get_daymet_point(ctx, 35.9313, -84.3104,
                                        "2023-06-01", "2023-06-05",
                                        variables="tmax")),
    ("ess-dive", "earth.search_datasets",
     lambda ctx: earth.search_datasets(ctx, text="permafrost", rows=5)),
    ("ess-dive", "earth.get_dataset",
     lambda ctx: earth.get_dataset(
         ctx, "ess-dive-5c6fb3269616635-20260906T172343833")),
    ("esgf-search", "climate.discover_facets",
     lambda ctx: earth.discover_facets(ctx)),
    ("esgf-search", "climate.search_cmip",
     lambda ctx: earth.search_cmip(ctx, model="CanESM5",
                                   experiment="historical", variable="tas",
                                   rows=5)),
    ("anl-sage-waggle", "sensors.find_nodes",
     lambda ctx: earth.find_nodes(ctx, rows=50)),
    ("lbnl-mp-optimade", "materials.describe_structure_fields",
     lambda ctx: materials.describe_structure_fields(ctx)),
    ("lbnl-mp-optimade", "materials.search_structures",
     lambda ctx: materials.search_structures(ctx, elements="Ga,N", rows=5)),
    ("lbnl-mp-optimade", "materials.get_structure",
     lambda ctx: materials.get_structure(ctx, "mp-1244984")),
    ("basis-set-exchange", "chemistry.search_basis_sets",
     lambda ctx: materials.search_basis_sets(ctx, family="pople")),
    ("basis-set-exchange", "chemistry.get_basis_set",
     lambda ctx: materials.get_basis_set(ctx, "6-31g",
                                         output_format="nwchem",
                                         elements="H,C,O")),
    ("doe-pure-resources", "discovery.list_pure_resources",
     lambda ctx: discovery.list_pure_resources(ctx)),
    ("doe-mcp-registry", "registry.lab_crosswalk",
     lambda ctx: registry_tools.lab_crosswalk(ctx)),
]
IDS = [f"{tool}<-{source}" for source, tool, _ in FIXED_SOURCE_TOOLS]


def _deactivate(ctx, source_id: str) -> None:
    m = ctx.sources.get(source_id)
    m.lifecycle.declared_state = DeclaredState.proposed
    m.lifecycle.blocked_reason = "flipped in memory by the sabotage test"


@pytest.mark.parametrize("source_id,tool,call", FIXED_SOURCE_TOOLS, ids=IDS)
async def test_a_proposed_source_is_refused_by_every_tool_that_names_it(
        ctx, source_id, tool, call):
    _deactivate(ctx, source_id)
    with pytest.raises(SourceNotActivated) as err:
        await call(ctx)
    assert "not active" in str(err.value)
    assert "flipped in memory" in str(err.value), (
        "the refusal must carry the manifest's blocked_reason")


@pytest.mark.parametrize("source_id,tool,call", FIXED_SOURCE_TOOLS, ids=IDS)
async def test_an_anti_automation_posture_is_refused_as_terms(
        ctx, source_id, tool, call):
    m = ctx.sources.get(source_id)
    m.access.automation_status = AutomationStatus.outreach_pending
    with pytest.raises(TermsRestricted, match="never queried"):
        await call(ctx)


@pytest.mark.parametrize("source_id,tool,call", FIXED_SOURCE_TOOLS, ids=IDS)
async def test_a_source_marked_unavailable_at_runtime_is_not_queried(
        ctx, source_id, tool, call):
    ctx.sources.set_operational(source_id, OperationalState.unavailable)
    with pytest.raises(SourceUnavailable, match="health probes"):
        await call(ctx)


async def test_literature_search_keeps_answering_from_the_live_collection(ctx):
    _deactivate(ctx, "osti-doe-pages")
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    assert env.coverage.sources_searched == ["osti-gov-records"]
    assert {(g.source_id, g.reason) for g in env.coverage.sources_unavailable} \
        == {("osti-doe-pages", "source_not_activated")}
    assert {p.source_id for p in env.provenance} == {"osti-gov-records"}
    assert env.data["record_count"] > 0


async def test_literature_search_with_no_live_collection_refuses(ctx):
    _deactivate(ctx, "osti-doe-pages")
    _deactivate(ctx, "osti-gov-records")
    with pytest.raises(SourceNotActivated):
        await research.search_literature(ctx, query="perovskite solar")


async def test_the_catalog_fan_out_names_a_deactivated_catalog(ctx):
    _deactivate(ctx, "ornl-openenergyhub")
    env = await discovery.search_all_catalogs(ctx, query="geothermal",
                                              per_catalog=2)
    assert "ornl-openenergyhub" not in env.coverage.sources_searched
    assert ("ornl-openenergyhub", "source_not_activated") in {
        (g.source_id, g.reason) for g in env.coverage.sources_unavailable}
    assert not any(c["catalog"] == "ornl-openenergyhub"
                   for c in env.data["per_catalog"])
    assert env.data["record_count"] > 0, "the other catalogs still answer"


async def test_capability_selection_and_the_gate_agree(ctx):
    """Both routes into a source read the same predicate."""
    _deactivate(ctx, "fueleconomy-ws")
    assert ctx.sources.select("vehicle.find") == []
    assert ("fueleconomy-ws", "source_not_activated") in \
        ctx.sources.unavailable_for("vehicle.find")


# --- provenance comes from the fetch, not from the tool's clock ------------

async def test_vehicle_menu_provenance_reports_the_cache_on_a_repeat(ctx):
    first = await energy.find_vehicle(ctx)
    second = await energy.find_vehicle(ctx)
    assert first.provenance[0].access_path is AccessPath.live
    assert second.provenance[0].access_path is AccessPath.cache
    assert second.provenance[0].retrieved_at == first.provenance[0].retrieved_at
    assert second.evidence[0].retrieved_at == first.provenance[0].retrieved_at


async def test_fuel_prices_provenance_reports_the_cache_on_a_repeat(ctx):
    first = await energy.get_fuel_prices(ctx)
    second = await energy.get_fuel_prices(ctx)
    assert first.provenance[0].access_path is AccessPath.live
    assert second.provenance[0].access_path is AccessPath.cache


async def test_record_lookup_provenance_comes_from_the_fetch(ctx):
    class OneRecord:
        async def fetch_json(self, url, params):
            return JsonResponse(payload=[{"osti_id": "1", "title": "T",
                                          "entry_date": "2026-01-01"}],
                                headers={}, url=url)

    ctx.osti = OstiFamilyAdapter(fetcher=OneRecord(), cache=TTLCache())
    first = await research.get_record(ctx, "1")
    second = await research.get_record(ctx, "1")
    assert first.provenance[0].access_path is AccessPath.live
    assert second.provenance[0].access_path is AccessPath.cache
    assert second.provenance[0].retrieved_at == first.provenance[0].retrieved_at


async def test_search_sources_finds_a_proposed_source_by_planned_capability(ctx):
    """"Does DOE-MCP cover geothermal?" is answered by the proposed GDR
    manifest and its blocked_reason, which beats an empty result."""
    env = await registry_tools.search_sources(ctx, capability="dataset.search",
                                             state="proposed")
    ids = [s["id"] for s in env.data["sources"]]
    assert "nlr-geothermal-data-repository" in ids
    row = next(s for s in env.data["sources"]
               if s["id"] == "nlr-geothermal-data-repository")
    assert row["declared_state"] == "proposed"
    assert row["blocked_reason"]
    assert "dataset.search" in row["planned_capabilities"]
