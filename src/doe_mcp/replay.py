"""Build an isolated, credential-free context from recorded publisher bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .adapters import ADAPTER_CLASSES
from .adapters.base import TTLCache
from .adapters.replay import ReplayFetcher
from .core.credentials import Credentials
from .runtime import RuntimeContext, load_context


def fixture_manifest(directory: Path) -> list[dict]:
    return [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
             "captured_at": json.loads(p.read_text()).get("recorded_at")}
            for p in sorted(directory.glob("*.json"))]


def load_replay_context(directory: Path, sources: Path | None = None, *,
                        keyed: bool = False) -> RuntimeContext:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("no recorded fixtures found")
    merged = {}
    for path in paths:
        merged.update(ReplayFetcher.from_file(path).interactions)
    fetcher = ReplayFetcher(interactions=merged)
    cache = TTLCache()
    credentials = Credentials(values={"EIA_API_KEY": "replay-only"} if keyed else {},
                              path=Path("/nonexistent"), file_exists=False)
    adapters = {}
    for kind, (field_name, cls) in ADAPTER_CLASSES.items():
        if kind == "curated":
            continue
        kwargs = {"fetcher": fetcher, "cache": cache}
        if kind == "eia_v2":
            kwargs["credentials"] = credentials
        adapters[field_name] = cls(**kwargs)
    return load_context(sources, credentials=credentials, **adapters)
