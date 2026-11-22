"""Offline BPA envelope receipts for forecast-panel observation preparation."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from ..core.envelope import ENVELOPE_VERSION, Envelope
from ..core.errors import InvalidQuery
from ..demo_worksheets import csv_text
from . import forecast, forecast_panel as panel
from .bundle import json_bytes

MAX_ENVELOPES = 8
MEASUREMENT = dict(quantity="electric_power", unit="MW", interval_seconds=300,
                   interval_semantics="instantaneous_sample", timestamp_semantics="sample_time")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_input(path: Path) -> bytes:
    """Bound the read itself, including inputs that grow after stat."""
    with path.open("rb") as stream:
        body = stream.read(panel.MAX_BYTES + 1)
    if len(body) > panel.MAX_BYTES:
        raise InvalidQuery("BPA preparation input exceeds 20 MB")
    return body


def decode(payload: bytes):
    if len(payload) > panel.MAX_BYTES:
        raise InvalidQuery("BPA preparation input exceeds 20 MB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeDecodeError, RecursionError) as err:
        raise InvalidQuery("BPA preparation requires valid UTF-8 JSON without duplicate keys or nonfinite numbers") from err


def prepare(payloads: list[bytes], *, timezone_name: str, region_id: str = "bpat",
            availability_payload: bytes | None = None, mode: str = "observation_import") -> tuple[dict, dict[str, bytes]]:
    if not 1 <= len(payloads) <= MAX_ENVELOPES or sum(map(len, payloads)) > panel.MAX_BYTES:
        raise InvalidQuery("BPA preparation allows 1 through 8 envelopes, at most 20 MB combined")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", region_id):
        raise InvalidQuery("BPA region ID must be a simple identifier of at most 80 characters")
    try:
        if not timezone_name:
            raise ZoneInfoNotFoundError
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise InvalidQuery("BPA preparation requires an explicit valid IANA timezone assumption") from err
    files, receipts, identities, envelopes = {}, [], set(), []
    for envelope_number, payload in enumerate(payloads, 1):
        raw = decode(payload)
        try:
            env = Envelope.model_validate(raw)
        except (ValidationError, TypeError) as err:
            raise InvalidQuery("BPA preparation needs an original tool envelope, not a report or fixture") from err
        if (env.execution is None or env.execution.tool != "grid.get_bpa_operations"
                or env.execution.tool_contract_version != "1" or env.execution.envelope_version != ENVELOPE_VERSION):
            raise InvalidQuery("BPA envelope execution must identify grid.get_bpa_operations contract 1 and the supported envelope version")
        if env.coverage.sources_searched != ["bpa-operations"]:
            raise InvalidQuery("BPA envelope coverage must identify only bpa-operations as searched")
        if env.coverage.source_failures or env.coverage.execution.value != "complete":
            raise InvalidQuery("BPA preparation requires a complete successful tool execution")
        data = env.data
        if (data.get("feed") not in {"balancing_authority", "balancing_authority_interchange"}
                or not isinstance(data.get("intervals"), list) or not data["intervals"]
                or data.get("record_count") != len(data["intervals"])
                or data.get("units") != "MW" or not isinstance(data.get("columns"), list)
                or not all(isinstance(c, str) for c in data["columns"])
                or len(set(data["columns"])) != len(data["columns"]) or data["columns"].count("Load") != 1):
            raise InvalidQuery("BPA envelope has inconsistent feed, MW columns or record count")
        sources = {s.id: s for s in env.provenance}
        if len(sources) != len(env.provenance) or len({e.id for e in env.evidence}) != len(env.evidence):
            raise InvalidQuery("BPA source and evidence IDs must be unique within each envelope")
        evidence = {}
        for item in env.evidence:
            source = sources.get(item.source_ref)
            if source is None or source.source_id != "bpa-operations":
                raise InvalidQuery("BPA row evidence must reference bpa-operations provenance")
            if item.effective_at in evidence:
                raise InvalidQuery("BPA row evidence must have unique effective timestamps")
            if item.record_id != f"{data['feed']}#{item.effective_at}":
                raise InvalidQuery("BPA evidence record identity differs from its feed and timestamp")
            evidence[item.effective_at] = item
        if len(evidence) != len(data["intervals"]):
            raise InvalidQuery("BPA envelope needs exactly one bound evidence record per row")
        try:
            observations = forecast.actuals(env, timezone_name)
        except (KeyError, TypeError, AttributeError) as err:
            raise InvalidQuery("BPA envelope has malformed observation rows") from err
        filename = f"bpa-envelope-{envelope_number:02d}.json"
        files[filename] = payload
        envelope_sha256 = digest(payload)
        envelopes.append({"file": filename, "sha256": envelope_sha256, "bytes": len(payload),
                          "coverage": raw["coverage"]})
        for row_number, (interval, observation) in enumerate(zip(data["intervals"], observations, strict=True), 1):
            if (set(interval) != {"timestamp", "values"} or not isinstance(interval["values"], dict)
                    or set(interval["values"]) != set(data["columns"])):
                raise InvalidQuery("BPA observation row columns differ from envelope declaration")
            target = observation["clock"]
            if (target.second or target.microsecond or target.minute % 5
                    or (row_number > 1 and (target - observations[row_number - 2]["clock"]) % timedelta(seconds=300))):
                raise InvalidQuery("BPA observations must remain on a five-minute sample grid")
            if target in identities:
                raise InvalidQuery("duplicate BPA UTC sample identity across envelopes; overlapping or revised rows require explicit resolution")
            identities.add(target)
            item = evidence[interval["timestamp"]]
            receipts.append({"actual_row": len(receipts) + 1, "region_id": region_id,
                "target_at": target.isoformat(), "value": observation["load"],
                "raw_timestamp": interval["timestamp"], "timezone_assumption": timezone_name,
                "envelope_file": filename, "envelope_sha256": envelope_sha256, "envelope_row": row_number,
                "row_sha256": digest(json_bytes(interval)), "row_hash_encoding": "UTF-8 indented JSON with final LF; see retained envelope for exact bytes",
                "source_ref": item.source_ref, "source_id": "bpa-operations", "evidence_id": item.id,
                "record_id": item.record_id, "measurement": MEASUREMENT,
                "available_at": None, "availability_status": "unknown", "availability_evidence": None})
            if len(receipts) > panel.MAX_ROWS:
                raise InvalidQuery("BPA preparation exceeds 100000 observations")
    if availability_payload is not None:
        declared = decode(availability_payload)
        if not isinstance(declared, dict) or set(declared) != {"rows"} or not isinstance(declared["rows"], list):
            raise InvalidQuery("availability evidence needs a rows list")
        indexed = {(r["envelope_sha256"], r["evidence_id"]): r for r in receipts}
        seen = set()
        for row in declared["rows"]:
            fields = {"envelope_sha256", "evidence_id", "target_at", "available_at", "evidence"}
            if not isinstance(row, dict) or set(row) != fields or not all(isinstance(v, str) and v.strip() for v in row.values()):
                raise InvalidQuery("availability evidence rows need envelope_sha256, evidence_id, target_at, available_at and nonempty evidence text")
            key = row["envelope_sha256"], row["evidence_id"]
            receipt = indexed.get(key)
            if receipt is None or key in seen or panel.timestamp(row["target_at"]) != panel.timestamp(receipt["target_at"]):
                raise InvalidQuery("availability evidence must bind uniquely to a retained envelope, evidence ID and target")
            available = panel.timestamp(row["available_at"])
            if available < panel.timestamp(receipt["target_at"]):
                raise InvalidQuery("declared availability must be at or after the sample time")
            seen.add(key)
            receipt.update(available_at=available.isoformat(), availability_status="caller_declared_unverified",
                           availability_evidence=row["evidence"])
        files["availability-evidence.json"] = availability_payload
    files["actuals.csv"] = csv_text(panel.ACTUAL_FIELDS, [[r["region_id"], r["target_at"], r["available_at"], r["value"], *MEASUREMENT.values()] for r in receipts]).encode()
    files["observation-receipts.json"] = json_bytes(receipts)
    known = sum(r["available_at"] is not None for r in receipts)
    report = {"application": panel.NAME, "mode": mode, "status": "prepared", "steps": [], "fixtures": [],
        "provenance": {"kind": "retained_bpa_envelopes", "verification": "not_publisher_authenticated",
                       "attribution": "Caller-provided tool envelopes; structural source/evidence binding is checked, not authenticity."},
        "measurement": MEASUREMENT, "region_id": region_id, "timezone_assumption": timezone_name,
        "source_envelopes": envelopes, "observation_rows": len(receipts),
        "availability": {"known_caller_declared": known, "unknown": len(receipts) - known,
                         "issuance_time_baseline_candidates": sum(r["available_at"] is not None and r["value"] is not None for r in receipts)},
        "forecast_rows": 0, "retrieval_note": "Retrieval and Last-Modified timestamps never establish per-observation availability.",
        "inputs": [{"file": name, "sha256": digest(body), "bytes": len(body)} for name, body in files.items() if name.startswith("bpa-envelope") or name == "availability-evidence.json"],
        "limitations": ["BPA Load is a five-minute instantaneous electric-power sample in MW, not an interval mean or energy total. No aggregation or conversion is performed.",
            "Raw timestamps and an explicit caller IANA timezone assumption are retained. Ambiguous and nonexistent local times are refused.",
            "Missing availability remains unknown. Unknown rows can score ex-post actual matches, but never supply an issuance-time persistence baseline.",
            "Caller-declared availability requires retained row-bound evidence and remains unverified, not publisher-authenticated.",
            "No forecasts are generated. This observation preparation makes no claim of prospective forecast performance.",
            "Only returned rows are imported. Source coverage, missing samples and any gaps remain in retained envelopes and receipts."]}
    return report, files


async def recorded_sample(ctx, fixtures=None, *, normalize: bool = False):
    """Real recorded BPA rows, no invented forecasts or availability."""
    from .bundle import Run
    from ..workflows import freeze
    run = Run(ctx, panel.NAME, "recorded", fixtures)
    env = await run.call("grid.get_bpa_operations", intervals=24)
    raw = env.model_dump(mode="json", by_alias=True)
    # Only public-site examples normalize replay execution metadata. CLI runs
    # retain the invocation envelope; caller-provided bytes are never rewritten.
    if normalize:
        raw = freeze(raw, f"{ctx.sources.revision}T00:00:00Z")
    payload = json_bytes(raw)
    report, files = prepare([payload], timezone_name="America/Los_Angeles", mode="recorded_observation_preparation")
    report["fixtures"] = fixtures or []
    report["retrieval_note"] += (" Site-only replay retrieval clocks/request IDs are normalized for repeatability, not capture dates."
                                 if normalize else " Original replay invocation envelope retained; retrieval clocks are execution times, not capture dates.")
    return report, files
