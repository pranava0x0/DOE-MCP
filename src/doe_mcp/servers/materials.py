"""`doe-materials` — computed inorganic structures and quantum-chemistry
basis sets."""
from __future__ import annotations

import os
import sys

from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "materials:default")
    if not profile.startswith("materials:"):
        print(f"doe-materials: profile {profile!r} does not belong to this "
              "server; use materials:default or materials:all",
              file=sys.stderr)
        return 2
    ctx = load_context(server_name="doe-materials")
    build_server(ctx, profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
