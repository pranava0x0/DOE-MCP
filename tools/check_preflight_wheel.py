"""Smoke-test a wheel in fresh base and energy-tech installs outside the checkout.

Usage: python tools/check_preflight_wheel.py /absolute/path/to/doe_mcp.whl
The install steps use pip; every doctor invocation must remain offline.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


GUARDED_CLI = """
import getpass
import os
import socket
import sys
from pathlib import Path
import httpx
import doe_mcp
import doe_mcp.cli.__main__ as cli
import doe_mcp.cli.configure as configure
import doe_mcp.core.credentials as credentials
import doe_mcp.runtime as runtime
import doe_mcp.servers.build as build

assert Path(doe_mcp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())

def forbidden(*args, **kwargs):
    raise AssertionError('preflight attempted a forbidden runtime operation')

credentials.Credentials.load = forbidden
credentials.default_path = forbidden
cli._load_ctx = forbidden
cli.client_targets = forbidden
configure.client_targets = forbidden
runtime.load_context = forbidden
build.build_server = forbidden
getpass.getpass = forbidden
httpx.Client.request = forbidden
httpx.AsyncClient.request = forbidden
socket.socket.connect = forbidden
environ_type = type(os.environ)
getitem = environ_type.__getitem__
def guarded(self, key):
    if key in credentials.CREDENTIAL_SPECS:
        forbidden()
    return getitem(self, key)
environ_type.__getitem__ = guarded
path_open = Path.open
def guarded_open(path, *args, **kwargs):
    if path.name in {'credentials.env', '.claude.json', 'claude_desktop_config.json', 'mcp.json'}:
        forbidden()
    return path_open(path, *args, **kwargs)
Path.open = guarded_open
sys.argv = ['doe-mcp', 'doctor', '--profile', sys.argv[1], '--json']
raise SystemExit(cli.main())
"""


def main() -> None:
    wheel = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="doe-preflight-wheel-") as temporary:
        root = Path(temporary)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        for key in ("EIA_API_KEY", "API_DATA_GOV_KEY", "MP_API_KEY", "ESS_DIVE_TOKEN"):
            environment[key] = "preflight-secret-must-not-be-read"
        for extra in (False, True):
            venv = root / ("energy-tech" if extra else "base")
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, cwd=root)
            bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
            python = bin_dir / ("python.exe" if os.name == "nt" else "python")
            requirement = str(wheel) + ("[energy-tech]" if extra else "")
            subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                            requirement], check=True, cwd=root, env=environment)
            for profile in ("research:default", "energy:default", "energy-tech:default", "energy-tech:all"):
                expected = 1 if profile.startswith("energy-tech:") and not extra else 0
                reports = []
                for command in (
                    [str(bin_dir / "doe-mcp"), "doctor", "--profile", profile, "--json"],
                    [str(python), "-c", GUARDED_CLI, profile],
                ):
                    completed = subprocess.run(command, cwd=root, env=environment,
                                               text=True, capture_output=True, check=False)
                    assert completed.returncode == expected, completed.stderr + completed.stdout
                    report = json.loads(completed.stdout)
                    reports.append(report)
                    assert report["readiness"]["installation"] == ("broken" if expected else "ready")
                    assert report["credentials"]["status"] == "not_checked"
                    assert "preflight-secret" not in completed.stdout + completed.stderr
                    pvwatts = next(s for s in report["sources"] if s["id"] == "nlr-pvwatts")
                    assert pvwatts["declared_state"] == "proposed"
                    assert pvwatts["selection_block"] == "source_not_activated"
                    if profile.startswith("energy-tech:"):
                        arrow = next(d for d in report["dependencies"] if d["name"] == "pyarrow")
                        assert arrow["status"] == ("ready" if extra else "missing")
                assert reports[0] == reports[1], "runtime interception changed the preflight report"
                print(f"{'energy-tech' if extra else 'base'} wheel: {profile} exit {expected}; offline checks passed")


if __name__ == "__main__":
    main()
