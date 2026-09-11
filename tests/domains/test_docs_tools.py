"""`docs.search_rulemakings` and `docs.get_rulemaking`.

The disclosure these tools exist to get right is DOE-MCP's own scope filter.
The Federal Register files FERC's documents under the Department of Energy,
and they are most of what that tree publishes; dropping them silently would
make DOE-MCP's scope look like the Federal Register's contents.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (AccessPath, PaginationCoverage,
                                   RegistryCoverage, ResultCoverage)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import docs


async def test_a_rule_search_carries_what_makes_a_rule_traceable(ctx):
    env = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    assert env.coverage.result == ResultCoverage.hit
    first = env.data["documents"][0]
    assert first["document_number"]
    assert first["docket_ids"]
    assert first["cfr_references"] == ["10 CFR 433", "10 CFR 435"]
    assert first["regulation_id_numbers"] == ["1904-AG17"]
    assert first["full_text_url"].endswith(".txt")


async def test_dropped_documents_are_counted_and_explained(ctx):
    env = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    assert env.data["excluded_documents"] == 1
    assert "FERC" in env.data["exclusion_reason"]
    assert len(env.data["documents"]) == 5 - env.data["excluded_documents"]


async def test_our_own_filter_never_reads_as_the_publisher_holding_nothing(
        ctx):
    """A page of notices is entirely FERC's. `registry: covered` with
    `result: empty` would say the Federal Register held no matching record,
    and it held five.

    Page two rather than page one: on 2026-09-09 DOE published a notice of
    its own, one of the five newest survived the filter, and a re-recorded
    fixture stopped exercising this path without failing. The fixture was
    moved to a page where the case still holds rather than the assertion
    being relaxed to fit it.
    """
    env = await docs.search_rulemakings(ctx, document_type="NOTICE", rows=5,
                                        page=2)
    assert env.data["record_count"] == 0
    assert env.data["excluded_documents"] == 5
    assert env.coverage.result == ResultCoverage.empty
    assert env.coverage.registry == RegistryCoverage.partial
    assert "rather than because the Federal Register had nothing" \
        in env.data["note"]


async def test_the_unfiltered_total_is_labelled_as_unfiltered(ctx):
    """`total_matches` is the publisher's count and includes what we drop.
    Reporting it beside a filtered page without saying so would overstate
    DOE's own output."""
    env = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    assert env.data["total_matches"] == 1690
    assert "includes the documents dropped above" \
        in env.data["total_matches_is_unfiltered"]


async def test_a_capped_count_is_reported_as_a_floor(ctx):
    env = await docs.search_rulemakings(ctx, document_type="NOTICE", rows=5)
    assert env.data["total_matches"] == 10000
    assert "is a floor rather than a total" in env.data["total_is_a_floor"]
    assert env.coverage.pagination == PaginationCoverage.truncated


async def test_an_out_of_scope_agency_is_refused_with_the_reason(ctx):
    with pytest.raises(InvalidQuery, match="scope stops at DOE"):
        await docs.search_rulemakings(
            ctx, agency="federal-energy-regulatory-commission")
    with pytest.raises(InvalidQuery, match="child agencies"):
        await docs.search_rulemakings(ctx, agency="not-an-agency")


async def test_one_document_comes_back_whole(ctx):
    env = await docs.get_rulemaking(ctx, "2026-17979")
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.coverage.result == ResultCoverage.hit
    assert env.data["type"] == "Rule"
    assert env.data["docket_ids"] == ["EERE-2026-FEMP-0067"]
    assert len(env.evidence) == 1
    assert env.evidence[0].record_id == "2026-17979"


async def test_a_document_we_do_not_cover_is_found_but_not_served(ctx):
    """Found, published, and not ours. `registry: none` is what says the data
    exists and DOE-MCP cannot see it, which is a different answer from 'there
    is no such document'."""
    env = await docs.get_rulemaking(ctx, "2026-18232")
    assert env.data["found"] is True
    assert env.data["served"] is False
    assert env.coverage.registry == RegistryCoverage.none
    assert env.coverage.result == ResultCoverage.empty
    assert env.data["html_url"]
    assert not env.evidence


async def test_provenance_names_the_federal_register_and_its_cache(ctx):
    first = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    again = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    entry = first.provenance[0]
    assert entry.source_id == "federal-register-doe"
    assert "Federal Register" in entry.steward
    assert entry.access_path == AccessPath.live
    assert again.provenance[0].access_path == AccessPath.cache


async def test_the_answer_says_where_the_docket_material_is_not(ctx):
    env = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    assert "regulations.gov" in env.data["note"]


async def test_the_time_range_covers_the_documents_returned(ctx):
    env = await docs.search_rulemakings(ctx, document_type="RULE", rows=5)
    dates = [d["publication_date"] for d in env.data["documents"]]
    assert env.coverage.time_range.from_ == min(dates)
    assert env.coverage.time_range.to == max(dates)
