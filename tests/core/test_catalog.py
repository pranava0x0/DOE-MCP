"""The plug-in catalog's `kind` axis (decision 0018, extended 2026-10-20).

A catalog entry used to mean "an MCP server a client can install and
call". DOE's Genesis Agent Skills is a public catalog of agent
instructions, not a server, so the entry says which it is, and the model
refuses the two claims a skill pack cannot make: that it answers a
capability, and that it carries an envelope.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from doe_mcp.core.catalog import CatalogEntry, EntryKind, SubMcpCatalog

ROOT = Path(__file__).resolve().parents[2]

PACK = {"id": "a-pack", "name": "A pack", "kind": "skill_pack",
        "maintainer": "someone", "why_not_absorbed": "instructions only",
        "last_checked": "2026-10-20"}


def test_an_entry_is_a_server_unless_it_says_otherwise():
    entry = CatalogEntry.model_validate({**PACK, "kind": "server"})
    assert entry.kind == EntryKind.server
    bare = {k: v for k, v in PACK.items() if k != "kind"}
    assert CatalogEntry.model_validate(bare).kind == EntryKind.server


@pytest.mark.parametrize("claim, message", [
    ({"capabilities": ["literature.search"]}, "maps to no capability"),
    ({"compliance": "full"}, "is opaque"),
])
def test_a_skill_pack_cannot_claim_to_answer(claim, message):
    with pytest.raises(ValidationError, match=message):
        CatalogEntry.model_validate({**PACK, **claim})


def test_genesis_is_listed_as_a_skill_pack_that_routes_nowhere():
    catalog = SubMcpCatalog.load(ROOT / "sources" / "catalog")
    genesis = catalog.get("genesis-agent-skills")
    assert genesis is not None and genesis.kind == EntryKind.skill_pack
    assert genesis in catalog.listed()
    for capability in ("literature.search", "compute.facility_status",
                       "dataset.get_record"):
        assert genesis not in catalog.for_capability(capability)


async def test_list_neighbors_says_which_kind_each_entry_is(ctx):
    from doe_mcp.domains import registry_tools
    env = await registry_tools.list_neighbors(ctx)
    kinds = {row["id"]: row["kind"] for row in env.data["neighbors"]}
    assert kinds["genesis-agent-skills"] == "skill_pack"
    assert kinds["nepa-mcp"] == "server"
    assert "skill_pack" in env.data["note"]


def test_the_catalog_counts_servers_and_skill_packs_apart():
    """The README, the site and `doctor` print this phrase; one number for
    both kinds would count a skill pack as a server."""
    def entry(i, kind):
        return CatalogEntry.model_validate({**PACK, "id": f"e{i}", "kind": kind})
    assert (SubMcpCatalog([entry(1, "server"), entry(2, "server"),
                           entry(3, "skill_pack")]).count_phrase()
            == "2 server entries and 1 skill pack in the plug-in catalog")
    assert (SubMcpCatalog([entry(1, "server")]).count_phrase()
            == "1 server entry in the plug-in catalog")
