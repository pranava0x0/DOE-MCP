"""Serve pinned ATB assumptions; PVWatts remains source-gated."""
import os
from pathlib import Path
import sys

from ..core.credentials import Credentials
from ..runtime import load_context
from .build import build_server


def main() -> int:
    profile = os.environ.get("DOE_MCP_PROFILE", "energy-tech:default")
    if profile not in {"energy-tech:default", "energy-tech:all"}:
        print("doe-energy-tech: use energy-tech:default or energy-tech:all", file=sys.stderr)
        return 2
    # No active source here needs a key. Do not even read the user's store.
    credentials = Credentials(values={}, path=Path("/nonexistent"), file_exists=False)
    build_server(load_context(server_name="doe-energy-tech", credentials=credentials), profile).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
