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
    ('outage-history', 'grid.find_outage_history', 'releases_for_year'),
    ('rulemaking', 'docs.search_rulemakings', 'documents'),
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


async def test_rulemaking_follows_returned_document_and_preserves_unknown_date(ctx):
    report = await run_workflow(ctx, 'rulemaking')
    search, detail = report['steps']
    doc = detail['envelope']['data']
    assert detail['args']['document_number'] == search['envelope']['data']['documents'][0]['document_number']
    assert doc['effective_on'] is None
    assert doc['action'] == 'Notification of stay.'
    page = render([report])
    assert 'Unknown' in page
    assert 'includes the documents dropped' in page
    doc['action'] = '<script>alert(1)</script>'
    assert '<script>alert(1)</script>' not in render([report])


async def test_outage_demo_separates_release_and_denominator(ctx):
    report = await run_workflow(ctx, 'outage-history')
    data = report['steps'][0]['envelope']['data']
    assert report['steps'][0]['args'] == {'year': 2021}
    assert all(r['covers_years']['from'] <= 2021 <= r['covers_years']['to'] for r in data['releases_for_year'])
    page = render([report])
    assert 'Customer denominator' in page and 'Fall 2025' in page
    assert 'no outage rate is calculated' in page
    data['customer_counts'][0]['doi_url'] = 'javascript:alert(1)'
    assert 'href="javascript:' not in render([report])


def test_recipe_labels_break_at_words_and_drop_inline_markup():
    """Labels were cut at a fixed 80 characters, which split words and
    left `SiO<sub>2<` on the page."""
    from doe_mcp.core.assemble import short_label
    title = ("Reducing non-radiative recombination in perovskite solar cells "
             "with a SiO<sub>2</sub>-graphene oxide buried interface layer")
    label = short_label(title)
    assert len(label) <= 80 and label.endswith("…")
    assert "<" not in label and not label[:-1].endswith(" ")
    assert title.startswith(label[:-1])
    assert short_label("SiO<sub>2</sub> film") == "SiO2 film"
    assert short_label("") is None


async def test_rendered_walkthroughs_show_no_raw_markup_or_dangling_dash(ctx):
    import re
    page = render([await run_workflow(ctx, 'evidence')])
    # The raw response dump keeps the publisher's bytes; the prose does not.
    prose = re.sub(r'<pre\b.*?</pre>', '', page, flags=re.S)
    assert '&lt;sub&gt;' not in prose
    assert '</a> — </li>' not in page


async def test_find_data_names_the_rename_and_the_old_harvests(ctx):
    """The walk exists for the two kinds of nothing: a dead acronym and a
    catalog harvested years ago. Both have to be visible in the result."""
    report = await run_workflow(ctx, 'find-data')
    org, sources, catalogs = (s['envelope'] for s in report['steps'])
    assert org['data']['resolved']['id'] == 'nlr'
    assert any(w['code'] == 'alias_match' for w in org['warnings'])
    vintages = {c['vintage'] for c in catalogs['data']['per_catalog']}
    assert 'live' in vintages and any(v != 'live' for v in vintages)
    assert any(w['code'] == 'catalog_vintage' for w in catalogs['warnings'])
    text = outcome(report)
    assert 'National Laboratory of the Rockies' in text and 'older harvests' in text
    page = render([report])
    assert 'A dated vintage is a harvested file' in page
    import re
    scope = re.search(r'<p class="scope">(.*?)</p>', page).group(1)
    assert 'from DOE Data Explorer' in scope and 'of 2 matches' not in scope


async def test_facility_status_keeps_the_zone_and_the_other_facilities_unknown(ctx):
    report = await run_workflow(ctx, 'facility-status')
    data = report['steps'][0]['envelope']['data']
    text = outcome(report)
    assert f"of {data['record_count']} NERSC systems" in text
    assert 'zone not stated' in text or 'No planned outage' in text
    assert any('ALCF and OLCF' in limit for limit in report['limitations'])
    page = render([report])
    assert 'Last changed (zone not stated)' in page
    assert data['other_facilities_note'] in page


async def test_every_walkthrough_says_how_much_it_read_and_that_it_is_a_recording(ctx):
    """The sample size and the replay caveat sat in closed disclosures; a
    reader saw "2 metadata drafts" with no sign 1,035 records matched."""
    import re
    report = await run_workflow(ctx, 'dataset-card')
    page = render([report])
    scope = re.search(r'<p class="scope">(.*?)</p>', page).group(1)
    total = report['steps'][0]['envelope']['data']['total_matches']
    assert f"of {total:,} matches" in scope and 'recorded replay' in scope


async def test_rulemaking_evidence_carries_the_publisher_link(ctx):
    """The Federal Register supplies html_url; the evidence carried none and
    every card said no locator was supplied."""
    report = await run_workflow(ctx, 'rulemaking')
    detail = report['steps'][-1]['envelope']
    assert detail['evidence'][0]['locator'] == detail['data']['html_url']
    assert detail['data']['html_url'].startswith('https://www.federalregister.gov/')


async def test_outage_evidence_cards_name_each_release_and_its_role(ctx):
    page = render([await run_workflow(ctx, 'outage-history')])
    assert '(release covering 2021)' in page
    assert "(another year's release)" in page or '(another year&#x27;s release)' in page
