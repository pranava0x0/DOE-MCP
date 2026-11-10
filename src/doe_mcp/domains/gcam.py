"""Bounded lookups over the separately published tables of a GCAM release."""
from __future__ import annotations

from ..core.assemble import pagination_coverage, result_dim, selection_coverage
from ..core.envelope import Coverage, Envelope, ExecutionCoverage, SourceClaimCoverage
from ..core.errors import InvalidQuery
from ..core.toolreg import ToolRegistry, ToolSpec
from ..gcam_catalog import TABLES
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, require_active_source


def _lookup(domain: str, name: str):
    tool = domain + "." + name

    async def lookup(ctx: RuntimeContext, filters: dict[str, str] | None = None,
                     rows: int = 10, offset: int = 0) -> Envelope:
        manifest = require_active_source(ctx, "gcam-" + domain)
        if type(rows) is not int or type(offset) is not int or not 1 <= rows <= 50 or offset < 0:
            raise InvalidQuery("rows must be 1 to 50 and offset must be nonnegative.")
        spec = ctx.gcam.params_for(manifest).tables[name]
        if filters is not None and (not isinstance(filters, dict)
                                    or not all(isinstance(v, str) for v in filters.values())):
            raise InvalidQuery("filters must map column names to exact text values.")
        filters = filters or {}
        if not filters.keys() <= set(spec.columns):
            raise InvalidQuery("Use the publisher's column names: " + ", ".join(spec.columns))
        fetched = await ctx.gcam.read(manifest, name)
        # Keep the publisher's decimal spellings, leading zeros and blanks.
        # Row numbers are one-based data records, excluding comments/header.
        matches = [(i, row) for i, row in enumerate(fetched.value.rows, 1)
                   if all(row[k] == v for k, v in filters.items())]
        page = matches[offset:offset + rows]
        b = builder(ctx, tool)
        ref = add_manifest_source(b, ctx, manifest, retrieved_at=fetched.retrieved_at,
                                  from_cache=fetched.from_cache, cache_age_seconds=fetched.cache_age_seconds,
                                  dataset=spec.url.rsplit("/", 1)[1])
        records = []
        for number, row in page:
            evidence = b.add_evidence(source_ref=ref, record_id=f"data row {number}",
                                      locator=fetched.request_url, retrieved_at=fetched.retrieved_at,
                                      transformations=["parse_csv", "empty_cell_to_null"])
            records.append({"row_number": number, "values": row, "evidence_ref": evidence})
        registry, gaps = selection_coverage(ctx.sources, "model." + domain + "_inputs", [manifest])
        return b.build({"table": name, "columns": spec.columns, "records": records,
                        "file_url": fetched.request_url, "source_notes": fetched.value.notes, "filters": filters, "offset": offset,
                        "record_count": len(page), "total_matches": len(matches),
                        "table_rows": len(fetched.value.rows), "release": manifest.coverage.dataset_version},
                       Coverage(registry=registry, execution=ExecutionCoverage.complete,
                                pagination=pagination_coverage(len(matches), len(page), offset=offset),
                                source_claim=SourceClaimCoverage.complete, result=result_dim(len(page)),
                                sources_searched=[manifest.id], sources_unavailable=gaps,
                                known_limitations=manifest.coverage.known_limitations))
    return lookup


def _registry(domain: str, tables: dict[str, str]) -> ToolRegistry:
    reg = ToolRegistry(package=domain)
    for name, description in tables.items():
        reg.register(ToolSpec(name=domain + "." + name, toolset="default", contract_version="1",
                              description=description + " in GCAM. Omit filters to see columns; "
                              "match column names and text values exactly. Blank cells return null.",
                              fn=_lookup(domain, name)))
    return reg


GCAM_TOOLS = {domain: _registry(domain, tables) for domain, tables in TABLES.items()}
