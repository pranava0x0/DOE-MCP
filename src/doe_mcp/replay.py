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
    manifests = []
    for path in sorted(directory.glob("*.json")):
        content = path.read_bytes()
        document = json.loads(content)
        item = {"file": path.name, "sha256": hashlib.sha256(content).hexdigest(),
                "captured_at": document.get("recorded_at")}
        sidecars = [entry["body_ref"] for entry in document["interactions"] if "body_ref" in entry]
        if sidecars:
            item["binary_assets"] = sidecars
        manifests.append(item)
    return manifests


def load_replay_context(directory: Path, sources: Path | None = None, *,
                        keyed: bool = False,
                        credential_names: tuple[str, ...] = ()) -> RuntimeContext:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("no recorded fixtures found")
    merged = {}
    for path in paths:
        merged.update(ReplayFetcher.from_file(path).interactions)
    fetcher = ReplayFetcher(interactions=merged)
    cache = TTLCache()
    names = set(credential_names) | ({"EIA_API_KEY"} if keyed else set())
    if not names <= {"EIA_API_KEY", "API_DATA_GOV_KEY"}:
        raise ValueError("only supported replay credential names may be requested")
    credentials = Credentials(values={name: "replay-only" for name in names},
                              path=Path("/nonexistent"), file_exists=False)
    adapters = {}
    for kind, (field_name, cls) in ADAPTER_CLASSES.items():
        if kind == "curated":
            continue
        kwargs = {"fetcher": fetcher, "cache": cache}
        if kind in ("eia_v2", "regulations_gov", "pvwatts_v8"):
            kwargs["credentials"] = credentials
        adapters[field_name] = cls(**kwargs)
    return load_context(sources, credentials=credentials, **adapters)
