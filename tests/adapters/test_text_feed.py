"""The `text_feed` adapter against BPA's recorded feeds.

Each test here is a quirk that produces a confidently wrong answer if it is
not handled: an empty interval read as zero generation, a column set assumed
rather than read, a carriage return left on the last number of every row.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.text_feed import TextFeedParams, parse_feed
from doe_mcp.core.errors import InvalidQuery, SourceSchemaChanged

SOURCE = "bpa-operations"

PARAMS = TextFeedParams(base_url="https://example.invalid/feeds",
                        feeds={"only": "only.txt"}, default_feed="only")


def _synthetic(rows: list[str], header: str = "Date/Time\tLoad\tHydro") -> str:
    return "\r\n".join(["A Feed", "Dates: 01Jan2026 - 02Jan2026", "",
                        header] + rows) + "\r\n"


async def test_the_trailing_unpopulated_intervals_are_dropped_and_counted(ctx):
    """The file is padded to the end of the current day with timestamped
    rows carrying no values. Coerced to zero they read as a grid that
    stopped generating at midday; kept as rows they make `latest` a reading
    with no reading in it."""
    manifest = ctx.sources.get(SOURCE)
    table = (await ctx.text_feed.read_feed(manifest)).value
    assert table.pending_intervals > 0, (
        "the fixture is meant to retain the publisher's trailing empty rows")
    assert all(not r.is_empty() for r in table.rows)
    assert table.latest is not None
    assert all(v is not None for v in table.latest.values.values())
    assert table.total_intervals == (table.available_intervals
                                     + table.pending_intervals)


async def test_the_two_feeds_do_not_share_a_column_set(ctx):
    """baltwg3.txt carries Interchange and baltwg.txt does not, so the
    header row is read rather than assumed. A hardcoded column list would
    mislabel every value in one of the two files."""
    manifest = ctx.sources.get(SOURCE)
    wind = (await ctx.text_feed.read_feed(
        manifest, "balancing_authority")).value
    inter = (await ctx.text_feed.read_feed(
        manifest, "balancing_authority_interchange")).value
    assert "Interchange" in inter.columns
    assert "Interchange" not in wind.columns
    assert len(inter.columns) == len(wind.columns) + 1
    for column in wind.columns:
        assert column in inter.columns


async def test_no_parsed_field_carries_a_stray_carriage_return(ctx):
    """CRLF line endings leave a \\r on the last field of every row, so the
    final column's value is `1129\\r` rather than a number."""
    manifest = ctx.sources.get(SOURCE)
    table = (await ctx.text_feed.read_feed(manifest)).value
    assert not any("\r" in c for c in table.columns)
    for row in table.rows:
        assert "\r" not in row.timestamp
        assert all(isinstance(v, (int, float)) for v in row.values.values())


async def test_the_values_are_numbers_and_interchange_may_be_negative(ctx):
    manifest = ctx.sources.get(SOURCE)
    table = (await ctx.text_feed.read_feed(manifest)).value
    loads = [r.values["Load"] for r in table.rows]
    assert loads and all(isinstance(v, int) for v in loads)
    assert min(loads) > 0, "BPA's balancing authority always carries load"


async def test_the_publishers_own_last_updated_stamp_is_carried(ctx):
    """It is Pacific local time with no offset, so it is carried as the
    string it is rather than parsed into a false UTC instant."""
    manifest = ctx.sources.get(SOURCE)
    table = (await ctx.text_feed.read_feed(manifest)).value
    assert table.published_note
    assert table.window and table.window.startswith("Dates:")
    assert table.reported_through == table.rows[-1].timestamp


async def test_intervals_returns_the_most_recent_and_reports_truncation(ctx):
    manifest = ctx.sources.get(SOURCE)
    whole = (await ctx.text_feed.read_feed(manifest)).value
    tail = (await ctx.text_feed.read_feed(manifest, intervals=3)).value
    assert len(tail.rows) == 3
    assert [r.timestamp for r in tail.rows] == [
        r.timestamp for r in whole.rows[-3:]]
    assert tail.available_intervals == whole.available_intervals, (
        "the window's size is a property of the file, not of the slice asked "
        "for")


async def test_provenance_comes_from_the_fetch_not_from_the_clock(ctx):
    manifest = ctx.sources.get(SOURCE)
    first = await ctx.text_feed.read_feed(manifest)
    again = await ctx.text_feed.read_feed(manifest)
    assert first.from_cache is False
    assert again.from_cache is True
    assert again.retrieved_at == first.retrieved_at, (
        "a cache hit must report when the bytes left the publisher, not now")


async def test_an_unverified_feed_name_is_refused(ctx):
    """Sibling file names on the same server are not guaranteed to exist —
    baltwg2.txt returns 404 — so the adapter serves the feeds the manifest
    names and does not fetch a path a caller invented."""
    manifest = ctx.sources.get(SOURCE)
    with pytest.raises(InvalidQuery, match="balancing_authority"):
        await ctx.text_feed.read_feed(manifest, "baltwg2")


def test_a_response_without_a_header_row_is_a_schema_change():
    """An outage page or a changed layout, not data."""
    with pytest.raises(SourceSchemaChanged, match="column-header row"):
        parse_feed("<html>Service Unavailable</html>", "only", PARAMS, SOURCE)


def test_a_row_whose_field_count_disagrees_with_the_header_is_refused():
    """A column set that changes mid-file is something no mapping survives,
    and silently zipping the short row would shift every value one column
    left."""
    text = _synthetic(["01/01/2026 00:00\t100\t50",
                       "01/01/2026 00:05\t100"])
    with pytest.raises(SourceSchemaChanged, match="field"):
        parse_feed(text, "only", PARAMS, SOURCE)


def test_a_gap_inside_the_window_stays_visible():
    """Only the TRAILING run of empty intervals is the rest of today. A gap
    in the middle is a real reporting gap and must not be quietly closed up,
    which would make the series look continuous when it is not."""
    text = _synthetic(["01/01/2026 00:00\t100\t50",
                       "01/01/2026 00:05\t\t",
                       "01/01/2026 00:10\t120\t60",
                       "01/01/2026 00:15\t\t"])
    table = parse_feed(text, "only", PARAMS, SOURCE)
    assert table.pending_intervals == 1
    assert len(table.rows) == 3
    assert table.rows[1].is_empty()
    assert table.rows[1].values == {"Load": None, "Hydro": None}
    assert table.reported_through == "01/01/2026 00:10"


def test_a_feed_that_is_entirely_unpopulated_reports_no_rows():
    """The publisher writing the file without data is an outage on their
    side, which is a different finding from an idle grid."""
    text = _synthetic(["01/01/2026 00:00\t\t", "01/01/2026 00:05\t\t"])
    table = parse_feed(text, "only", PARAMS, SOURCE)
    assert table.rows == [] and table.pending_intervals == 2
    assert table.latest is None and table.reported_through is None


def test_folded_scalar_prose_in_the_params_block_is_collapsed():
    """Manifest prose is written as YAML folded scalars, which leaves hard
    wraps and a trailing newline in the loaded string. The registry collapses
    its own fields; an adapter's params block is not covered by those
    validators, and this note reaches the caller verbatim."""
    params = TextFeedParams(
        base_url="https://example.invalid", feeds={"a": "a.txt"},
        default_feed="a",
        timezone_note="Pacific Time, stated in the file's\nown header.\n")
    assert params.timezone_note == "Pacific Time, stated in the file's own header."
