"""`doe-earth` — point weather, environmental datasets, climate-model search,
and an edge-sensor inventory."""
from __future__ import annotations

import os
import sys

from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "earth:default")
    if not profile.startswith("earth:"):
        print(f"doe-earth: profile {profile!r} does not belong to this "
              "server; use earth:default or earth:all", file=sys.stderr)
        return 2
    ctx = load_context(server_name="doe-earth")
    build_server(ctx, profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
