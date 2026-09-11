"""Earth tools: point weather, environmental datasets, climate-model search,
and an edge-sensor inventory.

Four publishers, four dialects, and one thing in common that decides how
these tools are written: three of the four answer a wrong request with data
instead of an error. Daymet returns all eight variables for a variable name
it does not recognise; ESS-DIVE returns its whole catalog for a filter it
does not know; the ESGF index returns retracted and superseded model output
first unless asked not to. Only the ESGF bridge refuses a bad parameter
outright, and it does so with a message worth repeating.

So the shape here is validate-then-ask, and every tool says what it
narrowed. The adapters hold the checks that stop a wrong request leaving;
these tools hold the ones a caller has to see in the answer — the range that
came back against the range that was asked for, the count of retracted
datasets in a page, the nodes a proximity search could not place.
"""
from __future__ import annotations

from typing import Any

from ..adapters.daymet import MAX_DAYS
from ..adapters.esgf import DEFAULT_ROWS as ESGF_ROWS
from ..adapters.esgf import MAX_ROWS as ESGF_MAX_ROWS
from ..adapters.essdive import DEFAULT_ROWS as ESSDIVE_ROWS
from ..adapters.sage import DEFAULT_ROWS as SAGE_ROWS
from ..core.assemble import pagination_coverage
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RecipeKind, ResultCoverage, SourceClaimCoverage, TimeRange,
                             WarningCode)
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

EARTH_TOOLS = ToolRegistry(package="earth")

DAYMET_SOURCE = "ornl-daac"
ESSDIVE_SOURCE = "ess-dive"
ESGF_SOURCE = "esgf-search"
SAGE_SOURCE = "anl-sage-waggle"

# The named facets `climate.search_cmip` takes as arguments. Everything else
# the bridge publishes is reachable through `filters`; these five are the
# ones every CMIP question is actually made of, and naming them is what
# keeps the tool signature readable without hiding the other hundred.
CMIP_ARGUMENTS = {"model": "source_id", "experiment": "experiment_id",
                  "variable": "variable_id", "frequency": "frequency",
                  "institution": "institution_id"}


async def get_daymet_point(ctx: RuntimeContext, latitude: float,
                           longitude: float, start: str, end: str,
                           variables: str = "") -> Envelope:
    b = builder(ctx, "earth.get_daymet_point", contract_version="1")
    manifest = require_active_source(ctx, DAYMET_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "earth.point_weather",
                                            [manifest])
    params = ctx.daymet.params_for(manifest)
    wanted = [v.strip() for v in variables.split(",") if v.strip()]

    fetched = await ctx.daymet.point_series(
        manifest, latitude=latitude, longitude=longitude, variables=wanted,
        start=start, end=end)
    series = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset="Daymet single-pixel extraction")
    b.add_evidence(source_ref=ref,
                   record_id=f"tile:{series.tile}@{latitude},{longitude}",
                   retrieved_at=fetched.retrieved_at,
                   effective_at=series.returned_start,
                   transformations=["day-of-year converted to calendar date"])

    b.warn(WarningCode.derived_layer,
           params.grid_note, manifest.id)
    missing = series.missing_leap_days()
    data: dict[str, Any] = {
        "latitude": series.latitude, "longitude": series.longitude,
        "elevation_meters": series.elevation_meters,
        "tile": series.tile,
        "projected_coordinates": series.projected_xy,
        "software_version": series.software_version,
        "publisher_citation": series.citation,
        "columns": [{"name": c.name, "units": c.units,
                     "description": params.variables.get(c.name)}
                    for c in series.columns],
        "days": [{"date": r.date, "yday": r.yday, "values": r.values}
                 for r in series.rows],
        "record_count": len(series.rows),
        "requested_range": {"from": series.requested_start,
                            "to": series.requested_end},
        "returned_range": {"from": series.returned_start,
                           "to": series.returned_end},
        "calendar": params.calendar_note,
        "note": ("Daily modelled surface weather for the 1 km cell holding "
                 "this point. Precipitation is a daily total; tmax and tmin "
                 "are daily extremes, so a daily mean has to be computed "
                 "and is not published. For observations at a station "
                 "rather than a modelled grid cell, this is the wrong "
                 "source."),
    }
    if missing:
        data["dates_absent"] = missing
        data["dates_absent_note"] = (
            "Daymet's year is 365 days long: 29 February is present and 31 "
            "December is dropped. The date(s) above are inside the range "
            "returned and are absent from the dataset rather than missing "
            "from this answer. An annual total for such a year is a 365-day "
            "total.")
    if series.rows and series.returned_start != series.requested_start:
        b.warn(WarningCode.screening_only,
               f"The series returned begins {series.returned_start}, not "
               f"{series.requested_start} as requested. The service moves a "
               "date outside its record rather than refusing it, so read "
               "the returned range rather than the requested one.",
               manifest.id)

    dates = [r.date for r in series.rows]
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(series.rows)), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        time_range=(TimeRange(**{"from": min(dates), "to": max(dates)})
                    if dates else None)))


async def search_datasets(ctx: RuntimeContext, text: str = "",
                          keywords: str = "", creator: str = "",
                          project: str = "", oldest_first: bool = False,
                          rows: int = ESSDIVE_ROWS,
                          offset: int = 0) -> Envelope:
    b = builder(ctx, "earth.search_datasets", contract_version="1")
    manifest = require_active_source(ctx, ESSDIVE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "earth.search_datasets",
                                            [manifest])
    filters = {"text": text, "keywords": keywords, "creator": creator,
               "providerName": project}
    fetched = await ctx.essdive.search(
        manifest, filters=filters,
        sort="dateUploaded:asc" if oldest_first else "dateUploaded:desc",
        rows=rows, offset=offset)
    page = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for package in page.packages:
        b.add_evidence(source_ref=ref, record_id=package.package_id,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=package.date_uploaded,
                       locator=package.view_url, transformations=[])
        if package.view_url:
            b.add_recipe(RecipeKind.landing_page, package.view_url,
                         label=package.name)

    returned = len(page.packages)
    data = {
        "datasets": [_package_summary(p) for p in page.packages],
        "record_count": returned,
        "total_matches": page.total,
        "offset": offset,
        "searched": page.applied_query,
        "note": ("Package-level metadata from DOE's environmental system "
                 "science repository. Each record points at files held in "
                 "the repository rather than carrying them; earth.get_dataset "
                 "returns one record in full. The search runs over metadata, "
                 "so a variable measured but never named in a record's "
                 "keywords or description is not findable here."),
    }
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(page.total, returned, offset=offset),
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(returned), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        known_limitations=list(manifest.coverage.known_limitations)))


async def get_dataset(ctx: RuntimeContext, package_id: str) -> Envelope:
    b = builder(ctx, "earth.get_dataset", contract_version="1")
    manifest = require_active_source(ctx, ESSDIVE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "earth.get_dataset",
                                            [manifest])
    fetched = await ctx.essdive.get_package(manifest, package_id)
    package = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=package.name, doi=package.doi)
    b.add_evidence(source_ref=ref, record_id=package.package_id,
                   retrieved_at=fetched.retrieved_at,
                   effective_at=package.date_uploaded,
                   locator=package.view_url, transformations=[])
    if package.view_url:
        b.add_recipe(RecipeKind.landing_page, package.view_url,
                     label=package.name,
                     instructions="The data files are downloaded from this "
                                  "page; the API returns metadata only.")

    record = _package_summary(package) | {
        "methods": package.methods,
        "license": package.license_url,
        "citation": package.citation,
        "note": ("One package's metadata in full. The files it describes are "
                 "not included and are reached through the landing page in "
                 "access_recipes. Creator email addresses are in the "
                 "publisher's record and are dropped here."),
    }
    return b.build(record, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


def _package_summary(package) -> dict[str, Any]:
    return {
        "package_id": package.package_id,
        "doi": package.doi,
        "name": package.name,
        "description": package.description,
        "project": package.project,
        "creators": [{"name": p.name, "affiliation": p.affiliation,
                      "orcid": p.orcid} for p in package.creators],
        "funders": package.funders,
        "keywords": package.keywords,
        "places": package.places,
        "temporal_coverage": package.temporal_coverage,
        "date_published": package.date_published,
        "date_uploaded": package.date_uploaded,
        "date_modified": package.date_modified,
        "view_url": package.view_url,
    }


async def discover_facets(ctx: RuntimeContext, facet: str = "",
                          project: str = "CMIP6") -> Envelope:
    b = builder(ctx, "climate.discover_facets", contract_version="1")
    manifest = require_active_source(ctx, ESGF_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "climate.discover_facets",
                                            [manifest])
    described = await ctx.esgf.describe(manifest)
    schema = described.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=described.retrieved_at,
                              cache_age_seconds=described.cache_age_seconds,
                              from_cache=described.from_cache,
                              dataset_version=schema.version or "current")

    data: dict[str, Any] = {
        "project": project,
        "facet_count": len(schema.facets),
        "note": ("The searchable facets this index publishes. Call again "
                 "with `facet` set to one of them for the values it takes "
                 "and how many datasets carry each, then pass those values "
                 "to climate.search_cmip. Searching this archive without "
                 "walking here first means guessing at controlled "
                 "vocabularies: a model name that is nearly right returns "
                 "nothing at all."),
    }
    counted = 0
    if facet:
        schema.check([facet])
        fetched = await ctx.esgf.search(
            manifest, schema=schema, filters={"project": project},
            facets=[facet], rows=0)
        page = fetched.value
        values = page.facet_counts.get(facet, {})
        counted = len(values)
        data |= {
            "facet": facet,
            "description": schema.facets.get(facet) or None,
            "values": [{"value": v, "datasets": n}
                       for v, n in sorted(values.items(),
                                          key=lambda kv: -kv[1])],
            "distinct_values": counted,
            "datasets_in_project": page.num_found,
        }
        b.add_evidence(source_ref=ref, record_id=f"facet:{facet}",
                       retrieved_at=fetched.retrieved_at, transformations=[])
    else:
        data["facets"] = [{"name": name, "description": text or None}
                          for name, text in sorted(schema.facets.items())]
        b.add_evidence(source_ref=ref, record_id="published-facet-list",
                       retrieved_at=described.retrieved_at,
                       transformations=[])

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(counted if facet else len(schema.facets)),
        sources_searched=[manifest.id], sources_unavailable=gaps))


async def search_cmip(ctx: RuntimeContext, model: str = "",
                      experiment: str = "", variable: str = "",
                      frequency: str = "", institution: str = "",
                      project: str = "CMIP6", filters: str = "",
                      include_superseded: bool = False,
                      rows: int = ESGF_ROWS, offset: int = 0) -> Envelope:
    b = builder(ctx, "climate.search_cmip", contract_version="1")
    manifest = require_active_source(ctx, ESGF_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "climate.search_cmip",
                                            [manifest])
    if rows < 1 or rows > ESGF_MAX_ROWS:
        raise InvalidQuery(f"rows must be between 1 and {ESGF_MAX_ROWS}.")

    described = await ctx.esgf.describe(manifest)
    schema = described.value
    named = {"model": model, "experiment": experiment, "variable": variable,
             "frequency": frequency, "institution": institution}
    clauses = {"project": project}
    clauses |= {CMIP_ARGUMENTS[name]: value.strip()
                for name, value in named.items() if value.strip()}
    clauses |= _parse_facet_filters(filters)

    fetched = await ctx.esgf.search(manifest, schema=schema, filters=clauses,
                                    rows=rows, offset=offset,
                                    latest=None if include_superseded
                                    else True)
    page = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset_version=project)
    for dataset in page.datasets:
        b.add_evidence(source_ref=ref, record_id=dataset.dataset_id,
                       retrieved_at=fetched.retrieved_at,
                       effective_at=dataset.datetime_start,
                       locator=dataset.citation_url, transformations=[])
        if dataset.further_info_url:
            b.add_recipe(RecipeKind.landing_page, dataset.further_info_url,
                         label=f"{dataset.source_id} {dataset.experiment_id} "
                               "documentation")
        if dataset.citation_url:
            b.add_recipe(RecipeKind.doi, dataset.citation_url,
                         label=f"Citation record for {dataset.dataset_id}")

    returned = len(page.datasets)
    data = {
        "datasets": [_climate_summary(d) for d in page.datasets],
        "record_count": returned,
        "total_matches": page.num_found,
        "offset": offset,
        "applied_filters": page.applied_filters,
        "latest_only": not include_superseded,
        "note": ("Metadata for climate-model output, not the output itself: "
                 "each record names the data node holding its files and the "
                 "methods that node serves them by. Restricted to the latest "
                 "version of each dataset unless include_superseded is set — "
                 "the index holds retracted and superseded output and sorts "
                 "it first otherwise. Walk climate.discover_facets for valid "
                 "model, experiment, and variable names; a near-miss returns "
                 "nothing rather than a suggestion."),
    }
    if page.retracted_count:
        data["retracted_in_page"] = page.retracted_count
        b.warn(WarningCode.stale_source,
               f"{page.retracted_count} of the {returned} datasets on this "
               "page are marked retracted by the modelling centre that "
               "published them. A retraction in CMIP is a withdrawal, often "
               "for an error in the run; do not report those values without "
               "saying so.", manifest.id)
    if (page.num_found is not None and offset + returned < page.num_found
            and offset + rows > 9000):
        b.warn(WarningCode.truncated_inline,
               "Paging this index stops at offset 9,999. A question that "
               "runs past it is answered by narrowing facets, not by paging.",
               manifest.id)

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(page.num_found, returned,
                                       offset=offset),
        source_claim=SourceClaimCoverage.partial,
        result=result_dim(returned), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        known_limitations=list(manifest.coverage.known_limitations)))


def _parse_facet_filters(text: str) -> dict[str, str]:
    """`grid_label=gn;realm=atmos` into a mapping.

    Semicolon-separated because that is the form the facility tools already
    take, and one filter syntax across the project is worth more than a
    syntax per publisher.
    """
    out: dict[str, str] = {}
    for clause in text.split(";"):
        if not clause.strip():
            continue
        name, sep, value = clause.partition("=")
        if not sep or not value.strip():
            raise InvalidQuery(
                f"filter {clause.strip()!r} is not in the form "
                "'facet=value'; separate several with semicolons.")
        out[name.strip()] = value.strip()
    return out


def _climate_summary(dataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "project": dataset.project,
        "model": dataset.source_id,
        "institution": dataset.institution_id,
        "experiment": dataset.experiment_id,
        "experiment_title": dataset.experiment_title,
        "variables": dataset.variables,
        "variable_long_names": dataset.variable_long_names,
        "variable_units": dataset.variable_units,
        "frequency": dataset.frequency,
        "realm": dataset.realm,
        "nominal_resolution": dataset.nominal_resolution,
        "grid_label": dataset.grid_label,
        "variant_label": dataset.variant_label,
        "version": dataset.version,
        "covers": {"from": dataset.datetime_start,
                   "to": dataset.datetime_stop},
        "files": dataset.number_of_files,
        "size_bytes": dataset.size_bytes,
        "data_node": dataset.data_node,
        "access_methods": dataset.access_methods,
        "latest": dataset.latest,
        "retracted": dataset.retracted,
        "replica": dataset.replica,
    }


async def find_nodes(ctx: RuntimeContext, vsn: str = "", project: str = "",
                     phase: str = "", instrument: str = "",
                     latitude: float | None = None,
                     longitude: float | None = None, within_km: float = 0.0,
                     rows: int = SAGE_ROWS, offset: int = 0) -> Envelope:
    b = builder(ctx, "sensors.find_nodes", contract_version="1")
    manifest = require_active_source(ctx, SAGE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "sensors.node_inventory",
                                            [manifest])
    if (latitude is None) != (longitude is None):
        raise InvalidQuery(
            "a proximity search needs both latitude and longitude.")
    near = (latitude, longitude) if latitude is not None else None

    fetched = await ctx.sage.nodes(manifest, vsn=vsn, project=project,
                                   phase=phase, instrument=instrument,
                                   near=near, within_km=within_km, rows=rows,
                                   offset=offset)
    page = fetched.value
    params = ctx.sage.params_for(manifest)
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for node in page.nodes:
        b.add_evidence(source_ref=ref, record_id=node.vsn,
                       retrieved_at=fetched.retrieved_at, transformations=[])

    returned = len(page.nodes)
    data = {
        "nodes": [{"vsn": n.vsn, "node_id": n.node_id, "phase": n.phase,
                   "deployed": n.deployed, "project": n.project,
                   "address": n.address, "latitude": n.latitude,
                   "longitude": n.longitude, "tags": n.tags,
                   "computers": n.computes,
                   "instruments": [{"name": s.name, "hardware": s.hardware,
                                    "model": s.model,
                                    "manufacturer": s.manufacturer,
                                    "datasheet": s.datasheet,
                                    "active": s.is_active}
                                   for s in n.sensors]}
                  for n in page.nodes],
        "record_count": returned,
        "total_matches": page.total_matched,
        "nodes_in_network": page.total_nodes,
        "offset": offset,
        "sorted_by": "distance" if near else "the publisher's own order",
        "measurements": params.measurement_note,
        "note": ("Where the network's nodes are and what is on them. This is "
                 "the inventory, NOT the readings: a node listed here has "
                 "instruments, and what those instruments recorded is a "
                 "different service. Deployment phase is the publisher's own "
                 "word for a node's state, and a node in this list is not "
                 "necessarily one that is running."),
    }
    if page.without_coordinates:
        data["without_coordinates"] = page.without_coordinates
        data["without_coordinates_note"] = (
            f"{page.without_coordinates} matching node(s) carry no position, "
            "most of them awaiting deployment. They are counted in "
            "total_matches and cannot be ranked by distance.")
    b.add_recipe(RecipeKind.contact_required, params.measurement_endpoint,
                 label="Sage measurement query API",
                 instructions=params.measurement_note)

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(page.total_matched, returned,
                                       offset=offset),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(returned), sources_searched=[manifest.id],
        sources_unavailable=gaps))


EARTH_TOOLS.register(ToolSpec(
    name="earth.get_daymet_point",
    description=(
        "Daily surface weather for one point in North America, Hawaii, or "
        "Puerto Rico, 1980 onward, from ORNL DAAC's Daymet: maximum and "
        "minimum temperature, precipitation, shortwave radiation, snow water "
        "equivalent, vapour pressure, and day length. `variables` is a "
        "comma-separated list (default all); `start` and `end` are ISO dates "
        f"spanning at most {MAX_DAYS} days. These are MODELLED values for "
        "the 1 km cell containing the point, interpolated from ground "
        "stations — not a station observation. Daymet's year is 365 days "
        "long: 29 February is kept and 31 December is dropped, and the "
        "answer names any such date inside the range."),
    toolset="default", contract_version="1", fn=get_daymet_point))

EARTH_TOOLS.register(ToolSpec(
    name="earth.search_datasets",
    description=(
        "Search ESS-DIVE, DOE's repository for environmental system science "
        "data: terrestrial ecosystem, watershed, and subsurface "
        "biogeochemistry studies, including NGEE Arctic and Tropics, SPRUCE, "
        "and the integrated field laboratories. `text` searches the whole "
        "record; `keywords` matches the depositor's own keyword list and is "
        "much narrower; `creator` is an author name; `project` is the field "
        "laboratory or campaign. Returns metadata and landing pages, not "
        "data files. For one record in full use earth.get_dataset."),
    toolset="default", contract_version="1", fn=search_datasets))

EARTH_TOOLS.register(ToolSpec(
    name="earth.get_dataset",
    description=(
        "One ESS-DIVE dataset in full by its package id, which comes from "
        "earth.search_datasets: creators and their ORCIDs, funding "
        "programme, licence, keywords, the methods the depositor described, "
        "the places and period it covers, and its citation. The data files "
        "themselves are reached through the landing page in "
        "access_recipes."),
    toolset="default", contract_version="1", fn=get_dataset))

EARTH_TOOLS.register(ToolSpec(
    name="climate.discover_facets",
    description=(
        "Walk the ESGF climate-model index before searching it. Call with no "
        "argument for the searchable facets it publishes; call with `facet` "
        "set to one of them — source_id, experiment_id, variable_id, "
        "frequency, nominal_resolution — for the values it takes and how "
        "many datasets carry each. ALWAYS walk here first: the archive holds "
        "millions of datasets under controlled vocabularies, and a model or "
        "variable name that is nearly right returns nothing at all rather "
        "than a suggestion."),
    toolset="default", contract_version="1", fn=discover_facets))

EARTH_TOOLS.register(ToolSpec(
    name="climate.search_cmip",
    description=(
        "Find climate-model output in the ESGF index: which model ran which "
        "experiment for which variable, at what frequency and resolution, "
        "over what period, and which data node holds the files. `model`, "
        "`experiment`, `variable`, `frequency`, and `institution` are the "
        "named facets; `filters` takes any other as 'grid_label=gn;"
        "realm=atmos'. Values must be exact — walk climate.discover_facets "
        "first. Returns the latest version of each dataset by default "
        "because the index holds retracted and superseded output and sorts "
        "it first otherwise. Metadata only; the output lives on federated "
        "data nodes, some of which need a federation account."),
    toolset="default", contract_version="1", fn=search_cmip))

EARTH_TOOLS.register(ToolSpec(
    name="sensors.find_nodes",
    description=(
        "Where the Sage/Waggle edge-sensor network's nodes are and what is "
        "on them: deployment phase, project, host address, coordinates, "
        "onboard computers, and instruments — cameras, weather and "
        "air-quality packages, microphones, rain gauges, lidar. Filter by "
        "`vsn` (the node's call sign, like W08D), `project`, `phase`, or "
        "`instrument`, or pass `latitude`/`longitude` with an optional "
        "`within_km` for the nearest ones. An INVENTORY, not measurements: "
        "the readings are a separate service and the answer says how to "
        "reach it."),
    toolset="default", contract_version="1", fn=find_nodes))
