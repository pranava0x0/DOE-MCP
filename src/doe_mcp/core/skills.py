"""Reading a skill's frontmatter, in the one shape the Agent Skills format
allows.

The format (agentskills.io, which the MCP Skills extension delegates to and
which DOE's Genesis Agent Skills catalog enforces) permits six top-level
keys: `name`, `description`, `license`, `allowed-tools`, `metadata`,
`compatibility`. `metadata` maps strings to strings. Until 2026-10-20 the
skills here put `capabilities`, `servers` and `credentials` at the top level
as YAML lists, which every client tolerated and the format's own validator
rejects. They now live under `metadata` with a `doe-mcp-` prefix, as one
line of space-separated ids each, because the strictest reader in that
ecosystem parses a metadata child as a single `key: value` line and nothing
more.

This module is the only parser of that shape. The tests, the site builder
and the installed-wheel check all read through it, so a skill that drifts
back to the old shape fails everywhere at once rather than in whichever
caller happened to look. There is deliberately no fallback to the old keys:
a reader that accepts both would hide the regression it exists to catch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PREFIX = "doe-mcp-"
FIELDS = ("capabilities", "servers", "credentials")
NONE = "none"
"""The explicit empty value. A blank reads as an oversight; `none` says the
skill was checked and needs nothing."""


@dataclass(frozen=True)
class Skill:
    path: Path
    name: str
    description: str
    compatibility: str | None
    capabilities: tuple[str, ...]
    servers: tuple[str, ...]
    credentials: tuple[str, ...]
    frontmatter: dict
    body: str


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (raw frontmatter, body). The delimiters are whole `---` lines,
    not the substring, so a `---` inside a value cannot end the block.
    Lines split on newline only: `str.splitlines` also breaks on form feeds
    and other separators, which would let one inside a value end the block
    early."""
    if text.startswith("\ufeff"):
        raise ValueError("the file opens with a byte-order mark; save it "
                         "as UTF-8 without one")
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        raise ValueError("no YAML frontmatter: the file must open with a "
                         "line that is exactly ---")
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r") == "---":
            return ("".join(ln + "\n" for ln in lines[1:i]),
                    "\n".join(lines[i + 1:]))
    raise ValueError("frontmatter is not closed by a --- line")


def _ids(path: Path, metadata: dict, field: str) -> tuple[str, ...]:
    key = PREFIX + field
    if key not in metadata:
        raise ValueError(f"{path}: metadata has no {key!r}")
    value = metadata[key]
    if not isinstance(value, str) or "\n" in value or not value.strip():
        raise ValueError(
            f"{path}: metadata {key!r} must be one line of space-separated "
            f"ids, or {NONE!r}")
    words = tuple(value.split())
    if words == (NONE,):
        return ()
    if NONE in words:
        raise ValueError(f"{path}: {key!r} mixes {NONE!r} with ids")
    if len(set(words)) != len(words):
        raise ValueError(f"{path}: {key!r} repeats an id")
    return words


def read_skill(path: Path) -> Skill:
    raw, body = split_frontmatter(path.read_text(encoding="utf-8"))
    front = yaml.safe_load(raw)
    if not isinstance(front, dict):
        raise ValueError(f"{path}: frontmatter is not a mapping")
    metadata = front.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: no metadata mapping")
    ids = {field: _ids(path, metadata, field) for field in FIELDS}
    return Skill(
        path=path,
        name=str(front.get("name") or ""),
        description=" ".join(str(front.get("description") or "").split()),
        compatibility=front.get("compatibility"),
        capabilities=ids["capabilities"],
        servers=ids["servers"],
        credentials=ids["credentials"],
        frontmatter=front,
        body=body,
    )


def read_skills(root: Path) -> list[Skill]:
    return [read_skill(p) for p in sorted(root.glob("*/SKILL.md"))]
