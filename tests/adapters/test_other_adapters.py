"""The other platform-genre adapters against recorded responses."""
from __future__ import annotations

import pytest

from doe_mcp.adapters.base import _forbidden_message
from doe_mcp.core.errors import CredentialMissing, InvalidQuery


async def test_opendatasoft_reads_the_explore_v2_catalog(ctx):
    manifest = ctx.sources.get("ornl-openenergyhub")
    page = (await ctx.opendatasoft.search_datasets(manifest, limit=3)).value
    assert page.total_count and page.total_count >= 150
    assert page.datasets[0].dataset_id and page.datasets[0].title


async def test_json_document_reads_code_gov_date_objects(ctx):
    """code.gov puts an OBJECT in `date` where Project Open Data puts a
    string. Reading it with str() produced entries whose date was the literal
    text "{'created'"."""
    manifest = ctx.sources.get("doe-code-json")
    result = (await ctx.json_document.search(manifest, limit=5)).value
    for entry in result.entries:
        if entry.modified:
            assert not entry.modified.startswith("{"), (
                f"a dict leaked through as a date: {entry.modified!r}")


async def test_json_document_reports_the_documents_own_vintage(ctx):
    """A catalog that looks live and is not is worse than one that admits its
    age, so every answer names the newest entry it holds."""
    manifest = ctx.sources.get("doe-open-data-catalog")
    result = (await ctx.json_document.search(manifest, limit=5)).value
    assert result.document_vintage, "no vintage reported"
    assert result.document_total > 0


async def test_json_document_search_narrows_rather_than_widens(ctx):
    manifest = ctx.sources.get("doe-open-data-catalog")
    broad = (await ctx.json_document.search(manifest, text="energy", limit=50)).value
    narrow = (await ctx.json_document.search(manifest, text="energy data",
                                            limit=50)).value
    assert narrow.total_matches <= broad.total_matches, (
        "adding a term must narrow: terms are ANDed, not ORed")


async def test_fueleconomy_single_option_object_is_normalized(ctx):
    """When a drill-down step has exactly one answer, `menuItem` is an OBJECT
    rather than a one-element array. A client iterating it gets the dict's
    keys as if they were two results."""
    from doe_mcp.adapters.fueleconomy import _menu_items
    one = _menu_items({"menuItem": {"text": "Auto (S8)", "value": "47085"}}, "u")
    assert len(one) == 1 and one[0].value == "47085"


async def test_fueleconomy_year_menu_reads(ctx):
    manifest = ctx.sources.get("fueleconomy-ws")
    fetched = await ctx.fueleconomy.menu(manifest, "year")
    years = fetched.value
    assert len(years) > 30
    assert all(y.value.isdigit() for y in years)
    assert fetched.retrieved_at and fetched.from_cache is False


async def test_fueleconomy_refuses_a_step_outside_the_drill_down(ctx):
    manifest = ctx.sources.get("fueleconomy-ws")
    with pytest.raises(InvalidQuery, match="year/make/model/options"):
        await ctx.fueleconomy.menu(manifest, "colour")


async def test_eia_without_a_key_names_the_credential_and_the_command(ctx):
    """The message has to be actionable and must never suggest putting the
    key in a client config."""
    manifest = ctx.sources.get("eia-api-v2")
    with pytest.raises(CredentialMissing) as err:
        await ctx.eia.describe_route(manifest)
    message = str(err.value)
    assert "eia.gov/opendata/register" in message
    assert "doe-mcp configure credentials" in message
    assert "Do not put the key" in message


async def test_eia_rejects_a_row_count_over_the_publishers_ceiling(ctx):
    manifest = ctx.sources.get("eia-api-v2")
    with pytest.raises(InvalidQuery, match="ceiling of 5000"):
        await ctx.eia.get_data(manifest, "electricity/rto/region-data",
                               rows=99_999)


def test_an_api_403_with_a_reason_is_not_reported_as_a_waf_block():
    """EIA answers a keyless request with 403 and a JSON body naming
    API_KEY_MISSING. A WAF answers with an HTML challenge. Conflating them
    produces a wrong claim in opposite directions."""
    api = _forbidden_message("api.eia.gov", b'{"error":{"code":'
                             b'"API_KEY_MISSING","message":"No api_key."}}')
    assert "API_KEY_MISSING" in api
    assert "blocked_probe" not in api

    waf = _forbidden_message("gesdb.sandia.gov", b"<html>Access Denied</html>")
    assert "blocked_probe" in waf
    assert "check it in a browser before describing the source as gated" in waf
