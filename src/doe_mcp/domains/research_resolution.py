"""One-hop DOI discovery over a fixed, request-bounded publisher frontier."""
from __future__ import annotations

from urllib.parse import quote

from ..adapters.base import RequestBudget, RequestBudgetExceeded, request_budget
from ..adapters.osti_family import normalized_doi
from ..core.assemble import failure, pagination_coverage, result_dim
from ..core.envelope import (Coverage, Envelope, ExecutionCoverage, PaginationCoverage,
                            RegistryCoverage, SourceClaimCoverage)
from ..core.errors import DoeMcpError, InvalidQuery, SourceSchemaChanged
from ..runtime import RuntimeContext
from ._common import add_manifest_source, builder, select_fixed

# No caller-supplied source IDs, URLs, or egress destinations.
COLLECTIONS = ('osti-gov-records', 'osti-doe-pages', 'osti-data-explorer', 'osti-doe-code')


async def resolve_doi(ctx: RuntimeContext, doi: str, request_limit: int = 4, rows: int = 5,
                      page_budget: int = 1) -> Envelope:
    """Find exact returned DOI candidates; never follow their relationships."""
    from .research import _add_records

    target = normalized_doi(doi)
    if (target is None or any(type(value) is not int for value in (request_limit, rows, page_budget))
            or not 1 <= request_limit <= 24 or not 1 <= rows <= 10 or not 1 <= page_budget <= 3):
        raise InvalidQuery('give a DOI, request_limit 1–24, rows 1–10, and page_budget 1–3')
    b = builder(ctx, 'research.resolve_doi')
    selected, skipped = select_fixed(ctx, COLLECTIONS)
    budget = RequestBudget(request_limit)
    token = request_budget.set(budget)
    scopes, records, failures, candidates = [], [], [], {}
    attempted = []
    try:
        for manifest in selected:
            scope = {'source_id': manifest.id, 'pages': [], 'status': 'budget_limited', 'requests_used': 0}
            scopes.append(scope)
            totals, seen = set(), set()
            for page in range(1, page_budget + 1):
                # Bound logical work too: cache hits cannot expand the frontier.
                if budget.used >= budget.limit:
                    break
                filters = {'all_fields' if manifest.id == 'osti-doe-code' else 'doi': target}
                before = budget.used
                if manifest.id not in attempted:
                    attempted.append(manifest.id)
                try:
                    fetched = await ctx.osti.search(manifest, filters=filters, rows=rows, page=page)
                    if len(fetched.value.records) > rows:
                        raise SourceSchemaChanged('publisher exceeded the requested bounded row count')
                    if any(not record.record_id for record in fetched.value.records):
                        raise SourceSchemaChanged('research record has no source-scoped identifier')
                except RequestBudgetExceeded:
                    scope['requests_used'] += budget.used - before
                    break
                except DoeMcpError as err:
                    scope['requests_used'] += budget.used - before
                    scope['status'] = 'failed'
                    failures.append(failure(manifest.id, err.code, str(err)))
                    break  # Never repeatedly retry a failed publisher search.
                scope['requests_used'] += budget.used - before
                result = fetched.value
                ref = add_manifest_source(b, ctx, manifest, retrieved_at=fetched.retrieved_at,
                    cache_age_seconds=fetched.cache_age_seconds, from_cache=fetched.from_cache)
                out = _add_records(b, ctx, manifest, result, ref, retrieved_at=fetched.retrieved_at, detailed=True)
                start = len(records)
                records.extend(out)
                duplicates = False
                for index, row in enumerate(out, start):
                    node = manifest.id + ':' + quote(row['id'], safe='')
                    duplicates |= node in seen
                    seen.add(node)
                    if normalized_doi(row.get('doi')) == target:
                        candidates.setdefault(node, {'node_id': node, 'source_id': manifest.id,
                            'record_id': row['id'], 'record_indices': []})['record_indices'].append(index)
                totals.add(result.total_matches)
                pagination = pagination_coverage(result.total_matches, len(out), offset=(page - 1) * rows)
                scope['pages'].append({'page': page, 'rows': rows, 'total_matches': result.total_matches,
                    'returned': len(out), 'pagination': pagination.value,
                    'requests_used': budget.used - before, 'repeated_records': duplicates})
                # Unknown totals and inconsistent/repeated pages never establish completeness.
                consistent = (len(totals) == 1 and None not in totals
                              and not any(p['repeated_records'] for p in scope['pages'])
                              and result.total_matches >= len(seen)
                              and sum(p['returned'] for p in scope['pages']) == result.total_matches)
                if pagination is PaginationCoverage.complete and consistent:
                    scope['status'] = 'complete'
                    break
                scope['status'] = 'unresolved' if pagination is PaginationCoverage.unknown else 'budget_limited'
    finally:
        request_budget.reset(token)
    complete = len(scopes) == len(COLLECTIONS) and not skipped and all(s['status'] == 'complete' for s in scopes)
    if len(candidates) > 1:
        status = 'ambiguous'
    elif complete:
        status = 'resolved' if candidates else 'unresolved'
    elif failures:
        status = 'failed'
    elif any(s['status'] == 'budget_limited' for s in scopes):
        status = 'budget_limited'
    else:
        status = 'unresolved'
    data = {'target_namespace': 'doi', 'target_identifier': target, 'status': status,
        'candidates': sorted(candidates.values(), key=lambda row: row['node_id']), 'records': records,
        'search_scope': scopes, 'search_complete': complete, 'request_limit': request_limit,
        'requests_used': budget.used,
        'request_accounting': 'HTTP attempts including retries and redirects; replay counts recorded fetches; cache hits use zero requests',
        'note': 'Only exact normalized returned DOIs are candidates. Multiple source-scoped records are ambiguous. Empty or unresolved is limited to these catalogs; no target URLs or relationships are fetched.'}
    return b.build(data, Coverage(registry=RegistryCoverage.covered if not skipped else RegistryCoverage.partial,
        execution=ExecutionCoverage.partial if failures or skipped else ExecutionCoverage.complete,
        pagination=PaginationCoverage.complete if complete else PaginationCoverage.unknown,
        source_claim=SourceClaimCoverage.partial, result=result_dim(len(candidates)),
        sources_searched=attempted, sources_unavailable=skipped, source_failures=failures))
