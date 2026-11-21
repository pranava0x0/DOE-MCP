"""The published matrix is a reproducible protocol demo, never model scoring."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
import zipfile

import pytest

from tools.build_evaluation_example import write_evaluation_example
from doe_mcp.evaluation_matrix import compare

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def example(tmp_path_factory):
    directory = tmp_path_factory.mktemp("evaluation-site")
    (directory / "examples").mkdir()
    (directory / "tests").mkdir()
    (directory / "tests" / "fixtures").symlink_to(ROOT / "tests" / "fixtures", target_is_directory=True)
    shutil.copy2(ROOT / "examples" / "evaluation-simulation-driver.py", directory / "examples")
    (directory / "docs").mkdir()
    asyncio.run(write_evaluation_example(directory))
    return directory


def test_site_example_has_no_numeric_quality_or_cost_claim(example):
    destination = example / "docs" / "data" / "evaluation-matrix"
    report = json.loads((destination / "report.json").read_text())
    assert report["simulation"] is True
    assert len(report["cohorts"]) == 1 and len(report["cells"]) == 4
    for cell in report["cells"]:
        assert cell["scheduled"] == 3 and cell["attempted"] == 2
        assert cell["completed"] == cell["automatic_checked"] == cell["automatic_pass"] == 1
        assert cell["reviewed"] == 0
        assert cell["reviewed_quality_mean"] is None
        assert cell["overall_quality_mean"] is None
        assert cell["estimated_cost_usd"] is None
    markdown = (example / "docs" / "evaluation-example.md").read_text()
    assert "No language model, reviewer or provider was called" in markdown
    assert "simulated protocol values" in markdown
    assert "Unknown: no reviews" in markdown and "Unknown: no prices" in markdown


def test_download_replays_offline_comparison_and_checksums(example):
    destination = example / "docs" / "data" / "evaluation-matrix"
    reports = [json.loads(path.read_text()) for path in sorted(destination.glob("*-skills-*.json"))]
    result, _ = compare(reports)
    assert len(result["cells"]) == 4
    manifest = json.loads((destination / "manifest.json").read_text())
    with zipfile.ZipFile(destination / "bundle.zip") as archive:
        for row in manifest["files"]:
            body = archive.read(row["file"])
            assert body == (destination / row["file"]).read_bytes()
            assert len(body) == row["bytes"]
            assert hashlib.sha256(body).hexdigest() == row["sha256"]


async def test_site_example_is_repeatable_and_never_loads_credentials_or_http(example, monkeypatch):
    from doe_mcp.core.credentials import Credentials
    from doe_mcp.adapters.base import HttpFetcher

    def refuse(*args, **kwargs):
        raise AssertionError("the protocol demo must not access keys or HTTP")

    monkeypatch.setattr(Credentials, "load", refuse)
    monkeypatch.setattr(HttpFetcher, "fetch_json", refuse)
    monkeypatch.setattr(HttpFetcher, "fetch_text", refuse)
    destination = example / "docs" / "data" / "evaluation-matrix"
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    await write_evaluation_example(example)
    assert before == {path.name: path.read_bytes() for path in destination.iterdir()}
