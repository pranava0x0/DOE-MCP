"""The sub-MCP plug-in catalog (decision 0018-B, extended).

This is the umbrella layer. DOE-MCP is not the only MCP server over
DOE-adjacent data and should not try to be: PNNL ships NEPA-MCP, LBNL ships
EnergyPlus-MCP, NLR ships OpenStudio-MCP, Materials Project ships one inside
its own Python library. The catalog is how those relate to this project
without either rebuilding them or pretending to install them.

**The constraint that shaped this file**, checked against the protocol on
2026-09-01: MCP has no mechanism by which one server can advertise or install
another, and none is planned — the roadmap's progressive-discovery work is
scoped to a single server's own primitives and is labelled experimental. So a
catalog entry is prose that helps only the users who already have the other
server. That finding is why reuse splits two ways:

1. **Depend-and-wrap** — the upstream ships a Python library (Materials
   Project's `mp-api`). Declare it as a dependency, write thin tools over it,
   one install, envelope applies natively, no MCP inside MCP. These are
   recorded as manifest dependencies, NOT as catalog entries, because the
   relationship is different: the tools are ours.
2. **Catalog entry** — the upstream is server-only, or its surface is too
   large to absorb (NEPA-MCP at 23 servers and 56 tools as of 2026-09-08
   would breach the sizing ceiling on its own). Each entry carries an executable install
   recipe rather than a link, so a shell-capable client can offer one
   reviewed command.

Compliance level describes servers we would CALL, and is deliberately
pessimistic: `opaque` results are labelled unverified external evidence,
because a server that does not speak this envelope cannot be assumed to carry
provenance just by being official.
"""
from __future__ import annotations

import enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ComplianceLevel(str, enum.Enum):
    full = "full"
    """Speaks the DOE-MCP envelope."""
    provenance_lite = "provenance-lite"
    """Returns sources but not the full contract."""
    opaque = "opaque"
    """Results are treated as unverified external evidence and labelled so."""


class EntryStatus(str, enum.Enum):
    listed = "listed"
    watch = "watch"
    """Not yet public, or public but not yet reviewed. The Genesis-lane
    servers live here: gated today, and a public tier would be an entry."""
    retired = "retired"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstallRecipe(_Strict):
    """Executable, not a link. The protocol offers no way for a server to
    advertise another one, so the only useful thing to hand a caller is the
    exact command — which a client can then put in front of a human to
    approve."""

    method: str  # pipx | pip | uvx | npx | docker | source
    command: str
    verified_at: str | None = None
    notes: str | None = None


class CatalogEntry(_Strict):
    id: str
    name: str
    maintainer: str
    maintainer_org: str | None = None
    """Organization id where the maintainer is in this project's table."""
    repository: str | None = None
    status: EntryStatus = EntryStatus.listed
    compliance: ComplianceLevel = ComplianceLevel.opaque
    capabilities: list[str] = Field(default_factory=list)
    """Mappings into this project's controlled vocabulary, so skills can
    route across built-in and plugged-in servers alike."""
    tool_count: int | None = None
    server_count: int | None = None
    install: InstallRecipe | None = None
    health_probe: str | None = None
    terms_url: str | None = None
    terms_reviewed_at: str | None = None
    why_not_absorbed: str
    """Required. Every entry has to say why this is a catalog entry rather
    than something DOE-MCP built or wrapped — otherwise the catalog becomes a
    list of things nobody got round to."""
    notes: str | None = None
    last_checked: str


class SubMcpCatalog:
    def __init__(self, entries: list[CatalogEntry]) -> None:
        self.entries = {e.id: e for e in entries}
        if len(self.entries) != len(entries):
            raise ValueError("duplicate catalog entry ids")

    @classmethod
    def load(cls, catalog_dir: Path) -> "SubMcpCatalog":
        if not catalog_dir.exists():
            return cls([])
        entries = [CatalogEntry.model_validate(yaml.safe_load(p.read_text()))
                   for p in sorted(catalog_dir.rglob("*.yaml"))]
        return cls(entries)

    def get(self, entry_id: str) -> CatalogEntry | None:
        return self.entries.get(entry_id)

    def listed(self) -> list[CatalogEntry]:
        return sorted((e for e in self.entries.values()
                       if e.status == EntryStatus.listed),
                      key=lambda e: e.id)

    def for_capability(self, capability: str) -> list[CatalogEntry]:
        return sorted((e for e in self.entries.values()
                       if capability in e.capabilities
                       and e.status == EntryStatus.listed),
                      key=lambda e: e.id)

    def all(self) -> list[CatalogEntry]:
        return sorted(self.entries.values(), key=lambda e: e.id)
