"""Preserve original tool envelopes and publish complete bundles atomically."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..core.errors import SourceUnavailable
from ..workflows import tool_spec


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


class Run:
    def __init__(self, ctx, app: str, mode: str, fixtures: list[dict] | None = None):
        self.ctx = ctx
        self.report = {"application": app, "mode": mode, "fixtures": fixtures or [], "steps": [],
                       "retrieval_note": ("Original envelopes are preserved. In recorded mode, retrieval clocks describe replay execution; fixture capture times remain separate."),
                       "limitations": []}

    async def call(self, tool: str, **args):
        env = await tool_spec(tool).fn(self.ctx, **args)
        if env.coverage.source_failures or env.coverage.execution.value != "complete":
            raise SourceUnavailable(f"application step {tool} did not complete")
        self.report["steps"].append({"tool": tool, "args": args,
                                     "envelope": env.model_dump(mode="json", by_alias=True)})
        return env


def export_bundle(output: Path, report: dict, files: dict[str, bytes]) -> Path:
    """All serialization and validation precedes disk writes; never replace a run."""
    payloads = {**files, "report.json": json_bytes(report)}
    if any(Path(name).name != name or name in {"", ".", "..", "manifest.json"} for name in payloads):
        raise ValueError("bundle file names must be simple names")
    payloads["manifest.json"] = json_bytes({"application": report["application"], "mode": report["mode"],
        "files": [{"file": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                  for name, body in sorted(payloads.items())]})
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; choose a new bundle directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".doe-app-", dir=output.parent))
    try:
        for name, body in payloads.items():
            (temporary / name).write_bytes(body)
        # mkdir is the exclusive claim: a concurrent writer cannot be overwritten.
        output.mkdir()
        try:
            os.rename(temporary, output)
        except BaseException:
            output.rmdir()
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output / "report.json"
