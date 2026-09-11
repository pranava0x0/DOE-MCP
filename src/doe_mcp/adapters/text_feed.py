"""The `text_feed` adapter: tab-delimited operational text files.

Not every publisher in this ecosystem answers in JSON. BPA's balancing-
authority feeds are flat text files on a web server, a utility-industry
convention that predates REST and that BPA has never had a reason to
abandon: the file is small, cacheable, and readable by anything. Treating
that as a lesser format would mean leaving out the one Power Marketing
Administration that publishes its own operating data directly.

The shape, verified live 2026-09-02 on both feeds:

    BPA Balancing Authority Load & Total Wind Generation
    at 5-minute intervals, last 7 days
    Dates: 27Aug2026 - 03Sep2026 (last updated 2Sep2026 09:41:14) Pacific Time
    ...four more preamble lines of scope caveats and a contact mailbox...

    Date/Time       <TAB>Load<TAB>VER<TAB>Hydro<TAB>Fossil/Biomass<TAB>Nuclear
    08/27/2026 00:00<TAB>6721<TAB>1237<TAB>4620<TAB>1123<TAB>1129

Four quirks, each of which produces a wrong answer if it is not handled:

1. **The file is padded to the end of the current day with empty rows.** At
   the 2026-09-02 read, 171 of 2,016 intervals had a timestamp and no
   values. A parser that coerces those to zero reports a grid that stopped
   generating at midday; one that keeps them as rows reports a "latest"
   reading with no reading in it. They are intervals that have not happened
   yet, they are dropped, and the count is carried on the result so the
   dropping is visible rather than silent.
2. **The columns differ between feeds.** `baltwg.txt` has five value
   columns and `baltwg3.txt` has six — it adds Interchange. So the header
   row is read; a hardcoded column list would mislabel every value in one
   of the two files.
3. **CRLF line endings**, which leave a trailing carriage return on the last
   field of every row. `1129\\r` is not a number.
4. **The header's date range runs into the future** because of (1). It is
   the file's window, not its coverage, and `reported_through` — the last
   interval that actually carries values — is the honest end of the data.

The publisher's own "last updated" stamp in line 3 is Pacific local time
with no offset, so it is carried as the string it is. `Last-Modified` on the
HTTP response is the unambiguous machine-readable version and is what
reaches the envelope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, field_validator

from ..core.errors import InvalidQuery, SourceSchemaChanged
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, FetchResult, HttpFetcher, TextFetcher, TTLCache,
                   egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

Number = int | float

# "(last updated 2Sep2026 09:41:14)" — the publisher's own write time, in
# whatever local zone the preamble names.
_LAST_UPDATED = re.compile(r"last updated\s+([^)]+)", re.I)


class TextFeedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    feeds: dict[str, str]
    """Feed name -> path under base_url. Named rather than free-form: the
    adapter will not fetch a path a caller invents, and a sibling file that
    was never verified (`baltwg2.txt` returns 404) cannot be reached by
    guessing."""
    default_feed: str
    header_prefix: str = "Date/Time"
    """The first cell of the column-header row, which is what separates the
    prose preamble from the table."""
    timezone_note: str | None = None
    """What zone the timestamps are in, in the publisher's words. The file
    itself states it in prose and never in an offset."""
    units: str | None = None

    @field_validator("timezone_note", "units", mode="after")
    @classmethod
    def _tidy(cls, v: str | None) -> str | None:
        """Collapse YAML folded-scalar whitespace, for the same reason the
        registry does it to manifest prose: these strings reach the caller
        verbatim, and a note with hard-wrapped newlines through the middle of
        its sentences is what the caller sees. The registry's own validators
        do not run on an adapter's params block."""
        return " ".join(v.split()) if v is not None else None


register_adapter_params("text_feed", TextFeedParams)


@dataclass
class FeedRow:
    timestamp: str
    values: dict[str, Number | None]

    def is_empty(self) -> bool:
        return all(v is None for v in self.values.values())


@dataclass
class FeedTable:
    feed: str
    title: str
    columns: list[str]
    """Value columns in the publisher's own order, read from the header."""
    rows: list[FeedRow]
    """Most recent last, as published."""
    reported_through: str | None
    """Timestamp of the last interval carrying values. The honest end of the
    data, as distinct from the end of the file's window."""
    window: str | None
    pending_intervals: int
    """Trailing intervals the file lists with no values yet. Reported rather
    than hidden: it is the difference between 'the grid stopped' and 'the
    day has not finished'."""
    published_note: str | None
    """The publisher's own last-updated stamp, in its local zone."""
    total_intervals: int
    preamble: list[str] = field(default_factory=list)

    @property
    def latest(self) -> FeedRow | None:
        return self.rows[-1] if self.rows else None

    @property
    def available_intervals(self) -> int:
        """Valued intervals in the fetched window, before any `intervals`
        trim. Kept separate from `len(rows)` so a caller who asked for the
        last hour can still be told how much the window held."""
        return self.total_intervals - self.pending_intervals


def _number(text: str) -> Number | None:
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        return None


def _split_table(text: str, params: TextFeedParams,
                 source_id: str) -> tuple[list[str], list[str], list[str]]:
    """Preamble, header cells, data lines. CRLF is normalized here so no
    downstream field ever carries a stray carriage return."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if line.startswith(params.header_prefix):
            header = [c.strip() for c in line.split("\t")]
            body = [ln for ln in lines[i + 1:] if ln.strip()]
            return [ln.strip() for ln in lines[:i] if ln.strip()], header, body
    raise SourceSchemaChanged(
        f"{source_id}: no column-header row starting with "
        f"{params.header_prefix!r} in the feed. The file's layout has "
        "changed, or the response is an error page rather than the feed.")


def parse_feed(text: str, feed: str, params: TextFeedParams,
               source_id: str) -> FeedTable:
    preamble, header, body = _split_table(text, params, source_id)
    if len(header) < 2:
        raise SourceSchemaChanged(
            f"{source_id}: the header row has {len(header)} column(s); a "
            "timestamp and at least one value column are expected.")
    columns = header[1:]

    rows: list[FeedRow] = []
    for line in body:
        cells = line.split("\t")
        if len(cells) != len(header):
            raise SourceSchemaChanged(
                f"{source_id}: a data row has {len(cells)} field(s) against "
                f"{len(header)} header column(s). The feed's column set has "
                "changed mid-file, which no column mapping can survive.")
        rows.append(FeedRow(timestamp=cells[0].strip(),
                            values={name: _number(cell)
                                    for name, cell in zip(columns,
                                                          cells[1:],
                                                          strict=True)}))

    # Trailing intervals with no values are the rest of today, not an
    # outage. Only the trailing run is dropped: a gap in the middle of the
    # window is a real reporting gap and stays visible as a row of nulls.
    pending = 0
    while rows and rows[-1].is_empty():
        rows.pop()
        pending += 1

    updated = _LAST_UPDATED.search(" ".join(preamble))
    window = next((ln for ln in preamble if ln.lower().startswith("dates:")),
                  None)
    return FeedTable(
        feed=feed, title=preamble[0] if preamble else feed, columns=columns,
        rows=rows, reported_through=rows[-1].timestamp if rows else None,
        window=window, pending_intervals=pending,
        published_note=updated.group(1).strip() if updated else None,
        total_intervals=len(rows) + pending, preamble=preamble)


class TextFeedAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: TextFetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> TextFeedParams:
        return TextFeedParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest,
                     params: TextFeedParams) -> TextFetcher:
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def read_feed(self, manifest: SourceManifest, feed: str = "",
                        *, intervals: int = 0) -> Fetched[FeedTable]:
        """One feed, parsed. `intervals` keeps only the most recent N rows;
        0 keeps the whole window."""
        params = self.params_for(manifest)
        name = feed.strip() or params.default_feed
        if name not in params.feeds:
            raise InvalidQuery(
                f"feed {name!r} is not one of {sorted(params.feeds)}. The "
                "feed names are the ones this source has been verified "
                "against; sibling file names on the same server are not "
                "guaranteed to exist.")
        url = f"{params.base_url.rstrip('/')}/{params.feeds[name].lstrip('/')}"
        result = await self._fetch(manifest, params, url)
        table = parse_feed(result.payload, name, params, manifest.id)
        if intervals > 0:
            table.rows = table.rows[-intervals:]
        log_source_call(manifest, "read_feed", {"feed": name},
                        len(table.rows))
        return Fetched.of(result, table)

    async def _fetch(self, manifest: SourceManifest, params: TextFeedParams,
                     url: str) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, {}, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_text(
            url, {})
        return self._cache.put(manifest.id, url, {}, response.text,
                               response.headers)
