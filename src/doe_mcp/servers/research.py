"""`doe-research` — the literature, dataset, software, and discovery server."""
from __future__ import annotations

import os
import sys

from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "research:default")
    if not profile.startswith("research:"):
        print(f"doe-research: profile {profile!r} does not belong to this "
              "server; use research:default or research:discovery",
              file=sys.stderr)
        return 2
    ctx = load_context(server_name="doe-research")
    build_server(ctx, profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
