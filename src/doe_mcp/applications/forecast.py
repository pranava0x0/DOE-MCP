"""Score submitted forecasts against timestamped BPA sample readings."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..demo_worksheets import csv_text
from ..load_baseline import _clock

FIELDS = ["model_id", "issued_at", "target_at", "prediction_mw"]
OPTIONAL = ["lower_mw", "upper_mw", "nominal_coverage"]


def scoring_status(matched: int, submitted: int) -> str:
    """Status describes forecast-to-actual matches; persistence is separate."""
    return "unscored" if matched == 0 else "scored" if matched == submitted else "partial"


def aware(stamp: str) -> datetime:
    try:
        value = datetime.fromisoformat(stamp)
    except (ValueError, TypeError) as err:
        raise InvalidQuery("forecast timestamps must be ISO 8601 with UTC offsets") from err
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidQuery("forecast timestamps need UTC offsets")
    return value.astimezone(timezone.utc)


def number(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError) as err:
        raise InvalidQuery(f"{name} must be finite numeric values") from err
    if not math.isfinite(result):
        raise InvalidQuery(f"{name} must be finite numeric values")
    return result


def parse_forecasts(payload: bytes) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not set(FIELDS) <= set(headers) or set(headers) - set(FIELDS + OPTIONAL):
            raise InvalidQuery("forecast CSV needs model_id, issued_at, target_at, prediction_mw; optional lower_mw, upper_mw, nominal_coverage")
        forecasts, identities = [], set()
        for raw in reader:
            if None in raw or any(raw.get(k) is None for k in headers):
                raise InvalidQuery("forecast CSV has a malformed row")
            row = {k: raw[k].strip() for k in FIELDS}
            if not row["model_id"]:
                raise InvalidQuery("model_id must not be empty")
            issued, target = aware(row["issued_at"]), aware(row["target_at"])
            if issued >= target:
                raise InvalidQuery("forecast issuance must precede its target")
            lead = (target - issued).total_seconds()
            identity = (row["model_id"], target, lead)
            if identity in identities:
                raise InvalidQuery("duplicate model, target and lead in forecast CSV")
            identities.add(identity)
            row.update(issued=issued, target=target, lead_seconds=lead,
                       prediction_mw=number(row["prediction_mw"], "prediction_mw"))
            bands = [raw.get(k, "").strip() for k in OPTIONAL]
            if any(bands):
                if not all(bands):
                    raise InvalidQuery("interval rows need lower_mw, upper_mw and nominal_coverage together")
                low, high, nominal = [number(v, k) for v, k in zip(bands, OPTIONAL, strict=True)]
                if low > high or not 0 < nominal < 1:
                    raise InvalidQuery("interval bounds must be ordered and nominal_coverage between zero and one")
                row.update(lower_mw=low, upper_mw=high, nominal_coverage=nominal)
            forecasts.append(row)
    except UnicodeDecodeError as err:
        raise InvalidQuery("forecast CSV must use UTF-8") from err
    if not forecasts:
        raise InvalidQuery("forecast CSV has no rows")
    return forecasts


def actuals(env, timezone_name: str | None) -> list[dict]:
    data = env.data
    if data["units"] != "MW" or data["columns"].count("Load") != 1:
        raise SourceSchemaChanged("forecast benchmark requires one BPA Load column in MW")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError as err:
        raise InvalidQuery("unknown actuals IANA timezone") from err
    evidence = {e.effective_at: e.id for e in env.evidence}
    result = []
    for interval in data["intervals"]:
        stamp = interval["timestamp"]
        clock = _clock(stamp)
        if clock.tzinfo is None:
            if zone is None:
                raise InvalidQuery("BPA timestamps lack offsets; pass --actual-timezone with an IANA timezone")
            first, second = clock.replace(tzinfo=zone, fold=0), clock.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset() or first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != clock:
                raise SourceSchemaChanged("BPA local timestamp is ambiguous or nonexistent in the supplied timezone")
            clock = first
        clock = clock.astimezone(timezone.utc)
        if result and clock <= result[-1]["clock"]:
            raise SourceSchemaChanged("BPA actual timestamps must be unique and increasing")
        value = interval["values"]["Load"]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise SourceSchemaChanged("BPA load must be finite or missing")
        if stamp not in evidence:
            raise SourceSchemaChanged("BPA actual lacks record evidence")
        result.append({"clock": clock, "load": value, "evidence_id": evidence[stamp]})
    return result


def sample_forecasts(observations: list[dict]) -> bytes:
    rows = []
    for prior, target in zip(observations[:-1], observations[1:], strict=True):
        if target["clock"] - prior["clock"] == timedelta(minutes=5) and prior["load"] is not None:
            for model, adjustment in [("retrospective-persistence", 0), ("retrospective-plus-10", 10)]:
                rows.append([model, prior["clock"].isoformat(), target["clock"].isoformat(), prior["load"] + adjustment])
    return csv_text(FIELDS, rows).encode()


def score(forecasts: list[dict], observations: list[dict]) -> dict:
    indexed = {r["clock"]: r for r in observations}
    rows, grouped = [], {}
    for forecast in forecasts:
        key = (forecast["model_id"], forecast["lead_seconds"])
        group = grouped.setdefault(key, {"model_id": key[0], "lead_seconds": key[1], "submitted": 0,
                                         "errors": [], "baseline_errors": [], "excluded": {}, "bands": {}})
        group["submitted"] += 1
        target = indexed.get(forecast["target"])
        reason = "target_not_returned" if target is None else "missing_actual" if target["load"] is None else None
        # A baseline must be observed by issuance and followed by a continuous
        # five-minute path to the target. It never reads a future observation.
        priors = [r for r in observations if r["clock"] <= forecast["issued"]]
        prior = priors[-1] if priors else None
        baseline_reason = None
        if prior is None:
            baseline_reason = "no_observation_by_issuance"
        elif prior["load"] is None:
            baseline_reason = "missing_prior_load"
        elif forecast["issued"] - prior["clock"] >= timedelta(minutes=5):
            baseline_reason = "stale_prior_observation"
        elif target is not None:
            path = [r for r in observations if prior["clock"] <= r["clock"] <= target["clock"]]
            if any(b["clock"] - a["clock"] != timedelta(minutes=5) for a, b in zip(path[:-1], path[1:], strict=True)):
                baseline_reason = "observation_gap"
            elif any(r["load"] is None for r in path):
                baseline_reason = "missing_load_on_path"
        row = {k: forecast[k] for k in FIELDS}
        row.update(lead_seconds=forecast["lead_seconds"], actual_mw=target["load"] if target else None,
                   actual_evidence_id=target["evidence_id"] if target else None,
                   excluded_reason=reason, baseline_excluded_reason=reason or baseline_reason,
                   persistence_mw=None, persistence_evidence_id=None, error_mw=None)
        if reason:
            group["excluded"][reason] = group["excluded"].get(reason, 0) + 1
        else:
            error = forecast["prediction_mw"] - target["load"]
            if not math.isfinite(error):
                raise InvalidQuery("forecast error exceeds supported numeric range")
            row["error_mw"] = error
            group["errors"].append(error)
            if baseline_reason is None:
                baseline_error = prior["load"] - target["load"]
                if not math.isfinite(baseline_error):
                    raise InvalidQuery("persistence error exceeds supported numeric range")
                group["baseline_errors"].append(baseline_error)
                row.update(persistence_mw=prior["load"], persistence_evidence_id=prior["evidence_id"])
            if "nominal_coverage" in forecast:
                band = group["bands"].setdefault(str(forecast["nominal_coverage"]), {"evaluated": 0, "hits": 0})
                band["evaluated"] += 1
                band["hits"] += forecast["lower_mw"] <= target["load"] <= forecast["upper_mw"]
                row.update({k: forecast[k] for k in OPTIONAL})
        rows.append(row)
    def metrics(errors):
        return {"matched": len(errors), "mae_mw": math.fsum(abs(e) / len(errors) for e in errors) if errors else None,
                "rmse_mw": math.hypot(*(e / math.sqrt(len(errors)) for e in errors)) if errors else None,
                "bias_mw": math.fsum(e / len(errors) for e in errors) if errors else None}
    summaries = []
    for group in grouped.values():
        group.update(metrics(group.pop("errors")))
        group["status"] = scoring_status(group["matched"], group["submitted"])
        group["persistence"] = metrics(group.pop("baseline_errors"))
        group["persistence"]["excluded"] = group["submitted"] - group["persistence"]["matched"]
        group["persistence"]["status"] = scoring_status(group["persistence"]["matched"], group["submitted"])
        for band in group["bands"].values():
            band["empirical_coverage"] = band["hits"] / band["evaluated"]
        summaries.append(group)
    matched = sum(group["matched"] for group in summaries)
    submitted = len(forecasts)
    return {"status": scoring_status(matched, submitted),
            "scoring": {"submitted": submitted, "matched": matched, "excluded": submitted - matched,
                        "status_basis": "Forecast targets matched to finite BPA actual readings; persistence coverage is reported separately."},
            "metrics": summaries, "rows": rows}


async def build(run, *, payload: bytes | None, timezone_name: str | None, intervals: int):
    demo = payload is None
    if demo:
        if run.report["mode"] != "recorded":
            raise InvalidQuery("live forecast benchmarks require --forecasts and --actual-timezone when BPA times lack offsets")
        timezone_name = timezone_name or "America/Los_Angeles"
    parsed = parse_forecasts(payload) if payload is not None else None
    env = await run.call("grid.get_bpa_operations", intervals=intervals)
    observations = actuals(env, timezone_name)
    payload = sample_forecasts(observations) if demo else payload
    result = score(parsed if parsed is not None else parse_forecasts(payload), observations)
    run.report.update(result, input_sha256=hashlib.sha256(payload).hexdigest(), actual_timezone=timezone_name,
                      retrospective_demo=demo)
    run.report["limitations"] += ["Actuals are BPA timestamped five-minute sample MW readings, not interval means.",
        "Issuance times are supplied assertions; this application cannot prove forecasts existed before their targets.",
        "Persistence uses only observations at or before issuance; gaps and unavailable targets are excluded with reasons.",
        "Scores compare each model and lead on its own matched rows; differing match sets are not a controlled model comparison."]
    if demo:
        run.report["limitations"].append("The sample forecasts were created retrospectively from recorded BPA readings; they are not publisher forecasts or evidence of prospective performance.")
    if timezone_name:
        run.report["limitations"].append("Unlabelled BPA Pacific clock readings use the supplied IANA timezone assumption; the publisher did not supply an offset for each reading.")
    columns = list(dict.fromkeys(k for row in result["rows"] for k in row))
    return {"forecasts.csv": payload, "scores.csv": csv_text(columns, [[r.get(k) for k in columns] for r in result["rows"]]).encode()}
