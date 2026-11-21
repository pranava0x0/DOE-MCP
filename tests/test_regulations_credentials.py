"""The public exploration key is not a credential for live docket tools."""
from pathlib import Path

import pytest

from doe_mcp.core.credentials import Credentials
from doe_mcp.core.errors import CredentialMissing


@pytest.mark.parametrize("value", ["DEMO_KEY", "demo_key", " DEMO_KEY "])
def test_api_data_gov_exploration_key_is_refused(value):
    credentials = Credentials({"API_DATA_GOV_KEY": value}, Path("/nonexistent"), False)
    with pytest.raises(CredentialMissing, match="user-owned") as exc:
        credentials.require("API_DATA_GOV_KEY")
    assert value not in str(exc.value)


def test_user_api_data_gov_key_is_accepted():
    credentials = Credentials({"API_DATA_GOV_KEY": "sentinel-key"}, Path("/nonexistent"), False)
    assert credentials.require("API_DATA_GOV_KEY") == "sentinel-key"


@pytest.mark.parametrize("keyed,names,expected", [
    (False, (), set()),
    (True, (), {"EIA_API_KEY"}),
    (False, ("API_DATA_GOV_KEY",), {"API_DATA_GOV_KEY"}),
])
def test_replay_injects_only_requested_sentinels_without_loading_credentials(
        monkeypatch, keyed, names, expected):
    from doe_mcp.replay import load_replay_context

    def refuse_load(*args, **kwargs):
        raise AssertionError("recorded contexts must never read the user's credentials")

    monkeypatch.setattr(Credentials, "load", refuse_load)
    fixtures = Path(__file__).parent / "fixtures"
    ctx = load_replay_context(fixtures, keyed=keyed, credential_names=names)
    assert set(ctx.credentials.values) == expected
    assert ctx.regulations_gov._credentials is ctx.credentials
    assert ctx.eia._credentials is ctx.credentials
    assert all(value == "replay-only" for value in ctx.credentials.values.values())


def test_proposed_docket_integration_is_not_a_shipping_server():
    from doe_mcp.core.toolreg import PROFILES, expand_profile
    from doe_mcp.servers.build import registries
    from doe_mcp.servers.lineup import by_key, shipping
    assert by_key()["docs"].status == "planned"
    assert all(spec.key != "docs" for spec in shipping())
    assert all(not profile.startswith("docs:") for profile in PROFILES)
    assert {spec.name for spec in registries()["docs"].tools("dockets")} == {
        "docs.get_docket", "docs.search_docket_documents", "docs.get_docket_document"}
    for profile in ("research:default", "research:discovery", "research:all"):
        names = {spec.name for spec in expand_profile(profile, registries())}
        assert {"docs.search_rulemakings", "docs.get_rulemaking"} <= names
        assert not {"docs.get_docket", "docs.search_docket_documents", "docs.get_docket_document"} & names
