"""Build a reproducible local-driver example, never a model-quality benchmark."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doe_mcp.applications.bundle import json_bytes  # noqa: E402
from doe_mcp.evaluation import run  # noqa: E402
from doe_mcp.evaluation_matrix import compare  # noqa: E402
from doe_mcp.workflows import freeze  # noqa: E402


async def write_evaluation_example(root: Path) -> None:
    reports, payloads = [], {}
    for exposure in ("focused", "all"):
        for skills in (False, True):
            label = exposure + "-skills-" + ("on" if skills else "off")
            report = await run(argparse.Namespace(
                driver=root / "examples" / "evaluation-simulation-driver.py",
                model="deterministic-simulation", max_calls=3, max_tokens=1000,
                max_turns=3, timeout=10, repeats=3, exposure=exposure, skills=skills,
                task=["alias"], fixtures=root / "tests" / "fixtures", simulation=True))
            # These are site-only execution identifiers, not provider or capture times.
            revision = report["runs"][0]["registry_revision"]
            clock = revision + "T00:00:00+00:00"
            report = freeze(report, clock)
            report["started_at"] = clock
            report["report_id"] = "simulation-" + label
            for index, row in enumerate(report["runs"], 1):
                row["run_id"] = "simulation-" + label + "-" + str(index)
                if "elapsed_seconds" in row:
                    row["elapsed_seconds"] = 0
            reports.append(report)
            payloads[label + ".json"] = json_bytes(report)
    result, files = compare(reports)
    result["site_execution_note"] = (
        "Deterministic test driver only. Identifiers and execution clocks normalized for the site; "
        "token counts are simulated protocol values. No model, reviewer or provider was called.")
    payloads.update(files)
    payloads["report.json"] = json_bytes(result)
    payloads["manifest.json"] = json_bytes({"application": "evaluation-matrix", "mode": "simulation",
        "files": [{"file": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                  for name, body in sorted(payloads.items())]})
    destination = root / "docs" / "data" / "evaluation-matrix"
    destination.mkdir(parents=True, exist_ok=True)
    for name, body in payloads.items():
        (destination / name).write_bytes(body)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, body in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, body)
    (destination / "bundle.zip").write_bytes(archive.getvalue())
    lines = ["# Evaluation matrix demonstration", "",
        "**Simulation only. No language model, reviewer or provider was called.** "
        "This example exercises the driver protocol against recorded tools. "
        "Token counts are simulated protocol values. They do not measure a model or its cost.", "",
        "[Download the complete comparison bundle](data/evaluation-matrix/bundle.zip) · "
        "[Read the JSON report](data/evaluation-matrix/report.json) · "
        "[Compare your own reports](evaluation-matrix.html)", "",
        "The same task and fixture hashes appear in every configuration. "
        "Each configuration has a budget that permits one final answer, "
        "one unfinished attempt and one unrun repetition. They all stay in the denominator.", "",
        "| Tool exposure | Skills | Scheduled | Attempted | Final answers | Reference checks passed | Reviewed quality | Estimated cost |",
        "|---|---|---:|---:|---:|---:|---|---|"]
    for cell in sorted(result["cells"], key=lambda item: (item["exposure"], item["skills_enabled"])):
        lines.append(f"| {cell['exposure']} | {'On' if cell['skills_enabled'] else 'Off'} | "
                     f"{cell['scheduled']} | {cell['attempted']} | {cell['completed']} | "
                     f"{cell['automatic_pass']} | Unknown: no reviews | Unknown: no prices |")
    lines += ["", "A passed reference check means citation identifiers resolve to returned evidence. "
        "It does not establish a correct claim, safe refusal, scientific accuracy or model quality. "
        "There are no reviewed quality scores or supplied model prices in this example.", "",
        "## Inspect or reproduce", "",
        "The bundle contains source driver reports, `matrix.csv`, `runs.csv`, "
        "a blank grade template, the comparison JSON and checksums. "
        "Unzip it, then compare the supplied reports without running a driver:", "",
        "```bash\ndoe-mcp evaluate-matrix \\\n  --report focused-skills-off.json --report focused-skills-on.json \\\n  --report all-skills-off.json --report all-skills-on.json \\\n  --output ./offline-comparison\n```", "",
        "[Matrix CSV](data/evaluation-matrix/matrix.csv) · "
        "[Run-level CSV](data/evaluation-matrix/runs.csv) · "
        "[Grade template](data/evaluation-matrix/grades-template.json)", "",
        "Site execution clocks and identifiers are normalized for repeatable builds. "
        "They are not fixture capture times. The source fixture hashes remain in the bundle.", ""]
    (root / "docs" / "evaluation-example.md").write_text("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(write_evaluation_example(ROOT))
