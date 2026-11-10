"""Start one GCAM domain server with its own profile and credential scope."""
import os
import sys

from ..core.toolreg import PROFILES
from ..runtime import load_context
from .build import build_server
from .lineup import by_key


def serve(domain: str) -> int:
    spec = by_key()[domain]
    profile = os.environ.get("DOE_MCP_PROFILE", spec.default_profile)
    if not profile or profile not in PROFILES or profile.split(":")[0] != domain:
        print(f"{spec.name}: use {domain}:default or {domain}:all", file=sys.stderr)
        return 2
    build_server(load_context(server_name=spec.name), profile).run()
    return 0
