"""The `basis_sets` adapter: the Basis Set Exchange's catalog and its files.

A basis set is the list of functions a quantum-chemistry calculation expands
its orbitals in, and choosing one is the first decision in any such
calculation. The Basis Set Exchange holds 776 of them. Its lineage is DOE's:
the original was built at PNNL's EMSL, and MolSSI rebuilt and now maintains
it with PNNL.

The service is two things behind one host, and the split is the reason this
adapter has two operations rather than one:

- **A catalog.** `/api/metadata/` is a 527 KB JSON document listing every
  set with its family, role, function types, and — per version — the
  elements it covers, as atomic numbers. It is fetched whole and searched in
  memory, like the `json_document` adapter's catalogs. "Which basis sets
  cover uranium" is a real question and this document is the only thing that
  answers it.
- **The sets themselves**, rendered on request into one of 25 program input
  formats: NWChem, Gaussian, Psi4, ORCA, Molpro and the rest. The response
  is that program's own input text, not JSON, because that is the artefact —
  a caller pastes it into a calculation.

Two behaviours shape the code:

1. **A set that does not exist is a 404 carrying `{"error": true,
    "message": "Basis set 'x' does not exist"}`.** The fetch path reports a
    404 as an outage, which is wrong here in the same direction ESS-DIVE's
    is: the service is running and has answered. Translated to
    `InvalidQuery` naming the catalog, per decision 0024.

2. **The references are a separate request in a separate format.** A basis
    set's citations are not in the data file; they come from
    `/api/references/<name>/format/bib/` for the same elements. The
    publisher asks that sets be cited, so an answer that returned the
    functions without the references would be handing over exactly the part
    that needs attribution and dropping the attribution. Both are fetched
    and returned together.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core.errors import (InvalidQuery, SourceSchemaChanged,
                           SourceUnavailable)
from ..core.registry import SourceManifest, register_adapter_params
from .base import (Fetched, Fetcher, FetchResult, HttpFetcher, TextFetcher,
                   TTLCache, egress_policy_for, log_source_call, shared_cache)

ADAPTER_VERSION = "1"

# One path segment. Basis-set names carry punctuation — `6-31g*`, `cc-pvdz`,
# `def2-universal-jkfit`, `aug-cc-pv(d+d)z` — so the set is permissive about
# which characters, and absolute about there being no separator: no slash,
# no backslash, and neither dot-segment, because "." and ".." are made only
# of characters this set allows and are exactly what would climb out of the
# path this URL is built from.
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.,()+*\-]{1,120}")

DEFAULT_ROWS = 20
MAX_ROWS = 200

# The periodic table by atomic number, which is how this catalog records
# element coverage: a version's `elements` list is ["1", "2", ...]. Held here
# so a caller can ask for uranium rather than for 92 — the question is asked
# in symbols and the data is stored in numbers.
ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co "
    "Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb "
    "Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
    "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es "
    "Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split()
NUMBER_FOR_SYMBOL = {s.lower(): str(i + 1)
                     for i, s in enumerate(ELEMENT_SYMBOLS)}
SYMBOL_FOR_NUMBER = {str(i + 1): s for i, s in enumerate(ELEMENT_SYMBOLS)}


class BasisSetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    metadata_path: str = "/api/metadata/"
    formats_path: str = "/api/formats/"
    basis_path: str = "/api/basis"
    references_path: str = "/api/references"
    reference_format: str = "bib"
    """The citation format requested alongside every set. BibTeX because it
    is the one every one of these publishers' journals accepts."""


register_adapter_params("basis_sets", BasisSetParams)


@dataclass
class BasisSetEntry:
    key: str
    """The catalog key, which is what the data endpoints take."""
    name: str
    family: str | None
    role: str | None
    description: str | None
    function_types: list[str] = field(default_factory=list)
    latest_version: str | None = None
    other_names: list[str] = field(default_factory=list)
    element_numbers: list[str] = field(default_factory=list)

    @property
    def elements(self) -> list[str]:
        return [SYMBOL_FOR_NUMBER[n] for n in self.element_numbers
                if n in SYMBOL_FOR_NUMBER]

    def covers(self, numbers: list[str]) -> bool:
        return set(numbers) <= set(self.element_numbers)


@dataclass
class BasisSetCatalog:
    entries: list[BasisSetEntry]
    total: int

    def find(self, key: str) -> BasisSetEntry | None:
        wanted = key.strip().lower()
        for entry in self.entries:
            if entry.key.lower() == wanted or entry.name.lower() == wanted:
                return entry
            if any(n.lower() == wanted for n in entry.other_names):
                return entry
        return None


@dataclass
class RenderedBasisSet:
    key: str
    name: str
    output_format: str
    elements: list[str]
    data: str
    """The program's own input text. The artefact, not a description of it."""
    references: str | None
    notes: str | None


def parse_catalog(payload: Any, source_id: str) -> BasisSetCatalog:
    if not isinstance(payload, dict) or not payload:
        raise SourceSchemaChanged(
            f"{source_id}: the metadata document is a "
            f"{type(payload).__name__} rather than an object keyed by basis "
            "set name.")
    entries = []
    for key, body in payload.items():
        if not isinstance(body, dict):
            continue
        latest = body.get("latest_version")
        versions = body.get("versions")
        version = (versions.get(str(latest))
                   if isinstance(versions, dict) and latest is not None
                   else None)
        entries.append(BasisSetEntry(
            key=key,
            name=body.get("display_name") or key,
            family=body.get("family"),
            role=body.get("role"),
            description=body.get("description"),
            function_types=list(body.get("function_types") or []),
            latest_version=str(latest) if latest is not None else None,
            other_names=list(body.get("other_names") or []),
            element_numbers=[str(e) for e in (version or {}).get(
                "elements", [])]))
    if not entries:
        raise SourceSchemaChanged(
            f"{source_id}: the metadata document held no basis sets.")
    return BasisSetCatalog(entries=entries, total=len(entries))


def element_numbers(elements: list[str]) -> list[str]:
    """Symbols or atomic numbers to atomic numbers, refusing what is
    neither. The catalog stores numbers and callers ask in symbols."""
    out = []
    for raw in elements:
        text = raw.strip()
        if not text:
            continue
        if text.isdigit() and text in SYMBOL_FOR_NUMBER:
            out.append(text)
            continue
        number = NUMBER_FOR_SYMBOL.get(text.lower())
        if number is None:
            raise InvalidQuery(
                f"{raw!r} is not an element symbol or atomic number. Use "
                "symbols such as 'H', 'C', 'U', or numbers 1-118.")
        out.append(number)
    return out


class BasisSetExchangeAdapter:
    """Read-only."""

    version = ADAPTER_VERSION

    def __init__(self, fetcher: Fetcher | None = None,
                 cache: TTLCache | None = None) -> None:
        self._fetcher = fetcher
        self._cache = cache or shared_cache()

    @staticmethod
    def params_for(manifest: SourceManifest) -> BasisSetParams:
        return BasisSetParams.model_validate(
            manifest.adapter.model_dump(exclude={"type"}))

    def _fetcher_for(self, manifest: SourceManifest, params: BasisSetParams):
        if self._fetcher is not None:
            return self._fetcher
        return HttpFetcher(policy=egress_policy_for(manifest,
                                                    params.base_url))

    async def catalog(self,
                      manifest: SourceManifest) -> Fetched[BasisSetCatalog]:
        params = self.params_for(manifest)
        url = f"{params.base_url.rstrip('/')}{params.metadata_path}"
        result = await self._fetch_json(manifest, params, url)
        catalog = parse_catalog(result.payload, manifest.id)
        log_source_call(manifest, "catalog", {}, catalog.total)
        return Fetched.of(result, catalog)

    async def formats(self,
                      manifest: SourceManifest) -> Fetched[dict[str, str]]:
        """The program input formats this service renders into, as the
        service names them. Read rather than listed here: a caller passing a
        format the service does not have gets a 404, and a table in this
        repository would be one release behind the day it was written."""
        params = self.params_for(manifest)
        url = f"{params.base_url.rstrip('/')}{params.formats_path}"
        result = await self._fetch_json(manifest, params, url)
        payload = result.payload
        if not isinstance(payload, dict) or not payload:
            raise SourceSchemaChanged(
                f"{manifest.id}: the formats endpoint returned no formats.")
        log_source_call(manifest, "formats", {}, len(payload))
        return Fetched.of(result, {str(k): str(v)
                                   for k, v in payload.items()})

    async def render(self, manifest: SourceManifest, *, key: str,
                     output_format: str, elements: list[str],
                     name: str = "") -> Fetched[RenderedBasisSet]:
        """One basis set as a program's input text, with its citations.

        The references are a second request. They are not optional: the
        publisher asks that sets be cited, and returning the functions
        without the citations would drop exactly the part that needs
        attributing.
        """
        params = self.params_for(manifest)
        # Both of these land in the URL PATH rather than in a query
        # parameter, so they are checked here as well as in the tool that
        # calls it. The tool resolves `key` against the publisher's own
        # catalog and `output_format` against the published format list,
        # which makes this belt-and-braces — but an adapter method is a
        # public entry point and the next caller may not do either.
        for name, value in (("key", key), ("format", output_format)):
            if (not value or value in (".", "..")
                    or not _PATH_SEGMENT.fullmatch(value)):
                raise InvalidQuery(
                    f"{value!r} is not a usable basis-set {name}: it must be "
                    "a single path segment of letters, digits and the "
                    "punctuation these names use.")
        numbers = element_numbers(elements)
        query = {"elements": ",".join(numbers)} if numbers else {}
        base = params.base_url.rstrip("/")
        data_url = f"{base}{params.basis_path}/{key}/format/{output_format}/"
        data = await self._fetch_text(manifest, params, data_url, query, key)

        references = None
        try:
            reference_url = (f"{base}{params.references_path}/{key}/format/"
                             f"{params.reference_format}/")
            references = (await self._fetch_text(
                manifest, params, reference_url, query, key)).payload
        except (SourceUnavailable, SourceSchemaChanged):
            # The set itself was served. A missing reference file is worth
            # reporting as an absence rather than losing the answer over,
            # and the tool says the citations could not be read.
            references = None

        log_source_call(manifest, "render",
                        {"key": key, "format": output_format}, 1)
        return Fetched.of(data, RenderedBasisSet(
            key=key, name=name or key, output_format=output_format,
            elements=[SYMBOL_FOR_NUMBER[n] for n in numbers],
            data=data.payload, references=references, notes=None))

    async def _fetch_json(self, manifest: SourceManifest,
                          params: BasisSetParams, url: str) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, {}, ttl)
        if cached is not None:
            return cached
        response = await self._fetcher_for(manifest, params).fetch_json(
            url, {})
        return self._cache.put(manifest.id, url, {}, response.payload,
                               response.headers)

    async def _fetch_text(self, manifest: SourceManifest,
                          params: BasisSetParams, url: str,
                          query: dict[str, str], key: str) -> FetchResult:
        ttl = manifest.freshness.ttl_hint_seconds
        cached = self._cache.get(manifest.id, url, query, ttl)
        if cached is not None:
            return cached
        fetcher: TextFetcher = self._fetcher_for(manifest, params)
        try:
            response = await fetcher.fetch_text(url, query)
        except SourceUnavailable as refusal:
            if refusal.status != 404:
                raise
            # The service says which name it does not know, and it is
            # running: that is a wrong argument rather than an outage
            # (decision 0024).
            raise InvalidQuery(
                f"the Basis Set Exchange has no basis set or format matching "
                f"{key!r} at that path. Search the catalog with "
                "chemistry.search_basis_sets, which lists the exact keys and "
                "the formats this service renders.") from refusal
        return self._cache.put(manifest.id, url, query, response.text,
                               response.headers)
