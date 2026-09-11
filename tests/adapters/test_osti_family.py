"""The osti_family adapter against recorded responses.

The three quirks this adapter exists to absorb each get a test, because each
one has produced a wrong answer somewhere before.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.osti_family import (DOECODE_FILTERS, SHARED_FILTERS,
                                          OstiFamilyAdapter)
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged


async def test_the_match_count_comes_from_the_header_not_the_body(ctx):
    """OSTI returns one page in the body and the total in X-Total-Count.
    Reading len(body) reports a 4.38-million-record match as 20."""
    manifest = ctx.sources.get("osti-gov-records")
    page = (await ctx.osti.search(manifest, filters={}, rows=1)).value
    assert len(page.records) == 1
    assert page.total_matches is not None and page.total_matches > 4_000_000


async def test_doe_pages_full_url_dois_normalize_to_bare_dois(ctx):
    """PAGES emits https://doi.org/10.x while OSTI.GOV emits 10.x. Without
    normalizing, the same paper in both collections never deduplicates."""
    manifest = ctx.sources.get("osti-doe-pages")
    page = (await ctx.osti.search(manifest, filters={"q": "perovskite solar"},
                                 rows=3)).value
    dois = [r.doi for r in page.records if r.doi]
    assert dois, "the recorded page has no DOIs to check"
    for doi in dois:
        assert not doi.startswith("http"), f"unnormalized DOI: {doi}"
    for record in page.records:
        if record.doi_url:
            assert record.doi_url.startswith("https://doi.org/")


async def test_doe_code_solr_shape_is_read_by_the_same_adapter(ctx):
    """The 'one adapter, four APIs' claim, honestly: DOE CODE is Solr-shaped
    with num_found/docs, and it takes a different response reader inside the
    same adapter rather than a different adapter."""
    manifest = ctx.sources.get("osti-doe-code")
    page = (await ctx.osti.search(manifest,
                                 filters={"all_fields": "machine learning"},
                                 rows=3)).value
    assert page.total_matches is not None and page.total_matches > 0
    assert page.records[0].title
    assert page.records[0].product_type == "Software"


async def test_an_unknown_filter_is_refused_rather_than_dropped(ctx):
    """Sending a filter the API ignores returns an UNFILTERED result that
    looks filtered, which is worse than an error."""
    manifest = ctx.sources.get("osti-gov-records")
    with pytest.raises(InvalidQuery, match="not accepted by this API"):
        await ctx.osti.search(manifest, filters={"invented_filter": "x"})


async def test_doe_code_refuses_the_records_api_filter_vocabulary(ctx):
    """The two vocabularies do not overlap: `q` works on /records and does
    nothing on DOE CODE."""
    manifest = ctx.sources.get("osti-doe-code")
    with pytest.raises(InvalidQuery):
        await ctx.osti.search(manifest, filters={"q": "anything"})
    assert "q" in SHARED_FILTERS and "q" not in DOECODE_FILTERS


async def test_row_counts_are_bounded(ctx):
    manifest = ctx.sources.get("osti-gov-records")
    for rows in (0, 500):
        with pytest.raises(InvalidQuery, match="rows must be between"):
            await ctx.osti.search(manifest, filters={"q": "x"}, rows=rows)


async def test_a_changed_response_shape_raises_the_drift_alarm(ctx):
    """SourceSchemaChanged is the alarm that says 'the API moved under us',
    which must never be reported to a caller as an empty result."""
    class WrongShape:
        async def fetch_json(self, url, params):
            from doe_mcp.adapters.base import JsonResponse
            return JsonResponse(payload={"unexpected": "object"}, headers={},
                                url=url)

    from doe_mcp.adapters.base import TTLCache
    adapter = OstiFamilyAdapter(fetcher=WrongShape(), cache=TTLCache())
    manifest = ctx.sources.get("osti-gov-records")
    with pytest.raises(SourceSchemaChanged, match="JSON array"):
        await adapter.search(manifest, filters={"q": "x"})


async def test_an_empty_page_is_a_result_not_an_error(ctx):
    manifest = ctx.sources.get("osti-gov-records")
    page = (await ctx.osti.search(manifest,
                                 filters={"q": "zzzznotarealtopiczzzz"},
                                 rows=3)).value
    assert page.records == []
    assert page.total_matches == 0


async def test_the_cache_serves_a_repeat_request_without_a_second_fetch(ctx,
                                                                       replay):
    manifest = ctx.sources.get("osti-gov-records")
    first = await ctx.osti.search(manifest, filters={}, rows=1)
    calls = len(replay.calls)
    fetched = await ctx.osti.search(manifest, filters={}, rows=1)
    assert len(replay.calls) == calls, "the second call hit the network"
    assert fetched.from_cache is True and first.from_cache is False
    assert fetched.retrieved_at == first.retrieved_at
    assert fetched.value.total_matches == first.value.total_matches
