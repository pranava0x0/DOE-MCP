"""The five materials tools.

Both sources here invite an overclaim and the tests are mostly about
refusing it. "Materials Project" is a name people trust and this route is
one part of what it covers. The Basis Set Exchange asks to be cited and
keeps its citations at a different endpoint from its data.
"""
from __future__ import annotations

import pytest

from doe_mcp.core.envelope import (PaginationCoverage, RegistryCoverage,
                                   ResultCoverage, WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import materials


async def test_the_field_walk_marks_what_is_the_providers_own(ctx):
    env = await materials.describe_structure_fields(ctx)
    names = {f["name"] for f in env.data["fields"]}
    assert "chemical_formula_reduced" in names
    own = env.data["provider_specific_fields"]
    assert own and all(n.startswith("_mp_") for n in own)
    by_name = {f["name"]: f for f in env.data["fields"]}
    assert by_name["elements"]["provider_specific"] is False
    assert all(by_name[n]["provider_specific"] for n in own)


async def test_a_structure_search_says_the_values_are_computed(ctx):
    env = await materials.search_structures(ctx, elements="Ga,N", rows=5)
    assert env.data["record_count"] == 5
    assert env.data["filter_sent"] == 'elements HAS ALL "Ga","N"'
    assert all("Ga" in s["elements"] for s in env.data["structures"])
    assert any(w.code == WarningCode.derived_layer for w in env.warnings), (
        "a computed structure reported as a measured one is the category "
        "error this source most invites")


async def test_the_answer_says_what_this_route_does_not_carry(ctx):
    """A caller who reads "Materials Project" will assume the whole
    database. The keyless route is structures and formulas."""
    env = await materials.search_structures(ctx, formula="GaN", rows=5)
    assert "keyed API" in env.data["coverage_note"]
    assert "federation" in env.data["federation_note"]


async def test_pagination_reports_the_providers_own_total(ctx):
    env = await materials.search_structures(ctx, elements="Ga,N", rows=5)
    assert env.coverage.pagination == PaginationCoverage.truncated
    assert env.data["total_matches"] > env.data["record_count"]
    assert env.data["entries_in_database"] > env.data["total_matches"]


async def test_nothing_matching_is_empty_under_covered(ctx):
    env = await materials.search_structures(ctx, formula="Zzz9", rows=5)
    assert env.coverage.result == ResultCoverage.empty
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.data["record_count"] == 0


async def test_a_search_with_no_criteria_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="at least one of"):
        await materials.search_structures(ctx, rows=5)


async def test_one_structure_keeps_the_provider_block_labelled(ctx):
    env = await materials.get_structure(ctx, "mp-1244984")
    assert env.data["formula"] == "GaN"
    assert env.data["lattice_vectors"]
    assert "_mp_stability" in env.data["provider_fields"]
    assert any(w.code == WarningCode.derived_layer for w in env.warnings)


async def test_basis_sets_are_searchable_by_family_and_element(ctx):
    env = await materials.search_basis_sets(ctx, family="pople")
    assert env.data["record_count"] > 0
    assert all(s["family"] == "pople" for s in env.data["basis_sets"])


async def test_element_coverage_means_all_of_them(ctx):
    """"Which sets cover uranium" is a superset question. A set that covers
    carbon and stops at zinc is not an answer to it."""
    everything = await materials.search_basis_sets(ctx)
    heavy = await materials.search_basis_sets(ctx, covers_elements="U")
    assert heavy.data["total_matches"] < everything.data["total_matches"]
    assert all("U" in s["elements"] for s in heavy.data["basis_sets"])


async def test_an_element_that_is_not_one_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="not an element symbol"):
        await materials.search_basis_sets(ctx, covers_elements="Xx")


async def test_a_basis_set_comes_back_with_the_citations_it_requires(ctx):
    env = await materials.get_basis_set(ctx, "6-31g", output_format="nwchem",
                                        elements="H,C,O")
    assert env.data["format_name"] == "NWChem"
    assert "Basis Set Exchange" in env.data["data"]
    assert "@article" in env.data["references"]
    assert env.data["elements_requested"] == ["H", "C", "O"]
    assert any(w.code == WarningCode.citation_required
               for w in env.warnings), (
        "this publisher makes citation a condition of use, and the string "
        "has to travel with the answer rather than sit in a manifest")


async def test_an_unknown_set_names_the_search_rather_than_failing(ctx):
    with pytest.raises(InvalidQuery, match="search_basis_sets"):
        await materials.get_basis_set(ctx, "not-a-basis-set")


async def test_an_unknown_output_format_is_refused_against_the_service(ctx):
    """The format list comes from the service, so the refusal can name what
    it does render rather than what this repository last remembered."""
    with pytest.raises(InvalidQuery, match="is not a format"):
        await materials.get_basis_set(ctx, "6-31g", output_format="wordperfect")
