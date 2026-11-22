"""Offline profile inspection. No runtime context or user configuration is loaded."""
from __future__ import annotations

import importlib
import re
import sys
from importlib import metadata
from typing import Any

from .. import __version__
from ..core.catalog import SubMcpCatalog
from ..core.registry import AccessMode, SourceRegistry
from ..core.toolreg import DEPRECATED_TOOL_ALIASES, expand_profile
from ..runtime import SOURCES_DIR
from ..servers.build import registries
from ..servers.lineup import for_profile

# This application exact-pins its direct dependencies. Refuse unfamiliar
# metadata instead of silently skipping a dependency or interpreting a marker
# incorrectly. Keep the wheel metadata as the source of versions and extras.
_PIN = re.compile(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)")
_EXTRA = re.compile(r"extra\s*==\s*['\"]([A-Za-z0-9_-]+)['\"]")


def _requirements(dist, extras: tuple[str, ...]) -> list[tuple[str, str]]:
    requirements = []
    for raw in dist.requires or []:
        requirement, separator, marker = raw.partition(";")
        if separator:
            selected = _EXTRA.fullmatch(marker.strip())
            if selected is None:
                raise ValueError("unsupported dependency marker")
            if selected[1] not in extras:
                continue
        pin = _PIN.fullmatch(requirement.strip())
        if pin is None:
            raise ValueError("expected an exact dependency pin")
        requirements.append((pin[1], pin[2]))
    return sorted(set(requirements))


def profile_preflight(profile: str) -> dict[str, Any]:
    """Inspect installed metadata and packaged registries, never key presence.

    Dependency imports validate the selected local install, but entry points
    are never invoked and neither adapters nor a server are constructed.
    """
    server = for_profile(profile)
    if server is None:
        raise ValueError("profile has no shipping server")
    report: dict[str, Any] = {
        "schema_version": 1, "version": __version__, "profile": profile,
        "server": server.name, "required_extras": list(server.required_extras),
        "entry_points": [], "dependencies": [], "registered_tools": [],
        "source_scope": "registry_inventory", "sources": [],
        "credentials": {"status": "not_checked", "requirements": []},
        "readiness": {"installation": "ready", "credentials": "not_checked",
                      "network": "not_checked", "server_startup": "not_checked"},
        "problems": [],
    }

    def problem(code: str, message: str) -> None:
        report["problems"].append({"code": code, "message": message})
        report["readiness"]["installation"] = "broken"

    if sys.version_info < (3, 12):
        problem("python_version", "Python 3.12 or later is required.")
    try:
        dist = metadata.distribution("doe-mcp")
    except metadata.PackageNotFoundError:
        problem("distribution_missing", "Install doe-mcp to provide package metadata and entry points.")
    else:
        for command, expected in (
            ("doe-mcp", "doe_mcp.cli.__main__:main"),
            (server.command, f"doe_mcp.servers.{server.short.replace('-', '_')}:main"),
        ):
            found = [e for e in dist.entry_points
                     if e.group == "console_scripts" and e.name == command]
            status = "ready"
            if len(found) != 1 or found[0].value != expected:
                status = "broken"
            else:
                try:
                    if not callable(found[0].load()):
                        status = "broken"
                except Exception:  # noqa: BLE001
                    status = "broken"
            report["entry_points"].append({
                "command": command, "target": expected, "status": status})
            if status == "broken":
                problem("entry_point", f"Reinstall doe-mcp: {command} entry point is missing or broken.")
        provided = set(dist.metadata.get_all("Provides-Extra") or [])
        if not set(server.required_extras) <= provided:
            problem("extra_metadata", "Reinstall doe-mcp: required extra metadata is missing.")
        try:
            requirements = _requirements(dist, server.required_extras)
        except ValueError:
            problem("dependency_metadata", "Cannot check dependency metadata: expected exact pins and extra markers.")
        else:
            for name, expected_version in requirements:
                status, installed = "ready", None
                try:
                    installed = metadata.version(name)
                except metadata.PackageNotFoundError:
                    status = "missing"
                if installed is not None:
                    if installed != expected_version:
                        status = "version_mismatch"
                    try:
                        module = {"pyyaml": "yaml"}.get(name.lower(), name.replace("-", "_"))
                        importlib.import_module(module)
                        if name.lower() == "pyarrow":
                            importlib.import_module("pyarrow.compute")
                            importlib.import_module("pyarrow.parquet")
                    except Exception:  # noqa: BLE001
                        status = "import_failed"
                report["dependencies"].append({
                    "name": name, "required_version": expected_version,
                    "installed_version": installed, "status": status})
                if status != "ready":
                    package = "doe-mcp"
                    if server.required_extras:
                        package += f"[{','.join(server.required_extras)}]"
                    problem("dependency", f"{name}: {status}. Install or repair {package}.")

    try:
        specs = expand_profile(profile, registries())
        names = [spec.name for spec in specs]
        report["registered_tools"] = names + [
            old for old, current in DEPRECATED_TOOL_ALIASES.items() if current in names]
    except Exception:  # noqa: BLE001
        problem("profile_tools", "Profile tool registration failed; reinstall doe-mcp.")
    try:
        sources = SourceRegistry.load(SOURCES_DIR)
        SubMcpCatalog.load(SOURCES_DIR / "catalog")
    except Exception:  # noqa: BLE001
        problem("registry", "Packaged source registry or catalog failed validation; reinstall doe-mcp.")
    else:
        credentials: dict[str, list[str]] = {}
        for manifest in sorted(sources.manifests.values(), key=lambda m: m.id):
            credential = manifest.access.credential_ref
            report["sources"].append({
                "id": manifest.id, "domain": manifest.domain,
                "server_domain": manifest.domain == server.key,
                "declared_state": manifest.lifecycle.declared_state.value,
                "automation_status": manifest.access.automation_status.value,
                "access_mode": manifest.access.mode.value,
                "selection_block": sources.selection_block(manifest),
                "blocked_reason": manifest.lifecycle.blocked_reason,
                "credential_required": manifest.access.mode in {
                    AccessMode.api_key, AccessMode.account_token, AccessMode.oauth},
                "credential_ref": credential,
                "credential_status": ("not_required" if manifest.access.mode == AccessMode.anonymous
                                      else "not_checked"),
                "network_status": "not_checked",
            })
            if credential:
                credentials.setdefault(credential, []).append(manifest.id)
        report["credentials"]["requirements"] = [
            {"name": name, "source_ids": ids} for name, ids in sorted(credentials.items())]
    return report
