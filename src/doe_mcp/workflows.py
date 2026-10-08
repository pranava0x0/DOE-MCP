"""Bounded demo walks over existing tools; no new publisher interface."""
from __future__ import annotations

import copy
import csv
import io
from datetime import datetime, timezone
from typing import Any

from .core.envelope import Envelope
from .core.errors import SourceUnavailable
from .core.registry import DeclaredState
from .runtime import RuntimeContext
from .servers.build import registries

CASES = {
    "evidence": ("DOE evidence finder", "Find perovskite solar research and inspect one returned record."),
    "partial": ("One collection unavailable", "What can the remaining literature collection answer?"),
    "blocked": ("A capability not yet served", "Can DOE-MCP return the solar resource at a candidate site?"),
    "grid": ("BPA grid briefing", "What do the recorded five-minute load readings show?"),
    "eia": ("CAISO demand briefing", "What are the latest three measured hourly demand values?"),
    "site": ("Rhode Island facility context", "Which sampled wind and solar facilities appear in this inventory?"),
    "earth": ("Environmental study brief", "Find permafrost datasets and inspect modelled weather at Oak Ridge."),
    "materials": ("Materials evidence sheet", "Inspect a computed GaN structure and a separate basis-set example."),
}


def tool_spec(name: str):
    for registry in registries().values():
        for spec in registry.tools():
            if spec.name == name:
                return spec
    raise ValueError(f"unknown tool: {name}")


def freeze(node: Any, clock: str) -> Any:
    """Normalize replay execution metadata; the report labels this clock synthetic."""
    if isinstance(node, dict):
        return {key: (clock if key == "retrieved_at" else
                      "recorded-workflow" if key == "request_id" else
                      0 if key == "cache_age_seconds" else freeze(value, clock))
                for key, value in node.items()}
    if isinstance(node, list):
        return [freeze(value, clock) for value in node]
    return node


async def run_workflow(ctx: RuntimeContext, case: str, *, mode: str = "recorded",
                       fixtures: list[dict] | None = None) -> dict:
    if case not in CASES or mode not in {"recorded", "live"}:
        raise ValueError("unknown workflow or mode")
    if case == "partial" and mode == "live":
        raise ValueError("the simulated outage is available only in recorded mode")
    if case == "partial":
        ctx = copy.copy(ctx)
        ctx.sources = copy.deepcopy(ctx.sources)
        manifest = ctx.sources.get("osti-doe-pages")
        manifest.lifecycle.declared_state = DeclaredState.proposed
        manifest.lifecycle.blocked_reason = "Simulated outage for this recorded demonstration"
    steps: list[dict] = []
    limits: list[str] = []

    async def call(tool_name: str, **args) -> Envelope:
        env = await tool_spec(tool_name).fn(ctx, **args)
        # Missing recordings and publisher failures must not silently become a demo.
        if env.coverage.source_failures or env.coverage.execution.value == "failed":
            raise SourceUnavailable(f"workflow step {tool_name} did not complete; inspect publisher access or recording")
        steps.append({"tool": tool_name, "args": args,
                      "envelope": env.model_dump(mode="json", by_alias=True)})
        return env

    if case in {"evidence", "partial"}:
        found = await call("research.search_literature", query="perovskite solar", rows=3)
        sources = {s.id: s.source_id for s in found.provenance}
        evidence = next((e for e in found.evidence
                         if sources[e.source_ref] == "osti-gov-records"), None)
        if evidence is None:
            raise SourceUnavailable("search returned no OSTI record to follow")
        detail = await call("research.get_record", record_id=evidence.record_id, collection="literature")
        if detail.coverage.result.value != "hit":
            raise SourceUnavailable("selected record is no longer available")
        limits += ["A sampled literature search is not a systematic review.",
                   "A common topic does not prove that a paper and dataset are linked."]
    elif case == "blocked":
        await call("registry.search_sources", capability="solar.get_resource")
        limits += ["Registry inventory only. Proposed sources were not queried.",
                   "Missing tool coverage does not mean the data does not exist."]
    elif case == "grid":
        await call("grid.get_bpa_operations", intervals=12)
        limits += ["BPA balancing-authority load is not local connection capacity.",
                   "Publisher timestamps are Pacific Time; no UTC offset is inferred."]
    elif case == "eia":
        await call("energy.grid_status", balancing_authority="CISO", hours=3, metric="demand")
        limits += ["Measured hourly energy demand in MWh (D), not forecast (DF).", "This regional series does not measure spare connection capacity."]
    elif case == "site":
        await call("facility.find_wind_turbines", state="RI", rows=5)
        await call("facility.find_solar_facilities", state="RI", rows=5)
        await call("registry.search_sources", capability="grid.interconnection_queue")
        await call("registry.list_neighbors", capability="")
        limits += ["First page only; map bounds describe returned points, not a complete site search.",
                   "Parcel ownership, queue position, available MW, tariffs and environmental clearance remain unknown.",
                   "NEPA-MCP is a separate installation and manual handoff. No external server was called."]
    elif case == "earth":
        await call("earth.search_datasets", text="permafrost", rows=5)
        await call("earth.get_daymet_point", latitude=35.9313, longitude=-84.3104,
                   start="2023-06-01", end="2023-06-05", variables="prcp,tmax,tmin")
        limits += ["The dataset search and Oak Ridge weather example are independent; no geographic or temporal relationship is asserted.",
                   "Daymet values are modelled grid-cell estimates, not station observations."]
    elif case == "materials":
        found = await call("materials.search_structures", elements="Ga,N", rows=5)
        rows = found.data["structures"]
        if not rows:
            raise SourceUnavailable("no structure to follow")
        await call("materials.get_structure", structure_id=rows[0]["id"])
        await call("chemistry.get_basis_set", name="6-31g", output_format="nwchem", elements="H,C,O")
        limits += ["Computed structures are not experimental validation.",
                   "The H/C/O basis set is a separate chemistry example, not a recommendation for gallium nitride."]
    clock = f"{ctx.sources.revision}T00:00:00Z"
    return {
        "format_version": "1", "case": case, "title": CASES[case][0], "question": CASES[case][1],
        "mode": mode, "simulation": case == "partial", "registry_revision": ctx.sources.revision,
        "generated_at": None if mode == "recorded" else datetime.now(timezone.utc).isoformat(),
        "replay_clock": clock if mode == "recorded" else None,
        "timestamp_note": ("Envelope retrieval timestamps use a fixed replay clock, not publisher retrieval times. "
                           "Actual fixture capture times appear in the manifest; null means unrecorded."
                           if mode == "recorded" else "Retrieval timestamps come from the fetch; cached results retain their original time."),
        "fixtures": [f for f in (fixtures or []) if f["file"].removesuffix(".json") in
                     {source["source_id"] for step in steps for source in step["envelope"]["provenance"]}],
        "limitations": limits,
        "steps": freeze(steps, clock) if mode == "recorded" else steps,
    }


def evidence_csv(report: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["step", "tool", "source", "record_id", "locator", "effective_at", "mode"])
    def cell(value):
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
    for i, step in enumerate(report["steps"], 1):
        env = step["envelope"]
        sources = {s["id"]: s["source_id"] for s in env["provenance"]}
        for evidence in env["evidence"]:
            writer.writerow([cell(v) for v in [i, step["tool"], sources[evidence["source_ref"]],
                             evidence["record_id"], evidence.get("locator"),
                             evidence.get("effective_at"), report["mode"]]])
    return output.getvalue()
