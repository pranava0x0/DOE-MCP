import json
import sys
from argparse import Namespace


from doe_mcp.evaluation import check_answer, remove_totals, run, tasks
from doe_mcp.domains.research import search_literature


def test_evaluation_reuses_twelve_skill_tasks():
    rows = tasks()
    assert len(rows) == 12
    assert sum(r['kind'] == 'trap' for r in rows) == 6
    assert sum(r['kind'] == 'answerable' for r in rows) == 6


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
