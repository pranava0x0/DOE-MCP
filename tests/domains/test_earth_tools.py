"""The six earth tools: what the answer says about what was actually asked.

The adapters refuse a wrong request before it leaves. These tests are about
the other half — what a caller has to be able to read in the answer to avoid
drawing a wrong conclusion from a right one: the range that came back
against the range asked for, the absent day, the retracted datasets in a
page, the nodes a proximity search could not place, and the difference
between "nothing matched" and "we could not look".
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (PaginationCoverage, RegistryCoverage,
                                   ResultCoverage, WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import earth


async def test_a_point_series_carries_dates_units_and_the_grid_caveat(ctx):
    env = await earth.get_daymet_point(ctx, 35.9313, -84.3104, "2023-06-01",
                                       "2023-06-05", variables="prcp,tmax,tmin")
    assert env.data["record_count"] == 5
    assert env.data["days"][0]["date"] == "2023-06-01"
    assert env.data["days"][0]["values"]["tmax"] == 28.62
    units = {c["name"]: c["units"] for c in env.data["columns"]}
    assert units["prcp"] == "mm/day"
    assert any(w.code == WarningCode.derived_layer for w in env.warnings), (
        "a modelled 1 km cell reported as a measurement at the point is the "
        "one wrong conclusion this tool exists to prevent")


async def test_the_answer_names_the_day_the_dataset_does_not_have(ctx):
    env = await earth.get_daymet_point(ctx, 35.9313, -84.3104, "2024-12-26",
                                       "2024-12-31", variables="tmax")
    assert env.data["dates_absent"] == ["2024-12-31"]
    assert env.data["returned_range"]["to"] == "2024-12-30"
    assert env.data["requested_range"]["to"] == "2024-12-31"
    assert "365-day total" in env.data["dates_absent_note"]


async def test_the_publishers_own_citation_reaches_the_answer(ctx):
    env = await earth.get_daymet_point(ctx, 35.9313, -84.3104, "2023-06-01",
                                       "2023-06-05",
                                       variables="prcp,tmax,tmin")
    assert "ORNLDAAC/2129" in env.data["publisher_citation"]
    entry = env.provenance[0]
    assert entry.funder == "National Aeronautics and Space Administration", (
        "NASA funds this archive; attributing it to DOE alone is the "
        "overclaim the publisher triple exists to prevent")


async def test_a_dataset_search_reports_what_the_service_actually_ran(ctx):
    env = await earth.search_datasets(ctx, text="permafrost", rows=5)
    assert env.data["record_count"] == 5
    assert env.data["searched"]["text"] == "permafrost"
    assert env.coverage.pagination == PaginationCoverage.truncated
    assert env.data["total_matches"] > 5


async def test_nothing_matched_is_an_empty_result_not_a_failure(ctx):
    """The service answers this with HTTP 404. `registry: covered` with
    `result: empty` is the honest reading — the repository was searched and
    holds no such record."""
    env = await earth.search_datasets(ctx, text="zzzznotarealtopiczzzz",
                                      rows=5)
    assert env.coverage.result == ResultCoverage.empty
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.data["record_count"] == 0


async def test_one_dataset_carries_its_licence_funder_and_methods(ctx):
    env = await earth.get_dataset(
        ctx, "ess-dive-5c6fb3269616635-20260906T172343833")
    assert env.data["doi"] == "doi:10.15485/2283980"
    assert env.data["license"].startswith("http")
    assert any("Biological and Environmental Research" in f
               for f in env.data["funders"])
    assert env.data["methods"]
    assert any(r.uri == env.data["view_url"] for r in env.access_recipes), (
        "the API returns metadata only; the files are behind the landing "
        "page and the answer has to say so")


async def test_the_facet_walk_lists_what_can_be_searched(ctx):
    env = await earth.discover_facets(ctx)
    names = {f["name"] for f in env.data["facets"]}
    assert {"source_id", "experiment_id", "variable_id"} <= names
    assert "limit" not in names
    assert env.data["facet_count"] > 50


async def test_one_facets_values_come_back_with_their_counts(ctx):
    env = await earth.discover_facets(ctx, facet="source_id")
    values = {v["value"]: v["datasets"] for v in env.data["values"]}
    assert values["CanESM5"] > 1000
    assert env.data["values"][0]["datasets"] >= env.data["values"][1][
        "datasets"], "ordered by how much output each carries"


async def test_an_unknown_facet_is_refused_with_the_walk_named(ctx):
    with pytest.raises(InvalidQuery, match="climate.discover_facets"):
        await earth.discover_facets(ctx, facet="not_a_facet")


async def test_a_cmip_search_defaults_to_the_current_versions(ctx):
    env = await earth.search_cmip(ctx, model="CanESM5",
                                  experiment="historical", variable="tas",
                                  rows=5)
    assert env.data["latest_only"] is True
    assert all(d["retracted"] is False for d in env.data["datasets"])
    assert "retracted_in_page" not in env.data
    assert 'source_id:"CanESM5"' in env.data["applied_filters"]


async def test_retracted_output_is_counted_and_warned_about(ctx):
    """Superseded output is in the index and sorts first. A caller who asked
    for it gets it, with the count and a warning — silently returning it is
    what the default guards against, and silently filtering it when it was
    asked for would be the other half of the same mistake."""
    env = await earth.search_cmip(ctx, model="CanESM5",
                                  experiment="historical", variable="tas",
                                  include_superseded=True, rows=5)
    assert env.data["retracted_in_page"] > 0
    assert any(w.code == WarningCode.stale_source for w in env.warnings)
    assert env.data["total_matches"] > 0


async def test_a_climate_record_names_the_node_holding_the_files(ctx):
    env = await earth.search_cmip(ctx, model="CanESM5",
                                  experiment="historical", variable="tas",
                                  rows=5)
    first = env.data["datasets"][0]
    assert first["data_node"]
    assert first["variable_units"] == ["K"]
    assert first["covers"]["from"] and first["covers"]["to"]
    assert env.access_recipes, (
        "this is a catalog of metadata; the output lives elsewhere and the "
        "answer has to point at it")


async def test_a_malformed_filter_clause_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="facet=value"):
        await earth.search_cmip(ctx, filters="grid_label", rows=5)


async def test_the_node_inventory_says_it_is_not_the_measurements(ctx):
    env = await earth.find_nodes(ctx, rows=50)
    assert env.data["record_count"] == env.data["nodes_in_network"]
    assert "inventory" in env.data["note"].lower()
    assert "POST" in env.data["measurements"]
    assert any("sagecontinuum" in r.uri for r in env.access_recipes), (
        "a caller who wants readings should be handed the endpoint rather "
        "than an empty result")


async def test_a_proximity_search_reports_what_it_could_not_place(ctx):
    env = await earth.find_nodes(ctx, latitude=41.98, longitude=-87.71,
                                 rows=50)
    assert env.data["without_coordinates"] > 0
    assert "awaiting deployment" in env.data["without_coordinates_note"]
    assert env.data["sorted_by"] == "distance"


async def test_half_a_coordinate_pair_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="both latitude and longitude"):
        await earth.find_nodes(ctx, latitude=41.98, rows=50)
