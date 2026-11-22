"""Fair forecast cohorts from declared CSV inputs; no publisher fetches."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import math

from ..core.errors import InvalidQuery
from ..demo_worksheets import csv_text
from .bundle import json_bytes
from .forecast import number, scoring_status

NAME = "forecast-panel"
MEASUREMENT = ["quantity", "unit", "interval_seconds", "interval_semantics", "timestamp_semantics"]
ACTUAL_FIELDS = ["region_id", "target_at", "available_at", "value", *MEASUREMENT]
FORECAST_FIELDS = ["model_id", "region_id", "issued_at", "target_at", "prediction", *MEASUREMENT]
MAX_BYTES = 20_000_000
MAX_ROWS = 100_000


def timestamp(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as err:
        raise InvalidQuery("panel timestamps must be ISO 8601 with explicit UTC offsets") from err


def stamp(value: datetime) -> str:
    return value.isoformat()


def validate_plan(payload: bytes) -> tuple[dict, list[datetime]]:
    import json
    if len(payload) > MAX_BYTES:
        raise InvalidQuery("panel plan exceeds the input size limit")
    try:
        plan = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as err:
        raise InvalidQuery("panel plan must be UTF-8 JSON") from err
    keys = {"data_kind", "cohort_mode", "models", "regions", "lead_seconds", "window", "measurement", "baseline_max_age_seconds"}
    if not isinstance(plan, dict) or set(plan) != keys:
        raise InvalidQuery("panel plan requires data_kind, cohort_mode, models, regions, lead_seconds, window, measurement and baseline_max_age_seconds")
    if (not isinstance(plan["data_kind"], str) or not isinstance(plan["cohort_mode"], str)
            or plan["data_kind"] not in {"simulation", "user_supplied"} or plan["cohort_mode"] not in {"within_region", "cross_region_intersection"}):
        raise InvalidQuery("panel data_kind must be simulation or user_supplied; cohort_mode must be within_region or cross_region_intersection")
    for field, minimum, maximum in [("models", 2, 12), ("regions", 1, 8)]:
        entries = plan[field]
        if not isinstance(entries, list) or not minimum <= len(entries) <= maximum:
            raise InvalidQuery(f"panel {field} needs {minimum} through {maximum} declarations")
        ids = set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"id", "label", "attribution"}
                    or not all(isinstance(v, str) and v.strip() for v in entry.values())
                    or len(entry["id"]) > 80 or entry["id"] in ids):
                raise InvalidQuery(f"panel {field} need unique IDs, nonempty labels and source attribution")
            ids.add(entry["id"])
    measure = plan["measurement"]
    if not isinstance(measure, dict) or set(measure) != set(MEASUREMENT):
        raise InvalidQuery("panel measurement must declare quantity, unit, interval_seconds, interval_semantics and timestamp_semantics")
    if not all(isinstance(measure[k], str) for k in MEASUREMENT if k != "interval_seconds"):
        raise InvalidQuery("panel measurement labels must be text")
    basis = (measure["quantity"], measure["unit"], measure["interval_semantics"], measure["timestamp_semantics"])
    if basis not in {("electric_power", "MW", "instantaneous_sample", "sample_time"),
                     ("electric_power", "MW", "interval_mean", "interval_end"),
                     ("electric_energy", "MWh", "interval_total", "interval_end")}:
        raise InvalidQuery("panel supports MW power samples, MW interval means or MWh interval totals with explicit timestamp semantics; incompatible quantities cannot be mixed")
    step = measure["interval_seconds"]
    if type(step) is not int or not 1 <= step <= 86400:
        raise InvalidQuery("measurement interval_seconds must be an integer from 1 through 86400")
    age = plan["baseline_max_age_seconds"]
    if type(age) is not int or not 0 <= age <= 604800:
        raise InvalidQuery("baseline_max_age_seconds must be an integer from zero through 604800")
    leads = plan["lead_seconds"]
    if (not isinstance(leads, list) or not 1 <= len(leads) <= 8
            or any(type(v) is not int or not step <= v <= 604800 or v % step for v in leads)
            or len(set(leads)) != len(leads)):
        raise InvalidQuery("panel needs 1 through 8 unique positive lead_seconds, each a cadence multiple no larger than seven days")
    window = plan["window"]
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise InvalidQuery("panel window must declare inclusive start and end timestamps")
    start, end = timestamp(window["start"]), timestamp(window["end"])
    try:
        start - timedelta(seconds=max(leads))
    except OverflowError as err:
        raise InvalidQuery("panel issuance times fall outside the supported calendar") from err
    span = end - start
    cadence = timedelta(seconds=step)
    if span < timedelta(0) or span % cadence:
        raise InvalidQuery("panel window must advance by whole measurement intervals")
    count = span // cadence + 1
    if count > 2000 or count * len(plan["models"]) * len(plan["regions"]) * len(leads) > MAX_ROWS:
        raise InvalidQuery("panel exceeds 2000 targets or 100000 model/region/lead/target slots")
    return plan, [start + i * cadence for i in range(count)]


def _csv(payload: bytes, fields: list[str]):
    if len(payload) > MAX_BYTES:
        raise InvalidQuery("panel CSV exceeds the 20 MB input limit")
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(fields) or set(reader.fieldnames) != set(fields):
            raise InvalidQuery("panel CSV columns must be exactly: " + ",".join(fields))
        for index, row in enumerate(reader, 1):
            if index > MAX_ROWS or None in row or any(v is None for v in row.values()):
                raise InvalidQuery("panel CSV has malformed rows or exceeds 100000 rows")
            yield index, {key: value.strip() for key, value in row.items()}
    except (UnicodeDecodeError, csv.Error) as err:
        raise InvalidQuery("panel CSV must contain valid UTF-8 and CSV quoting") from err


def parse_inputs(plan: dict, actual_payload: bytes, forecast_payload: bytes) -> tuple[dict, dict, list[dict]]:
    regions = {r["id"] for r in plan["regions"]}
    models = {m["id"] for m in plan["models"]}
    expected_measure = {k: str(v) for k, v in plan["measurement"].items()}
    actuals, forecasts, input_exclusions = {}, {}, []
    start, end = timestamp(plan["window"]["start"]), timestamp(plan["window"]["end"])
    cadence = timedelta(seconds=plan["measurement"]["interval_seconds"])
    for kind, payload, fields in [("actuals", actual_payload, ACTUAL_FIELDS), ("forecasts", forecast_payload, FORECAST_FIELDS)]:
        for row_number, row in _csv(payload, fields):
            if row["region_id"] not in regions or (kind == "forecasts" and row["model_id"] not in models):
                raise InvalidQuery("CSV names a region or model absent from the panel declaration")
            if {k: row[k] for k in MEASUREMENT} != expected_measure:
                raise InvalidQuery("CSV quantity, unit or interval semantics differ from the panel declaration; no conversion is performed")
            target = timestamp(row["target_at"])
            value = number(row["value" if kind == "actuals" else "prediction"], "panel values") if row["value" if kind == "actuals" else "prediction"] else None
            record = {"row_number": row_number, "target": target, "value": value, "region_id": row["region_id"]}
            if kind == "actuals":
                available = timestamp(row["available_at"]) if row["available_at"] else None
                if available is not None and available < target:
                    raise InvalidQuery("actual available_at must be at or after its sample time or interval end")
                key = (row["region_id"], target)
                record["available"] = available
                if key in actuals:
                    raise InvalidQuery("actual region/target identities must be unique, including equivalent UTC offsets")
                actuals[key] = record
            else:
                issued = timestamp(row["issued_at"])
                lead = next((v for v in plan["lead_seconds"] if target - issued == timedelta(seconds=v)), None)
                if lead is None:
                    raise InvalidQuery("forecast issuance must precede its target by a declared lead_seconds")
                key = (row["model_id"], row["region_id"], lead, target)
                if key in forecasts:
                    raise InvalidQuery("forecast model/region/lead/target identities must be unique")
                record.update(model_id=row["model_id"], issued=issued, lead_seconds=lead)
                forecasts[key] = record
                if not start <= target <= end or (target - start) % cadence:
                    input_exclusions.append({"file": "forecasts.csv", "row_number": row_number,
                                             "reason": "outside_declared_window" if not start <= target <= end else "target_off_declared_grid"})
    return actuals, forecasts, input_exclusions


def _metrics(errors: list[float]) -> dict:
    count = len(errors)
    return {"count": count, "mae": math.fsum(abs(e) / count for e in errors) if count else None,
            "rmse": math.hypot(*(e / math.sqrt(count) for e in errors)) if count else None,
            "bias": math.fsum(e / count for e in errors) if count else None}


def _error(prediction, actual):
    error = prediction - actual
    if not math.isfinite(error):
        raise InvalidQuery("panel error exceeds the supported numeric range")
    return error


def evaluate(plan: dict, targets: list[datetime], actuals: dict, forecasts: dict) -> dict:
    models = [m["id"] for m in plan["models"]]
    regions = [r["id"] for r in plan["regions"]]
    # Sweep availability once per region. available >= effective was checked
    # on import, so every candidate is both effective and known by issuance.
    issuances = sorted({target - timedelta(seconds=lead) for target in targets for lead in plan["lead_seconds"]})
    priors = {}
    for region in regions:
        history = sorted((r for (rid, _), r in actuals.items() if rid == region and r["value"] is not None and r["available"] is not None), key=lambda r: r["available"])
        cursor, latest = 0, None
        for issued in issuances:
            while cursor < len(history) and history[cursor]["available"] <= issued:
                item = history[cursor]
                if latest is None or item["target"] > latest["target"]:
                    latest = item
                cursor += 1
            priors[region, issued] = latest
    candidates, local = {}, {}
    for region in regions:
        for lead in plan["lead_seconds"]:
            local[region, lead] = set()
            for target in targets:
                reasons = []
                actual = actuals.get((region, target))
                if actual is None or actual["value"] is None:
                    reasons.append("missing_actual" if actual else "actual_not_supplied")
                for model in models:
                    forecast = forecasts.get((model, region, lead, target))
                    if forecast is None or forecast["value"] is None:
                        reasons.append(("missing_prediction:" if forecast else "missing_forecast:") + model)
                issued = target - timedelta(seconds=lead)
                prior = priors[region, issued]
                if prior is None:
                    reasons.append("no_actual_available_by_issuance")
                elif issued - prior["target"] > timedelta(seconds=plan["baseline_max_age_seconds"]):
                    reasons.append("stale_baseline_observation")
                candidates[region, lead, target] = {"actual": actual, "prior": prior, "reasons": reasons}
                if not reasons:
                    local[region, lead].add(target)
    shared = {lead: set.intersection(*(local[region, lead] for region in regions)) for lead in plan["lead_seconds"]}
    cohorts, rows, metrics, used_actuals = [], [], [], set()
    for region in regions:
        for lead in plan["lead_seconds"]:
            included = shared[lead] if plan["cohort_mode"] == "cross_region_intersection" else local[region, lead]
            status = scoring_status(len(included), len(targets))
            exclusions = []
            errors = {model: [] for model in models}
            baseline_errors = []
            for target in targets:
                candidate = candidates[region, lead, target]
                reasons = list(candidate["reasons"])
                blocking = [rid for rid in regions if target not in local[rid, lead]] if plan["cohort_mode"] == "cross_region_intersection" else []
                if blocking and not reasons:
                    reasons.append("outside_cross_region_intersection")
                selected = target in included
                if not selected:
                    exclusions.append({"target_at": stamp(target), "reasons": reasons, "blocking_regions": blocking})
                actual, prior = candidate["actual"], candidate["prior"]
                if selected:
                    baseline_error = _error(prior["value"], actual["value"])
                    baseline_errors.append(baseline_error)
                    used_actuals.update([actual["row_number"], prior["row_number"]])
                else:
                    baseline_error = None
                for model in models:
                    forecast = forecasts.get((model, region, lead, target))
                    error = _error(forecast["value"], actual["value"]) if selected else None
                    if selected:
                        errors[model].append(error)
                    rows.append({"region_id": region, "model_id": model, "lead_seconds": lead,
                                 "target_at": stamp(target), "issued_at": stamp(target - timedelta(seconds=lead)),
                                 "included": selected, "reasons": reasons, "blocking_regions": blocking,
                                 "prediction": forecast["value"] if forecast else None,
                                 "actual": actual["value"] if actual else None,
                                 "forecast_row": forecast["row_number"] if forecast else None,
                                 "actual_row": actual["row_number"] if actual else None,
                                 "persistence": prior["value"] if prior else None,
                                 "baseline_target_at": stamp(prior["target"]) if prior else None,
                                 "baseline_available_at": stamp(prior["available"]) if prior else None,
                                 "baseline_actual_row": prior["row_number"] if prior else None,
                                 "error": error, "persistence_error": baseline_error})
            cohorts.append({"region_id": region, "lead_seconds": lead, "status": status,
                            "expected_targets": len(targets), "eligible_before_region_intersection": len(local[region, lead]),
                            "included_targets": [stamp(t) for t in targets if t in included], "exclusions": exclusions})
            for model in [*models, None]:
                metrics.append({"region_id": region, "lead_seconds": lead, "kind": "model" if model is not None else "persistence",
                                "model_id": model, "status": status, "unit": plan["measurement"]["unit"],
                                **_metrics(errors[model] if model is not None else baseline_errors)})
    # Outcome matching does not require historical availability. Keep this
    # separately labelled cohort apart from the model+persistence comparison.
    ex_post_local = {(region, lead): {target for target in targets
        if not [reason for reason in candidates[region, lead, target]["reasons"]
                if reason not in {"no_actual_available_by_issuance", "stale_baseline_observation"}]}
        for region in regions for lead in plan["lead_seconds"]}
    ex_post_rows, ex_post_metrics, ex_post_cohorts, used_ex_post = [], [], [], set()
    for lead in plan["lead_seconds"]:
        intersection = set.intersection(*(ex_post_local[region, lead] for region in regions))
        for region in regions:
            selected_targets = intersection if plan["cohort_mode"] == "cross_region_intersection" else ex_post_local[region, lead]
            ex_post_cohorts.append({"region_id": region, "lead_seconds": lead,
                                   "expected_targets": len(targets), "included_targets": [stamp(t) for t in targets if t in selected_targets]})
            for model in models:
                model_errors = []
                for target in targets:
                    actual = actuals.get((region, target))
                    forecast = forecasts.get((model, region, lead, target))
                    selected = target in selected_targets
                    error = _error(forecast["value"], actual["value"]) if selected else None
                    if selected:
                        model_errors.append(error)
                        used_ex_post.add(actual["row_number"])
                    ex_post_rows.append({"region_id": region, "model_id": model, "lead_seconds": lead,
                        "target_at": stamp(target), "included": selected, "error": error,
                        "actual_row": actual["row_number"] if actual else None,
                        "forecast_row": forecast["row_number"] if forecast else None,
                        "actual_available_at": stamp(actual["available"]) if actual and actual["available"] is not None else None})
                ex_post_metrics.append({"region_id": region, "model_id": model, "lead_seconds": lead,
                    "status": scoring_status(len(selected_targets), len(targets)), "unit": plan["measurement"]["unit"], **_metrics(model_errors)})
    included_slots = sum(len(c["included_targets"]) for c in cohorts)
    total_slots = len(cohorts) * len(targets)
    return {"status": scoring_status(included_slots, total_slots), "cohorts": cohorts, "metrics": metrics, "rows": rows,
            "ex_post_model_only": {"cohort_basis": "Common finite outcome and all-model forecast targets; availability and persistence are not eligibility requirements.",
                "comparison_note": "Different cohort from model+persistence metrics; do not compare these metrics directly with persistence or infer prospective performance.",
                "cohorts": ex_post_cohorts, "metrics": ex_post_metrics, "rows": ex_post_rows, "ranking": "not_performed"},
            "coverage": {"included_region_lead_targets": included_slots, "expected_region_lead_targets": total_slots},
            "actual_row_usage": [{"row_number": r["row_number"], "region_id": r["region_id"], "target_at": stamp(r["target"]),
                                  "used_for_scoring_or_baseline": r["row_number"] in used_actuals or r["row_number"] in used_ex_post,
                                  "used_for_common_cohort_or_baseline": r["row_number"] in used_actuals,
                                  "used_for_ex_post_model_only": r["row_number"] in used_ex_post,
                                  "reason_if_unused": None if r["row_number"] in used_actuals or r["row_number"] in used_ex_post else "not_used_by_selected_cohorts"}
                                 for r in actuals.values()], "ranking": "not_performed"}


def _table(rows: list[dict], fields: list[str]) -> bytes:
    return csv_text(fields, [[json_bytes(row.get(k)).decode().strip() if isinstance(row.get(k), (list, dict)) else row.get(k) for k in fields] for row in rows]).encode()


def build(plan_payload: bytes, actual_payload: bytes, forecast_payload: bytes) -> tuple[dict, dict[str, bytes]]:
    plan, targets = validate_plan(plan_payload)
    actuals, forecasts, input_exclusions = parse_inputs(plan, actual_payload, forecast_payload)
    result = evaluate(plan, targets, actuals, forecasts)
    mode = "simulation" if plan["data_kind"] == "simulation" else "user_import"
    files = {"plan.json": plan_payload, "actuals.csv": actual_payload, "forecasts.csv": forecast_payload}
    report = {"application": NAME, "mode": mode, "plan": plan, "steps": [], "fixtures": [],
              "provenance": {"kind": mode, "verification": "not_publisher_verified",
                             "attribution": "Caller-declared region/model attribution and input bytes; no publisher API was queried."},
              "retrieval_note": "Imported or generated input bytes have no publisher retrieval timestamp. Availability and issuance timestamps are supplied assertions.",
              "inputs": [{"file": filename, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()} for filename, body in files.items()],
              **result, "input_exclusions": input_exclusions,
              "limitations": ["This panel compares declared inputs, not publisher-verified observations. A simulation run does not measure real-world model performance.",
                  "Every compared model and persistence use the identical eligible target set within each region and lead. Cross-region intersection mode additionally uses the same targets in every declared region.",
                  "Within-region mode permits different region cohorts. No cross-region aggregate score or ranking is produced in either mode.",
                  "Blank availability is unknown and excluded from issuance-time baselines. Separate ex-post model-only metrics use common actual/forecast targets and must not be compared directly to persistence metrics on a different cohort.",
                  "Availability and issuance timestamps are assertions in the supplied files; their historical truth cannot be established from a CSV.",
                  "Persistence uses only observations effective and available at or before issuance, within the declared maximum age. Later observations never set its prediction.",
                  "Quantity, unit, cadence, interval meaning and timestamp meaning must match the declaration. Power samples, interval means and energy totals cannot be mixed; no unit conversion or temporal aggregation is performed.",
                  "Missing declarations are refused. Missing values, forecasts and observations remain explicit cohort exclusions; a region with no common targets stays in the report with null metrics."]}
    included_rows = [{"region_id": c["region_id"], "lead_seconds": c["lead_seconds"], "target_at": t} for c in result["cohorts"] for t in c["included_targets"]]
    excluded_rows = [row for row in result["rows"] if not row["included"]]
    files.update({"ex-post-model-only.json": json_bytes(result["ex_post_model_only"]), "cohorts.json": json_bytes(result["cohorts"]),
                  "included-targets.csv": _table(included_rows, ["region_id", "lead_seconds", "target_at"]),
                  "metrics.csv": _table(result["metrics"], ["region_id", "lead_seconds", "kind", "model_id", "status", "unit", "count", "mae", "rmse", "bias"]),
                  "scored-rows.csv": _table(result["rows"], list(result["rows"][0])),
                  "row-exclusions.csv": _table(excluded_rows, ["region_id", "model_id", "lead_seconds", "target_at", "reasons", "blocking_regions", "forecast_row", "actual_row"]),
                  "input-exclusions.csv": _table(input_exclusions, ["file", "row_number", "reason"])})
    return report, files


def simulation_sample() -> tuple[dict, dict[str, bytes]]:
    """Deterministic arithmetic simulation; neither real regions nor fixtures."""
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    measure = dict(quantity="electric_power", unit="MW", interval_seconds=300,
                   interval_semantics="instantaneous_sample", timestamp_semantics="sample_time")
    plan = {"data_kind": "simulation", "cohort_mode": "cross_region_intersection",
            "models": [{"id": name, "label": name, "attribution": "Deterministic illustrative simulation, not an issued real-world forecast."} for name in ["sim-trend", "sim-offset"]],
            "regions": [{"id": name, "label": name, "attribution": "Fictitious region with generated MW samples; no actual balancing authority or publisher."} for name in ["sim-east", "sim-west"]],
            "lead_seconds": [300, 600], "window": {"start": stamp(origin + timedelta(minutes=10)), "end": stamp(origin + timedelta(minutes=65))},
            "measurement": measure, "baseline_max_age_seconds": 300}
    actual_rows, forecast_rows = [], []
    for region_number, region in enumerate(plan["regions"]):
        for i in range(14):
            target = origin + timedelta(minutes=5 * i)
            value = 100 + region_number * 100 + 3 * i + i % 3
            available = target + timedelta(minutes=10) if region_number == 1 and i == 4 else target
            actual_rows.append([region["id"], stamp(target), stamp(available), None if region_number == 1 and i == 9 else value, *measure.values()])
            if i < 2:
                continue
            for lead in plan["lead_seconds"]:
                for model_number, model in enumerate(plan["models"]):
                    if region_number == 0 and i == 6 and lead == 600 and model_number == 1:
                        continue
                    prediction = 100 + region_number * 100 + 3 * (i - 1) + model_number * 4
                    forecast_rows.append([model["id"], region["id"], stamp(target - timedelta(seconds=lead)), stamp(target), prediction, *measure.values()])
    return build(json_bytes(plan), csv_text(ACTUAL_FIELDS, actual_rows).encode(), csv_text(FORECAST_FIELDS, forecast_rows).encode())
