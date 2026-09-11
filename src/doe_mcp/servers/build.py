"""Assemble an MCPServer from domain tool registries.

decision 0004 chose the official SDK over FastMCP, so all SDK contact is
confined to this one module. That is the shim layer the decision record
mentions, and it now points the other way: if a hosted tier ever reopens and
FastMCP's auth and middleware batteries become worth having, this file is
what changes and nothing else does.

Registration order is deterministic — profile order, then registry order —
because `tools/list` SHOULD be deterministic per the 2026-07-28 spec, and a
contract test asserts it. Every tool is read-only and annotated as such.
"""
from __future__ import annotations

import inspect
import time
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..core.audit import error_record, record_from_envelope
from ..core.errors import DoeMcpError
from ..core.toolreg import (DEPRECATED_TOOL_ALIASES, ToolSpec, expand_profile)
from ..domains.discovery import DISCOVERY_TOOLS
from ..domains.docs import DOCS_TOOLS
from ..domains.earth import EARTH_TOOLS
from ..domains.energy import ENERGY_TOOLS
from ..domains.materials import MATERIALS_TOOLS
from ..domains.registry_tools import REGISTRY_TOOLS
from ..domains.research import RESEARCH_TOOLS
from ..domains.tech import TECH_TOOLS
from ..runtime import RuntimeContext
from .lineup import (COVERAGE_RULE, NON_AFFILIATION,
                     UNTRUSTED_CONTENT_RULE, for_profile, shipping)

__all__ = ["COVERAGE_RULE", "NON_AFFILIATION",
           "UNTRUSTED_CONTENT_RULE", "SERVER_FOR_PROFILE",
           "SERVER_INSTRUCTIONS", "build_server", "registries"]

# Both tables are views of the lineup, kept under their old names because the
# contract tests read them.
SERVER_INSTRUCTIONS = {s.name: s.instructions for s in shipping()}


def registries():
    return {"research": RESEARCH_TOOLS, "registry": REGISTRY_TOOLS,
            "discovery": DISCOVERY_TOOLS, "energy": ENERGY_TOOLS,
            "docs": DOCS_TOOLS, "tech": TECH_TOOLS,
            "earth": EARTH_TOOLS, "materials": MATERIALS_TOOLS}


def _bind(spec: ToolSpec, ctx: RuntimeContext):
    async def wrapper(**kwargs: Any):
        started = time.monotonic()
        try:
            envelope = await spec.fn(ctx, **kwargs)
        except DoeMcpError as err:
            ctx.audit.append(error_record(
                tool=spec.name, args=kwargs, error_code=err.code,
                duration_ms=int((time.monotonic() - started) * 1000),
                registry_revision=ctx.sources.revision,
                redact_values=ctx.redact_audit_args()))
            # Typed errors are anticipated failures whose message is written
            # FOR the model; ToolError is the SDK's pass-through for exactly
            # that, and keeps stack traces out of the wire.
            raise ToolError(err.model_message()) from err
        ctx.audit.append(record_from_envelope(
            spec.name, kwargs, envelope,
            duration_ms=int((time.monotonic() - started) * 1000),
            redact_values=ctx.redact_audit_args()))
        return envelope

    # Domain modules use `from __future__ import annotations`, so type hints
    # are strings the SDK cannot resolve from its own module. eval_str
    # resolves them here; a contract test asserts every bound tool got an
    # output schema, so a regression cannot slip through as a warning.
    sig = inspect.signature(spec.fn, eval_str=True)
    params = [p for name, p in sig.parameters.items() if name != "ctx"]
    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        params, return_annotation=sig.return_annotation)
    wrapper.__annotations__ = {
        name: p.annotation for name, p in sig.parameters.items()
        if name != "ctx"} | {"return": sig.return_annotation}
    wrapper.__name__ = spec.name.replace(".", "_")
    wrapper.__doc__ = spec.description
    return wrapper


# Profile prefix -> server name, from the lineup's `key`. An explicit
# mapping rather than a derivation from the profile: "energy:default" belongs
# to `doe-energy-data`, and deriving the name by prefixing "doe-" produced
# `doe-energy`, which missed its own instructions and silently served the
# research server's guidance instead. A test pins it.
SERVER_FOR_PROFILE = {s.key: s.name for s in shipping()}


def build_server(ctx: RuntimeContext, profile: str) -> MCPServer:
    specs = expand_profile(profile, registries())
    spec_for_server = for_profile(profile)
    if spec_for_server is None or not spec_for_server.instructions:
        raise ValueError(
            f"profile {profile!r} has no shipping server in the lineup. Add "
            "one with its own instructions rather than letting it inherit "
            "another server's.")
    server = MCPServer(name=spec_for_server.name, version=ctx.server_version,
                       instructions=spec_for_server.instructions)
    # open_world_hint is true because every one of these reaches a live
    # government API whose contents this project does not control.
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                  open_world_hint=True)
    registered: dict[str, ToolSpec] = {}
    for spec in specs:
        server.tool(name=spec.name, description=spec.description,
                    annotations=annotations)(_bind(spec, ctx))
        registered[spec.name] = spec

    for old_name, current in DEPRECATED_TOOL_ALIASES.items():
        spec = registered.get(current)
        if spec is None:
            continue  # alias targets a tool outside this profile
        server.tool(
            name=old_name,
            description=f"Deprecated alias of {current}. " + spec.description,
            annotations=annotations)(_bind(spec, ctx))
    return server
