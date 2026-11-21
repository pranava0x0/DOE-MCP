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
