"""Tool registration: deterministic ordering, toolsets, sizing bands.

- Registration order IS the wire order (`tools/list` SHOULD be deterministic
  per the 2026-07-28 spec; a contract test asserts it).
- The alias table ships empty but wired: renaming a tool means adding its old
  name here, and a test proves resolution works before it is ever needed.
- Profiles compose (package, toolset) selections. Activating a profile that
  references a missing package fails at startup, loudly, rather than serving
  a silently smaller tool surface.

The band numbers are decision 0014-A's, enforced at runtime rather than only
in CI: 8-12 tools in a default profile, a hard ceiling of 20 anywhere. They
are not style preferences. They are where measured tool-selection accuracy
falls off, which is the whole reason this project has ~10 domain servers
instead of one server with 240 tools.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("doe_mcp.toolreg")

# old-name -> current-name. Empty until the first rename; never delete rows.
DEPRECATED_TOOL_ALIASES: dict[str, str] = {}


def resolve_alias(name: str) -> str:
    return DEPRECATED_TOOL_ALIASES.get(name, name)


@dataclass(frozen=True)
class ToolSpec:
    name: str                     # e.g. "research.search_literature"
    description: str
    toolset: str                  # e.g. "default", "discovery", "crosswalk"
    contract_version: str
    fn: Callable[..., Awaitable[Any]]


@dataclass
class ToolRegistry:
    package: str                  # "research" | "registry" | "discovery"
    _tools: list[ToolSpec] = field(default_factory=list)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if any(t.name == spec.name for t in self._tools):
            raise ValueError(f"duplicate tool name {spec.name!r} in "
                             f"{self.package}")
        self._tools.append(spec)
        return spec

    def tools(self, toolset: str | None = None) -> list[ToolSpec]:
        if toolset is None or toolset == "*":
            return list(self._tools)
        out = [t for t in self._tools if t.toolset == toolset]
        if not out:
            raise ValueError(
                f"toolset {toolset!r} selects zero tools in package "
                f"{self.package!r}; known toolsets: "
                f"{sorted({t.toolset for t in self._tools})}")
        return out

    def toolsets(self) -> set[str]:
        return {t.toolset for t in self._tools}


# Profiles per server. The key is "<server>:<profile>".
# The discovery pair rides in research:default rather than behind a named
# toolset: architecture Part 1 § 4.1 puts the discovery toolset in the Phase-1 research server,
# and a caller who does not know which DOE catalog holds a dataset needs the
# fan-out on the default surface, not one toolset switch away.
PROFILES: dict[str, list[tuple[str, str]]] = {
    "research:default": [("research", "default"),
                         ("registry", "discovery-min"),
                         ("discovery", "default"),
                         ("docs", "default"),
                         ("tech", "default")],
    "research:discovery": [("research", "default"),
                           ("registry", "discovery-min"),
                           ("discovery", "default"),
                           ("docs", "default"),
                           ("tech", "default"),
                           ("registry", "discovery")],
    "research:all": [("research", "*"), ("registry", "*"),
                     ("discovery", "*"), ("docs", "*"),
                     ("tech", "*")],
    "energy:default": [("energy", "default"), ("registry", "discovery-min")],
    "energy:all": [("energy", "*"), ("registry", "*")],
    "earth:default": [("earth", "default"), ("registry", "discovery-min")],
    "earth:all": [("earth", "*"), ("registry", "*")],
    "materials:default": [("materials", "default"),
                          ("registry", "discovery-min")],
    "materials:all": [("materials", "*"), ("registry", "*")],
}

PROFILE_FLOOR = 8
PROFILE_DEFAULT_CEILING = 12
PROFILE_HARD_CEILING = 20


def expand_profile(profile: str,
                   registries: dict[str, ToolRegistry]) -> list[ToolSpec]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; known: "
                         f"{sorted(PROFILES)}")
    out: list[ToolSpec] = []
    for package, toolset in PROFILES[profile]:
        reg = registries.get(package)
        if reg is None:
            raise ValueError(
                f"profile {profile!r} references package {package!r} which "
                "is not loaded — refusing to start with a silently smaller "
                "tool surface")
        out.extend(reg.tools(toolset))
    if len(out) > PROFILE_HARD_CEILING:
        raise ValueError(
            f"profile {profile!r} expands to {len(out)} tools, over "
            f"decision 0014's ceiling of {PROFILE_HARD_CEILING}")
    if profile.endswith(":default") and len(out) > PROFILE_DEFAULT_CEILING:
        raise ValueError(
            f"profile {profile!r} expands to {len(out)} tools, over "
            f"decision 0014's ceiling of {PROFILE_DEFAULT_CEILING} for a "
            "default profile. A task profile may go to 20; a default one may "
            "not — the measured selection cliffs are what the number is for.")
    if len(out) < PROFILE_FLOOR:
        # A warning, not a refusal. The floor describes a filled-out toolset,
        # and a hard floor would refuse to start the server that exists
        # whenever a domain is still being built.
        log.warning(
            "profile %r expands to %d tools, under decision 0014's floor of "
            "%d. That is a report that a domain is still being built, not a "
            "fault.", profile, len(out), PROFILE_FLOOR)
    return out
