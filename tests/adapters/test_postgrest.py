"""The `postgrest` adapter: what a database published as REST gets wrong.

Every test here replays the two USGS services' own recorded responses. The
one this file exists for is the first: both databases are served from one
PostgreSQL schema under two path aliases, so the path a request goes to says
nothing about which data comes back.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.postgrest import (MAX_ROWS, Filter, parse_filters, parse_order)
from doe_mcp.core.errors import (InvalidQuery, SourceSchemaChanged,
                                 SourceUnavailable)


async def _schema(ctx, source_id: str):
    return (await ctx.postgrest.describe(ctx.sources.get(source_id))).value


async def test_the_table_comes_from_the_manifest_not_from_the_path(ctx):
    """`uswtdb/v1/projects` serves the SOLAR database's 6,611 facilities.

    A client that read `uswtdb` out of the base URL and asked for the obvious
    table would return solar rows under a wind source's provenance, and
    nothing in the response would say so. Both manifests name their table,
    and they name different ones from base paths that differ only in which
    database they appear to be.
    """
    wind = ctx.postgrest.params_for(ctx.sources.get("usgs-uswtdb"))
    solar = ctx.postgrest.params_for(ctx.sources.get("usgs-uspvdb"))
    assert wind.table == "turbines"
    assert solar.table == "projects"
    assert "uswtdb" in wind.base_url and "uspvdb" in solar.base_url


async def test_the_published_schema_names_the_sibling_table(ctx):
    """The aliasing is invisible unless something reports it."""
    schema = await _schema(ctx, "usgs-uswtdb")
    assert schema.table == "turbines"
    assert "t_conf_loc" in schema.columns
    assert schema.other_tables == ["projects"]


async def test_a_missing_table_is_a_schema_change_not_an_empty_result(ctx):
    manifest = ctx.sources.get("usgs-uswtdb")
    manifest.adapter.table = "no_such_table"
    with pytest.raises(SourceSchemaChanged, match="no longer publishes"):
        await ctx.postgrest.describe(manifest)


async def test_an_unknown_filter_column_is_refused_before_the_request(ctx):
    """The service answers an unknown column with HTTP 400. The fetch path
    now repeats that body, but a refusal that never leaves is better than a
    refusal that comes back: caught here, the message names the columns that
    do exist."""
    schema = await _schema(ctx, "usgs-uswtdb")
    with pytest.raises(InvalidQuery, match="is not a column"):
        parse_filters("nosuch=eq.1", schema)


async def test_the_boolean_grouping_parameters_are_unreachable(ctx):
    """`or`, `and` and `select` are instructions to PostgREST rather than
    columns. Neither table has a column by those names, so the schema check
    is what closes them."""
    schema = await _schema(ctx, "usgs-uswtdb")
    for reserved in ("or", "and", "not", "select", "limit"):
        with pytest.raises(InvalidQuery, match="is not a column"):
            parse_filters(f"{reserved}=eq.1", schema)


async def test_a_filter_needs_a_known_operator_and_a_value(ctx):
    schema = await _schema(ctx, "usgs-uswtdb")
    with pytest.raises(InvalidQuery, match="operator"):
        parse_filters("t_state=zz.CO", schema)
    with pytest.raises(InvalidQuery, match="no value"):
        parse_filters("t_state=eq.", schema)
    with pytest.raises(InvalidQuery, match="column=operator.value"):
        parse_filters("t_state", schema)


async def test_two_clauses_on_one_column_both_survive(ctx):
    """A bounding box is two clauses on longitude and two on latitude.
    Collapsing filters into one value per column would silently drop half of
    every range and answer with a much larger set."""
    schema = await _schema(ctx, "usgs-uswtdb")
    calls_before = len(ctx.postgrest._fetcher.calls)          # noqa: SLF001
    with pytest.raises(SourceUnavailable):
        # No recorded interaction for this box; the point is the parameters
        # the adapter built, which the replay fetcher records either way.
        await ctx.postgrest.query(
            ctx.sources.get("usgs-uswtdb"), schema=schema,
            filters=[Filter("xlong", "gte", "-105.5"),
                     Filter("xlong", "lte", "-105.0")])
    _, params = ctx.postgrest._fetcher.calls[calls_before]     # noqa: SLF001
    assert params["xlong"] == ["gte.-105.5", "lte.-105.0"]


async def test_the_order_column_is_checked_the_same_way(ctx):
    schema = await _schema(ctx, "usgs-uswtdb")
    assert parse_order("p_year.desc", schema) == "p_year.desc"
    assert parse_order("p_year", schema) == "p_year.asc"
    with pytest.raises(InvalidQuery, match="is not a column"):
        parse_order("nosuch.desc", schema)
    with pytest.raises(InvalidQuery, match="order direction"):
        parse_order("p_year.sideways", schema)


async def test_every_request_carries_an_explicit_limit(ctx):
    """This service has no row ceiling: a query without `limit` returns all
    75,727 turbines, and the egress cap would report that as an outage."""
    schema = await _schema(ctx, "usgs-uswtdb")
    manifest = ctx.sources.get("usgs-uswtdb")
    await ctx.postgrest.query(manifest, rows=5, schema=schema)
    rows_call = next(params for url, params
                     in ctx.postgrest._fetcher.calls                # noqa: SLF001
                     if url.endswith("/turbines")
                     and "select" not in params)
    assert rows_call["limit"] == 5
    with pytest.raises(InvalidQuery, match="rows must be between"):
        await ctx.postgrest.query(manifest, rows=MAX_ROWS + 1, schema=schema)
    with pytest.raises(InvalidQuery, match="offset cannot be negative"):
        await ctx.postgrest.query(manifest, rows=5, offset=-1, schema=schema)


async def test_the_total_is_the_matching_count_not_the_page(ctx):
    """`Content-Range` reads `0-1/*` without a header this fetch seam does
    not send, so the total comes from a `select=count` request with the same
    filters."""
    schema = await _schema(ctx, "usgs-uswtdb")
    page = await ctx.postgrest.query(ctx.sources.get("usgs-uswtdb"), rows=5,
                                     schema=schema)
    assert len(page.value.rows) == 5
    assert page.value.total == 75727
