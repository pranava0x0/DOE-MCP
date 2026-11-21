"""GCAM discovery describes local metadata without a publisher read."""
import pytest

from doe_mcp.core.errors import SourceNotActivated
from doe_mcp.core.registry import DeclaredState
from doe_mcp.domains.registry_tools import describe_source
from doe_mcp.gcam_catalog import TABLES
from doe_mcp.workflows import tool_spec


@pytest.mark.parametrize("domain", TABLES)
async def test_gcam_inventory_maps_every_table_without_fetching(ctx, replay, monkeypatch, domain):
    async def refuse_read(*args, **kwargs):
        pytest.fail("describing a local manifest must not fetch a publisher table")

    monkeypatch.setattr(ctx.gcam, "read", refuse_read)
    manifest = ctx.sources.get("gcam-" + domain)
    params = ctx.gcam.params_for(manifest)
    env = await describe_source(ctx, manifest.id)
    source = env.data["source"]
    inventory = source["table_inventory"]
    rows = inventory["tables"]
    assert inventory["metadata_source"] == "local_manifest_and_tool_catalog"
    assert "no publisher table was fetched" in inventory["note"]
    assert inventory["record_count"] == len(rows) == len(params.tables)
    assert {row["table"] for row in rows} == set(TABLES[domain])
    for row in rows:
        spec = params.tables[row["table"]]
        assert row == {
            "table": row["table"], "tool": f"{domain}.{row['table']}",
            "description": TABLES[domain][row["table"]],
            "filter_columns": spec.columns, "file_url": spec.url,
        }
        assert tool_spec(row["tool"]).fn
    assert source["dataset_version"] == manifest.coverage.dataset_version
    assert source["known_limitations"] == manifest.coverage.known_limitations
    assert all(p.source_id != manifest.id for p in env.provenance)
    assert not replay.calls


@pytest.mark.parametrize("domain", TABLES)
async def test_proposed_gcam_metadata_remains_visible_but_reads_are_gated(ctx, replay, domain):
    manifest = ctx.sources.get("gcam-" + domain)
    manifest.lifecycle.declared_state = DeclaredState.proposed
    env = await describe_source(ctx, manifest.id)
    source = env.data["source"]
    assert source["declared_state"] == "proposed"
    assert source["table_inventory"]["tables"]
    tool = source["table_inventory"]["tables"][0]["tool"]
    with pytest.raises(SourceNotActivated):
        await tool_spec(tool).fn(ctx)
    assert not replay.calls


async def test_other_source_details_keep_their_existing_shape(ctx):
    env = await describe_source(ctx, "osti-gov-records")
    assert "table_inventory" not in env.data["source"]
    assert env.data["source"]["id"] == "osti-gov-records"
    assert (await describe_source(ctx, "absent-source")).data["source"] is None
