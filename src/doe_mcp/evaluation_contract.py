"""Content bindings shared by the evaluation runner and offline comparisons."""
from __future__ import annotations

import hashlib
import json

RUBRIC_VERSION = "bench-rubric-1"


def content_sha256(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def task_binding(row: dict) -> dict:
    return {key: row[key] for key in
            ("task_id", "skill", "bench_task", "kind", "prompt", "mutation")}


def bind_run(row: dict) -> None:
    row["task_sha256"] = content_sha256(task_binding(row))
    row["rubric_sha256"] = content_sha256(row["rubric"])
    if "answer" in row:
        row["answer_sha256"] = content_sha256(row["answer"])
