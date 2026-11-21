"""Recorded discovery and isolated mutations of publisher relationship metadata."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from doe_mcp.adapters.osti_family import normalized_doi, related_artifacts
from doe_mcp.applications.bundle import Run
from doe_mcp.applications.research_linkage import build, receipt
from doe_mcp.core.errors import InvalidQuery, SourceNotActivated
from doe_mcp.core.registry import DeclaredState

ROOT = Path(__file__).resolve().parents[1]


def pointer(document, path):
    for part in path.strip('/').split('/'):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


async def test_recorded_inventory_retains_evidence_and_honest_empty_edges(ctx):
    run = Run(ctx, 'research-linkage', 'recorded')
    files = await build(run)
    report = run.report
    assert len(report['records']) == len(report['unmatched_records']) == 10
    assert report['explicit_relationships'] == report['shared_identity'] == []
    assert len(report['steps']) == 3
    assert {row['collection'] for row in report['records']} == {'literature', 'dataset', 'software'}
    assert {row['query'] for row in report['search_scope']} == {'perovskite solar', 'geothermal', 'machine learning'}
    assert all(scope['stop_reason'] == 'page_budget_exhausted' and not scope['pagination_complete'] for scope in report['search_scope'])
    for record in report['records']:
        assert record['node_id'].startswith(record['source_id'] + ':')
        for ref in record['evidence']:
            original = pointer(report, ref['record_pointer'])
            evidence = pointer(report, ref['evidence_pointer'])
            assert original['id'] == evidence['record_id'] == record['record_id']
            assert evidence['id'] == ref['evidence_id']
    assert json.loads(files['linkage.json'])['records'] == report['records']
    again = Run(ctx, 'research-linkage', 'recorded')
    repeated = await build(again)
    assert repeated['inventory.csv'] == files['inventory.csv']
    assert [row['node_id'] for row in again.report['records']] == [row['node_id'] for row in report['records']]


def recorded_relation():
    fixture = json.loads((ROOT / 'tests/fixtures/ceser-eagle-i-outages.json').read_bytes())
    for interaction in fixture['interactions']:
        for row in interaction['body']:
            if row.get('related_identifiers'):
                return row
    raise AssertionError('recorded typed relationship missing')


def test_normalizes_actual_publisher_typed_relation_without_generic_identifiers():
    raw = recorded_relation()
    result = related_artifacts(raw)
    assert result['unparsed_count'] == 0
    assertion = result['assertions'][0]
    assert assertion['relationship'] == 'IsPartOf'
    assert assertion['target_namespace'] == 'doi'
    assert assertion['target_identifier'] == '10.13139/ornlnccs/1975203'
    assert assertion['origin_field'] == 'related_identifiers/0'
    assert assertion['publisher_value'] == raw['related_identifiers'][0]
    assert related_artifacts({'identifier': '10.13139/ORNLNCCS/1975203',
        'award_dois': ['10.13139/ORNLNCCS/1975203'], 'relation': 'Journal Name: Example'}) == {'assertions': [], 'unparsed_count': 0}


@pytest.mark.parametrize('case', ['namespace', 'relation', 'mismatch', 'malformed', 'shape', 'unhashable_relation'])
def test_ambiguous_or_untyped_related_values_never_form_edges(case):
    raw = copy.deepcopy(recorded_relation())
    value = raw['related_identifiers'][0]
    if case == 'namespace':
        value = value.replace('"DOI"', '"AwardNumber"')
    elif case == 'relation':
        value = value.replace('IsPartOf', 'UnknownRelation')
    elif case == 'mismatch':
        value = value.replace('CURATED; IsPartOf;', 'CURATED; References;')
    elif case == 'malformed':
        value = 'CURATED; IsPartOf; {'
    elif case == 'unhashable_relation':
        value = value.replace('"relation_type" : "IsPartOf"', '"relation_type" : []')
    else:
        value = {'related_identifier': '10.13139/ORNLNCCS/1975203'}
    raw['related_identifiers'] = [value]
    assert related_artifacts(raw) == {'assertions': [], 'unparsed_count': 1}


@pytest.mark.parametrize('value,expected', [
    (' https://doi.org/10.13139/ORNLNCCS/1975203 ', '10.13139/ornlnccs/1975203'),
    ('DOI:10.1234/Test', '10.1234/test'), ('AC36-08GO28308', None),
    ('https://example.org/10.1234/a', None), ('10.1234/a b', None), (None, None)])
def test_doi_matching_has_explicit_namespace(value, expected):
    assert normalized_doi(value) == expected


def test_identity_and_asserted_artifact_relationships_are_separate_and_targets_retained():
    # Isolated normalized-record mutations exercise matching, never a publisher fixture.
    evidence = {'step': 0, 'evidence_pointer': '/steps/0/envelope/evidence/0'}
    assertion = related_artifacts(recorded_relation())['assertions'][0]
    records = [{'node_id': 'one:1', 'doi_key': '10.1234/shared', 'evidence': [evidence],
                'assertions': [{**assertion, 'evidence': evidence}]},
               {'node_id': 'two:2', 'doi_key': '10.1234/shared', 'evidence': [evidence], 'assertions': []}]
    result = receipt(records)
    assert result['shared_identity'][0]['members'] == ['one:1', 'two:2']
    edge = result['explicit_relationships'][0]
    assert edge['relationship'] == 'IsPartOf' and edge['matched_nodes'] == []
    assert edge['resolution'] == 'target_not_in_returned_records'
    assert 'url' not in edge and 'title' not in edge
    assert result['unmatched_records'] == []  # Shared DOI identity only, never a uses-data assertion.
    records[1]['doi_key'] = assertion['target_identifier']
    resolved = receipt(records)
    assert resolved['shared_identity'] == []
    assert resolved['explicit_relationships'][0]['matched_nodes'] == ['two:2']
    assert resolved['explicit_relationships'][0]['edge_id'] == edge['edge_id']
    assert resolved['unmatched_records'] == []


async def test_source_activation_gate_is_used_by_the_application(ctx):
    manifest = ctx.sources.get('osti-data-explorer')
    manifest.lifecycle.declared_state = DeclaredState.proposed
    with pytest.raises(SourceNotActivated, match='osti-data-explorer'):
        await build(Run(ctx, 'research-linkage', 'recorded'))


async def test_typed_assertion_survives_tool_and_receipt_with_field_evidence(ctx, monkeypatch):
    original = ctx.osti.search
    relation = recorded_relation()['related_identifiers'][0]

    async def changed(manifest, **kwargs):
        fetched = await original(manifest, **kwargs)
        if manifest.id == 'osti-doe-code':
            fetched.value.records[0].raw = {**fetched.value.records[0].raw, 'related_identifiers': [relation]}
        return fetched

    monkeypatch.setattr(ctx.osti, 'search', changed)
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run)
    edge, = run.report['explicit_relationships']
    assert edge['resolution'] == 'target_not_in_returned_records'
    assert edge['target_identifier'] == '10.13139/ornlnccs/1975203'
    ref, = edge['evidence']
    assertion = pointer(run.report, ref['assertion_pointer'])
    assert assertion['publisher_value'] == ref['publisher_value'] == relation
    assert assertion['origin_field'] == ref['origin_field'] == 'related_identifiers/0'
    assert pointer(run.report, ref['evidence_pointer'])['id'] == ref['evidence_id']
    assert ref['source_id'] == 'osti-doe-code'


async def test_page_budget_duplicate_results_and_unknown_totals_remain_visible(ctx, monkeypatch):
    original = ctx.osti.search
    calls = []

    async def repeated(manifest, **kwargs):
        calls.append((manifest.id, kwargs['page']))
        kwargs['page'] = 1
        fetched = await original(manifest, **kwargs)
        fetched.value.total_matches = None
        return fetched

    monkeypatch.setattr(ctx.osti, 'search', repeated)
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, page_budget=2)
    assert len(run.report['steps']) == 6 and len(run.report['records']) == 10
    assert {page for _, page in calls} == {1, 2}
    assert all(scope['repeated_records'] > 0 and not scope['pagination_complete'] for scope in run.report['search_scope'])
    assert all('records_repeated_across_pages' in scope['consistency_gaps'] for scope in run.report['search_scope'])
    assert all(page['coverage']['pagination'] == 'unknown' for scope in run.report['search_scope'] for page in scope['pages'])


@pytest.mark.parametrize('changed_total', [False, True])
async def test_short_final_literature_page_compares_totals_independently_of_returned_counts(ctx, monkeypatch, changed_total):
    original = ctx.osti.search

    async def split_recorded_rows(manifest, **kwargs):
        # Split real recorded records into disjoint pages without changing a
        # publisher fixture. Other collections finish in one bounded page.
        requested_page = kwargs['page']
        kwargs.update(page=1, rows=2 if manifest.id == 'osti-data-explorer' else 3)
        fetched = copy.deepcopy(await original(manifest, **kwargs))
        result = fetched.value
        result.page, result.rows = requested_page, 2
        if manifest.id in {'osti-gov-records', 'osti-doe-pages'}:
            assert len(result.records) == 3
            result.records = result.records[:2] if requested_page == 1 else result.records[2:]
            result.total_matches = 2 if changed_total and requested_page == 2 else 3
        else:
            result.records = result.records[:2]
            result.total_matches = len(result.records)
        return fetched

    monkeypatch.setattr(ctx.osti, 'search', split_recorded_rows)
    run = Run(ctx, 'research-linkage', 'recorded')
    await build(run, rows=2, page_budget=2)
    literature, dataset, software = run.report['search_scope']
    first, final = literature['pages']
    assert literature['repeated_records'] == 0
    assert first['returned_counts'] == {'osti-gov-records': 2, 'osti-doe-pages': 2}
    assert final['returned_counts'] == {'osti-gov-records': 1, 'osti-doe-pages': 1}
    assert first['publisher_totals'] == {'osti-gov-records': 3, 'osti-doe-pages': 3}
    assert final['publisher_totals'] == {'osti-gov-records': 2 if changed_total else 3,
                                         'osti-doe-pages': 2 if changed_total else 3}
    assert final['coverage']['pagination'] == 'complete'
    assert literature['pagination_complete'] is not changed_total
    assert literature['consistency_gaps'] == (['publisher_totals_changed'] if changed_total else [])
    assert literature['stop_reason'] == 'publisher_page_complete'
    for scope in [dataset, software]:
        assert scope['pagination_complete'] and len(scope['pages']) == 1
        assert list(scope['pages'][0]['publisher_totals'].values()) == [2]
        assert list(scope['pages'][0]['returned_counts'].values()) == [2]


@pytest.mark.parametrize('kwargs', [{'page_budget': 0}, {'page_budget': 6}, {'rows': 0}, {'rows': 51}])
async def test_bounded_search_parameters_are_validated_before_fetch(ctx, kwargs):
    run = Run(ctx, 'research-linkage', 'recorded')
    with pytest.raises(InvalidQuery):
        await build(run, **kwargs)
    assert run.report['steps'] == []


def test_cli_exports_default_recorded_receipt(tmp_path):
    output = tmp_path / 'research'
    result = subprocess.run([sys.executable, '-m', 'doe_mcp.cli', 'app', 'research-linkage', '--output', str(output)],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / 'report.json').read_bytes())
    assert len(report['records']) == 10 and report['mode'] == 'recorded'
    assert report['fixtures'] and report['steps']
    assert {row['file'] for row in json.loads((output / 'manifest.json').read_bytes())['files']} == {'report.json', 'linkage.json', 'inventory.csv'}
