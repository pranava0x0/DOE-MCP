from pathlib import Path

import pytest
import yaml

from doe_mcp.core.envelope import Envelope
from doe_mcp.core.errors import SourceUnavailable
from doe_mcp.replay import fixture_manifest, load_replay_context
from doe_mcp.workflows import CASES, evidence_csv, run_workflow
from tools.render_workflows import link, outcome, render, series_chart

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'


@pytest.mark.parametrize('case', CASES)
async def test_workflows_replay_with_resolvable_evidence(case):
    def context():
        return load_replay_context(FIXTURES, keyed=case == 'eia')
    report = await run_workflow(context(), case, fixtures=fixture_manifest(FIXTURES))
    assert report == await run_workflow(context(), case, fixtures=fixture_manifest(FIXTURES))
    for step in report['steps']:
        env = Envelope.model_validate(step['envelope'])
        assert all(e.source_ref in {s.id for s in env.provenance} for e in env.evidence)
        assert not env.coverage.source_failures
    assert report['mode'] == 'recorded'
    assert 'replay clock' in report['timestamp_note']


async def test_search_record_dependency_and_missing_record_refusal(ctx):
    report = await run_workflow(ctx, 'evidence')
    found, detail = report['steps']
    rid = detail['args']['record_id']
    assert rid in {r['id'] for r in found['envelope']['data']['records']}
    assert detail['envelope']['data']['record']['id'] == rid
    fetcher = ctx.osti._fetcher
    fetcher.interactions = {k: v for k, v in fetcher.interactions.items()
                            if not v['url'].endswith('/' + rid)}
    # Fresh cache prevents the previous lookup from hiding a missing recording.
    from doe_mcp.adapters.base import TTLCache
    ctx.osti._cache = TTLCache()
    with pytest.raises(SourceUnavailable):
        await run_workflow(ctx, 'evidence')


async def test_simulation_is_isolated_and_explicit(ctx):
    before = ctx.sources.get('osti-doe-pages').lifecycle.declared_state
    report = await run_workflow(ctx, 'partial')
    assert report['simulation']
    assert report['steps'][0]['envelope']['coverage']['sources_unavailable']
    assert ctx.sources.get('osti-doe-pages').lifecycle.declared_state == before
    with pytest.raises(ValueError, match='recorded mode'):
        await run_workflow(ctx, 'partial', mode='live')


def test_safe_rendering_and_no_missing_value_bridge():
    assert '<a ' not in link('javascript:alert(1)', '<script>')
    assert '&lt;script&gt;' in link('javascript:alert(1)', '<script>')
    chart = series_chart([('a', 1), ('b', None), ('c', 3)], 'Example', 'MW')
    assert chart.count('<polyline') == 2
    assert 'Unknown' in chart


async def test_publisher_text_cannot_become_html_or_csv_formula(ctx):
    report = await run_workflow(ctx, 'evidence')
    report['steps'][0]['envelope']['data']['records'][0]['title'] = '<img src=x onerror=alert(1)>'
    report['steps'][0]['envelope']['evidence'][0]['record_id'] = '=2+2'
    assert "'=2+2" in evidence_csv(report)
    page = render([report])
    assert '<img src=x' not in page
    assert '&lt;img src=x' in page


def test_each_workflow_annotation_names_an_existing_bench_task():
    cases = set()
    for path in (ROOT / 'skills').glob('*/bench.yaml'):
        bench = yaml.safe_load(path.read_text())
        checked = {r['task'] for r in bench['checked']}
        for workflow in bench.get('workflows', []):
            assert workflow['task'] in checked
            assert workflow['case'] in CASES
            cases.add(workflow['case'])
    assert {'evidence', 'grid', 'site', 'earth', 'materials'} <= cases


async def test_dataset_starters_preserve_metadata_evidence_and_unknowns(ctx):
    report = await run_workflow(ctx, 'dataset-card')
    envelope = report['steps'][0]['envelope']
    assert len(report['drafts']) == len(envelope['data']['datasets']) == 2
    for draft, record in zip(report['drafts'], envelope['data']['datasets'], strict=True):
        assert draft['metadata'] == record
        assert draft['status'] == 'draft_metadata_only'
        assert draft['evidence']
        assert all(e['record_id'] == record['id'] for e in draft['evidence'])
        assert any('checksums' in field for field in draft['needs_owner_review'])
        assert any('License' in field for field in draft['needs_owner_review'])
    assert 'not a validated Genesis' in report['limitations'][0]
    report['drafts'][0]['metadata']['title'] = '<img src=x onerror=alert(1)>'
    page = render([report])
    assert '<img src=x' not in page
    assert '&lt;img src=x' in page


async def test_microbiome_follows_search_id_and_keeps_sample_scope(ctx):
    report = await run_workflow(ctx, 'microbiome')
    search, samples = report['steps']
    study = search['envelope']['data']['studies'][0]['study_id']
    assert samples['args'] == {'study_id': study, 'rows': 5}
    assert all(study in r['studies'] for r in samples['envelope']['data']['biosamples'])
    assert samples['envelope']['coverage']['pagination'] == 'truncated'
    page = render([report])
    assert 'dataset_doi' in page and 'award_doi' in page
    assert 'Sample metadata; no sequence data retrieved' in page


@pytest.mark.parametrize('case,tool,key', [
    ('dataset-card', 'research.search_datasets', 'datasets'),
    ('microbiome', 'bio.search_studies', 'studies'),
])
async def test_new_workflows_refuse_empty_search_without_followup(ctx, monkeypatch, case, tool, key):
    from types import SimpleNamespace
    from doe_mcp import workflows
    original = workflows.tool_spec
    calls = []

    async def empty(context, **args):
        calls.append(tool)
        env = await original(tool).fn(context, **args)
        env.data[key] = []
        return env

    monkeypatch.setattr(workflows, 'tool_spec', lambda name: SimpleNamespace(fn=empty) if name == tool else pytest.fail('unexpected follow-up'))
    with pytest.raises(SourceUnavailable, match='no '):
        await run_workflow(ctx, case)
    assert calls == [tool]


async def test_microbiome_refuses_when_no_study_lists_samples(ctx, monkeypatch):
    """A parent study can carry no samples of its own. An empty follow-up
    for every returned study is not a biosample walkthrough, so the demo
    refuses rather than reporting success with nothing to inspect."""
    from types import SimpleNamespace
    from doe_mcp import workflows
    original = workflows.tool_spec
    tried = []

    async def no_samples(context, **args):
        tried.append(args['study_id'])
        env = await original('bio.search_biosamples').fn(context, **args)
        env.data['biosamples'] = []
        return env

    monkeypatch.setattr(workflows, 'tool_spec', lambda name: SimpleNamespace(fn=no_samples)
                        if name == 'bio.search_biosamples' else original(name))
    with pytest.raises(SourceUnavailable, match='listed biosamples'):
        await run_workflow(ctx, 'microbiome')
    assert tried and len(tried) == len(set(tried))


async def test_microbiome_outcome_reads_the_attempt_that_listed_samples(ctx):
    """When an earlier study's follow-up came back empty, the summary must
    describe the attempt that succeeded, not the first one."""
    import copy
    report = await run_workflow(ctx, 'microbiome')
    search, samples = report['steps']
    empty = copy.deepcopy(samples)
    empty['envelope']['data'].update(biosamples=[], record_count=0)
    report['steps'] = [search, empty, samples]
    text = outcome(report)
    assert 'Tried 2 returned studies' in text
    assert f"Showing {samples['envelope']['data']['record_count']} biosamples" in text
    assert 'Showing 0' not in text
