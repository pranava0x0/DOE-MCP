"""Energy tools: EIA's statistics tree and the DOE/EPA vehicle service.

EIA is where decision 0014's canonical four-tool shape earns itself. The API
is self-describing — every route lists its children, and a leaf lists its
facets, frequencies, and data columns — so `discover_routes` / `get_data` /
`grid_status` cover a tree that would otherwise want a tool per dataset. A
hundred datasets do not need a hundred tools; they need one tool that can
read a directory.

Vehicle lookups sit here rather than in their own server because
fueleconomy.gov is two endpoints and a drill-down, and a server for that
would breach the merge-before-splitting rule in architecture Part 1 § 3.4.
BPA's operations feed and the two facility inventories are here because
grid readings and facility inventories are what is measured, and what is
measured is this server's domain (decision 0026). The planned
`doe-energy-tech` server takes what is modelled, tested or published for a
technology; the two domains are also the two credential classes.
"""
from __future__ import annotations

from typing import Any

from ..adapters.eia_v2 import MAX_ROWS_PER_RESPONSE
from ..adapters.postgrest import (DEFAULT_ROWS, Filter,
                                  parse_filters, parse_order)
from ..core.assemble import pagination_coverage
from ..core.assemble import result_dim, selection_coverage
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage,
                             PaginationCoverage, RegistryCoverage,
                             ResultCoverage, SourceClaimCoverage, TimeRange,
                             WarningCode)
from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

ENERGY_TOOLS = ToolRegistry(package="energy")

EIA_SOURCE = "eia-api-v2"
VEHICLE_SOURCE = "fueleconomy-ws"
BPA_SOURCE = "bpa-operations"
WIND_TURBINE_SOURCE = "usgs-uswtdb"
SOLAR_FACILITY_SOURCE = "usgs-uspvdb"

# EIA-930's hourly grid route. Named because "grid status" is the question
# people actually ask, and making them discover the route first would be a
# worse tool.
GRID_ROUTE = "electricity/rto/region-data"

# EIA-930's `type` facet: four different metrics sharing one route, one
# `value` column, and one period axis. Filtering on it is not optional.
#
# The day-ahead forecast is published for hours that have not happened yet,
# so on a query sorted by period descending it ALWAYS sorts above actual
# demand — by a full day, permanently, for every balancing authority. Until
# 2026-09-08 this tool sent no type filter, and so answered "how much
# electricity did CISO use last night" with tomorrow's forecast: 30,991 MWh
# for 2026-09-09T07 where the demand it was asked for was 38,701 MWh for
# 2026-09-08T22. The `type-name` field distinguished them and nothing else
# did. The bug was invisible for six days because no key was configured and
# every test replayed a fixture recorded from a single-type query.
GRID_METRICS = {
    "demand": ("D", "Demand"),
    "forecast": ("DF", "Day-ahead demand forecast"),
    "generation": ("NG", "Net generation"),
    "interchange": ("TI", "Total interchange"),
}
GRID_METRIC_CODES = {code: (name, label)
                     for name, (code, label) in GRID_METRICS.items()}


async def discover_routes(ctx: RuntimeContext, route: str = "") -> Envelope:
    b = builder(ctx, "energy.discover_routes", contract_version="1")
    manifest = require_active_source(ctx, EIA_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "energy.discover_routes",
                                            [manifest])
    fetched = await ctx.eia.describe_route(manifest, route)
    node = fetched.value
    now = fetched.retrieved_at
    ref = add_manifest_source(b, ctx, manifest, retrieved_at=now,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=node.name or "EIA API v2")
    b.add_evidence(source_ref=ref, record_id=node.route_id or "(root)",
                   retrieved_at=now, transformations=[])

    data: dict[str, Any] = {
        "route": node.route_id or "(root)",
        "name": node.name,
        "description": node.description,
        "is_dataset": node.is_leaf,
    }
    if node.children:
        data["child_routes"] = [
            {"id": c.get("id"), "name": c.get("name"),
             "description": c.get("description")}
            for c in node.children]
    if node.is_leaf:
        data["frequencies"] = [{"id": f.get("id"), "format": f.get("format"),
                                "description": f.get("description")}
                               for f in node.frequencies]
        data["facets"] = [{"id": f.get("id"), "description":
                           f.get("description")} for f in node.facets]
        data["data_columns"] = sorted(node.data_columns)
        data["period"] = {"start": node.start_period, "end": node.end_period}
        data["note"] = (
            "A dataset route. Pass it to energy.get_data with a frequency and "
            "the data_columns you want. Column names must be exact: EIA "
            "returns rows with no value column and NO error if a column name "
            "is wrong, which looks like an empty dataset rather than a typo.")
    else:
        data["note"] = ("A branch, not a dataset. Call again with one of the "
                        "child route ids to walk down.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=ResultCoverage.hit, sources_searched=[manifest.id],
        sources_unavailable=gaps))


async def get_data(ctx: RuntimeContext, route: str, frequency: str = "",
                   data_columns: str = "", facets: str = "",
                   start: str = "", end: str = "",
                   rows: int = 24, offset: int = 0) -> Envelope:
    b = builder(ctx, "energy.get_data", contract_version="1")
    manifest = require_active_source(ctx, EIA_SOURCE)
    columns = [c.strip() for c in data_columns.split(",") if c.strip()]
    facet_map: dict[str, list[str]] = {}
    for clause in facets.split(";"):
        if not clause.strip():
            continue
        name, _, values = clause.partition("=")
        if not values.strip():
            raise InvalidQuery(
                f"facet clause {clause!r} has no value. Use "
                "'respondent=CISO,ERCO;fueltype=WND', and get the facet names "
                "from energy.discover_routes on this route.")
        facet_map[name.strip()] = [v.strip() for v in values.split(",")
                                   if v.strip()]

    fetched = await ctx.eia.get_data(manifest, route,
                                     frequency=frequency or None,
                                     data_columns=columns or None,
                                     facets=facet_map or None,
                                     start=start or None, end=end or None,
                                     rows=rows, offset=offset)
    result = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache, dataset=route)
    for i, row in enumerate(result.rows):
        b.add_evidence(source_ref=ref,
                       record_id=f"{route}#{offset + i}",
                       retrieved_at=fetched.retrieved_at,
                       effective_at=str(row.get("period") or "") or None,
                       transformations=[])

    periods = [str(r.get("period")) for r in result.rows if r.get("period")]
    data: dict[str, Any] = {
        "route": route, "rows": result.rows, "record_count": len(result.rows),
        "total_matches": result.total,
    }
    if not result.rows:
        data["note"] = (
            "EIA returned no rows. Check the data_columns against "
            "energy.discover_routes for this route: a wrong column name "
            "returns an empty result rather than an error, and reads exactly "
            "like a dataset with no data.")
    if result.truncated:
        b.next_action(
            finding=f"EIA reports {result.total} matching rows; "
                    f"{len(result.rows)} returned.",
            capability="energy.get_data",
            reason=f"Page with offset, up to EIA's ceiling of "
                   f"{MAX_ROWS_PER_RESPONSE} rows per response. Do not "
                   "describe this page as the full series.")

    return b.build(data, Coverage(
        registry=RegistryCoverage.covered,
        execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(result.total, len(result.rows),
                                       offset=offset),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(result.rows)),
        sources_searched=[manifest.id],
        time_range=(TimeRange(**{"from": min(periods), "to": max(periods)})
                    if periods else None),
        known_limitations=sorted(manifest.coverage.known_limitations)))


async def grid_status(ctx: RuntimeContext, balancing_authority: str,
                      hours: int = 24, metric: str = "demand") -> Envelope:
    b = builder(ctx, "energy.grid_status", contract_version="1")
    manifest = require_active_source(ctx, EIA_SOURCE)
    if not balancing_authority.strip():
        raise InvalidQuery(
            "give a balancing authority code, e.g. CISO (California ISO), "
            "ERCO (ERCOT), PJM, MISO, ISNE, NYIS, BPAT. "
            "energy.discover_routes on 'electricity/rto/region-data' lists "
            "the full respondent facet.")
    if hours < 1 or hours > 720:
        raise InvalidQuery("hours must be between 1 and 720 (30 days).")
    wanted = metric.strip().lower()
    if wanted in GRID_METRICS:
        code, label = GRID_METRICS[wanted]
    elif metric.strip().upper() in GRID_METRIC_CODES:
        code = metric.strip().upper()
        label = GRID_METRIC_CODES[code][1]
    else:
        raise InvalidQuery(
            f"metric {metric!r} is not one of {sorted(GRID_METRICS)} (or "
            f"EIA's own codes {sorted(GRID_METRIC_CODES)}). These are four "
            "different measurements sharing one route: asking for the wrong "
            "one returns real numbers that answer a different question.")

    fetched = await ctx.eia.get_data(
        manifest, GRID_ROUTE, frequency="hourly", data_columns=["value"],
        facets={"respondent": [balancing_authority.strip().upper()],
                "type": [code]},
        rows=min(hours, MAX_ROWS_PER_RESPONSE), sort_column="period",
        sort_direction="desc")
    result = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset="EIA-930 hourly electric grid monitor")
    for i, row in enumerate(result.rows):
        b.add_evidence(source_ref=ref, record_id=f"{GRID_ROUTE}#{i}",
                       retrieved_at=fetched.retrieved_at,
                       effective_at=str(row.get("period") or "") or None,
                       transformations=[])

    periods = [str(r.get("period")) for r in result.rows if r.get("period")]
    # What EIA actually labelled the rows, rather than what was asked for.
    # One row of a different type would mean the facet had stopped filtering,
    # and mixing metrics under one `value` column is the failure this tool
    # shipped with.
    returned = {str(r.get("type")) for r in result.rows if r.get("type")}
    if returned - {code}:
        raise SourceSchemaChanged(
            f"asked EIA-930 for type={code} and it returned "
            f"{sorted(returned)}. The type facet has stopped filtering, and "
            "these metrics share a single `value` column — reporting them "
            "together would mix a forecast into a demand series.")

    data: dict[str, Any] = {
        "balancing_authority": balancing_authority.strip().upper(),
        "metric": label,
        "metric_code": code,
        "observations": result.rows,
        "record_count": len(result.rows),
        "units": "megawatthours",
        "note": (
            f"EIA-930 hourly {label.lower()} for this balancing authority. "
            "Recent hours are PRELIMINARY and are revised: a number read "
            "today for last night may change. Treat it as operational "
            "reporting, not as a settled statistic. This route carries four "
            f"metrics — {', '.join(sorted(GRID_METRICS))} — under one `value` "
            "column, and only the one named above is here."),
    }
    if not result.rows:
        data["note"] = (
            f"No hourly rows of {label.lower()} for "
            f"{balancing_authority!r}. Either the code is not an EIA-930 "
            "respondent, or the reporting lag has not caught up. Check the "
            "respondent facet with energy.discover_routes.")

    if code == "DF":
        # The one metric whose newest row is in the future by design.
        data["forecast_note"] = (
            "These are FORECAST hours, published ahead of time. The most "
            "recent period here has not happened yet, so this is what the "
            "operator expected rather than what was measured. For what "
            "actually happened, ask for metric='demand'.")
        b.warn(WarningCode.screening_only,
               "A day-ahead demand forecast is a prediction, not a "
               "measurement, and its latest hours are in the future. It must "
               "not be reported as what the grid did.", manifest.id)
    else:
        b.warn(WarningCode.stale_source,
               "EIA-930 publishes with a reporting lag and revises recent "
               "hours. The most recent observation here is not 'now'.",
               manifest.id)

    return b.build(data, Coverage(
        registry=RegistryCoverage.covered,
        execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(result.total, len(result.rows)),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(result.rows)),
        sources_searched=[manifest.id],
        time_range=(TimeRange(**{"from": min(periods), "to": max(periods)})
                    if periods else None)))


async def find_vehicle(ctx: RuntimeContext, year: str = "", make: str = "",
                       model: str = "", vehicle_id: str = "") -> Envelope:
    b = builder(ctx, "fuel.find_vehicle", contract_version="1")
    manifest = require_active_source(ctx, VEHICLE_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources, "vehicle.find",
                                            [manifest])

    if vehicle_id.strip():
        fetched = await ctx.fueleconomy.get_vehicle(manifest, vehicle_id)
        vehicle = fetched.value
        now = fetched.retrieved_at
        ref = add_manifest_source(b, ctx, manifest, retrieved_at=now,
                                  cache_age_seconds=fetched.cache_age_seconds,
                                  from_cache=fetched.from_cache)
        if vehicle is None:
            return b.build(
                {"vehicle": None,
                 "note": f"No vehicle {vehicle_id!r}. Ids come from the "
                         "year/make/model drill-down, not from guessing."},
                Coverage(registry=registry_dim,
                         execution=ExecutionCoverage.complete,
                         pagination=PaginationCoverage.complete,
                         result=ResultCoverage.empty,
                         sources_searched=[manifest.id]))
        b.add_evidence(source_ref=ref, record_id=vehicle.vehicle_id,
                       retrieved_at=now, transformations=["field_subset"])
        return b.build(
            {"vehicle": vehicle.fields, "vehicle_id": vehicle.vehicle_id,
             "note": "EPA test-cycle ratings, not measured real-world "
                     "consumption. Comparing them against a driver's own "
                     "mileage compares two different things."},
            Coverage(registry=registry_dim,
                     execution=ExecutionCoverage.complete,
                     pagination=PaginationCoverage.complete,
                     source_claim=SourceClaimCoverage.complete,
                     result=ResultCoverage.hit,
                     sources_searched=[manifest.id]))

    if not year.strip():
        fetched = await ctx.fueleconomy.menu(manifest, "year")
        step, selectors = "year", {}
    elif not make.strip():
        fetched = await ctx.fueleconomy.menu(manifest, "make", year=year)
        step, selectors = "make", {"year": year}
    elif not model.strip():
        fetched = await ctx.fueleconomy.menu(manifest, "model", year=year,
                                             make=make)
        step, selectors = "model", {"year": year, "make": make}
    else:
        fetched = await ctx.fueleconomy.menu(manifest, "options", year=year,
                                             make=make, model=model)
        step, selectors = "options", {"year": year, "make": make,
                                      "model": model}
    options = fetched.value
    now = fetched.retrieved_at

    # The menu is the most cached thing in this server — the year list
    # changes once a year — so its provenance has to say "cache" when it is.
    ref = add_manifest_source(b, ctx, manifest, retrieved_at=now,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    for opt in options:
        b.add_evidence(source_ref=ref, record_id=opt.value, retrieved_at=now,
                       transformations=[])

    data: dict[str, Any] = {
        "step": step, "selected": selectors,
        "options": [{"text": o.text, "value": o.value} for o in options],
        "record_count": len(options),
    }
    if step == "options":
        data["note"] = ("Each option's value is a vehicle_id. Call again with "
                        "vehicle_id to get the full record.")
    elif not options:
        data["note"] = (f"No {step} options for {selectors}. Check the "
                        "spelling against the previous step's options — the "
                        "service matches exactly.")
    else:
        data["note"] = (f"Pick a {step} and call again with it. The service "
                        "is a strict drill-down: year, then make, then model, "
                        "then options.")

    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(options)), sources_searched=[manifest.id],
        sources_unavailable=gaps))


async def get_fuel_prices(ctx: RuntimeContext) -> Envelope:
    b = builder(ctx, "fuel.get_prices", contract_version="1")
    manifest = require_active_source(ctx, VEHICLE_SOURCE)
    fetched = await ctx.fueleconomy.fuel_prices(manifest)
    prices = fetched.value
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache)
    b.add_evidence(source_ref=ref, record_id="fuelprices",
                   retrieved_at=fetched.retrieved_at, transformations=[])
    return b.build(
        {"prices_usd": prices, "record_count": len(prices),
         "note": "National averages in US dollars per gallon, except "
                 "electricity, which is per kilowatt-hour. One current "
                 "figure per fuel with no history and no regional "
                 "breakdown — those are EIA series, reachable through "
                 "energy.get_data."},
        Coverage(registry=RegistryCoverage.covered,
                 execution=ExecutionCoverage.complete,
                 pagination=PaginationCoverage.complete,
                 source_claim=SourceClaimCoverage.complete,
                 result=result_dim(len(prices)),
                 sources_searched=[manifest.id]))


async def get_bpa_operations(ctx: RuntimeContext, feed: str = "",
                             intervals: int = 12) -> Envelope:
    b = builder(ctx, "grid.get_bpa_operations", contract_version="1")
    manifest = require_active_source(ctx, BPA_SOURCE)
    registry_dim, gaps = selection_coverage(ctx.sources,
                                            "grid.operations_feed",
                                            [manifest])
    if intervals < 1 or intervals > 2016:
        raise InvalidQuery(
            "intervals must be between 1 and 2016 (the seven-day window at "
            "five-minute resolution). 12 is the last hour.")

    fetched = await ctx.text_feed.read_feed(manifest, feed,
                                            intervals=intervals)
    table = fetched.value
    params = ctx.text_feed.params_for(manifest)
    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              dataset=table.title)
    for row in table.rows:
        b.add_evidence(source_ref=ref, record_id=f"{table.feed}#{row.timestamp}",
                       retrieved_at=fetched.retrieved_at,
                       effective_at=row.timestamp, transformations=[])

    latest = table.latest
    data: dict[str, Any] = {
        "feed": table.feed,
        "title": table.title,
        "units": params.units,
        "columns": table.columns,
        "latest": ({"timestamp": latest.timestamp, "values": latest.values}
                   if latest else None),
        "intervals": [{"timestamp": r.timestamp, "values": r.values}
                      for r in table.rows],
        "record_count": len(table.rows),
        "reported_through": table.reported_through,
        "window": table.window,
        "window_intervals": table.available_intervals,
        "pending_intervals": table.pending_intervals,
        "publisher_last_updated": table.published_note,
        "timestamps": params.timezone_note,
        "note": (
            "Five-minute SCADA readings from BPA's balancing authority, in "
            "megawatts. VER is variable energy resources — wind and solar "
            "together. The balancing authority includes loads and resources "
            "that are not BPA's own and excludes BPA loads served by "
            "transfer, so this is not a figure for 'BPA the utility'. For "
            "hourly history, or for any other balancing authority, use "
            "energy.grid_status; BPA's EIA-930 respondent code is BPAT."),
    }
    if table.pending_intervals:
        data["pending_note"] = (
            f"The file also listed {table.pending_intervals} later interval(s) "
            "today with a timestamp and no values. Those have not happened "
            "yet; they are not zero generation and not an outage. They are "
            "excluded from every figure above.")
    if not table.rows:
        data["note"] = (
            "The feed parsed but carried no populated intervals, which means "
            "BPA published the file without data rather than that the grid "
            "was idle. Outage on the publisher's side, not an empty result.")

    b.warn(WarningCode.screening_only,
           "Operational SCADA readings published minutes after the fact. "
           "They are situational awareness, not settled, revision-controlled "
           "or billing-quality figures, and should not be used as a "
           "system-of-record number.", manifest.id)

    timestamps = [r.timestamp for r in table.rows]
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=(PaginationCoverage.truncated
                    if len(table.rows) < table.available_intervals
                    else PaginationCoverage.complete),
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(len(table.rows)), sources_searched=[manifest.id],
        sources_unavailable=gaps,
        time_range=(TimeRange(**{"from": min(timestamps),
                                 "to": max(timestamps)})
                    if timestamps else None)))



async def _find_facilities(ctx: RuntimeContext, *, source_id: str, tool: str,
                           what: str, state: str, county: str, name: str,
                           filters: str, bbox: str, order: str, rows: int,
                           offset: int) -> Envelope:
    """The body behind both facility tools.

    One function because the two sources are one PostgreSQL schema behind two
    path aliases, and every difference between them — which table, which
    column holds the state, how the confidence codes are scored — is already
    a manifest field. A second copy of this would be a second place for those
    to drift.
    """
    b = builder(ctx, tool, contract_version="1")
    manifest = require_active_source(ctx, source_id)
    registry_dim, gaps = selection_coverage(ctx.sources, "facility.locations",
                                            [manifest])
    params = ctx.postgrest.params_for(manifest)
    described = await ctx.postgrest.describe(manifest)
    schema = described.value

    clauses: list[Filter] = []
    for value, column, operator in (
            (state.strip().upper(), params.state_column, "eq"),
            (county.strip(), params.county_column, "ilike"),
            (name.strip(), params.name_column, "ilike")):
        if not value:
            continue
        if column is None:
            raise InvalidQuery(
                f"{manifest.name} has no column for that filter. Use the "
                "`filters` argument against a column this table has: "
                f"{', '.join(sorted(schema.columns))}.")
        # County and project names are matched as substrings because a caller
        # types "Ellis" for a column holding "Ellis County", and case-
        # insensitively because the publisher's own documentation says the
        # case-sensitive form is the trap.
        clauses.append(Filter(column=column, operator=operator,
                              value=f"*{value}*" if operator == "ilike"
                              else value))
    clauses.extend(_bbox_filters(bbox, params, schema))
    clauses.extend(parse_filters(filters, schema))

    fetched = await ctx.postgrest.query(
        manifest, filters=clauses, rows=rows, offset=offset,
        order=parse_order(order, schema) if order.strip() else None,
        schema=schema)
    page = fetched.value

    ref = add_manifest_source(b, ctx, manifest,
                              retrieved_at=fetched.retrieved_at,
                              cache_age_seconds=fetched.cache_age_seconds,
                              from_cache=fetched.from_cache,
                              doi=params.dataset_doi)
    for row in page.rows:
        b.add_evidence(source_ref=ref,
                       record_id=f"{params.table}#{row.get(params.id_column)}",
                       retrieved_at=fetched.retrieved_at, transformations=[])

    returned = len(page.rows)
    data: dict[str, Any] = {
        "records": page.rows,
        "record_count": returned,
        "total_matches": page.total,
        "offset": offset,
        "columns": page.columns,
        "query": page.filters,
        "units": params.units_note,
        "confidence_codes": params.confidence_note,
        "dataset_version": manifest.coverage.dataset_version,
        "note": (
            f"An inventory of {what}: where they are and what they are, one "
            "row each, not how much they generated. For output, capacity "
            "factors, or anything hourly, use energy.get_data against EIA — "
            "the eia_id column on these rows is EIA's own plant id and is "
            "how the two line up. The API states no release version of its "
            "own, so dataset_version is this registry's record of the "
            f"current release, checked "
            f"{manifest.coverage.record_count_checked}."),
    }
    if page.total is not None and page.total > offset + returned:
        data["more"] = (
            f"{page.total} records match. Page with offset to read past the "
            f"{returned} returned here rather than describing these as all "
            "of them.")
    if not page.rows:
        data["note"] = (
            "No record in this database matches those filters. The database "
            "was reachable and the filters were valid columns, so this is an "
            "empty result rather than a coverage gap — but read the scope "
            "before concluding the facility does not exist: "
            + manifest.coverage.scope)

    if page.low_confidence_rows:
        b.warn(WarningCode.screening_only,
               f"{page.low_confidence_rows} of the {returned} records "
               "returned carry a confidence code below the publisher's top "
               "score, which means the compilers could not fully confirm the "
               "location or the attributes from imagery and records. Those "
               "rows are estimates and are marked as such in the data; "
               "reporting them beside the confirmed ones without the "
               "distinction turns an estimate into a survey. The codes are "
               "decoded in confidence_codes.", manifest.id)

    pagination = pagination_coverage(page.total, returned, offset=offset)
    return b.build(data, Coverage(
        registry=registry_dim, execution=ExecutionCoverage.complete,
        pagination=pagination,
        source_claim=SourceClaimCoverage.complete,
        result=result_dim(returned), sources_searched=[manifest.id],
        sources_unavailable=gaps))


def _bbox_filters(bbox: str, params, schema) -> list[Filter]:
    """A `minlon,minlat,maxlon,maxlat` box as four range clauses.

    Four clauses over two columns, which is why filters are grouped by column
    rather than collapsed into one value each. The box is checked here rather
    than sent as typed: a swapped pair reaches the service as a perfectly
    valid query that matches nothing, which reads as "no turbines there".
    """
    if not bbox.strip():
        return []
    if not (params.longitude_column and params.latitude_column):
        raise InvalidQuery("this source carries no coordinate columns, so it "
                           "cannot be filtered by bounding box.")
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise InvalidQuery(
            f"bbox takes four comma-separated decimal degrees, "
            f"minlon,minlat,maxlon,maxlat; got {len(parts)} value(s).")
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as err:
        raise InvalidQuery(f"bbox values must be numbers: {bbox!r}") from err
    if west >= east or south >= north:
        raise InvalidQuery(
            "bbox reads minlon,minlat,maxlon,maxlat and this one has a "
            "minimum at or above its maximum. Western longitudes are "
            "negative in this database, so -105.5 is west of -105.0.")
    lon = schema.require(params.longitude_column, "bbox longitude column")
    lat = schema.require(params.latitude_column, "bbox latitude column")
    return [Filter(column=lon, operator="gte", value=str(west)),
            Filter(column=lon, operator="lte", value=str(east)),
            Filter(column=lat, operator="gte", value=str(south)),
            Filter(column=lat, operator="lte", value=str(north))]


async def find_wind_turbines(ctx: RuntimeContext, state: str = "",
                             county: str = "", project: str = "",
                             filters: str = "", bbox: str = "",
                             order: str = "", rows: int = DEFAULT_ROWS,
                             offset: int = 0) -> Envelope:
    return await _find_facilities(
        ctx, source_id=WIND_TURBINE_SOURCE, tool="facility.find_wind_turbines",
        what="individual wind turbines", state=state, county=county,
        name=project, filters=filters, bbox=bbox, order=order, rows=rows,
        offset=offset)


async def find_solar_facilities(ctx: RuntimeContext, state: str = "",
                                county: str = "", facility: str = "",
                                filters: str = "", bbox: str = "",
                                order: str = "", rows: int = DEFAULT_ROWS,
                                offset: int = 0) -> Envelope:
    return await _find_facilities(
        ctx, source_id=SOLAR_FACILITY_SOURCE,
        tool="facility.find_solar_facilities",
        what="utility-scale solar facilities of one megawatt and up",
        state=state, county=county, name=facility, filters=filters,
        bbox=bbox, order=order, rows=rows, offset=offset)

ENERGY_TOOLS.register(ToolSpec(
    name="energy.discover_routes",
    description=(
        "Walk EIA's self-describing route tree to find the dataset that "
        "answers a question. Call with no route for the top level "
        "(electricity, natural-gas, petroleum, coal, nuclear-outages, "
        "international...), then with a child id to descend. A leaf route "
        "returns its frequencies, facets, data columns, and period range — "
        "everything energy.get_data needs. ALWAYS walk here first: EIA "
        "returns empty rows rather than an error for a wrong column name."),
    toolset="default", contract_version="1", fn=discover_routes))

ENERGY_TOOLS.register(ToolSpec(
    name="energy.get_data",
    description=(
        "Rows from a named EIA dataset route. Pass data_columns as a "
        "comma-separated list of exact column names from "
        "energy.discover_routes, and facets as 'name=v1,v2;other=v3'. EIA "
        "caps responses at 5,000 rows; when coverage.pagination says "
        "truncated, page with offset rather than describing what you got as "
        "the whole series. Needs an EIA API key — EIA's own, not an "
        "api.data.gov key."),
    toolset="default", contract_version="1", fn=get_data))

ENERGY_TOOLS.register(ToolSpec(
    name="energy.grid_status",
    description=(
        "Recent hourly electricity data for a US balancing authority from "
        "EIA-930 (CISO, ERCO, PJM, MISO, ISNE, NYIS, BPAT...). Use for 'how "
        "much power did X use'. `metric` picks one of four measurements that "
        "share this route: demand (the default, and what 'how much power did "
        "X use' means), forecast (day-ahead, and its newest hours have NOT "
        "happened yet), generation, or interchange. Recent hours are "
        "PRELIMINARY and get revised, so this is operational reporting "
        "rather than settled statistics — the envelope says so on every "
        "answer."),
    toolset="default", contract_version="1", fn=grid_status))

ENERGY_TOOLS.register(ToolSpec(
    name="grid.get_bpa_operations",
    description=(
        "Near-live load and generation inside the Bonneville Power "
        "Administration balancing authority, at five-minute resolution over "
        "a rolling seven-day window: load, VER (wind and solar), hydro, "
        "fossil/biomass, nuclear, and net interchange. `intervals` is how "
        "many of the most recent five-minute readings to return; 12 is the "
        "last hour. This is the Pacific Northwest at five-minute "
        "resolution — for any other balancing authority, or for hourly "
        "history, use energy.grid_status."),
    toolset="default", contract_version="1", fn=get_bpa_operations))

ENERGY_TOOLS.register(ToolSpec(
    name="fuel.find_vehicle",
    description=(
        "Official EPA fuel-economy ratings for US-market vehicles, 1984 "
        "onward. A strict drill-down: call with no arguments for years, then "
        "year for makes, then year+make for models, then year+make+model for "
        "trim options whose values are vehicle ids. Call with vehicle_id for "
        "the full record — ratings, tailpipe CO2, electric range, annual fuel "
        "cost. These are test-cycle values, not real-world consumption."),
    toolset="default", contract_version="1", fn=find_vehicle))

ENERGY_TOOLS.register(ToolSpec(
    name="fuel.get_prices",
    description=(
        "Current US national average fuel prices by type: regular, midgrade, "
        "premium, diesel, E85, CNG, LPG, and electricity. One current figure "
        "each, no history and no regional breakdown — for those use "
        "energy.get_data against EIA's petroleum routes."),
    toolset="default", contract_version="1", fn=get_fuel_prices))

ENERGY_TOOLS.register(ToolSpec(
    name="facility.find_wind_turbines",
    description=(
        "Where individual US wind turbines are and what they are, from the "
        "USGS/LBNL wind turbine database: state, county, project name, "
        "manufacturer, model, rated capacity, hub height, rotor diameter, "
        "year, and coordinates, land-based and offshore. Filter by `state` "
        "(two-letter), `county`, `project`, a `bbox` of "
        "minlon,minlat,maxlon,maxlat, or `filters` in the publisher's own "
        "form, 'p_year=gte.2020;t_cap=gt.3000', semicolon-separated. An "
        "inventory, NOT generation: for output use energy.get_data. Every "
        "row carries confidence codes, and the answer says how many are "
        "below full confidence."),
    toolset="default", contract_version="1", fn=find_wind_turbines))

ENERGY_TOOLS.register(ToolSpec(
    name="facility.find_solar_facilities",
    description=(
        "Where US utility-scale solar facilities of one megawatt and up are "
        "and what they are, from the USGS/LBNL photovoltaic database: state, "
        "county, facility name, AC and DC capacity, panel technology, axis "
        "type, tilt, site type, agrivoltaic status, year, power region, and "
        "coordinates. Same filter arguments as facility.find_wind_turbines, "
        "against this table's columns ('p_cap_ac=gt.100;p_axis=eq.single-"
        "axis'). Rooftop and community solar are not in it. An inventory, "
        "not generation."),
    toolset="default", contract_version="1", fn=find_solar_facilities))
