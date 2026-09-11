"""Materials and computational-chemistry tools.

Two sources with almost nothing in common except the people who use them.
The Materials Project's OPTIMADE endpoint answers "what inorganic materials
contain these elements"; the Basis Set Exchange answers "what functions
should this calculation expand its orbitals in". They share a server because
a domain's first sources are not yet two servers (architecture Part 1 § 3.4),
and because the same person asks both questions on the same afternoon.

The care in here is about two kinds of overclaim, one per source.

Materials Project is a NAME people trust and this route is one part of what
that name covers: structures and formulas, keyless, with the energetics
behind a keyed API this tool does not use. Every answer says so. It is also
one provider in a federation of twenty-nine, so "not found here" is not
"does not exist", and the coverage dimensions carry that distinction rather
than the prose.

The Basis Set Exchange asks to be cited, and its per-set references live at
a different endpoint from the sets. An answer that returned the functions
without them would drop exactly the part that needs attributing, so
`chemistry.get_basis_set` fetches both and the envelope carries the
requirement.
"""
from __future__ import annotations

from typing import Any

from ..adapters.basis_sets import DEFAULT_ROWS as BSE_ROWS
from ..adapters.basis_sets import MAX_ROWS as BSE_MAX_ROWS
from ..adapters.basis_sets import element_numbers
from ..adapters.optimade import DEFAULT_ROWS as OPTIMADE_ROWS
from ..adapters.optimade import build_filter
from ..core.assemble import pagination_coverage
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RecipeKind, ResultCoverage, SourceClaimCoverage, WarningCode)
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

MATERIALS_TOOLS = ToolRegistry(package="materials")

OPTIMADE_SOURCE = "lbnl-mp-optimade"
BASIS_SET_SOURCE = "basis-set-exchange"

FEDERATION_NOTE = (
    "This is one provider in the OPTIMADE federation, and the DOE member of "
    "it. A material absent here may exist at another provider; that is a "
    "different answer from its not existing.")


async def describe_structure_fields(ctx: RuntimeContext) -> Envelope:
    b = builder(ctx, "materials.describe_structure_fields",
                contract_version="1")
    manifest = require_active_source(ctx, OPTIMADE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "materials.describe_fields",
                                            [manifest])
    params = ctx.optimade.params_for(manifest)
    fetched = await ctx.optimade.describe(manifest)
    schema = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset_version=schema.api_version
                              or manifest.coverage.dataset_version)
    b.add_evidence(source_ref=ref, record_id="published-property-list",
                   retrieved_at=fetched.retrieved_at, transformations=[])

    extensions = schema.extensions(params.provider_prefix)
    data = {
        "provider": schema.provider_name,
        "api_version": schema.api_version,
        "fields": [{"name": name, "description": text or None,
                    "provider_specific": name in extensions}
                   for name, text in sorted(schema.properties.items())],
        "field_count": len(schema.properties),
        "provider_specific_fields": extensions,
        "filter_examples": [
            'elements HAS ALL "Ga","N"',
            'chemical_formula_reduced="GaN"',
            "nelements=2 AND nsites<20",
            'elements HAS ANY "Li","Na" AND NOT elements HAS "O"',
        ],
        "coverage_note": params.keyed_route_note,
        "note": ("The properties this database can be filtered and sorted "
                 "on. Names starting with a provider prefix are this "
                 "database's own additions to the standard: they carry what "
                 "the standard does not define, and they mean nothing at "
                 "another provider. Walk here before writing a filter — a "
                 "filter on a property this provider does not have is "
                 "refused, so a wrong name costs a round trip rather than "
                 "returning a wrong answer."),
    }
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(schema.properties)),
        sources_searched=[manifest.id], sources_unavailable=gaps))


async def search_structures(ctx: RuntimeContext, elements: str = "",
                            formula: str = "", nelements: str = "",
                            filter_expression: str = "",
                            rows: int = OPTIMADE_ROWS,
                            offset: int = 0) -> Envelope:
    b = builder(ctx, "materials.search_structures", contract_version="1")
    manifest = require_active_source(ctx, OPTIMADE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "materials.search",
                                            [manifest])
    params = ctx.optimade.params_for(manifest)
    wanted = [e.strip() for e in elements.split(",") if e.strip()]
    expression = build_filter(elements=wanted, formula=formula.strip(),
                              nelements=nelements,
                              extra=filter_expression.strip())
    if not expression:
        raise InvalidQuery(
            "a structure search needs at least one of elements, formula, "
            "nelements, or filter_expression. The database holds over "
            "150,000 entries "
            "and an unfiltered walk is not a question.")

    described = await ctx.optimade.describe(manifest)
    fetched = await ctx.optimade.search(manifest, schema=described.value,
                                        filter_expression=expression,
                                        rows=rows, offset=offset)
    page = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset_version=described.value.api_version
                              or manifest.coverage.dataset_version)
    for structure in page.structures:
        b.add_evidence(source_ref=ref, record_id=structure.entry_id,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=structure.last_modified,
                       transformations=[])

    returned = len(page.structures)
    b.warn(WarningCode.derived_layer,
           "These are computed structures, not measured ones: each is a "
           "calculation's output under a stated functional. Reporting one as "
           "an experimentally determined crystal structure is a category "
           "error.", manifest.id)
    data = {
        "structures": [_structure_summary(s) for s in page.structures],
        "record_count": returned,
        "total_matches": page.data_returned,
        "entries_in_database": page.data_available,
        "offset": offset,
        "filter_sent": page.query_sent,
        "provider": page.provider_name,
        "coverage_note": params.keyed_route_note,
        "federation_note": FEDERATION_NOTE,
        "note": ("Crystal structures matching the filter. `filter` takes the "
                 "OPTIMADE grammar directly through filter_expression, and "
                 "the named arguments build one for the common cases; call "
                 "materials.describe_structure_fields for the properties "
                 "this provider allows and for its own extension fields."),
    }
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(page.data_returned, returned,
                                       offset=offset,
                                       more=page.more_data_available),
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(returned), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        known_limitations=list(manifest.coverage.known_limitations)))


async def get_structure(ctx: RuntimeContext, structure_id: str) -> Envelope:
    b = builder(ctx, "materials.get_structure", contract_version="1")
    manifest = require_active_source(ctx, OPTIMADE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "materials.get_structure",
                                            [manifest])
    params = ctx.optimade.params_for(manifest)
    fetched = await ctx.optimade.get_entry(manifest, structure_id)
    structure = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    b.add_evidence(source_ref=ref, record_id=structure.entry_id,
                   retrieved_at=fetched.retrieved_at,
                   effective_at=structure.last_modified, transformations=[])
    b.warn(WarningCode.derived_layer,
           "A computed structure under a stated functional, not a measured "
           "one.", manifest.id)

    record = _structure_summary(structure) | {
        "lattice_vectors": structure.lattice_vectors,
        "structure_features": structure.structure_features,
        "immutable_id": structure.immutable_id,
        "provider_fields": structure.provider_fields,
        "coverage_note": params.keyed_route_note,
        "note": ("One structure in full. Fields under provider_fields are "
                 "this database's own additions to the OPTIMADE standard "
                 "and have no counterpart at another provider."),
    }
    return b.build(record, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.partial,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


def _structure_summary(structure) -> dict[str, Any]:
    return {
        "id": structure.entry_id,
        "formula": structure.formula_reduced,
        "formula_descriptive": structure.formula_descriptive,
        "formula_hill": structure.formula_hill,
        "elements": structure.elements,
        "element_ratios": structure.element_ratios,
        "element_count": structure.nelements,
        "sites": structure.nsites,
        "space_group_number": structure.space_group_number,
        "space_group_symbol": structure.space_group_symbol,
        "periodic_dimensions": structure.dimensionality,
        "last_modified": structure.last_modified,
        "provider_fields": structure.provider_fields,
    }


async def search_basis_sets(ctx: RuntimeContext, query: str = "",
                            family: str = "", role: str = "",
                            covers_elements: str = "",
                            rows: int = BSE_ROWS) -> Envelope:
    b = builder(ctx, "chemistry.search_basis_sets", contract_version="1")
    manifest = require_active_source(ctx, BASIS_SET_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "chemistry.search_basis_sets",
                                            [manifest])
    if rows < 1 or rows > BSE_MAX_ROWS:
        raise InvalidQuery(f"rows must be between 1 and {BSE_MAX_ROWS}.")
    numbers = element_numbers([e.strip()
                               for e in covers_elements.split(",")
                               if e.strip()])

    fetched = await ctx.basis_sets.catalog(manifest)
    catalog = fetched.value
    needle = query.strip().lower()
    matched = [e for e in catalog.entries
               if _matches_set(e, needle, family, role, numbers)]
    matched.sort(key=lambda e: (not e.key.lower().startswith(needle),
                                e.key.lower()))
    page = matched[:rows]

    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for entry in page:
        b.add_evidence(source_ref=ref, record_id=entry.key,
                       retrieved_at=fetched.retrieved_at, transformations=[])

    data = {
        "basis_sets": [{"key": e.key, "name": e.name, "family": e.family,
                        "role": e.role, "description": e.description,
                        "function_types": e.function_types,
                        "latest_version": e.latest_version,
                        "other_names": e.other_names,
                        "element_count": len(e.element_numbers),
                        "elements": e.elements}
                       for e in page],
        "record_count": len(page),
        "total_matches": len(matched),
        "sets_in_catalog": catalog.total,
        "note": ("Basis sets from the catalog, searched in memory over the "
                 "whole published metadata document. `key` is what "
                 "chemistry.get_basis_set takes. Element coverage is for "
                 "each set's newest version; an older version may cover a "
                 "different part of the periodic table."),
    }
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=(PaginationCoverage.truncated if len(matched) > len(page)
                    else PaginationCoverage.complete),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(page)), sources_searched=[manifest.id],
        sources_unavailable=gaps))


def _matches_set(entry, needle: str, family: str, role: str,
                 numbers: list[str]) -> bool:
    if needle:
        haystack = " ".join([entry.key, entry.name, entry.description or "",
                             *entry.other_names]).lower()
        if needle not in haystack:
            return False
    if family and (entry.family or "").lower() != family.strip().lower():
        return False
    if role and (entry.role or "").lower() != role.strip().lower():
        return False
    if numbers and not entry.covers(numbers):
        return False
    return True


async def get_basis_set(ctx: RuntimeContext, name: str,
                        output_format: str = "nwchem",
                        elements: str = "") -> Envelope:
    b = builder(ctx, "chemistry.get_basis_set", contract_version="1")
    manifest = require_active_source(ctx, BASIS_SET_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "chemistry.basis_sets",
                                            [manifest])
    catalog = (await ctx.basis_sets.catalog(manifest)).value
    entry = catalog.find(name)
    if entry is None:
        raise InvalidQuery(
            f"no basis set named {name!r} in the catalog of "
            f"{catalog.total}. Search with chemistry.search_basis_sets, "
            "which returns the exact keys this takes; the catalog also "
            "carries each set's alternative names.")
    formats = (await ctx.basis_sets.formats(manifest)).value
    if output_format not in formats:
        raise InvalidQuery(
            f"{output_format!r} is not a format this service renders. It "
            f"has {', '.join(sorted(formats))}.")

    wanted = [e.strip() for e in elements.split(",") if e.strip()]
    fetched = await ctx.basis_sets.render(
        manifest, key=entry.key, output_format=output_format,
        elements=wanted, name=entry.name)
    rendered = fetched.value

    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=f"{entry.name} ({formats[output_format]})",
                              dataset_version=entry.latest_version
                              or manifest.coverage.dataset_version)
    b.add_evidence(source_ref=ref, record_id=entry.key,
                   retrieved_at=fetched.retrieved_at,
                   transformations=[f"rendered into {output_format} format"])
    b.add_recipe(RecipeKind.landing_page,
                 f"{ctx.basis_sets.params_for(manifest).base_url}/"
                 f"basis/{entry.key}/",
                 label=f"{entry.name} at the Basis Set Exchange")

    data = {
        "key": entry.key,
        "name": entry.name,
        "family": entry.family,
        "role": entry.role,
        "description": entry.description,
        "version": entry.latest_version,
        "output_format": output_format,
        "format_name": formats[output_format],
        "elements_requested": rendered.elements or "all this set covers",
        "elements_available": entry.elements,
        "data": rendered.data,
        "references": rendered.references,
        "note": ("The basis set as this program's own input text, ready to "
                 "paste into a calculation. `references` holds the citations "
                 "for the set itself, which are separate from the citation "
                 "for the service and are what the publisher asks be cited "
                 "alongside it."),
    }
    if rendered.references is None:
        data["references_note"] = (
            "The reference file for this set could not be read. The set "
            "itself is the publisher's; cite it from its landing page "
            "rather than treating the absence as there being nothing to "
            "cite.")
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


MATERIALS_TOOLS.register(ToolSpec(
    name="materials.describe_structure_fields",
    description=(
        "Which properties the Materials Project's OPTIMADE endpoint can be "
        "filtered on, with the provider's own description of each and its "
        "extension fields marked — `_mp_stability` carries formation energy "
        "and energy above hull, which the standard does not define. ALWAYS "
        "walk here before writing a filter for materials.search_structures: "
        "a property this provider does not have is refused rather than "
        "ignored, and the extension fields are the ones nobody guesses."),
    toolset="default", contract_version="1",
    fn=describe_structure_fields))

MATERIALS_TOOLS.register(ToolSpec(
    name="materials.search_structures",
    description=(
        "Find computed inorganic crystal structures in the Materials "
        "Project by composition. `elements` is a comma-separated list every "
        "result must contain ('Ga,N'); `formula` is an exact reduced "
        "formula ('GaN'); `nelements` is a count with an optional operator "
        "('2', '<=3'); `filter` takes the OPTIMADE grammar directly for "
        "anything else. Returns structures and formulas — COMPUTED, not "
        "measured. This keyless route does not carry the energetics, "
        "electronic structure, or phase diagrams behind the Materials "
        "Project's own keyed API."),
    toolset="default", contract_version="1", fn=search_structures))

MATERIALS_TOOLS.register(ToolSpec(
    name="materials.get_structure",
    description=(
        "One computed structure in full by its id, which comes from "
        "materials.search_structures and looks like 'mp-1244984': formulas, "
        "elements and their ratios, site count, lattice vectors, space "
        "group, and the provider's own fields including its stability "
        "block."),
    toolset="default", contract_version="1", fn=get_structure))

MATERIALS_TOOLS.register(ToolSpec(
    name="chemistry.search_basis_sets",
    description=(
        "Find quantum-chemistry basis sets in the Basis Set Exchange, which "
        "originated at PNNL/EMSL and is run with MolSSI. `query` matches "
        "name, description and alternative names; `family` is the lineage "
        "(pople, dunning, ahlrichs, karlsruhe...); `role` is what the set "
        "is for (orbital, jkfit, rifit...); `covers_elements` takes symbols "
        "or atomic numbers and keeps only sets defined for ALL of them — "
        "the way to answer 'which basis sets cover uranium'. Returns the "
        "keys chemistry.get_basis_set takes."),
    toolset="default", contract_version="1", fn=search_basis_sets))

MATERIALS_TOOLS.register(ToolSpec(
    name="chemistry.get_basis_set",
    description=(
        "One basis set rendered as a named program's own input text, ready "
        "to paste into a calculation: NWChem, Gaussian, Psi4, ORCA, Molpro, "
        "CP2K and twenty more. `name` is a key or name from "
        "chemistry.search_basis_sets; `elements` narrows it to the ones you "
        "need, as symbols or atomic numbers. The citations for the set come "
        "back with it — the publisher asks that sets be cited and they live "
        "at a different endpoint from the data."),
    toolset="default", contract_version="1", fn=get_basis_set))
