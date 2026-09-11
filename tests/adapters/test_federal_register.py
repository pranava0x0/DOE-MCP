"""The `federal_register` adapter: four API behaviours that mislead.

`per_page=1` returning twenty is the one this file exists for. The others —
a capped count, a capped page walk, and an HTTP 400 whose body the fetch
path drops — are each a way to report something confidently wrong.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
from doe_mcp.adapters.federal_register import (COUNT_CAP, MAX_PAGES,
                                               FederalRegisterAdapter)
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged


def _manifest(ctx):
    return ctx.sources.get("federal-register-doe")


async def test_per_page_of_one_is_refused_because_the_api_ignores_it(ctx):
    """The API answers `per_page=1` with twenty documents and says nothing
    about having done so. A client that paged on the assumption it got one
    would skip nineteen documents per page. Refused rather than silently
    corrected: a caller who asked for one row and got twenty should be told
    which of those happened."""
    with pytest.raises(InvalidQuery, match="answers per_page=1 with twenty"):
        await ctx.federal_register.search(_manifest(ctx), rows=1)


async def test_the_page_walk_stops_where_the_api_stops(ctx):
    with pytest.raises(InvalidQuery, match=f"at most {MAX_PAGES} pages"):
        await ctx.federal_register.search(_manifest(ctx), page=MAX_PAGES + 1)
    with pytest.raises(InvalidQuery, match="page starts at 1"):
        await ctx.federal_register.search(_manifest(ctx), page=0)


async def test_a_capped_count_is_marked_as_a_floor(ctx):
    """Both the DOE query and the FERC query report exactly 10,000, which is
    the cap and not the total. Reporting it as a sum would be a wrong number
    presented as a precise one."""
    notices = await ctx.federal_register.search(
        _manifest(ctx), document_types=["NOTICE"], rows=5)
    assert notices.value.count == COUNT_CAP
    assert notices.value.count_is_capped is True
    rules = await ctx.federal_register.search(
        _manifest(ctx), document_types=["RULE"], rows=5)
    assert rules.value.count == 1690
    assert rules.value.count_is_capped is False


async def test_an_unknown_document_type_is_refused_before_the_request(ctx):
    """The API answers one with HTTP 400 and a body the fetch path does not
    surface, so the caller would see only the status code."""
    with pytest.raises(InvalidQuery, match="not one the API takes"):
        await ctx.federal_register.search(_manifest(ctx),
                                          document_types=["RULEZ"])


async def test_the_agency_list_comes_from_the_api_not_from_a_list_here(ctx):
    """A department reorganization should change the answer rather than
    produce an HTTP 400."""
    tree = (await ctx.federal_register.agencies(_manifest(ctx))).value
    assert tree.slug == "energy-department"
    assert len(tree.child_slugs) == 14
    assert "federal-energy-regulatory-commission" in tree.child_slugs
    assert tree.require("bonneville-power-administration")
    with pytest.raises(InvalidQuery, match="child agencies"):
        tree.require("environmental-protection-agency")


async def test_the_search_asks_for_the_fields_that_make_a_rule_traceable(ctx):
    """The API's default projection omits the docket, the CFR parts, and the
    regulatory identifier, which are what join a rule to anything else."""
    page = await ctx.federal_register.search(
        _manifest(ctx), document_types=["RULE"], rows=5)
    rule = page.value.documents[0].raw
    assert rule["docket_ids"]
    assert rule["cfr_references"]
    assert rule["regulation_id_numbers"]
    assert rule["raw_text_url"]


async def test_a_document_number_is_required(ctx):
    with pytest.raises(InvalidQuery, match="document number is required"):
        await ctx.federal_register.get_document(_manifest(ctx), "  ")


async def test_a_changed_response_shape_is_named_as_one(ctx):
    """`SourceSchemaChanged` rather than an empty page, because a search that
    answers with no `results` key has stopped being this API, and reporting
    that as zero documents would be the confident wrong answer."""

    class Renamed:
        async def fetch_json(self, url, params):
            return JsonResponse(payload={"count": 3, "records": []},
                                headers={}, url=url)

    adapter = FederalRegisterAdapter(fetcher=Renamed(), cache=TTLCache())
    with pytest.raises(SourceSchemaChanged, match="no 'results' array"):
        await adapter.search(_manifest(ctx), rows=5)
