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
