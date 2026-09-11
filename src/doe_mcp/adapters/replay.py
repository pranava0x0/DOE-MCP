"""Replay fetcher: tests run against recorded publisher responses.

The seam exists so contract tests read what OSTI actually returned rather
than what a contributor imagined it returns. A hand-written fixture agrees
with the code that consumes it by construction, which makes it useless as a
check on either.

A fixture is recorded with `doe-mcp sources sample --record` or
`tools/record_fixtures.py`; the drift alarm is a test that replays it and
fails on SourceSchemaChanged.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SourceUnavailable
from .base import JsonResponse, TextResponse

# Government APIs return people's contact details, and this project commits
# recorded responses to a public repository. Those are two different acts:
# DOE publishing a lab contact behind its own API is not the same as this
# project republishing it in a git history that outlives the record.
#
# The first recording pass captured 25 addresses across four fixtures,
# including a named developer's PERSONAL gmail account. The fixtures exist to
# pin response SHAPE, and a shape needs a string of the right kind in the
# right place, not a real person's address. So they are redacted at record
# time, before anything reaches disk.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
REDACTED_EMAIL = "redacted@example.invalid"

# Query-parameter names that carry a credential on the APIs this project
# wraps or plans to wrap: EIA and api.data.gov both take `api_key`; token
# APIs vary. Matched case-insensitively. A parameter with one of these names
# is written to a fixture as REDACTED_SECRET and is never part of a fixture's
# identity, so a recording made with one key replays under any other key or
# under none.
CREDENTIAL_PARAMS = frozenset({"api_key", "apikey", "api-key", "token",
                               "access_token", "auth_token"})
REDACTED_SECRET = "redacted"


def is_credential_param(name: str) -> bool:
    return name.lower() in CREDENTIAL_PARAMS


def redact_pii(node: Any) -> Any:
    """Replace email addresses anywhere in a recorded payload.

    Walks the whole structure rather than a list of known field names,
    because the fields carrying addresses differ per publisher — DOE CODE
    puts them in `developers[].email` and `last_editor`, Project Open Data in
    `contactPoint.hasEmail` as a mailto: URI, and one OSTI record had one
    sitting inside a free-text `availability` sentence. A field allowlist
    would have missed that last one.
    """
    if isinstance(node, dict):
        return {k: redact_pii(v) for k, v in node.items()}
    if isinstance(node, list):
        return [redact_pii(v) for v in node]
    if isinstance(node, str):
        return _EMAIL.sub(REDACTED_EMAIL, node)
    return node


def redact_secrets(node: Any, secrets: frozenset[str]) -> Any:
    """Replace known credential VALUES wherever they appear.

    The name-based rule covers the request parameter. This covers whatever
    the publisher reflected back: EIA's v2 responses carry a `request` block
    echoing the query, and a redirect or error body can quote the URL that
    produced it. Both routes would put a key on disk without this pass.
    """
    if not secrets:
        return node
    if isinstance(node, dict):
        return {k: redact_secrets(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [redact_secrets(v, secrets) for v in node]
    if isinstance(node, str):
        out = node
        for secret in secrets:
            if secret and secret in out:
                out = out.replace(secret, REDACTED_SECRET)
        return out
    return node


def _key(url: str, params: dict[str, Any]) -> str:
    return json.dumps([url, sorted((k, str(v)) for k, v in params.items()
                                   if not is_credential_param(k))],
                      separators=(",", ":"))


@dataclass
class ReplayFetcher:
    """Serves recorded (url, params) -> body pairs. Unknown requests raise
    rather than falling through to the network: a test that silently reached
    the internet would pass or fail on someone else's uptime."""

    interactions: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "ReplayFetcher":
        doc = json.loads(path.read_text())
        return cls(interactions={
            _key(i["url"], i.get("params", {})): i
            for i in doc["interactions"]})

    async def fetch_json(self, url: str,
                         params: dict[str, Any]) -> JsonResponse:
        self.calls.append((url, dict(params)))
        hit = self.interactions.get(_key(url, params))
        if hit is None:
            raise SourceUnavailable(
                f"no recorded interaction for {url} with params "
                f"{sorted(params)}. Record one with `doe-mcp sources sample "
                "--record` rather than hand-writing the response.")
        _reraise_recorded(hit)
        return JsonResponse(payload=hit["body"],
                            headers={k.lower(): str(v) for k, v
                                     in (hit.get("headers") or {}).items()},
                            url=url)

    async def fetch_text(self, url: str,
                         params: dict[str, Any]) -> TextResponse:
        """Text interactions are recorded in the same file and keyed the same
        way; only the body's type differs. A recorded JSON body served here
        would be a fixture/adapter mismatch rather than a publisher change,
        so it is refused by name rather than coerced with str()."""
        self.calls.append((url, dict(params)))
        hit = self.interactions.get(_key(url, params))
        if hit is None:
            raise SourceUnavailable(
                f"no recorded interaction for {url} with params "
                f"{sorted(params)}. Record one with `doe-mcp sources sample "
                "--record` rather than hand-writing the response.")
        _reraise_recorded(hit)
        body = hit["body"]
        if not isinstance(body, str):
            raise SourceUnavailable(
                f"the recorded interaction for {url} holds a "
                f"{type(body).__name__} body, but a text feed was read. The "
                "fixture was recorded through the JSON path.")
        return TextResponse(text=body,
                            headers={k.lower(): str(v) for k, v
                                     in (hit.get("headers") or {}).items()},
                            url=url)


def _reraise_recorded(hit: dict[str, Any]) -> None:
    """Replay a recorded refusal as the error it was.

    An error is part of a publisher's contract, not an absence of one.
    ESS-DIVE reports an empty result set as HTTP 404 carrying "No datasets
    were found", and the adapter that reads it turns that back into an empty
    page — behaviour that could not be tested at all if the fixture could
    only hold successes. So the recorder keeps refusals and this raises them
    again with the same status.
    """
    recorded = hit.get("error")
    if recorded:
        raise SourceUnavailable(recorded.get("message", "recorded refusal"),
                                recorded.get("status"))


@dataclass
class RecordingFetcher:
    """Wraps a real fetcher and keeps what came back, for `--record`."""

    inner: Any
    interactions: list[dict[str, Any]] = field(default_factory=list)
    secrets: frozenset[str] = frozenset()
    """Every credential value the runtime holds. The name-based rule catches
    the request parameter; these catch anything the publisher echoed."""

    async def fetch_json(self, url: str,
                         params: dict[str, Any]) -> JsonResponse:
        try:
            response = await self.inner.fetch_json(url, params)
        except SourceUnavailable as refusal:
            self._record_refusal(url, params, refusal)
            raise
        # Only the headers that carry meaning. Cookies and session ids are
        # not recorded: a fixture is committed to a public repository.
        kept = {k: v for k, v in response.headers.items()
                if k in ("x-total-count", "content-type", "link")}
        recorded_params = {
            k: (REDACTED_SECRET if is_credential_param(k) else str(v))
            for k, v in params.items()}
        self.interactions.append(redact_secrets(
            {"url": url, "params": recorded_params, "headers": kept,
             "body": redact_pii(response.payload)}, self.secrets))
        # The LIVE response is returned unredacted. Redaction is about what
        # this repository republishes, not about what a caller may see from
        # the publisher's own API.
        return response

    async def fetch_text(self, url: str,
                         params: dict[str, Any]) -> TextResponse:
        """Same recording contract for a text body.

        `redact_pii` walks strings wherever it finds them, so a whole text
        body is one such string and the addresses inside it are replaced the
        same way. That is not incidental: BPA's feed header carries an
        operations mailbox on every line-one read, and it would otherwise be
        committed to this repository verbatim.
        """
        response = await self.inner.fetch_text(url, params)
        kept = {k: v for k, v in response.headers.items()
                if k in ("last-modified", "content-type", "etag")}
        recorded_params = {
            k: (REDACTED_SECRET if is_credential_param(k) else str(v))
            for k, v in params.items()}
        self.interactions.append(redact_secrets(
            {"url": url, "params": recorded_params, "headers": kept,
             "body": redact_pii(response.text)}, self.secrets))
        return response

    def _record_refusal(self, url: str, params: dict[str, Any],
                        refusal: SourceUnavailable) -> None:
        """Keep a publisher's refusal as an interaction of its own.

        Only refusals carrying a status are kept: those came from the
        publisher. A transport failure has no status, is a property of the
        network on the day of recording rather than of the service, and
        would replay as a permanent outage.
        """
        if refusal.status is None:
            return
        self.interactions.append({
            "url": url,
            "params": {k: (REDACTED_SECRET if is_credential_param(k)
                           else str(v)) for k, v in params.items()},
            "headers": {},
            "error": {"code": refusal.code, "status": refusal.status,
                      "message": str(refusal)}})

    def write(self, path: Path, note: str) -> None:
        # A fixture with nothing in it is not a fixture, and writing one
        # over a good file destroys recorded publisher bytes that cannot be
        # got back. On 2026-09-09 a full recording sweep hit a transient
        # refusal from all four OSTI APIs and wrote four empty files over
        # 90 KB of recordings, taking 25 tests with them. The recorder is
        # the one tool here that can lose data, so it refuses rather than
        # truncates.
        if not self.interactions:
            raise ValueError(
                f"refusing to write {path}: no interactions were recorded. "
                "The previous fixture is left alone; a publisher that "
                "refused every request is an outage to wait out, not a "
                "reason to overwrite what was recorded when it was up.")
        text = json.dumps(
            {"note": note,
             "redaction": f"Email addresses replaced with "
                          f"{REDACTED_EMAIL} and credential values with "
                          f"{REDACTED_SECRET!r} at record time. Field shapes "
                          "and lengths are otherwise the publisher's.",
             "interactions": self.interactions},
            indent=2, sort_keys=False) + "\n"
        # The tripwire behind the two redaction passes. If a key reached the
        # serialized text anyway, nothing is written and the run fails loudly.
        if any(secret and secret in text for secret in self.secrets):
            raise ValueError(
                f"refusing to write {path}: a credential value survived "
                "redaction. Nothing was written.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def record_through(ctx: Any, inner: Any) -> RecordingFetcher:
    """Route every network adapter on a runtime context through one recorder.

    Adapters are discovered structurally — every field on the context whose
    value has a `_fetcher` seam — rather than listed. The listed version of
    this lived in two places, and both omitted the EIA adapter: the one
    adapter whose requests carry a credential. Adding it to either list
    without the redaction above would have written the key into a fixture.

    Each installed adapter's cache is cleared, because a recording that is
    served from cache records nothing.
    """
    recorder = RecordingFetcher(
        inner=inner,
        secrets=frozenset(v for v in ctx.credentials.values.values() if v))
    installed = 0
    for spec in dataclasses.fields(ctx):
        adapter = getattr(ctx, spec.name)
        if not hasattr(adapter, "_fetcher"):
            continue
        adapter._fetcher = recorder                # noqa: SLF001
        cache = getattr(adapter, "_cache", None)
        if cache is not None:
            cache.clear()
        installed += 1
    if not installed:
        raise ValueError("no adapter on this context has a fetcher seam")
    return recorder
