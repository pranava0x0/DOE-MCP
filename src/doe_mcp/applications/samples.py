"""Fixed recorded application presets for the CLI and site builder."""
from __future__ import annotations

from . import dockets, forecast, weather
from .bundle import Run

APPLICATIONS = ("forecast-benchmark", "weather-package", "docket-tracker")


async def recorded_sample(ctx, name: str, fixtures: list[dict] | None = None) -> tuple[dict, dict[str, bytes]]:
    """Return a preset bundle before export, preserving the original envelopes.

    Data, scores and CSVs are deterministic. Replay retrieval clocks and
    execution request IDs describe this invocation and are not capture times.
    """
    run = Run(ctx, name, "recorded", fixtures)
    if name == "forecast-benchmark":
        files = await forecast.build(run, payload=None, timezone_name=None, intervals=24)
    elif name == "weather-package":
        files = await weather.build(run, latitude=35.9313, longitude=-84.3104,
                                    start="2024-12-26", end="2024-12-31", variables="tmax")
    elif name == "docket-tracker":
        files = await dockets.build(run, query="", docket="", since="", until="",
                                    document_type="NOTICE", rows=5, page_budget=2)
    else:
        raise ValueError("unknown application sample")
    return run.report, files
