"""The research tools' disclosure behaviour."""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (DATA_TOKEN_BUDGET, PaginationCoverage,
                                   RecipeKind, RegistryCoverage,
                                   ResultCoverage, WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import discovery, research


async def test_search_deduplicates_the_same_paper_across_collections(ctx):
    """OSTI.GOV and DOE PAGES index overlapping work. The same paper twice
    with different ids reads as two findings."""
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=5)
    dois = [r["doi"] for r in env.data["records"] if r.get("doi")]
    assert len(dois) == len(set(dois))
    assert env.data.get("deduplicated_by_doi", 0) > 0, (
        "the recorded pages overlap; if they stop overlapping this test needs "
        "a new fixture rather than deleting")


async def test_an_unfiltered_search_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="answers nothing"):
        await research.search_literature(ctx)


async def test_provenance_names_every_collection_that_was_searched(ctx):
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    assert {p.source_id for p in env.provenance} == {"osti-gov-records",
                                                     "osti-doe-pages"}
    for entry in env.provenance:
        assert entry.dataset_version, "dataset_version is required"
        assert entry.steward


async def test_every_evidence_item_points_at_a_registered_source(ctx):
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    refs = {p.id for p in env.provenance}
    assert env.evidence
    for item in env.evidence:
        assert item.source_ref in refs


async def test_records_carry_access_recipes_rather_than_bare_urls(ctx):
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    kinds = {r.kind for r in env.access_recipes}
    assert RecipeKind.doi in kinds


async def test_the_concise_shape_stays_inside_the_data_budget(ctx):
    """Ten bibliographic records with full abstracts blow past the 2K-token
    budget and the model reads a truncated blob instead of ten answers."""
    env = await research.search_literature(ctx, query="perovskite solar",
                                           rows=3)
    estimate = env.data_token_estimate()
    assert estimate < DATA_TOKEN_BUDGET, (
        f"concise mode measured {estimate} tokens against a budget of "
        f"{DATA_TOKEN_BUDGET}")


async def test_public_access_only_discloses_the_embargo(ctx):
    env = await research.search_literature(ctx, query="perovskite solar",
                                           public_access_only=True, rows=3)
    assert {p.source_id for p in env.provenance} == {"osti-doe-pages"}
    note = next(w.message for w in env.warnings
                if w.code == WarningCode.terms_note
                and "embargo" in w.message)
    assert "systematically absent" in note


async def test_dataset_search_says_it_returns_pointers_not_data(ctx):
    env = await research.search_datasets(ctx, site_code="DOE-GDR", rows=2)
    assert "hosts nothing itself" in env.data["note"]
    assert env.data["total_matches"] and env.data["total_matches"] > 1000, (
        "site_ownership_code=DOE-GDR is the only API route into the "
        "Geothermal Data Repository and covers about 1,035 submissions")


async def test_software_search_explains_the_closed_source_classification(ctx):
    env = await research.search_software(ctx, query="machine learning", rows=3)
    assert "closed-source" in env.data["note"]


async def test_get_record_refuses_a_collection_it_cannot_route(ctx):
    with pytest.raises(InvalidQuery, match="separate id spaces"):
        await research.get_record(ctx, "123", collection="invented")


async def test_pure_resources_reports_this_projects_own_coverage(ctx):
    """The status column is deliberately unflattering: four of DOE's seven
    designated resources are still inventory here, and saying so publicly is
    the point."""
    env = await discovery.list_pure_resources(ctx)
    assert env.data["record_count"] == 7
    assert env.data["doe_mcp_covered"] < 7
    assert "not a DOE statement" in env.data["note"]
    assert env.provenance[0].dataset_version == "2026-09-01"


async def test_the_catalog_fanout_labels_every_result_with_its_catalog(ctx):
    env = await discovery.search_all_catalogs(ctx, query="geothermal",
                                              per_catalog=2)
    assert env.data["results"]
    for row in env.data["results"]:
        assert row["catalog"]
        assert "catalog_vintage" in row
    assert env.coverage.pagination is PaginationCoverage.truncated


async def test_the_fanout_refuses_an_unknown_catalog(ctx):
    with pytest.raises(InvalidQuery, match="unknown catalogs"):
        await discovery.search_all_catalogs(ctx, query="x",
                                            catalogs="not-a-catalog")


async def test_every_provenance_entry_resolves_to_a_registered_manifest(ctx):
    """Acceptance criterion 2, asserted rather than claimed.

    The tools that answer from this project's own tables are the ones that
    would slip: their provenance names `doe-mcp-registry`, which is a real
    registered manifest rather than a special-cased dict, so nothing gets an
    exemption from the rule that a caller can look up every source.
    """
    from doe_mcp.domains import registry_tools

    # Row counts match what tools/record_fixtures.py recorded; the replay
    # fetcher keys on params and raises rather than reaching the network, so
    # a mismatch here is a missing fixture and not a passing test.
    envelopes = [
        await research.search_literature(ctx, query="perovskite solar", rows=3),
        await research.search_datasets(ctx, site_code="DOE-GDR", rows=2),
        await research.search_software(ctx, query="machine learning", rows=3),
        await registry_tools.resolve_org(ctx, "NREL"),
        await registry_tools.lab_crosswalk(ctx, "ORNL"),
        await registry_tools.search_sources(ctx, text="osti"),
        await registry_tools.describe_source(ctx, "osti-gov-records"),
        await registry_tools.list_neighbors(ctx),
        await discovery.list_pure_resources(ctx),
    ]
    for env in envelopes:
        assert env.provenance, "an answer with no provenance at all"
        for entry in env.provenance:
            manifest = ctx.sources.get(entry.source_id)
            assert manifest is not None, (
                f"provenance names {entry.source_id!r}, which is not in the "
                "registry — a caller cannot look up where this came from")
            assert entry.dataset_version


async def test_project_answers_are_attributed_to_the_project_not_to_doe(ctx):
    """"What does Oak Ridge publish" is DOE-MCP's inventory of ORNL, not
    ORNL's statement about itself, and the provenance has to say so."""
    from doe_mcp.domains import registry_tools
    env = await registry_tools.lab_crosswalk(ctx, "ORNL")
    entry = env.provenance[0]
    assert entry.source_id == "doe-mcp-registry"
    assert entry.steward == "The DOE-MCP project"
    assert entry.authority_level.value == "unverified", (
        "an inventory of someone else's systems is not an authoritative "
        "record of them")


# --- research.get_record, after the 2026-09-08 merge -----------------------
#
# `research.get_fulltext_link` was a second tool over the same fetch,
# differing only in what it showed. It is folded in here, so these are the
# only tests of the "can I actually read this?" answer.

async def test_a_record_says_where_its_full_text_is(ctx):
    env = await research.get_record(ctx, "3413920", collection="literature")
    assert env.data["record"]["id"] == "3413920"
    assert env.data["fulltext"]["available"] is True
    assert env.data["fulltext"]["url"]
    assert env.coverage.result == ResultCoverage.hit


async def test_no_full_text_link_is_not_reported_as_a_paywall(ctx):
    """OSTI exposes no full-text link for this record. The work may still be
    readable through the publisher, and saying otherwise would be a claim
    about access that this project cannot make."""
    env = await research.get_record(ctx, "3389573", collection="literature")
    fulltext = env.data["fulltext"]
    assert fulltext["available"] is False
    assert fulltext["url"] is None
    assert "NOT a paywall finding" in fulltext["note"]


async def test_a_full_text_url_is_never_synthesized_from_the_record_id(ctx):
    """The regression this guards. Until 2026-09-08 the adapter filled a
    missing link from `https://www.osti.gov/servlets/purl/{id}`. Where OSTI
    supplies a link that is byte-identical to it, so the template bought
    nothing; where OSTI supplies none the URL 404s, and the tool was handing
    it over as `fulltext_pdf` — a claim no publisher had made."""
    env = await research.get_record(ctx, "3389573", collection="literature")
    kinds = {r.kind for r in env.access_recipes}
    assert RecipeKind.fulltext_pdf not in kinds
    assert not any("servlets/purl" in (r.uri or "") for r in env.access_recipes)


async def test_a_record_without_full_text_is_still_a_hit(ctx):
    """The merged-away tool reported `result: empty` here, which said the
    collection held no matching record while the caller was holding one. The
    result dimension is a statement about the search, not about the PDF."""
    env = await research.get_record(ctx, "3389573", collection="literature")
    assert env.coverage.result == ResultCoverage.hit
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.data["record"]["title"]


async def test_the_access_recipes_carry_both_routes_to_the_work(ctx):
    env = await research.get_record(ctx, "3413920", collection="literature")
    kinds = {r.kind for r in env.access_recipes}
    assert RecipeKind.fulltext_pdf in kinds
    assert RecipeKind.doi in kinds
    assert RecipeKind.landing_page in kinds


async def test_a_missing_record_carries_the_same_shape(ctx):
    """A caller reading `fulltext` should not have to check whether the
    record was found first."""
    env = await research.get_record(ctx, "3389573", collection="literature")
    assert "fulltext" in env.data
