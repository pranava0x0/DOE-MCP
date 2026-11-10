"""Read a released GCAM input table, including the notes above its CSV header."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from itertools import chain

from pydantic import BaseModel, ConfigDict

from ..core.errors import SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import Fetched, HttpFetcher, TextFetcher, TTLCache, egress_policy_for, shared_cache


class InputTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    columns: list[str]


class GcamParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    tables: dict[str, InputTable]


register_adapter_params("gcam_csv", GcamParams)


@dataclass
class Table:
    notes: list[str]
    rows: list[dict[str, str | None]]


def parse_table(text: str, columns: list[str]) -> Table:
    stream = io.StringIO(text.removeprefix("\ufeff"), newline="")
    # Only leading comment lines are metadata. Quoted CSV fields below the
    # header can span lines and can contain commas or a literal '#'.
    lines = iter(stream)
    notes = []
    for line in lines:
        if line.startswith("#"):
            notes.append(line[1:].strip())
        elif line.strip():
            break
    else:
        raise SourceSchemaChanged("GCAM table has no header.")
    reader = csv.reader(chain([line], lines), strict=True)
    try:
        if next(reader) != columns:
            raise SourceSchemaChanged("GCAM column names differ from the verified release.")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise SourceSchemaChanged("GCAM row width differs from its header.")
            rows.append(dict(zip(columns, (cell if cell else None for cell in row), strict=True)))
    except csv.Error as err:
        raise SourceSchemaChanged("GCAM table contains invalid CSV.") from err
    if not rows:
        raise SourceSchemaChanged("Previously populated GCAM release table has no rows.")
    return Table(notes, rows)


class GcamAdapter:
    version = "1"

    def __init__(self, fetcher: TextFetcher | None = None, cache: TTLCache | None = None):
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> GcamParams:
        return GcamParams.model_validate(manifest.adapter.model_dump(exclude={"type"}))

    async def read(self, manifest: SourceManifest, name: str) -> Fetched[Table]:
        params = self.params_for(manifest)
        spec = params.tables[name]
        fetched = self._cache.get(manifest.id, spec.url, {}, manifest.freshness.ttl_hint_seconds)
        if fetched is None:
            client = self._fetcher or HttpFetcher(policy=egress_policy_for(manifest, params.base_url))
            response = await client.fetch_text(spec.url, {})
            table = parse_table(response.text, spec.columns)
            fetched = self._cache.put(manifest.id, spec.url, {}, response.text, response.headers)
        else:
            table = parse_table(fetched.payload, spec.columns)
        return Fetched.of(fetched, table)
