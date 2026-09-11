"""The `curated` adapter: project-maintained data, sourced and dated.

Some answers are not in any API. DOE's own list of its Public Reusable
Research (PuRe) data resources is a page on science.osti.gov, and the
seven-entry list on it is exactly the kind of small, slow-moving,
high-authority fact a tool should be able to return without a network call.

The temptation is to hard-code it in Python. The reason not to is that a
hard-coded list has no provenance: nobody can see where it came from, when it
was checked, or that it is ours rather than DOE's. So curated data lives in
YAML files under sources/data/ with the page they were transcribed from and
the date, it reaches callers through the same envelope as everything else,
and its authority level is `official_derived` — a transcription of an
official list, not the official list itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from ..core.envelope import utc_now_iso
from ..core.errors import SourceUnavailable
from ..core.registry import SourceManifest, register_adapter_params
from .base import Fetched

ADAPTER_VERSION = "1"


class CuratedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_file: str
    """Path relative to the sources/ directory."""
    collection_key: str
    transcribed_from: str
    transcribed_at: str


register_adapter_params("curated", CuratedParams)


@dataclass
class CuratedResult:
    entries: list[dict[str, Any]]
    transcribed_from: str
    transcribed_at: str


class CuratedAdapter:
    """Read-only, and offline by construction — it makes no network call, so
    there is no egress policy to apply."""

    version = ADAPTER_VERSION

    def __init__(self, sources_dir: Path) -> None:
        self._sources_dir = sources_dir

    @staticmethod
    def params_for(manifest: SourceManifest) -> CuratedParams:
        return CuratedParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def read(self, manifest: SourceManifest) -> Fetched[CuratedResult]:
        """Offline, so the provenance is the read itself: retrieved now, never
        from a cache, from the data file rather than a URL. Returned as
        `Fetched` like every other adapter operation so a domain tool reads
        provenance from one place."""
        params = self.params_for(manifest)
        path = self._sources_dir / params.data_file
        if not path.exists():
            raise SourceUnavailable(
                f"curated data file {params.data_file} is missing from the "
                "installed registry; reinstall the package.")
        doc = yaml.safe_load(path.read_text()) or {}
        entries = doc.get(params.collection_key)
        if not isinstance(entries, list):
            raise SourceUnavailable(
                f"curated data file {params.data_file} has no "
                f"{params.collection_key!r} list.")
        return Fetched(
            value=CuratedResult(
                entries=[e for e in entries if isinstance(e, dict)],
                transcribed_from=params.transcribed_from,
                transcribed_at=params.transcribed_at),
            retrieved_at=utc_now_iso(), cache_age_seconds=0,
            request_url=params.data_file, from_cache=False)
