"""Record both verified public ATB releases; publish only after both validate."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doe_mcp.adapters.base import HttpFetcher, close_shared_client  # noqa: E402
from doe_mcp.adapters.replay import RecordingFetcher  # noqa: E402
from doe_mcp.core.egress import EgressPolicy  # noqa: E402
from doe_mcp.atb_releases import RELEASES  # noqa: E402


async def record():
    import pyarrow as pa
    import pyarrow.parquet as pq
    recorder = RecordingFetcher(HttpFetcher(EgressPolicy(frozenset({"oedi-data-lake.s3.amazonaws.com"}))))
    summaries = []
    try:
        for spec in RELEASES.values():
            response = await recorder.fetch_bytes(spec.url, {})
            if len(response.content) != spec.bytes or hashlib.sha256(response.content).hexdigest() != spec.sha256:
                raise ValueError("ATB release size changed; review publisher release before recording")
            reader = pq.ParquetFile(pa.BufferReader(response.content))
            if not {"atb_year", "technology", "scenario", "core_metric_parameter", "core_metric_variable", "units", "value"} <= set(reader.schema_arrow.names):
                raise ValueError("ATB release schema changed")
            summaries.append({"edition": spec.edition, "release": spec.release, "bytes": spec.bytes,
                              "sha256": hashlib.sha256(response.content).hexdigest(),
                              "rows": reader.metadata.num_rows, "schema": str(reader.schema_arrow)})
        recorder.write(ROOT / "tests/fixtures/nlr-atb.json",
            "Recorded live from anonymous ATB 2024 v4.0.0 and 2025 v2.0.0 Parquet releases from the publisher-listed OEDI assets. Binary bytes are preserved and content-addressed; no credentials were held or sent. See THIRD_PARTY_DATA.yml.")
        for summary in summaries:
            print(summary)
    finally:
        await close_shared_client()


if __name__ == "__main__":
    asyncio.run(record())
