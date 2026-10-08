"""`grid.find_outage_history`: EAGLE-I's annual releases, read from OSTI.

The tool answers "which release holds year X, and where is it", so what it
must get right is the year arithmetic in the titles, the release OSTI lists
twice, and the difference between a year that is not out yet and a year
nobody published.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.osti_family import OstiRecord
from doe_mcp.core.envelope import (PaginationCoverage, RecipeKind,
                                   RegistryCoverage, ResultCoverage,
                                   WarningCode)
from doe_mcp.core.errors import InvalidQuery
from doe_mcp.domains import energy


async def test_the_series_is_read_whole_and_split_by_kind(ctx):
    env = await energy.find_outage_history(ctx)
    titles = [r["title"] for r in env.data["releases"]]
    assert titles[0] == "EAGLE-I Power Outage Data 2014 - 2022"
    assert env.data["releases"][0]["covers_years"] == {"from": 2014,
                                                       "to": 2022}
    assert env.data["years_covered"] == {"from": 2014, "to": 2025}
    assert env.data["missing_years"] == []
    assert len(env.data["customer_counts"]) == 1
    assert len(env.data["documentation"]) == 1
    assert env.coverage.registry == RegistryCoverage.covered
    assert env.coverage.pagination == PaginationCoverage.complete
    assert env.coverage.time_range.from_ == "2014"


async def test_one_doi_listed_twice_is_one_release_naming_both_records(ctx):
    env = await energy.find_outage_history(ctx, year=2025)
    [release] = env.data["releases_for_year"]
    assert release["doi"] == "10.13139/ORNLNCCS/3012826"
    assert sorted(release["osti_ids"]) == ["3012826", "3019924"]
    assert sum(1 for r in env.data["releases"]
               if r["doi"] == release["doi"]) == 1
    # Both records stay in the evidence: a caller may hold either id.
    assert {"3012826", "3019924"} <= {e.record_id for e in env.evidence}


async def test_a_year_inside_a_multi_year_release_finds_it(ctx):
    env = await energy.find_outage_history(ctx, year=2021)
    [release] = env.data["releases_for_year"]
    assert release["covers_years"] == {"from": 2014, "to": 2022}
    assert env.coverage.result == ResultCoverage.hit
    dois = {r.uri for r in env.access_recipes if r.kind == RecipeKind.doi}
    assert release["doi_url"] in dois
    # The denominator travels with every answer about a year.
    assert env.data["customer_counts"][0]["doi_url"] in dois


async def test_a_year_not_yet_published_says_so_rather_than_empty(ctx):
    env = await energy.find_outage_history(ctx, year=2026)
    assert env.data["releases_for_year"] == []
    assert env.coverage.result == ResultCoverage.empty
    assert "may not be out yet" in env.data["note_for_year"]


async def test_a_year_before_the_series_names_where_it_starts(ctx):
    env = await energy.find_outage_history(ctx, year=2010)
    assert "start in 2014" in env.data["note_for_year"]


async def test_releases_from_different_years_warn_about_vintages(ctx):
    env = await energy.find_outage_history(ctx)
    assert WarningCode.mixed_vintages in {w.code for w in env.warnings}


async def test_one_release_does_not_warn_about_mixed_vintages(ctx):
    env = await energy.find_outage_history(ctx, year=2025)
    assert WarningCode.mixed_vintages not in {w.code for w in env.warnings}


async def test_a_nonsense_year_is_refused(ctx):
    with pytest.raises(InvalidQuery):
        await energy.find_outage_history(ctx, year=21)


def test_a_gap_between_releases_is_reported_by_year():
    assert energy._missing_years([(2014, 2016), (2019, 2019)]) == [2017,
                                                                   2018]
    note = energy._no_release_note(2017, [(2014, 2016), (2019, 2019)])
    assert "gap" in note


def test_titles_are_classified_by_what_they_hold():
    assert energy._outage_kind("EAGLE-I County Customer Dataset Fall 2025") \
        == "customer_counts"
    assert energy._outage_kind("EAGLE-I Power Outage Data Information") \
        == "documentation"
    assert energy._covered_years("EAGLE-I Power Outage Data 2014 - 2022") \
        == (2014, 2022)
    assert energy._covered_years("EAGLE-I Power Outage Data 2023") \
        == (2023, 2023)
    for title in ("Outage Data 2014 to 2022", "Outage Data 2014 - 22",
                  "Outage Data 2014 through 2022"):
        assert energy._covered_years(title) == (2014, 2022), title
    # A reversed span is not a span, and two loose years are not one.
    assert energy._covered_years("Outage Data 2022 - 2014") is None
    assert energy._covered_years("Outage Data 2020 and 2021") is None


def test_a_release_without_a_doi_is_kept_under_its_record_id():
    records = [OstiRecord(record_id="1", title="EAGLE-I Power Outage Data "
                          "2030", raw={}),
               OstiRecord(record_id="2", title="EAGLE-I Power Outage Data "
                          "2031", raw={})]
    releases = energy._releases(records)
    assert [r["osti_ids"] for r in releases] == [["1"], ["2"]]


async def test_a_caller_cannot_widen_the_pinned_filter(ctx):
    manifest = ctx.sources.get("ceser-eagle-i-outages")
    with pytest.raises(InvalidQuery):
        await ctx.osti.search(manifest, filters={"title": "solar"}, rows=5)
