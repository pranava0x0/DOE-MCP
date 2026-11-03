import csv
import hashlib
import io
import json

import pytest

from doe_mcp import daymet_package
from doe_mcp.core.errors import SourceSchemaChanged
from doe_mcp.workflows import run_workflow, write_report


async def test_temperature_download_covers_only_returned_dates(ctx, tmp_path):
    report = await run_workflow(ctx, daymet_package.CASE)
    write_report(tmp_path, report)
    metadata = json.loads((tmp_path / daymet_package.METADATA_NAME).read_bytes())
    dist, = metadata["distribution"]
    payload = (tmp_path / dist["contentUrl"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == dist["sha256"]
    assert len(payload) == int(dist["contentSize"])
    data = report["steps"][0]["envelope"]["data"]
    assert metadata["citation"] == data["publisher_citation"]
    reader = csv.DictReader(io.StringIO(payload.decode()))
    values = {r["date"]: float(r["tmax_deg_c"]) for r in reader}
    assert values == {r["date"]: r["values"]["tmax"] for r in data["days"]}
    assert len(values) == 5 and "2024-12-31" not in values
    data["columns"][0]["units"] = "deg f"
    with pytest.raises(SourceSchemaChanged):
        daymet_package.package(data)


async def test_recorded_package_rejects_live_mode(ctx):
    with pytest.raises(ValueError, match="recorded mode"):
        await run_workflow(ctx, daymet_package.CASE, mode="live")


@pytest.mark.parametrize("defect", ["gap", "missing_temperature", "units"])
async def test_failed_export_preserves_existing_files(ctx, tmp_path, defect):
    report = await run_workflow(ctx, daymet_package.CASE)
    write_report(tmp_path, report)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    data = report["steps"][0]["envelope"]["data"]
    if defect == "gap":
        data["days"].pop(2)
    elif defect == "missing_temperature":
        data["days"][0]["values"]["tmax"] = None
    else:
        data["columns"][0]["units"] = "K"
    with pytest.raises(SourceSchemaChanged):
        write_report(tmp_path, report)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


async def test_evidence_uses_echoed_precision(ctx):
    from doe_mcp.domains.earth import get_daymet_point
    env = await get_daymet_point(ctx, 35.931301, -84.310399, "2024-12-26", "2024-12-31", "tmax")
    assert env.evidence[0].record_id == f"tile:{env.data['tile']}@35.9313,-84.3104"
