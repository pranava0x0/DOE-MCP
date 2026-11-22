"""Real recorded one-hop demo and isolated adverse mutations, never fabricated fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from doe_mcp.adapters.base import HttpFetcher, RequestBudget, RequestBudgetExceeded, request_budget
from doe_mcp.applications.bundle import Run
from doe_mcp.applications.research_linkage import build, follow_assertions
from doe_mcp.core.egress import EgressPolicy
from doe_mcp.core.errors import InvalidQuery, SourceUnavailable
from doe_mcp.core.registry import DeclaredState
from doe_mcp.domains.research_resolution import COLLECTIONS, resolve_doi

TARGET = '10.13139/ornlnccs/1975203'
ROOT = Path(__file__).resolve().parents[1]


def pointer(document, path):
    for part in path.strip('/').split('/'):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


async def test_real_anonymous_frontier_retains_origin_and_each_candidate_evidence(ctx, replay):
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, dataset_query='EAGLE-I', resolve_relationships=True)
    resolution = run.report['relationship_resolution']
    assert resolution['one_hop'] and resolution['target_count'] == 1
    assert resolution['requests_used'] == 4
    edge, = run.report['explicit_relationships']
    followup = edge['followup']
    assert edge['relationship'] == 'IsPartOf'
    assert followup['status'] == 'ambiguous' and followup['search_complete']
    assert {c['node_id'] for c in followup['candidates']} == {'osti-gov-records:1975203', 'osti-data-explorer:1975203'}
    for ref in edge['evidence']:
        assert pointer(run.report, ref['assertion_pointer'])['publisher_value'] == ref['publisher_value']
    for candidate in followup['candidates']:
        assert candidate['evidence']
        for ref in candidate['evidence']:
            row = pointer(run.report, ref['record_pointer'])
            assert row['id'] == candidate['record_id']
            assert row['source_id'] == candidate['source_id']
            assert pointer(run.report, ref['evidence_pointer'])['id'] == ref['evidence_id']
    assert len(run.report['steps']) == 4
    assert all(url.startswith('https://www.osti.gov/') for url, _ in replay.calls)
    recorded = json.loads((ROOT / 'tests/fixtures/research-relationships.json').read_bytes())
    assert len(recorded['interactions']) == 5 and recorded['recorded_at']
    assert 'Recorded live from' in recorded['note']


@pytest.mark.parametrize('mutation,expected', [('one', 'resolved'), ('nonexact', 'unresolved'),
    ('unknown', 'unresolved'), ('truncated', 'budget_limited'), ('failed', 'failed'), ('inactive', 'unresolved')])
async def test_uniqueness_requires_exact_matches_and_complete_known_scope(ctx, monkeypatch, mutation, expected):
    original = ctx.osti.search

    async def changed(manifest, **kwargs):
        if mutation == 'failed' and manifest.id == 'osti-data-explorer':
            raise SourceUnavailable('isolated outage mutation')
        fetched = copy.deepcopy(await original(manifest, **kwargs))
        result = fetched.value
        if manifest.id == 'osti-data-explorer':
            result.records = []
            result.total_matches = 0
        if manifest.id == 'osti-gov-records':
            if mutation == 'nonexact':
                result.records[0].doi = '10.1234/unrelated'
            if mutation == 'unknown':
                result.total_matches = None
            if mutation == 'truncated':
                result.total_matches = 10
        return fetched

    monkeypatch.setattr(ctx.osti, 'search', changed)
    if mutation == 'inactive':
        ctx.sources.get('osti-doe-pages').lifecycle.declared_state = DeclaredState.proposed
    env = await resolve_doi(ctx, TARGET)
    assert env.data['status'] == expected
    if mutation != 'nonexact':
        assert len(env.data['candidates']) == 1
    else:
        assert env.data['candidates'] == []
    if mutation in {'unknown', 'truncated', 'failed', 'inactive'}:
        assert not env.data['search_complete']
    if mutation == 'inactive':
        assert any(g.source_id == 'osti-doe-pages' for g in env.coverage.sources_unavailable)


async def test_partial_envelope_survives_followup_failure_and_inventory_is_retained(ctx, monkeypatch):
    original = ctx.osti.search

    async def failed(manifest, **kwargs):
        if 'doi' in kwargs['filters'] and manifest.id == 'osti-data-explorer':
            raise SourceUnavailable('isolated publisher failure')
        return await original(manifest, **kwargs)

    monkeypatch.setattr(ctx.osti, 'search', failed)
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, dataset_query='EAGLE-I', resolve_relationships=True)
    assert len(run.report['records']) == 10
    envelope = run.report['steps'][-1]['envelope']
    assert envelope['coverage']['execution'] == 'partial'
    assert envelope['coverage']['source_failures']
    assert run.report['explicit_relationships'][0]['followup']['status'] == 'failed'
    assert run.report['explicit_relationships'][0]['followup']['candidates']


async def test_followup_uses_shared_budget_and_only_original_snapshot(ctx, monkeypatch):
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, dataset_query='EAGLE-I')
    linkage = {'explicit_relationships': copy.deepcopy(run.report['explicit_relationships'])}
    # Mutate normalized application input only; no fixture is edited. Repeated
    # target gets one lookup; distinct target is retained after budget ends.
    linkage['explicit_relationships'] += [copy.deepcopy(linkage['explicit_relationships'][0]) for _ in range(2)]
    linkage['explicit_relationships'][-1]['target_identifier'] = '10.9999/z'
    await follow_assertions(run, linkage, request_limit=1)
    assert run.report['relationship_resolution']['requests_used'] == 1
    assert len(run.report['steps']) == 4
    assert [e['followup']['status'] for e in linkage['explicit_relationships']] == ['budget_limited'] * 3
    assert linkage['explicit_relationships'][-1]['followup']['step'] is None
    assert linkage['explicit_relationships'][0]['followup'] == linkage['explicit_relationships'][1]['followup']


async def test_returned_assertions_never_expand_frontier(ctx, monkeypatch):
    original = ctx.osti.search
    calls = []

    async def changed(manifest, **kwargs):
        calls.append((manifest.id, kwargs['filters']))
        fetched = copy.deepcopy(await original(manifest, **kwargs))
        for record in fetched.value.records:
            if 'doi' in kwargs['filters']:
                record.raw['related_identifiers'] = ['CURATED; References; {"relation_type":"References","related_identifier_type":"DOI","related_identifier":"10.9999/never-follow"}']
        return fetched

    monkeypatch.setattr(ctx.osti, 'search', changed)
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, dataset_query='EAGLE-I', resolve_relationships=True)
    assert len(calls) == 8  # four initial catalog calls, four fixed follow-ups
    assert all('never-follow' not in str(filters) for _, filters in calls)
    assert run.report['relationship_resolution']['target_count'] == 1


@pytest.mark.parametrize('kwargs', [{'doi': 'https://example.org/a'}, {'doi': TARGET, 'rows': True},
    {'doi': TARGET, 'page_budget': True}, {'doi': TARGET, 'request_limit': True},
    {'doi': TARGET, 'rows': 11}, {'doi': TARGET, 'page_budget': 4}, {'doi': TARGET, 'request_limit': 25}])
async def test_invalid_resolver_bounds_fail_before_any_request(ctx, replay, kwargs):
    with pytest.raises(InvalidQuery):
        await resolve_doi(ctx, **kwargs)
    assert replay.calls == []


@pytest.mark.parametrize('mode', ['retry', 'redirect'])
async def test_actual_http_budget_covers_every_attempt_and_is_task_local(mode):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503) if mode == 'retry' else httpx.Response(302, headers={'location': 'https://publisher.test/b'})
    policy = EgressPolicy(allowed_hosts=frozenset({'publisher.test'}),
        resolver=lambda host, _: [(0, 0, 0, '', ('93.184.216.34', 0))])
    fetcher = HttpFetcher(policy=policy, transport=httpx.MockTransport(handler))
    budget = RequestBudget(1)
    token = request_budget.set(budget)
    try:
        with pytest.raises(RequestBudgetExceeded):
            await fetcher.fetch_json('https://publisher.test/a', {})
        assert budget.used == len(calls) == 1
    finally:
        request_budget.reset(token)
    assert request_budget.get() is None


async def test_unknown_replay_request_remains_local_failure_without_network(ctx):
    env = await resolve_doi(ctx, '10.9999/no-recording', request_limit=2)
    assert env.data['status'] == 'failed'
    assert env.data['requests_used'] == 2
    assert env.data['candidates'] == [] and not env.data['search_complete']
    assert len(env.coverage.source_failures) == 2
    assert len(env.data['search_scope']) == len(COLLECTIONS)


async def test_publisher_ignoring_row_limit_is_a_failure_not_an_unbounded_result(ctx, monkeypatch):
    original = ctx.osti.search
    async def changed(manifest, **kwargs):
        fetched = copy.deepcopy(await original(manifest, **kwargs))
        if manifest.id == 'osti-gov-records':
            fetched.value.records *= 6  # mutate recorded normalized input only
        return fetched
    monkeypatch.setattr(ctx.osti, 'search', changed)
    env = await resolve_doi(ctx, TARGET)
    assert env.data['status'] == 'failed'
    assert len(env.data['records']) == 1  # valid Data Explorer candidate retained
    assert any(f.source_id == 'osti-gov-records' for f in env.coverage.source_failures)
    assert not env.data['search_complete']


async def test_distinct_target_ceiling_applies_even_when_responses_are_cached(ctx):
    run = Run(ctx, 'research-linkage', 'recorded')
    # Exercise the fixed target ceiling through an isolated zero-request
    # accounting mutation of real recorded envelopes; no fixture is changed.
    from doe_mcp.applications import research_linkage
    import unittest.mock
    original = resolve_doi
    calls = []
    async def cached(context, **kwargs):
        calls.append(kwargs['doi'])
        env = await original(context, TARGET)
        env.data['requests_used'] = 0  # isolated cache-accounting mutation
        return env
    edges = [{'edge_id': str(i), 'target_namespace': 'doi', 'target_identifier': f'10.9999/{i:02}'} for i in range(10)]
    with unittest.mock.patch.object(research_linkage, 'resolve_doi', cached):
        await follow_assertions(run, {'explicit_relationships': edges}, request_limit=24)
    assert len(calls) == 8
    assert [edge['followup']['status'] for edge in edges[-2:]] == ['budget_limited', 'budget_limited']
    assert run.report['relationship_resolution']['requests_used'] == 0


async def test_source_coverage_names_only_attempted_queries_after_budget_exhaustion(ctx):
    env = await resolve_doi(ctx, TARGET, request_limit=1)
    assert env.coverage.sources_searched == ['osti-gov-records']
    assert env.data['requests_used'] == 1
    assert len(env.data['search_scope']) == 4
    assert all(scope['requests_used'] == 0 and scope['pages'] == [] for scope in env.data['search_scope'][1:])


async def test_source_coverage_retains_attempted_publisher_when_retry_budget_expires(ctx, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503)
    policy = EgressPolicy(allowed_hosts=frozenset({'www.osti.gov'}),
        resolver=lambda host, _: [(0, 0, 0, '', ('93.184.216.34', 0))])
    monkeypatch.setattr(ctx.osti, '_fetcher', HttpFetcher(policy=policy, transport=httpx.MockTransport(handler)))
    env = await resolve_doi(ctx, TARGET, request_limit=1)
    assert env.coverage.sources_searched == ['osti-gov-records']
    assert env.data['status'] == 'budget_limited'
    assert env.data['requests_used'] == len(calls) == 1
    assert env.data['search_scope'][0]['requests_used'] == 1
    assert not env.data['search_complete']
