"""Checks drawn from the maintainer's own security tracker.

`pranava0x0.github.io/vibe-coding-security` collects the supply-chain and
prompt-injection incidents that hit people building with AI coding tools,
and its prevention notes are a checklist. This project SHIPS an MCP server,
so the MCP-hygiene rules apply to what it hands a user, and the CI rules
apply to the workflow that builds it.

Two of those rules were being broken and are now enforced here rather than
remembered.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# A pinned `uses:` is `owner/repo@<40 hex>`, with the version in a comment.
_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}$")


def _jobs(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.name for p in WORKFLOWS])
def test_every_action_is_pinned_to_a_commit_not_a_tag(path):
    """A tag can be moved onto attacker code; a commit cannot.

    This is the tj-actions/reviewdog class of incident, and it applies to
    first-party actions too: "reputable" is not "immutable". Dependabot
    understands a SHA pin with a version comment and raises the bump as a
    pull request somebody reads.
    """
    floating = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("- uses:"):
            continue
        ref = stripped.split("uses:", 1)[1].split("#", 1)[0].strip()
        if not _SHA_PIN.fullmatch(ref):
            floating.append(ref)
    assert not floating, (
        f"{path.name} uses actions pinned to a movable ref: {floating}. Pin "
        "each to a full commit SHA with the version in a trailing comment.")


@pytest.mark.parametrize("path", WORKFLOWS, ids=[p.name for p in WORKFLOWS])
def test_the_workflow_scopes_its_token(path):
    """With no `permissions:` block a job inherits the repository's default
    GITHUB_TOKEN scope, which can be read/write across contents and
    packages. Nothing in this workflow writes anything, and one of its jobs
    runs on a schedule — a weekly job holding a write token is the case the
    checklist calls out."""
    doc = _jobs(path)
    top = doc.get("permissions")
    jobs = doc.get("jobs") or {}
    unscoped = [name for name, job in jobs.items()
                if top is None and "permissions" not in job]
    assert not unscoped, (
        f"{path.name}: job(s) {unscoped} inherit the default token scope. "
        "Set `permissions:` at the top level or per job.")
    if top is not None:
        assert top.get("contents") in (None, "read"), (
            f"{path.name} grants write access to repository contents; "
            "nothing here writes.")


def test_dependabot_keeps_the_action_pins_current():
    """Pinning without a bump path is how a pin becomes a stale, unpatched
    action nobody is watching."""
    path = ROOT / ".github" / "dependabot.yml"
    if not path.exists():
        pytest.skip("dependabot is disabled")
    doc = yaml.safe_load(path.read_text())
    ecosystems = {u["package-ecosystem"] for u in doc["updates"]}
    assert "github-actions" in ecosystems


def test_a_server_holds_only_the_credentials_it_declares():
    """"Never share tokens between MCP servers" — each should hold
    service-specific, minimal-scope credentials.

    Three of the four shipping servers declare no credential at all, and
    each of them used to load the whole credentials file: the EIA key, and
    every key the later phases add, in a process with no source to spend it
    on. A prompt injection reaching a tool in one server had the others'
    keys within reach.
    """
    from doe_mcp.core.credentials import Credentials
    from doe_mcp.runtime import load_context
    from doe_mcp.servers.lineup import shipping

    everything = Credentials(
        values={"EIA_API_KEY": "sentinel-eia", "MP_API_KEY": "sentinel-mp",
                "EDX_API_KEY": "sentinel-edx"},
        path=Path("/nonexistent"), file_exists=True)
    for spec in shipping():
        ctx = load_context(ROOT / "sources", credentials=everything,
                           server_name=spec.name)
        held = set(ctx.credentials.values)
        assert held == set(spec.needs_credentials), (
            f"{spec.name} declares {spec.needs_credentials or '()'} and "
            f"holds {sorted(held)}")


def test_no_tool_argument_can_change_where_a_request_goes():
    """The lethal trifecta needs private data, untrusted content, and
    outbound communication. This project has the last two — publisher text
    reaches the model, and it makes HTTP requests — so the mitigation it
    relies on is that the destination is never caller-controlled.

    Every adapter builds its egress policy from the MANIFEST's URL, and the
    policy allows exactly that host. An injected instruction inside a paper
    abstract cannot redirect a request, because nothing a caller passes
    reaches the host.
    """
    import doe_mcp.adapters as adapters_pkg
    source = Path(adapters_pkg.__file__).parent
    offenders = []
    for path in sorted(source.glob("*.py")):
        text = path.read_text()
        if "egress_policy_for(" not in text:
            continue
        for match in re.finditer(r"egress_policy_for\(\s*manifest,\s*([^)]+)\)",
                                 text):
            argument = " ".join(match.group(1).split())
            if not argument.startswith("params."):
                offenders.append(f"{path.name}: {argument}")
    assert not offenders, (
        "an egress policy was built from something other than a manifest "
        f"field: {offenders}")


def test_every_server_tells_the_model_that_results_are_untrusted():
    """GhostSplice (2026-08-11): splitting a malicious instruction across a
    tool's `description` and its `result` took agent compliance from 42% to
    82%, and several models from a clean 0% refusal to 100%.

    The descriptions here are this project's own. The results are not — they
    carry text publishers wrote — so the one mitigation available on this
    side of the channel is to say what kind of thing that text is, on every
    server, every time.
    """
    from doe_mcp.servers.lineup import UNTRUSTED_CONTENT_RULE, shipping
    for spec in shipping():
        assert UNTRUSTED_CONTENT_RULE in (spec.instructions or ""), spec.name
