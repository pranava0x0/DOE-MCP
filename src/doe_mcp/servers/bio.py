"""`doe-bio` — microbiome studies and biosamples from NMDC."""
from __future__ import annotations

import os
import sys

from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "bio:default")
    if not profile.startswith("bio:"):
        print(f"doe-bio: profile {profile!r} does not belong to this "
              "server; use bio:default or bio:all", file=sys.stderr)
        return 2
    ctx = load_context(server_name="doe-bio")
    build_server(ctx, profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
