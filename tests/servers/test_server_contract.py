"""Server assembly: sizing bands, determinism, annotations, output schemas."""
from __future__ import annotations

import pytest

from doe_mcp.core.toolreg import (DEPRECATED_TOOL_ALIASES, PROFILE_DEFAULT_CEILING,
                                  PROFILE_HARD_CEILING, PROFILES, ToolRegistry,
                                  ToolSpec, expand_profile, resolve_alias)
from doe_mcp.cli.configure import SERVERS
from doe_mcp.servers.build import (COVERAGE_RULE, NON_AFFILIATION,
                                   SERVER_FOR_PROFILE, SERVER_INSTRUCTIONS,
                                   build_server, registries)
from doe_mcp.servers.lineup import SERVER_LINEUP, shipping


def test_every_profile_stays_inside_the_sizing_bands():
    """decision 0014's numbers are where measured tool-selection accuracy
    falls off, not a style preference."""
    for profile in PROFILES:
        specs = expand_profile(profile, registries())
        assert len(specs) <= PROFILE_HARD_CEILING, profile
        if profile.endswith(":default"):
            assert len(specs) <= PROFILE_DEFAULT_CEILING, profile


def test_an_oversized_profile_refuses_to_start():
    """Enforced at runtime, not only in CI: a server that would ship 30 tools
    must fail loudly rather than quietly degrade selection."""
    reg = ToolRegistry(package="bloat")
    for i in range(PROFILE_HARD_CEILING + 1):
        reg.register(ToolSpec(name=f"bloat.t{i}", description="d",
                              toolset="default", contract_version="1",
                              fn=lambda ctx: None))
    PROFILES["bloat:test"] = [("bloat", "default")]
    try:
        with pytest.raises(ValueError, match="over decision 0014's ceiling"):
            expand_profile("bloat:test", {"bloat": reg})
    finally:
        del PROFILES["bloat:test"]


def test_a_profile_naming_a_missing_package_refuses_to_start():
    PROFILES["ghost:test"] = [("nonexistent", "default")]
    try:
        with pytest.raises(ValueError, match="silently smaller tool surface"):
            expand_profile("ghost:test", registries())
    finally:
        del PROFILES["ghost:test"]


def test_tool_order_is_deterministic():
    """`tools/list` SHOULD be deterministic per the 2026-07-28 spec."""
    first = [s.name for s in expand_profile("research:default", registries())]
    second = [s.name for s in expand_profile("research:default", registries())]
    assert first == second


def test_tool_names_are_unique_across_a_profile():
    for profile in PROFILES:
        names = [s.name for s in expand_profile(profile, registries())]
        assert len(names) == len(set(names)), profile


def test_the_alias_table_ships_empty_but_wired():
    assert DEPRECATED_TOOL_ALIASES == {}, (
        "the table ships empty; a rename adds a row and never deletes one")
    assert resolve_alias("research.search_literature") == \
        "research.search_literature"


async def test_every_tool_binds_with_an_output_schema(ctx):
    """Domain modules use string annotations the SDK cannot resolve from its
    own module. A regression here surfaces as a warning, not an error, so it
    is asserted."""
    server = build_server(ctx, "research:discovery")
    tools = await server.list_tools()
    assert len(tools) == 14
    for tool in tools:
        assert tool.output_schema, (
            f"{tool.name} bound without an output schema")
        assert tool.input_schema


async def test_every_tool_is_annotated_read_only(ctx):
    server = build_server(ctx, "research:discovery")
    for tool in await server.list_tools():
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        # open_world_hint: every tool reaches a live government API whose
        # contents this project does not control.
        assert tool.annotations.open_world_hint is True, tool.name


def test_server_instructions_carry_the_coverage_rule_and_disclaimer():
    """Two things a caller must never have to infer: what an empty result
    means, and that this is not a government system."""
    for name, text in SERVER_INSTRUCTIONS.items():
        assert COVERAGE_RULE in text, name
        assert NON_AFFILIATION in text, name
        assert "NOT affiliated" in text, name


async def test_a_typed_error_reaches_the_caller_as_a_tool_error(ctx):
    from mcp.server.mcpserver.exceptions import ToolError
    from doe_mcp.servers.build import _bind
    spec = next(s for s in expand_profile("research:all", registries())
                if s.name == "research.search_literature")
    handler = _bind(spec, ctx)
    with pytest.raises(ToolError) as err:
        await handler()          # no filters at all
    assert "InvalidQuery" in str(err.value)
    assert "Traceback" not in str(err.value)


async def test_the_audit_log_records_names_not_query_text(ctx):
    """A literature search can carry an unpublished research idea."""
    from doe_mcp.servers.build import _bind
    spec = next(s for s in expand_profile("research:all", registries())
                if s.name == "research.search_literature")
    await _bind(spec, ctx)(query="perovskite solar", rows=3)
    record = ctx.audit.recent(1)[0]
    assert record.args == {"query": "<redacted>", "rows": "<redacted>"}
    assert "perovskite" not in str(record.args)
    assert record.source_ids == ["osti-gov-records", "osti-doe-pages"]


async def test_each_profile_gets_its_own_servers_instructions(ctx):
    """`doe-energy-data` once served the research server's guidance, because
    the name was derived by prefixing "doe-" to the profile and landed on
    `doe-energy`, which had no entry. A model told to walk OSTI's collections
    while holding EIA's tools is being actively misled."""
    for profile, expected in [("research:default", "doe-research"),
                              ("energy:default", "doe-energy-data"),
                              ("earth:default", "doe-earth"),
                              ("materials:default", "doe-materials")]:
        server = build_server(ctx, profile)
        assert server.name == expected, profile
        assert server.instructions == SERVER_INSTRUCTIONS[expected], profile

    for profile in PROFILES:
        prefix = profile.split(":", 1)[0]
        assert prefix in SERVER_FOR_PROFILE, (
            f"profile {profile!r} would inherit another server's "
            "instructions")


def test_every_shipping_server_in_the_lineup_is_complete():
    """One table drives configure, assembly, the site, and the README. A
    shipping row missing any of its facts would break one of them."""
    for spec in shipping():
        assert spec.default_profile in PROFILES, spec.name
        assert spec.instructions and COVERAGE_RULE in spec.instructions
        assert spec.description.endswith(".")
    assert len({s.name for s in SERVER_LINEUP}) == len(SERVER_LINEUP)
    assert len({s.key for s in SERVER_LINEUP}) == len(SERVER_LINEUP)


def test_configure_and_the_assembler_read_the_same_lineup():
    assert set(SERVERS) == {s.short for s in shipping()}
    for spec in shipping():
        assert SERVERS[spec.short]["command"] == spec.name
        assert SERVER_FOR_PROFILE[spec.key] == spec.name
        assert SERVER_INSTRUCTIONS[spec.name] == spec.instructions


def test_earth_instructions_name_the_traps_its_publishers_set():
    """Three of the four publishers behind this server answer a wrong
    request with data rather than an error, and the archive behind
    climate.search_cmip returns withdrawn model output first unless asked
    not to. A model holding these tools and not told either is being set up
    to report a confident wrong answer."""
    text = SERVER_INSTRUCTIONS["doe-earth"]
    assert "climate.discover_facets before" in text
    assert "modelled estimates" in text
    assert "inventory of where the sensors are" in text


def test_materials_instructions_separate_the_two_routes_to_one_publisher():
    """The Materials Project is a name people trust, and this server reaches
    one part of what it covers over a keyless standard endpoint. A model
    told neither that the values are computed nor that the route is narrow
    will report a formation energy it never had access to."""
    text = SERVER_INSTRUCTIONS["doe-materials"]
    assert "COMPUTED under a stated functional" in text
    assert "keyed" in text
    assert "describe_structure_fields before" in text


def test_energy_instructions_name_the_eia_key_confusion():
    """EIA runs its own key system. Supplying an api.data.gov key produces a
    403 that reads like an outage."""
    text = SERVER_INSTRUCTIONS["doe-energy-data"]
    assert "not an api.data.gov key" in text
    assert "energy.discover_routes before energy.get_data" in text
