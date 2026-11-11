"""Retrospective one-step persistence scores for BPA load readings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from math import ceil, hypot, isfinite, sqrt
from statistics import mean
from typing import NamedTuple

from .core.envelope import Envelope
from .core.errors import SourceSchemaChanged, SourceUnavailable

CASE = "load-baseline"


def _clock(stamp: str) -> datetime:
    """Use an offset only when the publisher labels the individual reading."""
    clock, space, label = stamp.rpartition(" ")
    offsets = {"PDT": -7, "PST": -8}
    try:
        if space and label in offsets:
            return datetime.strptime(clock, "%m/%d/%Y %H:%M").replace(
                tzinfo=timezone(timedelta(hours=offsets[label])))
        return datetime.strptime(stamp, "%m/%d/%Y %H:%M")
    except ValueError as err:
        raise SourceSchemaChanged("unrecognized BPA local timestamp") from err


@dataclass(frozen=True)
class Reading:
    clock: datetime
    stamp: str
    load: int | float | None
    evidence_id: str


class LoadPair(NamedTuple):
    prior: str
    target: str
    prediction: int | float
    actual: int | float
    error: int | float
    prior_id: str
    target_id: str


def _load_pairs(env: Envelope) -> tuple[list[LoadPair], dict[str, int]]:
    data = env.data
    if data["units"] != "MW" or data["columns"].count("Load") != 1:
        raise SourceSchemaChanged("the BPA baseline needs one Load column in MW")
    sources = {s.id for s in env.provenance if s.source_id == "bpa-operations"}
    evidence = {}
    for entry in env.evidence:
        if entry.source_ref in sources:
            if entry.effective_at in evidence:
                raise SourceSchemaChanged("BPA local timestamps repeat; cannot identify a unique reading")
            evidence[entry.effective_at] = entry.id

    readings: list[Reading] = []
    for interval in data["intervals"]:
        stamp = interval["timestamp"]
        local = _clock(stamp)
        if readings and (local.tzinfo is None) != (readings[-1].clock.tzinfo is None):
            raise SourceSchemaChanged("BPA mixes labelled and unlabelled times; cannot compare intervals")
        if readings and local <= readings[-1].clock:
            raise SourceSchemaChanged("BPA local timestamps did not advance; cannot order the readings")
        if stamp not in evidence:
            raise SourceSchemaChanged("no BPA evidence for a load reading")
        load = interval["values"]["Load"]
        if load is not None and (type(load) not in (int, float) or not isfinite(load)):
            raise SourceSchemaChanged("non-numeric or non-finite BPA load")
        readings.append(Reading(local, stamp, load, evidence[stamp]))

    rows = []
    gaps = missing = 0
    for before, after in pairwise(readings):
        if after.clock - before.clock != timedelta(minutes=5):
            gaps += 1
        elif before.load is None or after.load is None:
            missing += 1
        else:
            error = before.load - after.load
            if not isfinite(error):
                raise SourceSchemaChanged("BPA errors exceed the supported numeric range")
            rows.append(LoadPair(before.stamp, after.stamp, before.load, after.load, error, before.evidence_id, after.evidence_id))
    if not rows:
        raise SourceUnavailable("no adjacent five-minute BPA readings with load to score")
    return rows, {"time_gap": gaps, "missing_load": missing}


def score_load(env: Envelope) -> dict:
    rows, excluded = _load_pairs(env)
    errors = [r.error for r in rows]
    mae = mean(abs(e) for e in errors)
    rmse = hypot(*(e / sqrt(len(errors)) for e in errors))
    if not isfinite(mae) or not isfinite(rmse):
        raise SourceSchemaChanged("BPA errors exceed the supported numeric range")
    return {
        "caption": "Five-minute load predictions and errors",
        "columns": ["Prior reading (Pacific Time)", "Target reading (Pacific Time)", "Prediction (MW)",
                    "Reported load (MW)", "Prediction minus reported (MW)", "Prior evidence ID", "Target evidence ID"],
        "rows": [list(r) for r in rows],
        "scores": {"pairs": len(rows), "mae_mw": mae, "rmse_mw": rmse},
        "excluded_pairs": excluded,
    }


INTERVAL_CASE = "load-interval-check"
CALIBRATION_PAIRS = 15
HOLDOUT_PAIRS = 8
INTERVAL_READINGS = CALIBRATION_PAIRS + HOLDOUT_PAIRS + 1


def check_intervals(env: Envelope) -> dict:
    """Fit an empirical radius once; later targets only score the frozen band."""
    pairs, _ = _load_pairs(env)
    if len(env.data["intervals"]) != INTERVAL_READINGS or len(pairs) != INTERVAL_READINGS - 1:
        raise SourceUnavailable(f"interval check needs {INTERVAL_READINGS} consecutive populated five-minute load readings")
    rank = ceil(0.90 * CALIBRATION_PAIRS)
    radius = sorted(abs(row.error) for row in pairs[:CALIBRATION_PAIRS])[rank - 1]
    rows = []
    hits = 0
    for i, (prior, target, prediction, actual, error, prior_id, target_id) in enumerate(pairs):
        calibration = i < CALIBRATION_PAIRS
        low = high = inside = None
        if not calibration:
            low, high = prediction - radius, prediction + radius
            if not isfinite(low) or not isfinite(high):
                raise SourceSchemaChanged("BPA prediction band exceeds the supported numeric range")
            inside = low <= actual <= high
            hits += inside
        rows.append(["calibration" if calibration else "holdout", prior, target, prediction, actual,
                     abs(error), low, high, inside, prior_id, target_id])
    return {"caption": "Five-minute load prediction bands",
            "columns": ["Phase", "Prior reading (Pacific Time)", "Target reading (Pacific Time)",
                        "Prediction (MW)", "Reported load (MW)", "Absolute error (MW)",
                        "Lower bound (MW)", "Upper bound (MW)", "Inside band",
                        "Prior evidence ID", "Target evidence ID"],
            "rows": rows, "scores": {"calibration_pairs": CALIBRATION_PAIRS, "radius_rank": rank,
                                     "radius_mw": radius, "holdout_pairs": HOLDOUT_PAIRS, "hits": hits}}
