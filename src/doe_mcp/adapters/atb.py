"""Hash-pinned Parquet scan; bounded batches and exact publisher dimensions."""
from __future__ import annotations

import copy
import hashlib
import json
import math

from pydantic import BaseModel, ConfigDict

from ..atb_releases import COMMON_COLUMNS, FILTER_COLUMNS, release_for
from ..core.errors import InvalidQuery, SourceSchemaChanged, SourceUnavailable
from ..core.registry import register_adapter_params
from .base import Fetched, HttpFetcher, TTLCache, egress_policy_for, shared_cache


class AtbParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


register_adapter_params("atb_parquet", AtbParams)


def validate_query(filters, rows, offset):
    if type(rows) is not int or not 1 <= rows <= 50 or type(offset) is not int or offset < 0:
        raise InvalidQuery("rows must be 1–50 and offset must be a nonnegative integer")
    if filters is not None and (not isinstance(filters, dict) or any(
            key not in FILTER_COLUMNS or not (value is None or isinstance(value, str))
            for key, value in filters.items())):
        raise InvalidQuery("filters must map published dimension names to exact text or null; use describe_atb to inspect them")


class AtbAdapter:
    version = "1"

    def __init__(self, fetcher=None, cache: TTLCache | None = None):
        self._fetcher = fetcher
        self._cache = cache or shared_cache()
        self._queries: dict[str, dict] = {}

    async def scan(self, manifest, edition: int, filters: dict | None, *, rows=10, offset=0, dimension=None):
        spec = release_for(edition)
        validate_query(filters, rows, offset)
        if dimension is not None and dimension not in FILTER_COLUMNS:
            raise InvalidQuery("dimension must be a published ATB qualifier")
        try:
            import pyarrow as pa
            import pyarrow.compute as pc
            import pyarrow.parquet as pq
        except ImportError as err:
            raise SourceUnavailable("ATB Parquet support requires doe-mcp[energy-tech]") from err
        result = self._cache.get(manifest.id, spec.url, {}, manifest.freshness.ttl_hint_seconds)
        if result is None:
            params = AtbParams.model_validate(manifest.adapter.model_dump(exclude={"type"}))
            fetcher = self._fetcher or HttpFetcher(egress_policy_for(manifest, params.base_url))
            response = await fetcher.fetch_bytes(spec.url, {})
            content = response.content
        else:
            content = result.payload
        # Check before handing any bytes to the native Parquet parser.
        if len(content) != spec.bytes or hashlib.sha256(content).hexdigest() != spec.sha256:
            raise SourceSchemaChanged("ATB release differs from its pinned size/SHA256; review and record the named release before use")
        if result is None:
            result = self._cache.put(manifest.id, spec.url, {}, content, response.headers)
        query_key = json.dumps([spec.sha256, filters or {}, rows, offset, dimension], sort_keys=True)
        if query_key in self._queries:
            return Fetched.of(result, copy.deepcopy(self._queries[query_key]))
        try:
            reader = pq.ParquetFile(pa.BufferReader(content))
            expected = list(COMMON_COLUMNS) + (["ref_summary", "ref_evidence_type"] if edition == 2025 else [])
            if reader.schema_arrow.names != expected:
                raise SourceSchemaChanged("ATB release columns differ from the recorded edition")
            total, selected, values = 0, [], set()
            number = 0
            projected = sorted({"atb_year", dimension, *(filters or {})}) if dimension is not None else None
            for batch in reader.iter_batches(batch_size=4096, columns=projected, use_threads=False):
                mask = pc.equal(batch.column(batch.schema.get_field_index("atb_year")), edition)
                if not pc.all(mask).as_py():
                    raise SourceSchemaChanged("ATB file contains a different edition")
                for key, value in (filters or {}).items():
                    column = batch.column(batch.schema.get_field_index(key))
                    comparison = pc.is_null(column) if value is None else pc.equal(pc.cast(column, pa.string()), value)
                    mask = pc.and_(mask, pc.fill_null(comparison, False))
                if dimension is not None:
                    column = batch.column(batch.schema.get_field_index(dimension)).filter(mask)
                    values.update(pc.unique(column).to_pylist())
                else:
                    indices = pc.indices_nonzero(mask).to_pylist()
                    for position in indices[max(0, offset - total):max(0, offset + rows - total)]:
                        row = {key: batch.column(i)[position].as_py() for i, key in enumerate(expected)}
                        if row["value"] is not None and not math.isfinite(row["value"]):
                            raise SourceSchemaChanged("ATB metric value is not finite")
                        selected.append({"row_number": number + position + 1, "values": row})
                    total += len(indices)
                number += batch.num_rows
            if dimension is not None:
                ordered = sorted(values, key=lambda value: (value is not None, str(value)))
                total, selected = len(ordered), ordered[offset:offset + rows]
            value = {"records": selected, "total_matches": total,
                     "table_rows": reader.metadata.num_rows, "columns": expected}
            if len(self._queries) >= 64:
                self._queries.pop(next(iter(self._queries)))
            self._queries[query_key] = copy.deepcopy(value)
            return Fetched.of(result, value)
        except SourceSchemaChanged:
            raise
        except (pa.ArrowException, ValueError, TypeError) as err:
            raise SourceSchemaChanged("ATB release could not be read as the pinned Parquet schema") from err
