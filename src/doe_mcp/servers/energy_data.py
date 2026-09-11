"""`doe-energy-data` — EIA statistics and the DOE/EPA vehicle service."""
from __future__ import annotations

import os
import sys

from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "energy:default")
    if not profile.startswith("energy:"):
        print(f"doe-energy-data: profile {profile!r} does not belong to this "
              "server; use energy:default", file=sys.stderr)
        return 2
    ctx = load_context(server_name="doe-energy-data")
    build_server(ctx, profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
