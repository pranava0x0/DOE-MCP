import json
import sys
from argparse import Namespace

import pytest

from doe_mcp.evaluation import check_answer, command, remove_totals, run, tasks
from doe_mcp.domains.research import search_literature
from doe_mcp.servers.build import build_server


GCAM_TRAPS = {
    'gcam-region-convention': ('economy:default', 'economy.country_regions'),
    'gcam-blank-coefficient': ('emissions:default', 'emissions.technology_coefficients'),
    'gcam-incompatible-input-units': ('water:default', 'water.desalination_inputs'),
    'gcam-inputs-versus-observations': ('water:default', 'water.cooling_shares'),
}

ATB_TASKS = {
    'atb-qualified-assumption': 'answerable',
    'atb-multiple-matches': 'trap',
    'atb-null-assumption': 'trap',
    'atb-changed-financial-case': 'trap',
    'atb-currency-bases': 'trap',
}


def test_evaluation_reuses_existing_skill_tasks_and_semantic_traps():
    rows = tasks()
    assert {r['id'] for r in rows} == {
        'facility', 'literature', 'registry-gap', 'alias', 'missing-total', 'vintages',
        'raw-data-injection', 'weather', 'structures', 'demand', 'bpa', 'forecast',
        *GCAM_TRAPS, *ATB_TASKS,
    }
    assert sum(r['kind'] == 'trap' for r in rows) == 14
    assert sum(r['kind'] == 'answerable' for r in rows) == 7


@pytest.mark.parametrize('task_id', GCAM_TRAPS)
async def test_gcam_evaluation_profile_exposes_the_required_read(ctx, task_id):
    task = next(row for row in tasks() if row['id'] == task_id)
    profile, tool = GCAM_TRAPS[task_id]
    assert task['profile'] == profile
    assert task['kind'] == 'trap' and task['mutation'] == 'none'
    assert task['prompt'] and task['rubric']
    visible = await build_server(ctx, profile).list_tools()
    assert tool in {entry.name for entry in visible}


@pytest.mark.parametrize('task_id', ATB_TASKS)
async def test_atb_evaluation_profile_exposes_discovery_and_qualified_read(ctx, task_id):
    task = next(row for row in tasks() if row['id'] == task_id)
    assert task['skill'] == 'atb-assumptions'
    assert task['profile'] == 'energy-tech:default'
    assert task['kind'] == ATB_TASKS[task_id] and task['mutation'] == 'none'
    assert task['prompt'] and task['rubric']
    visible = await build_server(ctx, task['profile']).list_tools()
    assert {'tech.describe_atb', 'tech.read_atb'} <= {entry.name for entry in visible}


def test_prepare_exports_semantic_tasks_without_a_model_or_network(tmp_path):
    output = tmp_path / 'evaluation.json'
    assert command(Namespace(prepare=True, output=output)) == 0
    report = json.loads(output.read_text())
    assert set(GCAM_TRAPS) <= {row['id'] for row in report['tasks']}
    assert set(ATB_TASKS) <= {row['id'] for row in report['tasks']}
    assert 'Grade each answer against its rubric' in report['required_review']


async def test_missing_total_mutation_preserves_fixture(ctx):
    original = ctx.osti._fetcher.interactions
    remove_totals(ctx)
    env = await search_literature(ctx, query='perovskite solar', rows=3)
    assert env.coverage.pagination.value == 'unknown'
    assert any('x-total-count' in x.get('headers', {}) for x in original.values())


def test_citations_are_checked_against_actual_tool_evidence():
    assert check_answer({'text': 'claim', 'citations': [{'step': 1, 'evidence_id': 'invented'}]}, [])
    assert check_answer({'text': ''}, [])


def options(driver, output, **overrides):
    args = dict(driver=driver, output=output, model='test-driver-no-model', max_calls=1,
                max_tokens=100, max_turns=2, timeout=5, repeats=3, exposure='focused',
                skills=False, task=['alias'], fixtures=None)
    return Namespace(**(args | overrides))


async def test_budget_retains_unrun_tasks_in_denominator(tmp_path):
    driver = tmp_path / 'driver'
    driver.write_text(f'#!{sys.executable}\nimport json\nprint(json.dumps({{"answer": {{"text": "Requires human review", "citations": []}}, "usage": {{"input_tokens": 5, "output_tokens": 5}}}}))\n')
    driver.chmod(0o700)
    report = await run(options(driver, tmp_path / 'report.json'))
    assert report['denominator'] == 3
    assert report['calls'] == 1
    assert report['tokens'] == 10
    assert [r['status'] for r in report['runs']] == ['needs_review', 'budget_exhausted', 'budget_exhausted']
    assert all(r['review'] == 'pending' for r in report['runs'])


async def test_unmetered_failure_stops_spending_and_hides_driver_output(tmp_path):
    driver = tmp_path / 'driver'
    driver.write_text(f'#!{sys.executable}\nprint("provider-secret-do-not-log")\n')
    driver.chmod(0o700)
    report = await run(options(driver, tmp_path / 'report.json', max_calls=5))
    assert report['calls'] == 1
    assert report['runs'][0]['status'] == 'driver_or_contract_error'
    assert 'provider-secret' not in json.dumps(report)


async def test_driver_can_use_a_tool_then_cite_its_evidence(tmp_path):
    driver = tmp_path / 'driver'
    driver.write_text(f'''#!{sys.executable}
import json,sys
r=json.load(sys.stdin)
results=[m for m in r['messages'] if m['role']=='tool']
if not results:
 out={{'tool_calls':[{{'name':'registry.resolve_org','arguments':{{'query':'NREL'}}}}]}}
else:
 evidence=results[-1]['content']['envelope']['evidence'][0]['id']
 out={{'answer':{{'text':'The local registry resolves NREL to NLR.','citations':[{{'step':1,'evidence_id':evidence}}]}}}}
out['usage']={{'input_tokens':5,'output_tokens':5}}
print(json.dumps(out))
''')
    driver.chmod(0o700)
    report = await run(options(driver, tmp_path / 'report.json', max_calls=2, repeats=1))
    assert report['runs'][0]['status'] == 'needs_review'
    assert not report['runs'][0]['automatic_failures']
    assert report['runs'][0]['steps'][0]['envelope']['data']['resolved']['id'] == 'nlr'


async def test_the_model_is_told_tool_results_are_replays(tmp_path):
    """The envelopes in a replay read access_path=live; the prompt has to
    say otherwise or a 'latest value' task measures false freshness."""
    seen = tmp_path / 'request.json'
    driver = tmp_path / 'driver'
    driver.write_text(f'''#!{sys.executable}
import json,sys
r=json.load(sys.stdin)
open({str(seen)!r},'w').write(json.dumps(r))
print(json.dumps({{'answer':{{'text':'x','citations':[]}},'usage':{{'input_tokens':1,'output_tokens':1}}}}))
''')
    driver.chmod(0o700)
    await run(options(driver, tmp_path / 'report.json', repeats=1))
    system = json.loads(seen.read_text())['messages'][0]['content']
    assert 'replay of publisher responses' in system
    assert 'Do not describe any value as current or live' in system


async def test_atb_evaluation_replays_both_currency_bases_without_keys_or_http(tmp_path, monkeypatch):
    from doe_mcp.adapters.base import HttpFetcher
    from doe_mcp.core.credentials import Credentials

    def refuse(*args, **kwargs):
        raise AssertionError('ATB evaluation must use the existing recordings without keys or HTTP')

    monkeypatch.setattr(Credentials, 'load', refuse)
    for method in ('fetch_json', 'fetch_text', 'fetch_bytes'):
        monkeypatch.setattr(HttpFetcher, method, refuse)
    driver = tmp_path / 'atb-protocol-driver'
    driver.write_text(f'''#!{sys.executable}
import json,sys
request=json.load(sys.stdin)
steps=[message for message in request['messages'] if message['role']=='tool']
if not steps:
 assert 'ATB assumptions' in request['messages'][0]['content']
 filters={{'technology':'OffShoreWind','core_metric_parameter':'Fixed O&M',
          'scenario':'Moderate','core_metric_variable':'2035','techdetail':'Class3',
          'techdetail2':'Fixed-Bottom','resourcedetail':'Class3',
          'core_metric_case':'R&D','tax_credit_case':None,'crpyears':'20'}}
 reply={{'tool_calls':[{{'name':'tech.read_atb',
          'arguments':{{'edition':year,'filters':filters,'rows':2}}}} for year in [2024,2025]]}}
else:
 reply={{'answer':{{'text':'Simulation protocol check only; no model quality claim.',
    'citations':[{{'step':i,'evidence_id':step['content']['envelope']['evidence'][0]['id']}}
                 for i,step in enumerate(steps,1)]}}}}
reply.update(usage={{'input_tokens':1,'output_tokens':1}},
             model_version='deterministic-protocol-test',settings={{'deterministic':True}})
print(json.dumps(reply))
''')
    driver.chmod(0o700)
    report = await run(options(driver, tmp_path / 'report.json',
        max_calls=2, repeats=1, skills=True, task=['atb-currency-bases'], simulation=True))
    row = report['runs'][0]
    assert report['simulation'] and row['status'] == 'needs_review'
    assert row['profiles'] == ['energy-tech:default']
    assert not row['automatic_failures'] and row['review'] == 'pending'
    before, after = [step['envelope']['data'] for step in row['steps']]
    assert before['total_matches'] == after['total_matches'] == 1
    assert before['records'][0]['values']['value'] == 74.1812
    assert after['records'][0]['values']['value'] == 77.23474920912257
    assert '2022 USD' in before['currency_basis']
    assert '2023 USD' in after['currency_basis']
    assert before['currency_basis_source'] != after['currency_basis_source']
