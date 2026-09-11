"""Repository-level promises: licensing, disclosure, and that the site data
cannot drift away from the registry it claims to describe."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_the_governance_file_set_is_present():
    """The starkest gap in the repo-family study was zero governance files
    across six repositories with 58 forks of demand."""
    for name in ("README.md", "LICENSE", "NOTICE", "CONTRIBUTING.md",
                 "CODE_OF_CONDUCT.md", "CITATION.cff",
                 "THIRD_PARTY_DATA.yml", "sources/LICENSE"):
        assert (ROOT / name).exists(), f"missing {name}"


def test_the_non_affiliation_disclaimer_is_on_every_public_surface():
    """"DOE-MCP" reads official. The disclaimer is not decoration."""
    for name in ("README.md", "NOTICE"):
        text = (ROOT / name).read_text()
        assert "not affiliated" in text.lower(), name


def test_the_registry_is_cc0_and_the_code_is_apache():
    assert "CC0" in (ROOT / "sources" / "LICENSE").read_text()
    assert "Apache License" in (ROOT / "LICENSE").read_text()
    assert "CC BY 4.0" in (ROOT / "docs" / "LICENSE").read_text()
    notice = (ROOT / "NOTICE").read_text()
    for term in ("CC0-1.0", "Apache-2.0", "CC-BY-4.0"):
        assert term in notice, f"NOTICE does not record the {term} split"


def test_every_recorded_fixture_is_declared_in_third_party_data():
    """Every redistributed byte gets an entry naming the publisher, the
    retrieval date, and the rights."""
    declared = yaml.safe_load((ROOT / "THIRD_PARTY_DATA.yml").read_text())
    covered = {e["source_id"] for e in declared["redistributed"]}
    recorded = {p.stem for p in (ROOT / "tests" / "fixtures").glob("*.json")}
    assert recorded <= covered, (
        f"undeclared recorded content: {sorted(recorded - covered)}")


def test_every_fixture_says_where_it_came_from():
    for path in (ROOT / "tests" / "fixtures").glob("*.json"):
        doc = json.loads(path.read_text())
        assert doc.get("note"), f"{path.name} has no provenance note"
        assert "Recorded live from" in doc["note"], path.name


def test_the_site_data_matches_the_registry_it_describes():
    """The CI staleness test: a generated site whose numbers disagree with
    the registry is a published claim this project cannot back."""
    site = ROOT / "docs" / "data" / "site.json"
    if not site.exists():
        pytest.skip("site not generated yet; run tools/build_site.py")
    data = json.loads(site.read_text())

    import doe_mcp.adapters  # noqa: F401
    from doe_mcp.core.organizations import OrganizationTable
    from doe_mcp.core.registry import SourceRegistry
    orgs = OrganizationTable.load(ROOT / "sources" / "organizations.yaml")
    registry = SourceRegistry.load(ROOT / "sources", orgs)

    assert data["counts"]["sources"] == len(registry.manifests)
    assert data["counts"]["active"] == sum(
        1 for m in registry.manifests.values() if m.is_active())
    assert data["counts"]["organizations"] == len(orgs.orgs)
    assert data["counts"]["labs"] == len(orgs.labs())
    assert data["registry_revision"] == registry.revision


def test_the_readme_status_block_matches_the_registry():
    """The first hand-written status table was one revision behind the
    runtime within a day. It is generated now, and this is the drift alarm."""
    text = (ROOT / "README.md").read_text()
    assert "<!-- status:begin -->" in text and "<!-- status:end -->" in text
    block = text.split("<!-- status:begin -->", 1)[1].split(
        "<!-- status:end -->", 1)[0]

    import doe_mcp.adapters  # noqa: F401
    from doe_mcp.core.organizations import OrganizationTable
    from doe_mcp.core.registry import SourceRegistry
    orgs = OrganizationTable.load(ROOT / "sources" / "organizations.yaml")
    registry = SourceRegistry.load(ROOT / "sources", orgs)
    active = sum(1 for m in registry.manifests.values() if m.is_active())
    assert f"{len(registry.manifests)} source manifests" in block
    assert f"{active} active" in block
    assert f"{len(orgs.orgs)} organizations" in block
    assert registry.revision in block


def test_the_generated_tool_reference_lists_every_tool():
    from doe_mcp.core.toolreg import PROFILES, expand_profile
    from doe_mcp.servers.build import registries
    ref = ROOT / "docs" / "reference.md"
    assert ref.exists(), "run tools/build_site.py"
    text = ref.read_text()
    for profile in PROFILES:
        assert f"`{profile}`" in text, profile
        for spec in expand_profile(profile, registries()):
            assert f"`{spec.name}`" in text, spec.name


def test_no_live_nrel_gov_url_appears_in_the_shipped_tree():
    """NREL became NLR on 2025-12-01 and every *.nrel.gov domain went dark
    with NO redirect. A fetchable nrel.gov URL in shipped material resolves
    to nothing.

    The check looks for URL-SHAPED occurrences, not for the string. Prose
    explaining the rename and alias tables recording the dead domains are the
    reason a caller with a stale link ever finds out what happened, and
    banning those would remove the explanation along with the mistake.
    """
    import re
    url_shaped = re.compile(r"https?://[\w.-]*\bnrel\.gov")
    offenders = []
    for root in (ROOT / "src", ROOT / "sources", ROOT / "tools",
                 ROOT / "docs"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or path.suffix not in (".py", ".yaml", ".yml",
                                                    ".md", ".html", ".json"):
                continue
            for i, line in enumerate(
                    path.read_text(errors="replace").splitlines(), 1):
                if url_shaped.search(line):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, ("fetchable *.nrel.gov URLs:\n"
                           + "\n".join(offenders))


def test_no_recorded_fixture_carries_a_real_email_address():
    """Government APIs return people's contact details, and this repository
    commits recorded responses publicly. Those are two different acts.

    The first recording pass captured 25 addresses across four fixtures,
    including a named developer's personal gmail account. Redaction happens at
    record time in `RecordingFetcher`; this test is what makes a regression
    in that visible instead of silent.
    """
    import re
    from doe_mcp.adapters.replay import REDACTED_EMAIL
    pattern = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    for path in (ROOT / "tests" / "fixtures").glob("*.json"):
        found = {m for m in pattern.findall(path.read_text())
                 if m != REDACTED_EMAIL}
        assert not found, (
            f"{path.name} republishes real addresses: {sorted(found)[:5]}. "
            "Re-record with python tools/record_fixtures.py.")


def test_every_fixture_declares_that_it_was_redacted():
    """A reader comparing a fixture against the live API must be able to see
    that the difference is deliberate rather than drift."""
    for path in (ROOT / "tests" / "fixtures").glob("*.json"):
        doc = json.loads(path.read_text())
        assert "redaction" in doc, f"{path.name} does not say it was redacted"


def test_no_shipped_file_carries_a_home_directory_path():
    """Absolute paths from a contributor's machine leak a username and break
    for everyone else.

    Matched as a filesystem path with a username segment, not as the bare
    substring: `arcgis.com/home/item.html` appears legitimately inside a
    recorded ORNL response and is not anybody's home directory.
    """
    import re
    home_path = re.compile(r"(?:^|[\s\"'(=])(?:/Users|/home)/[a-z][\w.-]*/")
    offenders = []
    for root in (ROOT / "src", ROOT / "tools", ROOT / "sources",
                 ROOT / "docs", ROOT / "design"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or path.suffix not in (
                    ".py", ".yaml", ".yml", ".md", ".json", ".html", ".toml"):
                continue
            for i, line in enumerate(
                    path.read_text(errors="replace").splitlines(), 1):
                if home_path.search(line):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not offenders, f"absolute home paths: {offenders[:10]}"


def test_nothing_committable_carries_a_third_party_email_address():
    """Tree-wide, not just fixtures.

    Publisher content reaches this repository through two doors — recorded
    fixtures and hand-written manifest prose — and only the first is
    redacted automatically. A contributor pasting a lab contact out of a
    terms page into `terms_notes` would slip past the fixture check.
    """
    import re
    from doe_mcp.adapters.replay import REDACTED_EMAIL
    pattern = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    skip = {"research", ".venv", ".git", "__pycache__", ".pytest_cache"}
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.parts):
            continue
        if path.suffix not in (".py", ".yaml", ".yml", ".md", ".json",
                               ".html", ".toml", ".cff"):
            continue
        for found in pattern.findall(path.read_text(errors="replace")):
            if found == REDACTED_EMAIL or found.endswith(".invalid"):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {found}")
    assert not offenders, f"third-party addresses in the tree: {offenders[:8]}"


def test_the_research_directory_is_not_committable():
    """The evidence base is working notes: third-party contact details from
    government pages, half-verified findings, and material that would read as
    claims out of context. It stays local."""
    ignore = (ROOT / ".gitignore").read_text()
    assert re.search(r"^/?research/\s*$", ignore, re.M), (
        "research/ must be gitignored; working research notes remain local")
    assert re.search(r"^/?design/\s*$", ignore, re.M), (
        "design/ must be gitignored; architecture docs remain local")


def test_the_agent_instructions_are_platform_neutral():
    """AGENTS.md is the cross-platform convention. A vendor-specific file
    would leave every other coding agent without the rules — including the
    ones about not circumventing a publisher's anti-automation posture."""
    assert not (ROOT / "CLAUDE.md").exists(), (
        "vendor-specific agent instructions; AGENTS.md is the portable one")


def test_the_architecture_doc_is_one_file_with_both_parts():
    arch_doc = ROOT / "design" / "architecture.md"
    if not arch_doc.exists():
        pytest.skip("architecture doc is local and gitignored")
    text = arch_doc.read_text()
    assert "# Part 1 — the system" in text
    assert "# Part 2 — the decisions" in text
    for n in range(1, 27):
        assert f"## {n:04d} —" in text, f"decision {n:04d} missing"
    assert not (ROOT / "PLAN.md").exists()
    assert not (ROOT / "DECISIONS.md").exists()


def test_each_test_gets_its_own_registry(ctx):
    """The suite parses the registry once and deep-copies it per test, which
    took a run from 109 seconds to 4. That is only safe because tests mutate
    manifests — the sabotage tests flip `declared_state`, others set adapter
    fields — and a shared registry would leak those mutations into whatever
    ran next, in an order-dependent way that would be miserable to debug.

    This test does the mutating; the one below checks it did not escape.
    """
    manifest = ctx.sources.get("osti-gov-records")
    manifest.name = "MUTATED BY A TEST"
    manifest.adapter.base_url = "https://example.invalid"


def test_a_mutation_in_another_test_did_not_escape(ctx):
    """Runs after the one above, in file order, and would fail if the
    template were handed out instead of a copy."""
    manifest = ctx.sources.get("osti-gov-records")
    assert manifest.name != "MUTATED BY A TEST"
    assert "osti.gov" in manifest.adapter.base_url
