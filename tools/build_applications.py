"""Recorded application examples, regenerated offline beside the local guide."""
from __future__ import annotations

import hashlib
import html
import io
from pathlib import Path
import zipfile

from doe_mcp.applications.bundle import json_bytes
from doe_mcp.applications.samples import APPLICATIONS, recorded_sample
from doe_mcp.replay import fixture_manifest, load_replay_context
from doe_mcp.workflows import freeze

TITLES = {"forecast-benchmark": "Grid forecast benchmark lab",
          "weather-package": "Weather package builder", "docket-tracker": "DOE docket notice tracker",
          "gcam-comparison": "GCAM comparison receipts"}


def cell(value) -> str:
    if isinstance(value, float):
        value = f"{value:.3f}"
    text = html.escape(str(value if value is not None else "Unknown")).replace("\n", " ")
    for char in ("\\", "[", "]", "`", "*", "_"):
        text = text.replace(char, "\\" + char)
    return text.replace("|", "&#124;")


async def write_applications(root: Path) -> None:
    docs, fixtures = root / "docs", root / "tests" / "fixtures"
    lines = ["# Applications built on DOE-MCP", "",
        "These are local command-line applications built on existing read-only tools. "
        "The examples below are recorded demonstrations, not live services. "
        "Download a complete sample bundle or run a command to create your own.", "",
        "A server hosts tools. A tool returns data and provenance. A skill describes "
        "a tested workflow. An application combines tools and writes a result bundle. "
        "[Installation and application options](apps.html) · [Servers](index.html#servers-list) "
        "· [Tool reference](reference.html) · [Scientific skills](guide.html#connect-a-client-and-provide-a-skill)", "",
        "Site examples normalize replay retrieval clocks and request IDs for repeatable builds. "
        "The normalized clock is not a capture date. Source data and fixture hashes are preserved. "
        "CLI runs preserve their original tool envelopes.", ""]
    for name in APPLICATIONS:
        ctx = load_replay_context(fixtures, root / "sources")
        report, files = await recorded_sample(ctx, name, fixture_manifest(fixtures))
        clock = f"{ctx.sources.revision}T00:00:00Z"
        report = freeze(report, clock)
        report["retrieval_note"] = "Site-only recorded example: retrieval clocks and request IDs are normalized; source data and fixture capture metadata remain separate. CLI exports retain original invocation envelopes."
        report["site_reference_clock"] = clock
        payloads = {**files, "report.json": json_bytes(report)}
        payloads["manifest.json"] = json_bytes({"application": name, "mode": "recorded",
            "files": [{"file": n, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                      for n, b in sorted(payloads.items())]})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for filename, body in sorted(payloads.items()):
                info = zipfile.ZipInfo(filename, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                output.writestr(info, body)
        destination = docs / "data" / "apps" / name
        destination.mkdir(parents=True, exist_ok=True)
        for filename, body in payloads.items():
            (destination / filename).write_bytes(body)
        (destination / "bundle.zip").write_bytes(archive.getvalue())
        lines += [f"## {TITLES[name]}", "", "**Recorded example. Runs locally.**", "",
                  f"```bash\ndoe-mcp app {name} --output ./{name}-run\n```", "",
                  f"[Download complete bundle](data/apps/{name}/bundle.zip) · "
                  f"[View report and source evidence](data/apps/{name}/report.json) · "
                  f"[File checksums](data/apps/{name}/manifest.json)", ""]
        if name == "forecast-benchmark":
            lines += ["Compare uploaded forecasts with BPA timestamped load samples. "
                "The preset creates retrospective forecasts for illustration; it does not measure prospective skill.", "",
                f"Run status: **{report['status']}**. Matched {report['scoring']['matched']} of {report['scoring']['submitted']} forecasts.", "",
                "| Model | Lead seconds | Matched | MAE MW | RMSE MW | Bias MW |",
                "|---|---:|---:|---:|---:|---:|"]
            for row in report["metrics"]:
                lines.append("| " + " | ".join(cell(row[k]) for k in ["model_id", "lead_seconds", "matched", "mae_mw", "rmse_mw", "bias_mw"]) + " |")
            lines += ["", "Table metrics are rounded to three decimals; the report retains full precision.", "", f"[Forecast input CSV](data/apps/{name}/forecasts.csv) · [Scored rows](data/apps/{name}/scores.csv)", "",
                      "For your forecasts, pass `--forecasts path/to/forecasts.csv` and an explicit "
                      "`--actual-timezone` when the publisher timestamps lack offsets. "
                      "An unscored run retains exclusions and does not claim calculated metrics.", ""]
        elif name == "weather-package":
            lines += ["Build a point-weather CSV and Croissant metadata with a verified CSV checksum. "
                "Coordinates, date range and variables are CLI parameters.", "",
                f"This preset contains **{report['row_count']} days**. "
                "Daymet keeps February 29 and omits December 31 in leap years; it reports modelled grid-cell weather.", "",
                f"[Weather CSV](data/apps/{name}/weather.csv) · [Croissant metadata](data/apps/{name}/weather-croissant.json)", ""]
        elif name == "gcam-comparison":
            lines += [
                f"[Selection plan JSON](data/apps/{name}/plan.json) · [Receipt CSV](data/apps/{name}/receipts.csv) · "
                f"[Receipts with source notes](data/apps/{name}/receipts.json) · [Pair assessments](data/apps/{name}/comparisons.json)", "",
                "Select exact rows from released GCAM input tables and place their values side by side. "
                "Each value retains a file, row, column, source note and evidence reference. "
                "No arithmetic or unit conversion is performed.", "",
                "The preset follows the publisher's country mappings for Japan, Indonesia, Kenya and Canada. "
                "Indonesia maps to the legacy **Southeast Asia** region; that value is not an Indonesia observation. "
                "The soil table uses numeric region IDs and prints `Units: NA`; its source description explains recovery in years. "
                "The 2100 desalination energy and cost fields are different inputs with different units.", "",
                "| Selection | Column | Original value | Region convention and value | Publisher unit statement |",
                "|---|---|---|---|---|"]
            for receipt in report["receipts"]:
                mapping = receipt["region_mapping"]
                region = f"{mapping['region_key']}: {mapping['join_value']}" if mapping else "No country mapping requested"
                for column, value in receipt["selected_values"].items():
                    lines.append("| " + " | ".join(cell(v) for v in [receipt["id"], column, value, region,
                        "; ".join(receipt["units"]["publisher_statements"]) or "Unspecified"]) + " |")
            lines += ["", "| Left selection | Right selection | Receipt relationship | Arithmetic |",
                      "|---|---|---|---|"]
            for pair in report["comparisons"]:
                lines.append("| " + " | ".join(cell(pair[k]) for k in ["left", "right", "relationship", "arithmetic"]) + " |")
            lines += ["", "A relationship describes table and field identity, not scientific interchangeability. "
                "The report retains each full row and its original envelope. Blank publisher values stay null.", "",
                "Download the plan, edit its exact filters and selected columns, then run:", "",
                "```bash\ndoe-mcp app gcam-comparison --plan ./plan.json --output ./gcam-custom\n```", ""]
        else:
            lines += ["Track Federal Register DOE notices, retain separate publication/effective/comment dates, "
                "group publisher-supplied docket IDs, and compare saved snapshots. "
                "Regulations.gov document metadata is a **gated integration**, awaiting a real recording after "
                "the public exploration key returned HTTP 429. No comment bodies or attachments are collected.", "",
                f"This bounded snapshot contains **{len(report['documents'])} notices**. "
                f"Complete pagination: **{str(report['pagination']['complete']).lower()}**. "
                "Absence in a snapshot never means deletion or withdrawal.", "",
                "| Notice | Title | Publication | Comment close |",
                "|---|---|---|---|"]
            for row in report["documents"]:
                lines.append("| " + " | ".join(cell(row.get(k)) for k in ["document_number", "title", "publication_date", "comments_close_on"]) + " |")
            lines += ["", f"[Notice CSV](data/apps/{name}/notices.csv)", ""]
        lines += ["### Scope and limits", ""] + ["- " + cell(note) for note in report["limitations"]] + [""]
    (docs / "applications.md").write_text("\n".join(lines))
