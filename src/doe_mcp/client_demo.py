"""A bounded SDK client round trip using only the local source registry."""
from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .core.envelope import Envelope


async def round_trip(timeout: float = 20) -> dict:
    """Exercise initialize, discovery, structured success and a typed error."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "doe_mcp.cli", "serve", "--profile", "research:default"],
    )
    async with asyncio.timeout(timeout):
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timeout) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                result = await session.call_tool("registry.resolve_org", {"query": "NREL"})
                if result.is_error or not result.structured_content:
                    raise RuntimeError("registry call did not return a structured envelope")
                envelope = Envelope.model_validate(result.structured_content)
                refused = await session.call_tool("registry.resolve_org", {"query": ""})
                error_text = " ".join(getattr(c, "text", "") for c in refused.content)
                if not refused.is_error or "InvalidQuery" not in error_text:
                    raise RuntimeError("typed tool error did not survive the transport")
                return {
                    "server": initialized.server_info.name,
                    "tool_count": len(listed.tools),
                    "envelope": envelope.model_dump(mode="json", by_alias=True),
                    "typed_error": "InvalidQuery",
                }
