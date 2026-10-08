"""Bio tools: microbiome studies and biosamples from NMDC.

Three tools over one publisher, in decision 0014's search / get / list
shape: find a study, read it whole with its sample count, then walk its
biosamples or search samples across studies. NMDC's API has 93 operations;
these three are the ones that answer "is there microbiome data from this
kind of place, and who collected it", which is the question that precedes
every request for the sequence data itself.

What none of them returns is sequence data. A study's DOIs lead to JGI,
EMSL and the sequence archives, and the tools hand those on as access
recipes rather than fetching them.
"""
from __future__ import annotations

from typing import Any

from ..adapters.nmdc import DEFAULT_ROWS, Biosample, Study
from ..core.assemble import pagination_coverage, result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RecipeKind,
                             SourceClaimCoverage)
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

BIO_TOOLS = ToolRegistry(package="bio")

NMDC_SOURCE = "nmdc-runtime"
STUDY_ROWS = 10
MAX_STUDY_ROWS = 50
DESCRIPTION_CHARS = 400

SEQUENCE_NOTE = (
    "Metadata only. Reads, assemblies and annotations are held at JGI, EMSL "
    "and the sequence archives; a study's DOIs in access_recipes lead to "
    "them and are never fetched here.")


def _study_summary(study: Study, *, full: bool) -> dict[str, Any]:
    description = study.description
    if description and not full and len(description) > DESCRIPTION_CHARS:
        description = description[:DESCRIPTION_CHARS].rstrip() + "…"
    out: dict[str, Any] = {
        "study_id": study.study_id, "name": study.name,
        "category": study.category, "ecosystem": study.ecosystem,
        "description": description,
        "investigators": [
            {"name": p.name, "orcid": p.orcid}
            | ({"roles": p.roles} if full else {})
            for p in study.investigators],
        "dois": study.dois,
        "part_of": study.part_of,
    }
    if full:
        out |= {"title": study.title, "funding": study.funding,
                "gold_ids": study.gold_ids, "websites": study.websites}
    return out


def _add_doi_recipes(b, study: Study) -> None:
    for doi in study.dois:
        b.add_recipe(RecipeKind.doi, f"https://doi.org/{doi['doi']}",
                     label=f"{study.name[:60]} ({doi['category'] or 'DOI'})",
                     instructions=("Minted by "
                                   f"{doi['provider'] or 'the publisher'}; "
                                   "returned as data and never fetched "
                                   "here."))


def _sample_summary(sample: Biosample) -> dict[str, Any]:
    return {"sample_id": sample.sample_id, "name": sample.name,
            "studies": sample.studies, "collected": sample.collected,
            "latitude": sample.latitude, "longitude": sample.longitude,
            "place": sample.place, "ecosystem": sample.ecosystem,
            "env_broad_scale": sample.env_broad_scale,
            "env_local_scale": sample.env_local_scale,
            "env_medium": sample.env_medium, "depth_m": sample.depth_m}


async def search_studies(ctx: RuntimeContext, text: str = "",
                         ecosystem: str = "", category: str = "",
                         rows: int = STUDY_ROWS, offset: int = 0) -> Envelope:
    b = builder(ctx, "bio.search_studies", contract_version="1")
    manifest = require_active_source(ctx, NMDC_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "bio.search_studies",
                                            [manifest])
    if rows < 1 or rows > MAX_STUDY_ROWS:
        raise InvalidQuery(f"rows must be between 1 and {MAX_STUDY_ROWS}.")
    if offset < 0:
        raise InvalidQuery("offset must be zero or more.")
    if category and category not in ("research_study", "consortium"):
        raise InvalidQuery("category is 'research_study' or 'consortium', "
                           "NMDC's own two values.")

    fetched = await ctx.nmdc.studies(manifest, text=text,
                                     ecosystem=ecosystem, category=category)
    page = fetched.value
    shown = page.studies[offset:offset + rows]
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for study in shown:
        b.add_evidence(source_ref=ref, record_id=study.study_id,
                       retrieved_at=fetched.retrieved_at,
                       transformations=["normalize", "drop_contact_details"])
        _add_doi_recipes(b, study)

    data: dict[str, Any] = {
        "studies": [_study_summary(s, full=False) for s in shown],
        "record_count": len(shown),
        "total_matches": page.total_matched,
        "studies_in_collection": page.collection_size,
        "offset": offset,
        "note": ("Text matches every word in a study's name, title or "
                 "description, without regard to case. " + SEQUENCE_NOTE),
    }
    if ecosystem:
        data["ecosystem_note"] = (
            "Most NMDC studies carry no ecosystem classification of their "
            "own; their samples do. A study missing here may still hold "
            "samples from that ecosystem: bio.search_biosamples with "
            "ecosystem_type finds them.")
    if not page.studies:
        data["note"] = (
            f"None of NMDC's {page.collection_size} studies matched. That is "
            "a statement about what NMDC has ingested, not about whether "
            "such a study exists. " + SEQUENCE_NOTE)

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(page.total_matched, len(shown),
                                       offset=offset),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(shown)), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        known_limitations=sorted(manifest.coverage.known_limitations)))


async def get_study(ctx: RuntimeContext, study_id: str) -> Envelope:
    b = builder(ctx, "bio.get_study", contract_version="1")
    manifest = require_active_source(ctx, NMDC_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "bio.get_study",
                                            [manifest])
    fetched = await ctx.nmdc.get_study(manifest, study_id)
    study = fetched.value
    counted = await ctx.nmdc.count_biosamples(manifest, study.study_id)
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    b.add_evidence(source_ref=ref, record_id=study.study_id,
                   retrieved_at=fetched.retrieved_at,
                   locator=fetched.request_url,
                   transformations=["normalize", "drop_contact_details"])
    _add_doi_recipes(b, study)

    data = _study_summary(study, full=True)
    data["biosample_count"] = counted.value
    data["note"] = SEQUENCE_NOTE
    if counted.value:
        b.next_action(
            finding=f"{counted.value} biosamples are linked to this study.",
            capability="bio.biosamples",
            reason="bio.search_biosamples with this study_id lists them "
                   "with coordinates, dates and environmental terms.")
    if study.part_of:
        data["part_of_note"] = (
            "This study is part of a larger NMDC study; its parent's id is "
            "in part_of and can be read with this tool.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(1), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        known_limitations=sorted(manifest.coverage.known_limitations)))


async def search_biosamples(ctx: RuntimeContext, study_id: str = "",
                            ecosystem_type: str = "", env_medium: str = "",
                            place: str = "", collected: str = "",
                            rows: int = DEFAULT_ROWS,
                            page: int = 1) -> Envelope:
    b = builder(ctx, "bio.search_biosamples", contract_version="1")
    manifest = require_active_source(ctx, NMDC_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "bio.biosamples",
                                            [manifest])
    filters = {"study_id": study_id, "ecosystem_type": ecosystem_type,
               "env_medium": env_medium, "place": place,
               "collected": collected}
    if not any(v.strip() for v in filters.values()):
        raise InvalidQuery(
            "give at least one of study_id, ecosystem_type, env_medium, "
            "place or collected. NMDC holds over 27,000 biosamples and an "
            "unfiltered first page answers nothing.")

    fetched = await ctx.nmdc.biosamples(manifest, filters=filters, rows=rows,
                                        page=page)
    result = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for sample in result.samples:
        b.add_evidence(source_ref=ref, record_id=sample.sample_id,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=sample.collected,
                       transformations=["normalize"])

    data: dict[str, Any] = {
        "biosamples": [_sample_summary(s) for s in result.samples],
        "record_count": len(result.samples),
        "total_matches": result.total,
        "page": page,
        "filter_applied": result.applied_filter,
        "note": ("Sample metadata as submitted. ecosystem_type is an exact "
                 "match on NMDC's value ('Soil', 'Freshwater'); env_medium "
                 "and place match a substring without regard to case; "
                 "collected matches the start of the date as written "
                 "('2017', '2017-06'). Depth is reported only where it was "
                 "stated in metres. " + SEQUENCE_NOTE),
    }
    if not result.samples:
        data["note"] = (
            "NMDC holds no biosample matching every filter given. Place is "
            "free text as submitted, so a state or site name can miss "
            "samples recorded under another spelling or only by "
            "coordinates. " + SEQUENCE_NOTE)

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(result.total, len(result.samples),
                                       offset=(page - 1) * rows),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(result.samples)),
        sources_searched=[manifest.id], sources_unavailable=gaps,
        known_limitations=sorted(manifest.coverage.known_limitations)))


BIO_TOOLS.register(ToolSpec(
    name="bio.search_studies",
    description=(
        "Search the microbiome studies the National Microbiome Data "
        "Collaborative (NMDC; LBNL with ANL, ORNL and PNNL) has ingested: "
        "soil, freshwater, marine, plant-associated, subsurface and other "
        "environmental microbiomes. `text` matches every word against a "
        "study's name, title and description; `ecosystem` matches the "
        "study's own classification, which most studies lack; `category` "
        "is research_study or consortium. Returns investigators with ORCID, "
        "DOIs leading to the sequence data, and the study id for "
        "bio.get_study and bio.search_biosamples. Metadata, not sequence "
        "data."),
    toolset="default", contract_version="1", fn=search_studies))

BIO_TOOLS.register(ToolSpec(
    name="bio.get_study",
    description=(
        "One NMDC microbiome study in full by its id ('nmdc:sty-11-...'): "
        "description, investigators and roles, funding statements, DOIs, "
        "GOLD ids, the parent study if it is part of one, and how many "
        "biosamples are linked to it."),
    toolset="default", contract_version="1", fn=get_study))

BIO_TOOLS.register(ToolSpec(
    name="bio.search_biosamples",
    description=(
        "Search NMDC's biosamples (over 27,000) by `study_id`, "
        "`ecosystem_type` (NMDC's exact value, e.g. 'Soil'), `env_medium` "
        "(an environmental-ontology term such as 'soil' or 'sediment'), "
        "`place` (a substring of the submitted location, e.g. 'Washington') "
        "or `collected` (the start of the date, '2017' or '2017-06'). Each "
        "sample comes with coordinates, collection date, place, ecosystem "
        "and environmental terms, and depth in metres where stated. Sample "
        "metadata, not sequence data."),
    toolset="default", contract_version="1", fn=search_biosamples))
