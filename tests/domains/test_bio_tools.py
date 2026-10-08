"""`doe-bio`: NMDC's studies and biosamples.

The publisher answers an unknown filter field with HTTP 200 and zero rows,
matches `.search:` case-sensitively, and publishes investigators' contact
details in its study records. Each of those is a way to return a wrong or
careless answer that looks right, so each has a test here.
"""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import JsonResponse
from doe_mcp.adapters.nmdc import NmdcAdapter, build_filter, check_id
from doe_mcp.core.envelope import (PaginationCoverage, RegistryCoverage,
                                   ResultCoverage)
from doe_mcp.core.errors import (InvalidQuery, SourceSchemaChanged,
                                 SourceUnavailable)
from doe_mcp.domains import bio

NEON = "nmdc:sty-11-34xj1150"
SHALE = "nmdc:sty-11-8fb6t785"


async def test_text_search_reads_the_whole_collection_case_blind(ctx):
    env = await bio.search_studies(ctx, text="SOIL", rows=3)
    assert env.data["studies_in_collection"] == 85
    assert env.data["total_matches"] > env.data["record_count"] == 3
    assert env.coverage.pagination == PaginationCoverage.truncated
    assert env.coverage.registry == RegistryCoverage.covered
    page = await ctx.nmdc.studies(ctx.sources.get("nmdc-runtime"),
                                  text="SOIL")
    for study in page.value.studies:
        assert "soil" in " ".join(filter(None, (
            study.name, study.title, study.description))).lower()


async def test_every_word_must_match(ctx):
    one = await bio.search_studies(ctx, text="soil", rows=50)
    two = await bio.search_studies(ctx, text="soil metagenomes", rows=50)
    assert 0 < two.data["total_matches"] <= one.data["total_matches"]


async def test_no_contact_details_reach_an_answer(ctx):
    found = await bio.search_studies(ctx, text="shale", rows=5)
    whole = await bio.get_study(ctx, NEON)
    for env in (found, whole):
        text = env.model_dump_json()
        assert "@" not in text.replace("redacted@example.invalid", "")
        assert "email" not in text
        assert "profile_image" not in text
    assert whole.data["investigators"][0]["orcid"].startswith("orcid:")


async def test_a_study_comes_with_its_sample_count_and_next_step(ctx):
    env = await bio.get_study(ctx, NEON)
    assert env.data["biosample_count"] > 1000
    assert env.next_actions[0].suggested_capability == "bio.biosamples"
    assert env.evidence[0].locator.endswith(NEON)


async def test_an_empty_study_search_is_about_nmdc_not_the_world(ctx):
    env = await bio.search_studies(ctx, text="zzzznotarealtopiczzzz")
    assert env.coverage.result == ResultCoverage.empty
    assert "not about whether such a study exists" in env.data["note"]


async def test_biosamples_by_study_report_the_publisher_total(ctx):
    env = await bio.search_biosamples(ctx, study_id=SHALE, rows=5)
    assert env.data["record_count"] == 5
    assert env.data["total_matches"] == 23
    assert env.coverage.pagination == PaginationCoverage.truncated
    assert all(SHALE in s["studies"] for s in env.data["biosamples"])


async def test_biosamples_by_ecosystem_and_place(ctx):
    env = await bio.search_biosamples(ctx, ecosystem_type="Soil",
                                      place="washington", rows=5)
    assert env.coverage.result == ResultCoverage.hit
    assert env.data["filter_applied"]["geo_loc_name.has_raw_value"] == {
        "$regex": "(?i)washington"}
    for sample in env.data["biosamples"]:
        assert "washington" in (sample["place"] or "").lower()


async def test_a_place_with_no_samples_is_an_empty_hit_not_an_error(ctx):
    env = await bio.search_biosamples(ctx, place="zzzznotaplacezzzz", rows=5)
    assert env.coverage.result == ResultCoverage.empty
    assert env.data["total_matches"] == 0


async def test_an_unfiltered_sample_search_is_refused(ctx):
    with pytest.raises(InvalidQuery):
        await bio.search_biosamples(ctx)


def test_filters_are_escaped_and_case_blind():
    expression, expected = build_filter({"place": "St. Louis (MO)"})
    assert expression.startswith("geo_loc_name.has_raw_value.search:(?i)")
    assert r"St\.\ Louis\ \(MO\)" in expression
    assert expected == {"geo_loc_name.has_raw_value":
                        {"$regex": r"(?i)St\.\ Louis\ \(MO\)"}}


def test_a_comma_in_a_value_is_refused_rather_than_split():
    with pytest.raises(InvalidQuery, match="comma"):
        build_filter({"place": "Richland, WA"})


def test_an_unknown_filter_is_refused_before_it_is_sent():
    with pytest.raises(InvalidQuery, match="zero results"):
        build_filter({"habitat": "soil"})


def test_ids_are_checked_before_they_become_a_path():
    assert check_id(" nmdc:sty-11-8fb6t785 ", "study") == SHALE
    for bad in ("../studies", "nmdc:sty-11-8fb6t785/x", "sty-11-8fb6t785"):
        with pytest.raises(InvalidQuery):
            check_id(bad, "study")


class _Fixed:
    def __init__(self, payload=None, refusal=None):
        self.payload, self.refusal = payload, refusal

    async def fetch_json(self, url, params):
        if self.refusal:
            raise self.refusal
        return JsonResponse(url=url, payload=self.payload, headers={})


async def test_an_echo_that_differs_from_the_filter_sent_is_drift(ctx):
    manifest = ctx.sources.get("nmdc-runtime")
    adapter = NmdcAdapter(fetcher=_Fixed(
        {"meta": {"mongo_filter_dict": {}, "count": 27352},
         "results": []}))
    with pytest.raises(SourceSchemaChanged, match="differently filtered"):
        await adapter.biosamples(manifest, filters={"place": "washington"})


async def test_a_partial_study_collection_is_refused(ctx):
    manifest = ctx.sources.get("nmdc-runtime")
    adapter = NmdcAdapter(fetcher=_Fixed(
        {"meta": {"count": 85}, "results": [{"id": SHALE, "name": "x"}]}))
    with pytest.raises(SourceSchemaChanged, match="partial"):
        await adapter.studies(manifest)


async def test_a_study_404_names_the_id_and_an_outage_stays_an_outage(ctx):
    manifest = ctx.sources.get("nmdc-runtime")
    missing = NmdcAdapter(fetcher=_Fixed(
        refusal=SourceUnavailable("HTTP 404", status=404)))
    with pytest.raises(InvalidQuery, match="has no study"):
        await missing.get_study(manifest, "nmdc:sty-00-zzzzzz")
    down = NmdcAdapter(fetcher=_Fixed(
        refusal=SourceUnavailable("HTTP 503", status=503)))
    with pytest.raises(SourceUnavailable):
        await down.get_study(manifest, "nmdc:sty-00-zzzzzz")
