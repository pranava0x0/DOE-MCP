"""Runtime context: the loaded registry and adapter instances tools run
against. Constructed once per process (server or CLI); tests construct it
with replay fetchers instead."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import ADAPTER_CLASSES, ADAPTER_VERSIONS
from .adapters.basis_sets import BasisSetExchangeAdapter
from .adapters.curated import CuratedAdapter
from .adapters.daymet import DaymetAdapter
from .adapters.eia_v2 import EiaV2Adapter
from .adapters.esgf import EsgfAdapter
from .adapters.essdive import EssDiveAdapter
from .adapters.federal_register import FederalRegisterAdapter
from .adapters.fueleconomy import FuelEconomyAdapter
from .adapters.json_document import JsonDocumentAdapter
from .adapters.opendatasoft import OpenDataSoftAdapter
from .adapters.optimade import OptimadeAdapter
from .adapters.osti_family import OstiFamilyAdapter
from .adapters.postgrest import PostgrestAdapter
from .adapters.sage import SageAdapter
from .adapters.text_feed import TextFeedAdapter
from .adapters.vips import VipsAdapter
from .core.audit import AuditLog
from .core.catalog import SubMcpCatalog
from .core.credentials import Credentials
from .core.organizations import OrganizationTable
from .core.registry import SourceRegistry
from .servers.lineup import SERVER_LINEUP


def _installed_sources_dir() -> Path:
    """sources/ lives beside the package in a wheel and two levels up in a
    source checkout. Both are supported so `pipx install` and `pip install
    -e .` behave the same."""
    packaged = Path(__file__).resolve().parent / "_sources"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[2] / "sources"


SOURCES_DIR = _installed_sources_dir()

# The manifest id used as provenance when a tool answers from this project's
# own tables rather than a publisher's system — organization resolution,
# registry searches, coverage-gap determinations.
#
# It is a real registered manifest (sources/discovery/doe-mcp-registry.yaml)
# rather than a hard-coded dict, because the envelope's rule is that every
# provenance entry resolves to a registered source. A special-cased entry
# that resolved to nothing would be the one exception, and it would sit on
# exactly the answers where the distinction matters most: "what does Oak
# Ridge publish" is DOE-MCP's inventory of ORNL, not ORNL's own statement.
PROJECT_SOURCE_ID = "doe-mcp-registry"


@dataclass
class RuntimeContext:
    sources: SourceRegistry
    organizations: OrganizationTable
    catalog: SubMcpCatalog
    credentials: Credentials
    osti: OstiFamilyAdapter
    opendatasoft: OpenDataSoftAdapter
    json_document: JsonDocumentAdapter
    curated: CuratedAdapter
    eia: EiaV2Adapter
    fueleconomy: FuelEconomyAdapter
    text_feed: TextFeedAdapter
    postgrest: PostgrestAdapter
    federal_register: FederalRegisterAdapter
    vips: VipsAdapter
    daymet: DaymetAdapter
    essdive: EssDiveAdapter
    esgf: EsgfAdapter
    sage: SageAdapter
    optimade: OptimadeAdapter
    basis_sets: BasisSetExchangeAdapter
    server_name: str = "doe-mcp"
    server_version: str = __version__
    adapters: dict[str, str] = field(
        default_factory=lambda: dict(ADAPTER_VERSIONS))
    audit: AuditLog = field(default_factory=AuditLog)

    def redact_audit_args(self) -> bool:
        """Query text is user content. A literature search can carry an
        unpublished research idea, so values are redacted from the audit log
        unless a caller explicitly asks otherwise."""
        return True


def load_context(sources_dir: Path | None = None, *,
                 credentials: Credentials | None = None,
                 server_name: str = "doe-mcp",
                 **adapters: Any) -> RuntimeContext:
    """The registry, the tables and one adapter per genre.

    Adapter instances are passed by their RuntimeContext field name (`osti=`,
    `eia=`, ...); anything not passed is constructed from `ADAPTER_CLASSES`,
    which is the one table that says what each adapter takes. A name that is
    not a field is refused rather than ignored, because a misspelled
    override would otherwise leave the live adapter in place under a test
    that believed it had replaced it.
    """
    root = sources_dir or SOURCES_DIR
    organizations = OrganizationTable.load(root / "organizations.yaml")
    creds = credentials or Credentials.load()
    # Least privilege between servers. A named server gets only the
    # credentials its lineup row declares; three of the four declare none,
    # and there is no reason for a process with no keyed source to be
    # holding the EIA key. The CLI passes no server name and keeps the whole
    # set, because `doctor` reports on all of them.
    #
    # Keyed on the server NAME, not on the profile key: they differ for
    # exactly one server — `doe-energy-data` has the key `energy` — and a
    # lookup by the stripped name missed it, which is the one server that
    # actually holds a credential.
    spec = {s.name: s for s in SERVER_LINEUP}.get(server_name)
    if spec is not None:
        creds = creds.scoped_to(spec.needs_credentials)

    fields = {field_name: (kind, cls)
              for kind, (field_name, cls) in ADAPTER_CLASSES.items()}
    unknown = sorted(set(adapters) - set(fields))
    if unknown:
        raise TypeError(f"load_context: {unknown} are not adapter fields; "
                        f"known: {sorted(fields)}")
    built: dict[str, Any] = {}
    for field_name, (kind, cls) in fields.items():
        if adapters.get(field_name) is not None:
            built[field_name] = adapters[field_name]
        elif kind == "curated":
            built[field_name] = cls(root)
        elif kind == "eia_v2":
            built[field_name] = cls(credentials=creds)
        else:
            built[field_name] = cls()
    return RuntimeContext(
        sources=SourceRegistry.load(root, organizations),
        organizations=organizations,
        catalog=SubMcpCatalog.load(root / "catalog"),
        credentials=creds,
        server_name=server_name,
        **built)
