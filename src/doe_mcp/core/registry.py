"""DOE Source Registry: manifest models, loader, activation gates, selection.

Contract: architecture Part 1 § 1.2, decision 0007-B (gates and hostile-source policy)
and 0008-B (publisher identity and churn).

Three things here are DOE's rather than inherited, each earned by something
the inventory measured:

1. **`publisher` is a `{steward, funder, host}` triple**, not one agency
   string. At least five flagship "lab assets" are funded by another agency
   (ORNL DAAC is NASA's, FAF is DOT's, LandScan is co-funded by NGA, the
   LANL HIV database is NIH's, fueleconomy.gov is DOE and EPA jointly), and
   several are served from infrastructure the steward does not own
   (developer.nlr.gov rides on GSA's api.data.gov). The operator scope rule
   (decision 0001-C) is honest only if the funder is displayed rather than
   hidden.

2. **`automation_status` has an `outreach_pending` value.** GESDB and the
   LANL sequence databases have explicit anti-automation architectures. The
   correct next step for those is an email, and a registry that can only say
   "restricted" loses the difference between "never" and "not yet asked".

3. **Probe outcomes distinguish `moved` and `blocked_probe` from `down`.** A
   dead domain with a live successor is a manifest PR, not an outage; and a
   403 to a scripted fetch is not evidence of a gate until a real browser has
   looked. Sixty-three cited URLs sat in exactly that state at the last
   sweep, and calling them "gated" in public would have been sixty-three
   wrong claims.
"""
from __future__ import annotations

import enum
from pathlib import Path

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator)

from .envelope import AuthorityLevel
from .errors import (InvalidQuery, SourceNotActivated, SourceUnavailable,
                     TermsRestricted)


def _collapse(value: str | None) -> str | None:
    """Collapse YAML folded-scalar whitespace.

    Manifest prose is written as folded scalars so it reads well in the file,
    which leaves hard-wrapped lines and a trailing newline in the loaded
    string. Those reach the envelope verbatim otherwise, and a caller sees a
    limitation note with newlines in the middle of sentences. Collapsed once
    here rather than in every consumer.
    """
    return " ".join(value.split()) if value is not None else None


class AutomationStatus(str, enum.Enum):
    permitted = "permitted"
    public_api = "public_api"
    public_download = "public_download"
    manual_review_required = "manual_review_required"
    outreach_pending = "outreach_pending"
    """The publisher's posture is anti-automation and the next step is a
    conversation, not a workaround (decision 0007-B). Gate G governs.
    Sources here are inventory: named, described, never queried."""
    restricted = "restricted"
    do_not_automate = "do_not_automate"
    unknown = "unknown"


ACTIVATABLE = {AutomationStatus.permitted, AutomationStatus.public_api,
               AutomationStatus.public_download}

# The adapter type and probe name a manifest carries when it describes no
# endpoint at all. Both are refused at the activation gate, so an inventory
# row can never be queried or probed.
INVENTORY_ADAPTER = "none"
NO_PROBE = "none"


class AccessMode(str, enum.Enum):
    anonymous = "anonymous"
    api_key = "api_key"
    account_token = "account_token"
    oauth = "oauth"
    restricted = "restricted"


class DataTier(str, enum.Enum):
    """The inventory's own A/B/C split (architecture Part 1 § 5.2–5.4), carried as a field
    because it decides tool shape: tier A gets a thin proxy, tier B gets
    catalog search plus an AccessRecipe, tier C gets patience."""

    thin_proxy = "thin_proxy"
    catalog_pointer = "catalog_pointer"
    crawl_or_defer = "crawl_or_defer"


class DeclaredState(str, enum.Enum):
    proposed = "proposed"
    active = "active"
    retired = "retired"


class OperationalState(str, enum.Enum):
    """Runtime state, never stored in manifests."""

    healthy = "healthy"
    impaired = "impaired"
    unavailable = "unavailable"
    moved = "moved"
    """The host answered from somewhere else. Distinct from `unavailable`
    because the fix is a manifest change, not a retry."""
    blocked_probe = "blocked_probe"
    """A scripted client was refused. NOT evidence of a gate: five DOE GIS
    and portal hosts are confirmed dropping plain HTTP clients while serving
    browsers fine. Needs a real-browser check before anything user-facing
    calls it gated."""
    unknown = "unknown"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Publisher(_Strict):
    """The three roles, because in this ecosystem they routinely differ."""

    steward: str
    """Organization id: who runs the system day to day."""
    funder: str | None = None
    """Organization id: who pays for the program. None means same as
    steward."""
    host: str | None = None
    """Organization id: whose infrastructure serves it, when that differs."""
    authority_level: AuthorityLevel


class Aliases(_Strict):
    """Required stanza for the source itself (decision 0008-B). Empty lists
    are fine; the absence of the stanza is not, because it is how a
    contributor is reminded that this ecosystem renames things."""

    former_names: list[str] = Field(default_factory=list)
    former_domains: list[str] = Field(default_factory=list)


class CapabilityDecl(_Strict):
    id: str
    tool_hint: str | None = None


class AdapterBlock(BaseModel):
    """`type` is fixed; everything else belongs to the adapter's params model
    and is validated against it when that adapter is registered."""

    model_config = ConfigDict(extra="allow")
    type: str


class Access(_Strict):
    mode: AccessMode
    automation_status: AutomationStatus
    tier: DataTier = DataTier.thin_proxy
    terms_url: str
    terms_notes: str
    # Optional at the schema level, required to activate. A `proposed`
    # manifest is inventory whose terms nobody has read yet; making the
    # field mandatory would force it to invent a review date.
    terms_reviewed_at: str | None = None
    terms_gap: str | None = None
    """What the terms review could NOT establish, in the publisher's terms.
    Set it and every envelope citing this source carries a terms_note
    warning quoting it, so a gap recorded in a manifest becomes a disclosure
    at the point of use."""
    citation_required: bool = False
    citation_text: str | None = None
    credential_ref: str | None = None
    """Name of the credential in the user's credentials file (DECISIONS
    0012). Never the credential itself."""
    insecure_transport: bool = False
    rate_limit_note: str | None = None

    @field_validator("terms_notes", "terms_gap", "citation_text",
                     "rate_limit_note", mode="after")
    @classmethod
    def _tidy(cls, v):
        return _collapse(v)


class Freshness(_Strict):
    expected_cadence: str
    cadence_source: str  # stated | observed | unknown, with prose
    ttl_hint_seconds: int

    @field_validator("cadence_source", mode="after")
    @classmethod
    def _tidy(cls, v):
        return _collapse(v)


class CoverageDecl(_Strict):
    scope: str
    temporal: str
    dataset_version: str = "current"
    """Vintage identity (architecture Part 1 § 3.3). 'current' for a live API; a named release
    ("ATB 2026", "CMIP6", "ENSDF April 2022") wherever the data has one, so
    the envelope can warn on cross-version joins instead of silently mixing
    them."""
    record_count: int | None = None
    record_count_checked: str | None = None
    known_limitations: list[str] = Field(default_factory=list)

    @field_validator("scope", "temporal", mode="after")
    @classmethod
    def _tidy(cls, v):
        return _collapse(v)

    @field_validator("known_limitations", mode="after")
    @classmethod
    def _tidy_list(cls, v):
        return [_collapse(x) for x in v]


class HealthDecl(_Strict):
    probe: str
    expect: dict = Field(default_factory=dict)


class Lifecycle(_Strict):
    declared_state: DeclaredState
    added: str
    last_verified: str
    verified_by: str
    blocked_reason: str | None = None
    """Why a proposed source is not active yet, in one sentence. The field
    that turns "we know it exists but can't wire it yet" from a lost note
    into a registry state (architecture Part 1 § 3.1 goal 4)."""

    @field_validator("blocked_reason", mode="after")
    @classmethod
    def _tidy(cls, v):
        return _collapse(v)


# The one category axis (decision 0026). A manifest's `domain` is the key of
# the lineup server that serves it or would serve it, and the directory it
# lives in under sources/ carries the same name. `discovery` is the one
# domain with no server of its own: doe-research serves it. A test holds
# this tuple to the lineup, and the loader refuses a manifest outside it or
# in the wrong directory.
DOMAINS: tuple[str, ...] = ("research", "discovery", "docs", "energy",
                            "energy-tech", "earth", "materials", "nuclear",
                            "bio", "projects")


class SourceManifest(_Strict):
    id: str
    name: str
    domain: str
    """Which domain server serves it or would serve it, from DOMAINS, and
    the name of the directory the manifest lives in."""

    @field_validator("domain", mode="after")
    @classmethod
    def _known_domain(cls, v: str) -> str:
        if v not in DOMAINS:
            raise ValueError(
                f"domain {v!r} is not one of {', '.join(DOMAINS)}. The domain "
                "is the directory under sources/ and the key of the server "
                "that serves it (decision 0026).")
        return v
    publisher: Publisher
    aliases: Aliases = Field(default_factory=Aliases)
    labs: list[str] = Field(default_factory=list)
    """Organization ids of the laboratories whose data this source carries.
    This is the crosswalk: "what does lab X publish?" is a registry query
    over this field, not a server topology (decision 0002-C)."""
    capabilities: list[CapabilityDecl]
    """Routing promises. Only an active manifest may declare them, and
    selection routes on these and nothing else."""
    planned_capabilities: list[str] = Field(default_factory=list)
    """What this source WOULD serve once wired up, by capability id. Typed,
    so a coverage gap can answer "not yet, and here is what is in the way"
    from a field rather than from a substring match over prose. Never routed
    on, and never counted as coverage."""
    adapter: AdapterBlock
    access: Access
    freshness: Freshness
    coverage: CoverageDecl
    authority_notes: str
    health: HealthDecl
    lifecycle: Lifecycle

    @field_validator("authority_notes", mode="after")
    @classmethod
    def _tidy(cls, v):
        return _collapse(v)

    def capability_ids(self) -> set[str]:
        return {c.id for c in self.capabilities}

    def is_active(self) -> bool:
        return self.lifecycle.declared_state == DeclaredState.active


# --- adapter params validation hook ---------------------------------------

_ADAPTER_PARAMS: dict[str, type[BaseModel]] = {}


def register_adapter_params(adapter_type: str, model: type[BaseModel]) -> None:
    _ADAPTER_PARAMS[adapter_type] = model


def registered_adapter_types() -> set[str]:
    return set(_ADAPTER_PARAMS)


# --- validation ------------------------------------------------------------

class ManifestProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    problem: str


def validate_manifest(manifest: SourceManifest, path: str,
                      known_capabilities: set[str],
                      known_orgs: set[str]) -> list[ManifestProblem]:
    """Activation-gate rules plus referential checks. Empty means valid."""
    problems: list[ManifestProblem] = []

    def bad(msg: str) -> None:
        problems.append(ManifestProblem(path=path, problem=msg))

    active = manifest.is_active()
    status = manifest.access.automation_status

    if active and status not in ACTIVATABLE:
        bad(f"declared_state=active requires automation_status in "
            f"{sorted(s.value for s in ACTIVATABLE)}, got {status.value!r}")
    if active and not manifest.access.terms_reviewed_at:
        bad("declared_state=active requires terms_reviewed_at; a source "
            "cannot be queried on terms nobody has read")
    if active and manifest.adapter.type == INVENTORY_ADAPTER:
        bad(f"adapter type {INVENTORY_ADAPTER!r} names the absence of an "
            "endpoint and cannot be active; it is inventory only")
    if active and manifest.health.probe == NO_PROBE:
        bad(f"declared_state=active requires a real health probe, not "
            f"{NO_PROBE!r}")
    if active and not manifest.capabilities:
        bad("declared_state=active requires at least one capability; an "
            "active source with none can never be selected")
    if not active and manifest.capabilities:
        # A capability id is a routing promise. Selection filters on
        # declared_state anyway, so a proposed manifest declaring one is not
        # dangerous — it is untrue, and `sources stats` counts capability
        # coverage from these rows.
        bad("only an active manifest may declare capabilities; list the "
            "intended ones under planned_capabilities until it is wired up")
    if not active and not manifest.lifecycle.blocked_reason:
        bad("a non-active manifest requires lifecycle.blocked_reason; "
            "'proposed' without a reason is the lost note this registry "
            "exists to prevent")
    if status == AutomationStatus.outreach_pending and active:
        bad("automation_status=outreach_pending cannot be active; the next "
            "step for these sources is a conversation (Gate G), not a query")
    if manifest.access.citation_required and not manifest.access.citation_text:
        bad("citation_required=true needs citation_text; telling a caller "
            "they must cite without giving them the string is not a "
            "disclosure")

    # Referential integrity against the organization table.
    for role, org_id in (("steward", manifest.publisher.steward),
                         ("funder", manifest.publisher.funder),
                         ("host", manifest.publisher.host)):
        if org_id and org_id not in known_orgs:
            bad(f"publisher.{role} {org_id!r} is not in the organization "
                "table")
    unknown_labs = set(manifest.labs) - known_orgs
    if unknown_labs:
        bad(f"labs not in the organization table: {sorted(unknown_labs)}")
    unknown_caps = manifest.capability_ids() - known_capabilities
    if unknown_caps:
        bad(f"capabilities not in the vocabulary: {sorted(unknown_caps)}")
    unknown_planned = set(manifest.planned_capabilities) - known_capabilities
    if unknown_planned:
        bad(f"planned_capabilities not in the vocabulary: "
            f"{sorted(unknown_planned)}")
    already = set(manifest.planned_capabilities) & manifest.capability_ids()
    if already:
        bad(f"planned_capabilities repeats declared capabilities "
            f"{sorted(already)}; a capability is planned or served, not both")

    # Active-only, like the other gates. An inventory row may know a source
    # needs a key without yet knowing what that key will be called; forcing
    # it to invent a name would be the same failure as forcing it to invent
    # a terms-review date.
    if active and manifest.access.mode in (AccessMode.api_key,
                                           AccessMode.account_token) \
            and not manifest.access.credential_ref:
        bad(f"declared_state=active with access.mode="
            f"{manifest.access.mode.value} requires a credential_ref naming "
            "the key in the credentials file; a keyed source cannot be "
            "queried without one, and the key never goes in a client config")

    params_model = _ADAPTER_PARAMS.get(manifest.adapter.type)
    if params_model is None:
        bad(f"adapter type {manifest.adapter.type!r} has no registered "
            "adapter (import doe_mcp.adapters before validating)")
    else:
        try:
            params_model.model_validate(
                manifest.adapter.model_dump(exclude={"type"}))
        except ValidationError as err:
            bad(f"adapter params invalid for {manifest.adapter.type!r}: "
                f"{err.errors()[0]['loc']}: {err.errors()[0]['msg']}")
    return problems


# --- the registry ----------------------------------------------------------

class SourceRegistry:
    def __init__(self, manifests: list[SourceManifest],
                 capability_vocab: set[str], revision: str) -> None:
        self.manifests = {m.id: m for m in manifests}
        if len(self.manifests) != len(manifests):
            raise ValueError("duplicate source ids in registry")
        self.capability_vocab = capability_vocab
        self.revision = revision
        self._operational: dict[str, OperationalState] = {}

    @classmethod
    def load(cls, sources_dir: Path,
             organizations: "OrganizationTableLike | None" = None
             ) -> "SourceRegistry":
        vocab_file = sources_dir / "capabilities.yaml"
        if not vocab_file.exists():
            raise FileNotFoundError(f"missing capability vocabulary: "
                                    f"{vocab_file}")
        vocab_doc = yaml.safe_load(vocab_file.read_text())
        vocab = {c["id"] for c in vocab_doc["capabilities"]}

        if organizations is None:
            from .organizations import OrganizationTable
            organizations = OrganizationTable.load(
                sources_dir / "organizations.yaml")
        known_orgs = organizations.ids()

        # `catalog/` holds sub-MCP plug-in entries (a different schema) and
        # `data/` holds curated payloads the `curated` adapter reads. Neither
        # is a source manifest.
        skip_files = {"capabilities.yaml", "organizations.yaml"}
        skip_dirs = {"catalog", "data"}
        paths = [p for p in sorted(sources_dir.rglob("*.yaml"))
                 if p.name not in skip_files
                 and not skip_dirs & set(p.parts)]
        manifests = [SourceManifest.model_validate(yaml.safe_load(p.read_text()))
                     for p in paths]
        misplaced = [f"{p}: domain {m.domain!r}"
                     for p, m in zip(paths, manifests, strict=True)
                     if p.parent.name != m.domain]
        if misplaced:
            raise ValueError(
                f"{len(misplaced)} manifest(s) sit in a directory that is "
                "not their domain (decision 0026: directory is domain is "
                f"server): {'; '.join(misplaced)}")

        # Activation-gate enforcement belongs to the runtime path, not only
        # to `doe-mcp sources validate` and CI: a manifest that fails its
        # gates must never become selectable because a CLI check was
        # skipped for one load.
        problems = [prob for path, manifest in zip(paths, manifests,
                                                   strict=True)
                    for prob in validate_manifest(manifest, str(path), vocab,
                                                  known_orgs)]
        if problems:
            detail = "; ".join(f"{p.path}: {p.problem}" for p in problems)
            raise ValueError(
                f"{len(problems)} source manifest(s) failed activation "
                f"gates: {detail}")

        revision = max((m.lifecycle.last_verified for m in manifests),
                       default="unknown")
        return cls(manifests, vocab, revision)

    # Runtime health overlay (never a manifest field).
    def set_operational(self, source_id: str, state: OperationalState) -> None:
        self._operational[source_id] = state

    def operational(self, source_id: str) -> OperationalState:
        return self._operational.get(source_id, OperationalState.unknown)

    def get(self, source_id: str) -> SourceManifest | None:
        return self.manifests.get(source_id)

    # --- the one gate -----------------------------------------------------
    #
    # Whether a source may be queried is decided here and nowhere else. Both
    # routes into a source — capability selection and a tool naming its
    # source directly — go through `selection_block`, so a manifest that is
    # not active, not automatable, or known to be down is refused by both.
    # Before this existed the fixed-source tools checked only that the
    # manifest was present, and a source flipped to `proposed` was still
    # queried by every tool that named it.

    def selection_block(self, m: SourceManifest) -> str | None:
        """None when the source may be queried; otherwise the reason it may
        not, in the vocabulary `coverage.sources_unavailable` uses."""
        if self.operational(m.id) == OperationalState.unavailable:
            return "source_unavailable"
        if m.access.automation_status == AutomationStatus.outreach_pending:
            return "outreach_pending"
        if not m.is_active() or m.access.automation_status not in ACTIVATABLE:
            return "source_not_activated"
        return None

    def selectable(self, m: SourceManifest) -> bool:
        return self.selection_block(m) is None

    def require_active(self, source_id: str) -> SourceManifest:
        """The manifest for a tool that names its source, or a typed error
        saying why it cannot be queried. The error is written for the model:
        a proposed source is a recorded intention with a reason attached,
        and saying so beats an empty result."""
        m = self.manifests.get(source_id)
        if m is None:
            raise SourceUnavailable(
                f"source {source_id!r} is missing from the registry; the "
                "install is incomplete.")
        block = self.selection_block(m)
        if block is None:
            return m
        if block == "source_unavailable":
            raise SourceUnavailable(
                f"{m.name} ({source_id}) is marked unavailable by this "
                "session's health probes. Outage, not an empty result; try "
                "again later.")
        if block == "outreach_pending":
            raise TermsRestricted(
                f"{m.name} ({source_id}) has an anti-automation posture and "
                "is described but never queried. The next step is a "
                "conversation with the publisher, not a request; "
                "registry.describe_source has the detail.")
        raise SourceNotActivated(
            f"{m.name} ({source_id}) is registered but "
            f"{m.lifecycle.declared_state.value}, not active"
            + (f": {m.lifecycle.blocked_reason}" if m.lifecycle.blocked_reason
               else "")
            + " It cannot be queried. This is a gap in DOE-MCP, not evidence "
              "about the data; registry.describe_source explains what stands "
              "in the way.")

    def covers_capability_anywhere(self, capability: str) -> bool:
        if capability not in self.capability_vocab:
            raise InvalidQuery(
                f"capability {capability!r} is not in the vocabulary; "
                f"known: {sorted(self.capability_vocab)}")
        return any(capability in m.capability_ids()
                   for m in self.manifests.values())

    def for_lab(self, org_id: str) -> list[SourceManifest]:
        """The crosswalk query. Every source carrying a laboratory's data,
        whatever domain server serves it and whatever platform hosts it —
        which is the point, since four labs' flagship data lives on shared
        cross-lab platforms."""
        return sorted((m for m in self.manifests.values()
                       if org_id in m.labs or m.publisher.steward == org_id),
                      key=lambda m: m.id)

    def select(self, capability: str) -> list[SourceManifest]:
        """Selectable sources for a capability, ordered by authority.

        No central ranking and no derived primary: ordering answers "which
        ones first", and callers query every returned source and surface
        every result.
        """
        if capability not in self.capability_vocab:
            raise InvalidQuery(f"capability {capability!r} is not in the "
                               "vocabulary")
        authority_order = {AuthorityLevel.primary: 0,
                           AuthorityLevel.official_secondary: 1,
                           AuthorityLevel.official_derived: 2,
                           AuthorityLevel.third_party: 3,
                           AuthorityLevel.unverified: 4}
        candidates = [m for m in self.manifests.values()
                      if capability in m.capability_ids() and self.selectable(m)]
        candidates.sort(key=lambda m: (
            authority_order[m.publisher.authority_level],
            m.freshness.ttl_hint_seconds, m.id))
        return candidates

    def unavailable_for(self, capability: str) -> list[tuple[str, str]]:
        """(source_id, reason) pairs explaining why a registered source did
        not serve this capability — the explanation behind
        coverage.sources_unavailable."""
        out: list[tuple[str, str]] = []
        for m in self.manifests.values():
            if capability not in m.capability_ids():
                continue
            block = self.selection_block(m)
            if block is not None:
                out.append((m.id, block))
        return sorted(out)

    def proposed_for_capability(self, capability: str) -> list[SourceManifest]:
        """Sources that WOULD serve a capability but are not active, read
        from the typed `planned_capabilities` field. This is what lets a
        coverage gap answer "we know about it, here is why it is not wired
        up" rather than an unqualified no. It used to be a substring search
        over `authority_notes`, which matched prose that happened to mention
        a capability and missed prose that described one in other words.
        """
        if capability not in self.capability_vocab:
            raise InvalidQuery(f"capability {capability!r} is not in the "
                               "vocabulary")
        return sorted((m for m in self.manifests.values()
                       if capability in m.planned_capabilities),
                      key=lambda m: m.id)


class OrganizationTableLike:
    """Structural type for the loader's organizations argument."""

    def ids(self) -> set[str]:  # pragma: no cover - protocol shim
        raise NotImplementedError
