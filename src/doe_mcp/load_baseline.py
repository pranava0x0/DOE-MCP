"""Retrospective one-step persistence scores for BPA load readings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import pairwise
from math import isfinite, sqrt
from statistics import mean

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


def score_load(env: Envelope) -> dict:
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

    readings = []
    for interval in data["intervals"]:
        stamp = interval["timestamp"]
        local = _clock(stamp)
        if readings and (local.tzinfo is None) != (readings[-1][0].tzinfo is None):
            raise SourceSchemaChanged("BPA mixes labelled and unlabelled times; cannot compare intervals")
        if readings and local <= readings[-1][0]:
            raise SourceSchemaChanged("BPA local timestamps did not advance; cannot order the readings")
        if stamp not in evidence:
            raise SourceSchemaChanged("no BPA evidence for a load reading")
        load = interval["values"]["Load"]
        if load is not None and (type(load) not in (int, float) or not isfinite(load)):
            raise SourceSchemaChanged("non-numeric or non-finite BPA load")
        readings.append((local, stamp, load, evidence[stamp]))

    rows = []
    gaps = missing = 0
    errors = []
    for before, after in pairwise(readings):
        if after[0] - before[0] != timedelta(minutes=5):
            gaps += 1
        elif before[2] is None or after[2] is None:
            missing += 1
        else:
            error = before[2] - after[2]
            errors.append(error)
            rows.append([before[1], after[1], before[2], after[2], error, before[3], after[3]])
    if not rows:
        raise SourceUnavailable("no adjacent five-minute BPA readings with load to score")
    return {
        "caption": "Five-minute load predictions and errors",
        "columns": ["Prior reading (Pacific Time)", "Target reading (Pacific Time)", "Prediction (MW)",
                    "Reported load (MW)", "Prediction minus reported (MW)", "Prior evidence ID", "Target evidence ID"],
        "rows": rows,
        "scores": {"pairs": len(rows), "mae_mw": mean(abs(e) for e in errors),
                   "rmse_mw": sqrt(mean(e * e for e in errors))},
        "excluded_pairs": {"time_gap": gaps, "missing_load": missing},
    }
