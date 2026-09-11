"""MCP client configuration writing (decision 0003-B, 0012-B).

Two rules shape this file:

1. **`configure --all` is a Phase-1 deliverable, not a later convenience.**
   Per-server entry points cost the user one config entry per server, and
   that cost is the only real objection to the packaging choice. Making
   "register everything" one command is the mitigation.

2. **Credentials never go in a client config.** Client configs get pasted
   into issues, synced to repositories, and shared in screenshots. Keys live
   in the user's own credentials file, which the servers read at startup.
   There is no code path here that writes a key into a client config, and a
   test asserts it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..servers.lineup import shipping

# Keyed by the `configure` argument (`research`, `energy-data`), which is the
# server name without its `doe-` prefix. Derived from the lineup so this
# table cannot disagree with the servers that ship.
SERVERS = {
    s.short: {"command": s.command, "description": s.description,
              "needs_credentials": list(s.needs_credentials)}
    for s in shipping()}


@dataclass
class ClientTarget:
    name: str
    path: Path
    key_path: tuple[str, ...]
    """Where the server map lives in the client's config document."""


def _home() -> Path:
    return Path.home()


def client_targets() -> dict[str, ClientTarget]:
    home = _home()
    if sys.platform == "darwin":
        claude_desktop = (home / "Library" / "Application Support" / "Claude"
                          / "claude_desktop_config.json")
    elif sys.platform.startswith("win"):
        claude_desktop = (Path(os.environ.get("APPDATA", home)) / "Claude"
                          / "claude_desktop_config.json")
    else:
        claude_desktop = home / ".config" / "Claude" / "claude_desktop_config.json"
    return {
        "claude-desktop": ClientTarget("Claude Desktop", claude_desktop,
                                       ("mcpServers",)),
        "claude-code": ClientTarget("Claude Code", home / ".claude.json",
                                    ("mcpServers",)),
        "vscode": ClientTarget("VS Code",
                               home / ".config" / "Code" / "User" / "mcp.json",
                               ("servers",)),
        "cursor": ClientTarget("Cursor", home / ".cursor" / "mcp.json",
                               ("mcpServers",)),
    }


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError as err:
        raise SystemExit(
            f"{path} is not valid JSON ({err}). Fix or move it before "
            "configuring; overwriting a config you cannot parse would "
            "destroy settings that are not ours.") from err


def _nested(doc: dict, key_path: tuple[str, ...]) -> dict:
    node = doc
    for key in key_path:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise SystemExit(f"existing config key {'.'.join(key_path)!r} is "
                             "not an object; refusing to overwrite it")
    return node


def entry_for(server: str) -> dict:
    """One client-config entry. Deliberately minimal: a command and no
    environment block, because a credential has no business here."""
    return {"command": SERVERS[server]["command"], "args": []}


def configure(client: str, servers: list[str], *,
              dry_run: bool = False) -> tuple[Path, dict]:
    targets = client_targets()
    if client not in targets:
        raise SystemExit(f"unknown client {client!r}; known: "
                         f"{sorted(targets)}")
    unknown = [s for s in servers if s not in SERVERS]
    if unknown:
        raise SystemExit(f"unknown servers {unknown}; known: "
                         f"{sorted(SERVERS)}")

    target = targets[client]
    doc = _load(target.path)
    node = _nested(doc, target.key_path)
    for server in servers:
        node[f"doe-{server}"] = entry_for(server)

    if not dry_run:
        if target.path.exists():
            # A config that was fine a moment ago should stay recoverable.
            shutil.copy2(target.path, target.path.with_suffix(
                target.path.suffix + ".doe-mcp-backup"))
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(json.dumps(doc, indent=2) + "\n")
    return target.path, doc
