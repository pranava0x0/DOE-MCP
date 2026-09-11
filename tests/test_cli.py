"""The CLI's promises: doctor never prints a secret, configure never writes
one into a client config."""
from __future__ import annotations

import json

import pytest

from doe_mcp.cli.configure import SERVERS, configure, entry_for
from doe_mcp.core.credentials import CREDENTIAL_SPECS, Credentials


def test_a_client_config_entry_carries_no_environment_block():
    """Client configs get pasted into issues and synced to repositories. A
    key in one is a key you have to rotate."""
    for server in SERVERS:
        entry = entry_for(server)
        assert set(entry) == {"command", "args"}
        assert "env" not in entry
        blob = json.dumps(entry).lower()
        for name in CREDENTIAL_SPECS:
            assert name.lower() not in blob


def test_configure_writes_the_servers_and_backs_up_what_was_there(tmp_path,
                                                                  monkeypatch):
    import doe_mcp.cli.configure as mod
    home = tmp_path
    monkeypatch.setattr(mod, "_home", lambda: home)
    target = home / ".claude.json"
    target.write_text(json.dumps({"mcpServers": {"someone-elses": {}}}))

    path, doc = configure("claude-code", ["research", "energy-data"])
    assert path == target
    written = json.loads(target.read_text())
    assert set(written["mcpServers"]) == {"someone-elses", "doe-research",
                                          "doe-energy-data"}
    assert written["mcpServers"]["doe-research"]["command"] == "doe-research"
    assert (target.parent / ".claude.json.doe-mcp-backup").exists(), (
        "a config that was fine a moment ago should stay recoverable")


def test_configure_refuses_to_overwrite_unparseable_json(tmp_path, monkeypatch):
    import doe_mcp.cli.configure as mod
    monkeypatch.setattr(mod, "_home", lambda: tmp_path)
    (tmp_path / ".claude.json").write_text("{not json")
    with pytest.raises(SystemExit, match="not valid JSON"):
        configure("claude-code", ["research"])


def test_credentials_report_presence_never_values(tmp_path):
    path = tmp_path / "credentials.env"
    path.write_text("EIA_API_KEY=super-secret-value\n")
    creds = Credentials.load(path)
    assert creds.has("EIA_API_KEY")
    assert creds.present() == ["EIA_API_KEY"]
    assert "super-secret-value" not in json.dumps(creds.present())
    assert "super-secret-value" not in json.dumps(creds.missing())


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    """So a container or CI run can inject a key without writing one to
    disk."""
    path = tmp_path / "credentials.env"
    path.write_text("EIA_API_KEY=from-file\n")
    monkeypatch.setenv("EIA_API_KEY", "from-env")
    assert Credentials.load(path).values["EIA_API_KEY"] == "from-env"


def test_a_group_readable_credentials_file_is_flagged(tmp_path):
    path = tmp_path / "credentials.env"
    path.write_text("EIA_API_KEY=x\n")
    path.chmod(0o644)
    assert Credentials.load(path).insecure_mode is True
    path.chmod(0o600)
    assert Credentials.load(path).insecure_mode is False


def test_the_demo_key_is_recognised(tmp_path):
    path = tmp_path / "credentials.env"
    path.write_text("API_DATA_GOV_KEY=DEMO_KEY\n")
    creds = Credentials.load(path)
    assert creds.has("API_DATA_GOV_KEY") and creds.is_demo("API_DATA_GOV_KEY")


def test_no_module_in_the_package_prints_a_credential_value():
    """A grep-level guarantee. There is no code path that echoes a key."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "doe_mcp"
    for path in root.rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            if "print(" in line and "values[" in line:
                pytest.fail(f"{path}: prints a credential value: {line}")


def test_a_key_typed_as_an_argument_is_refused_without_echoing_it(capsys):
    """argparse used to reject the stray positional by printing it back, so
    `configure credentials --set EIA_API_KEY <key>` echoed the key into the
    terminal and the scrollback — on the one command whose entire purpose is
    keeping keys out of places like that (decision 0012).

    Reported from real use on 2026-09-08.
    """
    import sys
    from doe_mcp.cli.__main__ import main
    secret = "s3cr3t-key-value-do-not-echo"
    argv = sys.argv
    sys.argv = ["doe-mcp", "configure", "credentials", "--set",
                "EIA_API_KEY", secret]
    try:
        code = main()
    finally:
        sys.argv = argv
    out = capsys.readouterr()
    assert code != 0
    assert secret not in out.out
    assert secret not in out.err
    assert "does not go on the command line" in out.out + out.err
    # The refusal must offer the routes that work, not only name the one
    # that does not: an instruction that says "do not do it that way" and
    # stops there leaves the person doing it that way (decision 0021).
    assert "pbpaste |" in out.out + out.err
    assert "< keyfile" in out.out + out.err
    assert "hidden prompt" in out.out + out.err


def test_a_credential_can_be_piped_in_without_touching_argv(tmp_path,
                                                            monkeypatch):
    """`pbpaste | doe-mcp configure credentials --set NAME` is the route a
    person with the key on their clipboard should reach for."""
    import io
    import sys
    from doe_mcp.cli.__main__ import main
    monkeypatch.setattr("doe_mcp.core.credentials.default_path",
                        lambda: tmp_path / "credentials.env")
    monkeypatch.setattr("doe_mcp.cli.__main__.default_path",
                        lambda: tmp_path / "credentials.env")
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped-value\n"))
    monkeypatch.setattr(sys, "argv",
                        ["doe-mcp", "configure", "credentials",
                         "--set", "EIA_API_KEY"])
    assert main() == 0
    written = (tmp_path / "credentials.env").read_text()
    assert "EIA_API_KEY=piped-value" in written
    assert (tmp_path / "credentials.env").stat().st_mode & 0o777 == 0o600


def test_a_credential_can_come_from_an_environment_variable(tmp_path,
                                                            monkeypatch):
    import sys
    from doe_mcp.cli.__main__ import main
    monkeypatch.setattr("doe_mcp.cli.__main__.default_path",
                        lambda: tmp_path / "credentials.env")
    monkeypatch.setenv("SOME_SOURCE_VAR", "env-value")
    monkeypatch.setattr(sys, "argv",
                        ["doe-mcp", "configure", "credentials", "--set",
                         "EIA_API_KEY", "--from-env", "SOME_SOURCE_VAR"])
    assert main() == 0
    assert "EIA_API_KEY=env-value" in (tmp_path / "credentials.env").read_text()


def test_tools_call_auto_detects_profile(capsys, monkeypatch):
    """When --profile is omitted, tools call auto-locates the tool's profile."""
    import sys
    from doe_mcp.cli.__main__ import main
    # Call a tool that lives in energy (not research), without --profile
    monkeypatch.setattr(
        sys, "argv",
        ["doe-mcp", "tools", "call", "facility.find_wind_turbines",
         "--args", '{"state": "RI", "rows": 1}'])
    assert main() == 0
    out = capsys.readouterr().out
    assert '"tool": "facility.find_wind_turbines"' in out
    assert '"data":' in out

