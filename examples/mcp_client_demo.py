"""DOE-MCP JSON-RPC Client Integration Demo.

Demonstrates how an external AI client or automation framework communicates
with a DOE-MCP server process over stdio using the Model Context Protocol.
"""
from __future__ import annotations

import json
import subprocess
import sys


def send_rpc(proc: subprocess.Popen, method: str, params: dict | None = None, req_id: int = 1) -> dict:
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }
    payload = json.dumps(msg) + "\n"
    proc.stdin.write(payload.encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline().decode("utf-8").strip()
    return json.loads(line) if line else {}


def main() -> None:
    print("Launching 'doe-mcp serve --profile research:default' on stdio...")
    cmd = [sys.executable, "-m", "doe_mcp.cli", "serve", "--profile", "research:default"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # 1. Initialize session
        init_res = send_rpc(
            proc,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "doe-mcp-demo-client", "version": "1.0"},
            },
            req_id=1,
        )
        server_info = init_res.get("result", {}).get("serverInfo", {})
        print(f"Connected to: {server_info.get('name')} v{server_info.get('version')}")

        # 2. List tools
        tools_res = send_rpc(proc, "tools/list", req_id=2)
        tools = tools_res.get("result", {}).get("tools", [])
        print(f"Discovered {len(tools)} tools in research:default profile:")
        for t in tools[:5]:
            print(f"  - {t['name']}: {t.get('description', '').split('.')[0]}")
        if len(tools) > 5:
            print(f"  ... and {len(tools) - 5} more.")

    finally:
        proc.terminate()
        proc.wait(timeout=2)
        print("MCP server session closed cleanly.")


if __name__ == "__main__":
    main()
