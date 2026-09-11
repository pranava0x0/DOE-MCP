"""Adapters: generic clients per platform genre.

One adapter per genre, never one per source. `osti_family` alone covers four
APIs and roughly 5.7 million records; the same principle holds for the
platform genres the later phases add (CKAN, ArcGIS, S3, OPeNDAP, Globus
Search). A genre is a response shape, not a protocol: `text_feed` reads
tab-delimited operational files, because a publisher that answers in text is
still a platform genre with more than one file behind it.

Importing this package registers every adapter's params model with the
registry, which is what lets manifest validation check an adapter block
against the adapter that will actually read it.
"""
from __future__ import annotations

from . import inventory as _inventory  # noqa: F401  (registers "none")
from .basis_sets import BasisSetExchangeAdapter
from .curated import CuratedAdapter
from .daymet import DaymetAdapter
from .eia_v2 import EiaV2Adapter
from .esgf import EsgfAdapter
from .essdive import EssDiveAdapter
from .federal_register import FederalRegisterAdapter
from .fueleconomy import FuelEconomyAdapter
from .json_document import JsonDocumentAdapter
from .opendatasoft import OpenDataSoftAdapter
from .optimade import OptimadeAdapter
from .osti_family import OstiFamilyAdapter
from .postgrest import PostgrestAdapter
from .sage import SageAdapter
from .text_feed import TextFeedAdapter
from .vips import VipsAdapter

# The one table behind every place that enumerates the adapters: the
# RuntimeContext field each one lives in, keyed by the `adapter.type` string
# the manifests carry. `load_context`, the test context and the site
# builder's replay context are loops over this; adding an adapter is one
# row here plus its module and its RuntimeContext field, and a test holds
# the three together. `curated` reads local tables and takes the sources
# directory; `eia_v2` takes the credential store; every other class takes
# (fetcher, cache).
ADAPTER_CLASSES: dict[str, tuple[str, type]] = {
    "osti_family": ("osti", OstiFamilyAdapter),
    "opendatasoft": ("opendatasoft", OpenDataSoftAdapter),
    "json_document": ("json_document", JsonDocumentAdapter),
    "curated": ("curated", CuratedAdapter),
    "eia_v2": ("eia", EiaV2Adapter),
    "fueleconomy": ("fueleconomy", FuelEconomyAdapter),
    "text_feed": ("text_feed", TextFeedAdapter),
    "postgrest": ("postgrest", PostgrestAdapter),
    "federal_register": ("federal_register", FederalRegisterAdapter),
    "vips": ("vips", VipsAdapter),
    "daymet": ("daymet", DaymetAdapter),
    "essdive": ("essdive", EssDiveAdapter),
    "esgf": ("esgf", EsgfAdapter),
    "sage": ("sage", SageAdapter),
    "optimade": ("optimade", OptimadeAdapter),
    "basis_sets": ("basis_sets", BasisSetExchangeAdapter),
}

ADAPTER_VERSIONS = {
    "osti_family": OstiFamilyAdapter.version,
    "opendatasoft": OpenDataSoftAdapter.version,
    "json_document": JsonDocumentAdapter.version,
    "curated": CuratedAdapter.version,
    "eia_v2": EiaV2Adapter.version,
    "fueleconomy": FuelEconomyAdapter.version,
    "text_feed": TextFeedAdapter.version,
    "postgrest": PostgrestAdapter.version,
    "federal_register": FederalRegisterAdapter.version,
    "vips": VipsAdapter.version,
    "daymet": DaymetAdapter.version,
    "essdive": EssDiveAdapter.version,
    "esgf": EsgfAdapter.version,
    "sage": SageAdapter.version,
    "optimade": OptimadeAdapter.version,
    "basis_sets": BasisSetExchangeAdapter.version,
    "none": "1",
    "self_registry": "1",
}

__all__ = ["OstiFamilyAdapter", "OpenDataSoftAdapter", "JsonDocumentAdapter",
           "CuratedAdapter", "EiaV2Adapter", "FuelEconomyAdapter",
           "TextFeedAdapter", "PostgrestAdapter",
           "FederalRegisterAdapter", "VipsAdapter", "DaymetAdapter",
           "EssDiveAdapter", "EsgfAdapter", "SageAdapter",
           "OptimadeAdapter", "BasisSetExchangeAdapter",
           "ADAPTER_CLASSES", "ADAPTER_VERSIONS"]
