"""Offline comparison of driver reports, bound reviews and supplied prices.

This module never invokes a driver, selects a provider or loads credentials.
"""
from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .applications.bundle import export_bundle, json_bytes
from .evaluation_contract import content_sha256, task_binding

STATUSES = {"needs_review", "failed_checks", "not_run", "budget_exhausted",
            "driver_exceeded_token_budget", "driver_or_contract_error", "turn_limit"}
COMPLETED = {"needs_review", "failed_checks"}
TOKENS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
RATE_FIELDS = ("input_per_million", "cached_input_per_million", "output_per_million")


def _fail(message):
    raise ValueError("evaluation matrix: " + message)


def _object(value, field):
    if not isinstance(value, dict):
        _fail(field + " must be an object")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        _fail(field + " must be nonempty text")
    return value


def _int(value, field):
    if type(value) is not int or value < 0:
        _fail(field + " must be a nonnegative integer")
    return value


def _bool(value, field):
    if type(value) is not bool:
        _fail(field + " must be a boolean")
    return value


def _list(value, field):
    if not isinstance(value, list):
        _fail(field + " must be an array")
    return value


def _stamp(value, field):
    try:
        parsed = datetime.fromisoformat(_text(value, field))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing offset")
    except ValueError:
        _fail(field + " must be an ISO timestamp with an offset")
    return parsed


def _hash(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail(field + " must be a lowercase SHA256")
    return value


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON object key")
        result[key] = value
    return result


def read_json(path: Path):
    if path.stat().st_size > 50_000_000:
        _fail("input exceeds the 50 MB report limit")
    return json.loads(path.read_text(), object_pairs_hook=_unique_pairs,
                      parse_constant=lambda _: _fail("non-finite JSON number"))


def _fixtures(report):
    rows = _list(report.get("fixtures"), "fixtures")
    identities = []
    for row in rows:
        _object(row, "fixture")
        identities.append((_text(row.get("file"), "fixture file"),
                           _hash(row.get("sha256"), "fixture SHA256")))
    if not identities or len({name for name, _ in identities}) != len(identities):
        _fail("fixtures must be present and have unique file names")
    return sorted(identities)


def _usage(row, report):
    attempted = row["model_calls"] > 0
    complete = row.get("usage_complete", report["usage_complete"] if attempted else True)
    _bool(complete, "run usage_complete")
    counts = {name: row.get(name) for name in TOKENS}
    for name in ("input_tokens", "output_tokens"):
        _int(counts[name], name)
    for name in ("cached_input_tokens", "reasoning_output_tokens"):
        if counts[name] is not None:
            _int(counts[name], name)
            total = counts["input_tokens" if name == "cached_input_tokens" else "output_tokens"]
            if counts[name] > total:
                _fail("token subsets exceed their parent totals")
    if attempted and complete and not counts["input_tokens"] + counts["output_tokens"]:
        _fail("a completed usage record must report nonzero tokens")
    if not attempted:
        if any(value not in (0, None) for value in counts.values()) or not complete:
            _fail("an unattempted run cannot carry usage")
        counts = dict.fromkeys(TOKENS, 0)
    calls = row.get("calls")
    if calls is not None:
        _list(calls, "call usage")
        if len(calls) > row["model_calls"] or complete and len(calls) != row["model_calls"]:
            _fail("call usage count disagrees with model_calls")
        sums = dict.fromkeys(TOKENS, 0)
        for call in calls:
            usage = _object(_object(call, "call").get("usage"), "call usage")
            if set(usage) - set(TOKENS):
                _fail("unsupported token usage categories")
            for name in TOKENS:
                value = usage.get(name)
                if name in ("input_tokens", "output_tokens") or value is not None:
                    _int(value, "call " + name)
                if value is None or sums[name] is None:
                    sums[name] = None
                else:
                    sums[name] += value
            for subset, parent in (("cached_input_tokens", "input_tokens"),
                                    ("reasoning_output_tokens", "output_tokens")):
                if usage.get(subset) is not None and usage[subset] > usage[parent]:
                    _fail("call token subsets exceed parent totals")
        if counts != sums:
            _fail("run token totals disagree with call usage")
    return counts, complete


def _steps(row):
    steps = _list(row.get("steps"), "steps")
    for step in steps:
        _object(step, "step")
        _text(step.get("tool"), "step tool")
        _object(step.get("args"), "step arguments")
        if "envelope" in step:
            envelope = _object(step["envelope"], "step envelope")
            evidence = _list(envelope.get("evidence", []), "step evidence")
            ids = [_text(_object(item, "evidence").get("id"), "evidence id") for item in evidence]
            if len(ids) != len(set(ids)):
                _fail("duplicate evidence id inside a step")
    return steps


def normalize_report(report: dict) -> dict:
    _object(report, "report")
    if report.get("format_version") not in ("1", "2") or report.get("mode") != "recorded":
        _fail("only version 1 or 2 recorded evaluation reports are supported")
    version = report["format_version"]
    if version == "2":
        _hash(report.get("driver_sha256"), "driver_sha256")
    digest = content_sha256(report)
    identifier = _text(report.get("report_id"), "report_id") if version == "2" else "legacy-" + digest
    model = _text(report.get("model"), "model")
    _text(report.get("driver"), "driver")
    started = _stamp(report.get("started_at"), "started_at").astimezone(timezone.utc)
    exposure = report.get("exposure")
    if exposure not in ("focused", "all"):
        _fail("exposure must be focused or all")
    _bool(report.get("skills_enabled"), "skills_enabled")
    _bool(report.get("usage_complete"), "usage_complete")
    simulation = _bool(report.get("simulation", False), "simulation")
    fixture_ids = _fixtures(report)
    rows = _list(report.get("runs"), "runs")
    if not rows or report.get("denominator") != len(rows) or type(report.get("denominator")) is not int:
        _fail("denominator must equal the nonempty scheduled run list")
    normalized, identities, run_ids = [], set(), set()
    definitions, contexts, schedule = {}, {}, []
    for row in rows:
        _object(row, "run")
        task_id = _text(row.get("task_id"), "task_id")
        repeat = _int(row.get("repeat"), "repeat")
        if repeat < 1 or (task_id, repeat) in identities:
            _fail("duplicate or invalid task/repeat identity")
        identities.add((task_id, repeat))
        run_id = _text(row.get("run_id"), "run_id") if version == "2" else task_id + ":" + str(repeat)
        if run_id in run_ids:
            _fail("duplicate run_id")
        run_ids.add(run_id)
        status = row.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            _fail("unknown run status")
        calls = _int(row.get("model_calls"), "model_calls")
        if (status == "not_run" and calls or status in COMPLETED and not calls
                or status in {"turn_limit", "driver_or_contract_error", "driver_exceeded_token_budget"} and not calls):
            _fail("status contradicts model call attempts")
        for field in ("skill", "kind", "prompt", "mutation"):
            _text(row.get(field), field)
        _int(row.get("bench_task"), "bench_task")
        rubric = _text(row.get("rubric"), "rubric")
        task_sha, rubric_sha = content_sha256(task_binding(row)), content_sha256(rubric)
        rubric_version = row.get("rubric_version")
        if version == "2":
            _text(rubric_version, "rubric_version")
            if row.get("task_sha256") != task_sha or row.get("rubric_sha256") != rubric_sha:
                _fail("task or rubric content hash mismatch")
        definition = {"task": task_binding(row), "rubric_version": rubric_version,
                      "rubric_sha256": rubric_sha}
        if task_id in definitions and definitions[task_id] != definition:
            _fail("a repeated task changed its definition")
        definitions[task_id] = definition
        schedule.append((task_id, repeat))
        context = {name: row.get(name) for name in
                   ("skill_sha256", "tool_schema_sha256", "profiles", "visible_tools", "registry_revision")}
        if version == "2":
            for name in ("skill_sha256", "tool_schema_sha256"):
                _hash(context[name], name)
            for name in ("profiles", "visible_tools"):
                values = _list(context[name], name)
                if not values or any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
                    _fail(name + " must be nonempty and unique")
            _text(context["registry_revision"], "registry_revision")
        if version == "2" or any(value is not None for value in context.values()):
            if task_id in contexts and contexts[task_id] != context:
                _fail("a repeated task changed its visible tools, skill or registry")
            contexts[task_id] = context
        counts, usage_complete = _usage(row, report)
        completed = "answer" in row
        if completed != (status in COMPLETED):
            _fail("status contradicts presence of a completed answer")
        answer_sha, automatic = None, None
        if completed:
            from .evaluation import check_answer
            answer = row["answer"]
            answer_sha = content_sha256(answer)
            if version == "2" and row.get("answer_sha256") != answer_sha:
                _fail("answer content hash mismatch")
            automatic = check_answer(answer, _steps(row))
            if row.get("automatic_failures") != automatic:
                _fail("stored automatic checks disagree with returned evidence")
            if (status == "needs_review") != (not automatic):
                _fail("answer status disagrees with automatic checks")
        settings = row.get("sample_settings", "not reported")
        if settings != "not reported" and not isinstance(settings, dict):
            _fail("sample_settings must be an object or explicitly not reported")
        model_version = row.get("reported_model_version", "not reported")
        _text(model_version, "reported_model_version")
        call_settings = row.get("calls", [])
        if call_settings and any(c.get("model_version", "not reported") != model_version
                                 or content_sha256(c.get("settings", "not reported")) != content_sha256(settings)
                                 for c in call_settings):
            _fail("a run changed model version or settings between calls")
        normalized.append({"report_id": identifier, "report_sha256": digest, "run_id": run_id,
            "task_id": task_id, "repeat": repeat, "task_sha256": task_sha,
            "rubric_version": rubric_version, "rubric_sha256": rubric_sha,
            "answer_sha256": answer_sha, "model": model, "model_version": model_version,
            "sample_settings": settings, "status": status, "attempted": bool(calls),
            "completed": completed, "automatic_checked": automatic is not None,
            "automatic_pass": not automatic if automatic is not None else None,
            "model_calls": calls, "usage": counts, "usage_complete": usage_complete,
            "grade": None, "estimated_cost": None, "cost_reason": None,
            "answer": row.get("answer"), "prompt": row["prompt"], "rubric": rubric})
    if (report.get("completed_answers") != sum(r["completed"] for r in normalized)
            or type(report.get("completed_answers")) is not int):
        _fail("completed_answers disagrees with scheduled rows")
    if report.get("calls") != sum(r["model_calls"] for r in normalized) or type(report.get("calls")) is not int:
        _fail("report call count disagrees with runs")
    if report.get("tokens") != sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in normalized) or type(report.get("tokens")) is not int:
        _fail("report token total disagrees with runs")
    if report["usage_complete"] != all(r["usage_complete"] for r in normalized):
        _fail("report usage completeness disagrees with runs")
    for field in ("max_calls", "max_tokens"):
        if _int(report.get(field), field) < 1:
            _fail("budgets must be positive")
    if report["calls"] > report["max_calls"]:
        _fail("reported calls exceed the pre-call budget")
    if report["tokens"] > report["max_tokens"] and not any(r["status"] == "driver_exceeded_token_budget" for r in normalized):
        _fail("token budget exceeded without the corresponding failure")
    repeats = Counter(task for task, _ in schedule)
    if len(set(repeats.values())) != 1 or any(
            {repeat for task, repeat in schedule if task == name} != set(range(1, count + 1))
            for name, count in repeats.items()):
        _fail("task repeats must be complete contiguous schedules")
    attempted = [row for row in normalized if row["attempted"]]
    if version == "2":
        task_ids = _list(report.get("task_ids"), "task_ids")
        if (any(not isinstance(task, str) for task in task_ids) or len(set(task_ids)) != len(task_ids)
                or set(task_ids) != set(definitions)):
            _fail("task_ids disagree with the scheduled tasks")
        if _int(report.get("repeats"), "repeats") not in set(repeats.values()):
            _fail("the declared repeat budget disagrees with scheduled runs")
        if _int(report.get("max_turns"), "max_turns") < 1:
            _fail("max_turns must be positive")
        timeout = report.get("timeout_seconds")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            _fail("timeout_seconds must be finite and positive")
    versions = {row["model_version"] for row in attempted if row["model_version"] != "not reported"}
    settings = {content_sha256(row["sample_settings"]) for row in attempted
                if row["sample_settings"] != "not reported"}
    if len(versions) > 1 or len(settings) > 1:
        _fail("a report changed model version or settings between runs")
    skill_versions = {task: contexts.get(task, {}).get("skill_sha256") for task in definitions}
    revisions = {task: contexts.get(task, {}).get("registry_revision") for task in definitions}
    cohort = {"tasks": definitions, "schedule": sorted(schedule), "fixtures": fixture_ids,
              "skill_versions": skill_versions, "registry_revisions": revisions}
    model_version = (next(iter(versions), "not reported") if attempted
                     and all(row["model_version"] != "not reported" for row in attempted) else "not reported")
    sample_settings = (attempted[0]["sample_settings"] if attempted
                       and all(row["sample_settings"] != "not reported" for row in attempted) else "not reported")
    configuration = {"model": model, "model_version": model_version,
        "sample_settings": sample_settings,
        "driver": report["driver"], "driver_sha256": report.get("driver_sha256"), "exposure": exposure, "skills_enabled": report["skills_enabled"],
        "simulation": simulation, "budgets": {key: report.get(key) for key in
            ("max_calls", "max_tokens", "max_turns", "timeout_seconds")}, "contexts": contexts}
    unknown = [name for name, value in (("model_version", configuration["model_version"]),
               ("sample_settings", configuration["sample_settings"]),
               ("driver_sha256", configuration["driver_sha256"])) if value in (None, "not reported")]
    configuration["unknown_configuration_fields"] = unknown
    if unknown:
        configuration["unpooled_report_id"] = identifier
    return {"id": identifier, "sha256": digest, "started_at": started.isoformat(),
            "format_version": version,
            "cohort": cohort, "cohort_id": content_sha256(cohort), "configuration": configuration,
            "configuration_id": content_sha256(configuration), "runs": normalized,
            "fixtures": report["fixtures"]}


def _exact_fields(row, required, optional=()):
    _object(row, "input row")
    if set(row) - set(required) - set(optional) or set(required) - set(row):
        _fail("input row has missing or unsupported fields")


def apply_grades(reports: list[dict], document: dict | None) -> None:
    if document is None:
        return
    _exact_fields(document, ("format_version", "grades"))
    if document["format_version"] != "1":
        _fail("unsupported grade format")
    runs = {(r["report_id"], r["run_id"]): r for report in reports for r in report["runs"]}
    seen = set()
    binding = ("report_id", "report_sha256", "run_id", "task_sha256", "answer_sha256",
               "rubric_version", "rubric_sha256")
    for grade in _list(document["grades"], "grades"):
        _exact_fields(grade, (*binding, "score", "reviewer", "reviewed_at"), ("notes",))
        key = (_text(grade["report_id"], "grade report_id"), _text(grade["run_id"], "grade run_id"))
        if key in seen:
            _fail("duplicate grade for a run")
        seen.add(key)
        row = runs.get(key)
        if row is None or not row["completed"] or row["rubric_version"] is None:
            _fail("grade does not identify a versioned completed run")
        if any(grade[name] != row[name] for name in binding):
            _fail("grade binding does not match the exact report, task, rubric and answer")
        score = grade["score"]
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            _fail("review scores must be finite numbers between zero and one")
        reviewer = grade["reviewer"]
        _exact_fields(reviewer, ("kind", "id"))
        if reviewer["kind"] not in ("human", "model"):
            _fail("reviewer kind must be human or model")
        _text(reviewer["id"], "reviewer id")
        _stamp(grade["reviewed_at"], "reviewed_at")
        if "notes" in grade and not isinstance(grade["notes"], str):
            _fail("review notes must be text")
        row["grade"] = grade


def _rate(value):
    if type(value) not in (str, int, float) or isinstance(value, bool):
        _fail("prices must be finite nonnegative decimal values")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        _fail("invalid decimal price")
    if not result.is_finite() or result < 0:
        _fail("prices must be finite nonnegative decimal values")
    return result


def prices(document: dict | None) -> list[dict]:
    if document is None:
        return []
    _exact_fields(document, ("format_version", "rates"))
    if document["format_version"] != "1":
        _fail("unsupported price format")
    result, seen = [], set()
    for row in _list(document["rates"], "rates"):
        _exact_fields(row, ("model", "model_version", "effective_date", "currency",
                            "input_per_million", "output_per_million"),
                      ("cached_input_per_million", "source_note"))
        for field in ("model", "model_version"):
            _text(row[field], "price " + field)
        if "source_note" in row and not isinstance(row["source_note"], str):
            _fail("price source_note must be text")
        if row["currency"] != "USD":
            _fail("only explicitly supplied USD prices are supported")
        try:
            stamp = date.fromisoformat(row["effective_date"])
            if stamp.isoformat() != row["effective_date"]:
                raise ValueError("noncanonical date")
        except (ValueError, TypeError):
            _fail("price effective_date must be YYYY-MM-DD")
        key = (row["model"], row["model_version"], row["effective_date"])
        if key in seen:
            _fail("duplicate model/version/date price")
        seen.add(key)
        result.append({**row, **{name: (_rate(row[name]) if name != "cached_input_per_million"
                                      or row.get(name) is not None else None)
                               for name in RATE_FIELDS}})
    return result


def estimate_cost(row: dict, rates: list[dict], started_at: str):
    if not row["attempted"]:
        return Decimal(0), "unattempted; no driver call", None
    if row["model_version"] == "not reported":
        return None, "model version unknown", None
    if not row["usage_complete"] or row["usage"]["cached_input_tokens"] is None:
        return None, "usage or cached-input breakdown unknown", None
    available = [rate for rate in rates if rate["model"] == row["model"]
                 and rate["model_version"] == row["model_version"]
                 and rate["effective_date"] <= started_at[:10]]
    if not available:
        return None, "no matching dated model-version price", None
    rate = max(available, key=lambda item: item["effective_date"])
    cached = row["usage"]["cached_input_tokens"]
    if cached and rate["cached_input_per_million"] is None:
        return None, "cached-input price unknown", rate["effective_date"]
    cost = ((row["usage"]["input_tokens"] - cached) * rate["input_per_million"]
            + cached * (rate["cached_input_per_million"] or Decimal(0))
            + row["usage"]["output_tokens"] * rate["output_per_million"]) / 1_000_000
    return cost, "estimate from supplied rates; not a bill", rate["effective_date"]


def compare(reports: list[dict], *, grades: dict | None = None, price_document: dict | None = None,
            allow_multiple_cohorts: bool = False) -> tuple[dict, dict[str, bytes]]:
    normalized = [normalize_report(report) for report in reports]
    if not normalized:
        _fail("supply at least one evaluation report")
    ids, digests, executions, run_ids = set(), set(), set(), set()
    for report in normalized:
        identity_config = {key: value for key, value in report["configuration"].items()
                           if key != "unpooled_report_id"}
        execution = (report["started_at"], report["cohort_id"], content_sha256(identity_config))
        if report["id"] in ids or report["sha256"] in digests or execution in executions:
            _fail("duplicate report or execution")
        if report["format_version"] != "1":
            current_ids = {row["run_id"] for row in report["runs"]}
            if run_ids & current_ids:
                _fail("duplicate run_id across reports")
            run_ids.update(current_ids)
        ids.add(report["id"])
        digests.add(report["sha256"])
        executions.add(execution)
    cohort_ids = {report["cohort_id"] for report in normalized}
    if len(cohort_ids) > 1 and not allow_multiple_cohorts:
        _fail("task, repeat, skill, registry or fixture cohorts differ; separate them explicitly")
    apply_grades(normalized, grades)
    rate_rows = prices(price_document)
    groups = defaultdict(list)
    for report in normalized:
        for row in report["runs"]:
            cost, reason, price_date = estimate_cost(row, rate_rows, report["started_at"])
            row.update(estimated_cost=str(cost) if cost is not None else None,
                       cost_reason=reason, price_effective_date=price_date)
        groups[(report["cohort_id"], report["configuration_id"])].append(report)
    cells = []
    for (cohort_id, configuration_id), group in sorted(groups.items()):
        runs = [row for report in group for row in report["runs"]]
        grades_present = [row["grade"] for row in runs if row["grade"] is not None]
        review_kinds = {grade["reviewer"]["kind"] for grade in grades_present}
        quality_by_kind = {kind: {"reviewed": len(selected),
                                 "mean": sum(g["score"] for g in selected) / len(selected)}
                           for kind in sorted(review_kinds)
                           for selected in [[g for g in grades_present if g["reviewer"]["kind"] == kind]]}
        costs = [Decimal(row["estimated_cost"]) if row["estimated_cost"] is not None else None for row in runs]
        total_cost = sum(costs, Decimal(0)) if all(cost is not None for cost in costs) else None
        usage = {name: sum(row["usage"][name] for row in runs)
                 if all(row["usage_complete"] and row["usage"][name] is not None for row in runs) else None
                 for name in TOKENS}
        reviewed_mean = sum(grade["score"] for grade in grades_present) / len(grades_present) if len(review_kinds) == 1 else None
        cells.append({"cohort_id": cohort_id, "configuration_id": configuration_id,
            **group[0]["configuration"], "reports": len(group), "scheduled": len(runs),
            "attempted": sum(row["attempted"] for row in runs),
            "completed": sum(row["completed"] for row in runs),
            "automatic_checked": sum(row["automatic_checked"] for row in runs),
            "automatic_pass": sum(row["automatic_pass"] is True for row in runs),
            "reviewed": len(grades_present), "unreviewed_or_unrun": len(runs) - len(grades_present),
            "reviewed_quality_mean": reviewed_mean,
            "overall_quality_mean": reviewed_mean if len(grades_present) == len(runs) else None,
            "quality_by_reviewer_kind": quality_by_kind,
            "reviewers": sorted({g["reviewer"]["kind"] + ":" + g["reviewer"]["id"] for g in grades_present}),
            "status_counts": dict(sorted(Counter(row["status"] for row in runs).items())),
            "usage": usage, "estimated_cost_usd": str(total_cost) if total_cost is not None else None,
            "cost_unknown_runs": sum(cost is None for cost in costs)})
    result = {"format_version": "1", "application": "evaluation-matrix", "mode": "offline",
        "simulation": any(cell["simulation"] for cell in cells),
        "cohorts": [{"id": report["cohort_id"], **report["cohort"]} for i, report in enumerate(normalized)
                    if report["cohort_id"] not in {p["cohort_id"] for p in normalized[:i]}],
        "reports": [{key: report[key] for key in ("id", "sha256", "started_at", "fixtures", "format_version")} for report in normalized],
        "cells": cells, "runs": [row for report in normalized for row in report["runs"]],
        "supplied_prices": price_document, "limitations": [
            "Automatic checks validate answer and citation references, not scientific or semantic correctness.",
            "Reviewed means are conditional on the bound supplied reviews; unreviewed and unrun quality remains unknown.",
            "Human and model reviewer results are identified separately; mixed reviewer kinds have no pooled mean.",
            "Token usage is driver-reported. Cached input is a subset of total input; reasoning output is a subset of total output.",
            "Costs are estimates from supplied dated prices, never invoices. Missing usage or prices produce unknown costs.",
            "Different cohorts are never pooled. Simulation reports are deterministic driver demonstrations, not model performance."]}
    files = {"matrix.csv": matrix_csv(cells), "runs.csv": runs_csv(result["runs"])}
    templates = []
    for row in result["runs"]:
        if row["completed"] and row["rubric_version"] is not None and row["grade"] is None:
            templates.append({**{key: row[key] for key in
                ("report_id", "report_sha256", "run_id", "task_sha256", "answer_sha256", "rubric_version", "rubric_sha256")},
                "score": None, "reviewer": {"kind": None, "id": None}, "reviewed_at": None, "notes": ""})
    files["grades-template.json"] = json_bytes({"format_version": "1", "grades": templates})
    return result, files


def _csv(rows: list[dict]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ["no_rows"], lineterminator="\n")
    writer.writeheader()
    for row in rows:
        values = {key: ("unknown" if value is None else value) for key, value in row.items()}
        writer.writerow({key: ("'" + value if isinstance(value, str)
                                and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                         for key, value in values.items()})
    return out.getvalue().encode()


def matrix_csv(cells):
    return _csv([{**{key: cell[key] for key in (
        "cohort_id", "configuration_id", "model", "model_version", "exposure", "skills_enabled",
        "simulation", "scheduled", "attempted", "completed", "automatic_checked", "automatic_pass",
        "reviewed", "unreviewed_or_unrun", "reviewed_quality_mean", "overall_quality_mean",
        "estimated_cost_usd", "cost_unknown_runs")}, **cell["usage"]} for cell in cells])


def runs_csv(runs):
    return _csv([{**{key: row[key] for key in ("report_id", "run_id", "task_id", "repeat", "status",
        "attempted", "completed", "automatic_checked", "automatic_pass", "usage_complete", "estimated_cost", "cost_reason")},
        **row["usage"], "reviewer_kind": row["grade"]["reviewer"]["kind"] if row["grade"] else None,
        "reviewed_quality": row["grade"]["score"] if row["grade"] else None} for row in runs])


def command(args) -> int:
    try:
        result, files = compare([read_json(path) for path in args.report],
            grades=read_json(args.grades) if args.grades else None,
            price_document=read_json(args.prices) if args.prices else None,
            allow_multiple_cohorts=args.allow_multiple_cohorts)
        destination = export_bundle(args.output, result, files)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(str(exc)) from None
    print(destination)
    return 0


def register(sub):
    parser = sub.add_parser("evaluate-matrix", help="compare supplied driver reports, bound reviews and prices offline")
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--grades", type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--allow-multiple-cohorts", action="store_true", help="partition unequal cohorts; never pool them")
    parser.add_argument("--output", type=Path, required=True, help="new JSON/CSV bundle directory")
    parser.set_defaults(func=command)
