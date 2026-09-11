"""`tech.find_licensable_ip`: the one tool that answers per laboratory.

Two things it must not get wrong. A renamed laboratory has to be rescued —
`NREL` returns zero here and `NLR` returns 1,536, so an unresolved name would
report a laboratory holding nothing. And the tool must not read as an offer
to license work it is only reporting the existence of.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (AccessPath, PaginationCoverage,
                                   RegistryCoverage, ResultCoverage,
                                   WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import tech


async def test_it_answers_for_each_laboratory_asked(ctx):
    for lab, total in (("PNNL", 2069), ("INL", 1219), ("NLR", 1536)):
        env = await tech.find_licensable_ip(ctx, lab=lab, rows=5)
        assert env.data["total_matches"] == total
        assert env.data["record_count"] == 5
        assert env.coverage.result == ResultCoverage.hit
        assert env.coverage.registry == RegistryCoverage.covered
        assert env.coverage.pagination == PaginationCoverage.truncated


async def test_a_renamed_laboratory_is_rescued_and_the_rescue_is_disclosed(
        ctx):
    """`labs=NREL` returns zero results and HTTP 200 from this service. An
    unresolved name would therefore report the most API-forward laboratory in
    the system as holding no intellectual property at all."""
    env = await tech.find_licensable_ip(ctx, lab="NREL", rows=5)
    assert env.data["total_matches"] == 1536
    codes = {w.code for w in env.warnings}
    assert WarningCode.alias_match in codes
    text = " ".join(w.message for w in env.warnings)
    assert "former name" in text
    assert "NLR" in text


async def test_a_current_name_resolves_without_crying_rename(ctx):
    """"Idaho" is an abbreviation of a current name, not a rename. Warning on
    it would train a reader to ignore the warning that matters."""
    env = await tech.find_licensable_ip(ctx, lab="Idaho", rows=5)
    assert env.data["total_matches"] == 1219
    assert WarningCode.alias_match not in {w.code for w in env.warnings}


async def test_an_unresolvable_laboratory_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="zero results"):
        await tech.find_licensable_ip(ctx, lab="NOTALAB")


async def test_the_omitted_filed_text_is_declared(ctx):
    env = await tech.find_licensable_ip(ctx, lab="INL", rows=5)
    codes = {w.code for w in env.warnings}
    assert WarningCode.truncated_inline in codes
    text = " ".join(w.message for w in env.warnings)
    assert "characters in all" in text
    assert all("description" not in r for r in env.data["records"])
    # Declared on the records it happened to and not on the others: some
    # software records carry no description at all, and claiming a
    # transformation there would be a transformation that did not occur.
    declared = [e.transformations for e in env.evidence]
    assert ["full text omitted"] in declared
    assert all(t in ([], ["full text omitted"]) for t in declared)


async def test_a_record_id_returns_the_filed_text(ctx):
    env = await tech.find_licensable_ip(ctx, record_id="US10016751")
    assert env.data["id"] == "US10016751"
    assert len(env.data["description"]) > 5000
    assert env.data["patent_first_claim"]
    assert env.coverage.pagination == PaginationCoverage.complete
    assert WarningCode.truncated_inline not in {w.code for w in env.warnings}


async def test_no_answer_reads_as_an_offer_to_license(ctx):
    """A published record of what exists is not a licence, and a reader who
    took it for one would go to the wrong party."""
    listing = await tech.find_licensable_ip(ctx, lab="PNNL", rows=5)
    single = await tech.find_licensable_ip(ctx, record_id="US10016751")
    for env in (listing, single):
        assert "not an offer to license" in env.data["note"].lower()


async def test_the_undeclared_terms_are_disclosed_on_every_answer(ctx):
    """Nobody has published terms of use for this API. That is a gap in the
    record, not permission, and the manifest's terms_gap is what puts it on
    the answer."""
    env = await tech.find_licensable_ip(ctx, lab="PNNL", rows=5)
    assert WarningCode.terms_note in {w.code for w in env.warnings}


async def test_paging_says_to_send_the_cursor_alone(ctx):
    env = await tech.find_licensable_ip(ctx, lab="PNNL", rows=5)
    assert env.data["next_cursor"]
    assert "with NO other filters" in env.data["paging"]


async def test_provenance_names_pnnl_and_reports_a_cache_hit_as_one(ctx):
    first = await tech.find_licensable_ip(ctx, lab="PNNL", rows=5)
    again = await tech.find_licensable_ip(ctx, lab="PNNL", rows=5)
    entry = first.provenance[0]
    assert entry.source_id == "pnnl-vips"
    assert entry.steward == "Pacific Northwest National Laboratory"
    assert entry.access_path == AccessPath.live
    assert again.provenance[0].access_path == AccessPath.cache


async def test_it_points_at_uspto_for_the_authoritative_patent_text(ctx):
    """This is a technology-transfer office's compilation, one remove from
    the patent office's own record."""
    env = await tech.find_licensable_ip(ctx, lab="INL", rows=5)
    assert "USPTO" in env.data["note"]
