"""In-process audit log: what was asked, what was reached, what came back.

Not telemetry and never network-bound. It exists so `doe-mcp tools call` can
show a caller which sources a single answer actually touched, and so a
support conversation about a wrong answer has something to read.

Log minimization: arguments are recorded as names and values for open
sources, and as names only for anything a manifest marks as needing care.
Query strings are user content; a literature search can carry an unpublished
idea in it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .envelope import Envelope, utc_now_iso


@dataclass
class AuditRecord:
    at: str
    tool: str
    args: dict[str, Any]
    outcome: str
    duration_ms: int
    source_ids: list[str] = field(default_factory=list)
    record_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    registry_revision: str | None = None


def _redact(args: dict[str, Any], redact_values: bool) -> dict[str, Any]:
    if redact_values:
        return {k: "<redacted>" for k in sorted(args)}
    return {k: args[k] for k in sorted(args)}


def record_from_envelope(tool: str, args: dict[str, Any], envelope: Envelope,
                         *, duration_ms: int,
                         redact_values: bool = False) -> AuditRecord:
    data = envelope.data
    count = data.get("record_count") if isinstance(data, dict) else None
    return AuditRecord(
        at=utc_now_iso(), tool=tool, args=_redact(args, redact_values),
        outcome=envelope.coverage.result.value, duration_ms=duration_ms,
        source_ids=[s.source_id for s in envelope.provenance],
        record_count=count if isinstance(count, int) else None,
        warnings=[w.code.value for w in envelope.warnings],
        registry_revision=(envelope.execution.registry_revision
                           if envelope.execution else None))


def error_record(*, tool: str, args: dict[str, Any], error_code: str,
                 duration_ms: int, registry_revision: str,
                 redact_values: bool = False) -> AuditRecord:
    return AuditRecord(
        at=utc_now_iso(), tool=tool, args=_redact(args, redact_values),
        outcome="error", duration_ms=duration_ms, error_code=error_code,
        registry_revision=registry_revision)


@dataclass
class AuditLog:
    limit: int = 200
    records: list[AuditRecord] = field(default_factory=list)

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)
        if len(self.records) > self.limit:
            del self.records[:-self.limit]

    def recent(self, n: int = 20) -> list[AuditRecord]:
        return self.records[-n:]
