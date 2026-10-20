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
    "dataset-card": ("Geothermal dataset-card starter", "What can public catalog metadata fill in, and what still needs a dataset owner?"),
    "microbiome": ("Microbiome study and sample finder", "Find a shale microbiome study and inspect a sample of its biosamples."),
    "rulemaking": ("DOE rulemaking brief", "What did DOE publish, which docket does it belong to, and what dates did the publisher supply?"),
    "outage-history": ("Historical outage-data finder", "Which EAGLE-I release covers 2021, and what else is needed to calculate an outage rate?"),
    "find-data": ("Does DOE publish this?", "Where would I find geothermal data from NREL, and what does an empty catalog mean?"),
    "facility-status": ("NERSC facility status", "Is Perlmutter up on the recorded board, and is maintenance scheduled?"),
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
    drafts: list[dict] = []

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
    elif case == "dataset-card":
        found = await call("research.search_datasets", site_code="DOE-GDR", rows=2)
        for record in found.data["datasets"]:
            refs = [{"source_ref": e.source_ref, "record_id": e.record_id,
                     "locator": e.locator} for e in found.evidence
                    if e.record_id == record["id"]]
            drafts.append({"status": "draft_metadata_only", "metadata": record,
                           "evidence": refs,
                           "needs_owner_review": [
                               "Downloadable files and access conditions",
                               "File formats, sizes and checksums",
                               "License and permitted reuse",
                               "Sensitivity and governance review",
                               "Data quality and suitability for AI use"]})
        if not drafts:
            raise SourceUnavailable("dataset search returned no metadata to draft")
        limits += ["A documentation starter, not a validated Genesis datacard or Croissant record.",
                   "Only catalog metadata was read; no dataset files were downloaded or inspected.",
                   "A public metadata record does not establish that the underlying data is freely accessible or licensed for reuse."]
    elif case == "microbiome":
        found = await call("bio.search_studies", text="shale")
        studies = found.data["studies"]
        if not studies:
            raise SourceUnavailable("no shale study returned to follow")
        # A parent study can list no samples of its own; an empty follow-up is
        # not a biosample walkthrough, so try the next returned study.
        for study in studies[:3]:
            samples = await call("bio.search_biosamples", study_id=study["study_id"], rows=5)
            if samples.data.get("biosamples"):
                break
        else:
            raise SourceUnavailable("no returned shale study listed biosamples to follow")
        limits += ["Study and sample metadata only; no sequences, assemblies or annotations were downloaded.",
                   "At most five samples are shown; they do not establish study completeness or biological function.",
                   "The publisher's access recipes distinguish dataset DOIs from award DOIs; a DOI alone does not establish free access."]
    elif case == "rulemaking":
        found = await call("docs.search_rulemakings", document_type="RULE", rows=5)
        documents = found.data["documents"]
        if not documents:
            raise SourceUnavailable("no DOE rulemaking returned to follow")
        detail = await call("docs.get_rulemaking", document_number=documents[0]["document_number"])
        if detail.coverage.result.value != "hit":
            raise SourceUnavailable("selected DOE rulemaking is no longer available")
        limits += ["A sampled Federal Register search, not a complete docket or a determination of current legal effect.",
                   "Publication, effective and comment-close dates are separate publisher fields; missing dates remain unknown.",
                   "Search totals include excluded FERC documents. Docket comments and supporting materials were not retrieved."]
    elif case == "outage-history":
        found = await call("grid.find_outage_history", year=2021)
        if not found.data["releases_for_year"]:
            raise SourceUnavailable("no outage release found for the requested year")
        limits += ["Release metadata only. No county outage files were downloaded and no outage counts or rates were calculated.",
                   "Customers without power are not people without power; coverage is incomplete.",
                   "The customer-count dataset has its own vintage. Pairing it with another year's outages can produce a misleading rate."]
    elif case == "find-data":
        org = await call("registry.resolve_org", query="NREL")
        if not org.data.get("resolved"):
            raise SourceUnavailable("the organization did not resolve")
        await call("registry.search_sources", text="geothermal")
        found = await call("discovery.search_all_catalogs", query="geothermal", per_catalog=2)
        if not found.data["results"]:
            raise SourceUnavailable("no catalog returned a geothermal record")
        limits += ["NREL is now the National Laboratory of the Rockies; nrel.gov has no DNS, so an old link or acronym can return nothing without an error.",
                   "Two of the four catalogs are harvested files with an old newest entry; zero matches there is not evidence that DOE holds nothing.",
                   "Registry sources marked proposed were named, not queried."]
    elif case == "facility-status":
        board = await call("compute.facility_status")
        if not board.data["systems"]:
            raise SourceUnavailable("the status board returned no systems")
        limits += ["One facility. ALCF and OLCF are registered and not read here; their absence says nothing about whether they are up.",
                   "Status is not queue position, allocation or permission to run a job.",
                   "The board's timestamps carry no time zone, and updated_at is the last change, not the last check. This is a recorded board, not current status."]
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
        **({"drafts": drafts} if drafts else {}),
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
