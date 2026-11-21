"""CLI for parameterized applications and their recorded presets."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from ..adapters.base import close_shared_client
from ..applications import dockets, forecast, weather
from ..applications.bundle import Run, export_bundle
from ..core.credentials import Credentials
from ..core.errors import DoeMcpError
from ..replay import fixture_manifest, load_replay_context
from ..runtime import load_context
from .workflows import asset_dir


async def execute(args):
    if args.live:
        source = getattr(args, "source", "")
        server = {"forecast-benchmark": "doe-energy-data", "weather-package": "doe-earth",
                  "docket-tracker": "doe-research"}[args.application]
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
    except (DoeMcpError, ValueError, OSError) as err:
        raise SystemExit(f"application failed: {err}") from None
    return 0


def register(sub):
    root = sub.add_parser("app", help="run a parameterized DOE data application")
    apps = root.add_subparsers(dest="application", required=True)
    for name in ["forecast-benchmark", "weather-package", "docket-tracker"]:
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
