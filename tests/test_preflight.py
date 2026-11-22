"""Profile preflight measures the local install without inspecting user secrets."""
from __future__ import annotations

import json
import os
import sys
from email.message import Message
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from doe_mcp.cli import preflight
from doe_mcp.cli.__main__ import main
from doe_mcp.core.credentials import CREDENTIAL_SPECS
from doe_mcp.core.toolreg import PROFILES, expand_profile
from doe_mcp.servers.build import registries
from doe_mcp.servers.lineup import for_profile


@pytest.fixture
def installed(monkeypatch):
    """Model the installed wheel while allowing individual install failures."""
    package_metadata = Message()
    package_metadata["Provides-Extra"] = "energy-tech"
    dist = SimpleNamespace(
        requires=["httpx==0.28.1", "pyarrow==26.0.1; extra == 'energy-tech'"],
        metadata=package_metadata,
        entry_points=[metadata.EntryPoint(name="doe-mcp", value="doe_mcp.cli.__main__:main",
                                          group="console_scripts"), *[
            metadata.EntryPoint(name=for_profile(p).command,
                                value=f"doe_mcp.servers.{for_profile(p).short.replace('-', '_')}:main",
                                group="console_scripts")
            for p in PROFILES if p.endswith(":default")]],
    )
    distribution = metadata.distribution
    monkeypatch.setattr(preflight.metadata, "distribution",
                        lambda name: dist if name == "doe-mcp" else distribution(name))
    return dist


def run(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", ["doe-mcp", "doctor", *arguments])
    code = main()
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_all_profiles_match_real_tool_registration(installed, monkeypatch, capsys, profile):
    code, report = run(monkeypatch, capsys, "--profile", profile, "--json")
    assert code == 0
    assert report["schema_version"] == 1
    assert report["server"] == for_profile(profile).name
    assert report["registered_tools"] == [t.name for t in expand_profile(profile, registries())]
    assert report["entry_points"][1]["command"] == for_profile(profile).command
    assert report["readiness"] == {
        "installation": "ready", "credentials": "not_checked",
        "network": "not_checked", "server_startup": "not_checked"}


def test_cli_never_inspects_credentials_clients_or_network(installed, monkeypatch, capsys):
    import doe_mcp.cli.__main__ as cli
    import doe_mcp.cli.configure as configure
    import doe_mcp.core.credentials as credentials
    import doe_mcp.runtime as runtime
    import doe_mcp.servers.build as build
    import getpass
    import httpx
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("preflight attempted runtime, credential, client, prompt, or network access")

    for obj, name in ((cli, "_load_ctx"), (runtime, "load_context"),
                      (cli, "client_targets"), (configure, "client_targets"),
                      (credentials.Credentials, "load"), (credentials, "default_path"),
                      (build, "build_server"), (getpass, "getpass"),
                      (httpx.Client, "request"), (httpx.AsyncClient, "request"),
                      (socket.socket, "connect")):
        monkeypatch.setattr(obj, name, forbidden)
    for key in CREDENTIAL_SPECS:
        monkeypatch.setenv(key, "secret-preflight-must-never-read")
    environ_type = type(os.environ)
    getitem = environ_type.__getitem__

    def safe_getitem(self, key):
        if key in CREDENTIAL_SPECS:
            forbidden()
        return getitem(self, key)

    monkeypatch.setattr(environ_type, "__getitem__", safe_getitem)
    path_open = Path.open

    def safe_open(path, *args, **kwargs):
        if path.name in {"credentials.env", ".claude.json", "claude_desktop_config.json", "mcp.json"}:
            forbidden()
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", safe_open)
    code, report = run(monkeypatch, capsys, "--profile", "energy-tech:default", "--json")
    assert code == 0
    assert "secret-preflight" not in json.dumps(report)
    sources = {row["id"]: row for row in report["sources"]}
    assert sources["nlr-atb"]["selection_block"] is None
    pvwatts = sources["nlr-pvwatts"]
    assert pvwatts["declared_state"] == "proposed"
    assert pvwatts["selection_block"] == "source_not_activated"
    assert "No user key or recorded model response" in pvwatts["blocked_reason"]
    assert pvwatts["credential_status"] == "not_checked"
    assert report["source_scope"] == "registry_inventory"
    assert "solar.estimate_production" in report["registered_tools"]
    assert sources["eia-api-v2"]["credential_required"] is True
    assert sources["eia-api-v2"]["credential_ref"] == "EIA_API_KEY"
    assert sources["eia-api-v2"]["credential_status"] == "not_checked"
    assert sources["pnnl-vips"]["credential_required"] is True
    assert sources["pnnl-vips"]["credential_ref"] is None
    assert sources["pnnl-vips"]["credential_status"] == "not_checked"


@pytest.mark.parametrize("failure", ["missing", "version_mismatch", "import_failed"])
def test_broken_selected_extra_is_failure_but_other_profiles_work(
        installed, monkeypatch, capsys, failure):
    version, import_module = metadata.version, preflight.importlib.import_module

    def check_version(name):
        if name == "pyarrow":
            if failure == "missing":
                raise metadata.PackageNotFoundError(name)
            if failure == "version_mismatch":
                return "0.0.0"
        return version(name)

    def check_import(name):
        if name == "pyarrow.parquet" and failure == "import_failed":
            raise ImportError("sensitive local path should not be printed")
        return import_module(name)

    monkeypatch.setattr(preflight.metadata, "version", check_version)
    monkeypatch.setattr(preflight.importlib, "import_module", check_import)
    code, report = run(monkeypatch, capsys, "--profile", "energy-tech:default", "--json")
    assert code == 1
    assert report["readiness"]["installation"] == "broken"
    assert next(d for d in report["dependencies"] if d["name"] == "pyarrow")["status"] == failure
    assert any("doe-mcp[energy-tech]" in p["message"] for p in report["problems"])
    assert "sensitive local path" not in json.dumps(report)
    code, report = run(monkeypatch, capsys, "--json")
    assert code == 0
    assert report["profile"] == "research:default"
    assert "pyarrow" not in [d["name"] for d in report["dependencies"]]


@pytest.mark.parametrize("damage", ["metadata", "extra", "entry_point", "registry", "toolset"])
def test_broken_install_produces_parseable_failure(installed, monkeypatch, capsys, damage):
    if damage == "metadata":
        installed.requires.append("unknown>=1")
    elif damage == "extra":
        del installed.metadata["Provides-Extra"]
    elif damage == "entry_point":
        installed.entry_points = []
    elif damage == "registry":
        monkeypatch.setattr(preflight.SourceRegistry, "load", lambda *_: (_ for _ in ()).throw(ValueError()))
    elif damage == "toolset":
        monkeypatch.setattr(preflight, "registries", lambda: {})
    code, report = run(monkeypatch, capsys, "--profile", "energy-tech:default", "--json")
    assert code == 1
    assert report["problems"]
    assert report["readiness"]["installation"] == "broken"


def test_distribution_missing_is_not_silently_accepted(monkeypatch, capsys):
    def missing(_):
        raise metadata.PackageNotFoundError("doe-mcp")
    monkeypatch.setattr(preflight.metadata, "distribution", missing)
    code, report = run(monkeypatch, capsys, "--json")
    assert code == 1
    assert report["problems"][0]["code"] == "distribution_missing"


@pytest.mark.parametrize("arguments", [
    ["--profile", "unknown", "--json"], ["--online", "--json"],
    ["--online", "--profile", "research:default"],
])
def test_invalid_cli_combinations_exit_two(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["doe-mcp", "doctor", *arguments])
    with pytest.raises(SystemExit) as err:
        main()
    assert err.value.code == 2


def test_text_profile_preflight_is_also_offline(installed, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["doe-mcp", "doctor", "--profile", "energy:default"])
    assert main() == 0
    out = capsys.readouterr().out
    assert "doe-energy-data (ready)" in out
    assert "credentials, network and server startup: not checked" in out


def test_parser_client_keys_match_client_targets():
    from doe_mcp.cli.configure import CLIENT_KEYS, client_targets
    assert set(CLIENT_KEYS) == set(client_targets())
