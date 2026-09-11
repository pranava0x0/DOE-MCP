"""The provenance/evidence envelope every DOE-MCP tool returns.

Contract: design/architecture.md Part 1 § 3.3 and decision 0006. This module
is the wire truth; the JSON Schema committed at schemas/envelope.schema.json
is generated from these models and a test keeps the two identical.

What DOE adds to the base shape, and why:

- A **citation block** on every source entry. DOE datasets carry DataCite
  DOIs and several publishers (ARM, AmeriFlux) require citation as a
  condition of use. A provenance entry that names a publisher but drops the
  DOI makes the answer un-citable, which for scientific data is the same as
  making it unusable.
- **`dataset_version`** as a required provenance field. ATB 2024 and ATB
  2026 are different data with the same name; CMIP6 is not CMIP5; LandScan
  2023 is not LandScan 2022. Vintage is not metadata here, it is identity.
- **AccessRecipe** objects. Bulk science data travels as pointers
  (decision 0010): a 4.8 PB S3 lake is answered with a URI and the recipe
  for reading it, never inlined and never pretended away.
- Three added warning codes — `derived_layer`, `catalog_vintage`,
  `domain_migrated` — each naming a confusion this ecosystem actually
  produces (architecture Part 1 § 3.2, principles 1, 3, and 4).

Serialization rules (inherited):
- absent-means-false / absent-means-none fields are dropped when empty
  (`requires_user_choice`, `next_actions`, `resources`);
- `warnings` is always present, often [];
- execution provenance rides under the reserved `_execution` key.
"""
from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer

ENVELOPE_VERSION = "1"

# Soft budget; the contract test enforces it over fixtures and prints the
# measured sizes so it stays falsifiable.
DATA_TOKEN_BUDGET = 2000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- the five independent coverage dimensions ------------------------------

class RegistryCoverage(str, enum.Enum):
    """Does DOE-MCP have a source for this question at all?

    The distinction this dimension exists to protect: `none` means the
    registry has nothing, so the data may well exist and we cannot see it.
    An empty result under `covered` means the searched systems hold no
    record. Collapsing the two is the single most damaging thing a
    government-data tool can do to a caller.
    """

    covered = "covered"
    partial = "partial"
    none = "none"
    unknown = "unknown"


class ExecutionCoverage(str, enum.Enum):
    complete = "complete"
    partial = "partial"
    failed = "failed"


class PaginationCoverage(str, enum.Enum):
    complete = "complete"
    truncated = "truncated"
    unknown = "unknown"


class SourceClaimCoverage(str, enum.Enum):
    """What the publisher itself claims to cover, which is often less than
    the caller assumes: DOE PAGES holds public-access literature after an
    embargo interval, not everything DOE funded."""

    complete = "complete"
    partial = "partial"
    unknown = "unknown"


class ResultCoverage(str, enum.Enum):
    hit = "hit"
    empty = "empty"


class AuthorityLevel(str, enum.Enum):
    primary = "primary"
    official_secondary = "official_secondary"
    official_derived = "official_derived"
    third_party = "third_party"
    unverified = "unverified"


class AccessPath(str, enum.Enum):
    live = "live"
    cache = "cache"
    index = "index"


class RawRecovery(str, enum.Enum):
    available = "available"
    forbidden_by_terms = "forbidden_by_terms"
    expired = "expired"


class RecipeKind(str, enum.Enum):
    """How to actually get at data this answer only points to
    (architecture Part 1 § 3.3, decision 0010)."""

    landing_page = "landing_page"
    doi = "doi"
    fulltext_pdf = "fulltext_pdf"
    s3_uri = "s3_uri"
    opendap_url = "opendap_url"
    wcs_getcoverage = "wcs_getcoverage"
    globus_endpoint = "globus_endpoint"
    hsds_domain = "hsds_domain"
    bulk_download = "bulk_download"
    repository = "repository"
    contact_required = "contact_required"


class WarningCode(str, enum.Enum):
    """Adding a value is a reviewed change.

    The first block is the base contract's; the second is DOE's, each one
    named for a confusion this ecosystem measurably produces.
    """

    # base contract
    screening_only = "screening_only"
    stale_source = "stale_source"
    freshness_unavailable = "freshness_unavailable"
    alias_match = "alias_match"
    mixed_vintages = "mixed_vintages"
    terms_note = "terms_note"
    insecure_transport = "insecure_transport"
    truncated_inline = "truncated_inline"

    # DOE additions (Part 1 § 3.3)
    derived_layer = "derived_layer"
    """This is the public derived product, not raw facility data. Fires when
    a caller's question reaches for beamtime/shot/run data that is
    proposal-gated by design (research/facilities.md)."""

    catalog_vintage = "catalog_vintage"
    """Answered from a harvested index rather than live, with the harvest
    date. data.json has been measured carrying an ARPA-E entry from 2022
    next to a live 1,721-record API."""

    domain_migrated = "domain_migrated"
    """The source moved hosts or names and an alias resolved it. NREL to NLR
    killed every *.nrel.gov domain with no redirects; nine further host
    moves were found in one 2026-09-01 sweep."""

    citation_required = "citation_required"
    """The publisher requires citation as a condition of use. The string to
    use rides in the source entry's citation block."""

    rate_limited_demo = "rate_limited_demo"
    """Running on a shared demo key (decision 0012). Answers are real; the
    budget is not yours and will run out under any real workload."""


class SourceGap(_Strict):
    """Why a registered source contributed nothing to this answer."""

    source_id: str
    reason: str


class TimeRange(_Strict):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceFailure(_Strict):
    source_id: str
    error: str
    detail: str


class Coverage(_Strict):
    """Five independent dimensions; never collapse them into one status."""

    registry: RegistryCoverage
    execution: ExecutionCoverage
    pagination: PaginationCoverage = PaginationCoverage.unknown
    source_claim: SourceClaimCoverage = SourceClaimCoverage.unknown
    result: ResultCoverage
    sources_searched: list[str] = Field(default_factory=list)
    sources_unavailable: list[SourceGap] = Field(default_factory=list)
    time_range: TimeRange | None = None
    source_failures: list[SourceFailure] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)


class Citation(_Strict):
    """How to cite what this source entry contributed (architecture Part 1 § 3.3).

    `required` is the publisher's own condition of use, not a courtesy: ARM
    and AmeriFlux both state one. When it is true the builder raises a
    citation_required warning so the obligation reaches the caller rather
    than sitting in a manifest.
    """

    doi: str | None = None
    recommended: str | None = None
    required: bool = False


class SourceEntry(_Strict):
    id: str
    source_id: str
    steward: str
    """The organization that runs the system day to day."""
    funder: str | None = None
    """Who pays for the program. Often not the steward: ORNL DAAC is
    NASA-funded, FAF is DOT's, LandScan is co-funded by NGA. Displaying it
    is what makes the operator scope rule (decision 0001-C) honest instead
    of an overclaim."""
    host: str | None = None
    """Whose infrastructure serves the bytes, when it differs from the
    steward — developer.nlr.gov rides on GSA's api.data.gov."""
    system: str
    dataset: str
    dataset_version: str
    """Required. 'current' is a legitimate value for a live API; a blank is
    not, because ATB 2024 and ATB 2026 are different data."""
    authority_level: AuthorityLevel
    access_path: AccessPath
    source_updated_at: str | None
    retrieved_at: str
    cache_age_seconds: int
    citation: Citation | None = None


class Evidence(_Strict):
    id: str
    source_ref: str
    record_id: str
    # A wrong link is worse than no link: locator is emitted only when the
    # platform actually produces one, never derived by guesswork.
    locator: str | None = None
    retrieved_at: str
    effective_at: str | None = None
    transformations: list[str] = Field(default_factory=list)
    payload_hash: str | None = None
    raw_recovery: RawRecovery = RawRecovery.available


class AccessRecipe(_Strict):
    """A typed instruction for reaching data this answer only points at.

    Every field but `kind` and `uri` is optional because the recipes differ:
    an S3 prefix needs a region and a note about anonymous access, a DOI
    needs nothing, a Globus endpoint needs a collection id.
    """

    kind: RecipeKind
    uri: str
    label: str | None = None
    instructions: str | None = None
    requires_account: bool = False
    approximate_size: str | None = None


class WarningNote(_Strict):
    code: WarningCode
    message: str
    source_id: str | None = None


class NextAction(_Strict):
    finding: str
    suggested_capability: str
    reason: str


class ResourceRef(_Strict):
    uri: str
    media_type: str | None = None
    description: str | None = None


class ExecutionProvenance(_Strict):
    server: str
    server_version: str
    tool: str
    tool_contract_version: str
    envelope_version: str = ENVELOPE_VERSION
    adapters: dict[str, str] = Field(default_factory=dict)
    registry_revision: str
    request_id: str


class Envelope(_Strict):
    data: dict[str, Any]
    provenance: list[SourceEntry] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    coverage: Coverage
    warnings: list[WarningNote] = Field(default_factory=list)
    access_recipes: list[AccessRecipe] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    resources: list[ResourceRef] = Field(default_factory=list)
    requires_user_choice: bool = False
    execution: ExecutionProvenance | None = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """The schema must describe the WIRE shape the serializer emits
        (clients validate structured content against it, strictly): the
        `execution` field rides as `_execution` on the wire."""
        schema = handler(core_schema)
        props = schema.get("properties", {})
        if "execution" in props:
            props["_execution"] = props.pop("execution")
        return schema

    @model_serializer(mode="wrap")
    def _wire(self, handler: Any) -> dict[str, Any]:
        out: dict[str, Any] = handler(self)
        execution = out.pop("execution", None)
        if execution is not None:
            out["_execution"] = execution
        if not out.get("requires_user_choice"):
            out.pop("requires_user_choice", None)
        for optional_list in ("next_actions", "resources", "access_recipes"):
            if not out.get(optional_list):
                out.pop(optional_list, None)
        # Coverage optionals drop when empty; the five dimensions always stay.
        cov = out.get("coverage")
        if isinstance(cov, dict):
            for k in ("time_range", "sources_unavailable", "source_failures",
                      "known_limitations", "sources_searched"):
                if not cov.get(k):
                    cov.pop(k, None)
        # Provenance optionals: a null funder is noise on the ~85% of sources
        # whose steward and funder are the same organization.
        for entry in out.get("provenance", []):
            if isinstance(entry, dict):
                for k in ("funder", "host", "citation"):
                    if entry.get(k) is None:
                        entry.pop(k, None)
        return out

    def data_token_estimate(self) -> int:
        """Rough tokens for `data` (~4 chars/token). An estimate for budget
        tests, not an exact count; the test prints it so it stays
        falsifiable."""
        return len(json.dumps(self.data, separators=(",", ":"))) // 4

    @classmethod
    def wire_schema(cls) -> dict[str, Any]:
        """The published wire schema. The serializer's absent-when-empty
        fields are optional here by construction."""
        schema = cls.model_json_schema()
        schema["title"] = "DoeMcpEnvelope"
        schema["$comment"] = (
            f"envelope_version {ENVELOPE_VERSION}; generated from "
            "doe_mcp.core.envelope — edit the models, not this file.")
        return schema


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
