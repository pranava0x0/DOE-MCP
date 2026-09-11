"""The adapter table and the context it builds.

`ADAPTER_CLASSES` is the one list of adapters; `RuntimeContext` carries a
typed field per adapter so domain code reads `ctx.eia` rather than a dict
lookup. These tests hold the two together, so that a field added to one
and forgotten in the other fails here rather than on the first request the
server takes.
"""
from __future__ import annotations

import dataclasses

import pytest

from doe_mcp.adapters import ADAPTER_CLASSES, ADAPTER_VERSIONS
from doe_mcp.runtime import RuntimeContext, load_context

ROOT_SOURCES = None  # the conftest context is built from the real registry


def test_every_adapter_in_the_table_is_a_context_field(ctx):
    for kind, (field_name, cls) in ADAPTER_CLASSES.items():
        assert hasattr(ctx, field_name), f"{kind}: no field {field_name!r}"
        assert isinstance(getattr(ctx, field_name), cls), (
            f"{kind}: ctx.{field_name} is not a {cls.__name__}")


def test_every_adapter_field_on_the_context_is_in_the_table(ctx):
    classes = tuple(cls for _, cls in ADAPTER_CLASSES.values())
    fields = {f.name for f in dataclasses.fields(RuntimeContext)
              if isinstance(getattr(ctx, f.name), classes)}
    assert fields == {name for name, _ in ADAPTER_CLASSES.values()}


def test_the_version_table_and_the_class_table_name_the_same_adapters():
    assert set(ADAPTER_VERSIONS) == set(ADAPTER_CLASSES) | {"none",
                                                              "self_registry"}


def test_an_unknown_adapter_override_is_refused(credentials):
    from tests.conftest import SOURCES
    with pytest.raises(TypeError, match="not adapter fields"):
        load_context(SOURCES, credentials=credentials, ostii=object())
