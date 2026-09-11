"""The `none` adapter: an endpoint-shaped hole.

A registry that can only describe systems it can call would have to drop
every source it knows about and cannot reach yet — which in this ecosystem is
most of them: 4.8 PB behind an S3 catalog, a geothermal repository with no
API, two databases whose publishers have anti-automation postures, and a long
tail of "we found it, we have not verified the terms". Dropping those is how
an inventory becomes a lie of omission.

So they get manifests with `adapter: {type: none}`. The activation gate
refuses to let one go active, so it can never be queried or probed, and
`registry.describe_source` still answers everything a caller needs to reach
the data themselves.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..core.registry import register_adapter_params


class NoAdapterParams(BaseModel):
    """No endpoint, so no parameters. `extra=forbid` means a contributor who
    pastes a service_url into an inventory row is told to promote the
    manifest instead of quietly half-wiring it."""

    model_config = ConfigDict(extra="forbid")


register_adapter_params("none", NoAdapterParams)


class SelfRegistryParams(BaseModel):
    """The `self_registry` adapter: this project's own registry, described as
    a source.

    It looks redundant and is not. Several tools answer from DOE-MCP's own
    tables rather than from a publisher's system — `registry.resolve_org`
    reads the organization table, `registry.search_sources` reads the
    manifests — and their provenance already names `doe-mcp-registry`. Without
    a manifest behind that name, a provenance entry pointed at nothing, which
    breaks the rule that every entry must resolve to a registered source.

    It also makes the distinction visible where it matters: an answer about
    what Oak Ridge publishes is DOE-MCP's inventory of ORNL, not ORNL's own
    statement about itself, and the authority level says so.
    """

    model_config = ConfigDict(extra="forbid")


register_adapter_params("self_registry", SelfRegistryParams)
