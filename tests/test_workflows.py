from pathlib import Path

import pytest
import yaml

from doe_mcp.core.envelope import Envelope
from doe_mcp.core.errors import SourceUnavailable
from doe_mcp.replay import fixture_manifest, load_replay_context
from doe_mcp.workflows import CASES, evidence_csv, run_workflow
from tools.render_workflows import link, render, series_chart

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
