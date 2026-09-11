"""DOE-MCP Quickstart Demo.

Shows how to query DOE-MCP tools programmatically in Python, inspecting
the structured provenance envelope returned by each operation.
"""
from __future__ import annotations

import asyncio
from doe_mcp.core.toolreg import expand_profile
from doe_mcp.runtime import load_context
from doe_mcp.servers.build import registries


async def main() -> None:
    print("Loading DOE-MCP context and registry...")
    ctx = load_context()
    all_regs = registries()

    # Expand the research profile
    tools = {s.name: s for s in expand_profile("research:all", all_regs)}

    # 1. Resolve organization aliases
    resolve_tool = tools["registry.resolve_org"]
    print("\n1. Resolving organization 'NREL'...")
    res1 = await resolve_tool.fn(ctx, query="NREL")
    resolved = res1.data.get("resolved", {})
    print(f"   Name: {resolved.get('name')}")
    print(f"   Status: {resolved.get('status')}")
    print(f"   Former names: {resolved.get('former_names')}")
    print(f"   Provenance: {res1.provenance[0].steward}")

    # 2. National laboratory crosswalk
    crosswalk_tool = tools["registry.lab_crosswalk"]
    print("\n2. Querying Oak Ridge National Laboratory data crosswalk...")
    res2 = await crosswalk_tool.fn(ctx, lab="ornl")
    sources = res2.data.get("sources", [])
    print(f"   Sources found: {len(sources)}")
    print(f"   Coverage registry: {res2.coverage.registry.value}")
    if sources:
        print(f"   Sample source: {sources[0]['id']} ({sources[0].get('name')})")

    # 3. Search registry sources
    search_tool = tools["registry.search_sources"]
    print("\n3. Searching registry sources for 'climate'...")
    res3 = await search_tool.fn(ctx, text="climate", limit=2)
    matched = res3.data.get("sources", [])
    print(f"   Matched systems: {len(matched)}")
    for m in matched[:2]:
        print(f"   - {m['id']}: {m['name']} (state: {m.get('declared_state')})")

    print("\nAll demo queries completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
