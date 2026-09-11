"""Shared helpers for domain modules: the activation gate for tools that name
their source, envelope construction from manifests, and the source-entry
conversion that keeps the publisher triple honest."""
from __future__ import annotations

from collections.abc import Iterable

from ..core.assemble import EnvelopeBuilder, gap
from ..core.envelope import (AccessPath, Citation, SourceGap, WarningCode,
                             utc_now_iso)
from ..core.errors import SourceUnavailable
from ..core.registry import SourceManifest
from ..runtime import PROJECT_SOURCE_ID, RuntimeContext


def require_active_source(ctx: RuntimeContext,
                          source_id: str) -> SourceManifest:
    """The manifest a fixed-source tool may query, or a typed error.

    Every tool that names its source rather than selecting by capability
    goes through here, and here delegates to the registry's one gate. A
    check that only asked whether the manifest existed let a source flipped
    to `proposed` keep answering through every tool that named it.
    """
    return ctx.sources.require_active(source_id)


def select_fixed(ctx: RuntimeContext, source_ids: Iterable[str]
                 ) -> tuple[list[SourceManifest], list[SourceGap]]:
    """Fixed-source selection for a tool spanning several sources.

    A source that fails the gate becomes a `sources_unavailable` gap rather
    than an error, so a literature search keeps answering from the collection
    that is live and says which one it did not read. A tool with nothing left
    to query raises through `require_active_source` on its first source, so
    the caller gets the reason rather than an empty envelope.
    """
    selected: list[SourceManifest] = []
    gaps: list[SourceGap] = []
    for source_id in source_ids:
        manifest = ctx.sources.get(source_id)
        if manifest is None:
            raise SourceUnavailable(
                f"source {source_id!r} is missing from the registry; the "
                "install is incomplete.")
        block = ctx.sources.selection_block(manifest)
        if block is None:
            selected.append(manifest)
        else:
            gaps.append(gap(source_id, block))
    if not selected:
        for source_id in source_ids:
            require_active_source(ctx, source_id)
    return selected, gaps


def merge_gaps(*groups: list[SourceGap]) -> list[SourceGap]:
    """One gap per source, whichever route found it first."""
    seen: dict[str, SourceGap] = {}
    for group in groups:
        for g in group:
            seen.setdefault(g.source_id, g)
    return [seen[k] for k in sorted(seen)]


def builder(ctx: RuntimeContext, tool: str,
            contract_version: str = "1") -> EnvelopeBuilder:
    return EnvelopeBuilder(server=ctx.server_name,
                           server_version=ctx.server_version, tool=tool,
                           contract_version=contract_version,
                           registry_revision=ctx.sources.revision,
                           adapters=ctx.adapters)


def org_name(ctx: RuntimeContext, org_id: str | None) -> str | None:
    """Resolve an organization id to its display name.

    Falls back to the raw id rather than to None: a provenance entry reading
    'ornl' is worse than one reading 'Oak Ridge National Laboratory' and much
    better than one with an empty steward.
    """
    if not org_id:
        return None
    org = ctx.organizations.get(org_id)
    return org.name if org else org_id


def add_manifest_source(b: EnvelopeBuilder, ctx: RuntimeContext,
                        manifest: SourceManifest, *, retrieved_at: str,
                        cache_age_seconds: int, from_cache: bool = False,
                        dataset: str | None = None,
                        dataset_version: str | None = None,
                        doi: str | None = None) -> str:
    """One source entry, built from the manifest rather than by hand.

    Building it from the manifest is what keeps the disclosure rules
    automatic: the funder appears whenever the manifest has one, the terms
    gap becomes a warning, and a citation requirement becomes a
    citation_required warning carrying the string. A tool that assembled its
    own provenance would be one refactor away from dropping any of those.
    """
    citation = None
    if manifest.access.citation_required or doi:
        citation = Citation(doi=doi,
                            recommended=manifest.access.citation_text,
                            required=manifest.access.citation_required)
    ref = b.add_source(
        source_id=manifest.id,
        steward=org_name(ctx, manifest.publisher.steward) or manifest.id,
        funder=org_name(ctx, manifest.publisher.funder),
        host=org_name(ctx, manifest.publisher.host),
        system=manifest.name,
        dataset=dataset or manifest.name,
        dataset_version=dataset_version or manifest.coverage.dataset_version,
        authority_level=manifest.publisher.authority_level,
        access_path=AccessPath.cache if from_cache else AccessPath.live,
        source_updated_at=None,
        retrieved_at=retrieved_at,
        cache_age_seconds=cache_age_seconds,
        citation=citation,
        # The OSTI family and its neighbours expose no layer-level update
        # date, and saying so on every answer would be noise: the freshness
        # facts that matter here are per record (`entry_date`) and are
        # surfaced there instead.
        warn_on_missing_freshness=False,
        terms_gap=manifest.access.terms_gap)
    if manifest.access.credential_ref and ctx.credentials.is_demo(
            manifest.access.credential_ref):
        b.warn(WarningCode.rate_limited_demo,
               "This answer used a shared demo key. The data is real; the "
               "rate budget is not yours and will run out under any real "
               "workload. Run `doe-mcp configure credentials` to register "
               "your own.", manifest.id)
    return ref


def add_project_source(b: EnvelopeBuilder, ctx: RuntimeContext) -> str:
    """Provenance for an answer that came from this project's own registry
    rather than from a publisher's system.

    Built from the registered manifest like any other source, so it carries
    the same `unverified` authority level a caller would see by looking it
    up. A caller reading "what does Oak Ridge publish" needs to know the
    answer is DOE-MCP's inventory of ORNL, not ORNL's own statement about
    itself, and the authority level is where that is said.
    """
    manifest = require_active_source(ctx, PROJECT_SOURCE_ID)
    return add_manifest_source(b, ctx, manifest, retrieved_at=utc_now_iso(),
                               cache_age_seconds=0,
                               dataset_version=ctx.sources.revision)
