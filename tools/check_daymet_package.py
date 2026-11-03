"""Read the published temperature CSV through MLCommons' Croissant loader."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mlcroissant


def main() -> None:
    directory = Path(__file__).resolve().parents[1] / "docs/data/demos"
    metadata = directory / "daymet-calendar-croissant.json"
    resource, = json.loads(metadata.read_bytes())["distribution"]
    csv_file = directory / "daymet-calendar-temperatures.csv"
    if (resource["contentUrl"] != csv_file.name
            or hashlib.sha256(csv_file.read_bytes()).hexdigest() != resource["sha256"]):
        raise ValueError("Croissant distribution does not match the published CSV")
    report = json.loads((directory / "daymet-calendar.json").read_bytes())
    expected = [(r["date"], r["yday"], r["values"]["tmax"])
                for r in report["steps"][0]["envelope"]["data"]["days"]]
    dataset = mlcroissant.Dataset(str(metadata))
    actual = [(r["days/date"].date().isoformat(), r["days/yday"], r["days/tmax_deg_c"])
              for r in dataset.records("days")]
    if not actual or actual != expected:
        raise ValueError("Croissant reader did not recover the recorded Daymet values")
    print(f"Croissant reader recovered {len(actual)} dated temperatures; CSV checksum matches")


if __name__ == "__main__":
    main()
