"""The CLI's promises: doctor never prints a secret, configure never writes
one into a client config."""
from __future__ import annotations

import json

import pytest

from doe_mcp.cli.configure import SERVERS, configure, entry_for
from doe_mcp.core.credentials import CREDENTIAL_SPECS, Credentials, write_credential


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


def test_tools_call_auto_detects_profile(ctx, capsys, monkeypatch):
    """Resolve a tool outside research, then call it on a recorded feed."""
    import json
    import sys
    from doe_mcp.cli.__main__ import main
    monkeypatch.setattr("doe_mcp.cli.__main__._load_ctx", lambda: ctx)
    monkeypatch.setattr(sys, "argv", ["doe-mcp", "tools", "call",
                                     "grid.get_bpa_operations", "--args", '{"intervals": 2}'])
    assert main() == 0
    response = json.loads(capsys.readouterr().out)
    assert response["_execution"]["tool"] == "grid.get_bpa_operations"
    assert response["data"]["record_count"] == 2


@pytest.mark.parametrize("text", ["null", "[]", '"settings"', "42", ""])
def test_configure_preserves_a_config_without_an_object_root(tmp_path, monkeypatch, text):
    import doe_mcp.cli.configure as mod
    monkeypatch.setattr(mod, "_home", lambda: tmp_path)
    target = tmp_path / ".claude.json"
    target.write_text(text)
    with pytest.raises(SystemExit):
        configure("claude-code", ["research"])
    assert target.read_text() == text
    assert not target.with_suffix(".json.doe-mcp-backup").exists()


def test_configure_replaces_a_backup_link_without_touching_its_target(tmp_path, monkeypatch):
    import doe_mcp.cli.configure as mod
    monkeypatch.setattr(mod, "_home", lambda: tmp_path)
    target = tmp_path / ".claude.json"
    original = '{"other": true}'
    target.write_text(original)
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("preserve me")
    backup = tmp_path / ".claude.json.doe-mcp-backup"
    backup.symlink_to(unrelated)
    configure("claude-code", ["research"])
    assert unrelated.read_text() == "preserve me"
    assert backup.read_text() == original
    assert not backup.is_symlink()


def test_failed_config_replacement_preserves_the_original(tmp_path, monkeypatch):
    import doe_mcp.cli.configure as mod
    monkeypatch.setattr(mod, "_home", lambda: tmp_path)
    target = tmp_path / ".claude.json"
    original = '{"other": true}'
    target.write_text(original)
    replace = mod.os.replace

    def fail_final(source, destination):
        if destination == target:
            raise OSError("simulated disk failure")
        replace(source, destination)

    monkeypatch.setattr(mod.os, "replace", fail_final)
    with pytest.raises(OSError, match="simulated disk failure"):
        configure("claude-code", ["research"])
    assert target.read_text() == original
    assert set(tmp_path.iterdir()) == {target, target.with_suffix(".json.doe-mcp-backup")}


def test_credential_replacement_is_private_before_publication(tmp_path, monkeypatch):
    import doe_mcp.core.credentials as mod
    path = tmp_path / "credentials.env"
    path.write_text("# keep this\nEIA_API_KEY = old-key\nMP_API_KEY=other-key\n")
    replace = mod.os.replace

    def check_mode(source, destination):
        assert source.stat().st_mode & 0o777 == 0o600
        assert "EIA_API_KEY=new-key" in source.read_text()
        replace(source, destination)

    monkeypatch.setattr(mod.os, "replace", check_mode)
    write_credential(path, "EIA_API_KEY", "new-key")
    assert Credentials.load(path).values["EIA_API_KEY"] == "new-key"
    assert "old-key" not in path.read_text()
    assert "# keep this" in path.read_text()
    assert "MP_API_KEY=other-key" in path.read_text()


def test_credential_write_refuses_a_symbolic_link(tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("preserve me")
    path = tmp_path / "credentials.env"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic-link"):
        write_credential(path, "EIA_API_KEY", "new-key")
    assert target.read_text() == "preserve me"


def test_failed_credential_replacement_preserves_the_original(tmp_path, monkeypatch):
    import doe_mcp.core.credentials as mod
    path = tmp_path / "credentials.env"
    original = "EIA_API_KEY=old-key\n"
    path.write_text(original)

    def fail_replace(source, destination):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(mod.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        write_credential(path, "EIA_API_KEY", "new-key")
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_configure_refuses_a_symbolic_link_without_touching_the_target(tmp_path, monkeypatch):
    import doe_mcp.cli.configure as mod
    monkeypatch.setattr(mod, "_home", lambda: tmp_path)
    target = tmp_path / "actual-config"
    target.write_text('{"other": true}')
    (tmp_path / ".claude.json").symlink_to(target)
    with pytest.raises(SystemExit, match="symbolic link"):
        configure("claude-code", ["research"])
    assert target.read_text() == '{"other": true}'


@pytest.mark.parametrize("value", ["first\nMP_API_KEY=injected", "a\rb", "a\x00b", "a b"])
def test_credential_write_refuses_multiple_values_without_echoing_them(tmp_path, value):
    path = tmp_path / "credentials.env"
    with pytest.raises(ValueError) as err:
        write_credential(path, "EIA_API_KEY", value)
    assert value not in str(err.value)
    assert not path.exists()


@pytest.mark.parametrize("arguments", [
    ["--set", "secret-as-name"],
    ["--set", "secret-as-name", "another-value"],
    ["--set", "EIA_API_KEY", "--secret-as-name"],
    ["--set", "EIA_API_KEY", "--from-env", "secret-as-name"],
])
def test_all_credential_argument_refusals_hide_input(arguments, capsys, monkeypatch):
    import sys
    from doe_mcp.cli.__main__ import main
    monkeypatch.setattr(sys, "argv", ["doe-mcp", "configure", "credentials", *arguments])
    monkeypatch.delenv("secret-as-name", raising=False)
    try:
        assert main() != 0
    except SystemExit as err:
        assert err.code != 0
    captured = capsys.readouterr()
    assert "secret-as-name" not in captured.out + captured.err


def test_credentials_repr_omits_values(tmp_path):
    creds = Credentials({"EIA_API_KEY": "do-not-print-me"}, tmp_path, False)
    assert "do-not-print-me" not in repr(creds)
