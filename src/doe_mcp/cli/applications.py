"""CLI for parameterized applications and their recorded presets."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from ..adapters.base import close_shared_client
from ..applications import dockets, energy_assumptions, forecast, forecast_panel, gcam, research_linkage, weather
from ..applications.samples import APPLICATIONS
from ..applications.bundle import Run, export_bundle
from ..core.credentials import Credentials
from ..core.errors import DoeMcpError, InvalidQuery
from ..replay import fixture_manifest, load_replay_context
from ..runtime import load_context
from .workflows import asset_dir


async def execute(args):
    if args.application == forecast_panel.NAME:
        if args.live or args.fixtures:
            raise InvalidQuery("forecast-panel imports CSVs or runs a simulation; it does not fetch live publishers or replay publisher fixtures")
        supplied = [args.plan, args.actuals, args.forecasts]
        if not any(supplied):
            return forecast_panel.simulation_sample()
        if not all(supplied):
            raise InvalidQuery("forecast-panel imports require --plan, --actuals and --forecasts together")
        return forecast_panel.build(args.plan.read_bytes(), args.actuals.read_bytes(), args.forecasts.read_bytes())
    if args.live:
        source = getattr(args, "source", "")
        server = {"forecast-benchmark": "doe-energy-data", "weather-package": "doe-earth",
                  "docket-tracker": "doe-research", gcam.NAME: "doe-mcp", "research-linkage": "doe-research", "energy-assumptions": "doe-energy-tech"}[args.application]
        creds = Credentials(values={}, path=Path("/nonexistent"), file_exists=False)
        if args.application == "docket-tracker" and source == "regulations":
            # The source gate precedes any read of the user's key store.
            ctx = load_context(credentials=creds)
            ctx.sources.require_active("regulations-gov-doe")
            creds = Credentials.load().scoped_to(["API_DATA_GOV_KEY"])
            server = "doe-docs"
        ctx = load_context(credentials=creds, server_name=server)
        fixtures = []
    else:
        directory = args.fixtures or asset_dir("fixtures")
        names = (("API_DATA_GOV_KEY",) if args.application == "docket-tracker"
                 and args.source == "regulations" else ())
        ctx, fixtures = load_replay_context(directory, credential_names=names), fixture_manifest(directory)
    run = Run(ctx, args.application, "live" if args.live else "recorded", fixtures)
    try:
        if args.application == "forecast-benchmark":
            files = await forecast.build(run, payload=args.forecasts.read_bytes() if args.forecasts else None,
                                         timezone_name=args.actual_timezone, intervals=args.intervals)
        elif args.application == "weather-package":
            files = await weather.build(run, latitude=args.latitude, longitude=args.longitude, start=args.start, end=args.end, variables=args.variables)
        elif args.application == "energy-assumptions":
            filters = json.loads(args.filters) if args.filters is not None else None
            files = await energy_assumptions.build(run, edition=args.edition, filters=filters, rows=args.rows, offset=args.offset)
        elif args.application == gcam.NAME:
            plan = gcam.validate_plan(json.loads(args.plan.read_bytes())) if args.plan else None
            files = await gcam.build(run, plan=plan)
        elif args.application == "research-linkage":
            files = await research_linkage.build(run, query=args.query, literature_query=args.literature_query,
                dataset_query=args.dataset_query, software_query=args.software_query, rows=args.rows, page_budget=args.page_budget,
                resolve_relationships=args.resolve_relationships, relationship_request_budget=args.relationship_request_budget)
        else:
            previous = json.loads(args.previous.read_bytes()) if args.previous else None
            if args.source == "regulations":
                files = await dockets.build_regulations(run, docket=args.docket or dockets.DEFAULT_DOCKET,
                    agency_id=args.agency_id, query=args.query, since=args.since, until=args.until,
                    rows=args.rows, page_budget=1 if args.page_budget is None else args.page_budget, previous=previous)
            else:
                files = await dockets.build(run, query=args.query, docket=args.docket, since=args.since, until=args.until,
                                            document_type=args.document_type, rows=args.rows, page_budget=2 if args.page_budget is None else args.page_budget, previous=previous)
        return run.report, files
    finally:
        await close_shared_client()


def cmd_app(args):
    try:
        report, files = asyncio.run(execute(args))
        if args.output:
            print(export_bundle(args.output, report, files))
        else:
            print(json.dumps(report, indent=2, allow_nan=False))
        if report["application"] == "forecast-benchmark":
            counts = report["scoring"]
            print(f"Forecast benchmark status: {report['status']} "
                  f"({counts['matched']} of {counts['submitted']} forecasts matched).", file=sys.stderr)
            if report["status"] == "unscored":
                print("Warning: no forecasts matched usable actuals; no forecast metrics were calculated. "
                      "Evidence and exclusion reasons are available in the report.", file=sys.stderr)
        elif report["application"] == forecast_panel.NAME:
            coverage = report["coverage"]
            print(f"Forecast panel mode: {report['mode']}; status: {report['status']}; "
                  f"included {coverage['included_region_lead_targets']} of {coverage['expected_region_lead_targets']} "
                  "declared region/lead/target slots. Inputs are not publisher verified.", file=sys.stderr)
            if report["status"] == "unscored":
                print("Warning: no common eligible targets; metrics are null and no ranking is produced.", file=sys.stderr)
    except (DoeMcpError, ValueError, OSError) as err:
        raise SystemExit(f"application failed: {err}") from None
    return 0


def register(sub):
    root = sub.add_parser("app", help="run a parameterized DOE data application")
    apps = root.add_subparsers(dest="application", required=True)
    for name in APPLICATIONS:
        parser = apps.add_parser(name)
        parser.add_argument("--live", action="store_true", help="query publishers; default replays recordings")
        parser.add_argument("--fixtures", type=Path)
        parser.add_argument("--output", type=Path, help="write a new complete bundle directory")
        parser.set_defaults(func=cmd_app)
        if name == "forecast-benchmark":
            parser.add_argument("--forecasts", type=Path, help="forecast CSV; default is a retrospective recorded sample")
            parser.add_argument("--actual-timezone", help="explicit IANA timezone for unlabelled BPA times")
            parser.add_argument("--intervals", type=int, default=24)
        elif name == "weather-package":
            parser.add_argument("--latitude", type=float, default=35.9313)
            parser.add_argument("--longitude", type=float, default=-84.3104)
            parser.add_argument("--start", default="2024-12-26")
            parser.add_argument("--end", default="2024-12-31")
            parser.add_argument("--variables", default="tmax")
        elif name == "energy-assumptions":
            parser.add_argument("--edition", type=int, default=2024, help="explicit supported edition; recorded preset is ATB 2024 v4.0.0")
            parser.add_argument("--filters", help="JSON object of exact publisher dimension text or null")
            parser.add_argument("--rows", type=int, default=10)
            parser.add_argument("--offset", type=int, default=0)
        elif name == gcam.NAME:
            parser.add_argument("--plan", type=Path, help="JSON selections and side-by-side pairs; default is the recorded GCAM preset")
        elif name == "research-linkage":
            parser.add_argument("--query", default="", help="search all three collections with this topic")
            parser.add_argument("--literature-query", default="", help="override literature topic")
            parser.add_argument("--dataset-query", default="", help="override dataset topic")
            parser.add_argument("--software-query", default="", help="override software topic")
            parser.add_argument("--resolve-relationships", action="store_true", help="resolve one hop of original typed DOI assertions")
            parser.add_argument("--relationship-request-budget", type=int, default=4, help="shared HTTP attempt limit for relationship follow-ups (1–24)")
            parser.add_argument("--rows", type=int, help="per-source page size; preset uses recorded sizes")
            parser.add_argument("--page-budget", type=int, default=1)

        elif name == forecast_panel.NAME:
            parser.add_argument("--plan", type=Path, help="JSON model/region/lead/window/measurement declaration")
            parser.add_argument("--actuals", type=Path, help="actual CSV with availability timestamps and measurement labels")
            parser.add_argument("--forecasts", type=Path, help="forecast CSV; provide all three inputs or run the labelled simulation")
        else:
            parser.add_argument("--source", choices=["federal-register", "regulations"],
                                default="federal-register",
                                help="default tracks Federal Register notices; Regulations.gov is gated")
            parser.add_argument("--agency-id", default="EERE")
            parser.add_argument("--query", default="")
            parser.add_argument("--docket", default="")
            parser.add_argument("--since", default="")
            parser.add_argument("--until", default="")
            parser.add_argument("--document-type", default="NOTICE")
            parser.add_argument("--rows", type=int, default=5)
            parser.add_argument("--page-budget", type=int)
            parser.add_argument("--previous", type=Path, help="prior report.json with the same search parameters and mode")
