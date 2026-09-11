"""The `vips` adapter: a service whose mistakes are all silent.

Every wrong filter on this API is HTTP 200 with `{"total": 0, "hits": []}`,
and a cursor makes it ignore the filters sent beside it. Both are checked
before a request goes out, and both are what these tests pin.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse, TTLCache
from doe_mcp.adapters.vips import MAX_ROWS, VipsAdapter
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged


def _manifest(ctx):
    return ctx.sources.get("pnnl-vips")


async def test_the_lab_list_is_the_services_own_not_the_registrys(ctx):
    """It indexes twenty-one sites: the seventeen national laboratories plus
    four NNSA production sites, which are not in the registry's lab table and
    would be rejected by a check against it."""
    index = (await ctx.vips.labs(_manifest(ctx))).value
    assert len(index.acronyms) == 21
    for lab in ("PNNL", "INL", "NLR"):
        assert index.require(lab) == lab
    for site in ("KCNSC", "NNSS", "SRS", "Y12"):
        assert site in index.names
    assert "NREL" not in index.names


async def test_an_unknown_acronym_is_refused_rather_than_returning_zero(ctx):
    """The failure this check exists for: the service answers an unknown
    acronym with zero results and HTTP 200, which reads as a laboratory that
    holds nothing."""
    index = (await ctx.vips.labs(_manifest(ctx))).value
    with pytest.raises(InvalidQuery, match="zero results"):
        index.require("NREL")


async def test_the_laboratory_contacts_are_not_carried(ctx):
    """The lab-list response carries a technology-transfer mailbox per site.
    This project does not republish government staff contact details, so the
    adapter keeps the acronym and the name and nothing else."""
    index = (await ctx.vips.labs(_manifest(ctx))).value
    assert "@" not in "".join(index.names.values())


async def test_a_cursor_and_filters_together_are_refused(ctx):
    """The service ignores every filter sent with a cursor — a PNNL cursor
    replayed with labs=INL returns PNNL rows and PNNL's total. Sending both
    is a caller believing they changed the filter when they did not."""
    with pytest.raises(InvalidQuery, match="ignores every filter"):
        await ctx.vips.search(_manifest(ctx), cursor="abc", lab="INL")


async def test_an_unknown_record_type_is_refused_before_the_request(ctx):
    with pytest.raises(InvalidQuery, match="zero results"):
        await ctx.vips.search(_manifest(ctx), record_type="Widget")


async def test_the_row_count_is_bounded(ctx):
    with pytest.raises(InvalidQuery, match="rows must be between"):
        await ctx.vips.search(_manifest(ctx), rows=MAX_ROWS + 1)
    with pytest.raises(InvalidQuery, match="rows must be between"):
        await ctx.vips.search(_manifest(ctx), rows=0)


async def test_a_search_page_drops_the_filed_text_and_counts_it(ctx):
    """A patent's `description` is the filed text, ten kilobytes on the one
    recorded here. A page of them would be megabytes of patent prose."""
    page = await ctx.vips.search(_manifest(ctx), lab="INL", rows=5)
    assert page.value.total == 1219
    assert len(page.value.records) == 5
    for record in page.value.records:
        assert "description" not in record.raw
    assert sum(r.full_text_chars for r in page.value.records) > 0


async def test_a_single_record_keeps_everything(ctx):
    record = (await ctx.vips.get_record(_manifest(ctx), "US10016751")).value
    assert record.id == "US10016751"
    assert record.type == "Patent"
    assert len(record.raw["description"]) > 5000
    assert record.raw["patent_first_claim"]
    assert record.full_text_chars == 0


async def test_one_index_answers_for_every_laboratory_asked(ctx):
    """The claim that makes this source worth building: three laboratories,
    one source, no per-lab integration."""
    totals = {}
    for lab in ("PNNL", "INL", "NLR"):
        page = await ctx.vips.search(_manifest(ctx), lab=lab, rows=5)
        totals[lab] = page.value.total
        assert {r.lab for r in page.value.records} == {
            (await ctx.vips.labs(_manifest(ctx))).value.names[lab]}
    assert totals == {"PNNL": 2069, "INL": 1219, "NLR": 1536}


async def test_a_changed_response_shape_is_named_as_one(ctx):
    class Renamed:
        async def fetch_json(self, url, params):
            return JsonResponse(payload={"total": 3, "results": []},
                                headers={}, url=url)

    adapter = VipsAdapter(fetcher=Renamed(), cache=TTLCache())
    with pytest.raises(SourceSchemaChanged, match="no 'hits' array"):
        await adapter.search(_manifest(ctx), rows=5)
