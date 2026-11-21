"""Offline comparison contracts; reports come from a deterministic test driver."""
from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import io
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from doe_mcp.evaluation import register, run
from doe_mcp.evaluation_contract import bind_run
from doe_mcp.evaluation_matrix import compare, normalize_report, read_json


@pytest.fixture(scope="module")
def driver_report(tmp_path_factory):
    root = tmp_path_factory.mktemp("matrix-driver")
    driver = root / "deterministic-driver"
    driver.write_text(f'''#!{sys.executable}
import json,sys
request=json.load(sys.stdin)
steps=[message for message in request['messages'] if message['role']=='tool']
if not steps:
 result={{'tool_calls':[{{'name':'registry.resolve_org','arguments':{{'query':'NREL'}}}}]}}
else:
 evidence=steps[-1]['content']['envelope']['evidence'][0]['id']
 result={{'answer':{{'text':'Simulation: the local registry resolves the former name. No model was called.',
                   'citations':[{{'step':1,'evidence_id':evidence}}]}}}}
result.update(usage={{'input_tokens':10,'cached_input_tokens':4,'output_tokens':5,'reasoning_output_tokens':2}},
              model_version='simulation-v1',settings={{'deterministic':True}})
print(json.dumps(result))
''')
    driver.chmod(0o700)
    return asyncio.run(run(argparse.Namespace(driver=driver, output=root / "unused.json",
        model="deterministic-test-driver", max_calls=4, max_tokens=1000,
        max_turns=3, timeout=5, repeats=2, exposure="focused", skills=False,
        task=["alias"], fixtures=None, simulation=True)))


@pytest.fixture
def report(driver_report):
    return copy.deepcopy(driver_report)


def clone(report):
    other = copy.deepcopy(report)
    other["report_id"] = uuid.uuid4().hex
    other["started_at"] = (datetime.fromisoformat(report["started_at"]) + timedelta(seconds=1)).isoformat()
    for row in other["runs"]:
        row["run_id"] = uuid.uuid4().hex
    return other


def price_document(report, **changes):
    return {"format_version": "1", "rates": [{"model": report["model"],
        "model_version": "simulation-v1", "effective_date": "2000-01-01", "currency": "USD",
        "input_per_million": "2", "cached_input_per_million": "0.5",
        "output_per_million": "4", **changes}]}


def grade_document(report, scores=(0.75,)):
    _, files = compare([report])
    template = json.loads(files["grades-template.json"])
    template["grades"] = template["grades"][:len(scores)]
    for grade, score in zip(template["grades"], scores, strict=True):
        grade.update(score=score, reviewer={"kind": "human", "id": "unit-test-only"},
                     reviewed_at=report["started_at"], notes="Synthetic test of arithmetic; not published grading.")
    return template


def make_unrun(report, status="budget_exhausted", attempted=False):
    row = report["runs"][-1]
    for name in ("answer", "answer_sha256", "automatic_failures", "reported_model_version", "sample_settings"):
        row.pop(name, None)
    row.update(status=status, input_tokens=0, output_tokens=0, cached_input_tokens=0,
               reasoning_output_tokens=0, model_calls=int(attempted), calls=[],
               usage_complete=not attempted)
    report["usage_complete"] = not attempted
    report["completed_answers"] = 1
    report["calls"] = 2 + int(attempted)
    report["tokens"] = 30
    return report


def test_real_test_driver_report_has_exact_content_bindings_and_usage(report):
    normalized = normalize_report(report)
    assert report["format_version"] == "2" and report["simulation"]
    assert len(normalized["runs"]) == 2
    assert normalized["runs"][0]["usage"] == {
        "input_tokens": 20, "cached_input_tokens": 8,
        "output_tokens": 10, "reasoning_output_tokens": 4}
    assert normalized["runs"][0]["automatic_pass"] is True


def test_unknown_grades_and_prices_are_not_zero_or_accuracy(report):
    result, files = compare([report])
    cell = result["cells"][0]
    assert cell["scheduled"] == cell["attempted"] == cell["completed"] == 2
    assert cell["automatic_checked"] == cell["automatic_pass"] == 2
    assert cell["reviewed"] == 0
    assert cell["reviewed_quality_mean"] is None
    assert cell["overall_quality_mean"] is None
    assert cell["estimated_cost_usd"] is None
    assert cell["cost_unknown_runs"] == 2
    assert "unknown" in files["matrix.csv"].decode()
    assert len(json.loads(files["grades-template.json"])["grades"]) == 2


def test_cached_input_and_reasoning_output_are_not_double_counted(report):
    result, _ = compare([report], price_document=price_document(report))
    cell = result["cells"][0]
    assert cell["estimated_cost_usd"] == "0.000136"
    assert cell["usage"] == {"input_tokens": 40, "cached_input_tokens": 16,
                             "output_tokens": 20, "reasoning_output_tokens": 8}
    assert all(row["estimated_cost"] == "0.000068" for row in result["runs"])


def test_missing_and_partial_reviews_keep_whole_cohort_quality_unknown(report):
    result, _ = compare([report], grades=grade_document(report))
    cell = result["cells"][0]
    assert cell["reviewed"] == 1 and cell["scheduled"] == 2
    assert cell["reviewed_quality_mean"] == 0.75
    assert cell["overall_quality_mean"] is None
    assert cell["unreviewed_or_unrun"] == 1
    result, _ = compare([report], grades=grade_document(report, (0.75, 0.25)))
    assert result["cells"][0]["overall_quality_mean"] == 0.5


def test_human_and_model_reviewers_are_not_pooled(report):
    grades = grade_document(report, (0.75, 0.25))
    grades["grades"][1]["reviewer"] = {"kind": "model", "id": "simulated-grader-version"}
    result, _ = compare([report], grades=grades)
    cell = result["cells"][0]
    assert cell["reviewed"] == 2
    assert cell["reviewed_quality_mean"] is None and cell["overall_quality_mean"] is None
    assert cell["quality_by_reviewer_kind"] == {
        "human": {"reviewed": 1, "mean": 0.75}, "model": {"reviewed": 1, "mean": 0.25}}


@pytest.mark.parametrize("field", ["report_id", "report_sha256", "run_id", "task_sha256",
                                   "answer_sha256", "rubric_version", "rubric_sha256"])
def test_review_is_bound_to_the_exact_report_task_rubric_and_answer(report, field):
    grades = grade_document(report)
    grades["grades"][0][field] = "changed-binding"
    with pytest.raises(ValueError, match="grade"):
        compare([report], grades=grades)


def test_unrun_and_failed_attempts_remain_in_denominators(report):
    incomplete = make_unrun(copy.deepcopy(report))
    result, _ = compare([incomplete], price_document=price_document(report))
    cell = result["cells"][0]
    assert (cell["scheduled"], cell["attempted"], cell["completed"], cell["automatic_checked"]) == (2, 1, 1, 1)
    assert cell["status_counts"]["budget_exhausted"] == 1
    assert result["runs"][1]["estimated_cost"] == "0"
    failed = make_unrun(report, "driver_or_contract_error", attempted=True)
    result, _ = compare([failed], price_document=price_document(report))
    cell = result["cells"][0]
    assert (cell["scheduled"], cell["attempted"], cell["completed"]) == (2, 2, 1)
    assert cell["estimated_cost_usd"] is None and cell["usage"]["input_tokens"] is None
    assert result["runs"][0]["estimated_cost"] == "0.000068"


def test_missing_cached_breakdown_and_missing_price_remain_unknown(report):
    for row in report["runs"]:
        row["cached_input_tokens"] = None
        for call in row["calls"]:
            call["usage"].pop("cached_input_tokens")
    result, _ = compare([report], price_document=price_document(report))
    assert result["cells"][0]["estimated_cost_usd"] is None
    assert result["cells"][0]["usage"]["input_tokens"] == 40
    assert result["cells"][0]["usage"]["cached_input_tokens"] is None


@pytest.mark.parametrize("changes", [{"model_version": "different-version"},
                                      {"effective_date": "2999-01-01"},
                                      {"cached_input_per_million": None}])
def test_unmatched_future_or_incomplete_prices_do_not_become_free(report, changes):
    result, _ = compare([report], price_document=price_document(report, **changes))
    assert result["cells"][0]["estimated_cost_usd"] is None


def test_explicit_zero_prices_are_distinct_from_unknown_prices(report):
    result, _ = compare([report], price_document=price_document(report,
        input_per_million="0", cached_input_per_million="0", output_per_million="0"))
    assert result["cells"][0]["estimated_cost_usd"] == "0"
    assert result["cells"][0]["cost_unknown_runs"] == 0


@pytest.mark.parametrize("mutation", ["report", "execution", "run"])
def test_duplicate_reports_executions_and_run_ids_are_refused(report, mutation):
    other = clone(report)
    if mutation == "report":
        other = copy.deepcopy(report)
    elif mutation == "execution":
        other["started_at"] = report["started_at"]
    else:
        other["runs"][0]["run_id"] = report["runs"][0]["run_id"]
    with pytest.raises(ValueError, match="duplicate"):
        compare([report, other])


@pytest.mark.parametrize("field", ["fixture", "prompt", "skill", "rubric"])
def test_unequal_cohorts_are_refused_or_explicitly_partitioned(report, field):
    other = clone(report)
    if field == "fixture":
        other["fixtures"][0]["sha256"] = "0" * 64
    else:
        for row in other["runs"]:
            if field == "skill":
                row["skill_sha256"] = "0" * 64
            else:
                row[field] += " altered"
                bind_run(row)
    with pytest.raises(ValueError, match="cohorts differ"):
        compare([report, other])
    result, _ = compare([report, other], allow_multiple_cohorts=True)
    assert len(result["cohorts"]) == len(result["cells"]) == 2


def test_exposure_and_skill_settings_are_explicit_matrix_dimensions(report):
    other = clone(report)
    other.update(exposure="all", skills_enabled=True)
    result, _ = compare([report, other])
    assert len(result["cohorts"]) == 1
    assert {(cell["exposure"], cell["skills_enabled"]) for cell in result["cells"]} == {
        ("focused", False), ("all", True)}
    assert all(cell["contexts"] for cell in result["cells"])


@pytest.mark.parametrize("field,value", [("input_tokens", True), ("cached_input_tokens", 999),
                                         ("output_tokens", -1), ("model_calls", 0),
                                         ("repeat", 0), ("status", "success"),
                                         ("automatic_failures", ["made up"]),
                                         ("task_sha256", "0" * 64)])
def test_inconsistent_or_invalid_report_metadata_is_refused(report, field, value):
    report["runs"][0][field] = value
    with pytest.raises(ValueError):
        compare([report])


def test_unsupported_usage_categories_and_changing_model_settings_are_refused(report):
    bad = copy.deepcopy(report)
    bad["runs"][0]["calls"][0]["usage"]["audio_tokens"] = 1
    with pytest.raises(ValueError, match="unsupported token"):
        compare([bad])
    report["runs"][0]["calls"][0]["settings"] = {"changed": True}
    with pytest.raises(ValueError, match="changed model version or settings"):
        compare([report])


@pytest.mark.parametrize("value", [None, -1, True, "NaN", "Infinity"])
def test_invalid_prices_are_refused(report, value):
    with pytest.raises(ValueError):
        compare([report], price_document=price_document(report, input_per_million=value))


@pytest.mark.parametrize("value", [None, -0.1, 1.1, True, float("nan")])
def test_invalid_review_scores_are_refused(report, value):
    grades = grade_document(report)
    grades["grades"][0]["score"] = value
    with pytest.raises(ValueError):
        compare([report], grades=grades)


def test_duplicate_reviews_and_prices_are_refused(report):
    grades = grade_document(report)
    grades["grades"].append(copy.deepcopy(grades["grades"][0]))
    with pytest.raises(ValueError, match="duplicate grade"):
        compare([report], grades=grades)
    rates = price_document(report)
    rates["rates"].append(copy.deepcopy(rates["rates"][0]))
    with pytest.raises(ValueError, match="duplicate model"):
        compare([report], price_document=rates)


def test_legacy_reports_keep_quality_and_cached_cost_unknown(report):
    report["format_version"] = "1"
    report.pop("report_id")
    for row in report["runs"]:
        for key in ("run_id", "task_sha256", "rubric_sha256", "answer_sha256", "rubric_version",
                    "calls", "cached_input_tokens", "reasoning_output_tokens"):
            row.pop(key, None)
    result, _ = compare([report], price_document=price_document(report))
    assert result["cells"][0]["reviewed_quality_mean"] is None
    assert result["cells"][0]["estimated_cost_usd"] is None


def test_json_parser_refuses_duplicate_keys_and_nonfinite_numbers(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"a": 1, "a": 2}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        read_json(path)
    path.write_text('{"a": NaN}')
    with pytest.raises(ValueError, match="non-finite"):
        read_json(path)


def test_csv_export_does_not_execute_supplied_model_labels(report):
    report["model"] = "=untrusted-label"
    _, files = compare([report])
    rows = list(csv.DictReader(io.StringIO(files["matrix.csv"].decode())))
    assert rows[0]["model"] == "'=untrusted-label"


def test_cli_writes_atomic_bundle_without_credentials_or_driver_calls(report, tmp_path, monkeypatch):
    from doe_mcp.core.credentials import Credentials
    from doe_mcp.evaluation_matrix import command
    import subprocess

    def refuse(*args, **kwargs):
        raise AssertionError("offline comparison must not load keys or invoke a driver")

    monkeypatch.setattr(Credentials, "load", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    output = tmp_path / "comparison"
    args = argparse.Namespace(report=[source], grades=None, prices=None,
                              allow_multiple_cohorts=False, output=output)
    assert command(args) == 0
    matrix = json.loads((output / "report.json").read_text())
    assert matrix["application"] == "evaluation-matrix"
    assert matrix["cells"][0]["scheduled"] == 2
    assert (output / "matrix.csv").exists() and (output / "runs.csv").exists()
    assert (output / "manifest.json").exists()
    with pytest.raises(SystemExit, match="already exists"):
        command(args)


def test_comparison_cli_is_registered_without_changing_evaluate_options():
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["evaluate-matrix", "--report", "input.json", "--output", "matrix"])
    assert args.report == [Path("input.json")]
    existing = parser.parse_args(["evaluate", "--prepare", "--output", "tasks.json"])
    assert existing.prepare


@pytest.mark.parametrize("answer", [{"text": "", "citations": []}, "malformed answer",
                                     {"text": "answer", "citations": [{"step": {}, "evidence_id": []}]}])
def test_failed_automatic_answers_stay_in_completed_denominator(report, answer):
    from doe_mcp.evaluation import check_answer
    row = report["runs"][0]
    row.update(answer=answer, status="failed_checks",
               automatic_failures=check_answer(answer, row["steps"]))
    bind_run(row)
    result, _ = compare([report])
    cell = result["cells"][0]
    assert cell["scheduled"] == cell["completed"] == cell["automatic_checked"] == 2
    assert cell["automatic_pass"] == 1
    assert cell["reviewed"] == 0


def test_malformed_step_metadata_is_refused_cleanly(report):
    report["runs"][0]["steps"] = [None]
    with pytest.raises(ValueError, match="step"):
        compare([report])


def test_unknown_configuration_does_not_hide_duplicate_execution(report):
    for row in report["runs"]:
        row["sample_settings"] = "not reported"
        for call in row["calls"]:
            call["settings"] = "not reported"
    other = clone(report)
    other["started_at"] = report["started_at"]
    with pytest.raises(ValueError, match="duplicate report or execution"):
        compare([report, other])


def test_run_id_collision_cannot_be_hidden_in_a_legacy_named_new_report(report):
    other = clone(report)
    other["report_id"] = "legacy-looking-new-report"
    other["runs"][0]["run_id"] = report["runs"][0]["run_id"]
    with pytest.raises(ValueError, match="duplicate run_id"):
        compare([report, other])


def test_model_settings_preserve_json_types_between_calls(report):
    for row in report["runs"]:
        row["sample_settings"] = {"parameter": 1}
        for call in row["calls"]:
            call["settings"] = {"parameter": 1}
    report["runs"][0]["calls"][0]["settings"] = {"parameter": True}
    with pytest.raises(ValueError, match="changed model version or settings"):
        compare([report])
