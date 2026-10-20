"""Skills conform to the Agent Skills format, which a test holds.

The MCP Skills extension (SEP-2640) delegates its file format to the Agent
Skills specification, and DOE's Genesis Agent Skills catalog (released
2026-10-15) enforces that format with a validator whose `spec` profile is
the strictest reader in the ecosystem. Until 2026-10-20 every skill here
failed it: `capabilities`, `servers` and `credentials` sat at the top level,
and six files wrote them as flow lists. Clients tolerated both, which is why
nobody noticed, and why this is a test rather than a sentence.

The rules are re-implemented from their descriptions, not vendored: the
upstream validator is thousands of lines that track someone else's release
schedule, and a dozen of its rules are the ones that bind files like ours.
Each check below names the upstream rule it mirrors.

Not mirrored, so not claimed: Unicode normalization and separator rules,
case and hyphen near-misses in key names, strictyaml's plain-scalar start
characters, and name collisions across a shared catalog. A one-off run of
the upstream validator over a scratch copy of `skills/` closes that gap; it
executes third-party code, so it is a maintainer's step and not CI.

Two rules are stricter than upstream. The description's "use"/"when" cue
is a warning there and a failure here, and `allowed-tools` is forbidden
outright: a skill in this project pre-approves no tool. Genesis's HPC
skills pre-approve job submission and cancellation, which is the case the
second rule exists to rule out.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml
from yaml import events as ev_, nodes as node_

from doe_mcp.core.skills import FIELDS, PREFIX, split_frontmatter

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))

ALLOWED_KEYS = {"name", "description", "license", "allowed-tools",
                "metadata", "compatibility"}
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
USE_CUE = re.compile(r"\b(use|when)\b", re.IGNORECASE)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CLIENT_TOKENS = ("$ARGUMENTS", "${CLAUDE_", "!`")
LINK = re.compile(r"\]\(([^)\s]+)\)|^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)
FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
MAX_LINES = 500


def _syntax_problems(raw: str) -> list[str]:
    """What PyYAML's safe_load quietly accepts or hides: flow collections,
    anchors and aliases, explicit tags, and duplicate keys (the last one
    wins without a word). Read from the event and node streams instead."""
    problems = []
    if "\t" in raw:
        # stricter than spec-fm-tabs-structural: a tab anywhere, not only in indentation
        problems.append("tab character in frontmatter")
    if CONTROL.search(raw):
        problems.append("control character in frontmatter")  # spec-fm-control-characters-crash
    try:
        events = list(yaml.parse(raw, Loader=yaml.SafeLoader))
        root = yaml.compose(raw, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        return problems + [f"frontmatter is not YAML: {exc}"]
    for ev in events:
        if isinstance(ev, ev_.CollectionStartEvent) and ev.flow_style:
            problems.append("flow-style collection")  # spec-fm-no-flow-style
        if isinstance(ev, ev_.AliasEvent) or getattr(ev, "anchor", None):
            problems.append("anchor or alias")  # spec-fm-no-anchors-aliases
        if isinstance(ev, (ev_.ScalarEvent, ev_.MappingStartEvent,
                           ev_.SequenceStartEvent)):
            implicit = getattr(ev, "implicit", True)
            if ev.tag == "!" or ev.tag is not None and not (implicit is True or (
                    isinstance(implicit, tuple) and any(implicit))):
                problems.append(f"explicit tag {ev.tag}")  # spec-fm-no-tags

    def walk(node):
        if isinstance(node, node_.MappingNode):
            seen = set()
            for key, value in node.value:
                if not isinstance(key, node_.ScalarNode):
                    problems.append("a mapping key that is not a plain value")
                    continue
                if key.value in seen:
                    problems.append(f"duplicate key {key.value!r}")  # spec-fm-no-duplicate-keys
                seen.add(key.value)
                walk(value)
        elif isinstance(node, node_.SequenceNode):
            for item in node.value:
                walk(item)
    walk(root)
    return problems


def spec_problems(text: str, dirname: str, skill_dir: Path | None = None) -> list[str]:
    """Every rule this project holds a SKILL.md to, as a list of problems.
    A pure function so the negative cases below can prove each rule bites."""
    if len(text.splitlines()) > MAX_LINES:
        return [f"more than {MAX_LINES} lines"]  # spec-body-under-500-lines
    try:
        raw, body = split_frontmatter(text)  # spec-fm-must-start-with-dashes, -closing-delimiter
    except ValueError as exc:
        return [str(exc)]
    problems = _syntax_problems(raw)
    if problems:
        return problems
    front = yaml.safe_load(raw)
    if not isinstance(front, dict) or not front:
        return ["frontmatter is not a non-empty mapping"]  # spec-fm-must-be-mapping

    extra = set(front) - ALLOWED_KEYS
    if extra:
        problems.append(f"keys outside the format: {sorted(extra)}")  # spec-keys-allowed-set
    if "allowed-tools" in front:
        problems.append("allowed-tools pre-approves tools; skills here approve none")

    name = front.get("name")
    if not isinstance(name, str) or not name:
        problems.append("name missing")  # spec-keys-required-name-description
    else:
        if not NAME.fullmatch(name) or len(name) > 64:
            problems.append(f"name {name!r} breaks the pattern or length")  # spec-name-*
        if name != dirname:
            problems.append(f"name {name!r} is not the directory {dirname!r}")

    desc = front.get("description")
    if not isinstance(desc, str) or not desc.strip():
        problems.append("description missing")
    else:
        if len(desc) > 1024:  # counts a folded scalar's trailing newline, as upstream does
            problems.append("description over 1024 characters")  # spec-desc-max-1024
        if len(desc.strip()) < 40 or not USE_CUE.search(desc):
            problems.append("description says what but not when to use it")  # spec-desc-content-guidance

    if "compatibility" in front:
        compat = front["compatibility"]
        if not isinstance(compat, str) or not 1 <= len(compat) <= 500:
            problems.append("compatibility must be a string of 1 to 500")  # spec-compat-string-max-500

    meta = front.get("metadata")
    if not isinstance(meta, dict):
        problems.append("metadata missing or not a mapping")
    else:
        for key, value in meta.items():
            if not isinstance(key, str) or not isinstance(value, str):
                problems.append(f"metadata {key!r} is not a string")  # spec-metadata-string-map-coercion
            elif "\n" in value:
                problems.append(f"metadata {key!r} spans lines")  # skill-search reads one line per key
        for field in FIELDS:
            if PREFIX + field not in meta:
                problems.append(f"metadata has no {PREFIX + field}")

    for token in CLIENT_TOKENS:
        if token in body:
            problems.append(f"client-specific token {token!r} in body")  # spec-sem-body-tokens-not-interpreted
    if skill_dir is not None:
        for inline, reference in LINK.findall(FENCE.sub("", body)):
            target = inline or reference
            if SCHEME.match(target) or target.startswith("#"):
                continue
            local = unquote(re.split(r"[?#]", target)[0])
            resolved = (skill_dir / local).resolve()
            if not resolved.is_relative_to(skill_dir.resolve()) or not resolved.exists():
                problems.append(f"relative link {target!r} leaves or misses the skill")  # spec-files-relative-paths-from-root
    return problems


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_conforms_to_the_agent_skills_format(path):
    problems = spec_problems(path.read_text(encoding="utf-8"),
                             path.parent.name, path.parent)
    assert not problems, f"{path.parent.name}: {problems}"


GOOD = """---
name: sample-skill
description: >
  Find a thing in public DOE data. Use when someone asks where the thing is.
compatibility: Needs the DOE-MCP server doe-research.
metadata:
  doe-mcp-capabilities: literature.search
  doe-mcp-servers: doe-research
  doe-mcp-credentials: none
---

# Sample
"""


def test_the_good_sample_passes():
    """The negative cases below are edits of this one, so it has to pass for
    their failures to mean anything."""
    assert spec_problems(GOOD, "sample-skill") == []


@pytest.mark.parametrize("old, new, expect", [
    ("metadata:\n", "capabilities: [literature.search]\nmetadata:\n",
     "flow-style"),
    ("metadata:\n", "servers:\n  - doe-research\nmetadata:\n",
     "keys outside the format"),
    ("doe-mcp-servers: doe-research",
     "doe-mcp-servers: [doe-research]", "flow-style"),
    ("doe-mcp-servers: doe-research",
     "doe-mcp-servers:\n    - doe-research", "is not a string"),
    ("doe-mcp-credentials: none", "doe-mcp-credentials: yes",
     "is not a string"),
    ("  doe-mcp-credentials: none\n", "", "no doe-mcp-credentials"),
    ("Use when someone asks where the thing is.", "It is a thing.",
     "not when to use"),
    ("name: sample-skill", "name: Sample_Skill", "breaks the pattern"),
    ("name: sample-skill", "name: other-skill", "is not the directory"),
    ("metadata:\n", "allowed-tools: Bash\nmetadata:\n", "allowed-tools"),
    ("metadata:\n", "metadata: &m\n", "anchor or alias"),
    ("compatibility: Needs", "compatibility: !!str Needs", "explicit tag"),
    ("name: sample-skill\n", "name: sample-skill\nname: sample-skill\n",
     "duplicate key"),
    ("# Sample\n", "# Sample\nRun $ARGUMENTS\n", "client-specific token"),
    ("---\nname", "name", "no YAML frontmatter"),
    ("name: sample-skill", "name: sample-skill\t", "tab character"),
    ("name: sample-skill", "name: sample\x07skill", "control character"),
    ("Use when someone asks where the thing is.",
     "Use when someone asks. " + "x" * 1024, "over 1024"),
    ("compatibility: Needs the DOE-MCP server doe-research.",
     "compatibility: " + "x" * 501, "1 to 500"),
    ("compatibility: Needs the DOE-MCP server doe-research.",
     "compatibility: 3", "1 to 500"),
    ("compatibility: Needs", "compatibility: ! Needs", "explicit tag"),
    ("metadata:\n", "? [a]\n: b\nmetadata:\n", "not a plain value"),
])
def test_each_rule_fails_the_file_it_describes(old, new, expect):
    bad = GOOD.replace(old, new, 1)
    assert bad != GOOD, f"the edit {old!r} did not apply"
    problems = spec_problems(bad, "sample-skill")
    assert any(expect in p for p in problems), problems


def test_a_relative_link_must_stay_inside_the_skill(tmp_path):
    skill = tmp_path / "sample-skill"
    skill.mkdir()
    (skill / "notes.md").write_text("x")
    (tmp_path / "README.md").write_text("x")
    fine = GOOD + ("\nSee [notes](notes.md), [the part](notes.md#part), "
                   "[a query](notes.md?x=1), [news](News:comp.lang.python), "
                   "[web](HTTPS://example.org) and [here](#sample).\n"
                   "\n```\n[not a link](nowhere.md)\n```\n")
    assert spec_problems(fine, "sample-skill", skill) == []
    for bad in ("See [readme](../README.md).", "See [gone](missing.md).",
                "[ref]: ../README.md"):
        problems = spec_problems(GOOD + "\n" + bad + "\n", "sample-skill", skill)
        assert any("leaves or misses" in p for p in problems), bad


def test_the_reader_names_what_it_refuses(tmp_path):
    """The reader is the one parser of the shape, so its refusals have to say
    which rule a file broke rather than fail somewhere downstream."""
    from doe_mcp.core.skills import read_skill
    skill = tmp_path / "sample-skill" / "SKILL.md"
    skill.parent.mkdir()
    for text, expect in [
        ("﻿" + GOOD, "byte-order mark"),
        (GOOD.replace("---\n", "--- \n", 1), "exactly ---"),
        (GOOD.replace("doe-mcp-servers: doe-research",
                      "doe-mcp-servers: doe-research doe-research"), "repeats"),
        (GOOD.replace("doe-mcp-credentials: none",
                      "doe-mcp-credentials: none EIA_API_KEY"), "mixes"),
    ]:
        skill.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=expect):
            read_skill(skill)
    # a form feed inside a value does not end the frontmatter early
    skill.write_text(GOOD.replace("# Sample", "x\f\n---\n# Sample"),
                     encoding="utf-8")
    assert read_skill(skill).credentials == ()
