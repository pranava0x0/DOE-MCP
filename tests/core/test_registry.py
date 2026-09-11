"""The registry, its activation gates, and the organization table."""
from __future__ import annotations

import pytest
import yaml

from doe_mcp.core.organizations import OrganizationTable
from doe_mcp.core.errors import SourceNotActivated
from doe_mcp.core.registry import (ACTIVATABLE, AutomationStatus,
                                   DeclaredState, SourceManifest,
                                   validate_manifest)
from tests.conftest import SOURCES


def _base_manifest(**overrides) -> SourceManifest:
    doc = yaml.safe_load((SOURCES / "research" / "osti-gov-records.yaml").read_text())
    doc.update(overrides)
    return SourceManifest.model_validate(doc)


@pytest.fixture
def vocab():
    doc = yaml.safe_load((SOURCES / "capabilities.yaml").read_text())
    return {c["id"] for c in doc["capabilities"]}


@pytest.fixture
def orgs():
    return OrganizationTable.load(SOURCES / "organizations.yaml").ids()


def test_the_shipped_registry_loads_and_has_active_sources(registry):
    assert len(registry.manifests) >= 60, (
        "the registry is the deliverable: it encodes the research files as "
        "manifests, and a thin one means the inventory was lost")
    assert sum(1 for m in registry.manifests.values() if m.is_active()) >= 5


def test_every_lifecycle_state_is_accounted_for(registry):
    for m in registry.manifests.values():
        if m.is_active():
            assert m.access.terms_reviewed_at, f"{m.id} active without a terms review"
            assert m.capabilities, f"{m.id} active with no capability"
            assert m.health.probe != "none", f"{m.id} active with no probe"
        else:
            assert m.lifecycle.blocked_reason, (
                f"{m.id} is not active and does not say why")
            assert not m.capabilities, (
                f"{m.id} is not active but declares capabilities, which is a "
                "routing promise it cannot keep")


def test_active_requires_an_activatable_automation_status(vocab, orgs):
    m = _base_manifest(access={**_base_manifest().access.model_dump(mode="json"),
                               "automation_status": "outreach_pending"})
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("outreach_pending cannot be active" in p.problem
               for p in problems)


def test_the_inventory_adapter_cannot_be_active(vocab, orgs):
    m = _base_manifest(adapter={"type": "none"})
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("cannot be active" in p.problem for p in problems)


def test_an_unknown_organization_id_is_caught(vocab, orgs):
    m = _base_manifest(publisher={"steward": "not-a-real-org",
                                  "authority_level": "primary"})
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("is not in the organization table" in p.problem
               for p in problems)


def test_a_capability_outside_the_vocabulary_is_caught(vocab, orgs):
    m = _base_manifest(capabilities=[{"id": "invented.capability"}])
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("not in the vocabulary" in p.problem for p in problems)


def test_a_citation_requirement_without_the_string_is_caught(vocab, orgs):
    access = _base_manifest().access.model_dump(mode="json")
    access["citation_required"] = True
    access["citation_text"] = None
    problems = validate_manifest(_base_manifest(access=access), "x", vocab, orgs)
    assert any("citation_required=true needs citation_text" in p.problem
               for p in problems)


def test_an_adapter_block_is_validated_against_its_adapter(vocab, orgs):
    m = _base_manifest(adapter={"type": "osti_family",
                                "base_url": "https://x", "flavor": "nonsense"})
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("adapter params invalid" in p.problem for p in problems)


def test_outreach_pending_sources_exist_and_are_never_selectable(registry):
    """GESDB and the LANL sequence databases have anti-automation postures.
    Recording them without querying them is the whole point of the state."""
    pending = [m for m in registry.manifests.values()
               if m.access.automation_status == AutomationStatus.outreach_pending]
    assert pending, "the outreach_pending state exists for real sources"
    for m in pending:
        assert not m.is_active()
        assert m.access.automation_status not in ACTIVATABLE


def test_selection_never_returns_a_proposed_source(registry):
    for capability in sorted(registry.capability_vocab):
        for m in registry.select(capability):
            assert m.is_active()


def test_planned_capabilities_outside_the_vocabulary_are_caught(vocab, orgs):
    m = _base_manifest(planned_capabilities=["invented.capability"])
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("planned_capabilities not in the vocabulary" in p.problem
               for p in problems)


def test_a_capability_cannot_be_both_planned_and_declared(vocab, orgs):
    m = _base_manifest(planned_capabilities=["literature.search"])
    problems = validate_manifest(m, "x", vocab, orgs)
    assert any("planned or served, not both" in p.problem for p in problems)


def test_proposed_sources_name_what_they_would_serve(registry):
    """Typed, not a substring match over prose: the field is what lets a
    coverage gap say 'not yet, and here is what is in the way'."""
    waiting = registry.proposed_for_capability("dataset.search")
    ids = {m.id for m in waiting}
    assert {"nlr-geothermal-data-repository", "pnnl-mhkdr", "netl-edx"} <= ids
    for m in waiting:
        assert not m.is_active() and m.lifecycle.blocked_reason
    planned = [m for m in registry.manifests.values() if m.planned_capabilities]
    assert len(planned) >= 20, "the migration from authority_notes was lost"
    for m in planned:
        assert not m.is_active() or not (
            set(m.planned_capabilities) & m.capability_ids())


def test_the_gate_refuses_a_source_flipped_to_proposed_in_memory(registry):
    m = registry.get("fueleconomy-ws")
    m.lifecycle.declared_state = DeclaredState.proposed
    with pytest.raises(SourceNotActivated):
        registry.require_active("fueleconomy-ws")
    assert registry.select("vehicle.find") == []


def test_folded_yaml_prose_reaches_callers_without_line_breaks(registry):
    """Manifest prose is written as folded scalars for readability, which
    leaves hard-wrapped lines in the loaded string. A caller should never see
    a newline in the middle of a sentence."""
    for m in registry.manifests.values():
        for text in [m.authority_notes, m.access.terms_notes,
                     m.coverage.scope, *m.coverage.known_limitations]:
            assert "\n" not in text, f"{m.id}: unfolded prose reached a caller"


def test_all_seventeen_labs_are_in_the_organization_table():
    table = OrganizationTable.load(SOURCES / "organizations.yaml")
    assert len(table.labs()) == 17


def test_every_lab_has_at_least_one_registered_source(registry):
    """The crosswalk has to be able to answer for all seventeen, including
    the labs whose honest answer is 'almost nothing structured, and here is
    why'."""
    table = OrganizationTable.load(SOURCES / "organizations.yaml")
    bare = [o.id for o in table.labs() if not registry.for_lab(o.id)]
    assert not bare, f"no registered source carries data for: {bare}"


def test_renamed_organizations_carry_the_date_they_changed():
    table = OrganizationTable.load(SOURCES / "organizations.yaml")
    for org in table.orgs.values():
        if org.aliases.former_names and org.kind.value != "external":
            assert (org.renamed_on or org.rename_date_unverified
                    or "dismantled" in (org.note or "")), (
                f"{org.id} has former names but neither a renamed_on date "
                "nor rename_date_unverified, so \"(formerly X)\" strings "
                "here can never be pruned and nobody can tell whether the "
                "date is missing or unknown")
