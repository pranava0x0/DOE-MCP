"""The `optimade` and `basis_sets` adapters.

OPTIMADE is the first source in this registry that is a SPECIFICATION rather
than a service, so the tests are about what the standard guarantees and what
the provider adds on top of it. The Basis Set Exchange is the opposite: one
service, two shapes, and a 404 that means the caller got a name wrong.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.basis_sets import (SYMBOL_FOR_NUMBER, element_numbers,
                                         parse_catalog)
from doe_mcp.adapters.optimade import (build_filter, property_names, quote)
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged

OPTIMADE = "lbnl-mp-optimade"
BSE = "basis-set-exchange"


async def _schema(ctx):
    return (await ctx.optimade.describe(ctx.sources.get(OPTIMADE))).value


# --- the filter grammar ---------------------------------------------------

def test_a_value_can_never_end_its_own_string_literal():
    """Values reach the service inside a filter expression, so a quote in
    one would otherwise close the literal early and the rest would be read
    as grammar."""
    assert quote('Ga') == '"Ga"'
    assert quote('a"b') == '"a\\"b"'
    assert quote('a\\b') == '"a\\\\b"'


def test_the_named_arguments_build_one_expression():
    assert build_filter(elements=["Ga", "N"], formula="", nelements="",
                        extra="") == 'elements HAS ALL "Ga","N"'
    assert build_filter(elements=[], formula="GaN", nelements="<=3",
                        extra="nsites<20") == (
        'chemical_formula_reduced="GaN" AND nelements<=3 AND (nsites<20)')


def test_a_count_that_is_not_a_count_is_refused():
    with pytest.raises(InvalidQuery, match="takes a number"):
        build_filter(elements=[], formula="", nelements="lots", extra="")


def test_property_names_ignores_keywords_and_string_contents():
    """`HAS` is grammar and `"HAS"` is a value. A name-finder that missed
    the difference would report a legal filter as using an unknown
    property."""
    found = property_names('elements HAS ALL "Ga","HAS" AND nsites<20')
    assert found == ["elements", "nsites"]


# --- OPTIMADE -------------------------------------------------------------

async def test_the_property_list_comes_from_the_provider(ctx):
    schema = await _schema(ctx)
    assert "chemical_formula_reduced" in schema.properties
    assert "elements" in schema.properties
    assert schema.api_version
    assert len(schema.properties) > 20


async def test_the_providers_own_fields_are_marked_as_its_own(ctx):
    """`_mp_stability` carries the formation energies and means nothing at
    another provider. The standard reserves the underscore for exactly this
    and the answer has to keep the distinction."""
    schema = await _schema(ctx)
    extensions = schema.extensions("_mp_")
    assert extensions
    assert all(n.startswith("_mp_") for n in extensions)
    assert "elements" not in extensions


async def test_a_filter_on_an_unknown_property_never_leaves(ctx):
    """The provider refuses it with a precise message, but a refusal that
    does not travel is better than one that does: caught here, the message
    names the walk that lists the real properties."""
    schema = await _schema(ctx)
    with pytest.raises(InvalidQuery, match="describe_structure_fields"):
        await ctx.optimade.search(ctx.sources.get(OPTIMADE), schema=schema,
                                  filter_expression="nosuchprop = 3")


async def test_a_search_carries_the_providers_own_totals(ctx):
    schema = await _schema(ctx)
    page = (await ctx.optimade.search(
        ctx.sources.get(OPTIMADE), schema=schema,
        filter_expression='elements HAS ALL "Ga","N"', rows=5)).value
    assert page.structures
    assert page.data_returned > len(page.structures)
    assert page.data_available > page.data_returned
    assert page.more_data_available is True
    assert all("Ga" in s.elements and "N" in s.elements
               for s in page.structures)


async def test_nothing_matching_is_an_empty_page_not_an_error(ctx):
    schema = await _schema(ctx)
    page = (await ctx.optimade.search(
        ctx.sources.get(OPTIMADE), schema=schema,
        filter_expression='chemical_formula_reduced="Zzz9"', rows=5)).value
    assert page.structures == []
    assert page.data_returned == 0


async def test_one_structure_carries_the_provider_block_separately(ctx):
    structure = (await ctx.optimade.get_entry(ctx.sources.get(OPTIMADE),
                                              "mp-1244984")).value
    assert structure.formula_reduced == "GaN"
    assert structure.elements == ["Ga", "N"]
    assert structure.nsites
    assert structure.lattice_vectors
    assert "_mp_stability" in structure.provider_fields
    assert not any(k.startswith("_mp_") for k in
                   ("formula_reduced", "elements"))


async def test_an_entry_id_that_is_a_path_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="not an entry id"):
        await ctx.optimade.get_entry(ctx.sources.get(OPTIMADE), "../info")


async def test_a_response_without_the_standards_shape_is_a_schema_change(ctx):
    params = ctx.optimade.params_for(ctx.sources.get(OPTIMADE))
    with pytest.raises(SourceSchemaChanged, match="no 'data' array"):
        ctx.optimade._parse_page({"meta": {}}, OPTIMADE, params, 0, "")


# --- the Basis Set Exchange ----------------------------------------------

async def _catalog(ctx):
    return (await ctx.basis_sets.catalog(ctx.sources.get(BSE))).value


def test_elements_are_asked_for_in_symbols_and_stored_as_numbers():
    """The catalog records coverage as atomic numbers and people ask in
    symbols. Both are accepted; anything else is refused rather than
    silently dropped from the filter."""
    assert element_numbers(["H", "c", "U"]) == ["1", "6", "92"]
    assert element_numbers(["1", "92"]) == ["1", "92"]
    assert SYMBOL_FOR_NUMBER["92"] == "U"
    with pytest.raises(InvalidQuery, match="not an element symbol"):
        element_numbers(["Xx"])


async def test_the_catalog_carries_family_role_and_element_coverage(ctx):
    catalog = await _catalog(ctx)
    entry = catalog.find("6-31g")
    assert entry is not None
    assert entry.family == "pople"
    assert entry.role == "orbital"
    assert "H" in entry.elements and "C" in entry.elements
    assert entry.latest_version


async def test_coverage_is_a_superset_test_not_an_overlap_test(ctx):
    """"Which sets cover uranium AND carbon" means both, not either. A set
    that has carbon and stops at zinc is not an answer."""
    catalog = await _catalog(ctx)
    pople = catalog.find("6-31g")
    assert pople.covers(element_numbers(["H", "C"]))
    assert not pople.covers(element_numbers(["H", "U"]))


async def test_a_set_is_found_by_its_key_or_its_display_name(ctx):
    catalog = await _catalog(ctx)
    assert catalog.find("6-31G") is catalog.find("6-31g")
    assert catalog.find("no-such-basis-set") is None


async def test_a_rendered_set_comes_back_with_its_references(ctx):
    """The citations are a second request against a different endpoint. The
    publisher asks that sets be cited, so an answer with the functions and
    without the citations drops exactly the part that needs attributing."""
    rendered = (await ctx.basis_sets.render(
        ctx.sources.get(BSE), key="6-31g", output_format="nwchem",
        elements=["H", "C", "O"])).value
    assert "Basis Set Exchange" in rendered.data
    assert rendered.elements == ["H", "C", "O"]
    assert rendered.references and "@article" in rendered.references


async def test_the_format_list_comes_from_the_service(ctx):
    formats = (await ctx.basis_sets.formats(ctx.sources.get(BSE))).value
    assert "nwchem" in formats
    assert formats["nwchem"] == "NWChem"
    assert len(formats) > 10


def test_a_metadata_document_that_is_not_a_catalog_is_a_schema_change():
    with pytest.raises(SourceSchemaChanged, match="rather than an object"):
        parse_catalog([], BSE)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "..", ".",
                                 "a\\b", ""])
async def test_a_basis_set_key_that_could_climb_the_path_is_refused(ctx, bad):
    """`key` and `format` land in the URL PATH rather than in a query
    parameter. The tool resolves both against the publisher's own lists
    first, so this is the second line — but an adapter method is a public
    entry point and the next caller may not do either."""
    with pytest.raises(InvalidQuery, match="not a usable basis-set"):
        await ctx.basis_sets.render(ctx.sources.get(BSE), key=bad,
                                    output_format="nwchem", elements=["H"])


async def test_a_basis_set_format_that_could_climb_the_path_is_refused(ctx):
    with pytest.raises(InvalidQuery, match="not a usable basis-set"):
        await ctx.basis_sets.render(ctx.sources.get(BSE), key="6-31g",
                                    output_format="../json", elements=["H"])
