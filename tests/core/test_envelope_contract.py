"""The envelope's wire contract.

The published JSON Schema and the pydantic models must not drift apart: a
client validating structured content against a stale schema rejects valid
answers, which looks like a server bug and is not.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from doe_mcp.core.assemble import EnvelopeBuilder
from doe_mcp.core.envelope import (AccessPath,
                                   AuthorityLevel, Citation, Coverage,
                                   Envelope, ExecutionCoverage,
                                   RegistryCoverage,
                                   ResultCoverage, WarningCode, utc_now_iso)

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "envelope.schema.json"


def _builder(tool: str = "test.tool") -> EnvelopeBuilder:
    return EnvelopeBuilder(server="doe-mcp", server_version="0", tool=tool,
                           contract_version="1", registry_revision="r",
                           adapters={})


def test_committed_schema_matches_the_models():
    committed = json.loads(SCHEMA.read_text())
    assert committed == Envelope.wire_schema(), (
        "schemas/envelope.schema.json is out of date. Regenerate it with "
        "`python tools/build_schema.py` — do not hand-edit it.")


def test_execution_rides_as_underscore_execution_on_the_wire():
    b = _builder()
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.empty))
    wire = env.model_dump(mode="json")
    assert "_execution" in wire and "execution" not in wire


def test_empty_optional_lists_are_dropped_not_emitted_as_empty():
    """Absent-means-none. An empty `next_actions` array reads as "we looked
    and there is nothing to suggest", which is a different claim from not
    having looked."""
    b = _builder()
    env = b.build({}, Coverage(registry=RegistryCoverage.none,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.empty))
    wire = env.model_dump(mode="json")
    for key in ("next_actions", "resources", "access_recipes",
                "requires_user_choice"):
        assert key not in wire
    assert "warnings" in wire, "warnings is always present, often empty"


def test_the_five_coverage_dimensions_always_survive_serialization():
    b = _builder()
    env = b.build({}, Coverage(registry=RegistryCoverage.partial,
                               execution=ExecutionCoverage.partial,
                               result=ResultCoverage.hit))
    cov = env.model_dump(mode="json")["coverage"]
    for dim in ("registry", "execution", "pagination", "source_claim",
                "result"):
        assert dim in cov, f"{dim} must never be dropped"


def test_missing_publisher_date_raises_freshness_unavailable():
    b = _builder()
    b.add_source(source_id="s", steward="Pub", system="Sys", dataset="D",
                 dataset_version="current",
                 authority_level=AuthorityLevel.primary,
                 access_path=AccessPath.live, source_updated_at=None,
                 retrieved_at=utc_now_iso(), cache_age_seconds=0)
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.hit))
    assert WarningCode.freshness_unavailable in {w.code for w in env.warnings}


def test_required_citation_becomes_a_warning_carrying_the_string():
    """A publisher's citation requirement is an obligation on the caller.
    Recording it in a manifest and not surfacing it hands them the obligation
    without the means to meet it."""
    b = _builder()
    b.add_source(source_id="arm", steward="ORNL", system="ARM",
                 dataset="ARM data", dataset_version="current",
                 authority_level=AuthorityLevel.primary,
                 access_path=AccessPath.live, source_updated_at="2026-01-01",
                 retrieved_at=utc_now_iso(), cache_age_seconds=0,
                 citation=Citation(doi="10.5439/x", recommended="Cite ARM.",
                                   required=True))
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.hit))
    warning = next(w for w in env.warnings
                   if w.code == WarningCode.citation_required)
    assert "Cite ARM." in warning.message


def test_two_versions_of_one_dataset_raise_mixed_vintages():
    """The canonical error this ecosystem produces: ATB 2024 numbers quoted
    beside ATB 2026 numbers as though they were the same series."""
    b = _builder()
    common = dict(steward="NLR", system="ATB", dataset="Annual Technology "
                  "Baseline", authority_level=AuthorityLevel.primary,
                  access_path=AccessPath.live, source_updated_at="2026-01-01",
                  retrieved_at=utc_now_iso(), cache_age_seconds=0)
    b.add_source(source_id="atb-2024", dataset_version="ATB 2024", **common)
    b.add_source(source_id="atb-2026", dataset_version="ATB 2026", **common)
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.hit))
    warning = next(w for w in env.warnings
                   if w.code == WarningCode.mixed_vintages)
    assert "ATB 2024" in warning.message and "ATB 2026" in warning.message


def test_one_dataset_at_one_version_does_not_warn():
    b = _builder()
    common = dict(steward="NLR", system="ATB", dataset="ATB",
                  dataset_version="ATB 2026",
                  authority_level=AuthorityLevel.primary,
                  access_path=AccessPath.live, source_updated_at="2026-01-01",
                  retrieved_at=utc_now_iso(), cache_age_seconds=0)
    b.add_source(source_id="a", **common)
    b.add_source(source_id="b", **common)
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.hit))
    assert WarningCode.mixed_vintages not in {w.code for w in env.warnings}


def test_evidence_must_reference_a_registered_source():
    b = _builder()
    with pytest.raises(ValueError, match="unknown source entry"):
        b.add_evidence(source_ref="source_99", record_id="x",
                       retrieved_at=utc_now_iso(), transformations=[])


def test_funder_and_host_are_dropped_when_absent():
    """A null funder on the ~85% of sources whose steward and funder are the
    same organization is noise that dilutes the cases where it matters."""
    b = _builder()
    b.add_source(source_id="s", steward="OSTI", system="Sys", dataset="D",
                 dataset_version="current",
                 authority_level=AuthorityLevel.primary,
                 access_path=AccessPath.live, source_updated_at="2026-01-01",
                 retrieved_at=utc_now_iso(), cache_age_seconds=0)
    entry = b.build({}, Coverage(registry=RegistryCoverage.covered,
                                 execution=ExecutionCoverage.complete,
                                 result=ResultCoverage.hit)
                    ).model_dump(mode="json")["provenance"][0]
    assert "funder" not in entry and "host" not in entry


def test_access_recipes_dedupe_on_kind_and_uri():
    from doe_mcp.core.envelope import RecipeKind
    b = _builder()
    for _ in range(3):
        b.add_recipe(RecipeKind.doi, "https://doi.org/10.1/x")
    env = b.build({}, Coverage(registry=RegistryCoverage.covered,
                               execution=ExecutionCoverage.complete,
                               result=ResultCoverage.hit))
    assert len(env.access_recipes) == 1
