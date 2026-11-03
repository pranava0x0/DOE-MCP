import csv
from dataclasses import replace
from math import sqrt

import pytest

from doe_mcp.core.errors import SourceSchemaChanged, SourceUnavailable
from doe_mcp.domains.energy import get_bpa_operations
from doe_mcp.load_baseline import CASE, score_load
from doe_mcp.workflows import run_workflow, write_report


async def test_export_explains_scores_with_both_recorded_readings(ctx, tmp_path):
    report = await run_workflow(ctx, CASE)
    write_report(tmp_path, report)
    env = report["steps"][0]["envelope"]
    refs = {e["id"]: e["effective_at"] for e in env["evidence"]}
    loads = {r["timestamp"]: r["values"]["Load"] for r in env["data"]["intervals"]}
    with (tmp_path / f"{CASE}-worksheet.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    errors = []
    for row in rows:
        before, after = refs[row["Prior evidence ID"]], refs[row["Target evidence ID"]]
        assert [before, after] == [row["Prior reading (Pacific Time)"], row["Target reading (Pacific Time)"]]
        assert float(row["Prediction (MW)"]) == loads[before]
        assert float(row["Reported load (MW)"]) == loads[after]
        error = loads[before] - loads[after]
        assert float(row["Prediction minus reported (MW)"]) == error
        errors.append(error)
    assert len(errors) == 11
    scores = report["worksheet"]["scores"]
    assert scores["mae_mw"] == pytest.approx(sum(map(abs, errors)) / len(errors))
    assert scores["rmse_mw"] == pytest.approx(sqrt(sum(x*x for x in errors) / len(errors)))


async def _readings(ctx, stamps, values):
    env = await get_bpa_operations(ctx, intervals=len(stamps))
    for row, ev, stamp, load in zip(env.data["intervals"], env.evidence, stamps, values, strict=True):
        row.update(timestamp=stamp, values={"Load": load})
        ev.effective_at = stamp
        ev.record_id = env.data["feed"] + "#" + stamp
    return env


async def test_clock_rollback_needs_publisher_labels(ctx):
    stamps = ["11/01/2026 01:55 PDT", "11/01/2026 01:00 PST"]
    sheet = score_load(await _readings(ctx, stamps, [100, 90]))
    assert sheet["rows"][0][:5] == [*stamps, 100, 90, 10]
    with pytest.raises(SourceSchemaChanged, match="did not advance"):
        score_load(await _readings(ctx, [s[:-4] for s in stamps], [100, 90]))
    with pytest.raises(SourceSchemaChanged, match="mixes"):
        score_load(await _readings(ctx, [stamps[0], stamps[1][:-4]], [100, 90]))


async def test_no_bridging_missing_load_or_clock_gaps(ctx):
    stamps = ["09/03/2026 00:" + mm for mm in ["00", "05", "10", "15", "25", "30"]]
    env = await _readings(ctx, stamps, [0, 3, None, 200, 100, 96])
    sheet = score_load(env)
    assert sheet["excluded_pairs"] == {"time_gap": 1, "missing_load": 2}
    assert sheet["scores"] == pytest.approx({"pairs": 2, "mae_mw": 3.5, "rmse_mw": sqrt(12.5)})
    env.evidence.pop()
    with pytest.raises(SourceSchemaChanged, match="no BPA evidence"):
        score_load(env)


async def test_no_score_without_a_usable_pair(ctx):
    env = await _readings(ctx, ["09/03/2026 01:00", "09/03/2026 01:05"], [0, None])
    with pytest.raises(SourceUnavailable, match="no adjacent"):
        score_load(env)


async def test_source_time_range_follows_published_order_at_clock_rollback(ctx, monkeypatch):
    fetch = ctx.text_feed._fetcher.fetch_text
    first, last = "11/01/2026 01:55 PDT", "11/01/2026 01:00 PST"

    async def rollback(*args, **kwargs):
        original = await fetch(*args, **kwargs)
        text = original.text.replace("09/03/2026 01:50", first).replace("09/03/2026 01:55", last)
        return replace(original, text=text)

    monkeypatch.setattr(ctx.text_feed._fetcher, "fetch_text", rollback)
    env = await get_bpa_operations(ctx, intervals=2)
    assert (env.coverage.time_range.from_, env.coverage.time_range.to) == (first, last)
