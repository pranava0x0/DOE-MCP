"""Bounded demo walks over existing tools; no new publisher interface."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.envelope import Envelope
from .core.errors import SourceUnavailable
from .core.registry import DeclaredState
from . import daymet_package
from .demo_worksheets import bibliography, csv_text, soil_metadata
from .gcam_examples import EXAMPLES, run_example
from .load_baseline import (CASE as LOAD_BASELINE, INTERVAL_CASE, INTERVAL_READINGS,
                            check_intervals, score_load)
from .runtime import RuntimeContext
from .servers.build import registries

CASES = {
    **{k: (v.title, v.question) for k, v in EXAMPLES.items()},
    "evidence": ("DOE evidence finder", "Find perovskite solar research and inspect one returned record."),
    "bibliography": ("Perovskite bibliography worksheet", "Which citation fields did DOE PAGES supply for this page of results?"),
    daymet_package.CASE: ("The missing Daymet date", "Why does a six-day request return five daily maximum temperatures?"),
    INTERVAL_CASE: ("Check a load prediction band", "How often does a band fitted on earlier BPA load errors cover later readings?"),
    LOAD_BASELINE: ("Five-minute BPA load prediction", "How far off is the last load reading as a prediction for the next five minutes?"),
    "soil-metadata": ("Soil sample field check", "Which returned NMDC soil samples have a collection date, coordinates and depth in metres?"),
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
    "global-climate-archive": ("Find Canadian climate simulations", "What historical near-surface air temperature output does ORNL index for CanESM5?"),
    "materials": ("Materials evidence sheet", "Inspect a computed GaN structure and a separate basis-set example."),
}

RECORDED_ONLY = {
    "partial": "the simulated outage is available only in recorded mode",
    daymet_package.CASE: "the published Daymet data package is available only in recorded mode",
}

CASE_SKILLS = {
    **{k: v.skill for k, v in EXAMPLES.items()},
    "global-climate-archive": "environmental-study-brief",
    INTERVAL_CASE: "genesis-check-prediction-bands",
    LOAD_BASELINE: "genesis-score-load-errors",
    daymet_package.CASE: "genesis-package-weather-data",
    "bibliography": "genesis-build-bibliography", "soil-metadata": "genesis-audit-sample-fields",
    "evidence": "doe-literature-review", "partial": "doe-literature-review",
    "dataset-card": "genesis-draft-dataset-card", "find-data": "find-doe-data",
    "microbiome": "microbiome-data-finder", "rulemaking": "doe-rulemaking-brief",
    "outage-history": "grid-status-brief", "grid": "grid-status-brief",
    "eia": "grid-status-brief", "site": "energy-project-site-screen",
    "blocked": "energy-project-site-screen", "earth": "environmental-study-brief",
    "materials": "materials-structure-and-code", "facility-status": None,
}
"""The skill each walkthrough exercises; None for a tool-only demo. The
landing page and the demos page both read it, and a test holds it to
CASES and to the skills directory."""


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
    if mode == "live" and case in RECORDED_ONLY:
        raise ValueError(RECORDED_ONLY[case])
    if case == "partial":
        ctx = copy.copy(ctx)
        ctx.sources = copy.deepcopy(ctx.sources)
        manifest = ctx.sources.get("osti-doe-pages")
        manifest.lifecycle.declared_state = DeclaredState.proposed
        manifest.lifecycle.blocked_reason = "Simulated outage for this recorded demonstration"
    steps: list[dict] = []
    limits: list[str] = []
    drafts: list[dict] = []
    worksheet = None

    async def call(tool_name: str, **args) -> Envelope:
        env = await tool_spec(tool_name).fn(ctx, **args)
        # Missing recordings and publisher failures must not silently become a demo.
        if env.coverage.source_failures or env.coverage.execution.value == "failed":
            raise SourceUnavailable(f"workflow step {tool_name} did not complete; inspect publisher access or recording")
        steps.append({"tool": tool_name, "args": args,
                      "envelope": env.model_dump(mode="json", by_alias=True)})
        return env

    answer = None
    if case in EXAMPLES:
        example = EXAMPLES[case]
        answer = await run_example(example, call)
        limits.append(example.limit)
    elif case in {"evidence", "partial"}:
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
    elif case == "bibliography":
        found = await call("research.search_literature", query="perovskite solar", rows=3,
                           public_access_only=True)
        worksheet = bibliography(found)
        limits += ["Only the first DOE PAGES results page was read. No full text or citation counts were retrieved.",
                   "Author strings come from the catalog. Check omitted authors against the publisher before citing."]
    elif case == "soil-metadata":
        samples = await call("bio.search_biosamples", ecosystem_type="Soil", place="washington", rows=5)
        worksheet = soil_metadata(samples)
        limits += ["The place filter matches text containing Washington; it does not test state boundaries.",
                   "Counts describe this page only. Depth counts as present if the response gives a value or a range bound in metres."]
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
    elif case == INTERVAL_CASE:
        worksheet = check_intervals(await call("grid.get_bpa_operations", intervals=INTERVAL_READINGS))
        s = worksheet["scores"]
        answer = (f"The 90th percentile of the first {s['calibration_pairs']} absolute errors was "
                  f"{s['radius_mw']:g} MW. A band that far either side of the prior reading "
                  f"covered {s['hits']} of the next {s['holdout_pairs']} readings.")
        limits += ["The band uses the nearest-rank percentile of earlier errors. Later readings do not set its width.",
                   "Coverage describes this short window. Serially related readings do not establish a future coverage guarantee."]
    elif case == LOAD_BASELINE:
        found = await call("grid.get_bpa_operations", intervals=12)
        worksheet = score_load(found)
        limits += ["The prediction repeats the prior reading. BPA supplied the load measurements, not these predictions.",
                   "The scores describe this window alone. No model was trained and no prediction interval was estimated.",
                   "Pairs must be five minutes apart on the publisher’s Pacific clock. Blank load values do not count as zero."]
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
    elif case == daymet_package.CASE:
        weather = await call("earth.get_daymet_point", latitude=35.9313, longitude=-84.3104,
                             start="2024-12-26", end="2024-12-31", variables="tmax")
        if not weather.data["days"]:
            raise SourceUnavailable("no daily temperatures returned for the Daymet package")
        daymet_package.validate_extract(weather.data)
        limits += ["Daymet models the 1 km grid cell around the requested point. These are not station readings.",
                   "The CSV contains only the returned dates. A missing date has no temperature value."]
    elif case == "global-climate-archive":
        found = await call("climate.search_cmip", model="CanESM5", experiment="historical",
                           variable="tas", rows=5)
        if not found.data["datasets"]:
            raise SourceUnavailable("No matching CanESM5 metadata to inspect.")
        first = found.data["datasets"][0]
        answer = (f"The first returned CanESM5 dataset lists variant {first['variant_label']} "
                  f"and file version {first['version']}.")
        limits.append("ORNL indexes model output from multiple institutions. The rows describe files; they contain no temperature readings.")
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
        **({"answer": answer} if answer is not None else {}),
        "limitations": limits,
        "steps": freeze(steps, clock) if mode == "recorded" else steps,
        **({"worksheet": worksheet} if worksheet else {}),
        **({"drafts": drafts} if drafts else {}),
    }


def evidence_csv(report: dict) -> str:
    rows = []
    for i, step in enumerate(report["steps"], 1):
        env = step["envelope"]
        sources = {s["id"]: s["source_id"] for s in env["provenance"]}
        for evidence in env["evidence"]:
            rows.append([i, step["tool"], sources[evidence["source_ref"]],
                             evidence["record_id"], evidence.get("locator"),
                             evidence.get("effective_at"), report["mode"]])
    return csv_text(["step", "tool", "source", "record_id", "locator", "effective_at", "mode"], rows)


def write_report(directory: Path, report: dict) -> None:
    case = report["case"]
    files = {f"{case}.json": (json.dumps(report, indent=2) + "\n").encode(),
             f"{case}.csv": evidence_csv(report).encode()}
    if case == daymet_package.CASE:
        payload, metadata = daymet_package.package(report["steps"][0]["envelope"]["data"])
        files[daymet_package.CSV_NAME] = payload
        files[daymet_package.METADATA_NAME] = (json.dumps(metadata, indent=2) + "\n").encode()
    if sheet := report.get("worksheet"):
        headers = ["Run mode", "Registry revision", *sheet["columns"]]
        rows = ([report["mode"], report["registry_revision"], *row] for row in sheet["rows"])
        files[f"{case}-worksheet.csv"] = csv_text(headers, rows).encode()
    for name, content in files.items():
        (directory / name).write_bytes(content)
