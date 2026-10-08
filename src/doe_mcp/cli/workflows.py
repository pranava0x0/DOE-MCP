"""Local demos and installed skill discovery."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..adapters.base import close_shared_client
from ..core.credentials import Credentials
from ..replay import fixture_manifest, load_replay_context
from ..runtime import load_context
from ..workflows import CASES, evidence_csv, run_workflow


def asset_dir(name: str) -> Path:
    package = Path(__file__).resolve().parents[1]
    installed = package / ("_" + name)
    if installed.exists():
        return installed
    root = package.parents[1]
    return root / ("tests/fixtures" if name == "fixtures" else name)


def cmd_skills(args) -> int:
    root = asset_dir("skills")
    skills = {p.parent.name: p for p in root.glob("*/SKILL.md")}
    if args.name:
        if args.name not in skills:
            raise SystemExit("unknown installed skill; run doe-mcp skills")
        print(skills[args.name].read_text() if args.show else skills[args.name])
    else:
        print(json.dumps({name: str(path) for name, path in sorted(skills.items())}, indent=2))
    return 0


async def build_report(args):
    if args.live:
        server = {"eia": "doe-energy-data", "grid": "doe-energy-data",
                  "site": "doe-energy-data", "earth": "doe-earth",
                  "materials": "doe-materials"}.get(args.case, "doe-research")
        creds = Credentials.load().scoped_to(["EIA_API_KEY"]) if args.case == "eia" else Credentials(values={}, path=Path("/nonexistent"), file_exists=False)
        ctx = load_context(credentials=creds, server_name=server)
        manifests = []
    else:
        directory = args.fixtures or asset_dir("fixtures")
        ctx = load_replay_context(directory, keyed=args.case == "eia")
        manifests = fixture_manifest(directory)
    try:
        return await run_workflow(ctx, args.case, mode="live" if args.live else "recorded", fixtures=manifests)
    finally:
        await close_shared_client()


def cmd_demo(args) -> int:
    if args.case == "client":
        from ..client_demo import round_trip
        print(json.dumps(asyncio.run(round_trip()), indent=2))
        return 0
    report = asyncio.run(build_report(args))
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / f"{args.case}.json").write_text(output)
        (args.output / f"{args.case}.csv").write_text(evidence_csv(report))
        print(args.output / f"{args.case}.json")
    else:
        print(output, end="")
    return 0


def register(sub):
    parser = sub.add_parser("demo", help="run a recorded workflow or a local SDK client test")
    parser.add_argument("case", choices=[*CASES, "client"], default="evidence", nargs="?")
    parser.add_argument("--live", action="store_true", help="query publishers instead of replaying")
    parser.add_argument("--fixtures", type=Path, help="recorded fixture directory")
    parser.add_argument("--output", type=Path, help="write JSON and CSV into this directory")
    parser.set_defaults(func=cmd_demo)
    skills = sub.add_parser("skills", help="list installed skill paths or print a skill")
    skills.add_argument("name", nargs="?")
    skills.add_argument("--show", action="store_true")
    skills.set_defaults(func=cmd_skills)
