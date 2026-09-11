"""The four earth adapters, against what their publishers actually return.

Three of these services answer a wrong request with data. Every test that
matters here is about that: what the adapter refuses before the request
leaves, and what it reports about the answer that came back.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.daymet import MAX_DAYS, parse_series
from doe_mcp.adapters.essdive import parse_package
from doe_mcp.core.errors import (InvalidQuery, SourceSchemaChanged)

DAYMET = "ornl-daac"
ESSDIVE = "ess-dive"
ESGF = "esgf-search"
SAGE = "anl-sage-waggle"


async def _daymet(ctx, **kwargs):
    return (await ctx.daymet.point_series(
        ctx.sources.get(DAYMET), latitude=35.9313, longitude=-84.3104,
        **kwargs)).value


# --- Daymet ---------------------------------------------------------------

async def test_a_day_number_becomes_a_calendar_date(ctx):
    series = await _daymet(ctx, variables=["prcp", "tmax", "tmin"],
                           start="2023-06-01", end="2023-06-05")
    assert [r.date for r in series.rows] == [
        "2023-06-01", "2023-06-02", "2023-06-03", "2023-06-04", "2023-06-05"]
    assert series.rows[0].yday == 152


async def test_the_last_day_of_a_leap_year_is_absent_and_named(ctx):
    """Daymet keeps 29 February and drops 31 December, so its year is always
    365 days. A caller summing a leap year is summing 365 days, and finding
    that out from the arithmetic is finding it out too late."""
    series = await _daymet(ctx, variables=["tmax"], start="2024-12-26",
                           end="2024-12-31")
    assert series.returned_end == "2024-12-30"
    assert series.rows[-1].yday == 365
    assert series.missing_leap_days() == ["2024-12-31"]


async def test_units_come_from_the_column_header(ctx):
    """srad is W/m^2 and swe is kg/m^2 on adjacent columns. A hardcoded
    units table would be wrong in the units rather than in the numbers,
    which is the harder error to see."""
    series = await _daymet(ctx, variables=["prcp", "tmax", "tmin"],
                           start="2023-06-01", end="2023-06-05")
    units = {c.name: c.units for c in series.columns}
    assert units == {"prcp": "mm/day", "tmax": "deg c", "tmin": "deg c"}


async def test_the_preamble_carries_elevation_tile_and_citation(ctx):
    series = await _daymet(ctx, variables=["tmax"], start="2024-12-26",
                           end="2024-12-31")
    assert series.elevation_meters == 261
    assert series.tile == "11208"
    assert series.citation and "ORNLDAAC/2129" in series.citation


async def test_an_unknown_variable_is_refused_before_the_request(ctx):
    """The service answers `vars=notavar` with ALL eight variables at HTTP
    200. A caller who typed `tmean` would get eight columns of real data and
    nothing saying the request was not the one that ran."""
    with pytest.raises(InvalidQuery, match="unknown Daymet variable"):
        await _daymet(ctx, variables=["tmean"], start="2023-06-01",
                      end="2023-06-05")


async def test_a_year_before_the_record_is_refused_rather_than_moved(ctx):
    """Asking for 1970 returns 1980's numbers under the year asked for."""
    with pytest.raises(InvalidQuery, match="begins in 1980"):
        await _daymet(ctx, variables=["tmax"], start="1970-01-01",
                      end="1970-01-05")


async def test_a_span_wider_than_the_client_ceiling_is_refused(ctx):
    with pytest.raises(InvalidQuery, match=f"at most {MAX_DAYS}"):
        await _daymet(ctx, variables=["tmax"], start="1990-01-01",
                      end="2020-01-01")


async def test_a_reversed_range_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="is after end"):
        await _daymet(ctx, variables=["tmax"], start="2023-06-05",
                      end="2023-06-01")


def test_a_response_with_no_header_row_is_a_schema_change():
    with pytest.raises(SourceSchemaChanged, match="column-header row"):
        parse_series("<html>bot challenge</html>", DAYMET,
                     requested_start="2023-06-01", requested_end="2023-06-05")


# --- ESS-DIVE -------------------------------------------------------------

async def _search(ctx, **kwargs):
    """The search the earth tool actually sends, sort included: a fixture is
    keyed on (url, params), so a test that dropped an argument the tool
    always passes would be replaying a request nothing makes."""
    return (await ctx.essdive.search(ctx.sources.get(ESSDIVE), rows=5,
                                     sort="dateUploaded:desc",
                                     **kwargs)).value


async def test_an_empty_result_set_is_not_an_outage(ctx):
    """This service answers a search that matched nothing with HTTP 404.
    Left alone that reaches a caller as SourceUnavailable — "outage, not an
    empty result" — which is the exact inversion the coverage dimensions
    exist to prevent."""
    page = await _search(ctx, filters={"text": "zzzznotarealtopiczzzz"})
    assert page.packages == []
    assert page.total == 0


async def test_a_filter_this_source_does_not_take_is_refused(ctx):
    """An unknown parameter is ignored, and the whole catalog comes back
    under the caller's own search terms."""
    with pytest.raises(InvalidQuery, match="not a filter this source takes"):
        await _search(ctx, filters={"notaparam": "x"})


async def test_a_filter_missing_from_the_services_echo_is_a_schema_change(
        ctx, monkeypatch):
    """The echo is the only signal that a filter was dropped. If the service
    stops understanding `text`, the answer would be the unfiltered catalog
    presented as a filtered search — which is worse than an error."""
    adapter = ctx.essdive
    real = adapter._parse_page.__func__ if hasattr(
        adapter._parse_page, "__func__") else adapter._parse_page
    page = await _search(ctx, filters={"text": "permafrost"})
    assert page.applied_query.get("text") == "permafrost"
    with pytest.raises(SourceSchemaChanged, match="echo of the query"):
        real({"result": [], "query": {"isPublic": True}, "total": 0},
             ESSDIVE, {"text": "permafrost"})


async def test_a_keyword_search_is_narrower_than_a_text_search(ctx):
    """Two filters over the same word, and they are not the same question:
    keywords matches the depositor's own list, text matches the record."""
    by_text = await _search(ctx, filters={"text": "permafrost"})
    by_keyword = await _search(ctx, filters={"keywords": "permafrost"})
    assert by_keyword.total < by_text.total


async def test_offsets_are_converted_to_the_publishers_1_based_rows(ctx):
    page = await _search(ctx, filters={"text": "permafrost"})
    assert page.row_start == 1


async def test_creator_email_addresses_do_not_leave_the_adapter(ctx):
    """The publisher's records carry them. Republishing a researcher's
    address in an answer is a different act from the publisher exposing it
    through its own API."""
    package = (await ctx.essdive.get_package(
        ctx.sources.get(ESSDIVE),
        "ess-dive-5c6fb3269616635-20260906T172343833")).value
    assert package.creators
    assert not any("@" in (p.name + (p.affiliation or "") + (p.orcid or ""))
                   for p in package.creators)


async def test_a_package_id_that_is_a_path_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="not an ESS-DIVE package id"):
        await ctx.essdive.get_package(ctx.sources.get(ESSDIVE), "../etc")


def test_a_record_with_no_dataset_object_is_a_schema_change():
    with pytest.raises(SourceSchemaChanged, match="no 'dataset' object"):
        parse_package({"id": "x"}, ESSDIVE)


# --- ESGF -----------------------------------------------------------------

async def _schema(ctx):
    return (await ctx.esgf.describe(ctx.sources.get(ESGF))).value


async def test_the_facet_list_comes_from_the_published_schema(ctx):
    schema = await _schema(ctx)
    assert "source_id" in schema.facets
    assert "experiment_id" in schema.facets
    # Control parameters steer the request; they are not facets to search on.
    assert "limit" not in schema.facets and "limit" in schema.controls
    assert len(schema.facets) > 50


async def test_an_unknown_facet_is_refused_against_that_schema(ctx):
    schema = await _schema(ctx)
    with pytest.raises(InvalidQuery, match="unknown ESGF facet"):
        schema.check(["not_a_facet"])


async def test_the_latest_flag_is_what_keeps_retracted_output_out(ctx):
    """Without it the index returns 456 records for this triple whose first
    two are retracted; with it, 366 and none. A retraction is a modelling
    centre withdrawing output, often for an error in the run.

    `latest=None` here is the absence of the parameter, not `latest=false`:
    the flag is three-valued and false means "only the superseded ones",
    which is 90 records rather than 456.
    """
    schema = await _schema(ctx)
    triple = {"project": "CMIP6", "source_id": "CanESM5",
              "experiment_id": "historical", "variable_id": "tas"}
    latest = (await ctx.esgf.search(ctx.sources.get(ESGF), schema=schema,
                                    filters=triple, rows=5)).value
    everything = (await ctx.esgf.search(ctx.sources.get(ESGF), schema=schema,
                                        filters=triple, rows=5,
                                        latest=None)).value
    assert latest.num_found < everything.num_found
    assert latest.retracted_count == 0
    assert everything.retracted_count > 0


async def test_paging_stops_where_the_publishers_own_schema_says_it_does(ctx):
    schema = await _schema(ctx)
    with pytest.raises(InvalidQuery, match="narrowing the facets"):
        await ctx.esgf.search(ctx.sources.get(ESGF), schema=schema,
                              filters={"project": "CMIP6"}, offset=20000)


async def test_solr_facet_counts_are_paired_up(ctx):
    """Solr returns [value, count, value, count]; an odd read would pair
    every count with the following value's name."""
    schema = await _schema(ctx)
    page = (await ctx.esgf.search(ctx.sources.get(ESGF), schema=schema,
                                  filters={"project": "CMIP6"},
                                  facets=["source_id"], rows=0)).value
    counts = page.facet_counts["source_id"]
    assert counts["CanESM5"] > 1000
    assert all(isinstance(v, int) for v in counts.values())


async def test_a_dataset_record_carries_its_variable_units_and_extent(ctx):
    schema = await _schema(ctx)
    page = (await ctx.esgf.search(
        ctx.sources.get(ESGF), schema=schema,
        filters={"project": "CMIP6", "source_id": "CanESM5",
                 "experiment_id": "historical", "variable_id": "tas"},
        rows=5)).value
    first = page.datasets[0]
    assert first.variables == ["tas"]
    assert first.variable_units == ["K"]
    assert first.datetime_start and first.datetime_stop
    assert first.data_node


async def test_the_end_of_the_covered_period_is_read_under_either_name(ctx):
    """This index names one fact two ways. CanESM5's records carry
    `datetime_stop` and GFDL-ESM4's carry `datetime_end`, on the same day in
    the same index, and reading only the first left the end of the covered
    period null for a large share of the archive with nothing saying so."""
    schema = await _schema(ctx)
    both = []
    for triple in ({"source_id": "CanESM5", "experiment_id": "historical"},
                   {"source_id": "GFDL-ESM4", "experiment_id": "ssp585"}):
        page = (await ctx.esgf.search(
            ctx.sources.get(ESGF), schema=schema,
            filters={"project": "CMIP6", "variable_id": "tas"} | triple,
            rows=5)).value
        both.append(page.datasets[0])
    assert all(d.datetime_start and d.datetime_stop for d in both)
    assert both[0].datetime_stop != both[1].datetime_stop


async def test_a_portal_html_shell_is_reported_as_a_moved_endpoint(ctx):
    """The same path on the new host answers 200 with the web portal's
    React shell. A checker reading status codes would call that healthy."""
    with pytest.raises(SourceSchemaChanged, match="no 'response' block"):
        ctx.esgf._parse_page("<!DOCTYPE html>", ESGF, 0)


# --- Sage -----------------------------------------------------------------

async def _nodes(ctx, **kwargs):
    return (await ctx.sage.nodes(ctx.sources.get(SAGE), rows=50,
                                 **kwargs)).value


async def test_nodes_carry_their_instruments(ctx):
    page = await _nodes(ctx)
    assert page.nodes
    with_sensors = [n for n in page.nodes if n.sensors]
    assert with_sensors
    assert any(s.manufacturer for n in with_sensors for s in n.sensors)


async def test_an_instrument_filter_matches_hardware_or_name(ctx):
    page = await _nodes(ctx, instrument="bme680")
    assert page.nodes
    assert all(any("bme680" in (s.hardware or "") for s in n.sensors)
               for n in page.nodes)


async def test_a_proximity_search_keeps_the_nodes_it_cannot_place(ctx):
    """Nodes awaiting deployment carry no coordinates. Dropping them would
    report a network smaller than it is without saying so."""
    page = await _nodes(ctx, near=(41.98, -87.71))
    assert page.without_coordinates > 0
    placed = [n for n in page.nodes if n.latitude is not None]
    assert page.nodes[:len(placed)] == placed


async def test_a_radius_excludes_the_unplaced_rather_than_ranking_them(ctx):
    page = await _nodes(ctx, near=(41.98, -87.71), within_km=50)
    assert all(n.latitude is not None for n in page.nodes)
    assert page.without_coordinates == 0


async def test_a_radius_without_a_point_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="needs a point"):
        await _nodes(ctx, within_km=50)


async def test_deployment_phase_is_reported_rather_than_assumed(ctx):
    """A node in the manifest is not necessarily a node that is running."""
    page = await _nodes(ctx)
    assert {n.deployed for n in page.nodes} == {True, False}
