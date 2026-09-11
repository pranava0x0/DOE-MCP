#!/usr/bin/env python3
"""Record replay fixtures from live publisher responses.

Run from the repository root, online:

    python tools/record_fixtures.py

Fixtures are recorded rather than hand-written on purpose. A hand-written
fixture agrees with the code that consumes it by construction, which makes it
useless as a check on either — it tests that the parser parses what the
parser expects. A recorded one is the publisher's own bytes, so when OSTI
changes a field name the contract test fails and `SourceSchemaChanged` means
something.

Large documents are trimmed to a stated number of entries. The trim is
recorded in the fixture's note, because a fixture that silently holds 15 of
483 entries would make a coverage test lie.

Keyed sources are recorded when their credential is configured and skipped
with a note when it is not. The recorder replaces the credential in the
request parameters and anywhere the publisher echoed it before anything
reaches disk, and refuses to write if the value survived; a test replays
that guarantee with a sentinel key.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import doe_mcp.adapters  # noqa: E402,F401
from doe_mcp.adapters.base import HttpFetcher, egress_policy_for  # noqa: E402
from doe_mcp.adapters.postgrest import parse_filters  # noqa: E402
from doe_mcp.adapters.replay import RecordingFetcher, record_through  # noqa: E402
from doe_mcp.runtime import load_context  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
THIRD_PARTY = ROOT / "THIRD_PARTY_DATA.yml"

# (source_id, description, request spec). Each entry is one interaction a
# contract test replays.
PLANS: dict[str, list[tuple[str, str]]] = {
    "osti-gov-records": [
        ("unfiltered head", "search:rows=1"),
        ("topic search", "search:q=perovskite solar,rows=3"),
        # A wider page of the SAME query, because the deduplication test
        # needs the two collections to actually overlap and they no longer
        # do in the first three hits: OSTI.GOV leads with technical reports
        # (10.2172/...) and DOE PAGES with the journal versions. At five they
        # overlap again. Recorded on both collections or the fan-out replays
        # only half.
        ("topic search, wider page", "search:q=perovskite solar,rows=5"),
        ("no matches", "search:q=zzzznotarealtopiczzzz,rows=3"),
        # Two single-record lookups, because research.get_record now answers
        # "can I actually read this?" as well, and the two answers are
        # different records rather than different code paths: OSTI serves
        # the full text of the first and exposes none for the second.
        ("record with full text", "record:3413920"),
        ("record with no full text", "record:3389573"),
    ],
    "osti-doe-pages": [
        ("topic search", "search:q=perovskite solar,rows=3"),
        ("topic search, wider page", "search:q=perovskite solar,rows=5"),
        # Recorded so the empty-result trap replays BOTH collections. With
        # only one recorded, the other raised and the test exercised the
        # source-failure path while claiming to test the empty path.
        ("no matches", "search:q=zzzznotarealtopiczzzz,rows=3"),
    ],
    "osti-data-explorer": [
        ("repository filter", "search:site_ownership_code=DOE-GDR,rows=2"),
        # The cross-catalog fan-out's own query, so the discovery tests
        # replay the real four-catalog shape rather than an empty one.
        ("fan-out query", "search:q=geothermal,rows=2"),
    ],
    "osti-doe-code": [
        ("software search", "search:all_fields=machine learning,rows=3"),
    ],
    "ornl-openenergyhub": [("catalog head", "ods:limit=3"),
                           ("fan-out query", "ods:limit=2,text=geothermal")],
    "fueleconomy-ws": [("year menu", "fe:menu:year"),
                       ("fuel prices", "fe:prices")],
    "bpa-operations": [
        # Both feeds, because they do not carry the same columns: the
        # adapter reads the header row, and a fixture with only one file
        # would not exercise that.
        ("interchange feed", "text:balancing_authority_interchange"),
        ("wind feed", "text:balancing_authority"),
    ],
    # Both USGS databases, and for each the OpenAPI document as well as
    # rows: the adapter reads the column list out of that document before it
    # sends a filter, so a fixture without it could not replay a query. The
    # filtered pair exists because the count query and the row query are two
    # requests against the same filters.
    "usgs-uswtdb": [
        ("published schema", "pg:describe"),
        ("head of the table", "pg:rows"),
        ("one state", "pg:rows=t_state=eq.RI"),
    ],
    "usgs-uspvdb": [
        ("published schema", "pg:describe"),
        ("head of the table", "pg:rows"),
        ("one state", "pg:rows=p_state=eq.RI"),
    ],
    # The agency record as well as searches: the tool checks an agency
    # argument against the API's own list of DOE's fourteen child agencies
    # before sending it. The rule search and the notice search are both
    # recorded because they behave completely differently under the FERC
    # scope filter — a page of notices is mostly FERC and a page of rules is
    # mostly not, and only recording the first would hide that.
    "federal-register-doe": [
        ("agency record", "fr:agencies"),
        ("recent rules", "fr:search=RULE"),
        ("recent notices", "fr:search=NOTICE"),
        # A page of notices deeper in the same result set, because the
        # scope filter's own "everything on this page was FERC's" case
        # needs a page where that is true. Page one held it until
        # 2026-09-09, when DOE published a notice of its own and the
        # re-recorded fixture quietly stopped exercising the filter.
        ("a page of notices that is entirely FERC's", "fr:page=NOTICE"),
        ("one document", "fr:document"),
        ("one document outside the scope filter", "fr:excluded"),
    ],
    # The laboratory list as well as searches, because the tool checks a
    # lab acronym against it before searching — this service answers an
    # unknown acronym with zero results and no error. Three labs are
    # recorded because the crosswalk claim is that one source answers for
    # all of them, and one record whole because the search view drops the
    # filed text and the single-record view keeps it.
    "pnnl-vips": [
        ("laboratory list", "vips:labs"),
        ("PNNL", "vips:search=PNNL"),
        ("INL", "vips:search=INL"),
        ("NLR", "vips:search=NLR"),
        ("one patent whole", "vips:record"),
    ],
    # Two Daymet windows and one refusal. The leap year is recorded because
    # 31 December is absent from it and that absence is what the calendar
    # rule is checked against; a single ordinary window could not fail if
    # the rule were dropped.
    "ornl-daac": [
        ("five days, three variables", "daymet:2023-06-01,2023-06-05,"
                                       "prcp,tmax,tmin"),
        ("the end of a leap year", "daymet:2024-12-26,2024-12-31,tmax"),
    ],
    # A filtered search, an unfiltered one, and a single package. The
    # unfiltered search is what an ignored filter would return, so both are
    # needed for the silently-dropped-filter test to mean anything.
    "ess-dive": [
        ("text search", "essdive:text=permafrost"),
        ("keyword search", "essdive:keywords=permafrost"),
        ("no filters", "essdive:"),
        ("no matches", "essdive:text=zzzznotarealtopiczzzz"),
        ("one package", "essdive:package"),
    ],
    # The published parameter list as well as searches: the adapter checks
    # every facet against that document before it sends one, so a fixture
    # without it could not replay a query at all.
    "esgf-search": [
        ("published parameter list", "esgf:describe"),
        ("one model, experiment and variable", "esgf:search"),
        ("the same search including superseded output", "esgf:superseded"),
        ("facet counts", "esgf:facets=source_id"),
        # A second model, because this index names one fact two ways: these
        # records carry `datetime_end` where CanESM5's carry
        # `datetime_stop`. A fixture from one model could not fail if the
        # other spelling were dropped.
        ("a model that spells the end date differently", "esgf:second"),
    ],
    "anl-sage-waggle": [("node manifest", "sage:nodes")],
    # The published property list as well as searches: the adapter checks
    # every property named in a filter against that document before sending
    # one, so a fixture without it could not replay a query at all. The
    # no-match search is recorded because an empty result and a refused
    # filter are different answers on this provider and both have to be
    # testable.
    "lbnl-mp-optimade": [
        ("published property list", "optimade:describe"),
        ("two elements", "optimade:search=elements HAS ALL \"Ga\",\"N\""),
        ("an exact formula", "optimade:search=chemical_formula_reduced=\"GaN\""),
        ("no matches", "optimade:search=chemical_formula_reduced=\"Zzz9\""),
        ("one structure", "optimade:entry"),
    ],
    # The catalog document, the format list, and one set with its
    # references. All three because a rendered answer is three requests and
    # the tool checks the name and the format before it sends any of them.
    "basis-set-exchange": [
        ("catalog", "bse:catalog"),
        ("format list", "bse:formats"),
        ("one set for three elements", "bse:render"),
    ],
    "doe-open-data-catalog": [("catalog document", "doc:limit=5")],
    "doe-code-json": [("software inventory", "doc:limit=5")],
    # Keyed. Skipped until EIA_API_KEY is configured; the credential never
    # reaches the fixture. When this first records, add an entry to
    # THIRD_PARTY_DATA.yml — the script says so if one is missing.
    "eia-api-v2": [
        ("route tree root", "eia:route="),
        ("grid route", "eia:route=electricity/rto/region-data"),
        # Demand AND the day-ahead forecast. Two metrics rather than one
        # because they share a route, a period axis and a single `value`
        # column, and the forecast always sorts first — which is how this
        # tool shipped for six days answering "how much power did CISO use"
        # with tomorrow's prediction. A fixture with only one type could not
        # fail if the filter were dropped again.
        ("grid rows, demand", "eia:data=D"),
        ("grid rows, day-ahead forecast", "eia:data=DF"),
    ],
}

# Documents big enough that committing them whole would be unreasonable.
TRIM = {"doe-open-data-catalog": ("dataset", 15),
        "doe-code-json": ("releases", 15)}

# The same idea for a document that IS the array. Sage's node manifest is
# 2 MB of 295 nodes and committing it whole would be a redistribution of the
# network's inventory rather than a test.
BARE_ARRAY_TRIM = {"anl-sage-waggle": 12}

# A document that is an object keyed by name rather than a list. The Basis
# Set Exchange's catalog is 527 KB of 776 sets; the keys kept are the ones
# the tests name plus a spread of families and roles, so a search over the
# fixture still has something to discriminate.
# The url fragment identifies WHICH document to cut. Without it the trim
# matched every object-shaped body in the recording, including the format
# list, and replaced that with an empty object — a fixture that reported a
# service offering no output formats at all.
KEYED_OBJECT_TRIM = {
    "basis-set-exchange": ("/api/metadata/",
                           ["6-31g", "6-31g*", "sto-3g", "cc-pvdz",
                            "cc-pvtz", "aug-cc-pvdz", "def2-svp",
                            "def2-tzvp", "lanl2dz", "crenbl", "ano-rcc",
                            "3-21g", "def2-universal-jkfit",
                            "cc-pvdz-rifit"])}

# The same idea for a text table: (valued rows to keep, trailing empty rows
# to keep). BPA's feeds are ~90 KB of five-minute intervals over seven days.
# The trailing empty run is kept deliberately rather than trimmed away —
# it is the publisher quirk the adapter exists to handle, and a fixture
# without it could not fail if that handling regressed.
TEXT_TRIM = {"bpa-operations": (24, 12)}

# Rows kept per recorded PostgREST query. Small on purpose: the fixture is
# there to pin the row shape and the paging arithmetic, and 75,727 turbines
# in a public repository would be a redistribution of the whole database
# rather than a test.
RECORDED_ROWS = 5

# The Federal Register document recorded whole, chosen because it is a real
# DOE rule with a docket, CFR references, and a RIN — the fields the tool
# exists to carry — rather than a bare notice.
RECORDED_DOCUMENT = "2026-17979"

# A FERC document, recorded so the scope filter's own path replays. Without
# it the only test of "found, published, and not ours to serve" would be a
# hand-written body agreeing with the code that reads it.
RECORDED_EXCLUDED_DOCUMENT = "2026-18232"

# An INL patent, recorded whole. A patent rather than a software record
# because the field the search view drops — the filed text — is ten
# kilobytes on a patent and a paragraph on some software, and the fixture
# exists to pin the drop.
RECORDED_PATENT = "US10016751"

# Oak Ridge. A fixed point so a recorded Daymet window is reproducible.
DAYMET_LATITUDE, DAYMET_LONGITUDE = 35.9313, -84.3104

# One ESS-DIVE package, recorded whole. `Model America` because it carries
# the fields the search view leaves out — methods, licence, funder, and a
# multi-place spatial coverage — which is what the single-record tool exists
# to return.
RECORDED_PACKAGE = "ess-dive-5c6fb3269616635-20260906T172343833"

# One model, experiment and variable. Narrow on purpose: the archive holds
# 14.7 million datasets and the fixture exists to pin the record shape and
# the retracted-versus-latest split, which this triple has both sides of.
RECORDED_CMIP = {"project": "CMIP6", "source_id": "CanESM5",
                 "experiment_id": "historical", "variable_id": "tas"}

# A second model whose records end in `datetime_end` rather than
# `datetime_stop`. Recorded so the two spellings are both in the fixture
# set; the difference is invisible from either one alone.
RECORDED_CMIP_SECOND = {"project": "CMIP6", "source_id": "GFDL-ESM4",
                        "experiment_id": "ssp585", "variable_id": "tas"}

# One OPTIMADE structure, recorded whole. A gallium nitride cell because it
# carries the provider's own stability block, which is the part of the
# record the standard does not define and the part a caller most needs
# labelled as one database's rather than the federation's.
RECORDED_STRUCTURE = "mp-1244984"

# 6-31G: the most-used basis set there is, in the Pople family, with a
# reference file and coverage through the first thirty elements.
RECORDED_BASIS_SET = "6-31g"


def _declared_third_party() -> set[str]:
    doc = yaml.safe_load(THIRD_PARTY.read_text()) or {}
    return {e["source_id"] for e in doc.get("redistributed", [])}


async def run(source_id: str, plan: list[tuple[str, str]]) -> None:
    ctx = load_context()
    manifest = ctx.sources.get(source_id)
    if manifest is None or not ctx.sources.selectable(manifest):
        print(f"skip {source_id}: not an active source")
        return
    credential = manifest.access.credential_ref
    if credential and not ctx.credentials.has(credential):
        print(f"skip {source_id}: {credential} is not configured "
              "(doe-mcp configure credentials)")
        return
    base = next((getattr(manifest.adapter, field)
                 for field in ("base_url", "document_url", "manifest_url")
                 if getattr(manifest.adapter, field, None)), "")
    recorder = record_through(
        ctx, HttpFetcher(policy=egress_policy_for(manifest, base)))

    failures: list[str] = []
    for label, spec in plan:
        kind, _, rest = spec.partition(":")
        try:
            if kind == "record":
                await ctx.osti.get_record(manifest, rest)
            elif kind == "search":
                filters, rows = _parse_search(rest)
                await ctx.osti.search(manifest, filters=filters, rows=rows)
            elif kind == "ods":
                limit, text = _parse_kv(rest)
                await ctx.opendatasoft.search_datasets(
                    manifest, limit=limit, text=text)
            elif kind == "doc":
                await ctx.json_document.search(
                    manifest, limit=int(rest.split("=")[1]))
            elif kind == "vips":
                if rest == "labs":
                    await ctx.vips.labs(manifest)
                elif rest == "record":
                    await ctx.vips.get_record(manifest, RECORDED_PATENT)
                else:
                    _, _, lab = rest.partition("=")
                    await ctx.vips.search(manifest, lab=lab,
                                          rows=RECORDED_ROWS)
            elif kind == "fr":
                if rest == "agencies":
                    await ctx.federal_register.agencies(manifest)
                elif rest == "document":
                    await ctx.federal_register.get_document(
                        manifest, RECORDED_DOCUMENT)
                elif rest == "excluded":
                    await ctx.federal_register.get_document(
                        manifest, RECORDED_EXCLUDED_DOCUMENT)
                else:
                    what, _, doctype = rest.partition("=")
                    await ctx.federal_register.search(
                        manifest, document_types=[doctype],
                        rows=RECORDED_ROWS,
                        page=2 if what == "page" else 1)
            elif kind == "pg":
                schema = (await ctx.postgrest.describe(manifest)).value
                if rest.startswith("rows"):
                    _, _, clause = rest.partition("=")
                    await ctx.postgrest.query(
                        manifest, rows=RECORDED_ROWS, schema=schema,
                        filters=parse_filters(clause, schema))
            elif kind == "daymet":
                first, last, *variables = rest.split(",")
                await ctx.daymet.point_series(
                    manifest, latitude=DAYMET_LATITUDE,
                    longitude=DAYMET_LONGITUDE, variables=variables,
                    start=first, end=last)
            elif kind == "essdive":
                if rest == "package":
                    await ctx.essdive.get_package(manifest,
                                                  RECORDED_PACKAGE)
                else:
                    name, _, value = rest.partition("=")
                    # With the sort the tool sends. A fixture is keyed on
                    # (url, params), so one recorded without it replays for
                    # nothing the server actually serves.
                    await ctx.essdive.search(
                        manifest,
                        filters={name: value} if name else {},
                        sort="dateUploaded:desc", rows=RECORDED_ROWS)
            elif kind == "esgf":
                schema = (await ctx.esgf.describe(manifest)).value
                if rest.startswith("facets"):
                    _, _, facet = rest.partition("=")
                    await ctx.esgf.search(
                        manifest, schema=schema,
                        filters={"project": "CMIP6"}, facets=[facet], rows=0)
                elif rest in ("search", "superseded", "second"):
                    await ctx.esgf.search(
                        manifest, schema=schema,
                        filters=(RECORDED_CMIP_SECOND if rest == "second"
                                 else RECORDED_CMIP),
                        rows=RECORDED_ROWS,
                        latest=None if rest == "superseded" else True)
            elif kind == "sage":
                await ctx.sage.nodes(manifest, rows=RECORDED_ROWS)
            elif kind == "optimade":
                schema = (await ctx.optimade.describe(manifest)).value
                if rest == "entry":
                    await ctx.optimade.get_entry(manifest,
                                                 RECORDED_STRUCTURE)
                elif rest.startswith("search"):
                    _, _, expression = rest.partition("=")
                    await ctx.optimade.search(
                        manifest, schema=schema,
                        filter_expression=expression, rows=RECORDED_ROWS)
            elif kind == "bse":
                if rest == "catalog":
                    await ctx.basis_sets.catalog(manifest)
                elif rest == "formats":
                    await ctx.basis_sets.formats(manifest)
                else:
                    await ctx.basis_sets.render(
                        manifest, key=RECORDED_BASIS_SET,
                        output_format="nwchem", elements=["H", "C", "O"])
            elif kind == "text":
                await ctx.text_feed.read_feed(manifest, rest)
            elif kind == "fe":
                if rest == "menu:year":
                    await ctx.fueleconomy.menu(manifest, "year")
                else:
                    await ctx.fueleconomy.fuel_prices(manifest)
            elif kind == "eia":
                what, _, value = rest.partition("=")
                if what == "route":
                    await ctx.eia.describe_route(manifest, value)
                else:
                    await ctx.eia.get_data(
                        manifest, "electricity/rto/region-data",
                        frequency="hourly", data_columns=["value"],
                        facets={"respondent": ["CISO"], "type": [value]},
                        rows=3, sort_column="period", sort_direction="desc")
        except Exception as err:                   # noqa: BLE001
            print(f"  ! {source_id} [{label}]: {err}")
            failures.append(label)
            continue
        print(f"  + {source_id} [{label}]")

    # A partial recording is worse than no recording: it overwrites the
    # cases that DID record with a file missing the ones that did not, and
    # the tests covering them start failing for a reason that looks like a
    # publisher change. Leave the previous fixture alone and say what
    # failed.
    if failures:
        print(f"  ! {source_id}: {len(failures)} of {len(plan)} interaction(s) "
              f"failed ({', '.join(failures)}). NOT written; the existing "
              "fixture is untouched. Re-run when the publisher is up.")
        return

    trimmed = _trim(source_id, recorder)
    note = (f"Recorded live from {manifest.name} "
            f"({manifest.access.terms_url}) by tools/record_fixtures.py. "
            "Publisher content redistributed under the terms recorded in "
            "THIRD_PARTY_DATA.yml.")
    if trimmed:
        note += f" TRIMMED: {trimmed}."
    recorder.write(FIXTURES / f"{source_id}.json", note=note)
    if source_id not in _declared_third_party():
        print(f"  ! {source_id}: recorded, but THIRD_PARTY_DATA.yml has no "
              "entry for it. Add one before committing; a test fails "
              "otherwise.")


def _parse_kv(rest: str) -> tuple[int, str]:
    parts = dict(c.split("=", 1) for c in rest.split(",") if "=" in c)
    return int(parts.get("limit", 10)), parts.get("text", "")


def _parse_search(rest: str) -> tuple[dict, int]:
    filters: dict = {}
    rows = 10
    for clause in rest.split(","):
        key, _, value = clause.partition("=")
        if key.strip() == "rows":
            rows = int(value)
        else:
            filters[key.strip()] = value.strip()
    return filters, rows


def _trim(source_id: str, recorder: RecordingFetcher) -> str | None:
    if source_id in TEXT_TRIM:
        return _trim_text(source_id, recorder)
    if source_id in BARE_ARRAY_TRIM:
        return _trim_bare_array(source_id, recorder)
    if source_id in KEYED_OBJECT_TRIM:
        return _trim_keyed_object(source_id, recorder)
    if source_id not in TRIM:
        return None
    key, keep = TRIM[source_id]
    trimmed = None
    for interaction in recorder.interactions:
        body = interaction.get("body")
        if isinstance(body, dict) and isinstance(body.get(key), list):
            total = len(body[key])
            body[key] = body[key][:keep]
            trimmed = (f"the {key!r} array held {total} entries and is cut to "
                       f"the first {keep}, so record counts read from this "
                       "fixture are the fixture's, not the publisher's")
    return trimmed


def _trim_text(source_id: str, recorder: RecordingFetcher) -> str | None:
    """Cut a recorded text table down, keeping the shape that matters.

    Everything before the column header is kept whole: the preamble carries
    the window, the publisher's own last-updated stamp, and the scope
    caveats, and all three reach the parsed result. Of the table itself, the
    first N rows that carry values and the last M that do not are kept, so a
    replayed fixture still exercises both the parse and the trailing-empty
    rule.
    """
    keep_valued, keep_empty = TEXT_TRIM[source_id]
    trimmed = None
    for interaction in recorder.interactions:
        body = interaction.get("body")
        if not isinstance(body, str):
            continue
        lines = body.replace("\r\n", "\n").split("\n")
        head = next((i for i, ln in enumerate(lines)
                     if ln.startswith("Date/Time")), None)
        if head is None:
            continue
        table = [ln for ln in lines[head + 1:] if ln.strip()]
        valued = [ln for ln in table if ln.split("\t", 1)[-1].strip()]
        empty = [ln for ln in table if not ln.split("\t", 1)[-1].strip()]
        interaction["body"] = "\r\n".join(
            lines[:head + 1] + valued[:keep_valued] + empty[-keep_empty:]
        ) + "\r\n"
        trimmed = (f"the table held {len(table)} five-minute intervals and is "
                   f"cut to the first {min(keep_valued, len(valued))} that "
                   f"carry values plus the last "
                   f"{min(keep_empty, len(empty))} that do not, so interval "
                   "counts read from this fixture are the fixture's, not the "
                   "publisher's. The preamble and column header are whole")
    return trimmed


def _trim_bare_array(source_id: str,
                     recorder: RecordingFetcher) -> str | None:
    """Cut a recorded top-level JSON array down to a stated number of
    entries, keeping the ones that exercise the parse.

    Kept deliberately rather than by slicing the head: the first nodes in
    Sage's manifest carry no coordinates and no instruments, so a plain
    head would replay a network in which nothing is deployed anywhere.
    """
    keep = BARE_ARRAY_TRIM[source_id]
    trimmed = None
    for interaction in recorder.interactions:
        body = interaction.get("body")
        if not isinstance(body, list):
            continue
        total = len(body)
        placed = [e for e in body if isinstance(e, dict)
                  and e.get("gps_lat") is not None and e.get("sensors")]
        bare = [e for e in body if isinstance(e, dict)
                and e.get("gps_lat") is None]
        interaction["body"] = placed[:keep - 2] + bare[:2]
        trimmed = (f"the manifest held {total} nodes and is cut to "
                   f"{len(interaction['body'])} — the first "
                   f"{min(keep - 2, len(placed))} with coordinates and "
                   "instruments plus two with neither — so node counts read "
                   "from this fixture are the fixture's, not the "
                   "publisher's")
    return trimmed


def _trim_keyed_object(source_id: str,
                       recorder: RecordingFetcher) -> str | None:
    """Cut a recorded object-keyed document down to named keys.

    Named rather than sliced, because which entries survive decides what the
    tests can discriminate on: the fixture needs several families, both
    roles, and at least one set with an element range narrow enough that a
    coverage filter excludes it.
    """
    marker, keep = KEYED_OBJECT_TRIM[source_id]
    trimmed = None
    for interaction in recorder.interactions:
        body = interaction.get("body")
        if marker not in interaction.get("url", ""):
            continue
        if not isinstance(body, dict) or len(body) <= len(keep):
            continue
        total = len(body)
        interaction["body"] = {k: body[k] for k in keep if k in body}
        trimmed = (f"the catalog held {total} entries and is cut to "
                   f"{len(interaction['body'])} named ones, so counts read "
                   "from this fixture are the fixture's, not the "
                   "publisher's")
    return trimmed


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for source_id, plan in PLANS.items():
        print(source_id)
        asyncio.run(run(source_id, plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
