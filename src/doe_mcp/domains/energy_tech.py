"""Pinned ATB assumptions and an explicitly gated monthly PVWatts contract."""
from __future__ import annotations

from ..atb_releases import FILTER_COLUMNS, release_for
from ..core.assemble import pagination_coverage, result_dim, selection_coverage
from ..core.envelope import Coverage, Envelope, ExecutionCoverage, SourceClaimCoverage
from ..core.toolreg import ToolRegistry, ToolSpec
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source

ENERGY_TECH_TOOLS = ToolRegistry(package="energy_tech")


async def _atb(ctx, tool, edition, filters, rows, offset, dimension=None):
    manifest = require_active_source(ctx, "nlr-atb")
    spec = release_for(edition)
    fetched = await ctx.atb.scan(manifest, edition, filters, rows=rows, offset=offset, dimension=dimension)
    b = builder(ctx, tool)
    version = f"ATB {edition} {spec.release}"
    ref = add_manifest_source(b, ctx, manifest, retrieved_at=fetched.retrieved_at,
        from_cache=fetched.from_cache, cache_age_seconds=fetched.cache_age_seconds,
        dataset="ATB electricity", dataset_version=version)
    data = {"edition": edition, "release": spec.release, "file_url": spec.url,
        "asset_sha256": spec.sha256, "asset_bytes": spec.bytes, "table_rows": fetched.value["table_rows"],
        "currency_basis": spec.currency_basis, "currency_basis_source": spec.currency_source,
        "filters": filters or {}, "offset": offset, "total_matches": fetched.value["total_matches"],
        "columns": fetched.value["columns"], "filter_columns": sorted(FILTER_COLUMNS),
        "interpretation": "Published scenario assumptions and projections, not observations, bids or a project-specific cost estimate."}
    if dimension is not None:
        evidence = b.add_evidence(source_ref=ref, record_id=f"distinct {dimension}", locator=spec.url,
            retrieved_at=fetched.retrieved_at, payload_hash=spec.sha256,
            transformations=["verify_sha256", "parse_parquet", "exact_filter", "distinct_dimension"])
        data.update(dimension=dimension, values=fetched.value["records"], evidence_ref=evidence,
                    returned=len(fetched.value["records"]))
    else:
        records = []
        for row in fetched.value["records"]:
            evidence = b.add_evidence(source_ref=ref, record_id=f"{version}:data row {row['row_number']}",
                locator=spec.url, retrieved_at=fetched.retrieved_at, payload_hash=spec.sha256,
                transformations=["verify_sha256", "parse_parquet", "exact_filter"])
            records.append({**row, "evidence_ref": evidence})
        data.update(records=records, returned=len(records),
            selection_status="no_matching_rows" if not data["total_matches"] else
                "single_matching_row" if data["total_matches"] == 1 else "multiple_matching_rows",
            selection_note="All qualifying rows remain distinct; no ambiguous first row is chosen. Refine technology detail, scenario, year, financial case, tax credit and CRP qualifiers as needed.")
    registry, gaps = selection_coverage(ctx.sources, "tech.cost_assumptions", [manifest])
    return b.build(data, Coverage(registry=registry, execution=ExecutionCoverage.complete,
        pagination=pagination_coverage(data["total_matches"], data["returned"], offset=offset),
        source_claim=SourceClaimCoverage.complete, result=result_dim(data["returned"]),
        sources_searched=[manifest.id], sources_unavailable=gaps, known_limitations=manifest.coverage.known_limitations))


async def describe_atb(ctx: RuntimeContext, edition: int, dimension: str = "technology",
                       filters: dict[str, str | None] | None = None, rows: int = 50, offset: int = 0) -> Envelope:
    return await _atb(ctx, "tech.describe_atb", edition, filters, rows, offset, dimension)


async def read_atb(ctx: RuntimeContext, edition: int, filters: dict[str, str | None],
                   rows: int = 10, offset: int = 0) -> Envelope:
    return await _atb(ctx, "tech.read_atb", edition, filters, rows, offset)


async def estimate_production(ctx: RuntimeContext, inputs: dict) -> Envelope:
    # This gate executes before the adapter can inspect a credential store.
    manifest = require_active_source(ctx, "nlr-pvwatts")
    fetched = await ctx.pvwatts.model(manifest, inputs)
    b = builder(ctx, "solar.estimate_production")
    ref = add_manifest_source(b, ctx, manifest, retrieved_at=fetched.retrieved_at,
        from_cache=fetched.from_cache, cache_age_seconds=fetched.cache_age_seconds,
        dataset_version="PVWatts " + fetched.value["version"])
    evidence = b.add_evidence(source_ref=ref, record_id="monthly model result", locator=fetched.request_url,
        retrieved_at=fetched.retrieved_at, transformations=["validate_input_echo", "validate_monthly_output", "remove_credential_echo"])
    registry, gaps = selection_coverage(ctx.sources, "solar.estimate_production", [manifest])
    return b.build({**fetched.value, "evidence_ref": evidence}, Coverage(registry=registry,
        execution=ExecutionCoverage.complete, source_claim=SourceClaimCoverage.complete,
        result=result_dim(1), sources_searched=[manifest.id], sources_unavailable=gaps,
        known_limitations=manifest.coverage.known_limitations))


for name, fn, description in [
    ("tech.describe_atb", describe_atb, "Inspect exact qualifier values in a pinned ATB electricity edition (2024 or 2025). Returns bounded distinct values, units/currency notes and the release SHA256. No latest alias."),
    ("tech.read_atb", read_atb, "Read bounded ATB scenario-assumption rows from an explicit 2024 or 2025 release. Exact publisher-column text filters or null; preserves technology/resource detail, year, metric, units, financial case, tax-credit case and CRP. Multiple matching rows remain explicit."),
    ("solar.estimate_production", estimate_production, "Prepared PVWatts v8 monthly model contract. Currently refuses: source proposed, user key and real recording absent. No model result or fallback is fabricated. Future activation requires its checklist; modelled production is not observed generation."),
]:
    ENERGY_TECH_TOOLS.register(ToolSpec(name=name, fn=fn, description=description, toolset="default", contract_version="1"))
