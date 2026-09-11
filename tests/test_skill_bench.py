"""The bench runner: the checkable half of every skill's bench tasks runs
against recorded publisher responses.

Architecture Part 1 § 4.3 says a skill without bench tasks does not ship,
and `test_skills.py` counts the rows. Until 2026-09-11 nothing ran them.
Each skill's `bench.yaml` splits its tasks into `checked`, whose steps this
file executes and grades, and `reader`, which a person grades because no
recording covers the call. Every task number in the SKILL.md table has to
appear in exactly one of the two lists, so a task cannot go quietly
ungraded, and at least one has to be checked.

The expectation language is deliberately small: a coverage dimension by
value, a dotted path into `data` compared for equality or against
`min`/`max`/`nonempty`/`any`, the warning codes that must be present,
whether `next_actions` is non-empty, and whether some evidence entry
carries a publisher-supplied locator. A step that needs more than that is
a step that should become its own domain test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from doe_mcp.servers.build import registries

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))


def _table_tasks(path: Path) -> set[int]:
    body = path.read_text(encoding="utf-8").split("## Bench tasks", 1)[1]
    return {int(m.group(1))
            for m in re.finditer(r"^\|\s*(\d+)\s*\|", body, re.M)}


def _bench(path: Path) -> dict:
    return yaml.safe_load((path.parent / "bench.yaml").read_text(
        encoding="utf-8"))


@pytest.mark.parametrize("path", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_every_bench_task_is_checked_or_read(path):
    name = path.parent.name
    assert (path.parent / "bench.yaml").exists(), (
        f"{name}: no bench.yaml. Every task in the SKILL.md table is either "
        "checked by this suite or graded by a reader, and the file says "
        "which.")
    bench = _bench(path)
    checked = {t["task"] for t in bench.get("checked") or []}
    reader = set(bench.get("reader") or [])
    assert checked.isdisjoint(reader), f"{name}: {checked & reader} in both"
    table = _table_tasks(path)
    assert checked | reader == table, (
        f"{name}: bench.yaml covers {sorted(checked | reader)}, the table "
        f"holds {sorted(table)}")
    assert checked, f"{name}: no task is checked by the suite"


def _steps():
    out = []
    for path in SKILLS:
        for task in _bench(path).get("checked") or []:
            for n, step in enumerate(task["steps"], 1):
                out.append(pytest.param(
                    step, id=f"{path.parent.name}-{task['task']}.{n}-"
                             f"{step['tool']}"))
    return out


def _tool(name: str):
    for reg in registries().values():
        for spec in reg.tools():
            if spec.name == name:
                return spec.fn
    raise KeyError(f"no tool named {name!r} is registered")


def _walk(node, dotted: str):
    for part in dotted.split("."):
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node


def _check(env, expect: dict) -> None:
    for key, want in expect.items():
        if key == "warnings":
            codes = {w.code.value for w in env.warnings}
            assert not set(want) - codes, (
                f"warnings {set(want) - codes} absent; present: {codes}")
        elif key == "next_actions":
            assert bool(env.next_actions) is (want == "nonempty")
        elif key == "evidence":
            if want.get("locators") == "some":
                assert any(e.locator for e in env.evidence), (
                    "no evidence entry carries a publisher-supplied locator")
        elif key.startswith("coverage."):
            got = getattr(env.coverage, key.split(".", 1)[1]).value
            assert got == want, f"{key}: {got!r} != {want!r}"
        elif key.startswith("data."):
            got = _walk(env.data, key.split(".", 1)[1])
            if isinstance(want, dict):
                if "min" in want:
                    assert got >= want["min"], f"{key}: {got} < {want['min']}"
                if "max" in want:
                    assert got <= want["max"], f"{key}: {got} > {want['max']}"
                if want.get("nonempty"):
                    assert got, f"{key}: empty"
                if "any" in want:
                    assert any(all(item.get(k) == v
                                   for k, v in want["any"].items())
                               for item in got), (
                        f"{key}: no element matches {want['any']}")
            else:
                assert got == want, f"{key}: {got!r} != {want!r}"
        else:
            raise AssertionError(f"unknown expectation {key!r}")


@pytest.mark.parametrize("step", _steps())
async def test_bench_step(step, ctx, keyed_ctx):
    fn = _tool(step["tool"])
    env = await fn(keyed_ctx if step.get("keyed") else ctx,
                   **(step.get("args") or {}))
    _check(env, step.get("expect") or {})
