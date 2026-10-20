"""Skills: the frontmatter is checked, and so is the rule that they be
graded.

A skill names CAPABILITIES rather than tools, which is what lets one walk
reach a plugged-in server that mapped its own tools into the same vocabulary
(decision 0018). That only holds if the ids are real, so they are checked
against `sources/capabilities.yaml` here — a skill naming a capability
nobody serves is a walk that stops in the middle.

The bench-task rule is architecture Part 1 § 4.3's and is the reason this
file also counts rows: a walk nobody grades is a walk nobody has checked.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from doe_mcp.core.skills import read_skill

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
MIN_BENCH_TASKS = 3


def _vocabulary() -> set[str]:
    doc = yaml.safe_load((ROOT / "sources" / "capabilities.yaml").read_text())
    return {c["id"] for c in doc["capabilities"]}


def test_there_is_at_least_one_skill():
    """The layer is either built or it is not. An empty directory that the
    site counts as zero is honest; a directory that stopped existing would
    make the site's count silently right for the wrong reason."""
    assert (ROOT / "skills").is_dir()
    assert SKILLS, "no skills found under skills/*/SKILL.md"


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_names_capabilities_that_exist(path):
    vocabulary = _vocabulary()
    declared = read_skill(path).capabilities
    assert declared, f"{path.parent.name}: names no capabilities"
    unknown = [c for c in declared if c not in vocabulary]
    assert not unknown, (
        f"{path.parent.name} names capabilities that are not in the "
        f"vocabulary: {unknown}. A skill routes on these, so an id nothing "
        "serves is a walk that stops in the middle.")


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_names_the_directory_it_lives_in(path):
    skill = read_skill(path)
    assert skill.name == path.parent.name
    assert skill.description, "a skill needs a description"


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_without_bench_tasks_does_not_ship(path):
    """Architecture Part 1 § 4.3, enforced rather than stated."""
    body = read_skill(path).body
    assert "## Bench tasks" in body, (
        f"{path.parent.name}: no bench tasks. A skill without them does not "
        "ship.")
    table = body.split("## Bench tasks", 1)[1]
    rows = [ln for ln in table.splitlines()
            if ln.strip().startswith("|") and "---" not in ln]
    # header row plus the tasks
    assert len(rows) - 1 >= MIN_BENCH_TASKS, (
        f"{path.parent.name}: {len(rows) - 1} bench task(s), fewer than "
        f"{MIN_BENCH_TASKS}.")


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_names_servers_that_ship(path):
    from doe_mcp.servers.lineup import shipping
    names = {s.name for s in shipping()}
    for server in read_skill(path).servers:
        assert server in names, (
            f"{path.parent.name} names {server!r}, which is not a shipping "
            "server. A skill over a planned server is a plan, not a skill.")


def test_the_skills_index_lists_every_skill():
    index = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    for path in SKILLS:
        assert path.parent.name in index, (
            f"{path.parent.name} is not in skills/README.md")


DOTTED = re.compile(r"`([a-z_]+(?:\.[a-z_]+)+)`")
NOT_A_TOOL = {"nrel.gov", "coverage.pagination"}
"""Backticked dotted names that are prose: a dead domain the walk warns
about and an envelope field it tells the reader to check."""


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_a_skill_body_names_tools_that_exist(path):
    """The frontmatter names capabilities, so a skill routes across built-in
    and plugged-in servers alike. The walk may name the tools that serve
    them today, because an agent calls tools, but a name there has to be
    real: `research.get_fulltext_link` was merged into `research.get_record`
    on 2026-09-08 and a body still naming it would send an agent to a tool
    that is not there."""
    from doe_mcp.servers.build import registries
    tools = {spec.name for reg in registries().values() for spec in reg.tools()}
    known = tools | _vocabulary() | NOT_A_TOOL
    unknown = sorted(set(DOTTED.findall(read_skill(path).body)) - known)
    assert not unknown, (
        f"{path.parent.name} names {unknown}, which is neither a tool nor a "
        "capability id")
